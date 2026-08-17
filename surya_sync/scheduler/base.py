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
from dataclasses import dataclass, field
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

    FALLBACK_ENGAGED = "fallback_engaged"
    """A higher tier failed and a lower tier produced this decision."""


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
    observation: ResourceObservation
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
    """Whether the inputs came from hardware or the simulator."""

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
    compute_ms: float | None = None
    """Measured runtime. Phase 12 checks this against the Pi Zero budget."""


class Scheduler(ABC):
    """Decides whether a flexible resource should act now.

    Contract:

    * ``generate_plan`` is pure with respect to system state — it reads a
      request and returns a plan. It does not write to the database, does
      not touch hardware, and does not mutate the resource.
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

    Phase 0: structure only. Wired up in Phase 2 (two tiers) and completed
    in Phase 9.
    """

    def __init__(self, schedulers: tuple[Scheduler, ...]) -> None:
        self._schedulers = tuple(sorted(schedulers, key=lambda s: s.tier))

    @property
    def schedulers(self) -> tuple[Scheduler, ...]:
        """Schedulers in descending order of sophistication."""
        return self._schedulers

    def generate_plan(self, request: SchedulingRequest) -> SchedulingPlan:
        raise NotImplementedError("Phase 2 — see ROADMAP.md")
