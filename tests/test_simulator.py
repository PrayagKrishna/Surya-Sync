"""``TankSimulator`` and ``SimulatedTankResource`` — Phase 1 exit criteria.

Two claims carry the phase:

1. A multi-day trajectory is **deterministic**. Without it no experiment
   from Phase 14 is reproducible and no A/B comparison means anything.
2. ``predict_trajectory`` and ``TankSimulator.advance`` run the **same
   physics**. If they can drift, the optimizer is searching over a world
   that is not the one it will be judged in — and that divergence would
   show up as "the scheduler is bad" rather than as the modelling bug it
   really is.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from surya_sync.config.schema import Config
from surya_sync.domain import ControlAction, Forecast, ForecastPoint, Provenance, ResourceType
from surya_sync.models.generic_resource import (
    ActuationHistory,
    FlexibleResource,
    ResourceObservation,
)
from surya_sync.simulator import scenarios
from surya_sync.simulator.demand import ConstantDemandProfile, DiurnalDemandProfile
from surya_sync.simulator.grid import ConstantBaseLoadProfile, DiurnalBaseLoadProfile
from surya_sync.simulator.solar import ClearSkyProfile
from surya_sync.simulator.tank import (
    SimulatedTankResource,
    TankSimulator,
    conservative_demand_at,
    demand_at,
    forecast_step_minutes,
    tank_constraints,
)

START = datetime(2026, 3, 1, 6, 0)
STEP = 15.0


@pytest.fixture
def config() -> Config:
    return Config()


def build(config: Config, *, demand_lpm: float = 2.0, level: float = 0.6, **kwargs) -> TankSimulator:
    return TankSimulator.from_config(
        config=config,
        demand_profile=ConstantDemandProfile(lpm=demand_lpm),
        solar_profile=ClearSkyProfile.from_config(config.solar),
        start=START,
        initial_service_level=level,
        base_load_profile=ConstantBaseLoadProfile(kw=0.3),
        **kwargs,
    )


def hysteresis_run(
    resource: SimulatedTankResource, n_steps: int, step: float = STEP
) -> list:
    """A deliberately naive driver, standing in until Phase 2 builds a real
    threshold controller. It exists to exercise the simulator, not to
    schedule well."""
    simulator = resource.simulator
    steps = []
    for _ in range(n_steps):
        observation = simulator.observe()
        allowed = resource.admissible_actions(
            observation, simulator.actuation_history(), simulator.clock
        )
        if observation.service_level < 0.45 and ControlAction.RUN in allowed:
            action = ControlAction.RUN
        elif observation.service_level > 0.80 and ControlAction.STOP in allowed:
            action = ControlAction.STOP
        else:
            action = ControlAction.RUN if observation.actuator_on else ControlAction.WAIT
        steps.append(simulator.advance(action, step))
    return steps


# --- construction -------------------------------------------------------


def test_constraints_come_from_config(config):
    constraints = tank_constraints(config)
    assert constraints.service_level_critical == config.tank.critical_level
    assert constraints.service_level_min == config.tank.min_level
    assert constraints.service_level_max == config.tank.max_level
    assert constraints.min_off_minutes == config.pump.min_off_minutes
    assert constraints.max_starts_per_day == config.pump.max_starts_per_day


def test_initial_level_maps_to_litres(config):
    simulator = build(config, level=0.6)
    assert simulator.volume_l == pytest.approx(600.0)
    assert simulator.service_level == pytest.approx(0.6)


def test_a_volume_outside_the_tank_is_rejected(config):
    simulator = build(config)
    with pytest.raises(ValueError):
        TankSimulator(
            tank=simulator.tank,
            pump=simulator.pump,
            constraints=simulator.constraints,
            demand_profile=simulator.demand_profile,
            solar_profile=simulator.solar_profile,
            clock=START,
            volume_l=5000.0,
        )


def test_the_resource_satisfies_the_flexible_resource_contract(config):
    resource = SimulatedTankResource(build(config))
    assert isinstance(resource, FlexibleResource)
    assert resource.resource_type is ResourceType.WATER_TANK
    assert resource.resource_id == "tank_1"
    assert resource.model_version


def test_observations_are_labelled_simulated(config):
    observation = SimulatedTankResource(build(config)).observe()
    assert observation.provenance is Provenance.SIMULATED
    assert observation.native_unit == "l"
    assert observation.timestamp == START


def test_rated_power_comes_from_the_pump(config):
    assert SimulatedTankResource(build(config)).rated_power_kw() == pytest.approx(0.75)


# --- advancing ----------------------------------------------------------


def test_a_step_moves_the_clock(config):
    simulator = build(config)
    step = simulator.advance(ControlAction.WAIT, 15.0)
    assert step.start == START
    assert step.end == START + timedelta(minutes=15)
    assert simulator.clock == step.end


def test_waiting_drains_the_tank(config):
    simulator = build(config, demand_lpm=2.0)
    step = simulator.advance(ControlAction.WAIT, 15.0)
    assert step.volume_end_l == pytest.approx(600.0 - 30.0)
    assert not step.actuator_on


def test_running_fills_the_tank(config):
    simulator = build(config, demand_lpm=2.0, level=0.3)
    step = simulator.advance(ControlAction.RUN, 15.0)
    assert step.inflow_lpm == pytest.approx(30.0)
    assert step.volume_end_l == pytest.approx(300.0 + (30.0 - 2.0) * 15.0)


def test_a_zero_length_step_is_rejected(config):
    with pytest.raises(ValueError):
        build(config).advance(ControlAction.WAIT, 0.0)


def test_starting_the_pump_records_the_transition(config):
    simulator = build(config)
    assert simulator.starts_today == 0
    simulator.advance(ControlAction.RUN, 15.0)
    assert simulator.starts_today == 1
    assert simulator.last_start_at == START
    assert simulator.changed_at == START


def test_holding_a_running_pump_is_not_a_new_start(config):
    simulator = build(config)
    simulator.advance(ControlAction.RUN, 15.0)
    simulator.advance(ControlAction.RUN, 15.0)
    assert simulator.starts_today == 1


def test_the_daily_start_count_resets_at_midnight(config):
    simulator = build(config, demand_lpm=0.0)
    simulator.advance(ControlAction.RUN, 15.0)
    simulator.advance(ControlAction.STOP, 15.0)
    assert simulator.starts_today == 1
    simulator.clock = START + timedelta(days=1)
    simulator.advance(ControlAction.RUN, 15.0)
    assert simulator.starts_today == 1


def test_the_simulator_executes_illegal_actions_faithfully(config):
    """Admissibility is a gate in ``safety/`` and the scheduler, not here. A
    simulator that quietly corrected a bad command could not be used to
    demonstrate a scheduler behaving badly."""
    simulator = build(config)
    simulator.advance(ControlAction.RUN, 1.0)
    observation = simulator.observe()
    allowed = simulator.pump.admissible_actions(
        observation.actuator_on, simulator.actuation_history(), simulator.clock
    )
    assert ControlAction.STOP not in allowed
    step = simulator.advance(ControlAction.STOP, 1.0)
    assert not step.actuator_on


def test_a_cold_start_holds_the_pump(config):
    """No actuation log means no known timing, so no start is permitted —
    and nothing in the loop resolves that on its own. ``state/`` has to
    establish a timestamp in Phase 11."""
    simulator = build(config, cold_start=True)
    resource = SimulatedTankResource(simulator)
    assert simulator.changed_at is None
    for _ in range(20):
        observation = simulator.observe()
        allowed = resource.admissible_actions(
            observation, simulator.actuation_history(), simulator.clock
        )
        assert allowed == (ControlAction.WAIT,)
        simulator.advance(ControlAction.WAIT, STEP)


def test_a_warm_start_knows_when_the_actuator_settled(config):
    simulator = build(config)
    assert simulator.changed_at == START
    assert simulator.actuation_history().minutes_in_state(START) == 0.0


# --- energy accounting --------------------------------------------------


def test_an_idle_pump_draws_no_controllable_power(config):
    step = build(config).advance(ControlAction.WAIT, 15.0)
    assert step.energy.controllable_kw == 0.0
    assert step.pump_energy_kwh == 0.0


def test_a_running_pump_draws_its_rated_power(config):
    step = build(config).advance(ControlAction.RUN, 60.0)
    assert step.energy.controllable_kw == pytest.approx(0.75)
    assert step.pump_energy_kwh == pytest.approx(0.75)


def test_running_at_night_is_entirely_grid_powered(config):
    simulator = build(config)
    simulator.clock = START.replace(hour=2)
    step = simulator.advance(ControlAction.RUN, 60.0)
    assert step.pv_kw == 0.0
    assert step.energy.controllable_solar_kwh == 0.0
    assert step.energy.controllable_grid_kwh == pytest.approx(0.75)


def test_running_at_midday_is_mostly_solar_powered(config):
    simulator = build(config)
    simulator.clock = START.replace(hour=12)
    step = simulator.advance(ControlAction.RUN, 60.0)
    assert step.pv_kw > 1.0
    assert step.energy.controllable_solar_kwh == pytest.approx(0.75)
    assert step.energy.controllable_grid_kwh == 0.0


def test_the_same_run_costs_differently_by_time_of_day(config):
    """The premise of the entire project, asserted once: identical pump work
    draws different amounts of grid energy depending only on when it ran."""

    def grid_kwh(hour: int) -> float:
        simulator = build(config)
        simulator.clock = START.replace(hour=hour)
        return simulator.advance(ControlAction.RUN, 60.0).energy.controllable_grid_kwh

    assert grid_kwh(2) > grid_kwh(12)


# --- hard constraints ---------------------------------------------------


def test_overflow_is_flagged_as_a_violation(config):
    simulator = build(config, demand_lpm=0.0, level=0.99)
    step = simulator.advance(ControlAction.RUN, 60.0)
    assert step.spilled_l > 0.0
    assert step.violates_hard_constraint


def test_falling_below_critical_is_flagged_as_a_violation(config):
    simulator = build(config, demand_lpm=30.0, level=0.25)
    step = simulator.advance(ControlAction.WAIT, 60.0)
    assert step.service_level_end < config.tank.critical_level
    assert step.violates_hard_constraint


def test_dipping_below_the_planning_floor_is_not_a_violation(config):
    """``min_level`` is where the plan went wrong; ``critical_level`` is
    where the household did. Conflating them would make every cautious
    scheduler look unsafe."""
    simulator = build(config, demand_lpm=5.0, level=0.32)
    step = simulator.advance(ControlAction.WAIT, 15.0)
    assert step.service_level_end == pytest.approx(0.245)
    assert step.service_level_end < config.tank.min_level
    assert step.service_level_end > config.tank.critical_level
    assert not step.violates_hard_constraint


# --- the physics must not fork ------------------------------------------


def test_prediction_and_simulation_produce_identical_trajectories(config):
    """The load-bearing test of the phase.

    ``predict_trajectory`` is what the optimizer searches over and
    ``advance`` is what it will be scored against. Given the same actions
    and a perfect demand forecast they must agree exactly — not
    approximately — because they are required to call the same
    ``TankModel.step``.
    """
    demand = DiurnalDemandProfile(daily_volume_l=450.0)
    actions = tuple(
        ControlAction.RUN if (k // 3) % 2 == 0 else ControlAction.WAIT for k in range(48)
    )

    simulator = TankSimulator.from_config(
        config=config,
        demand_profile=demand,
        solar_profile=ClearSkyProfile.from_config(config.solar),
        start=START,
        initial_service_level=0.6,
    )
    resource = SimulatedTankResource(simulator)

    observation = simulator.observe()
    forecast = demand.as_forecast(START, n_steps=len(actions), step_minutes=STEP)
    predicted = resource.predict_trajectory(observation, actions, forecast, STEP)

    simulated = simulator.run(actions, STEP)

    assert len(predicted) == len(simulated)
    for expected, actual in zip(predicted, simulated):
        assert expected.timestamp == actual.end
        assert expected.native_value == pytest.approx(actual.volume_end_l, abs=1e-9)
        assert expected.service_level == pytest.approx(actual.service_level_end, abs=1e-12)
        assert expected.actuator_on == actual.actuator_on


def test_predictions_are_labelled_predicted(config):
    resource = SimulatedTankResource(build(config))
    forecast = ConstantDemandProfile(lpm=2.0).as_forecast(START, 4, STEP)
    states = resource.predict_trajectory(
        resource.observe(), (ControlAction.WAIT,) * 4, forecast, STEP
    )
    assert all(state.provenance is Provenance.PREDICTED for state in states)


def test_prediction_starts_from_the_observation_not_the_live_state(config):
    """Schedulers are contractually forbidden from re-observing mid-cycle,
    so the trajectory must honour the snapshot it was handed."""
    simulator = build(config, demand_lpm=0.0, level=0.6)
    resource = SimulatedTankResource(simulator)
    snapshot = simulator.observe()

    simulator.advance(ControlAction.RUN, 60.0)
    assert simulator.volume_l > snapshot.native_value

    forecast = ConstantDemandProfile(lpm=0.0).as_forecast(START, 4, STEP)
    states = resource.predict_trajectory(
        snapshot, (ControlAction.WAIT,) * 4, forecast, STEP
    )
    assert states[0].native_value == pytest.approx(snapshot.native_value)


def test_prediction_does_not_silently_fix_an_illegal_plan(config):
    """Rewriting a proposed sequence would hide an optimizer bug behind a
    trajectory that looks fine."""
    resource = SimulatedTankResource(build(config, demand_lpm=0.0))
    forecast = ConstantDemandProfile(lpm=0.0).as_forecast(START, 4, STEP)
    actions = (ControlAction.RUN, ControlAction.STOP, ControlAction.RUN, ControlAction.STOP)
    states = resource.predict_trajectory(resource.observe(), actions, forecast, STEP)
    assert [state.actuator_on for state in states] == [True, False, True, False]


def test_an_empty_forecast_is_refused_rather_than_read_as_zero_demand(config):
    """Assuming no demand makes every trajectory look safe. That is the one
    optimistic default this project cannot afford."""
    resource = SimulatedTankResource(build(config))
    empty = Forecast(
        target="water_demand_lpm",
        issued_at=START,
        points=(),
        model_name="test",
        model_version="0",
    )
    with pytest.raises(ValueError, match="no points"):
        resource.predict_trajectory(resource.observe(), (ControlAction.WAIT,), empty, STEP)


def test_a_non_positive_step_is_rejected(config):
    resource = SimulatedTankResource(build(config))
    forecast = ConstantDemandProfile(lpm=1.0).as_forecast(START, 2, STEP)
    with pytest.raises(ValueError):
        resource.predict_trajectory(resource.observe(), (ControlAction.WAIT,), forecast, 0.0)


# --- forecast lookup ----------------------------------------------------


def _banded_forecast() -> Forecast:
    return Forecast(
        target="water_demand_lpm",
        issued_at=START,
        points=(
            ForecastPoint(target_time=START, p50=1.0, p10=0.5, p90=3.0),
            ForecastPoint(target_time=START + timedelta(minutes=15), p50=2.0),
        ),
        model_name="test",
        model_version="0",
    )


def test_lookup_holds_each_point_until_the_next(config):
    forecast = _banded_forecast()
    assert demand_at(forecast, START) == 1.0
    assert demand_at(forecast, START + timedelta(minutes=14)) == 1.0
    assert demand_at(forecast, START + timedelta(minutes=15)) == 2.0
    assert demand_at(forecast, START + timedelta(hours=5)) == 2.0


def test_lookup_before_the_first_point_uses_the_first_point(config):
    forecast = _banded_forecast()
    assert demand_at(forecast, START - timedelta(hours=1)) == 1.0


def test_the_conservative_lookup_plans_for_a_thirstier_household(config):
    forecast = _banded_forecast()
    assert conservative_demand_at(forecast, START) == 3.0


def test_the_conservative_lookup_falls_back_to_p50_without_a_band(config):
    forecast = _banded_forecast()
    assert conservative_demand_at(forecast, START + timedelta(minutes=15)) == 2.0


def test_the_step_is_inferred_from_point_spacing(config):
    assert forecast_step_minutes(_banded_forecast()) == pytest.approx(15.0)


def test_a_single_point_forecast_falls_back_to_the_default_step(config):
    forecast = Forecast(
        target="water_demand_lpm",
        issued_at=START,
        points=(ForecastPoint(target_time=START, p50=1.0),),
        model_name="test",
        model_version="0",
    )
    assert forecast_step_minutes(forecast) == pytest.approx(15.0)


# --- flexibility --------------------------------------------------------


def test_flexibility_is_reported_in_minutes_of_deferral(config):
    """600 L, a 300 L planning floor and 10 lpm draw leaves 30 minutes."""
    simulator = build(config, demand_lpm=10.0, level=0.6)
    resource = SimulatedTankResource(simulator)
    forecast = ConstantDemandProfile(lpm=10.0).as_forecast(START, 48, 5.0)
    estimate = resource.estimate_flexibility(simulator.observe(), forecast)
    assert estimate.flexibility_minutes == pytest.approx(30.0, abs=5.0)


def test_the_safety_runway_is_never_shorter_than_the_planning_slack(config):
    simulator = build(config, demand_lpm=10.0, level=0.6)
    resource = SimulatedTankResource(simulator)
    forecast = ConstantDemandProfile(lpm=10.0).as_forecast(START, 48, 5.0)
    estimate = resource.estimate_flexibility(simulator.observe(), forecast)
    assert estimate.time_to_critical_minutes >= estimate.flexibility_minutes


def test_flexibility_is_labelled_estimated(config):
    simulator = build(config)
    resource = SimulatedTankResource(simulator)
    forecast = ConstantDemandProfile(lpm=2.0).as_forecast(START, 8, STEP)
    estimate = resource.estimate_flexibility(simulator.observe(), forecast)
    assert estimate.provenance is Provenance.ESTIMATED
    assert estimate.resource_id == "tank_1"


def test_a_full_tank_with_no_demand_has_no_deadline(config):
    simulator = build(config, demand_lpm=0.0, level=0.9)
    resource = SimulatedTankResource(simulator)
    forecast = ConstantDemandProfile(lpm=0.0).as_forecast(START, 48, STEP)
    estimate = resource.estimate_flexibility(simulator.observe(), forecast)
    assert estimate.must_run_by is None
    assert estimate.flexibility_minutes == pytest.approx(48 * STEP)


def test_a_draining_tank_has_a_deadline_inside_the_horizon(config):
    simulator = build(config, demand_lpm=10.0, level=0.4)
    resource = SimulatedTankResource(simulator)
    forecast = ConstantDemandProfile(lpm=10.0).as_forecast(START, 48, STEP)
    estimate = resource.estimate_flexibility(simulator.observe(), forecast)
    assert estimate.must_run_by is not None
    assert START <= estimate.must_run_by <= START + timedelta(minutes=48 * STEP)


def test_a_heavier_forecast_shortens_the_flexibility(config):
    simulator = build(config, demand_lpm=5.0, level=0.6)
    resource = SimulatedTankResource(simulator)
    observation = simulator.observe()
    light = resource.estimate_flexibility(
        observation, ConstantDemandProfile(lpm=5.0).as_forecast(START, 96, 5.0)
    )
    heavy = resource.estimate_flexibility(
        observation, ConstantDemandProfile(lpm=20.0).as_forecast(START, 96, 5.0)
    )
    assert heavy.flexibility_minutes < light.flexibility_minutes


def test_flexibility_uses_the_p90_band_when_one_exists(config):
    """Conservative by construction: a band must shorten the estimate, never
    leave it unchanged."""
    simulator = build(config, level=0.6)
    resource = SimulatedTankResource(simulator)
    observation = simulator.observe()

    points_p50 = tuple(
        ForecastPoint(target_time=START + timedelta(minutes=5 * k), p50=5.0)
        for k in range(96)
    )
    points_banded = tuple(
        ForecastPoint(target_time=START + timedelta(minutes=5 * k), p50=5.0, p10=1.0, p90=20.0)
        for k in range(96)
    )

    def estimate(points):
        return resource.estimate_flexibility(
            observation,
            Forecast(
                target="water_demand_lpm",
                issued_at=START,
                points=points,
                model_name="test",
                model_version="0",
            ),
        ).flexibility_minutes

    assert estimate(points_banded) < estimate(points_p50)


# --- determinism and multi-day trajectories -----------------------------


def test_a_three_day_trajectory_is_reproducible(config):
    """Phase 1 exit criterion. Every later comparison depends on it."""
    scenario = scenarios.sunny(config, days=3)

    def run() -> list[tuple]:
        resource = SimulatedTankResource(scenario.build(config))
        return [
            (step.start, step.volume_end_l, step.actuator_on, step.energy.controllable_grid_kw)
            for step in hysteresis_run(resource, scenario.n_steps(STEP))
        ]

    assert run() == run()


def test_a_three_day_trajectory_actually_covers_three_days(config):
    scenario = scenarios.sunny(config, days=3)
    resource = SimulatedTankResource(scenario.build(config))
    steps = hysteresis_run(resource, scenario.n_steps(STEP))
    assert steps[0].start == scenario.start
    assert steps[-1].end == scenario.end
    assert len(steps) == 288


def test_the_pump_runs_and_the_household_is_served(config):
    """A trajectory in which the pump never starts would pass a
    determinism check while proving nothing."""
    scenario = scenarios.sunny(config, days=3)
    resource = SimulatedTankResource(scenario.build(config))
    steps = hysteresis_run(resource, scenario.n_steps(STEP))
    assert any(step.actuator_on for step in steps)
    assert sum(step.unmet_demand_l for step in steps) == 0.0


def test_the_daily_start_budget_is_respected_over_three_days(config):
    scenario = scenarios.sunny(config, days=3)
    resource = SimulatedTankResource(scenario.build(config))
    hysteresis_run(resource, scenario.n_steps(STEP))
    assert resource.simulator.starts_today <= config.pump.max_starts_per_day


def test_a_coarse_control_step_overshoots_the_target_band(config):
    """A control-resolution limit, recorded so it is not later mistaken for
    a physics bug.

    At 30 lpm the pump fills this 1000 L tank in 33 minutes, so one
    15-minute step moves the level by 45 percentage points. A controller
    that only *reacts* to crossing a threshold therefore cannot avoid
    sailing past it, however the thresholds are placed. Phase 2's threshold
    controller has to predict the fill and stop early; a purely reactive
    one will look bad here for reasons that are not its fault.
    """
    scenario = scenarios.sunny(config, days=3)
    stop_threshold = 0.80

    def peak_level(step_minutes: float) -> float:
        resource = SimulatedTankResource(scenario.build(config))
        steps = hysteresis_run(resource, scenario.n_steps(step_minutes), step_minutes)
        return max(step.service_level_end for step in steps)

    fine, coarse = peak_level(1.0), peak_level(15.0)
    assert fine == pytest.approx(stop_threshold, abs=0.02)
    assert coarse > stop_threshold + 0.05
    assert coarse > fine


# --- scenarios ----------------------------------------------------------


def test_the_standard_set_is_the_documented_four(config):
    names = [scenario.name for scenario in scenarios.standard_set(config)]
    assert names == ["sunny", "cloudy", "spike", "low_start"]


def test_every_scenario_runs_without_starving_the_household(config):
    for scenario in scenarios.standard_set(config, days=2):
        resource = SimulatedTankResource(scenario.build(config))
        steps = hysteresis_run(resource, scenario.n_steps(5.0), 5.0)
        assert steps, scenario.name
        assert sum(step.unmet_demand_l for step in steps) == 0.0, scenario.name


def test_building_a_scenario_twice_gives_independent_simulators(config):
    """One run must never be contaminated by a previous controller's state."""
    scenario = scenarios.sunny(config, days=1)
    first = scenario.build(config)
    second = scenario.build(config)
    first.advance(ControlAction.RUN, 60.0)
    assert second.volume_l != first.volume_l
    assert second.clock == scenario.start


