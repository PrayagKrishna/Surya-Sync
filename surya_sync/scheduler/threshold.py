"""Tier 4 fallback: conventional level-threshold controller. Baseline A.

This is the controller every household already has: refill when the level
drops below a threshold, stop when it is full enough, ignore the sun
entirely. It exists for two reasons, and neither of them is to be good.

1. **It is the baseline.** Every later phase has to beat it on grid energy,
   and a claim of improvement is meaningless without a measured conventional
   number to improve on.
2. **It is the last tier that still thinks.** When the MPC, the heuristic
   and the reactive scheduler have all failed, this is what keeps water in
   the tank. It therefore has to work with nothing: no forecast, no solar
   reading, no history.

What it is *not* is a solar scheduler. It never looks at PV, and that is
the point — the gap between its grid consumption and the later tiers' is
the number the whole project is arguing about.

**One-step lookahead, and why that is not cheating.** The controller checks
its own proposal against ``predict_trajectory`` before returning it. That
is not intelligence smuggled into the baseline; it is the ``Scheduler``
contract, which requires a ``ConstraintStatus`` and forbids returning a
plan that breaches a hard constraint. Without it the controller cannot
answer the question the interface asks it.

The check earns its keep because ``scheduler.step_minutes`` is coarse: at
30 lpm into a 1000 L tank, one 15-minute step moves the level 45 points, so
a controller that stops *at* the ceiling has already overshot it by the
time it acts. Phase 1 recorded this as control resolution, not a physics
bug, and this is where it gets paid for. With no demand forecast the
lookahead is unavailable, the controller degrades to plain hysteresis, and
``ConstraintStatus.checked_constraints`` says so rather than implying the
check passed.
"""

from __future__ import annotations

import time

from surya_sync.config.schema import SchedulerConfig
from surya_sync.domain import ControlAction
from surya_sync.models.generic_resource import (
    FlexibilityEstimate,
    PredictedState,
    ResourceConstraints,
)
from surya_sync.scheduler.base import (
    ConstraintStatus,
    DecisionExplanation,
    PlannedAction,
    ReasonCode,
    Scheduler,
    SchedulerTier,
    SchedulingPlan,
    SchedulingRequest,
    SolverStatus,
)


