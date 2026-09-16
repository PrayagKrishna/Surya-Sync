"""Execute a scenario against a scheduler, reproducibly.

Phase 2 implements the closed loop: build a scenario's simulator, drive it
one control step at a time through ``ControlCycle``, and keep every step
and every decision. Phase 14 extends this into the full experiment runner
(versioned configs, multiple controllers, persisted results); the loop
itself does not change, which is the point of writing it here rather than
inside a test.

**The runner drives; it does not decide.** Every decision comes from
``ControlCycle``, which is the same object the hardware loop will use in
Phase 11. The runner's only privileges are the ones a simulation driver is
entitled to: it may reach into ``SimulatedTankResource.simulator`` to
actuate and to read ground truth. A scheduler may not, and does not.

**The demand forecast handed to the scheduler here is the simulator's own
ground truth.** It is a *perfect* forecast — the upper bound a real
forecaster is measured against, never evidence of forecasting performance.
Phase 5 replaces it with a model that can be wrong, and any result produced
before then carries that caveat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from surya_sync.config.schema import Config
from surya_sync.control_loop import ControlCycle, CycleDecision
from surya_sync.domain import Forecast, Horizon, Provenance, RunMode
from surya_sync.models.generic_resource import ResourceRegistry
from surya_sync.safety.rules import default_rules
from surya_sync.safety.validator import SafetyValidator
from surya_sync.scheduler.base import FallbackChain, Scheduler, SchedulingRequest
from surya_sync.simulator.scenarios import Scenario
from surya_sync.simulator.tank import SimulatedTankResource, SimulationStep
from surya_sync.state.state_manager import StateManager
from surya_sync.state.system_state import ElectricalState
from surya_sync.version import VersionStamp


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    """Everything one controller did under one scenario.

    Steps and decisions are parallel sequences: ``steps[i]`` is what the
    world did in response to ``decisions[i]``. Keeping both, rather than a
    merged summary, is what lets Phase 14 ask questions nobody thought of
    in Phase 2.
    """

    scenario_name: str
    scenario_version: str
    scheduler_name: str
    algorithm_version: str
    config_hash: str | None
    step_minutes: float
    steps: tuple[SimulationStep, ...]
    decisions: tuple[CycleDecision, ...]
    provenance: Provenance = Provenance.SIMULATED
    """Simulated throughout. Never report these numbers as measured."""

    @property
    def violation_steps(self) -> tuple[SimulationStep, ...]:
        """Steps that ended outside the hard constraint band.

        The Phase 2 exit criterion is that this is empty for every scenario
        in the standard set.
        """
        return tuple(step for step in self.steps if step.violates_hard_constraint)

    @property
    def unmet_demand_l(self) -> float:
        return sum(step.unmet_demand_l for step in self.steps)

    @property
    def spilled_l(self) -> float:
        return sum(step.spilled_l for step in self.steps)

    @property
    def pump_energy_kwh(self) -> float:
        return sum(step.pump_energy_kwh for step in self.steps)

    @property
    def solar_energy_kwh(self) -> float:
        """Pump energy met by surplus PV. With ``grid_energy_kwh`` this
        sums to ``pump_energy_kwh``; the split is the whole argument."""
        return sum(step.energy.controllable_solar_kwh for step in self.steps)

    @property
    def grid_energy_kwh(self) -> float:
        """Pump energy drawn from the grid. Phase 3's headline number."""
        return sum(step.energy.controllable_grid_kwh for step in self.steps)

    @property
    def starts(self) -> int:
        """Pump starts: transitions from off to on across the run."""
        count = 0
        previous = False
        for step in self.steps:
            if step.actuator_on and not previous:
                count += 1
            previous = step.actuator_on
        return count

    @property
    def safety_overrides(self) -> int:
        return sum(1 for decision in self.decisions if decision.safety_overridden)

    @property
    def forced_by_safety(self) -> int:
        """Cycles where the pre-scheduling gate decided outright."""
        return sum(1 for decision in self.decisions if not decision.scheduler_consulted)


def build_control_cycle(
    config: Config, registry: ResourceRegistry, schedulers: tuple[Scheduler, ...]
) -> ControlCycle:
    """Assemble the safety layer and the fallback chain for a run.

    One place, so a simulation and the Phase 11 hardware loop cannot end up
    with different rule sets — which would make every simulated safety
    result unverifiable on the roof.
    """
    validator = SafetyValidator(
        default_rules(
            registry=registry,
            max_runtime_minutes=config.pump.max_runtime_seconds / 60.0,
        )
    )
    return ControlCycle(validator, FallbackChain(schedulers))