def test_low_start_begins_just_above_the_hard_floor(config):
    scenario = scenarios.low_start(config)
    assert config.tank.critical_level < scenario.initial_service_level < config.tank.min_level


def test_the_spike_scenario_really_spikes(config):
    scenario = scenarios.spike(config)
    profile = scenario.demand_profile
    spike_moment = scenarios.DEFAULT_START + timedelta(days=1, hours=11, minutes=10)
    quiet_moment = scenarios.DEFAULT_START + timedelta(days=1, hours=11, minutes=50)
    assert profile.lpm_at(spike_moment) > profile.lpm_at(quiet_moment) + 5.0


def test_the_cloudy_scenario_generates_less_than_the_sunny_one(config):
    sunny = scenarios.sunny(config)
    cloudy = scenarios.cloudy(config)
    noon = scenarios.DEFAULT_START.replace(hour=12)
    sunny_kwh = sunny.solar_profile.energy_kwh(noon, 240.0, 5.0)
    cloudy_kwh = cloudy.solar_profile.energy_kwh(noon, 240.0, 5.0)
    assert cloudy_kwh < sunny_kwh


def test_scenario_geometry_is_validated(config):
    with pytest.raises(ValueError):
        scenarios.Scenario(
            name="bad",
            description="",
            demand_profile=ConstantDemandProfile(lpm=1.0),
            solar_profile=ClearSkyProfile.from_config(config.solar),
            base_load_profile=DiurnalBaseLoadProfile(),
            initial_service_level=0.5,
            days=0.0,
        )


