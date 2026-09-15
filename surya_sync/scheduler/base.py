"""The ``Scheduler`` interface and the fallback chain.

Every scheduler — from the dumb threshold controller to the uncertainty-
aware MPC — implements ``generate_plan`` and returns a ``SchedulingPlan``.
One interface, six implementations, so they are directly comparable under
identical conditions (Phase 14) and interchangeable at runtime (fallback).

Layer boundary: ``ml/`` answers *what will likely happen*. This module
answers *what should happen now*. Forecasts arrive here as inputs inside
``SchedulingRequest``; a scheduler never trains or calls a model itself.

Phase 0: interface and result types only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any

from surya_sync.domain import (
    ControlAction,
    Forecast,
    Horizon,
    Provenance,
)
from surya_sync.models.generic_resource import (
    ActuationHistory,
    FlexibilityEstimate,
    FlexibleResource,
    PredictedState,
    ResourceObservation,
)


class SchedulerTier(IntEnum):
    """Position in the fallback chain. Lower value = more sophisticated.

    The household must keep functioning when the clever algorithm fails.
    Degradation is always downward through these tiers, never a hard stop.
    """

    SAFETY_LAYER = 0
    """Not a scheduler at all — the safety layer, which sits *above* the
    whole chain and can compel an action without consulting any tier.

    It is listed here because a decision has to record who made it, and
    ``scheduler_decisions.tier`` is that field. Filing a safety override
    under ``LOCAL_SAFE_MODE`` instead would conflate "the Pi's safety rules
    decided" with "the Pi is gone and the ESP32 is on its own" — which are
    opposite states of health, and Phase 14 counts them."""

    RISK_AWARE_MPC = 1
    PREDICTIVE_HEURISTIC = 2
    REACTIVE_SOLAR = 3
    THRESHOLD = 4
    LOCAL_SAFE_MODE = 5
    """Not a Python scheduler — the ESP32's autonomous failsafe. Listed so
    the chain's terminal state is explicit."""


class SolverStatus(str, Enum):
    """Outcome of the optimization step, if there was one."""

    NOT_APPLICABLE = "not_applicable"
    """Rule-based schedulers (threshold, reactive) report this."""

    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    """A valid but not provably optimal solution — e.g. hit a time limit."""

    INFEASIBLE = "infeasible"
    """No action sequence satisfies the hard constraints. Must trigger
    fallback, never a relaxed re-solve that violates them quietly."""

    TIMEOUT = "timeout"
    ERROR = "error"


class ReasonCode(str, Enum):
    """Why the scheduler decided what it decided.

    This enum is the backbone of the "Why?" screen. It is deliberately a
    closed vocabulary: every decision maps to exactly one code, so the UI
    can render plain language and experiments can count decision types.
    Extend it when a genuinely new rationale appears — never fall back to
    a free-text catch-all.
    """

    # --- reasons to RUN ---
    CRITICAL_LEVEL = "critical_level"
    """Service level at or near the hard floor. Runs regardless of solar."""

    SOLAR_SURPLUS_AVAILABLE = "solar_surplus_available"
    """Running now is powered by surplus PV generation."""

    OPTIMAL_WINDOW_NOW = "optimal_window_now"
    """MPC's horizon says this step is the cheapest feasible one."""

    NO_BETTER_WINDOW_AHEAD = "no_better_window_ahead"
    """Deferring would only make things worse within the horizon."""

    MUST_RUN_DEADLINE = "must_run_deadline"
    """Latest feasible start time reached; deferring breaches a constraint."""

    BELOW_TARGET_LEVEL = "below_target_level"
    """A refill already in progress is continuing because the level has not
    reached its target yet. Distinct from ``CRITICAL_LEVEL``: nothing is
    wrong, the tank is simply still filling. Collapsing the two would make
    the "Why?" screen cry wolf at 45% full, and would make it impossible to
    count how often the system actually approached the floor."""

    # --- reasons to WAIT ---
    SUFFICIENT_LEVEL = "sufficient_level"
    """Service level comfortably above target; no need to act."""

    AWAITING_SOLAR = "awaiting_solar"
    """Flexibility exists and a better-lit window is forecast. The core
    value proposition of the whole system, in one reason code."""

    DEMAND_LOW = "demand_low"
    """Forecast demand does not justify actuation yet."""

    EQUIPMENT_COOLDOWN = "equipment_cooldown"
    """min-off time or daily start limit blocks actuation."""

    # --- reasons to STOP ---
    TARGET_REACHED = "target_reached"
    SOLAR_SURPLUS_ENDED = "solar_surplus_ended"

    # --- overrides and degraded operation ---
    SAFETY_OVERRIDE = "safety_override"
    """The safety layer replaced the scheduler's action. Always logged."""

    MANUAL_OVERRIDE = "manual_override"
    SENSOR_INVALID = "sensor_invalid"
    ANOMALY_DETECTED = "anomaly_detected"
    """Behaviour outside learned norms; scheduling turns conservative."""

    # There is deliberately no ``FALLBACK_ENGAGED`` code. Falling back is not
    # a rationale — the lower tier still decided for a substantive reason,
    # and that reason is what the "Why?" screen must show. The fact of the
    # fallback is carried by ``SchedulingPlan.fallback_engaged`` and
    # ``SchedulingPlan.tier``, so a decision records both what happened and
    # why, instead of losing the why.


