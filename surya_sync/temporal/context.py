"""Build a temporal context vector for a timestamp.

Cyclic encoding only — no history is read here. ``profiles.py`` and
``history.py`` are what turn a household's past into a feature; this module
turns the clock alone into one.

Naive local datetimes throughout, matching the rest of the repo.
``SystemConfig.timezone`` (``"Asia/Kolkata"``) is declared but unused here
and everywhere else: Asia/Kolkata has no DST, so a naive local timestamp is
unambiguous for this deployment. A DST zone would make "slot of day"
ambiguous twice a year and is out of scope until a deployment needs it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

MINUTES_PER_DAY = 1440.0
DAYS_PER_WEEK = 7.0


class DayType(str, Enum):
    """A household's demand shape genuinely differs by day type —
    ``simulator/demand.py``'s ``DiurnalDemandProfile`` already scales
    weekends by 1.15. Kept as its own type rather than a bool so a future
    day-type (e.g. public holiday) is an enum addition, not a bool
    reinterpretation."""

    WEEKDAY = "weekday"
    WEEKEND = "weekend"

    @classmethod
    def of(cls, moment: datetime) -> DayType:
        """``weekday() >= 5``, the same weekend definition
        ``simulator/demand.py:176`` already uses — a second definition
        here would silently disagree with the profile that generated the
        training data."""
        return cls.WEEKEND if moment.weekday() >= 5 else cls.WEEKDAY


def slots_per_day(slot_minutes: float) -> int:
    """How many equal slots divide a day, or raise if they don't divide evenly.

    An uneven division would make the last slot of the day a different
    length from the rest, silently biasing its statistics — the same
    failure mode ``DemandProfile.volume_l`` guards against for
    integration windows.
    """
    if slot_minutes <= 0.0:
        raise ValueError("slot_minutes must be > 0")
    slots = MINUTES_PER_DAY / slot_minutes
    if slots != int(slots):
        raise ValueError(
            f"slot_minutes={slot_minutes} does not divide a day evenly "
            f"({MINUTES_PER_DAY} / {slot_minutes} = {slots})"
        )
    return int(slots)


def slot_index_of(moment: datetime, slot_minutes: float) -> int:
    """Which slot of the day ``moment`` falls in, ``0`` at midnight.

    Not the noise index used in ``simulator/*.py``
    (``int(when.timestamp() // (slot*60))``) — that is a monotonic count
    since the Unix epoch, used only to seed deterministic noise. This is a
    slot *of the day*, the same value every day at the same clock time.
    """
    slots_per_day(slot_minutes)  # validates slot_minutes divides evenly
    minute_of_day = moment.hour * 60 + moment.minute + moment.second / 60.0
    return int(minute_of_day // slot_minutes)


def cyclic(value: float, period: float) -> tuple[float, float]:
    """``(sin, cos)`` of ``value`` on a circle of length ``period``.

    The pair, not either alone, is what makes the encoding continuous:
    ``value=0`` and ``value=period`` must land on the same point, which a
    single sine cannot guarantee near its own wraparound.
    """
    if period <= 0.0:
        raise ValueError("period must be > 0")
    angle = 2.0 * math.pi * (value / period)
    return math.sin(angle), math.cos(angle)


def _days_in_year(year: int) -> float:
    is_leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return 366.0 if is_leap else 365.0


@dataclass(frozen=True, slots=True)
class TemporalContext:
    """The clock alone, encoded for a model — no history read."""

    moment: datetime
    slot_minutes: float
    slot_index: int
    day_type: DayType
    tod_sin: float
    tod_cos: float
    dow_sin: float
    dow_cos: float
    doy_sin: float
    doy_cos: float

    @property
    def is_weekend(self) -> bool:
        return self.day_type is DayType.WEEKEND


def build_temporal_context(moment: datetime, slot_minutes: float) -> TemporalContext:
    """Encode ``moment`` alone. Pure in time: same ``moment`` in, same
    context out, regardless of call order — the same property
    ``simulator`` profiles are held to, and for the same reason (MPC
    re-queries the same instant across replans)."""
    minute_of_day = moment.hour * 60 + moment.minute + moment.second / 60.0
    tod_sin, tod_cos = cyclic(minute_of_day, MINUTES_PER_DAY)
    dow_sin, dow_cos = cyclic(float(moment.weekday()), DAYS_PER_WEEK)
    day_of_year = moment.timetuple().tm_yday - 1  # 0-indexed, like the others
    doy_sin, doy_cos = cyclic(float(day_of_year), _days_in_year(moment.year))

    return TemporalContext(
        moment=moment,
        slot_minutes=slot_minutes,
        slot_index=slot_index_of(moment, slot_minutes),
        day_type=DayType.of(moment),
        tod_sin=tod_sin,
        tod_cos=tod_cos,
        dow_sin=dow_sin,
        dow_cos=dow_cos,
        doy_sin=doy_sin,
        doy_cos=doy_cos,
    )
