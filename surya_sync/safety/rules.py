"""Safety rules and their strict priority order.

The safety layer runs **before** the scheduler, every cycle, without
exception. It is rule-based and deterministic — no model, no forecast, no
optimization. If the sophisticated stack disagrees with a safety rule, the
safety rule wins.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum

from surya_sync.domain import ControlAction
from surya_sync.models.generic_resource import (
    ActuationHistory,
    ResourceObservation,
    ResourceRegistry,
)
from surya_sync.state.system_state import SystemState


class SafetyPriority(IntEnum):
    """Evaluation order. Lower value = higher authority.

    This ordering is a project invariant, not a tuning parameter. It
    encodes the claim that equipment and people matter more than solar
    optimization, and it must not be reordered to make a benchmark look
    better.
    """

    ELECTRICAL_SAFETY = 1
    ACTUATOR_PROTECTION = 2
    """Pump protection, for the tank. Named for the actuator rather than the
    pump because this layer must not know which resource it is guarding."""

    OVERFLOW_PROTECTION = 3
    SENSOR_VALIDITY = 4
    CRITICAL_SERVICE_AVAILABILITY = 5
    """Critical water availability, for the tank — expressed as service
    level so the rule generalizes without renaming."""

    MANUAL_OVERRIDE = 6
    EQUIPMENT_CONSTRAINTS = 7
    MPC_SCHEDULING = 8
    """The scheduler's own preference sits here — below every rule above."""

    SOLAR_UTILIZATION = 9
    GRID_OPTIMIZATION = 10


@dataclass(frozen=True, slots=True)
class SafetyVerdict:
    """The outcome of evaluating one rule."""

    rule_name: str
    priority: SafetyPriority
    triggered: bool
    forced_action: ControlAction | None = None
    """Non-``None`` only when ``triggered``. The rule is *compelling* this
    action; lower-priority rules and the scheduler cannot overturn it."""

    message: str = ""
    """Recorded verbatim in ``safety_events`` and shown to the user."""


class SafetyRule(ABC):
    """One deterministic check over system state.

    Rules must be cheap and total: no I/O, no exceptions, and a verdict
    for every possible state — including states with missing or invalid
    data, which are exactly when safety matters most.
    """

    name: str
    priority: SafetyPriority

    @abstractmethod
    def evaluate(self, state: SystemState, resource_id: str) -> SafetyVerdict:
        """Return this rule's verdict for the given resource."""

    def evaluate_action(
        self, state: SystemState, resource_id: str, action: ControlAction
    ) -> SafetyVerdict:
        """Verdict on an action the scheduler has *proposed*.

        Defaults to the state verdict, which is right for every rule that
        compels an action outright: if the tank is over its ceiling, the
        pump stops whatever anyone proposed.

        A rule overrides this when it forbids some actions without
        compelling any — cycling limits are the example. Such a rule is
        silent in ``check_state`` (there is nothing to force) and must
        still be able to reject a proposal, or the post-scheduling
        validation would have nothing to validate.
        """
        return self.evaluate(state, resource_id)


def hold_action(actuator_on: bool) -> ControlAction:
    """The action that changes nothing.

    ``WAIT`` and ``RUN`` are both "carry on as you are", depending on which
    way you are already carrying on. Every rule that wants to forbid a
    *transition* rather than compel a state forces this.
    """
    return ControlAction.RUN if actuator_on else ControlAction.WAIT


def _observation(state: SystemState, resource_id: str) -> ResourceObservation | None:
    return state.observations.get(resource_id)


def _history(state: SystemState, resource_id: str) -> ActuationHistory | None:
    return state.actuation_history.get(resource_id)


