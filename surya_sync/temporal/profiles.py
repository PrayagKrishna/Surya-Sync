"""Historical per-slot behavioural profiles.

A slot profile answers "what does this household usually draw at this
time of day, on this kind of day" — the historical-mean half of the
feature vector, distinct from the lag/rolling features in ``history.py``
which look at the immediate past rather than a long-run average.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from surya_sync.domain import TimedValue
from surya_sync.temporal.context import DayType, slot_index_of, slots_per_day


@dataclass(frozen=True, slots=True)
class SlotKey:
    day_type: DayType
    slot_index: int


@dataclass(frozen=True, slots=True)
class SlotStats:
    mean: float
    count: int


@dataclass(frozen=True, slots=True)
class SlotProfile:
    """Per-(day-type, slot) mean of a quantity, built from history.

    ``min_samples`` exists because a mean of one sample is not a profile,
    it's an echo of that one day. Below it, every accessor reports
    unknown — a `standard_set` run (Monday start, 3 days) supplies zero
    weekend samples, and this must say so rather than quietly reusing the
    weekday mean.
    """

    slot_minutes: float
    min_samples: int
    stats: dict[SlotKey, SlotStats] = field(default_factory=dict)
    profile_version: str = "slot-profile-1.0.0"

    def _key_for(self, moment: datetime) -> SlotKey:
        return SlotKey(
            day_type=DayType.of(moment),
            slot_index=slot_index_of(moment, self.slot_minutes),
        )

    def stats_for(self, moment: datetime) -> SlotStats | None:
        found = self.stats.get(self._key_for(moment))
        if found is None or found.count < self.min_samples:
            return None
        return found

    def mean_for(self, moment: datetime) -> float | None:
        found = self.stats_for(moment)
        return None if found is None else found.mean

    def sample_count_for(self, moment: datetime) -> int:
        """Reports the raw count even below ``min_samples`` — this is what
        lets a caller see "3 samples, not enough yet" rather than a flat
        zero indistinguishable from "no history at all"."""
        found = self.stats.get(self._key_for(moment))
        return 0 if found is None else found.count


def build_slot_profile(
    samples: tuple[TimedValue, ...],
    slot_minutes: float,
    min_samples: int,
) -> SlotProfile:
    """Aggregate ``samples`` into per-(day-type, slot) means.

    ``samples`` need not be chronological — a mean does not care about
    order, unlike the lag/rolling features in ``history.py``.
    """
    slots_per_day(slot_minutes)  # validates slot_minutes divides evenly
    if min_samples < 1:
        raise ValueError("min_samples must be >= 1")

    sums: dict[SlotKey, float] = {}
    counts: dict[SlotKey, int] = {}
    for sample in samples:
        key = SlotKey(
            day_type=DayType.of(sample.at),
            slot_index=slot_index_of(sample.at, slot_minutes),
        )
        sums[key] = sums.get(key, 0.0) + sample.value
        counts[key] = counts.get(key, 0) + 1

    stats = {
        key: SlotStats(mean=sums[key] / counts[key], count=counts[key])
        for key in sums
    }
    return SlotProfile(slot_minutes=slot_minutes, min_samples=min_samples, stats=stats)
