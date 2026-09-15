"""The standard scenario set: sunny, cloudy, spike, low-start.

Phase 2 onward compares controllers, and a comparison is only meaningful
if every controller met identical conditions. A ``Scenario`` is therefore a
complete, declarative statement of those conditions — profiles, start time,
initial level, duration — and it builds a fresh simulator on demand rather
than handing out a shared mutable one.

The four cases are chosen to attack different failure modes:

``sunny``
    Plenty of surplus. Any controller should do well; one that does not is
    broken. This is the sanity floor.
``cloudy``
    Surplus is scarce and short. Separates schedulers that plan from
    schedulers that hope.
``spike``
    Demand jumps without warning. Tests whether flexibility was genuinely
    available or merely assumed.
``low_start``
    Begins just above the critical floor. Service comes first; a scheduler
    that waits for sun here has mistaken the objective for the constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from surya_sync.config.schema import Config
from surya_sync.simulator.demand import (
    DemandProfile,
    DiurnalDemandProfile,
    SpikeDemandProfile,
    SpikeWindow,
)
from surya_sync.simulator.grid import BaseLoadProfile, DiurnalBaseLoadProfile
from surya_sync.simulator.solar import (
    ClearSkyProfile,
    IntermittentProfile,
    OvercastProfile,
    SolarProfile,
)
from surya_sync.simulator.tank import TankSimulator

DEFAULT_START = datetime(2026, 3, 2, 0, 0)
"""A Monday near the equinox: day length is neither the year's best nor its
worst, and a run of up to five days stays on weekdays, so a comparison is
not perturbed by the weekend demand scaling landing differently."""

DEFAULT_DAILY_DEMAND_L = 450.0
DEFAULT_SEED = 20260915


@dataclass(frozen=True, slots=True)
class Scenario:
    """A named, reproducible set of simulation conditions."""

    name: str
    description: str
    demand_profile: DemandProfile
    solar_profile: SolarProfile
    base_load_profile: BaseLoadProfile
    initial_service_level: float
    start: datetime = DEFAULT_START
    days: float = 3.0
    scenario_version: str = "scenarios-1.0.0"

    def __post_init__(self) -> None:
        if self.days <= 0.0:
            raise ValueError("days must be > 0")
        if not 0.0 <= self.initial_service_level <= 1.0:
            raise ValueError("initial_service_level must be in [0, 1]")

    @property
    def end(self) -> datetime:
        return self.start + timedelta(days=self.days)

    def total_minutes(self) -> float:
        return self.days * 1440.0

    def n_steps(self, step_minutes: float) -> int:
        if step_minutes <= 0.0:
            raise ValueError("step_minutes must be > 0")
        return int(self.total_minutes() // step_minutes)

    def build(
        self,
        config: Config,
        resource_id: str = "tank_1",
        cold_start: bool = False,
    ) -> TankSimulator:
        """A fresh simulator at the scenario's starting conditions.

        Called once per controller so that no run can be contaminated by a
        previous one's state.

        ``cold_start`` leaves the actuator's timing unknown, as on a Pi that
        has booted with no actuation log. It is off by default because a
        comparison between controllers must not be perturbed by it; it is
        available because the path needs exercising by something other than
        a unit test. See ``TankSimulator.cold_start``.
        """
        return TankSimulator.from_config(
            config=config,
            demand_profile=self.demand_profile,
            solar_profile=self.solar_profile,
            start=self.start,
            initial_service_level=self.initial_service_level,
            base_load_profile=self.base_load_profile,
            resource_id=resource_id,
            cold_start=cold_start,
        )


def _clear_sky(config: Config) -> ClearSkyProfile:
    return ClearSkyProfile.from_config(config.solar)


def _household_demand(daily_volume_l: float = DEFAULT_DAILY_DEMAND_L) -> DiurnalDemandProfile:
    return DiurnalDemandProfile(daily_volume_l=daily_volume_l, seed=DEFAULT_SEED)


def sunny(config: Config, days: float = 3.0) -> Scenario:
    return Scenario(
        name="sunny",
        description="Clear sky and ordinary demand. Surplus is abundant; any "
        "controller that fails here is broken.",
        demand_profile=_household_demand(),
        solar_profile=_clear_sky(config),
        base_load_profile=DiurnalBaseLoadProfile(),
        initial_service_level=0.60,
        days=days,
    )


def cloudy(config: Config, days: float = 3.0) -> Scenario:
    return Scenario(
        name="cloudy",
        description="Overcast with passing breaks. Surplus windows are short "
        "and intermittent, which punishes purely reactive control.",
        demand_profile=_household_demand(),
        solar_profile=IntermittentProfile(
            base=OvercastProfile(clear_sky=_clear_sky(config), cloud_factor=0.45),
            seed=DEFAULT_SEED,
        ),
        base_load_profile=DiurnalBaseLoadProfile(),
        initial_service_level=0.60,
        days=days,
    )


def spike(config: Config, days: float = 3.0) -> Scenario:
    """Two unannounced heavy draws on the second day."""
    base = _household_demand()
    day_two = DEFAULT_START + timedelta(days=1)
    return Scenario(
        name="spike",
        description="Ordinary days interrupted by two large unforecast draws. "
        "Tests whether the flexibility claimed was really there.",
        demand_profile=SpikeDemandProfile(
            base=base,
            spikes=(
                SpikeWindow(start=day_two.replace(hour=11), minutes=45.0, extra_lpm=8.0),
                SpikeWindow(start=day_two.replace(hour=20), minutes=30.0, extra_lpm=12.0),
            ),
        ),
        solar_profile=_clear_sky(config),
        base_load_profile=DiurnalBaseLoadProfile(),
        initial_service_level=0.60,
        days=days,
    )


def low_start(config: Config, days: float = 3.0) -> Scenario:
    """Begins just above the hard floor, before sunrise."""
    return Scenario(
        name="low_start",
        description="Starts barely above critical, hours before sunrise. "
        "Service availability must outrank solar optimization here.",
        demand_profile=_household_demand(),
        solar_profile=_clear_sky(config),
        base_load_profile=DiurnalBaseLoadProfile(),
        initial_service_level=0.24,
        start=DEFAULT_START.replace(hour=4),
        days=days,
    )


def standard_set(config: Config, days: float = 3.0) -> tuple[Scenario, ...]:
    """Every controller from Phase 2 onward is measured against all four."""
    return (
        sunny(config, days),
        cloudy(config, days),
        spike(config, days),
        low_start(config, days),
    )
