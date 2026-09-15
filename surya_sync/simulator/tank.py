"""Deterministic tank simulation and the ``SimulatedTankResource`` adapter.

Two classes with distinct jobs:

``TankSimulator``
    The world. Owns the clock, the stored volume and the actuator state,
    and advances them under whatever action it is given. It is the only
    mutable thing here.

``SimulatedTankResource``
    The ``FlexibleResource`` the scheduler talks to. It holds no scheduling
    state; it answers questions about physics by delegating to the same
    ``TankModel`` and ``PumpModel`` the simulator uses.

That delegation is the point. ``predict_trajectory`` and
``TankSimulator.advance`` both call ``TankModel.step``, so the physics the
optimizer searches over is byte-for-byte the physics it will be judged
against. The real resource in Phase 11 replaces the simulator underneath
this same adapter and the scheduling code path does not change.

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
from surya_sync.models.pump import PumpModel
from surya_sync.models.tank import TankModel
from surya_sync.simulator.demand import DemandProfile
from surya_sync.simulator.grid import (
    BaseLoadProfile,
    ConstantBaseLoadProfile,
    EnergySplit,
    GridModel,
)
from surya_sync.simulator.solar import SolarProfile

DEFAULT_STEP_MINUTES = 15.0
"""Used only where a step cannot be inferred from a forecast's spacing."""

WATER_DEMAND_TARGET = "water_demand_lpm"
"""The only forecast target the tank's physics can consume.

``Forecast`` is a general container, so a PV forecast is structurally
indistinguishable from a demand one. Handed the wrong series the tank would
read kW as litres per minute and produce a trajectory that looks entirely
plausible — see ``require_demand_forecast``.
"""


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
        """Roll the tank forward under a candidate action sequence.

        Uses the ``p50`` demand band: this answers *what is expected to
        happen*. Conservative planning is the optimizer's job in Phase 10,
        expressed by feeding this a ``p90`` forecast, not by biasing the
        physics here.

        The sequence is simulated as given. Inadmissible actions are not
        filtered out — ``admissible_actions`` is the gate, and silently
        rewriting a proposed plan would hide an optimizer bug.
        """
        if step_minutes <= 0.0:
            raise ValueError("step_minutes must be > 0")
        require_demand_forecast(demand_forecast)

        tank = self._simulator.tank
        pump = self._simulator.pump
        constraints = self._simulator.constraints

        states: list[PredictedState] = []
        volume = observation.native_value
        moment = observation.timestamp

        for action in actions:
            actuator_on = action is ControlAction.RUN
            demand_lpm = demand_at(demand_forecast, moment)
            outcome = tank.step(
                volume, pump.inflow_lpm(actuator_on), demand_lpm, step_minutes
            )
            volume = outcome.volume_l
            moment = moment + timedelta(minutes=step_minutes)
            level = tank.service_level_from_volume_l(volume)
            states.append(
                PredictedState(
                    timestamp=moment,
                    service_level=level,
                    native_value=volume,
                    actuator_on=actuator_on,
                    violates_hard_constraint=violates_hard_constraint(
                        level, constraints, outcome.spilled_l
                    ),
                    provenance=Provenance.PREDICTED,
                )
            )

        return tuple(states)

    def estimate_flexibility(
        self,
        observation: ResourceObservation,
        demand_forecast: Forecast,
    ) -> FlexibilityEstimate:
        """How long the pump can stay off before a floor is breached.

        Conservative by construction: uses ``p90`` demand where the forecast
        carries a band, so flexibility is understated rather than
        overstated. Until Phase 10 populates bands this falls back to
        ``p50``, and the estimate is only as cautious as the forecast is.

        ``must_run_by`` is found by asking, for each step in turn, whether a
        run starting there would hold the level above ``critical`` for the
        rest of the horizon. The last step that succeeds is the answer. That
        scan — rather than simply reporting when the level hits the floor —
        is what makes the number correct when demand outpaces the pump.
        """
        require_demand_forecast(demand_forecast)

        tank = self._simulator.tank
        pump = self._simulator.pump
        constraints = self._simulator.constraints
        step_minutes = forecast_step_minutes(demand_forecast)
        n_steps = max(1, len(demand_forecast.points))

        demands = [
            conservative_demand_at(
                demand_forecast,
                observation.timestamp + timedelta(minutes=index * step_minutes),
            )
            for index in range(n_steps)
        ]

        flexibility_minutes = _minutes_until_below(
            tank, observation.native_value, demands, step_minutes,
            constraints.service_level_min, pump_lpm=0.0,
        )
        time_to_critical_minutes = _minutes_until_below(
            tank, observation.native_value, demands, step_minutes,
            constraints.service_level_critical, pump_lpm=0.0,
        )

        horizon_minutes = n_steps * step_minutes
        if flexibility_minutes is None:
            flexibility_minutes = horizon_minutes
        if time_to_critical_minutes is None:
            time_to_critical_minutes = horizon_minutes

        return FlexibilityEstimate(
            timestamp=observation.timestamp,
            resource_id=self.resource_id,
            flexibility_minutes=flexibility_minutes,
            time_to_critical_minutes=max(flexibility_minutes, time_to_critical_minutes),
            must_run_by=_latest_safe_start(
                tank, pump, observation, demands, step_minutes, constraints
            ),
            provenance=Provenance.ESTIMATED,
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"SimulatedTankResource(resource_id={self.resource_id!r}, "
            f"model_version={self.model_version!r})"
        )


# --- shared helpers -----------------------------------------------------


