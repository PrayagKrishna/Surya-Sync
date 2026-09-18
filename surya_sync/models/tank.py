"""Overhead water tank physical model.

Two jobs, kept separate:

1. **Geometry** — convert between what the ultrasonic sensor reports
   (a distance in mm), what the household cares about (litres), and what
   the scheduler reasons about (a 0..1 ``service_level``).
2. **Mass balance** — advance the stored volume over one time step given
   pump inflow and household draw.

The model is a pure function library: it holds no state and no clock.
``simulator/tank.py`` owns the state and calls in here; so will the real
resource in Phase 11. Both therefore run identical physics, which is the
whole point of the simulated/real split.

Geometry assumption: a uniform vertical cross-section, so volume is linear
in water-column height. A cylindrical or cuboid overhead tank satisfies
this. A tapered tank would need a calibration curve here and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from surya_sync.config.schema import Config, TankConfig
from surya_sync.domain import (
    ControlAction,
    Forecast,
    Provenance,
    TimedValue,
    forecast_value_at,
)
from surya_sync.models.generic_resource import (
    PredictedState,
    ResourceConstraints,
    ResourceObservation,
)
from surya_sync.models.pump import PumpModel

WATER_DEMAND_TARGET = "water_demand_lpm"
"""The only forecast target the tank's physics can consume.

``Forecast`` is a general container, so a PV series is structurally
indistinguishable from a demand one. Handed the wrong series the tank would
read kW as litres per minute and produce a trajectory that looks entirely
plausible — see ``require_demand_forecast``.
"""

NATIVE_UNIT_LITRES = "l"
"""The only ``ResourceObservation.native_unit`` the tank's physics can
consume — same failure mode as ``WATER_DEMAND_TARGET``, one level closer to
the sensor: nothing in the type system stops a caller handing
``observed_demand_lpm`` two readings labelled ``"kW"``, and the arithmetic
would produce a plausible-looking number regardless. Compared
case-insensitively, since ``SimulatedTankResource`` writes ``"l"`` but
nothing enforces a single casing at the point of construction.
"""


@dataclass(frozen=True, slots=True)
class TankStep:
    """The outcome of advancing the tank by one interval.

    ``spilled_l`` and ``unmet_demand_l`` exist so that clamping the volume
    into its physical range cannot silently hide a failure. A trajectory
    that overflows or runs dry must be visible as such, not as a volume
    that quietly stopped changing.
    """

    volume_l: float
    inflow_l: float
    """Water the pump actually delivered into the tank, spill excluded."""

    drawn_l: float
    """Water the household actually received."""

    spilled_l: float
    unmet_demand_l: float

    @property
    def overflowed(self) -> bool:
        return self.spilled_l > 0.0

    @property
    def ran_dry(self) -> bool:
        return self.unmet_demand_l > 0.0


@dataclass(frozen=True, slots=True)
class TankModel:
    """Geometry and mass balance for one overhead tank."""

    capacity_l: float
    height_mm: float
    sensor_offset_mm: float
    """Distance from the sensor face down to the water surface when full."""

    model_version: str = "tank-1.0.0"

    def __post_init__(self) -> None:
        if self.capacity_l <= 0.0:
            raise ValueError("capacity_l must be > 0")
        if self.height_mm <= 0.0:
            raise ValueError("height_mm must be > 0")
        if self.sensor_offset_mm < 0.0:
            raise ValueError("sensor_offset_mm must be >= 0")

    @classmethod
    def from_config(cls, config: TankConfig) -> TankModel:
        return cls(
            capacity_l=config.capacity_l,
            height_mm=config.height_mm,
            sensor_offset_mm=config.sensor_offset_mm,
        )

    # --- geometry -------------------------------------------------------

    @property
    def litres_per_mm(self) -> float:
        """Cross-sectional area, expressed in the units that matter here."""
        return self.capacity_l / self.height_mm

    @property
    def distance_mm_full(self) -> float:
        return self.sensor_offset_mm

    @property
    def distance_mm_empty(self) -> float:
        return self.sensor_offset_mm + self.height_mm

    def water_column_mm_from_distance(self, distance_mm: float) -> float:
        """Sensor distance -> depth of water, clamped to the tank."""
        column = self.height_mm - (distance_mm - self.sensor_offset_mm)
        return _clamp(column, 0.0, self.height_mm)

    def distance_mm_from_water_column(self, column_mm: float) -> float:
        """Depth of water -> the distance a healthy sensor would report."""
        column = _clamp(column_mm, 0.0, self.height_mm)
        return self.sensor_offset_mm + (self.height_mm - column)

    def volume_l_from_water_column(self, column_mm: float) -> float:
        return _clamp(column_mm, 0.0, self.height_mm) * self.litres_per_mm

    def water_column_mm_from_volume(self, volume_l: float) -> float:
        return _clamp(volume_l, 0.0, self.capacity_l) / self.litres_per_mm

    def volume_l_from_distance_mm(self, distance_mm: float) -> float:
        """The full sensor path, which is how real telemetry arrives."""
        return self.volume_l_from_water_column(
            self.water_column_mm_from_distance(distance_mm)
        )

    def distance_mm_from_volume_l(self, volume_l: float) -> float:
        return self.distance_mm_from_water_column(
            self.water_column_mm_from_volume(volume_l)
        )

    def service_level_from_volume_l(self, volume_l: float) -> float:
        """Normalize to the 0..1 quantity ``scheduler/`` reasons about.

        Fraction of nominal capacity. The *usable* band is carved out of
        this by ``ResourceConstraints``, not by rescaling here — keeping
        the mapping linear means a level reads the same before and after
        someone edits a threshold.
        """
        return _clamp(volume_l / self.capacity_l, 0.0, 1.0)

    def volume_l_from_service_level(self, service_level: float) -> float:
        return _clamp(service_level, 0.0, 1.0) * self.capacity_l

    def is_plausible_distance_mm(self, distance_mm: float, tolerance_mm: float = 50.0) -> bool:
        """Whether a reading could have come from this tank at all.

        An ultrasonic sensor reports nonsense when it misses the surface or
        catches a side wall. ``tolerance_mm`` allows for mounting slop and
        surface ripple without admitting a wild reading.
        """
        return (
            self.distance_mm_full - tolerance_mm
            <= distance_mm
            <= self.distance_mm_empty + tolerance_mm
        )

    # --- mass balance ---------------------------------------------------

    def step(
        self,
        volume_l: float,
        inflow_lpm: float,
        demand_lpm: float,
        minutes: float,
    ) -> TankStep:
        """Advance the stored volume by ``minutes``.

        Inflow and draw are applied over the same interval and the result is
        clamped to ``[0, capacity_l]``. Demand is served before the ceiling
        is applied, so a tank that is filling and being drawn simultaneously
        behaves correctly.

        Deterministic by construction: no clock, no randomness, no state.
        """
        if minutes < 0.0:
            raise ValueError("minutes must be >= 0")
        if inflow_lpm < 0.0 or demand_lpm < 0.0:
            raise ValueError("inflow_lpm and demand_lpm must be >= 0")

        start = _clamp(volume_l, 0.0, self.capacity_l)
        requested_in = inflow_lpm * minutes
        requested_out = demand_lpm * minutes

        available = start + requested_in
        drawn = min(requested_out, available)
        unmet = requested_out - drawn

        raw = available - drawn
        spilled = max(0.0, raw - self.capacity_l)
        end = _clamp(raw, 0.0, self.capacity_l)

        return TankStep(
            volume_l=end,
            inflow_l=requested_in - spilled,
            drawn_l=drawn,
            spilled_l=spilled,
            unmet_demand_l=unmet,
        )


def tank_constraints(config: Config) -> ResourceConstraints:
    """Build the resource's constraint set from validated config.

    One place, so the simulator, the real resource, the scheduler and the
    safety layer cannot disagree about where the floors are.
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


