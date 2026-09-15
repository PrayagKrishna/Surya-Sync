"""Deterministic tank simulation and the ``SimulatedTankResource`` adapter.

Two classes with distinct jobs:

``TankSimulator``
    The world. Owns the clock, the stored volume and the actuator state,
    and advances them under whatever action it is given. It is the only
    mutable thing here.

``SimulatedTankResource``
    The ``FlexibleResource`` the scheduler talks to. A thin adapter: it
    holds no scheduling state and no physics of its own, delegating every
    physical question to ``models/``.

That delegation is the point, and it is structural rather than a
convention. ``predict_trajectory`` calls ``models.tank.predict_tank_trajectory``
and ``estimate_flexibility`` calls ``models.flexibility``; the real
resource in Phase 11 will call the same two functions. Nothing physical
lives in this module that ``RealTankResource`` would have to import from
``simulator/`` — which is what would eventually get copied instead, and
how "never fork the algorithm" stops being true.

``TankSimulator.advance`` and ``predict_tank_trajectory`` both reach
``TankModel.step``, so the physics the optimizer searches over is the
physics it is judged against. A test asserts they agree exactly.

**The simulator executes what it is told.** It does not re-check
admissibility or safety. Those gates live in ``safety/`` and run before
the scheduler; a simulator that silently corrected illegal commands would
make it impossible to demonstrate a scheduler behaving badly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from surya_sync.config.schema import Config
from surya_sync.domain import ControlAction, Forecast, Provenance, ResourceType
from surya_sync.models.generic_resource import (
    ActuationHistory,
    FlexibilityEstimate,
    FlexibleResource,
    PredictedState,
    ResourceConstraints,
    ResourceObservation,
)
from surya_sync.models.flexibility import estimate_tank_flexibility
from surya_sync.models.pump import PumpModel
from surya_sync.models.tank import (
    TankModel,
    predict_tank_trajectory,
    tank_constraints,
    violates_hard_constraint,
)
from surya_sync.simulator.demand import DemandProfile
from surya_sync.simulator.grid import (
    BaseLoadProfile,
    ConstantBaseLoadProfile,
    EnergySplit,
    GridModel,
)
from surya_sync.simulator.solar import SolarProfile


@dataclass(frozen=True, slots=True)
class SimulationStep:
    """One interval of a simulation run.

    ``start`` and ``minutes`` delimit the interval. Volumes are given at
    both ends explicitly so there is never a question of which instant a
    number refers to; every other quantity is an average or total over the
    interval.
    """

    start: datetime
    minutes: float
    action: ControlAction
    actuator_on: bool
    """Whether the pump was energized *during* this interval."""

    volume_start_l: float
    volume_end_l: float
    service_level_start: float
    service_level_end: float

    demand_lpm: float
    inflow_lpm: float
    drawn_l: float
    spilled_l: float
    unmet_demand_l: float

    pv_kw: float
    base_load_kw: float
    energy: EnergySplit

    violates_hard_constraint: bool
    provenance: Provenance = Provenance.SIMULATED

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)

    @property
    def pump_energy_kwh(self) -> float:
        return self.energy.controllable_kw * self.minutes / 60.0


@dataclass(slots=True)
class TankSimulator:
    """Mutable tank world, advanced one interval at a time.

    Determinism: every input is either a pure function of time (the
    profiles) or explicit state carried on this object. Replaying the same
    action sequence from the same construction arguments reproduces the
    trajectory exactly.
    """

    tank: TankModel
    pump: PumpModel
    constraints: ResourceConstraints
    demand_profile: DemandProfile
    solar_profile: SolarProfile
    clock: datetime
    volume_l: float

    base_load_profile: BaseLoadProfile = None  # type: ignore[assignment]
    grid: GridModel = None  # type: ignore[assignment]
    resource_id: str = "tank_1"

    actuator_on: bool = False
    cold_start: bool = False
    """Leave the actuator's timing unknown, as on a Pi that has booted with
    no actuation log. Every time-based equipment constraint then reads as
    unsatisfied, so the pump is held.

    **This state does not resolve on its own.** ``changed_at`` is written by
    a transition, and the unknown forbids the only transition that would
    write it, so a cold-started resource stays held indefinitely. Whatever
    owns the history — ``state/``, in Phase 11 — must establish a timestamp
    from the log or from boot time. The simulator does not have that
    problem by default, because a simulated world provably begins at
    ``clock``; this flag exists to exercise the path deliberately."""

    changed_at: datetime | None = None
    starts_today: int = 0
    last_start_at: datetime | None = None
    _start_count_date: date | None = None

    def __post_init__(self) -> None:
        if self.base_load_profile is None:
            self.base_load_profile = ConstantBaseLoadProfile(kw=0.0)
        if self.grid is None:
            self.grid = GridModel()
        if not 0.0 <= self.volume_l <= self.tank.capacity_l:
            raise ValueError(
                f"volume_l must be within [0, {self.tank.capacity_l}], got {self.volume_l}"
            )
        if self.changed_at is None and not self.cold_start:
            self.changed_at = self.clock
        if self._start_count_date is None:
            self._start_count_date = self.clock.date()

    # --- construction ---------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: Config,
        demand_profile: DemandProfile,
        solar_profile: SolarProfile,
        start: datetime,
        initial_service_level: float,
        base_load_profile: BaseLoadProfile | None = None,
        resource_id: str = "tank_1",
        cold_start: bool = False,
    ) -> TankSimulator:
        tank = TankModel.from_config(config.tank)
        return cls(
            tank=tank,
            pump=PumpModel.from_config(config.pump),
            constraints=tank_constraints(config),
            demand_profile=demand_profile,
            solar_profile=solar_profile,
            clock=start,
            volume_l=tank.volume_l_from_service_level(initial_service_level),
            base_load_profile=base_load_profile or ConstantBaseLoadProfile(kw=0.0),
            grid=GridModel.from_config(config.solar),
            resource_id=resource_id,
            cold_start=cold_start,
        )

    # --- state ----------------------------------------------------------

    @property
    def service_level(self) -> float:
        return self.tank.service_level_from_volume_l(self.volume_l)

    def observe(self) -> ResourceObservation:
        return ResourceObservation(
            timestamp=self.clock,
            resource_id=self.resource_id,
            service_level=self.service_level,
            native_value=self.volume_l,
            native_unit="l",
            actuator_on=self.actuator_on,
            provenance=Provenance.SIMULATED,
            sensor_valid=True,
        )

    def actuation_history(self) -> ActuationHistory:
        return ActuationHistory(
            resource_id=self.resource_id,
            actuator_on=self.actuator_on,
            changed_at=self.changed_at,
            starts_today=self.starts_today,
            last_start_at=self.last_start_at,
            provenance=Provenance.DERIVED,
        )

    # --- advancing ------------------------------------------------------

    def advance(self, action: ControlAction, minutes: float) -> SimulationStep:
        """Apply ``action`` for ``minutes`` and move the clock forward."""
        if minutes <= 0.0:
            raise ValueError("minutes must be > 0")

        self._roll_start_count(self.clock)
        self._apply(action)

        demand_lpm = self.demand_profile.lpm_at(self.clock)
        inflow_lpm = self.pump.inflow_lpm(self.actuator_on)
        outcome = self.tank.step(self.volume_l, inflow_lpm, demand_lpm, minutes)

        pv_kw = self.solar_profile.kw_at(self.clock)
        base_load_kw = self.base_load_profile.kw_at(self.clock)
        pump_kw = self.pump.rated_power_kw if self.actuator_on else 0.0
        energy = self.grid.split(pv_kw, base_load_kw, pump_kw, minutes)

        level_start = self.service_level
        level_end = self.tank.service_level_from_volume_l(outcome.volume_l)

        step = SimulationStep(
            start=self.clock,
            minutes=minutes,
            action=action,
            actuator_on=self.actuator_on,
            volume_start_l=self.volume_l,
            volume_end_l=outcome.volume_l,
            service_level_start=level_start,
            service_level_end=level_end,
            demand_lpm=demand_lpm,
            inflow_lpm=inflow_lpm,
            drawn_l=outcome.drawn_l,
            spilled_l=outcome.spilled_l,
            unmet_demand_l=outcome.unmet_demand_l,
            pv_kw=pv_kw,
            base_load_kw=base_load_kw,
            energy=energy,
            violates_hard_constraint=violates_hard_constraint(
                level_end, self.constraints, outcome.spilled_l
            ),
        )

        self.volume_l = outcome.volume_l
        self.clock = step.end
        return step

    def run(
        self, actions: Iterable[ControlAction], minutes: float
    ) -> tuple[SimulationStep, ...]:
        """Advance once per action. The multi-day trajectory entry point."""
        return tuple(self.advance(action, minutes) for action in actions)

    def _apply(self, action: ControlAction) -> None:
        desired_on = action is ControlAction.RUN
        if desired_on == self.actuator_on:
            return
        self.actuator_on = desired_on
        self.changed_at = self.clock
        if desired_on:
            self.starts_today += 1
            self.last_start_at = self.clock

    def _roll_start_count(self, now: datetime) -> None:
        if self._start_count_date != now.date():
            self._start_count_date = now.date()
            self.starts_today = 0


class SimulatedTankResource(FlexibleResource):
    """``FlexibleResource`` over a ``TankSimulator``.

    Holds no scheduling state of its own — everything it reports comes from
    the simulator or from the stateless physical models.
    """

    resource_type = ResourceType.WATER_TANK

    def __init__(self, simulator: TankSimulator, model_version: str = "sim-tank-1.0.0") -> None:
        self._simulator = simulator
        self.resource_id = simulator.resource_id
        self.model_version = model_version

    @property
    def simulator(self) -> TankSimulator:
        """The world behind this resource. For experiment drivers only —
        a scheduler reading it would be re-observing mid-cycle, which its
        own contract forbids."""
        return self._simulator

    def constraints(self) -> ResourceConstraints:
        return self._simulator.constraints

    def observe(self) -> ResourceObservation:
        return self._simulator.observe()

    def rated_power_kw(self) -> float:
        return self._simulator.pump.rated_power_kw

    def admissible_actions(
        self,
        observation: ResourceObservation,
        history: ActuationHistory | None,
        now: datetime,
    ) -> tuple[ControlAction, ...]:
        return self._simulator.pump.admissible_actions(
            observation.actuator_on, history, now
        )

    def predict_trajectory(
        self,
        observation: ResourceObservation,
        actions: tuple[ControlAction, ...],
        demand_forecast: Forecast,
        step_minutes: float,
    ) -> tuple[PredictedState, ...]:
        """Delegate to the shared tank physics. See
        ``models.tank.predict_tank_trajectory`` for the contract."""
        return predict_tank_trajectory(
            tank=self._simulator.tank,
            pump=self._simulator.pump,
            constraints=self._simulator.constraints,
            observation=observation,
            actions=actions,
            demand_forecast=demand_forecast,
            step_minutes=step_minutes,
        )

    def estimate_flexibility(
        self,
        observation: ResourceObservation,
        demand_forecast: Forecast,
    ) -> FlexibilityEstimate:
        """Delegate to the shared flexibility model. See
        ``models.flexibility.estimate_tank_flexibility`` for the contract."""
        return estimate_tank_flexibility(
            tank=self._simulator.tank,
            pump=self._simulator.pump,
            constraints=self._simulator.constraints,
            observation=observation,
            demand_forecast=demand_forecast,
            resource_id=self.resource_id,
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"SimulatedTankResource(resource_id={self.resource_id!r}, "
            f"model_version={self.model_version!r})"
        )
