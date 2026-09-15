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

from surya_sync.config.schema import TankConfig


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


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value
