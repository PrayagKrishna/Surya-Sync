"""Synthetic household water-demand profiles.

Demand is the driver the whole system exists to stay ahead of, so the
profiles need three properties:

- **Pure in time.** ``lpm_at(t)`` depends only on ``t``. The MPC queries the
  same future instant repeatedly across replans and must get the same
  answer every time, or its plans are not comparable and replay is broken.
- **Shaped like a household.** Two peaks, quiet nights, heavier weekends.
  A flat draw would make the scheduling problem trivial and the results
  meaningless.
- **Exactly budgeted.** With jitter off, a day integrates to exactly
  ``daily_volume_l``. Scenarios are then stated in litres per day, which is
  a number a person can sanity-check.

Phase 4 replaces these as the *learning target* with real logged history.
Until then they are the ground truth the forecasters will be scored
against, and every value they produce is ``Provenance.SIMULATED``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from surya_sync.domain import Forecast, ForecastPoint, Provenance
from surya_sync.simulator.noise import signed_noise

WEEKDAY_HOURLY_WEIGHTS: tuple[float, ...] = (
    0.05, 0.03, 0.02, 0.02, 0.03, 0.15,   # 00:00 - 05:59
    0.70, 1.00, 0.95, 0.60, 0.40, 0.35,   # 06:00 - 11:59  morning peak
    0.40, 0.35, 0.25, 0.25, 0.30, 0.45,   # 12:00 - 17:59
    0.80, 0.95, 0.85, 0.55, 0.30, 0.12,   # 18:00 - 23:59  evening peak
)
"""Relative draw for each hour, anchored at the hour's midpoint.

