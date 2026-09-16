"""The reactive scheduler — Phase 3, Baseline C.

What is being asserted is that this tier adds exactly one behaviour on top
of the threshold controller's hysteresis: an opportunistic top-up when
``solar_surplus_kw`` says the sun is out right now. Everything else —
critical service, the ceiling, the one-step lookahead, equipment limits,
degraded operation with no forecast — must behave identically to the
threshold controller, because this tier is still the threshold controller
whenever there is no surplus to react to.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from surya_sync.config.schema import Config
from surya_sync.domain import ControlAction, Horizon, Provenance
from surya_sync.models.generic_resource import ActuationHistory
from surya_sync.scheduler.base import ReasonCode, SchedulerTier, SchedulingRequest, SolverStatus
from surya_sync.scheduler.reactive import ReactiveScheduler
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
def scheduler(config) -> ReactiveScheduler:
    return ReactiveScheduler(config.scheduler, config.solar.surplus_threshold_kw)


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
    solar_surplus_kw: float | None = 0.0,
) -> SchedulingRequest:
    resource = make_resource(config, level, demand_lpm, actuator_on)
    horizon_steps = int(config.scheduler.horizon_minutes // STEP)
    if history == "settled":
        history = settled(actuator_on)
    forecast = None
    if with_forecast:
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
        solar_surplus_kw=solar_surplus_kw,
        provenance=Provenance.SIMULATED,
    )


# --- the one new behaviour: opportunistic top-up on current surplus -----


def test_sufficient_level_with_no_surplus_waits(config, scheduler):
    """Same as the threshold controller: nothing needs doing, sun or not."""
    plan = scheduler.generate_plan(make_request(config, level=0.80, solar_surplus_kw=0.0))
    assert plan.first_action is ControlAction.WAIT
    assert plan.explanation.reason_code is ReasonCode.SUFFICIENT_LEVEL


def test_sufficient_level_with_surplus_tops_up_anyway(config, scheduler):
    """The behaviour this tier exists for: free energy is on the table, and
    the tank is not full, so it takes it — something the threshold
    controller, blind to solar, would never do.

    Level 0.40: above the ~0.35 start threshold (so hysteresis alone would
    say WAIT) but low enough that one full step of pump inflow (0.45 of
    capacity at 30 lpm over 15 min) does not push it past the 0.95 ceiling.
    """
    plan = scheduler.generate_plan(make_request(config, level=0.40, solar_surplus_kw=1.0))
    assert plan.first_action is ControlAction.RUN
    assert plan.explanation.reason_code is ReasonCode.SOLAR_SURPLUS_AVAILABLE


def test_surplus_below_the_noise_threshold_does_not_count(config, scheduler):
    tiny = config.solar.surplus_threshold_kw / 2.0
    plan = scheduler.generate_plan(make_request(config, level=0.80, solar_surplus_kw=tiny))
    assert plan.first_action is ControlAction.WAIT


def test_a_full_tank_never_tops_up(config, scheduler):
    plan = scheduler.generate_plan(
        make_request(config, level=config.tank.max_level, actuator_on=True, solar_surplus_kw=5.0)
    )
    assert plan.first_action is ControlAction.STOP
    assert plan.explanation.reason_code is ReasonCode.TARGET_REACHED


def test_a_full_idle_tank_never_starts_for_surplus_even_with_no_forecast(config, scheduler):
    """The ceiling check for the opportunistic top-up must not rely solely
    on the one-step lookahead: with no demand forecast the lookahead is
    unavailable (see ``_lookahead``), and a tank already at the ceiling
    must still never be proposed a RUN."""
    plan = scheduler.generate_plan(
        make_request(
            config,
            level=config.tank.max_level,
            actuator_on=False,
            with_forecast=False,
            solar_surplus_kw=5.0,
        )
    )
    assert plan.first_action is ControlAction.WAIT


# --- critical service never waits for the sun ----------------------------


def test_a_low_tank_starts_regardless_of_surplus(config, scheduler):
    plan = scheduler.generate_plan(make_request(config, level=0.32, solar_surplus_kw=0.0))
    assert plan.first_action is ControlAction.RUN
    assert plan.explanation.reason_code is ReasonCode.CRITICAL_LEVEL


def test_a_low_tank_with_surplus_is_credited_to_solar(config, scheduler):
    """Same action as the no-surplus case, but the honest reason this time
    is that the sun happens to be doing the work."""
    plan = scheduler.generate_plan(make_request(config, level=0.32, solar_surplus_kw=1.0))
    assert plan.first_action is ControlAction.RUN
    assert plan.explanation.reason_code is ReasonCode.SOLAR_SURPLUS_AVAILABLE


def test_a_refill_in_progress_keeps_running_without_solar(config, scheduler):
    plan = scheduler.generate_plan(
        make_request(config, level=0.45, actuator_on=True, solar_surplus_kw=0.0)
    )
    assert plan.first_action is ControlAction.RUN
    assert plan.explanation.reason_code is ReasonCode.BELOW_TARGET_LEVEL


# --- never AWAITING_SOLAR: this tier has no forecast to justify it -------


def test_it_never_emits_awaiting_solar(config, scheduler):
    """This reason code means 'a better-lit window is forecast'. This tier
    sees only the current instant and must never claim to be waiting for a
    window it cannot see."""
    for level in (0.10, 0.32, 0.50, 0.80, 1.0):
        for surplus in (0.0, 5.0):
            plan = scheduler.generate_plan(
                make_request(config, level=level, solar_surplus_kw=surplus)
            )
            assert plan.explanation.reason_code is not ReasonCode.AWAITING_SOLAR


# --- the one-step lookahead still applies --------------------------------


def test_it_never_overflows_even_while_chasing_solar(config, scheduler):
    """A tank near the ceiling with surplus available must still stop
    before it overflows — the lookahead is not solar-aware and should not
    need to be; it is the same contract obligation as tier 4's."""
    level = 0.70
    assert level < config.tank.max_level
    request = make_request(config, level=level, actuator_on=True, solar_surplus_kw=5.0)

    would_overflow = request.resource.predict_trajectory(
        request.observation, (ControlAction.RUN,), request.demand_forecast, STEP
    )
    assert would_overflow[0].violates_hard_constraint

    plan = scheduler.generate_plan(request)
    assert plan.first_action is ControlAction.STOP
    assert plan.constraint_status.satisfied


