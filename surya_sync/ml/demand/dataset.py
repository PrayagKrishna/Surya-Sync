"""Turn a resource's observation history into a model-ready demand dataset.

The training target is ``models.tank.observed_demand_series`` — demand
*recovered from tank readings*, not the simulator's ground truth. A real
Pi never has ground truth; it only has the readings it must invert, gaps
and all (see the Phase 4 carried-forward note in ``CLAUDE.md``: "Phase 5's
training data will have gaps at exactly the demand spikes and dry-outs
that matter most — expected, not a bug to fix by guessing").

Chronological, walk-forward split (hard rule — never shuffled):

- The demand series itself is split first, by position:
  ``train_frac``/``val_frac``/remainder-as-test.
- ``temporal.profiles.SlotProfile`` is fit on the **train** portion only.
  Its per-slot mean is a historical average with no notion of "before" or
  "after" within a (day-type, slot) bucket — fitting it on the whole
  series would let a training-period feature carry information smoothed
  in from val/test-period days, which is the same shape of leak the
  walk-forward rule exists to prevent.
- Lag/rolling features (``demand_lag_*``, ``demand_roll_*``) are built
  from the **full** demand series for every example, train or test. This
  is not a leak: ``temporal.history`` only ever looks strictly backward
  from ``moment`` (``value_at_lag``, and ``rolling_stats``'s
  ``[now - window, now)`` window), so a val/test moment's lag features can
  only ever see data that is earlier than that moment — exactly what a
  live system would already have on hand by the time it reached that
  instant, regardless of which split the moment nominally falls in.
"""

from __future__ import annotations

from dataclasses import dataclass

from surya_sync.domain import TimedValue
from surya_sync.ml.evaluation import chronological_split
from surya_sync.ml.features.builder import FeatureVector, build_feature_vector
from surya_sync.models.generic_resource import ResourceObservation
from surya_sync.models.pump import PumpModel
from surya_sync.models.tank import observed_demand_series
from surya_sync.temporal.profiles import SlotProfile, build_slot_profile


@dataclass(frozen=True, slots=True)
class DemandSplit:
    features: tuple[FeatureVector, ...]
    targets: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.features) != len(self.targets):
            raise ValueError(
                f"{len(self.features)} feature vectors for {len(self.targets)} targets"
            )

    def __len__(self) -> int:
        return len(self.targets)


@dataclass(frozen=True, slots=True)
class DemandDataset:
    train: DemandSplit
    val: DemandSplit
    test: DemandSplit
    slot_profile: SlotProfile
    """Fit on ``train`` only — see module docstring."""


def build_demand_dataset(
    observations: tuple[ResourceObservation, ...],
    pump: PumpModel,
    slot_minutes: float,
    min_samples: int,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
) -> DemandDataset:
    """Build a chronologically split demand dataset from raw tank readings.

    ``observations`` must already be chronological (as
    ``ObservationRepository.history()`` returns them); this does not sort.
    Raises ``ValueError`` if too few demand intervals can be identified to
    split three ways — a caller asking for a dataset from too little
    history has a bug, not a silently tiny test set.
    """
    demand_series = observed_demand_series(observations, pump)
    if len(demand_series) < 10:
        raise ValueError(
            f"only {len(demand_series)} identifiable demand intervals; need "
            "at least 10 to build a train/val/test split"
        )

    train_targets, val_targets, test_targets = chronological_split(
        demand_series, train_frac, val_frac
    )
    if not train_targets or not val_targets or not test_targets:
        raise ValueError(
            f"split produced an empty subset: {len(train_targets)} train, "
            f"{len(val_targets)} val, {len(test_targets)} test — widen the "
            "history or adjust train_frac/val_frac"
        )

    slot_profile = build_slot_profile(train_targets, slot_minutes, min_samples)

    service_level_series = tuple(
        TimedValue(at=obs.timestamp, value=obs.service_level, provenance=obs.provenance)
        for obs in observations
    )

    def _split(subset: tuple[TimedValue, ...]) -> DemandSplit:
        features = tuple(
            build_feature_vector(
                tv.at,
                demand_series=demand_series,
                slot_profile=slot_profile,
                service_level_series=service_level_series,
                provenance=tv.provenance,
            )
            for tv in subset
        )
        return DemandSplit(features=features, targets=tuple(tv.value for tv in subset))

    return DemandDataset(
        train=_split(train_targets),
        val=_split(val_targets),
        test=_split(test_targets),
        slot_profile=slot_profile,
    )
