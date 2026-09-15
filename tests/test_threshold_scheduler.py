"""The threshold controller — Phase 2, Baseline A.

What is actually being asserted here is not "the controller is good". It is
that the controller is **honest**: it fills every field the ``Scheduler``
contract mandates, it never proposes an action the equipment forbids, it
never returns a plan it knows breaches a hard constraint, and it degrades
to plain hysteresis when it is handed nothing — because tier 4 is what runs
when everything else is broken.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from surya_sync.config.schema import Config
from surya_sync.domain import (
    ControlAction,
    Forecast,
    ForecastPoint,
    Horizon,
    Provenance,
)
from surya_sync.models.generic_resource import ActuationHistory
from surya_sync.scheduler.base import (
    ReasonCode,
    SchedulerTier,
    SchedulingRequest,
    SolverStatus,
)
from surya_sync.scheduler.threshold import ThresholdScheduler
from surya_sync.simulator.demand import ConstantDemandProfile
from surya_sync.simulator.solar import ClearSkyProfile
from surya_sync.simulator.tank import SimulatedTankResource, TankSimulator

NOW = datetime(2026, 3, 2, 9, 0)
STEP = 15.0
RESOURCE_ID = "tank_1"


@pytest.fixture
def config() -> Config:
    return Config()


@pytest.fixture
def scheduler(config) -> ThresholdScheduler:
    return ThresholdScheduler(config.scheduler)


def make_resource(
    config: Config, level: float, demand_lpm: float = 2.0, actuator_on: bool = False
) -> SimulatedTankResource:
    simulator = TankSimulator.from_config(
        config=config,
        demand_profile=ConstantDemandProfile(lpm=demand_lpm),
        solar_profile=ClearSkyProfile.from_config(config.solar),
        start=NOW,
        initial_service_level=level,
        resource_id=RESOURCE_ID,
    )
    simulator.actuator_on = actuator_on
    return SimulatedTankResource(simulator)


def settled(actuator_on: bool, minutes: float = 60.0) -> ActuationHistory:
    return ActuationHistory(
        resource_id=RESOURCE_ID,
        actuator_on=actuator_on,
        changed_at=NOW - timedelta(minutes=minutes),
        starts_today=1,
    )


def make_request(
    config: Config,
    level: float,
    *,
    demand_lpm: float = 2.0,
    actuator_on: bool = False,
    with_forecast: bool = True,
    history: ActuationHistory | None = "settled",  # type: ignore[assignment]
    forecast: Forecast | None = None,
) -> SchedulingRequest:
    resource = make_resource(config, level, demand_lpm, actuator_on)
    horizon_steps = int(config.scheduler.horizon_minutes // STEP)
    if history == "settled":
        history = settled(actuator_on)
    if forecast is None and with_forecast:
        forecast = resource.simulator.demand_profile.as_forecast(
            start=NOW, n_steps=horizon_steps, step_minutes=STEP
        )
    return SchedulingRequest(
        now=NOW,
        horizon=Horizon(
            start=NOW, length_minutes=horizon_steps * STEP, step_minutes=STEP
        ),
        resource=resource,
        observation=resource.observe(),
        actuation_history=history,
        demand_forecast=forecast,
        provenance=Provenance.SIMULATED,
    )


# --- the hysteresis itself ----------------------------------------------


def test_start_level_sits_a_reserve_above_the_planning_floor(config, scheduler):
    constraints = make_resource(config, 0.5).constraints()
    assert scheduler.start_level(constraints) == pytest.approx(
        config.tank.min_level + config.scheduler.safety_reserve_level
    )


def test_a_full_tank_waits(config, scheduler):
    plan = scheduler.generate_plan(make_request(config, level=0.80))
    assert plan.first_action is ControlAction.WAIT
    assert plan.explanation.reason_code is ReasonCode.SUFFICIENT_LEVEL


def test_a_low_tank_starts_refilling(config, scheduler):
    plan = scheduler.generate_plan(make_request(config, level=0.32))
    assert plan.first_action is ControlAction.RUN
    assert plan.explanation.reason_code is ReasonCode.CRITICAL_LEVEL


def test_a_refill_in_progress_is_not_reported_as_critical(config, scheduler):
    """A tank at 45% and filling is not an alarm. Reusing CRITICAL_LEVEL
    here would have the "Why?" screen cry wolf, and would make the count of
    genuine near-misses useless."""
    plan = scheduler.generate_plan(make_request(config, level=0.45, actuator_on=True))
    assert plan.first_action is ControlAction.RUN
    assert plan.explanation.reason_code is ReasonCode.BELOW_TARGET_LEVEL


def test_hysteresis_does_not_chatter_at_the_start_threshold(config, scheduler):
    """Just above the start level with the pump already running, the
    controller keeps running. A single threshold would stop here, the level
    would fall a point, and it would start again next cycle."""
    start = scheduler.start_level(make_resource(config, 0.5).constraints())
    plan = scheduler.generate_plan(
        make_request(config, level=start + 0.01, actuator_on=True)
    )
    assert plan.first_action is ControlAction.RUN


def test_a_tank_at_the_ceiling_stops(config, scheduler):
    plan = scheduler.generate_plan(
        make_request(config, level=config.tank.max_level, actuator_on=True)
    )
    assert plan.first_action is ControlAction.STOP
    assert plan.explanation.reason_code is ReasonCode.TARGET_REACHED


# --- the one-step lookahead ---------------------------------------------


def test_the_controller_stops_before_it_overflows_not_after(config, scheduler):
    """Phase 1 measured this: one 15-minute step moves the level 45 points,
    so a controller that stops *at* the ceiling has already overshot it.
    Below the ceiling and running, plain hysteresis says RUN; the lookahead
    is what turns that into a STOP."""
    level = 0.70
    assert level < config.tank.max_level
    request = make_request(config, level=level, actuator_on=True)

    would_overflow = request.resource.predict_trajectory(
        request.observation, (ControlAction.RUN,), request.demand_forecast, STEP
    )
    assert would_overflow[0].violates_hard_constraint

    plan = scheduler.generate_plan(request)
    assert plan.first_action is ControlAction.STOP
    assert plan.constraint_status.satisfied


def test_the_controller_runs_early_rather_than_break_the_floor(config, scheduler):
    """Above the start threshold, hysteresis alone says WAIT. With demand
    heavy enough that waiting one step breaches the critical floor, the
    lookahead promotes it to a RUN with a deadline reason."""
    plan = scheduler.generate_plan(
        make_request(config, level=0.36, demand_lpm=20.0)
    )
    assert plan.first_action is ControlAction.RUN
    assert plan.explanation.reason_code is ReasonCode.MUST_RUN_DEADLINE


def test_the_lookahead_predicts_exactly_what_will_happen(config, scheduler):
    """The prediction and the simulation both reach ``TankModel.step``. If
    they can drift, the controller is judged in a world it never searched."""
    request = make_request(config, level=0.32)
    plan = scheduler.generate_plan(request)

    simulator = request.resource.simulator
    step = simulator.advance(plan.first_action, STEP)
    assert plan.predicted_states[0].service_level == pytest.approx(
        step.service_level_end
    )


def test_a_satisfied_plan_names_the_constraints_it_checked(config, scheduler):
    plan = scheduler.generate_plan(make_request(config, level=0.50))
    assert plan.constraint_status.satisfied
    assert "service_level_critical" in plan.constraint_status.checked_constraints
    assert "service_level_max" in plan.constraint_status.checked_constraints


# --- degraded operation --------------------------------------------------


def test_it_still_decides_with_no_forecast_at_all(config, scheduler):
    """Tier 4 runs when everything else is broken, which includes the
    forecaster. It must produce a decision, not an error."""
    plan = scheduler.generate_plan(make_request(config, level=0.32, with_forecast=False))
    assert plan.first_action is ControlAction.RUN
    assert plan.solver_status is SolverStatus.NOT_APPLICABLE


def test_a_skipped_check_is_not_a_passed_check(config, scheduler):
    """Without a forecast there is no predicted state, so no constraint was
    verified. Reporting ``satisfied`` with an empty ``checked_constraints``
    is what keeps those two apart."""
    plan = scheduler.generate_plan(make_request(config, level=0.32, with_forecast=False))
    assert plan.constraint_status.checked_constraints == ()
    assert plan.predicted_states == ()
    assert any("no demand forecast" in note for note in plan.explanation.notes)


def test_it_never_proposes_an_action_the_equipment_forbids(config, scheduler):
    """The safety layer would catch it, but a scheduler that relies on being
    overruled fills the decision log with overrides that misattribute the
    cause."""
    cooling = ActuationHistory(
        resource_id=RESOURCE_ID,
        actuator_on=False,
        changed_at=NOW - timedelta(minutes=1.0),
        starts_today=1,
    )
    request = make_request(config, level=0.32, history=cooling)
    assert ControlAction.RUN not in request.resource.admissible_actions(
        request.observation, cooling, NOW
    )

    plan = scheduler.generate_plan(request)
    assert plan.first_action is ControlAction.WAIT
    assert plan.explanation.reason_code is ReasonCode.EQUIPMENT_COOLDOWN


def test_unknown_actuation_timing_holds_the_pump(config, scheduler):
    """``None`` history means unknown, and unknown must read as "the
    constraint is not satisfied" — never as a long-elapsed cooldown."""
    plan = scheduler.generate_plan(make_request(config, level=0.32, history=None))
    assert plan.first_action is ControlAction.WAIT
    assert plan.explanation.reason_code is ReasonCode.EQUIPMENT_COOLDOWN