# --- degraded operation ---------------------------------------------------


def test_it_still_decides_with_no_forecast_at_all(config, scheduler):
    plan = scheduler.generate_plan(
        make_request(config, level=0.32, with_forecast=False, solar_surplus_kw=0.0)
    )
    assert plan.first_action is ControlAction.RUN
    assert plan.solver_status is SolverStatus.NOT_APPLICABLE


def test_it_notes_when_there_is_no_solar_reading_at_all(config, scheduler):
    """``None`` means unmeasured, not zero. A missing reading and a reading
    of exactly zero surplus must not be silently collapsed."""
    plan = scheduler.generate_plan(make_request(config, level=0.80, solar_surplus_kw=None))
    assert plan.first_action is ControlAction.WAIT
    assert any("no solar surplus reading" in note for note in plan.explanation.notes)


def test_it_never_proposes_an_action_the_equipment_forbids(config, scheduler):
    cooling = ActuationHistory(
        resource_id=RESOURCE_ID,
        actuator_on=False,
        changed_at=NOW - timedelta(minutes=1.0),
        starts_today=1,
    )
    request = make_request(config, level=0.32, history=cooling, solar_surplus_kw=5.0)
    assert ControlAction.RUN not in request.resource.admissible_actions(
        request.observation, cooling, NOW
    )

    plan = scheduler.generate_plan(request)
    assert plan.first_action is ControlAction.WAIT
    assert plan.explanation.reason_code is ReasonCode.EQUIPMENT_COOLDOWN


def test_it_never_re_reads_the_resource(config, scheduler):
    request = make_request(config, level=0.32, solar_surplus_kw=0.0)
    calls = []
    original = request.resource.observe
    request.resource.observe = lambda: (calls.append(1), original())[1]  # type: ignore[method-assign]
    scheduler.generate_plan(request)
    assert calls == []


# --- the contract ----------------------------------------------------------


def test_the_plan_fills_every_mandated_field(config, scheduler):
    plan = scheduler.generate_plan(make_request(config, level=0.50, solar_surplus_kw=0.0))
    assert plan.scheduler_name == "reactive"
    assert plan.algorithm_version == "reactive-1.0.0"
    assert plan.tier is SchedulerTier.REACTIVE_SOLAR
    assert plan.objective_value is None
    assert plan.solver_status is SolverStatus.NOT_APPLICABLE
    assert plan.computed_at == NOW
    assert plan.compute_ms is not None and plan.compute_ms >= 0.0
    assert plan.explanation.decision is plan.first_action
    assert len(plan.planned_actions) == 1