@dataclass(frozen=True, slots=True)
class ConstraintStatus:
    """Whether the returned plan respects the hard constraints.

    A plan with ``satisfied=False`` must never be executed. It is returned
    rather than raised so the failure is logged and attributable.
    """

    satisfied: bool
    violations: tuple[str, ...] = ()
    """Human-readable violation descriptions, e.g.
    ``"service_level 0.14 < critical 0.20 at 2026-08-17T14:30"``."""

    checked_constraints: tuple[str, ...] = ()
    """Names of the constraints actually evaluated — so a plan that
    skipped a check is distinguishable from one that passed it."""


@dataclass(frozen=True, slots=True)
class DecisionExplanation:
    """Structured rationale for a single decision.

    Produced by *every* scheduler on *every* cycle — never skipped, not
    even by the threshold controller. Phase 15 renders this JSON as plain
    language; the user never sees raw parameters.
    """

    decision: ControlAction
    reason_code: ReasonCode
    service_level: float
    """Resource state at decision time (tank level, SoC, ...)."""

    flexibility_minutes: float
    """How long the decision could have been deferred."""

    solar_surplus_kw: float | None = None
    """Present PV generation minus base load. ``None`` if unmeasured."""

    expected_best_time: datetime | None = None
    """When the scheduler expects the best window to open. This is what
    makes a WAIT decision legible instead of alarming."""

    predicted_service_level_at_best_time: float | None = None
    notes: tuple[str, ...] = ()
    """Optional supporting detail. Never a substitute for ``reason_code``."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason_code": self.reason_code.value,
            "service_level": self.service_level,
            "flexibility_minutes": self.flexibility_minutes,
            "solar_surplus_kw": self.solar_surplus_kw,
            "expected_best_time": (
                self.expected_best_time.isoformat()
                if self.expected_best_time
                else None
            ),
            "predicted_service_level_at_best_time": (
                self.predicted_service_level_at_best_time
            ),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class PlannedAction:
    """One step of the future plan.

    Only step 0 is ever executed (MPC principle). The remainder is
    published so the UI can show intent and so experiments can measure how
    far plans drift from execution.
    """

    step_index: int
    target_time: datetime
    action: ControlAction
    predicted_service_level: float | None = None
    predicted_solar_kw: float | None = None


@dataclass(frozen=True, slots=True)
class SchedulingRequest:
    """Everything a scheduler is allowed to look at.

    Passing one object rather than a long argument list is what keeps the
    interface identical across all six schedulers as inputs grow through
    the roadmap. A simple scheduler ignores the fields it doesn't need —
    the threshold controller reads only ``observation``.
    """

    now: datetime
    horizon: Horizon

    resource: FlexibleResource
    """Physics only — ``constraints``, ``predict_trajectory``,
    ``estimate_flexibility``, ``admissible_actions``.

    A scheduler must **never** call ``resource.observe()``. The request
    already carries the observation for this cycle, and a second read would
    return a different state mid-decision, so the plan would no longer
    correspond to the state it was justified against.

    This is also the one field that stops a request being serializable, so
    a replay run reconstructs the resource from ``config`` plus
    ``observation.resource_id`` and rebuilds the request around it.
    """

    observation: ResourceObservation
    """The authoritative state for this cycle. The only state a scheduler
    is allowed to reason from."""

    actuation_history: ActuationHistory | None = None
    """Actuator timing, for the equipment constraints that ``observation``
    cannot express. ``None`` means unknown, and forces conservative
    handling — see ``FlexibleResource.admissible_actions``."""

    demand_forecast: Forecast | None = None
    solar_forecast: Forecast | None = None
    base_load_forecast: Forecast | None = None
    flexibility: FlexibilityEstimate | None = None
    solar_surplus_kw: float | None = None
    """Current instantaneous surplus. Distinct from ``solar_forecast`` —
    the reactive scheduler uses only this, the predictive ones only that."""

    anomaly_flagged: bool = False
    """Set by ``ml/anomaly``. Schedulers must respond by widening safety
    margins, not by refusing to decide."""

    provenance: Provenance = Provenance.MEASURED
    """Whether the observation came from hardware or the simulator.

    Deliberately ``Provenance`` and not ``RunMode``: this labels where the
    numbers came from, which is what ``scheduler_decisions.provenance``
    stores, and a replay run has no distinct answer of its own. Forecasts
    carry their own provenance and are not covered by this field."""

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SchedulingPlan:
    """The complete, auditable output of one scheduling cycle.

    Every field here is mandated by the project's hard rules — a scheduler
    that cannot fill one in must say so explicitly (``None``,
    ``NOT_APPLICABLE``) rather than omit it.
    """

    first_action: ControlAction
    """The only action that will actually be executed this cycle."""

    planned_actions: tuple[PlannedAction, ...]
    explanation: DecisionExplanation
    objective_value: float | None
    """``None`` for rule-based schedulers, which optimize nothing."""

    predicted_states: tuple[PredictedState, ...]
    constraint_status: ConstraintStatus
    solver_status: SolverStatus
    algorithm_version: str
    scheduler_name: str
    tier: SchedulerTier
    computed_at: datetime

    fallback_engaged: bool = False
    """True when a higher tier failed and this plan came from a lower one.
    Orthogonal to ``explanation.reason_code``, which still carries the
    substantive reason. Maps to ``scheduler_decisions.fallback_engaged``."""

    compute_ms: float | None = None
    """Measured runtime. Phase 12 checks this against the Pi Zero budget."""


class Scheduler(ABC):
    """Decides whether a flexible resource should act now.

    Contract:

    * ``generate_plan`` is pure with respect to system state — it reads a
      request and returns a plan. It does not write to the database, does
      not touch hardware, and does not mutate the resource.
    * It reads state **only** from the request. Calling
      ``request.resource.observe()`` is a contract violation: it re-reads
      the world mid-decision, so the resulting plan is justified against a
      state that was never the one acted on, and the decision stops being
      reproducible from its logged inputs.
    * It always returns a ``SchedulingPlan``. Internal failure is
      expressed as ``SolverStatus.ERROR`` with a safe ``first_action``, so
      the fallback chain can react; exceptions are for programmer error.
    * It never relaxes a hard constraint to find a solution.
    """

    name: str
    algorithm_version: str
    tier: SchedulerTier

    @abstractmethod
    def generate_plan(self, request: SchedulingRequest) -> SchedulingPlan:
        """Produce a plan for the request's horizon."""