def tank_constraints(config: Config) -> ResourceConstraints:
    """Build the resource's constraint set from validated config.

    One place, so the simulator, the scheduler and the safety layer cannot
    disagree about where the floors are.
    """
    return ResourceConstraints(
        service_level_critical=config.tank.critical_level,
        service_level_min=config.tank.min_level,
        service_level_max=config.tank.max_level,
        min_on_minutes=config.pump.min_on_minutes,
        min_off_minutes=config.pump.min_off_minutes,
        max_starts_per_day=config.pump.max_starts_per_day,
    )


def violates_hard_constraint(
    service_level: float, constraints: ResourceConstraints, spilled_l: float = 0.0
) -> bool:
    """Whether a state is outside the hard band.

    ``service_level_min`` is a *planning* floor and is not checked here —
    dipping below it is a plan going wrong, not a constraint violation.
    Overflow counts even though the clamped level reads exactly at the
    ceiling, because the spill is the evidence.
    """
    return (
        service_level < constraints.service_level_critical
        or service_level > constraints.service_level_max
        or spilled_l > 0.0
    )


def require_demand_forecast(forecast: Forecast) -> None:
    """Reject a forecast that is not water demand.

    The units are the whole point: a PV forecast carries kW, and nothing in
    the type system stops it being passed where litres per minute are
    expected. The resulting trajectory is wrong but entirely plausible —
    no negative volumes, no exceptions, just a tank draining at the wrong
    rate — which is the hardest kind of error to notice.
    """
    if forecast.target != WATER_DEMAND_TARGET:
        raise ValueError(
            f"expected a {WATER_DEMAND_TARGET!r} forecast, got {forecast.target!r}; "
            "the tank reads these values as litres per minute"
        )


def demand_at(forecast: Forecast, moment: datetime) -> float:
    """The forecast's expected (``p50``) value covering ``moment``.

    Zero-order hold: each point governs until the next one starts, and the
    first point governs anything before it.
    """
    return _lookup(forecast, moment, conservative=False)


def conservative_demand_at(forecast: Forecast, moment: datetime) -> float:
    """The ``p90`` value covering ``moment``, falling back to ``p50``.

    Plan for a thirstier household than expected.
    """
    return _lookup(forecast, moment, conservative=True)


def _lookup(forecast: Forecast, moment: datetime, conservative: bool) -> float:
    """Pick the governing point without assuming the series is sorted.

    A ``Forecast`` does not promise chronological points, and one rebuilt
    from the database or assembled by hand need not be. Scanning for the
    latest point at or before ``moment`` — rather than walking until the
    first point in the future — costs nothing at horizon lengths and
    removes an assumption that would otherwise fail silently, returning a
    demand from the wrong time.
    """
    if not forecast.points:
        raise ValueError(
            f"forecast {forecast.target!r} has no points — refusing to assume "
            "zero demand, which would make every trajectory look safe"
        )

    earliest = forecast.points[0]
    chosen: ForecastPoint | None = None
    for point in forecast.points:
        if point.target_time < earliest.target_time:
            earliest = point
        if point.target_time <= moment and (
            chosen is None or point.target_time > chosen.target_time
        ):
            chosen = point

    if chosen is None:
        chosen = earliest
    if conservative and chosen.p90 is not None:
        return chosen.p90
    return chosen.p50


def forecast_step_minutes(forecast: Forecast) -> float:
    """Spacing between forecast points, or the default if it cannot be told."""
    if len(forecast.points) < 2:
        return DEFAULT_STEP_MINUTES
    delta = forecast.points[1].target_time - forecast.points[0].target_time
    minutes = delta.total_seconds() / 60.0
    return minutes if minutes > 0.0 else DEFAULT_STEP_MINUTES


def _minutes_until_below(
    tank: TankModel,
    volume_l: float,
    demands: list[float],
    step_minutes: float,
    floor_level: float,
    pump_lpm: float,
) -> float | None:
    """Minutes until the level first drops below ``floor_level``.

    ``None`` when it never does within the supplied demand series. The
    caller decides what to report for that — the honest answer is "at least
    the horizon", not "infinite".
    """
    volume = volume_l
    for index, demand_lpm in enumerate(demands):
        outcome = tank.step(volume, pump_lpm, demand_lpm, step_minutes)
        volume = outcome.volume_l
        if tank.service_level_from_volume_l(volume) < floor_level:
            return (index + 1) * step_minutes
    return None


def _latest_safe_start(
    tank: TankModel,
    pump: PumpModel,
    observation: ResourceObservation,
    demands: list[float],
    step_minutes: float,
    constraints: ResourceConstraints,
) -> datetime | None:
    """Last step at which starting the pump still avoids the hard floor.

    Three outcomes, and they must stay distinguishable:

    ``None``
        Unconstrained. Idling through the whole horizon never approaches
        ``critical``, so there is no deadline to report.
    a time in the future
        The genuine deadline.
    ``observation.timestamp``
        Already too late — not even starting immediately holds the level
        above ``critical``. Reporting ``None`` here would read as "no
        deadline" and invite a scheduler to keep deferring at exactly the
        moment it should be running flat out, so the doomed case is
        reported as a deadline of *now* instead.
    """
    unconstrained = (
        _minutes_until_below(
            tank,
            observation.native_value,
            demands,
            step_minutes,
            constraints.service_level_critical,
            pump_lpm=0.0,
        )
        is None
    )
    if unconstrained:
        return None

    latest: datetime | None = None
    for start_index in range(len(demands)):
        volume = observation.native_value
        safe = True
        for index, demand_lpm in enumerate(demands):
            inflow = pump.flow_rate_lpm if index >= start_index else 0.0
            volume = tank.step(volume, inflow, demand_lpm, step_minutes).volume_l
            if tank.service_level_from_volume_l(volume) < constraints.service_level_critical:
                safe = False
                break
        if safe:
            latest = observation.timestamp + timedelta(minutes=start_index * step_minutes)

    return latest if latest is not None else observation.timestamp