def test_scenario_step_count_matches_its_duration(config):
    scenario = scenarios.sunny(config, days=2)
    assert scenario.n_steps(15.0) == 192
    assert scenario.total_minutes() == pytest.approx(2880.0)


# --- history handed to the scheduler ------------------------------------


def test_the_simulator_reports_a_usable_actuation_history(config):
    simulator = build(config)
    simulator.advance(ControlAction.RUN, 15.0)
    history = simulator.actuation_history()
    assert isinstance(history, ActuationHistory)
    assert history.actuator_on
    assert history.provenance is Provenance.DERIVED
    assert history.minutes_in_state(simulator.clock) == pytest.approx(15.0)


def test_admissible_actions_read_the_observation_not_the_simulator(config):
    """The resource must answer about the state it was handed, so a stale
    snapshot produces a stale — but honest — answer."""
    simulator = build(config)
    resource = SimulatedTankResource(simulator)
    stale = ResourceObservation(
        timestamp=START,
        resource_id="tank_1",
        service_level=0.5,
        native_value=500.0,
        native_unit="l",
        actuator_on=True,
        provenance=Provenance.SIMULATED,
    )
    assert not simulator.actuator_on
    actions = resource.admissible_actions(stale, simulator.actuation_history(), START)
    assert ControlAction.RUN in actions
    assert ControlAction.WAIT not in actions