class ActuatorRuntimeLimitRule(SafetyRule):
    """Deadman timer: an actuator may not stay energized indefinitely.

    This mirrors, on the Pi, the limit the ESP32 enforces independently.
    Two enforcers is not redundancy for its own sake — the ESP32's timer is
    the one that survives the Pi crashing, and this one is the one that
    produces a logged, attributable decision instead of a silent cut.

    Unknown runtime while energized **forces a stop**, which is the
    opposite of what ``PumpModel.admissible_actions`` does with the same
    unknown. That is deliberate and the priority order is what makes it
    coherent: actuator protection (2) outranks equipment constraints (7).
    A pump we cannot prove is within its runtime budget is a pump that may
    have been running for hours. Stopping it costs a ``min_off`` wait;
    not stopping it costs a pump.
    """

    priority = SafetyPriority.ACTUATOR_PROTECTION

    def __init__(self, max_runtime_minutes: float, name: str = "actuator_runtime_limit") -> None:
        if max_runtime_minutes <= 0.0:
            raise ValueError("max_runtime_minutes must be > 0")
        self.name = name
        self.max_runtime_minutes = max_runtime_minutes

    def evaluate(self, state: SystemState, resource_id: str) -> SafetyVerdict:
        observation = _observation(state, resource_id)
        if observation is None or not observation.actuator_on:
            return SafetyVerdict(self.name, self.priority, triggered=False)

        history = _history(state, resource_id)
        elapsed = None if history is None else history.minutes_in_state(state.timestamp)

        if elapsed is None:
            return SafetyVerdict(
                self.name,
                self.priority,
                triggered=True,
                forced_action=ControlAction.STOP,
                message=(
                    f"{resource_id} is energized but its runtime is unknown; "
                    "stopping rather than assuming it is within the "
                    f"{self.max_runtime_minutes:g} min limit"
                ),
            )
        if elapsed >= self.max_runtime_minutes:
            return SafetyVerdict(
                self.name,
                self.priority,
                triggered=True,
                forced_action=ControlAction.STOP,
                message=(
                    f"{resource_id} has run {elapsed:.1f} min, at or beyond the "
                    f"{self.max_runtime_minutes:g} min limit"
                ),
            )
        return SafetyVerdict(self.name, self.priority, triggered=False)


class OverflowProtectionRule(SafetyRule):
    """The service level may not exceed the resource's ceiling.

    Forces a stop, never a "run a bit less" — the ceiling is a hard
    constraint and hard constraints are not negotiated down.
    """

    priority = SafetyPriority.OVERFLOW_PROTECTION

    def __init__(self, registry: ResourceRegistry, name: str = "overflow_protection") -> None:
        self.name = name
        self._registry = registry

    def evaluate(self, state: SystemState, resource_id: str) -> SafetyVerdict:
        observation = _observation(state, resource_id)
        if observation is None or not observation.sensor_valid:
            return SafetyVerdict(self.name, self.priority, triggered=False)

        ceiling = self._registry.get(resource_id).constraints().service_level_max
        if observation.service_level >= ceiling:
            return SafetyVerdict(
                self.name,
                self.priority,
                triggered=True,
                forced_action=ControlAction.STOP,
                message=(
                    f"{resource_id} at service level {observation.service_level:.3f}, "
                    f"at or above the ceiling {ceiling:.3f}"
                ),
            )
        return SafetyVerdict(self.name, self.priority, triggered=False)


class SensorValidityRule(SafetyRule):
    """A resource with no trustworthy reading may not be actuated.

    Ranked above critical service on purpose. Running a pump against an
    unknown level is how a tank overflows, and an overflow is not
    recoverable by waiting; a dry tap is.
    """

    priority = SafetyPriority.SENSOR_VALIDITY

    def __init__(self, name: str = "sensor_validity") -> None:
        self.name = name

    def evaluate(self, state: SystemState, resource_id: str) -> SafetyVerdict:
        observation = _observation(state, resource_id)
        if observation is None:
            return SafetyVerdict(
                self.name,
                self.priority,
                triggered=True,
                forced_action=ControlAction.STOP,
                message=f"no observation for {resource_id}; cannot actuate blind",
            )
        if not observation.sensor_valid:
            return SafetyVerdict(
                self.name,
                self.priority,
                triggered=True,
                forced_action=ControlAction.STOP,
                message=f"{resource_id} sensor reading failed validation",
            )
        return SafetyVerdict(self.name, self.priority, triggered=False)


class CriticalServiceRule(SafetyRule):
    """Below the hard floor, the resource is replenished regardless of solar.

    This is the rule that stops SuryaSync mistaking its objective for its
    constraint. Cheap solar is what the system optimizes; water in the tank
    is what the household actually bought.
    """

    priority = SafetyPriority.CRITICAL_SERVICE_AVAILABILITY

    def __init__(self, registry: ResourceRegistry, name: str = "critical_service") -> None:
        self.name = name
        self._registry = registry

    def evaluate(self, state: SystemState, resource_id: str) -> SafetyVerdict:
        observation = _observation(state, resource_id)
        if observation is None or not observation.sensor_valid:
            return SafetyVerdict(self.name, self.priority, triggered=False)

        floor = self._registry.get(resource_id).constraints().service_level_critical
        if observation.service_level <= floor:
            return SafetyVerdict(
                self.name,
                self.priority,
                triggered=True,
                forced_action=ControlAction.RUN,
                message=(
                    f"{resource_id} at service level {observation.service_level:.3f}, "
                    f"at or below the critical floor {floor:.3f}"
                ),
            )
        return SafetyVerdict(self.name, self.priority, triggered=False)