class FallbackChain:
    """Ordered scheduler tiers with automatic degradation.

    Tries each scheduler in tier order and returns the first plan that is
    both produced without error and constraint-satisfying. Every fallback
    is logged with the tier that failed and why — silent degradation would
    make the system look like it is working when it is not.

    A tier is rejected for exactly three reasons, and they are kept
    separate in the log because they mean different things: it raised (a
    bug), it reported a non-usable ``SolverStatus`` (it knows it failed),
    or its plan does not satisfy the hard constraints (it produced
    something unsafe). Only the third is a scheduling failure; the first
    two are the scheduler telling the truth about itself.

    Phase 2 wires it up. Phase 9 fills in the upper tiers.
    """

    def __init__(self, schedulers: tuple[Scheduler, ...]) -> None:
        self._schedulers = tuple(sorted(schedulers, key=lambda s: s.tier))

    @property
    def schedulers(self) -> tuple[Scheduler, ...]:
        """Schedulers in descending order of sophistication."""
        return self._schedulers

    def generate_plan(self, request: SchedulingRequest) -> SchedulingPlan:
        """The first usable plan, in tier order.

        ``fallback_engaged`` is set on the returned plan whenever a more
        sophisticated tier was tried and rejected — never on the first tier
        that happens to be registered, because "the MPC was not installed"
        and "the MPC failed" are not the same event.

        The substantive ``reason_code`` from the tier that succeeded is
        preserved untouched. Falling back is not a rationale; it is a fact
        about which tier answered, and it is carried by ``fallback_engaged``
        and ``tier`` instead.
        """
        if not self._schedulers:
            raise ValueError(
                "fallback chain is empty; a chain with no tiers cannot degrade, "
                "it can only fail silently"
            )

        rejections: list[str] = []
        for scheduler in self._schedulers:
            try:
                plan = scheduler.generate_plan(request)
            except Exception as exc:  # noqa: BLE001 - a broken tier must not
                # take the chain down with it; that is what the chain is for.
                rejections.append(
                    f"{scheduler.name} raised {type(exc).__name__}: {exc}"
                )
                continue

            if plan.solver_status in _UNUSABLE_STATUSES:
                rejections.append(
                    f"{scheduler.name} reported solver_status="
                    f"{plan.solver_status.value}"
                )
                continue

            if not plan.constraint_status.satisfied:
                rejections.append(
                    f"{scheduler.name} returned a plan violating "
                    f"{len(plan.constraint_status.violations)} constraint(s)"
                )
                continue

            if not rejections:
                return plan
            return replace(
                plan,
                fallback_engaged=True,
                explanation=replace(
                    plan.explanation,
                    notes=plan.explanation.notes + tuple(rejections),
                ),
            )

        return self._safe_mode_plan(request, tuple(rejections))

    def _safe_mode_plan(
        self, request: SchedulingRequest, rejections: tuple[str, ...]
    ) -> SchedulingPlan:
        """Every tier failed. Hold the actuator and say so loudly.

        Holding — ``RUN`` if energized, ``WAIT`` if not — is the only action
        that no failure can make worse. A chain that has run out of opinions
        has no basis for a transition, and a spurious transition is the one
        outcome a total failure must not produce. The actuator's real
        backstops are the ESP32 deadman timer and the float switch, neither
        of which depends on this process being alive.

        ``constraint_status`` reports ``checked_constraints=()``: nothing was
        verified, which is deliberately distinguishable from everything
        having passed.
        """
        held = (
            ControlAction.RUN
            if request.observation.actuator_on
            else ControlAction.WAIT
        )
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
                notes=("every scheduler tier was rejected",) + rejections,
            ),
            objective_value=None,
            predicted_states=(),
            constraint_status=ConstraintStatus(
                satisfied=True,
                violations=(),
                checked_constraints=(),
            ),
            solver_status=SolverStatus.ERROR,
            algorithm_version="fallback-chain-1.0.0",
            scheduler_name="local_safe_mode",
            tier=SchedulerTier.LOCAL_SAFE_MODE,
            computed_at=request.now,
            fallback_engaged=True,
        )


_UNUSABLE_STATUSES = frozenset(
    {SolverStatus.INFEASIBLE, SolverStatus.TIMEOUT, SolverStatus.ERROR}
)
"""Statuses that disqualify a plan. ``NOT_APPLICABLE`` is not one of them —
a rule-based scheduler optimizes nothing and says so, which is a complete
answer rather than a failure."""
