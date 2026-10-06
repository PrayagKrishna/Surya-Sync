"""Turn an hourly PV series into a lead-time solar forecasting dataset.

One example = "standing at ``issue_time`` with everything observed up to
then, what will the array deliver at ``issue_time + lead``?" Leads 1-12 h
cover the scheduler's 720-minute horizon at hourly spacing; 18 and 24 h
cover the day-ahead case.

Daylight only. When the sun is below the horizon the clear-sky curve is
exactly zero, and so is the array. That is geometry, not a forecast, so
every method (baselines included) is scored only on targets where the
clear-sky output exceeds ``DAYLIGHT_FLOOR_KW``. Scoring night hours would
add the same free zero to every method and make all of them look better.

Walk-forward split (hard rule — never shuffled): the series is cut by
position into train/val/test, and an example belongs to the split its
**target time** falls in. An example's inputs may reach back into an
earlier split — a live system has that history on hand — but never forward:
every feature below looks at ``issue_time`` and earlier only, which
``tests/test_solar_forecast.py`` pins by perturbing the future and checking
the vector does not move. Assigning by issue time instead would let a
training example's *target* sit inside the validation period.

``SlotProfile`` (the historical-profile baseline's data) is fit on the train
slice only, for the same reason as in ``ml.demand.dataset``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from surya_sync.domain import Provenance, TimedValue
from surya_sync.ml.evaluation import chronological_split
from surya_sync.ml.features.builder import FeatureVector
from surya_sync.models.solar import ClearSkyModel
from surya_sync.temporal.context import cyclic
from surya_sync.temporal.profiles import SlotProfile, build_slot_profile
from surya_sync.version import SOLAR_FEATURE_SET_VERSION

LEAD_HOURS: tuple[int, ...] = (*range(1, 13), 18, 24)
HEADLINE_LEADS: tuple[int, ...] = (1, 3, 6, 12, 24)
"""The leads printed in the report. All of ``LEAD_HOURS`` are scored."""

DAYLIGHT_FLOOR_KW = 0.05
"""Below this clear-sky output the sun is effectively down. About 2% of a
3 kW array."""

SOLAR_FEATURE_NAMES: tuple[str, ...] = (
    "lead_hours",
    "clear_sky_target_kw",
    "clear_sky_now_kw",
    "observed_now_kw",
    "ksi_now",
    "smart_persistence_kw",
    "ksi_mean_3h",
    "ksi_mean_24h",
    "ksi_yesterday_at_target",
    "tod_sin",
    "tod_cos",
    "doy_sin",
    "doy_cos",
)
"""Fixed order; a model trained on one ordering cannot be fed another.

