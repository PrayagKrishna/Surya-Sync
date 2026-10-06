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
from surya_sync.models.solar import ClearSkyModel
from surya_sync.simulator.noise import unit_noise


class SolarProfile(ABC):
    """A PV generation series expressed as a pure function of time."""

    profile_version: str

    @abstractmethod
    def kw_at(self, when: datetime) -> float:
        """AC power delivered by the array at ``when``, in kW."""

    def energy_kwh(self, start: datetime, minutes: float, step_minutes: float = 1.0) -> float:
        """Integrate generation over a window, left-endpoint rule.

        Rejects a window the step does not divide evenly rather than
        truncating it, for the same reason the demand integral does:
        dropping the remainder quietly changes the answer.
        """
        if minutes < 0.0 or step_minutes <= 0.0:
            raise ValueError("minutes must be >= 0 and step_minutes > 0")
        steps = round(minutes / step_minutes)
        if abs(steps * step_minutes - minutes) > 1e-9:
            raise ValueError(
                f"step_minutes={step_minutes} does not divide minutes={minutes} evenly"
            )
        total = 0.0
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
class ClearSkyProfile(ClearSkyModel, SolarProfile):
    """The clear-sky physics (``models.solar``) as a simulator profile."""


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

    base: SolarProfile
    """The unclouded reference. Typically a ``ClearSkyProfile``, but any
    profile works — the cloudy scenario layers this over an
    ``OvercastProfile`` — so it is deliberately not named ``clear_sky``."""

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
        return self.base.kw_at(when) * self.cloud_factor_at(when)