def test_a_wrong_forecast_becomes_an_error_plan_not_an_exception(config, scheduler):
    """A PV series is structurally indistinguishable from a demand series.
    The tank rejects it; the scheduler must turn that into a plan the
    fallback chain can act on, because an exception would take the chain
    down with the tier that failed."""
    pv = Forecast(
        target="pv_generation_kw",
        issued_at=NOW,
        points=(ForecastPoint(target_time=NOW, p50=1.5, unit="kw"),),
        model_name="test",
        model_version="0",
    )
    plan = scheduler.generate_plan(make_request(config, level=0.32, forecast=pv))
    assert plan.solver_status is SolverStatus.ERROR
    assert not plan.constraint_status.satisfied


def test_a_failure_holds_the_actuator_rather_than_switching_it(config, scheduler):
    """A failed scheduler is not evidence that a transition is needed, and a
    spurious transition is the one thing a failure must not cause."""
    pv = Forecast(
        target="pv_generation_kw",
        issued_at=NOW,
        points=(ForecastPoint(target_time=NOW, p50=1.5, unit="kw"),),
        model_name="test",
        model_version="0",
    )
    running = scheduler.generate_plan(
        make_request(config, level=0.50, actuator_on=True, forecast=pv)
    )
    assert running.first_action is ControlAction.RUN

    idle = scheduler.generate_plan(make_request(config, level=0.50, forecast=pv))
    assert idle.first_action is ControlAction.WAIT