``ksi`` is the clear-sky index: observed output divided by what a cloudless
sky would give at that moment. It strips out the sun's own daily and
seasonal motion, leaving the part that is actually weather. The clear-sky
terms come from solar geometry for the configured latitude/longitude, a
deterministic function of time, so they carry no leakage.
``smart_persistence_kw`` (``ksi_now * clear_sky_target_kw``) is also its own
baseline; it is a feature so a linear model can use the product directly."""


@dataclass(frozen=True, slots=True)
class SolarSplit:
    features: tuple[FeatureVector, ...]
    targets: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.features) != len(self.targets):
            raise ValueError(
                f"{len(self.features)} feature vectors for {len(self.targets)} targets"
            )

    def __len__(self) -> int:
        return len(self.targets)

    def lead_of(self, index: int) -> int:
        return int(self.features[index].as_dict()["lead_hours"])


@dataclass(frozen=True, slots=True)
class SolarDataset:
    train: SolarSplit
    val: SolarSplit
    test: SolarSplit
    slot_profile: SlotProfile
    """Hour-of-day mean output, fit on the train slice only."""


class PvHistory:
    """Exact-timestamp lookup over an hourly series, plus clear-sky index."""

    def __init__(self, series: tuple[TimedValue, ...], clear_sky: ClearSkyModel) -> None:
        self._kw = {point.at: point.value for point in series}
        self._clear_sky = clear_sky

    def kw(self, at: datetime) -> float | None:
        return self._kw.get(at)

    def ksi(self, at: datetime) -> float | None:
        """Clear-sky index at ``at``; ``None`` at night or with no reading."""
        observed = self._kw.get(at)
        clear = self._clear_sky.kw_at(at)
        if observed is None or clear <= DAYLIGHT_FLOOR_KW:
            return None
        return observed / clear

    def ksi_mean(self, issue_time: datetime, hours: int) -> float | None:
        """Mean ksi over ``[issue_time - hours, issue_time]``, daylight only."""
        values = [
            k
            for back in range(hours + 1)
            if (k := self.ksi(issue_time - timedelta(hours=back))) is not None
        ]
        return sum(values) / len(values) if values else None


def build_solar_features(
    issue_time: datetime,
    lead_hours: int,
    series: PvHistory,
    clear_sky: ClearSkyModel,
    provenance: Provenance,
) -> FeatureVector:
    """The fixed-order vector for one (issue time, lead) pair.

    Looks at ``issue_time`` and earlier only. ``ksi_yesterday_at_target`` is
    the clear-sky index 24 h before the target; that is in the past only if
    ``lead_hours <= 24``, which is enforced.
    """
    if not 0 < lead_hours <= 24:
        raise ValueError(f"lead_hours must be in (0, 24], got {lead_hours}")
    target_time = issue_time + timedelta(hours=lead_hours)

    clear_target = clear_sky.kw_at(target_time)
    clear_now = clear_sky.kw_at(issue_time)
    observed_now = series.kw(issue_time)
    ksi_now = series.ksi(issue_time)
    tod_sin, tod_cos = cyclic(target_time.hour + target_time.minute / 60.0, 24.0)
    doy_sin, doy_cos = cyclic(float(target_time.timetuple().tm_yday - 1), 365.25)

    values: dict[str, float | None] = {
        "lead_hours": float(lead_hours),
        "clear_sky_target_kw": clear_target,
        "clear_sky_now_kw": clear_now,
        "observed_now_kw": observed_now,
        "ksi_now": ksi_now,
        "smart_persistence_kw": None if ksi_now is None else ksi_now * clear_target,
        "ksi_mean_3h": series.ksi_mean(issue_time, 3),
        "ksi_mean_24h": series.ksi_mean(issue_time, 24),
        "ksi_yesterday_at_target": series.ksi(target_time - timedelta(hours=24)),
        "tod_sin": tod_sin,
        "tod_cos": tod_cos,
        "doy_sin": doy_sin,
        "doy_cos": doy_cos,
    }
    return FeatureVector(
        moment=issue_time,
        names=SOLAR_FEATURE_NAMES,
        values=tuple(values[name] for name in SOLAR_FEATURE_NAMES),
        feature_set_version=SOLAR_FEATURE_SET_VERSION,
        provenance=provenance,
    )


def build_solar_dataset(
    series: tuple[TimedValue, ...],
    clear_sky: ClearSkyModel,
    min_samples: int = 30,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
    leads: tuple[int, ...] = LEAD_HOURS,
) -> SolarDataset:
    """Chronologically split dataset from an hourly PV series.

    ``series`` must be chronological and evenly hourly (a missing hour is
    simply a lookup that returns ``None``; the target hour itself must be
    present for an example to exist). Raises if any split comes out empty.
    """
    if len(series) < 24 * 30:
        raise ValueError(f"{len(series)} hourly points is too little for a three-way split")

    train_pts, val_pts, test_pts = chronological_split(series, train_frac, val_frac)
    if not train_pts or not val_pts or not test_pts:
        raise ValueError("split produced an empty subset; widen the series")

    lookup = PvHistory(series, clear_sky)
    provenance = series[0].provenance

    def _split(points: tuple[TimedValue, ...]) -> SolarSplit:
        features: list[FeatureVector] = []
        targets: list[float] = []
        for target in points:
            if clear_sky.kw_at(target.at) <= DAYLIGHT_FLOOR_KW:
                continue
            for lead in leads:
                issue_time = target.at - timedelta(hours=lead)
                if lookup.kw(issue_time) is None:
                    continue  # nothing observed at issue time: no example
                features.append(
                    build_solar_features(issue_time, lead, lookup, clear_sky, provenance)
                )
                targets.append(target.value)
        return SolarSplit(features=tuple(features), targets=tuple(targets))

    return SolarDataset(
        train=_split(train_pts),
        val=_split(val_pts),
        test=_split(test_pts),
        slot_profile=build_slot_profile(train_pts, slot_minutes=60.0, min_samples=min_samples),
    )
