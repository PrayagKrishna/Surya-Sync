"""Clear-sky PV output from solar geometry: the physics half of ``simulator/solar.py``.

Declination and solar altitude for the configured latitude, with no
atmosphere, no diffuse component and no panel temperature derating. Clouds
are not modelled here: the simulator layers them on top, and the solar
forecaster divides them out (the clear-sky index).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from surya_sync.config.schema import SolarConfig


@dataclass(frozen=True, slots=True)
class ClearSkyModel:
    """Cloudless generation for a fixed, horizontal-equivalent array.

    Shared physics, so it lives here and not in ``simulator/``: the solar
    forecaster (``ml/solar``) needs the same clear-sky curve the simulator
    uses, and a production layer may not import the simulator. A copy would
    let the two drift apart without anyone deciding they should.
    """

    pv_capacity_kw: float
    latitude: float = 12.97
    longitude: float = 77.59
    utc_offset_hours: float = 5.5
    """Offset of the timestamps handed to ``kw_at``. Used only to place
    solar noon correctly relative to clock noon."""

    performance_ratio: float = 0.80
    """Everything between nameplate DC and delivered AC: inverter losses,
    soiling, temperature, wiring. One lumped constant on purpose."""

    profile_version: str = "solar-clearsky-1.0.0"

    def __post_init__(self) -> None:
        if self.pv_capacity_kw <= 0.0:
            raise ValueError("pv_capacity_kw must be > 0")
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError("latitude out of range")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError("longitude out of range")
        if not 0.0 < self.performance_ratio <= 1.0:
            raise ValueError("performance_ratio must be in (0, 1]")

    @classmethod
    def from_config(cls, config: SolarConfig, utc_offset_hours: float = 5.5) -> ClearSkyModel:
        return cls(
            pv_capacity_kw=config.pv_capacity_kw,
            latitude=config.latitude,
            longitude=config.longitude,
            utc_offset_hours=utc_offset_hours,
        )

    def declination_deg(self, when: datetime) -> float:
        """Cooper's equation. Accurate to well under a degree."""
        day_of_year = when.timetuple().tm_yday
        return 23.45 * math.sin(math.radians(360.0 / 365.0 * (284 + day_of_year)))

    def solar_altitude_deg(self, when: datetime) -> float:
        """Angle of the sun above the horizon. Negative before sunrise."""
        latitude = math.radians(self.latitude)
        declination = math.radians(self.declination_deg(when))

        standard_meridian = 15.0 * self.utc_offset_hours
        longitude_correction_hours = (self.longitude - standard_meridian) / 15.0
        clock_hours = when.hour + when.minute / 60.0 + when.second / 3600.0
        solar_hours = clock_hours + longitude_correction_hours
        hour_angle = math.radians(15.0 * (solar_hours - 12.0))

        sin_altitude = math.sin(latitude) * math.sin(declination) + math.cos(
            latitude
        ) * math.cos(declination) * math.cos(hour_angle)
        return math.degrees(math.asin(max(-1.0, min(1.0, sin_altitude))))

    def clear_sky_fraction(self, when: datetime) -> float:
        """Output as a fraction of nameplate, before the performance ratio.

        Proportional to the sine of solar altitude, which is the geometric
        part of beam irradiance on a horizontal surface.
        """
        altitude = self.solar_altitude_deg(when)
        if altitude <= 0.0:
            return 0.0
        return math.sin(math.radians(altitude))

    def kw_at(self, when: datetime) -> float:
        return self.pv_capacity_kw * self.performance_ratio * self.clear_sky_fraction(when)