# --- the contract --------------------------------------------------------


def test_the_plan_fills_every_mandated_field(config, scheduler):
    plan = scheduler.generate_plan(make_request(config, level=0.50))
    assert plan.scheduler_name == "threshold"
    assert plan.algorithm_version == "threshold-1.0.0"
    assert plan.tier is SchedulerTier.THRESHOLD
    assert plan.objective_value is None
    assert plan.solver_status is SolverStatus.NOT_APPLICABLE
    assert plan.computed_at == NOW
    assert plan.compute_ms is not None and plan.compute_ms >= 0.0
    assert plan.explanation.decision is plan.first_action
    assert len(plan.planned_actions) == 1


def test_it_plans_one_step_and_claims_no_more(config, scheduler):
    """A threshold controller has no horizon. Publishing a longer plan would
    invent intent it does not have, and Phase 14 measures plan drift against
    exactly this field."""
    plan = scheduler.generate_plan(make_request(config, level=0.50))
    assert plan.planned_actions[0].step_index == 0
    assert plan.planned_actions[0].target_time == NOW


def test_the_explanation_survives_serialization(config, scheduler):
    plan = scheduler.generate_plan(make_request(config, level=0.32))
    payload = plan.explanation.to_dict()
    assert payload["decision"] == plan.first_action.value
    assert payload["reason_code"] == plan.explanation.reason_code.value


def test_it_reports_flexibility_when_it_can_estimate_it(config, scheduler):
    plan = scheduler.generate_plan(make_request(config, level=0.50))
    assert plan.explanation.flexibility_minutes > 0.0


def test_it_says_so_when_it_cannot_estimate_flexibility(config, scheduler):
    """Zero minutes of slack is a claim. Reporting it silently where nothing
    was computed would be a lie in the conservative direction, which is
    still a lie."""
    plan = scheduler.generate_plan(make_request(config, level=0.50, with_forecast=False))
    assert plan.explanation.flexibility_minutes == 0.0
    assert any("flexibility not estimated" in n for n in plan.explanation.notes)


def test_it_never_re_reads_the_resource(config, scheduler):
    """Contract violation: the request already carries the observation, and
    a second read would return a different state mid-decision."""
    request = make_request(config, level=0.32)
    calls = []
    original = request.resource.observe
    request.resource.observe = lambda: (calls.append(1), original())[1]  # type: ignore[method-assign]
    scheduler.generate_plan(request)
    assert calls == []


def test_the_lookahead_uses_the_step_the_driver_will_execute(config, scheduler):
    """The step comes from ``request.horizon``, not from construction. A
    remembered step can silently disagree with the driver, and a lookahead
    over the wrong interval would clear a plan that overflows."""
    resource = make_resource(config, 0.70, actuator_on=True)
    coarse = SchedulingRequest(
        now=NOW,
        horizon=Horizon(start=NOW, length_minutes=60.0, step_minutes=15.0),
        resource=resource,
        observation=resource.observe(),
        actuation_history=settled(True),
        demand_forecast=resource.simulator.demand_profile.as_forecast(
            start=NOW, n_steps=48, step_minutes=15.0
        ),
    )
    fine = SchedulingRequest(
        now=NOW,
        horizon=Horizon(start=NOW, length_minutes=60.0, step_minutes=1.0),
        resource=resource,
        observation=resource.observe(),
        actuation_history=settled(True),
        demand_forecast=coarse.demand_forecast,
    )
    assert scheduler.generate_plan(coarse).first_action is ControlAction.STOP
    assert scheduler.generate_plan(fine).first_action is ControlAction.RUN