Absolute scale is irrelevant — the profile renormalizes to
``daily_volume_l`` — so only the *shape* is a modelling claim.
"""


class DemandProfile(ABC):
    """A water-demand series expressed as a pure function of time."""

    profile_version: str

    @abstractmethod
    def lpm_at(self, when: datetime) -> float:
        """Household draw in litres per minute at ``when``."""

    def volume_l(self, start: datetime, minutes: float, step_minutes: float = 1.0) -> float:
        """Integrate the draw over a window, left-endpoint rule.

        ``step_minutes`` must divide the window evenly, and must be the step
        the simulator used, or the total will not match the trajectory it
        produced. A window that does not divide is rejected rather than
        silently truncated: dropping the remainder *under*-counts demand,
        which is the optimistic direction and the one that hides a problem.
        """
        if minutes < 0.0 or step_minutes <= 0.0:
            raise ValueError("minutes must be >= 0 and step_minutes > 0")
        steps = round(minutes / step_minutes)
        if abs(steps * step_minutes - minutes) > 1e-9:
            raise ValueError(
                f"step_minutes={step_minutes} does not divide minutes={minutes} evenly; "
                "truncating would under-count demand"
            )
        total = 0.0
        for index in range(steps):
            moment = start + timedelta(minutes=index * step_minutes)
            total += self.lpm_at(moment) * step_minutes
        return total

    def as_forecast(
        self,
        start: datetime,
        n_steps: int,
        step_minutes: float,
        model_name: str = "simulator_ground_truth",
    ) -> Forecast:
        """Expose the profile in the shape a scheduler consumes.

        This is a *perfect* forecast — the simulator handing over its own
        ground truth. It is the upper bound a real forecaster is measured
        against, and using it in an experiment must be reported as such,
        never as forecasting performance.
        """
        points = tuple(
            ForecastPoint(
                target_time=start + timedelta(minutes=index * step_minutes),
                p50=self.lpm_at(start + timedelta(minutes=index * step_minutes)),
                unit="lpm",
            )
            for index in range(n_steps)
        )
        return Forecast(
            target="water_demand_lpm",
            issued_at=start,
            points=points,
            model_name=model_name,
            model_version=self.profile_version,
            provenance=Provenance.SIMULATED,
        )


@dataclass(frozen=True, slots=True)
class ConstantDemandProfile(DemandProfile):
    """A flat draw. For unit tests and for isolating a single variable."""

    lpm: float
    profile_version: str = "demand-constant-1.0.0"

    def __post_init__(self) -> None:
        if self.lpm < 0.0:
            raise ValueError("lpm must be >= 0")

    def lpm_at(self, when: datetime) -> float:
        return self.lpm


@dataclass(frozen=True, slots=True)
class DiurnalDemandProfile(DemandProfile):
    """A two-peak household day.

    The hourly weights are interpolated linearly between hour midpoints.
    Because that interpolation is periodic and piecewise-linear, its mean
    over a full day equals the mean of the weights exactly — which is what
    lets ``daily_volume_l`` be exact rather than approximate when
    ``jitter`` is zero.
    """

    daily_volume_l: float
    weights: tuple[float, ...] = WEEKDAY_HOURLY_WEIGHTS
    weekend_scale: float = 1.15
    """Weekends draw more and later. Applied on Saturday and Sunday."""

    jitter: float = 0.0
    """Fractional day-to-day variation, e.g. ``0.15`` for +/-15%. Non-zero
    jitter breaks the exact daily budget on purpose — real households are
    not exact. Keep it at zero when a test asserts a volume."""

    seed: int = 20260915
    jitter_slot_minutes: float = 30.0
    profile_version: str = "demand-diurnal-1.0.0"

    def __post_init__(self) -> None:
        if self.daily_volume_l < 0.0:
            raise ValueError("daily_volume_l must be >= 0")
        if len(self.weights) != 24:
            raise ValueError("weights must have exactly 24 entries")
        if any(w < 0.0 for w in self.weights):
            raise ValueError("weights must be >= 0")
        if sum(self.weights) <= 0.0:
            raise ValueError("weights must not sum to zero")
        if not 0.0 <= self.jitter < 1.0:
            raise ValueError("jitter must be in [0, 1)")
        if self.weekend_scale < 0.0:
            raise ValueError("weekend_scale must be >= 0")
        if self.jitter_slot_minutes <= 0.0:
            raise ValueError("jitter_slot_minutes must be > 0")

    def shape_at(self, when: datetime) -> float:
        """The interpolated hourly weight, before any scaling."""
        position = (when.hour * 60 + when.minute + when.second / 60.0) / 60.0 - 0.5
        lower = int(position // 1)
        fraction = position - lower
        left = self.weights[lower % 24]
        right = self.weights[(lower + 1) % 24]
        return left + (right - left) * fraction

    def lpm_at(self, when: datetime) -> float:
        mean_weight = sum(self.weights) / 24.0
        base = self.daily_volume_l * self.shape_at(when) / (mean_weight * 1440.0)

        if when.weekday() >= 5:
            base *= self.weekend_scale

        if self.jitter > 0.0:
            slot = int(when.timestamp() // (self.jitter_slot_minutes * 60))
            base *= 1.0 + self.jitter * signed_noise(self.seed, slot)

        return max(0.0, base)


@dataclass(frozen=True, slots=True)
class SpikeWindow:
    """An unusual draw laid over the base profile — guests, washing, a leak."""

    start: datetime
    minutes: float
    extra_lpm: float

    def __post_init__(self) -> None:
        if self.minutes <= 0.0:
            raise ValueError("minutes must be > 0")
        if self.extra_lpm < 0.0:
            raise ValueError("extra_lpm must be >= 0")

    def contains(self, when: datetime) -> bool:
        return self.start <= when < self.start + timedelta(minutes=self.minutes)


@dataclass(frozen=True, slots=True)
class SpikeDemandProfile(DemandProfile):
    """A base profile plus explicit spikes.

    The scenario that breaks naive schedulers: flexibility looked ample
    right up until it did not. Spikes are declared rather than sampled so a
    failure can be reproduced and pointed at.
    """

    base: DemandProfile
    spikes: tuple[SpikeWindow, ...] = field(default_factory=tuple)
    profile_version: str = "demand-spike-1.0.0"

    def lpm_at(self, when: datetime) -> float:
        extra = sum(spike.extra_lpm for spike in self.spikes if spike.contains(when))
        return self.base.lpm_at(when) + extra