def predict_tank_trajectory(
    tank: TankModel,
    pump: PumpModel,
    constraints: ResourceConstraints,
    observation: ResourceObservation,
    actions: tuple[ControlAction, ...],
    demand_forecast: Forecast,
    step_minutes: float,
) -> tuple[PredictedState, ...]:
    """Roll the tank forward under a candidate action sequence.

    A free function rather than a method so that the simulated and the real
    resource cannot end up with separate copies of it. Both adapters are
    three lines that call in here, which is what makes "never fork the
    algorithm" structural instead of a convention.

    Uses the ``p50`` demand band: this answers *what is expected to happen*.
    Conservative planning is the optimizer's job, expressed by feeding this
    a ``p90`` forecast, not by biasing the physics.

    The sequence is simulated as given. Inadmissible actions are not
    filtered out — ``admissible_actions`` is the gate, and silently
    rewriting a proposed plan would hide an optimizer bug.
    """
    if step_minutes <= 0.0:
        raise ValueError("step_minutes must be > 0")
    require_demand_forecast(demand_forecast)

    states: list[PredictedState] = []
    volume = observation.native_value
    moment = observation.timestamp

    for action in actions:
        actuator_on = action is ControlAction.RUN
        demand_lpm = forecast_value_at(demand_forecast, moment)
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


_BOUNDARY_TOLERANCE = 1e-9
"""``service_level`` is exactly 0.0 or 1.0 at a clamp, by construction of
``TankModel``'s own ``_clamp``. The tolerance only guards against a real
sensor reporting a level that rounds to the boundary without having
actually clamped."""