class ThresholdScheduler(Scheduler):
    """Hysteresis on service level, with a one-step constraint check."""

    name = "threshold"
    algorithm_version = "threshold-1.0.0"
    tier = SchedulerTier.THRESHOLD

    def __init__(self, config: SchedulerConfig) -> None:
        self._safety_reserve = config.safety_reserve_level

    # --- thresholds -----------------------------------------------------

    def start_level(self, constraints: ResourceConstraints) -> float:
        """Below this, refilling begins.

        Sits a static reserve above the planning floor, so that ordinary
        forecast-free operation still leaves a margin between "start
        refilling" and "service has failed". Phase 10 makes the reserve
        scale with demand uncertainty; here it is a constant, because a
        tier-4 fallback has no uncertainty estimate to scale it with.
        """
        return min(
            constraints.service_level_min + self._safety_reserve,
            constraints.service_level_max,
        )

    # --- the decision ---------------------------------------------------

    def generate_plan(self, request: SchedulingRequest) -> SchedulingPlan:
        started = time.perf_counter()
        try:
            return self._plan(request, started)
        except Exception as exc:  # noqa: BLE001 - contract: never raise
            # The contract says internal failure is a plan with
            # ``SolverStatus.ERROR`` and a safe action, not an exception.
            # Raising here would take the whole fallback chain down with
            # the tier that failed.
            return self._error_plan(request, started, exc)

    def _plan(self, request: SchedulingRequest, started: float) -> SchedulingPlan:
        observation = request.observation
        constraints = request.resource.constraints()
        level = observation.service_level
        start_level = self.start_level(constraints)

        wants_run = self._wants_run(level, observation.actuator_on, constraints, start_level)
        action = ControlAction.RUN if wants_run else _idle_action(observation.actuator_on)
        reason = self._reason_for(level, observation.actuator_on, constraints, start_level, action)

        # One-step lookahead. Only ever makes the decision more conservative:
        # it can turn a RUN that would overflow into a STOP, or a WAIT that
        # would run the tank dry into a RUN. It never invents a reason to run.
        predicted, checked = self._lookahead(request, action)
        if predicted is not None and predicted[0].violates_hard_constraint:
            alternative = _idle_action(observation.actuator_on) if wants_run else ControlAction.RUN
            alt_predicted, _ = self._lookahead(request, alternative)
            if alt_predicted is not None and not alt_predicted[0].violates_hard_constraint:
                action = alternative
                predicted = alt_predicted
                reason = (
                    ReasonCode.MUST_RUN_DEADLINE
                    if action is ControlAction.RUN
                    else ReasonCode.TARGET_REACHED
                )

        action, reason = self._respect_equipment_limits(request, action, reason)
        predicted, checked = self._lookahead(request, action)

        return self._build_plan(
            request=request,
            started=started,
            action=action,
            reason=reason,
            predicted=predicted or (),
            checked=checked,
        )

    def _wants_run(
        self,
        level: float,
        actuator_on: bool,
        constraints: ResourceConstraints,
        start_level: float,
    ) -> bool:
        """The hysteresis itself, and nothing else.

        Two thresholds rather than one, because a single one chatters: the
        level sits on it and the pump starts and stops every cycle. Once
        running, the controller keeps running until the ceiling, which is
        what makes the duty cycle long and the start count low.
        """
        if actuator_on:
            return level < constraints.service_level_max
        return level < start_level

    def _reason_for(
        self,
        level: float,
        actuator_on: bool,
        constraints: ResourceConstraints,
        start_level: float,
        action: ControlAction,
    ) -> ReasonCode:
        if action is not ControlAction.RUN:
            if actuator_on:
                return ReasonCode.TARGET_REACHED
            return ReasonCode.SUFFICIENT_LEVEL
        if level <= constraints.service_level_critical:
            return ReasonCode.CRITICAL_LEVEL
        if actuator_on:
            # A refill already under way. Not an alarm — reporting this as
            # CRITICAL_LEVEL would have the "Why?" screen cry wolf at 45%.
            return ReasonCode.BELOW_TARGET_LEVEL
        return ReasonCode.CRITICAL_LEVEL

    def _respect_equipment_limits(
        self, request: SchedulingRequest, action: ControlAction, reason: ReasonCode
    ) -> tuple[ControlAction, ReasonCode]:
        """Never propose an action the equipment forbids.

        The safety layer would catch it, but a scheduler that knowingly
        proposes an illegal action and relies on being overruled produces a
        decision log full of overrides that misattributes the cause.
        """
        admissible = request.resource.admissible_actions(
            request.observation, request.actuation_history, request.now
        )
        if action in admissible:
            return action, reason
        return _idle_action(request.observation.actuator_on), ReasonCode.EQUIPMENT_COOLDOWN

    # --- supporting detail ----------------------------------------------

    def _lookahead(
        self, request: SchedulingRequest, action: ControlAction
    ) -> tuple[tuple[PredictedState, ...] | None, tuple[str, ...]]:
        """Predict the one step this plan actually commits to.

        ``None`` when there is no demand forecast to predict against —
        distinct from an empty trajectory, and reported as an unchecked
        constraint rather than a satisfied one.

        The step comes from ``request.horizon``, not from config. The
        request is what the driver is actually about to execute; a step
        remembered from construction can silently disagree with it, and a
        lookahead over the wrong interval would clear a plan that overflows.
        """
        if request.demand_forecast is None:
            return None, ()
        states = request.resource.predict_trajectory(
            observation=request.observation,
            actions=(action,),
            demand_forecast=request.demand_forecast,
            step_minutes=request.horizon.step_minutes,
        )
        return states, ("service_level_critical", "service_level_max")

    def _flexibility(self, request: SchedulingRequest) -> FlexibilityEstimate | None:
        if request.flexibility is not None:
            return request.flexibility
        if request.demand_forecast is None:
            return None
        return request.resource.estimate_flexibility(
            request.observation, request.demand_forecast
        )

    def _build_plan(
        self,
        request: SchedulingRequest,
        started: float,
        action: ControlAction,
        reason: ReasonCode,
        predicted: tuple[PredictedState, ...],
        checked: tuple[str, ...],
    ) -> SchedulingPlan:
        flexibility = self._flexibility(request)
        notes: list[str] = []
        if flexibility is None:
            notes.append(
                "flexibility not estimated: no demand forecast available at this tier"
            )
        if not checked:
            notes.append(
                "no demand forecast: decided on the current level alone, "
                "without a predicted next state"
            )

        violations = tuple(
            f"service_level {state.service_level:.3f} outside the hard band at "
            f"{state.timestamp.isoformat()}"
            for state in predicted
            if state.violates_hard_constraint
        )

        return SchedulingPlan(
            first_action=action,
            planned_actions=(
                PlannedAction(
                    step_index=0,
                    target_time=request.now,
                    action=action,
                    predicted_service_level=(
                        predicted[0].service_level if predicted else None
                    ),
                    predicted_solar_kw=None,
                ),
            ),
            explanation=DecisionExplanation(
                decision=action,
                reason_code=reason,
                service_level=request.observation.service_level,
                flexibility_minutes=(
                    flexibility.flexibility_minutes if flexibility else 0.0
                ),
                solar_surplus_kw=request.solar_surplus_kw,
                expected_best_time=None,
                predicted_service_level_at_best_time=None,
                notes=tuple(notes),
            ),
            objective_value=None,
            predicted_states=predicted,
            constraint_status=ConstraintStatus(
                satisfied=not violations,
                violations=violations,
                checked_constraints=checked,
            ),
            solver_status=SolverStatus.NOT_APPLICABLE,
            algorithm_version=self.algorithm_version,
            scheduler_name=self.name,
            tier=self.tier,
            computed_at=request.now,
            compute_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _error_plan(
        self, request: SchedulingRequest, started: float, exc: Exception
    ) -> SchedulingPlan:
        """A plan that says "I failed" without leaving the actuator undefined.

        The safe action is the hold: whatever the actuator is doing, keep
        doing it. A failed scheduler is not evidence that a transition is
        needed, and a spurious transition is the one thing a failure must
        not cause.
        """
        held = _hold_action(request.observation.actuator_on)
        return SchedulingPlan(
            first_action=held,
            planned_actions=(
                PlannedAction(step_index=0, target_time=request.now, action=held),
            ),
            explanation=DecisionExplanation(
                decision=held,
                reason_code=ReasonCode.SAFETY_OVERRIDE,
                service_level=request.observation.service_level,
                flexibility_minutes=0.0,
                notes=(f"{type(exc).__name__}: {exc}",),
            ),
            objective_value=None,
            predicted_states=(),
            constraint_status=ConstraintStatus(
                satisfied=False,
                violations=(f"scheduler failed: {type(exc).__name__}",),
                checked_constraints=(),
            ),
            solver_status=SolverStatus.ERROR,
            algorithm_version=self.algorithm_version,
            scheduler_name=self.name,
            tier=self.tier,
            computed_at=request.now,
            compute_ms=(time.perf_counter() - started) * 1000.0,
        )


def _idle_action(actuator_on: bool) -> ControlAction:
    """The "do not run" action, in the vocabulary the actuator is in.

    ``STOP`` de-energizes something running; ``WAIT`` is the deliberate
    choice not to start. Using one where the other belongs would make the
    decision log unreadable — and ``STOP`` on an idle pump is not in the
    admissible set, so it would also be rejected.
    """
    return ControlAction.STOP if actuator_on else ControlAction.WAIT


def _hold_action(actuator_on: bool) -> ControlAction:
    """The "change nothing" action. The opposite question to ``_idle_action``.

    Distinct from it in exactly one case, and it is the case that matters:
    a running actuator is *held* by ``RUN`` and *idled* by ``STOP``. A
    failure path that confused the two would stop the pump every time the
    scheduler threw.
    """
    return ControlAction.RUN if actuator_on else ControlAction.WAIT
