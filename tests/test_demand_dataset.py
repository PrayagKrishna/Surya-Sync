"""Phase 5 dataset assembly: chronological split, no leakage into the profile.

Uses real simulated observation history (``standard_set``, 3 days — fast,
and this is about the assembly plumbing, not about scenario duration) so
that ``models.tank.observed_demand_series``'s genuine gaps (overflow,
run-dry) are exercised rather than a hand-built series that never hits them.
"""

from __future__ import annotations

import pytest

from surya_sync.config.schema import Config
from surya_sync.experiments.runner import run_scenario
from surya_sync.ml.demand.dataset import build_demand_dataset
from surya_sync.ml.features.builder import FEATURE_NAMES
from surya_sync.models.pump import PumpModel
from surya_sync.scheduler.threshold import ThresholdScheduler
from surya_sync.simulator import scenarios


@pytest.fixture(scope="module")
def dataset():
    config = Config()
    scenario = scenarios.standard_set(config)[0]  # sunny, 3 days
    run = run_scenario(config, scenario, ThresholdScheduler(config.scheduler))
    pump = PumpModel.from_config(config.pump)
    return build_demand_dataset(
        run.observations,
        pump,
        config.temporal.slot_minutes,
        config.temporal.profile_min_samples,
    )


def test_every_split_is_nonempty(dataset):
    assert len(dataset.train) > 0
    assert len(dataset.val) > 0
    assert len(dataset.test) > 0


def test_splits_are_chronological_and_non_overlapping(dataset):
    train_end = dataset.train.features[-1].moment
    val_start = dataset.val.features[0].moment
    val_end = dataset.val.features[-1].moment
    test_start = dataset.test.features[0].moment
    assert train_end < val_start
    assert val_end < test_start


def test_feature_vectors_use_the_pinned_spec(dataset):
    assert dataset.train.features[0].names == FEATURE_NAMES


def test_slot_profile_is_fit_from_train_only(dataset):
    """Total samples the profile counted across all its buckets must not
    exceed the number of training examples — if val/test data leaked in,
    this would be larger than ``len(dataset.train)``."""
    total_profile_samples = sum(stats.count for stats in dataset.slot_profile.stats.values())
    assert total_profile_samples <= len(dataset.train)


def test_too_little_history_raises_rather_than_returning_a_tiny_dataset():
    config = Config()
    pump = PumpModel.from_config(config.pump)
    with pytest.raises(ValueError):
        build_demand_dataset((), pump, config.temporal.slot_minutes, config.temporal.profile_min_samples)
