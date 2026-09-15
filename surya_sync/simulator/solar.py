"""Synthetic PV generation profiles.

Clear-sky output is computed from actual solar geometry — declination and
solar altitude for the configured latitude — rather than a fixed bell
curve. That costs a dozen lines of trigonometry and buys two things the
project needs: day length varies with the season, so "wait for the sun"
means something different in December than in June; and ``latitude`` /
``longitude`` in the config become live parameters instead of decoration.

What this deliberately is **not**: an irradiance forecaster. It has no
atmospheric model, no diffuse component, no panel temperature derating.
Cloud is a multiplier, not physics. Phase 6 builds forecasting against
real measured generation; this module only has to produce a plausible,
reproducible sun for the scheduler to chase.

Like the demand profiles, every profile is a pure function of time.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

from surya_sync.config.schema import SolarConfig
from surya_sync.domain import Forecast, ForecastPoint, Provenance
from surya_sync.simulator.noise import unit_noise


class SolarProfile(ABC):
    """A PV generation series expressed as a pure function of time."""

    profile_version: str

    @abstractmethod
    def kw_at(self, when: datetime) -> float:
        """AC power delivered by the array at ``when``, in kW."""

    def energy_kwh(self, start: datetime, minutes: float, step_minutes: float = 1.0) -> float:
        if minutes < 0.0 or step_minutes <= 0.0:
            raise ValueError("minutes must be >= 0 and step_minutes > 0")
        total = 0.0
        steps = int(round(minutes / step_minutes))
        for index in range(steps):
            moment = start + timedelta(minutes=index * step_minutes)
            total += self.kw_at(moment) * step_minutes / 60.0
        return total

    def as_forecast(
        self,
        start: datetime,
        n_steps: int,
        step_minutes: float,
        model_name: str = "simulator_ground_truth",
    ) -> Forecast:
        """The simulator's own ground truth, in forecast shape.

        A perfect forecast. Results obtained with it are an upper bound,
        and must be reported as such rather than as forecast skill.
        """
        points = tuple(
            ForecastPoint(
                target_time=start + timedelta(minutes=index * step_minutes),
                p50=self.kw_at(start + timedelta(minutes=index * step_minutes)),
                unit="kw",
            )
            for index in range(n_steps)
        )
        return Forecast(
            target="pv_generation_kw",
            issued_at=start,
            points=points,
            model_name=model_name,
            model_version=self.profile_version,
            provenance=Provenance.SIMULATED,
        )


@dataclass(frozen=True, slots=True)
class ClearSkyProfile(SolarProfile):
    """Cloudless generation for a fixed, horizontal-equivalent array."""

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
    def from_config(cls, config: SolarConfig, utc_offset_hours: float = 5.5) -> ClearSkyProfile:
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


@dataclass(frozen=True, slots=True)
class OvercastProfile(SolarProfile):
    """A uniformly dull day: clear sky scaled by a constant."""

    clear_sky: ClearSkyProfile
    cloud_factor: float = 0.30
    """Fraction of clear-sky output that still gets through."""

    profile_version: str = "solar-overcast-1.0.0"

    def __post_init__(self) -> None:
        if not 0.0 <= self.cloud_factor <= 1.0:
            raise ValueError("cloud_factor must be in [0, 1]")

    def kw_at(self, when: datetime) -> float:
        return self.clear_sky.kw_at(when) * self.cloud_factor


@dataclass(frozen=True, slots=True)
class IntermittentProfile(SolarProfile):
    """Passing clouds: clear sky scaled by a value that changes per slot.

    This is the case that punishes a purely reactive scheduler — surplus
    appears and vanishes on a timescale shorter than a useful pump run, so
    chasing it instant by instant short-cycles the pump while a forecast-
    aware scheduler waits for a window that holds.
    """

    clear_sky: ClearSkyProfile
    seed: int = 20260915
    slot_minutes: float = 20.0
    min_factor: float = 0.15
    max_factor: float = 1.0
    profile_version: str = "solar-intermittent-1.0.0"

    def __post_init__(self) -> None:
        if self.slot_minutes <= 0.0:
            raise ValueError("slot_minutes must be > 0")
        if not 0.0 <= self.min_factor <= self.max_factor <= 1.0:
            raise ValueError("require 0 <= min_factor <= max_factor <= 1")

    def cloud_factor_at(self, when: datetime) -> float:
        slot = int(when.timestamp() // (self.slot_minutes * 60))
        span = self.max_factor - self.min_factor
        return self.min_factor + span * unit_noise(self.seed, slot)

    def kw_at(self, when: datetime) -> float:
        return self.clear_sky.kw_at(when) * self.cloud_factor_at(when)