def run_scenario(
    config: Config,
    scenario: Scenario,
    scheduler: Scheduler | tuple[Scheduler, ...],
    resource_id: str = "tank_1",
    config_hash: str | None = None,
    cold_start: bool = False,
) -> ScenarioRun:
    """Run one controller through one scenario, start to finish.

    ``scheduler`` accepts either a single ``Scheduler`` (Phase 2's call
    shape, kept working unchanged) or a tuple of them. A tuple is a real
    fallback chain, not several independent runs: ``FallbackChain`` sorts
    it by tier and only drops to a lower one when the higher one raises,
    reports a failing ``SolverStatus``, or breaches a hard constraint. A
    scenario benchmarking one tier in isolation should still pass a tuple
    of exactly that one scheduler rather than lean on the single-scheduler
    form meaning "no fallback exists" — it doesn't; it means the chain has
    one link. ``ScenarioRun.scheduler_name`` / ``algorithm_version`` name
    the most sophisticated tier passed in, since that is "the controller
    under test" even on cycles where it happened to fail over.

    ``cold_start`` boots the resource with unknown actuator timing, the way
    a Pi with no actuation log would. Off by default: a controller
    comparison must hold every condition identical, and this one changes
    the first hours of a run.
    """
    schedulers = (scheduler,) if isinstance(scheduler, Scheduler) else tuple(scheduler)
    primary = min(schedulers, key=lambda s: s.tier)

    simulator = scenario.build(config, resource_id=resource_id, cold_start=cold_start)
    resource = SimulatedTankResource(simulator)
    registry = ResourceRegistry(resources=(resource,))
    cycle = build_control_cycle(config, registry, schedulers)

    versions = VersionStamp(
        scheduler_version=primary.algorithm_version, config_hash=config_hash
    )
    state_manager = StateManager(mode=RunMode.SIMULATED, versions=versions)
    state_manager.begin_run(run_id=None, at=scenario.start)

    step_minutes = config.scheduler.step_minutes
    horizon_steps = max(1, int(config.scheduler.horizon_minutes // step_minutes))

    steps: list[SimulationStep] = []
    decisions: list[CycleDecision] = []

    for _ in range(scenario.n_steps(step_minutes)):
        now = simulator.clock
        observation = resource.observe()
        state_manager.record_observation(observation)
        state_manager.record_actuation_history(simulator.actuation_history())
        state_manager.record_electrical(_electrical_at(simulator, now))

        request = SchedulingRequest(
            now=now,
            horizon=Horizon(
                start=now,
                length_minutes=horizon_steps * step_minutes,
                step_minutes=step_minutes,
            ),
            resource=resource,
            observation=observation,
            actuation_history=simulator.actuation_history(),
            demand_forecast=_perfect_demand_forecast(
                simulator, now, horizon_steps, step_minutes
            ),
            solar_surplus_kw=_surplus_kw(simulator, now),
            provenance=Provenance.SIMULATED,
        )

        decision = cycle.decide(state_manager.snapshot(), request)
        state_manager.record_desired_action(resource_id, decision.action, now)

        step = simulator.advance(decision.action, step_minutes)
        state_manager.record_simulated_actuation(
            resource_id, simulator.actuator_on, now
        )

        steps.append(step)
        decisions.append(decision)

    return ScenarioRun(
        scenario_name=scenario.name,
        scenario_version=scenario.scenario_version,
        scheduler_name=primary.name,
        algorithm_version=primary.algorithm_version,
        config_hash=config_hash,
        step_minutes=step_minutes,
        steps=tuple(steps),
        decisions=tuple(decisions),
    )


def run_standard_set(
    config: Config,
    scenarios: tuple[Scenario, ...],
    scheduler_factory,
    config_hash: str | None = None,
) -> tuple[ScenarioRun, ...]:
    """Run one controller across several scenarios.

    ``scheduler_factory`` is a zero-argument callable rather than a
    scheduler, so each scenario gets a fresh instance. A scheduler is not
    supposed to carry state between runs, and building a new one is how
    that stops being something to remember.
    """
    return tuple(
        run_scenario(config, scenario, scheduler_factory(), config_hash=config_hash)
        for scenario in scenarios
    )


# --- simulation ground truth, used only by the driver ---------------------


def _perfect_demand_forecast(
    simulator, start: datetime, n_steps: int, step_minutes: float
) -> Forecast:
    return simulator.demand_profile.as_forecast(
        start=start,
        n_steps=n_steps,
        step_minutes=step_minutes,
        model_name="simulator_ground_truth",
    )


def _surplus_kw(simulator, now: datetime) -> float:
    pv_kw = simulator.solar_profile.kw_at(now)
    base_kw = simulator.base_load_profile.kw_at(now)
    return max(0.0, pv_kw - base_kw)


def _electrical_at(simulator, now: datetime) -> ElectricalState:
    pv_kw = simulator.solar_profile.kw_at(now)
    base_kw = simulator.base_load_profile.kw_at(now)
    pump_kw = simulator.pump.rated_power_kw if simulator.actuator_on else 0.0
    return ElectricalState(
        timestamp=now,
        pv_generation_kw=pv_kw,
        base_load_kw=base_kw,
        grid_import_kw=max(0.0, base_kw + pump_kw - pv_kw),
        provenance=Provenance.SIMULATED,
    )