class ManualOverrideRule(SafetyRule):
    """The user's explicit instruction outranks the scheduler.

    It does not outrank anything above it: a manual RUN cannot overflow the
    tank, because overflow protection was evaluated first and its verdict
    wins. The user commands the system, not the physics.
    """

    priority = SafetyPriority.MANUAL_OVERRIDE

    def __init__(self, name: str = "manual_override") -> None:
        self.name = name

    def evaluate(self, state: SystemState, resource_id: str) -> SafetyVerdict:
        if state.manual_override is None:
            return SafetyVerdict(self.name, self.priority, triggered=False)
        return SafetyVerdict(
            self.name,
            self.priority,
            triggered=True,
            forced_action=state.manual_override,
            message=f"manual override active: {state.manual_override.value}",
        )


class EquipmentConstraintRule(SafetyRule):
    """Cycling limits: min-on, min-off and the daily start budget.

    The rule asks the resource itself via ``admissible_actions`` rather
    than re-deriving the limits from config. Re-deriving them would put a
    second copy of the cycling logic in the safety layer, and two copies of
    a rule are two rules the moment one of them is edited.

    It compels the *hold* action only when a transition is illegal. When
    both options are open it stays silent, because choosing between them is
    the scheduler's job, not safety's.
    """

    priority = SafetyPriority.EQUIPMENT_CONSTRAINTS

    def __init__(self, registry: ResourceRegistry, name: str = "equipment_constraints") -> None:
        self.name = name
        self._registry = registry

    def evaluate(self, state: SystemState, resource_id: str) -> SafetyVerdict:
        observation = _observation(state, resource_id)
        if observation is None:
            return SafetyVerdict(self.name, self.priority, triggered=False)

        resource = self._registry.get(resource_id)
        admissible = resource.admissible_actions(
            observation, _history(state, resource_id), state.timestamp
        )
        held = hold_action(observation.actuator_on)
        if len(admissible) > 1:
            return SafetyVerdict(self.name, self.priority, triggered=False)

        return SafetyVerdict(
            self.name,
            self.priority,
            triggered=True,
            forced_action=held,
            message=(
                f"{resource_id} may not change state yet (cycling limits); "
                f"holding {held.value}"
            ),
        )

    def evaluate_action(
        self, state: SystemState, resource_id: str, action: ControlAction
    ) -> SafetyVerdict:
        """Reject a proposed action the equipment limits do not permit.

        ``evaluate`` is silent whenever a choice exists, so without this a
        scheduler could propose an action outside the admissible set and
        nothing downstream would notice. The set is the authority; the
        replacement is always the hold.
        """
        observation = _observation(state, resource_id)
        if observation is None:
            return SafetyVerdict(self.name, self.priority, triggered=False)

        resource = self._registry.get(resource_id)
        admissible = resource.admissible_actions(
            observation, _history(state, resource_id), state.timestamp
        )
        if action in admissible:
            return SafetyVerdict(self.name, self.priority, triggered=False)

        held = hold_action(observation.actuator_on)
        permitted = ", ".join(sorted(a.value for a in admissible)) or "nothing"
        return SafetyVerdict(
            self.name,
            self.priority,
            triggered=True,
            forced_action=held,
            message=(
                f"{action.value} is not admissible for {resource_id} "
                f"(permitted: {permitted}); holding {held.value}"
            ),
        )


UNCOVERED_PRIORITIES: tuple[SafetyPriority, ...] = (
    SafetyPriority.ELECTRICAL_SAFETY,
)
"""Priorities with no rule yet, declared rather than left to be discovered.

``ELECTRICAL_SAFETY`` needs an electrical fault signal — earth leakage, a
stalled-rotor current signature, a dry-run current draw — and nothing in
the system produces one until the ESP32 arrives in Phase 11. Writing a rule
that checks nothing would make the layer *look* complete, which is worse
than a gap that is written down and tested for.

``SOLAR_UTILIZATION`` and ``GRID_OPTIMIZATION`` are deliberately absent for
a different reason: they rank *below* the scheduler and are objectives, not
safety rules. They are listed in ``SafetyPriority`` to fix the ordering, not
to be implemented here.
"""


def default_rules(
    registry: ResourceRegistry, max_runtime_minutes: float
) -> tuple[SafetyRule, ...]:
    """The rule set SuryaSync runs with, in no particular order.

    ``SafetyValidator`` sorts them by priority, so the order here carries no
    meaning and cannot be used to smuggle in a different one.
    """
    return (
        ActuatorRuntimeLimitRule(max_runtime_minutes=max_runtime_minutes),
        OverflowProtectionRule(registry),
        SensorValidityRule(),
        CriticalServiceRule(registry),
        ManualOverrideRule(),
        EquipmentConstraintRule(registry),
    )
