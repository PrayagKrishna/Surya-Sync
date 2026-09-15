"""Base household load and the solar / grid energy split.

The system's headline claim is that it runs the pump on energy that would
otherwise have been exported. Measuring that needs an explicit, stated
attribution rule, because "solar-powered" is otherwise a matter of opinion.

**The rule: the pump may only claim surplus.** The base household load
exists whether or not the pump runs, so PV is attributed to it first and
the pump competes for what is left over. This is marginal attribution, and
it is the conservative choice — it never lets the scheduler take credit for
solar that the fridge was going to consume anyway.

Splitting on this basis is what makes Phase 3's exit criterion
("beats the threshold controller on grid-powered pump energy") a real test
rather than an artefact of bookkeeping.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

from surya_sync.config.schema import SolarConfig
from surya_sync.simulator.noise import signed_noise

BASE_LOAD_HOURLY_KW: tuple[float, ...] = (
    0.22, 0.20, 0.19, 0.19, 0.20, 0.28,   # 00:00 - 05:59  fridge, standby
    0.45, 0.62, 0.55, 0.40, 0.35, 0.38,   # 06:00 - 11:59
    0.48, 0.42, 0.35, 0.33, 0.38, 0.52,   # 12:00 - 17:59
    0.78, 0.92, 0.85, 0.62, 0.40, 0.28,   # 18:00 - 23:59  evening peak
)
"""Non-controllable household draw by hour, in kW, anchored at hour
midpoints. Evening-heavy and deliberately overlapping the solar shoulder,
because that overlap is what makes surplus scarce when it matters."""


class BaseLoadProfile(ABC):
    """Non-controllable household electrical load, as a function of time."""

    profile_version: str

    @abstractmethod
    def kw_at(self, when: datetime) -> float:
        """Household draw excluding every load SuryaSync controls."""


@dataclass(frozen=True, slots=True)
class ConstantBaseLoadProfile(BaseLoadProfile):
    """A flat draw. For unit tests and single-variable experiments."""

    kw: float
    profile_version: str = "baseload-constant-1.0.0"

    def __post_init__(self) -> None:
        if self.kw < 0.0:
            raise ValueError("kw must be >= 0")

    def kw_at(self, when: datetime) -> float:
        return self.kw


@dataclass(frozen=True, slots=True)
class DiurnalBaseLoadProfile(BaseLoadProfile):
    """A typical household day, interpolated between hourly values."""

    hourly_kw: tuple[float, ...] = BASE_LOAD_HOURLY_KW
    scale: float = 1.0
    jitter: float = 0.0
    seed: int = 20260915
    jitter_slot_minutes: float = 30.0
    profile_version: str = "baseload-diurnal-1.0.0"

    def __post_init__(self) -> None:
        if len(self.hourly_kw) != 24:
            raise ValueError("hourly_kw must have exactly 24 entries")
        if any(value < 0.0 for value in self.hourly_kw):
            raise ValueError("hourly_kw must be >= 0")
        if self.scale < 0.0:
            raise ValueError("scale must be >= 0")
        if not 0.0 <= self.jitter < 1.0:
            raise ValueError("jitter must be in [0, 1)")
        if self.jitter_slot_minutes <= 0.0:
            raise ValueError("jitter_slot_minutes must be > 0")

    def kw_at(self, when: datetime) -> float:
        position = (when.hour * 60 + when.minute + when.second / 60.0) / 60.0 - 0.5
        lower = int(position // 1)
        fraction = position - lower
        left = self.hourly_kw[lower % 24]
        right = self.hourly_kw[(lower + 1) % 24]
        value = (left + (right - left) * fraction) * self.scale

        if self.jitter > 0.0:
            slot = int(when.timestamp() // (self.jitter_slot_minutes * 60))
            value *= 1.0 + self.jitter * signed_noise(self.seed, slot)

        return max(0.0, value)


@dataclass(frozen=True, slots=True)
class EnergySplit:
    """Where one interval's energy came from and where it went.

    Power fields are interval averages in kW; energy fields are kWh for the
    interval. Both are kept because the scheduler reasons in power and the
    experiment reports in energy.
    """

    minutes: float
    pv_kw: float
    base_load_kw: float
    controllable_kw: float

    surplus_kw: float
    """PV left after the base load, before the controllable load bids for it.
    Below ``surplus_threshold_kw`` this is reported as zero."""

    controllable_solar_kw: float
    controllable_grid_kw: float
    grid_import_kw: float
    export_kw: float

    @property
    def controllable_solar_kwh(self) -> float:
        return self.controllable_solar_kw * self.minutes / 60.0

    @property
    def controllable_grid_kwh(self) -> float:
        return self.controllable_grid_kw * self.minutes / 60.0

    @property
    def grid_import_kwh(self) -> float:
        return self.grid_import_kw * self.minutes / 60.0

    @property
    def export_kwh(self) -> float:
        return self.export_kw * self.minutes / 60.0

    @property
    def solar_fraction(self) -> float:
        """Share of the controllable load met by surplus PV.

        ``1.0`` when nothing controllable ran — nothing was drawn from the
        grid, so nothing failed to be solar-powered. Reporting ``0.0`` there
        would punish a scheduler for correctly staying off.
        """
        if self.controllable_kw <= 0.0:
            return 1.0
        return self.controllable_solar_kw / self.controllable_kw


@dataclass(frozen=True, slots=True)
class GridModel:
    """Attributes generation to loads under the surplus-only rule."""

    surplus_threshold_kw: float = 0.1
    """Surplus below this is treated as zero: sensor noise, not energy."""

    model_version: str = "grid-1.0.0"

    def __post_init__(self) -> None:
        if self.surplus_threshold_kw < 0.0:
            raise ValueError("surplus_threshold_kw must be >= 0")

    @classmethod
    def from_config(cls, config: SolarConfig) -> GridModel:
        return cls(surplus_threshold_kw=config.surplus_threshold_kw)

    def surplus_kw(self, pv_kw: float, base_load_kw: float) -> float:
        """PV available to controllable loads after the base load is served."""
        surplus = pv_kw - base_load_kw
        return surplus if surplus >= self.surplus_threshold_kw else 0.0

    def split(
        self,
        pv_kw: float,
        base_load_kw: float,
        controllable_kw: float,
        minutes: float,
    ) -> EnergySplit:
        if minutes < 0.0:
            raise ValueError("minutes must be >= 0")
        if pv_kw < 0.0 or base_load_kw < 0.0 or controllable_kw < 0.0:
            raise ValueError("power values must be >= 0")

        surplus = self.surplus_kw(pv_kw, base_load_kw)
        from_solar = min(controllable_kw, surplus)
        from_grid = controllable_kw - from_solar

        return EnergySplit(
            minutes=minutes,
            pv_kw=pv_kw,
            base_load_kw=base_load_kw,
            controllable_kw=controllable_kw,
            surplus_kw=surplus,
            controllable_solar_kw=from_solar,
            controllable_grid_kw=from_grid,
            grid_import_kw=max(0.0, base_load_kw - pv_kw) + from_grid,
            export_kw=max(0.0, surplus - from_solar),
        )
