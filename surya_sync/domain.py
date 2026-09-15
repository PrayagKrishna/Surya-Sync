"""Cross-cutting vocabulary shared by every layer.

Kept deliberately dependency-free: this module imports nothing from the
rest of the package, so it can never participate in an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ControlAction(str, Enum):
    """The action space exposed to every scheduler.

    Deliberately minimal — on/off resources are what SuryaSync schedules.
    Continuous-power resources (battery charging, EV modulation) will
    extend this in Phase 16, not before.
    """

    RUN = "RUN"
    """Energize the actuator for the coming interval."""

    WAIT = "WAIT"
    """Stay de-energized. The *deliberate* choice to defer, not idleness."""

    STOP = "STOP"
    """De-energize an actuator that is currently running."""


class Provenance(str, Enum):
    """Where a number came from.

    Hard rule: never present simulated results as physical results. Every
    persisted value carries one of these labels, and every report groups
    by it.
    """

    MEASURED = "measured"
    """Read from physical sensors."""

    SIMULATED = "simulated"
    """Produced by the simulator. Not physical evidence."""

    PREDICTED = "predicted"
    """Model output about a future time."""

    ESTIMATED = "estimated"
    """Inferred from measurements with a model (e.g. flexibility minutes)."""

    DERIVED = "derived"
    """Deterministic transform of other values (e.g. mm -> litres)."""


class RunMode(str, Enum):
    """How the control loop is sourcing its observations."""

    SIMULATED = "simulated"
    REAL = "real"
    REPLAY = "replay"
    """Re-run a recorded observation stream through the scheduler."""


class ResourceType(str, Enum):
    """Known flexible-resource classes.

    Only ``WATER_TANK`` is implemented. The rest are listed to keep the
    ``FlexibleResource`` interface honest about what it must generalize to.
    """

    WATER_TANK = "water_tank"
    WASHING_MACHINE = "washing_machine"
    WATER_HEATER = "water_heater"
    EV_CHARGER = "ev_charger"
    BATTERY = "battery"
    HVAC = "hvac"


@dataclass(frozen=True, slots=True)
class TimeStep:
    """One slot of a discretized planning horizon."""

    index: int
    start: datetime
    minutes: float


@dataclass(frozen=True, slots=True)
class Horizon:
    """A discretized planning horizon.

    ``step_minutes`` is the control resolution; ``length_minutes`` is how
    far ahead the scheduler looks. MPC re-plans the whole horizon every
    cycle but executes only the first step.
    """

    start: datetime
    length_minutes: float
    step_minutes: float

    @property
    def n_steps(self) -> int:
        return int(self.length_minutes // self.step_minutes)


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """A single forecast value with its uncertainty band.

    ``p10``/``p90`` are populated from Phase 10 onward. Until then they
    equal ``p50`` and schedulers must not assume the band is meaningful —
    check ``has_band``.
    """

    target_time: datetime
    p50: float
    p10: float | None = None
    p90: float | None = None
    unit: str = ""

    @property
    def has_band(self) -> bool:
        return self.p10 is not None and self.p90 is not None


@dataclass(frozen=True, slots=True)
class Forecast:
    """A forecast series for one target, stamped with its producer.

    Conservative scheduling uses ``Q_P90`` for demand and ``PV_P10`` for
    solar — i.e. plan for more thirst and less sun than expected.
    """

    target: str
    """e.g. ``"water_demand_lpm"``, ``"pv_generation_kw"``."""

    issued_at: datetime
    points: tuple[ForecastPoint, ...]
    model_name: str
    model_version: str
    provenance: Provenance = Provenance.PREDICTED


DEFAULT_FORECAST_STEP_MINUTES = 15.0
"""Used only where a step cannot be inferred from a forecast's spacing."""


def forecast_value_at(
    forecast: Forecast, moment: datetime, conservative: bool = False
) -> float:
    """The forecast value governing ``moment``, by zero-order hold.

    Each point governs until the next one starts, and the earliest point
    governs anything before it. ``conservative`` takes ``p90`` where a band
    exists — plan for more demand, not less.

    Deliberately does not assume the points are in chronological order.
    ``Forecast`` makes no such promise, and one rebuilt from the database
    need not be; walking until the first point in the future would return a
    value from the wrong time and raise nothing.
    """
    if not forecast.points:
        raise ValueError(
            f"forecast {forecast.target!r} has no points — refusing to assume "
            "a zero value, which would make every trajectory look safe"
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
        return DEFAULT_FORECAST_STEP_MINUTES
    delta = forecast.points[1].target_time - forecast.points[0].target_time
    minutes = delta.total_seconds() / 60.0
    return minutes if minutes > 0.0 else DEFAULT_FORECAST_STEP_MINUTES
