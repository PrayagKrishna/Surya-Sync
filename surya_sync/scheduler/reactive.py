"""Tier 3 fallback: react to *current* solar surplus only. Baseline C.

This tier's whole contribution is one behaviour the threshold controller
does not have: when there is surplus PV *right now* and the tank is not
already full, top it up, even if hysteresis alone would have said WAIT.
Every other cycle it behaves exactly like the threshold controller —
because "reactive" means it may only look at ``request.solar_surplus_kw``,
never ``request.solar_forecast``. It has no idea whether the sun will still
be out in twenty minutes, so it never defers a run to wait for one; that is
Phase 7's job, once a solar forecast exists to defer towards.

For the same reason ``ReasonCode.AWAITING_SOLAR`` never appears here — its
own docstring is "a better-lit window is forecast", and this tier forecasts
nothing. Emitting it from a scheduler with no forecast would be a real, not
a defensible, WAIT.

Below the start threshold it runs regardless of solar, same as the
threshold controller: critical service does not wait for the sun. Above the
ceiling, or once an overflow lookahead says a step would breach one, it
stops or refuses to start, via the same one-step ``predict_trajectory``
check the threshold controller uses — the ``Scheduler`` contract requires
it, not solar intelligence.
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


class ReactiveScheduler(Scheduler):
    """Threshold hysteresis, plus an opportunistic top-up on current surplus."""

    name = "reactive"
    algorithm_version = "reactive-1.0.0"
    tier = SchedulerTier.REACTIVE_SOLAR

    def __init__(self, config: SchedulerConfig, surplus_threshold_kw: float) -> None:
        self._safety_reserve = config.safety_reserve_level
        self._surplus_threshold_kw = surplus_threshold_kw

    # --- thresholds -----------------------------------------------------

    def start_level(self, constraints: ResourceConstraints) -> float:
        """Below this, refilling begins regardless of solar. Identical
        definition to the threshold controller's — this tier only adds a
        reason to run *earlier*, never a reason to run later."""
        return min(
            constraints.service_level_min + self._safety_reserve,
            constraints.service_level_max,
        )

    def _has_surplus(self, request: SchedulingRequest) -> bool:
        """Surplus that would fully power the load, not merely nonzero.

        Measured, not assumed: an early version topped up on any surplus
        above the sensor-noise floor and *lost* to the threshold controller
        on ``spike`` (0.38 -> 0.51 kWh) and ``sunny`` (0.00 -> 0.20 kWh),
        because a partial surplus still draws the rest from the grid, and
        those extra opportunistic starts were grid draw the threshold
        controller's later, better-covered run would not have needed. A
        current-surplus scheduler only earns the name if it declines a run
        the grid would have to subsidize.
        """
        surplus = request.solar_surplus_kw
        if surplus is None:
            return False
        required = max(self._surplus_threshold_kw, request.resource.rated_power_kw())
        return surplus >= required

    # --- the decision ---------------------------------------------------

    def generate_plan(self, request: SchedulingRequest) -> SchedulingPlan:
        started = time.perf_counter()
        try:
            return self._plan(request, started)
        except Exception as exc:  # noqa: BLE001 - contract: never raise
            return self._error_plan(request, started, exc)

    def _plan(self, request: SchedulingRequest, started: float) -> SchedulingPlan:
        observation = request.observation
        constraints = request.resource.constraints()
        level = observation.service_level
        start_level = self.start_level(constraints)

        action, reason = self._decide(request, level, observation.actuator_on, constraints, start_level)

        # One-step lookahead, same contract obligation as the threshold
        # controller: never return a plan known to breach a hard constraint.
        predicted, checked = self._lookahead(request, action)
        if predicted is not None and predicted[0].violates_hard_constraint:
            alternative = (
                _idle_action(observation.actuator_on)
                if action is ControlAction.RUN
                else ControlAction.RUN
            )
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

    def _decide(
        self,
        request: SchedulingRequest,
        level: float,
        actuator_on: bool,
        constraints: ResourceConstraints,
        start_level: float,
    ) -> tuple[ControlAction, ReasonCode]:
        if actuator_on:
            # Already filling: finish the job, same hysteresis as the
            # threshold controller. Stopping mid-fill because a cloud
            # passed would trade one form of chatter for another.
            if level < constraints.service_level_max:
                return ControlAction.RUN, ReasonCode.BELOW_TARGET_LEVEL
            return ControlAction.STOP, ReasonCode.TARGET_REACHED

        if level <= constraints.service_level_critical:
            return ControlAction.RUN, ReasonCode.CRITICAL_LEVEL

        if level < start_level:
            # Needs water regardless of the sun. Critical service does not
            # wait for a forecast this tier does not have.
            reason = (
                ReasonCode.SOLAR_SURPLUS_AVAILABLE
                if self._has_surplus(request)
                else ReasonCode.CRITICAL_LEVEL
            )
            return ControlAction.RUN, reason

        if level < constraints.service_level_max and self._has_surplus(request):
            # The one behaviour that earns this tier its name: top up on
            # free energy the tank did not yet strictly need. The ceiling
            # check is checked here, not left to the lookahead alone,
            # because the lookahead is unavailable with no demand forecast
            # (see ``_lookahead``) and a full tank must never be proposed
            # a RUN regardless of whether a forecast exists to catch it.
            return ControlAction.RUN, ReasonCode.SOLAR_SURPLUS_AVAILABLE

        return ControlAction.WAIT, ReasonCode.SUFFICIENT_LEVEL

    def _respect_equipment_limits(
        self, request: SchedulingRequest, action: ControlAction, reason: ReasonCode
    ) -> tuple[ControlAction, ReasonCode]:
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
                "no demand forecast: decided on the current level and current "
                "solar surplus alone, without a predicted next state"
            )
        if request.solar_surplus_kw is None:
            notes.append("no solar surplus reading: cannot react, only threshold-gate")

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
                    predicted_solar_kw=request.solar_surplus_kw,
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
    return ControlAction.STOP if actuator_on else ControlAction.WAIT


def _hold_action(actuator_on: bool) -> ControlAction:
    return ControlAction.RUN if actuator_on else ControlAction.WAIT
