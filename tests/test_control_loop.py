"""The control loop, the fallback chain, and the Phase 2 exit criterion.

The exit criterion is the last test group: the threshold controller runs
against the simulator across the standard scenario set and never violates a
hard tank constraint. Everything above it exists because a passing run is
only evidence if the machinery around it is honest — a loop that silently
skipped the safety layer, or a chain that silently returned nothing, would
also produce a clean scoreboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from surya_sync.config.schema import Config
from surya_sync.control_loop import ControlCycle
from surya_sync.domain import ControlAction, Horizon, Provenance, RunMode
from surya_sync.experiments.runner import (
    build_control_cycle,
    run_scenario,
    run_standard_set,
)
from surya_sync.models.generic_resource import (
    ActuationHistory,
    ResourceObservation,
    ResourceRegistry,
)
from surya_sync.safety.rules import default_rules
from surya_sync.safety.validator import SafetyValidator
from surya_sync.scheduler.base import (
    ConstraintStatus,
    DecisionExplanation,
    FallbackChain,
    PlannedAction,
    ReasonCode,
    Scheduler,
    SchedulerTier,
    SchedulingPlan,
    SchedulingRequest,
    SolverStatus,
)
from surya_sync.scheduler.threshold import ThresholdScheduler
from surya_sync.simulator import scenarios
from surya_sync.simulator.demand import ConstantDemandProfile
from surya_sync.simulator.solar import ClearSkyProfile
from surya_sync.simulator.tank import SimulatedTankResource, TankSimulator
from surya_sync.state.state_manager import StateManager
from surya_sync.state.system_state import ActuatorState, SystemState
from surya_sync.version import VersionStamp

NOW = datetime(2026, 3, 2, 9, 0)
STEP = 15.0
RESOURCE_ID = "tank_1"


@pytest.fixture
def config() -> Config:
    return Config()


def make_resource(config: Config, level: float, actuator_on: bool = False):
    simulator = TankSimulator.from_config(
        config=config,
        demand_profile=ConstantDemandProfile(lpm=2.0),
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


def make_state_and_request(
    config: Config,
    level: float,
    *,
    actuator_on: bool = False,
    manual_override: ControlAction | None = None,
    sensor_valid: bool = True,
    history: ActuationHistory | None = None,
) -> tuple[SystemState, SchedulingRequest, ResourceRegistry]:
    resource = make_resource(config, level, actuator_on)
    registry = ResourceRegistry(resources=(resource,))
    history = history if history is not None else settled(actuator_on)
    observation = ResourceObservation(
        timestamp=NOW,
        resource_id=RESOURCE_ID,
        service_level=level,
        native_value=level * config.tank.capacity_l,
        native_unit="l",
        actuator_on=actuator_on,
        provenance=Provenance.SIMULATED,
        sensor_valid=sensor_valid,
    )
    state = SystemState(
        timestamp=NOW,
        run_id=None,
        mode=RunMode.SIMULATED,
        versions=VersionStamp(),
        observations={RESOURCE_ID: observation},
        actuation_history={RESOURCE_ID: history},
        manual_override=manual_override,
    )
    request = SchedulingRequest(
        now=NOW,
        horizon=Horizon(start=NOW, length_minutes=720.0, step_minutes=STEP),
        resource=resource,
        observation=observation,
        actuation_history=history,
        demand_forecast=resource.simulator.demand_profile.as_forecast(
            start=NOW, n_steps=48, step_minutes=STEP
        ),
        provenance=Provenance.SIMULATED,
    )
    return state, request, registry


def make_cycle(config: Config, registry: ResourceRegistry, *schedulers) -> ControlCycle:
    validator = SafetyValidator(
        default_rules(registry, max_runtime_minutes=config.pump.max_runtime_seconds / 60.0)
    )
    return ControlCycle(validator, FallbackChain(tuple(schedulers)))


# --- fallback chain ------------------------------------------------------


class Stub(Scheduler):
    """A scheduler whose behaviour is dictated, for testing the chain."""

    def __init__(self, name, tier, action=ControlAction.WAIT, status=None, raises=None,
                 satisfied=True, reason=ReasonCode.SUFFICIENT_LEVEL):
        self.name = name
        self.algorithm_version = f"{name}-0.0.0"
        self.tier = tier
        self._action = action
        self._status = status or SolverStatus.NOT_APPLICABLE
        self._raises = raises
        self._satisfied = satisfied
        self._reason = reason
        self.calls = 0

    def generate_plan(self, request):
        self.calls += 1
        if self._raises:
            raise self._raises
        return SchedulingPlan(
            first_action=self._action,
            planned_actions=(
                PlannedAction(step_index=0, target_time=request.now, action=self._action),
            ),
            explanation=DecisionExplanation(
                decision=self._action,
                reason_code=self._reason,
                service_level=request.observation.service_level,
                flexibility_minutes=42.0,
            ),
            objective_value=None,
            predicted_states=(),
            constraint_status=ConstraintStatus(
                satisfied=self._satisfied,
                violations=() if self._satisfied else ("made up",),
            ),
            solver_status=self._status,
            algorithm_version=self.algorithm_version,
            scheduler_name=self.name,
            tier=self.tier,
            computed_at=request.now,
        )


def test_the_first_usable_tier_answers(config):
    _, request, _ = make_state_and_request(config, 0.5)
    top = Stub("mpc", SchedulerTier.RISK_AWARE_MPC)
    low = Stub("threshold", SchedulerTier.THRESHOLD)
    plan = FallbackChain((low, top)).generate_plan(request)

    assert plan.scheduler_name == "mpc"
    assert plan.fallback_engaged is False
    assert low.calls == 0


def test_a_raising_tier_does_not_take_the_chain_down(config):
    _, request, _ = make_state_and_request(config, 0.5)
    top = Stub("mpc", SchedulerTier.RISK_AWARE_MPC, raises=RuntimeError("solver died"))
    low = Stub("threshold", SchedulerTier.THRESHOLD)
    plan = FallbackChain((low, top)).generate_plan(request)

    assert plan.scheduler_name == "threshold"
    assert plan.fallback_engaged is True
    assert any("solver died" in note for note in plan.explanation.notes)


@pytest.mark.parametrize(
    "status", [SolverStatus.INFEASIBLE, SolverStatus.TIMEOUT, SolverStatus.ERROR]
)
def test_a_tier_that_reports_failure_is_skipped(config, status):
    _, request, _ = make_state_and_request(config, 0.5)
    top = Stub("mpc", SchedulerTier.RISK_AWARE_MPC, status=status)
    low = Stub("threshold", SchedulerTier.THRESHOLD)
    plan = FallbackChain((low, top)).generate_plan(request)
    assert plan.scheduler_name == "threshold"


def test_not_applicable_is_not_a_failure(config):
    """Rule-based schedulers optimize nothing and say so. That is a complete
    answer, not a reason to degrade past them."""
    _, request, _ = make_state_and_request(config, 0.5)
    only = Stub("threshold", SchedulerTier.THRESHOLD, status=SolverStatus.NOT_APPLICABLE)
    plan = FallbackChain((only,)).generate_plan(request)
    assert plan.scheduler_name == "threshold"
    assert plan.fallback_engaged is False


def test_a_plan_that_violates_a_hard_constraint_is_rejected(config):
    _, request, _ = make_state_and_request(config, 0.5)
    top = Stub("mpc", SchedulerTier.RISK_AWARE_MPC, satisfied=False)
    low = Stub("threshold", SchedulerTier.THRESHOLD)
    plan = FallbackChain((low, top)).generate_plan(request)
    assert plan.scheduler_name == "threshold"


def test_the_substantive_reason_survives_a_fallback(config):
    """There is deliberately no FALLBACK_ENGAGED reason code. Falling back
    is not a rationale — the tier that answered still decided for a reason,
    and that reason is what the "Why?" screen must show."""
    _, request, _ = make_state_and_request(config, 0.5)
    top = Stub("mpc", SchedulerTier.RISK_AWARE_MPC, raises=RuntimeError("x"))
    low = Stub("threshold", SchedulerTier.THRESHOLD, reason=ReasonCode.CRITICAL_LEVEL)
    plan = FallbackChain((low, top)).generate_plan(request)

    assert plan.explanation.reason_code is ReasonCode.CRITICAL_LEVEL
    assert plan.fallback_engaged is True


def test_a_single_registered_tier_is_not_a_fallback(config):
    """"The MPC was never installed" and "the MPC failed" are different
    events and must not log identically."""
    _, request, _ = make_state_and_request(config, 0.5)
    plan = FallbackChain((Stub("threshold", SchedulerTier.THRESHOLD),)).generate_plan(request)
    assert plan.fallback_engaged is False


def test_every_tier_failing_holds_the_actuator(config):
    _, request, _ = make_state_and_request(config, 0.5, actuator_on=True)
    chain = FallbackChain(
        (
            Stub("mpc", SchedulerTier.RISK_AWARE_MPC, raises=RuntimeError("a")),
            Stub("threshold", SchedulerTier.THRESHOLD, raises=RuntimeError("b")),
        )
    )
    plan = chain.generate_plan(request)

    assert plan.tier is SchedulerTier.LOCAL_SAFE_MODE
    assert plan.first_action is ControlAction.RUN  # held, not stopped
    assert plan.fallback_engaged is True
    assert plan.solver_status is SolverStatus.ERROR


def test_safe_mode_does_not_claim_to_have_checked_anything(config):
    """``checked_constraints=()`` with ``satisfied=True`` is the documented
    way to say "nothing was verified" without blocking execution of a hold."""
    _, request, _ = make_state_and_request(config, 0.5)
    chain = FallbackChain((Stub("t", SchedulerTier.THRESHOLD, raises=RuntimeError("b")),))
    plan = chain.generate_plan(request)
    assert plan.constraint_status.checked_constraints == ()


def test_an_empty_chain_is_a_programming_error(config):
    _, request, _ = make_state_and_request(config, 0.5)
    with pytest.raises(ValueError, match="empty"):
        FallbackChain(()).generate_plan(request)


# --- the control cycle ---------------------------------------------------


def test_safety_runs_before_the_scheduler(config):
    """The hard rule, made structural: when the gate forces an action the
    scheduler is never called at all."""
    state, request, registry = make_state_and_request(config, level=0.05)
    scheduler = Stub("threshold", SchedulerTier.THRESHOLD, action=ControlAction.WAIT)
    decision = make_cycle(config, registry, scheduler).decide(state, request)

    assert scheduler.calls == 0
    assert decision.action is ControlAction.RUN
    assert not decision.scheduler_consulted


def test_a_forced_action_still_gets_an_explanation(config):
    """Every decision produces a structured explanation — including the ones
    the scheduler never saw. Those are exactly the cycles a user asks about."""
    state, request, registry = make_state_and_request(config, level=0.05)
    decision = make_cycle(
        config, registry, Stub("threshold", SchedulerTier.THRESHOLD)
    ).decide(state, request)

    assert decision.plan.explanation.reason_code is ReasonCode.CRITICAL_LEVEL
    assert "the scheduler was not consulted" in decision.plan.explanation.notes


def test_a_forced_action_keeps_the_substantive_reason(config):
    """"A safety rule fired" is a fact about the decision path, not a
    rationale. The household needs to read why."""
    state, request, registry = make_state_and_request(
        config, level=0.5, manual_override=ControlAction.RUN
    )
    decision = make_cycle(
        config, registry, Stub("threshold", SchedulerTier.THRESHOLD)
    ).decide(state, request)
    assert decision.plan.explanation.reason_code is ReasonCode.MANUAL_OVERRIDE


def test_an_invalid_sensor_is_named_as_such(config):
    state, request, registry = make_state_and_request(config, level=0.5, sensor_valid=False)
    decision = make_cycle(
        config, registry, Stub("threshold", SchedulerTier.THRESHOLD)
    ).decide(state, request)
    assert decision.plan.explanation.reason_code is ReasonCode.SENSOR_INVALID
    assert decision.action is ControlAction.STOP


def test_a_bad_proposal_is_overridden_after_the_fact(config):
    """The gate is silent when nothing is compelled. That is precisely when
    a scheduler can propose something a rule forbids, and post-scheduling
    validation is the only thing standing there."""
    state, request, registry = make_state_and_request(config, level=0.5)
    scheduler = Stub("threshold", SchedulerTier.THRESHOLD, action=ControlAction.STOP)
    decision = make_cycle(config, registry, scheduler).decide(state, request)

    assert scheduler.calls == 1
    assert decision.safety_overridden
    assert decision.action is ControlAction.WAIT
    assert decision.plan.explanation.reason_code is ReasonCode.SAFETY_OVERRIDE


def test_an_override_records_what_was_wanted_and_why(config):
    """"Safety said no" on its own tells nobody which scheduler needs fixing."""
    state, request, registry = make_state_and_request(config, level=0.5)
    scheduler = Stub(
        "threshold",
        SchedulerTier.THRESHOLD,
        action=ControlAction.STOP,
        reason=ReasonCode.TARGET_REACHED,
    )
    decision = make_cycle(config, registry, scheduler).decide(state, request)
    notes = " ".join(decision.plan.explanation.notes)
    assert "proposed STOP" in notes
    assert "target_reached" in notes


def test_agreement_with_safety_is_not_recorded_as_an_override(config):
    state, request, registry = make_state_and_request(config, level=0.5)
    scheduler = Stub("threshold", SchedulerTier.THRESHOLD, action=ControlAction.WAIT)
    decision = make_cycle(config, registry, scheduler).decide(state, request)
    assert not decision.safety_overridden
    assert decision.plan.explanation.reason_code is ReasonCode.SUFFICIENT_LEVEL


def test_the_cycle_decides_but_never_actuates(config):
    """Actuation is where *command sent != command executed*. A function
    that both decided and actuated would have nowhere to record it."""
    state, request, registry = make_state_and_request(config, level=0.05)
    before = request.resource.simulator.actuator_on
    make_cycle(config, registry, Stub("t", SchedulerTier.THRESHOLD)).decide(state, request)
    assert request.resource.simulator.actuator_on is before


# --- the state manager ---------------------------------------------------


def test_a_snapshot_cannot_be_mutated_from_outside(config):
    manager = StateManager(mode=RunMode.SIMULATED, versions=VersionStamp())
    manager.begin_run(run_id=1, at=NOW)
    first = manager.snapshot()
    manager.record_desired_action(RESOURCE_ID, ControlAction.RUN, NOW)
    assert RESOURCE_ID not in first.actuation


def test_an_empty_state_manager_refuses_to_pretend(config):
    """An empty snapshot looks exactly like a healthy system with nothing
    connected, which is the worst possible thing for it to look like."""
    manager = StateManager(mode=RunMode.SIMULATED, versions=VersionStamp())
    with pytest.raises(RuntimeError, match="no state recorded"):
        manager.snapshot()


def test_a_desired_action_does_not_imply_execution(config):
    """The hard rule: command sent != command executed."""
    manager = StateManager(mode=RunMode.SIMULATED, versions=VersionStamp())
    manager.begin_run(run_id=1, at=NOW)
    manager.record_desired_action(RESOURCE_ID, ControlAction.RUN, NOW)

    actuation = manager.snapshot().actuation[RESOURCE_ID]
    assert actuation.desired is ActuatorState.ON
    assert actuation.reported is ActuatorState.UNKNOWN
    assert actuation.confirmed is ActuatorState.UNKNOWN
    assert not actuation.is_consistent


def test_late_telemetry_does_not_rewind_the_clock(config):
    """Out-of-order arrivals are normal. Letting one rewind the clock would
    make a snapshot claim to be older than the observation it contains."""
    manager = StateManager(mode=RunMode.SIMULATED, versions=VersionStamp())
    manager.begin_run(run_id=1, at=NOW)
    manager.record_desired_action(RESOURCE_ID, ControlAction.RUN, NOW + timedelta(minutes=15))
    manager.record_desired_action(RESOURCE_ID, ControlAction.WAIT, NOW)
    assert manager.snapshot().timestamp == NOW + timedelta(minutes=15)


def test_a_real_link_starts_unhealthy(config):
    """A Phase 11 bug that never sets the flag must fail closed."""
    real = StateManager(mode=RunMode.REAL, versions=VersionStamp())
    real.begin_run(run_id=1, at=NOW)
    assert real.snapshot().hardware_link_healthy is False

    simulated = StateManager(mode=RunMode.SIMULATED, versions=VersionStamp())
    simulated.begin_run(run_id=1, at=NOW)
    assert simulated.snapshot().hardware_link_healthy is True


# --- Phase 2 exit criterion ----------------------------------------------


@pytest.mark.parametrize("name", ["sunny", "cloudy", "spike", "low_start"])
def test_the_threshold_controller_never_violates_a_hard_constraint(config, name):
    """**The Phase 2 exit criterion.**

    Hard constraints are hard: ``critical <= level <= max``, and no spill.
    A violation here is not a scheduling shortfall, it is a broken promise,
    so the assertion is on zero and not on a rate.
    """
    scenario = getattr(scenarios, name)(config)
    run = run_scenario(config, scenario, ThresholdScheduler(config.scheduler))

    assert run.violation_steps == (), (
        f"{name}: {len(run.violation_steps)} violating steps, first at "
        f"{run.violation_steps[0].start if run.violation_steps else None}"
    )
    assert run.spilled_l == 0.0
    assert run.unmet_demand_l == 0.0


def test_the_standard_set_runs_the_full_duration(config):
    """A run that quietly stopped early would also report zero violations."""
    runs = run_standard_set(
        config, scenarios.standard_set(config), lambda: ThresholdScheduler(config.scheduler)
    )
    assert len(runs) == 4
    for run in runs:
        assert len(run.steps) == 3 * 1440 / config.scheduler.step_minutes
        assert len(run.decisions) == len(run.steps)


def test_the_run_is_deterministic(config):
    """Reproducibility is the precondition for every comparison from Phase 3
    onward. Two identical runs must agree exactly, not approximately."""
    scenario = scenarios.spike(config)
    first = run_scenario(config, scenario, ThresholdScheduler(config.scheduler))
    second = run_scenario(config, scenario, ThresholdScheduler(config.scheduler))

    assert [s.volume_end_l for s in first.steps] == [s.volume_end_l for s in second.steps]
    assert [d.action for d in first.decisions] == [d.action for d in second.decisions]


def test_the_controller_actually_ran_the_pump(config):
    """Zero violations is trivially achievable by never doing anything. The
    run only means something if the controller was actually in control."""
    run = run_scenario(
        config, scenarios.low_start(config), ThresholdScheduler(config.scheduler)
    )
    assert run.starts > 0
    assert run.pump_energy_kwh > 0.0
    assert any(step.actuator_on for step in run.steps)


def test_the_pump_stays_within_its_daily_start_budget(config):
    """Equipment protection is not only about the hard tank band. A
    controller that short-cycles has passed the exit criterion and broken
    the pump."""
    for scenario in scenarios.standard_set(config):
        run = run_scenario(config, scenario, ThresholdScheduler(config.scheduler))
        per_day = run.starts / scenario.days
        assert per_day <= config.pump.max_starts_per_day


def test_the_energy_split_accounts_for_every_kwh(config):
    """Grid plus solar must equal what the pump drew. Phase 3's whole claim
    rests on this split, so it is checked before anything is claimed."""
    run = run_scenario(
        config, scenarios.cloudy(config), ThresholdScheduler(config.scheduler)
    )
    assert run.grid_energy_kwh + run.solar_energy_kwh == pytest.approx(
        run.pump_energy_kwh
    )


def test_results_are_labelled_simulated(config):
    """Never present simulation results as physical results."""
    run = run_scenario(
        config, scenarios.sunny(config), ThresholdScheduler(config.scheduler)
    )
    assert run.provenance is Provenance.SIMULATED
    assert all(step.provenance is Provenance.SIMULATED for step in run.steps)


def test_the_first_cycle_is_held_by_the_cooldown(config):
    """A simulated world begins with ``changed_at`` at the clock, so the
    actuator reads as having *just* changed state and min-off is not yet
    satisfied. The pump is therefore held for one cycle at the start of
    every run. Conservative and harmless here — but Phase 11 inherits the
    same arithmetic from a boot timestamp, so it is pinned rather than
    rediscovered."""
    run = run_scenario(
        config, scenarios.sunny(config), ThresholdScheduler(config.scheduler)
    )
    first = run.decisions[0]
    assert first.action is ControlAction.WAIT
    assert first.plan.explanation.reason_code is ReasonCode.EQUIPMENT_COOLDOWN
    assert not first.scheduler_consulted


# --- what Phase 2 measured and could not fix -----------------------------


def test_a_safety_override_is_not_filed_as_local_safe_mode(config):
    """``LOCAL_SAFE_MODE`` means the Pi is gone and the ESP32 is on its own.
    A safety rule firing means the Pi is working exactly as designed. They
    are opposite states of health, and Phase 14 counts tiers."""
    state, request, registry = make_state_and_request(config, level=0.05)
    decision = make_cycle(
        config, registry, Stub("threshold", SchedulerTier.THRESHOLD)
    ).decide(state, request)

    assert decision.plan.tier is SchedulerTier.SAFETY_LAYER
    assert decision.plan.scheduler_name == "safety:critical_service"


def test_the_safety_layer_outranks_the_whole_chain(config):
    assert SchedulerTier.SAFETY_LAYER < min(
        t for t in SchedulerTier if t is not SchedulerTier.SAFETY_LAYER
    )


@pytest.mark.parametrize("step_minutes", [30.0, 60.0])
def test_a_control_step_this_coarse_cannot_hold_the_band(config, step_minutes):
    """**A measured limit, not a bug to fix in the controller.**

    At 30 lpm into a 1000 L tank, a 30-minute step delivers 900 L. Every
    available action breaches something: running overflows, waiting runs the
    level under the floor. The loop behaves correctly — it declines to
    overflow, the level falls, and the critical-service rule then forces a
    run that overflows anyway.

    The assertion is that this *still fails*, so that nobody raises
    ``scheduler.step_minutes`` past what the pump can be steered at and
    reads a clean scoreboard. The exit criterion holds at the configured
    15 minutes and is not a claim about any step.
    """
    from dataclasses import replace as _replace

    coarse = _replace(
        config, scheduler=_replace(config.scheduler, step_minutes=step_minutes)
    )
    run = run_scenario(coarse, scenarios.sunny(coarse), ThresholdScheduler(coarse.scheduler))
    assert run.violation_steps, (
        "a step this coarse is expected to breach the band; if it no longer "
        "does, the pump, the tank or the physics changed and this limit needs "
        "re-measuring rather than deleting"
    )


def test_a_cold_start_holds_the_pump_until_the_floor_forces_it(config):
    """**Phase 11 owes a fix, and here is the cost of not having one.**

    Phase 1 found that unknown actuator timing forbids the only transition
    that would resolve it. This measures what that costs once a safety layer
    exists: the pump is held for most of a day, and the critical-service
    rule is reactive — it sees the level only after it has already fallen —
    so the hard floor is breached once before anything starts the pump.

    The safety layer is a floor, not a plan. ``state/`` must establish a
    boot timestamp from the actuation log or from boot time; nothing in the
    control loop can substitute for it.
    """
    run = run_scenario(
        config,
        scenarios.sunny(config),
        ThresholdScheduler(config.scheduler),
        cold_start=True,
    )
    assert run.starts == 0 or run.violation_steps
    assert all(
        decision.plan.explanation.reason_code is ReasonCode.EQUIPMENT_COOLDOWN
        for decision in run.decisions[:4]
    )

    warm = run_scenario(config, scenarios.sunny(config), ThresholdScheduler(config.scheduler))
    assert warm.violation_steps == ()


def test_the_daily_start_budget_yields_to_critical_service(config):
    """A documented consequence of the project's priority order, measured
    rather than assumed: critical service (5) outranks equipment constraints
    (7), so ``max_starts_per_day`` is a preference the safety layer will
    overrule to keep water in the tank. Phase 8's optimizer must not treat
    it as an inviolable bound."""
    from dataclasses import replace as _replace

    stingy = _replace(config, pump=_replace(config.pump, max_starts_per_day=1))
    run = run_scenario(
        stingy, scenarios.low_start(stingy), ThresholdScheduler(stingy.scheduler)
    )
    assert run.starts > stingy.pump.max_starts_per_day
    assert run.violation_steps == ()
