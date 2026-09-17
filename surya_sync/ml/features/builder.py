"""Assemble model-ready feature vectors from temporal context.

The single point where cyclic encoding (``temporal.context``), per-slot
history (``temporal.profiles``) and lag/rolling features
(``temporal.history``) come together into the fixed-order vector a Phase 5
model will consume. ``FEATURE_NAMES`` is the pinned spec: a model trained
on one ordering cannot be fed another, so the order lives in code and is
asserted by a test, not left to whatever order a dict happens to iterate.

Any feature whose inputs are missing is ``None``, never ``0.0`` — the same
rule ``domain.forecast_value_at`` follows. Imputation is a modelling
choice Phase 5 makes deliberately, not one buried here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from surya_sync.domain import Provenance, TimedValue
from surya_sync.temporal.context import build_temporal_context
from surya_sync.temporal.history import rolling_stats, value_at, value_at_lag
from surya_sync.temporal.profiles import SlotProfile
from surya_sync.version import FEATURE_SET_VERSION

FEATURE_NAMES: tuple[str, ...] = (
    "tod_sin",
    "tod_cos",
    "dow_sin",
    "dow_cos",
    "doy_sin",
    "doy_cos",
    "is_weekend",
    "slot_index",
    "slot_mean_demand_lpm",
    "slot_sample_count",
    "demand_lag_1_slot",
    "demand_lag_1_day",
    "demand_lag_7_day",
    "demand_roll_mean_1h",
    "demand_roll_mean_24h",
    "demand_roll_max_1h",
    "service_level",
    "service_level_delta_1h",
)
"""Fixed order. Section 12 of the original design doc is not in this repo;
this list is the pinned, testable substitute — see ``ROADMAP.md`` Phase 4
and the carried-forward note in ``CLAUDE.md``."""


@dataclass(frozen=True, slots=True)
class FeatureVector:
    moment: datetime
    names: tuple[str, ...]
    values: tuple[float | None, ...]
    feature_set_version: str
    provenance: Provenance
    """Whether the observations this vector was built from are measured or
    simulated — mirrors ``SchedulingRequest.provenance``, a single
    top-level flag rather than one per feature, since a cyclic-time
    feature has no data-quality question to answer and the demand/service-
    level features all come from the same caller-assembled history."""

    def __post_init__(self) -> None:
        if len(self.values) != len(self.names):
            raise ValueError(
                f"{len(self.values)} values for {len(self.names)} names"
            )

    def as_dict(self) -> dict[str, float | None]:
        return dict(zip(self.names, self.values))

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(
            name for name, value in zip(self.names, self.values) if value is None
        )


def build_feature_vector(
    moment: datetime,
    *,
    demand_series: tuple[TimedValue, ...],
    slot_profile: SlotProfile,
    service_level_series: tuple[TimedValue, ...],
    provenance: Provenance,
) -> FeatureVector:
    """Build the fixed-order feature vector for ``moment``.

    ``demand_series`` and ``service_level_series`` are each expected
    chronological, produced by ``models.tank.observed_demand_series`` and
    from raw ``ResourceObservation`` history respectively; this function
    does not sort them.

    The slot grid comes from ``slot_profile.slot_minutes`` alone — there is
    deliberately no separate ``slot_minutes`` argument. An earlier version
    took one, and nothing stopped it disagreeing with the profile's own
    grid: ``slot_index`` would be computed on one grid while
    ``slot_mean_demand_lpm`` was looked up on another, silently, in the
    same vector.
    """
    slot_minutes = slot_profile.slot_minutes
    context = build_temporal_context(moment, slot_minutes)
    tolerance = slot_minutes / 2.0

    slot_stats = slot_profile.stats_for(moment)
    service_level = value_at(service_level_series, moment, tolerance)
    service_level_1h_ago = value_at(
        service_level_series, moment - timedelta(minutes=60.0), tolerance
    )
    service_level_delta_1h = (
        None
        if service_level is None or service_level_1h_ago is None
        else service_level - service_level_1h_ago
    )

    roll_1h = rolling_stats(demand_series, moment, 60.0)
    roll_24h = rolling_stats(demand_series, moment, 1440.0)

    values: dict[str, float | None] = {
        "tod_sin": context.tod_sin,
        "tod_cos": context.tod_cos,
        "dow_sin": context.dow_sin,
        "dow_cos": context.dow_cos,
        "doy_sin": context.doy_sin,
        "doy_cos": context.doy_cos,
        "is_weekend": 1.0 if context.is_weekend else 0.0,
        "slot_index": float(context.slot_index),
        "slot_mean_demand_lpm": None if slot_stats is None else slot_stats.mean,
        "slot_sample_count": float(slot_profile.sample_count_for(moment)),
        "demand_lag_1_slot": value_at_lag(demand_series, moment, slot_minutes, tolerance),
        "demand_lag_1_day": value_at_lag(demand_series, moment, 1440.0, tolerance),
        "demand_lag_7_day": value_at_lag(demand_series, moment, 7 * 1440.0, tolerance),
        "demand_roll_mean_1h": None if roll_1h is None else roll_1h.mean,
        "demand_roll_mean_24h": None if roll_24h is None else roll_24h.mean,
        "demand_roll_max_1h": None if roll_1h is None else roll_1h.max,
        "service_level": service_level,
        "service_level_delta_1h": service_level_delta_1h,
    }

    return FeatureVector(
        moment=moment,
        names=FEATURE_NAMES,
        values=tuple(values[name] for name in FEATURE_NAMES),
        feature_set_version=FEATURE_SET_VERSION,
        provenance=provenance,
    )