def observed_demand_lpm(
    previous: ResourceObservation,
    current: ResourceObservation,
    pump: PumpModel,
) -> float | None:
    """Recover the household draw between two consecutive readings.

    The inverse of ``TankModel.step``: given the volume change and the
    inflow the pump delivered, ``demand_lpm = inflow_lpm - (v1 - v0) / dt``.

    ``current.actuator_on`` governs the inflow, **not**
    ``previous.actuator_on``. This looks backwards next to
    ``predict_tank_trajectory`` (which holds one action across a step,
    read from the state *before* it), but the two functions face opposite
    directions: ``predict_tank_trajectory`` is told an action to apply
    going forward from a known state, while this function is handed two
    readings *after the fact* and has to infer what happened between them.
    A real control loop (and the simulator that mirrors it) observes,
    *then* decides, *then* acts — so ``resource.observe()`` reports the
    actuator's current state, which is whatever the *previous* decision
    left it as, not the one about to govern the next interval. That means
    ``current``, timestamped at the end of the interval being inverted, is
    the reading taken *after* the decision that governed this interval was
    applied, and its ``actuator_on`` is the one that ran during
    ``[previous.timestamp, current.timestamp)``. Using ``previous`` here
    silently reads the actuator state that governed the *preceding*
    interval instead, and was verified in Phase 5 to fabricate demand
    values far outside anything the demand profile can produce (up to
    +/-30 L/min against a true peak under 1 L/min) — see the Phase 5
    hardening note in ``CLAUDE.md``.

    Raises ``ValueError`` for a genuine caller mistake — mismatched
    resources, readings not in litres, or ``current`` at or before
    ``previous`` (which ``observed_demand_series`` never passes, since it
    checks order itself before calling in here; a direct caller handing
    this function a reversed pair has a bug, not an unidentifiable
    interval, and deserves a loud failure rather than a plausible-looking
    ``None``).

    Returns ``None`` — never a number — when the interval genuinely
    cannot identify demand:

    - either reading has ``sensor_valid=False``
    - the elapsed time is exactly zero (two readings at the same instant;
      arithmetically degenerate, not a caller mistake the way a *negative*
      interval is)
    - the tank ended the interval at capacity while the pump ran (a spill
      may have absorbed inflow the volume change cannot show, so the raw
      arithmetic would *overstate* demand by however much spilled)
    - the tank ended the interval empty (unmet demand may have absorbed
      draw the volume change cannot show, so the raw arithmetic would
      only be a lower bound on the true demand)

    ``TankModel.step`` clamps into ``[0, capacity_l]`` precisely so a
    trajectory cannot show volumes outside the tank; that clamp is what
    makes both boundary cases undecidable here rather than merely
    imprecise.
    """
    if previous.resource_id != current.resource_id:
        raise ValueError(
            f"observations are for different resources: "
            f"{previous.resource_id!r} vs {current.resource_id!r}"
        )
    for observation in (previous, current):
        if observation.native_unit.lower() != NATIVE_UNIT_LITRES:
            raise ValueError(
                f"expected native_unit {NATIVE_UNIT_LITRES!r}, got "
                f"{observation.native_unit!r}; this function reads "
                "native_value as litres"
            )
    if current.timestamp < previous.timestamp:
        raise ValueError(
            f"current ({current.timestamp}) is before previous "
            f"({previous.timestamp}) — observed_demand_lpm requires "
            "previous, current in chronological order"
        )
    if not previous.sensor_valid or not current.sensor_valid:
        return None

    dt_minutes = (current.timestamp - previous.timestamp).total_seconds() / 60.0
    if dt_minutes == 0.0:
        return None

    inflow_lpm = pump.inflow_lpm(current.actuator_on)
    if current.service_level >= 1.0 - _BOUNDARY_TOLERANCE and inflow_lpm > 0.0:
        return None
    if current.service_level <= _BOUNDARY_TOLERANCE:
        return None

    volume_change = current.native_value - previous.native_value
    return inflow_lpm - volume_change / dt_minutes


def observed_demand_series(
    observations: tuple[ResourceObservation, ...],
    pump: PumpModel,
) -> tuple[TimedValue, ...]:
    """Derive a chronological demand series from raw tank observations.

    Walks consecutive pairs and skips any interval ``observed_demand_lpm``
    cannot identify (overflow or run-dry) rather than inserting a guess —
    a gap in the derived series stays a gap, so a time-based lag lookup
    reports it honestly instead of silently reading the wrong instant.

    Each value is stamped at the interval's *start*, matching
    ``SimulationStep.demand_lpm`` (the realized draw at ``step.start``).
    ``observations`` must already be chronological; this does not sort.
    """
    values: list[TimedValue] = []
    for previous, current in zip(observations, observations[1:]):
        if previous.timestamp >= current.timestamp:
            raise ValueError(
                "observed_demand_series requires chronological input, got "
                f"{previous.timestamp} before {current.timestamp}"
            )
        demand_lpm = observed_demand_lpm(previous, current, pump)
        if demand_lpm is None:
            continue
        values.append(
            TimedValue(at=previous.timestamp, value=demand_lpm, provenance=Provenance.DERIVED)
        )

    return tuple(values)


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value
