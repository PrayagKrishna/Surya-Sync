"""Demand baseline and models — unit level, synthetic feature vectors.

Uses hand-built ``FeatureVector`` instances rather than the full
temporal/simulator pipeline, so these tests are about the model wrappers
(imputation, fit/predict shape, versioning) and not about feature
engineering, which ``test_temporal.py`` already covers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from surya_sync.domain import Provenance
from surya_sync.ml.demand.baselines import MeanBaseline
from surya_sync.ml.demand.models import (
    GradientBoostingDemandModel,
    LinearDemandModel,
    RandomForestDemandModel,
)
from surya_sync.ml.features.builder import FEATURE_NAMES, FeatureVector
from surya_sync.version import FEATURE_SET_VERSION

MODEL_CLASSES = [LinearDemandModel, RandomForestDemandModel, GradientBoostingDemandModel]


def _vector(values: tuple[float | None, ...]) -> FeatureVector:
    assert len(values) == len(FEATURE_NAMES)
    return FeatureVector(
        moment=datetime(2026, 1, 1, tzinfo=timezone.utc),
        names=FEATURE_NAMES,
        values=values,
        feature_set_version=FEATURE_SET_VERSION,
        provenance=Provenance.SIMULATED,
    )


def _training_set(n: int) -> tuple[tuple[FeatureVector, ...], tuple[float, ...]]:
    features = tuple(_vector(tuple(float(i + j) for j in range(len(FEATURE_NAMES)))) for i in range(n))
    targets = tuple(float(i) for i in range(n))
    return features, targets


def test_mean_baseline_predicts_the_training_mean_regardless_of_input():
    features, targets = _training_set(5)  # targets 0..4, mean 2.0
    model = MeanBaseline().fit(features, targets)
    predictions = model.predict((_vector((None,) * len(FEATURE_NAMES)), _vector((99.0,) * len(FEATURE_NAMES))))
    assert predictions == (2.0, 2.0)


def test_mean_baseline_refuses_to_fit_on_nothing():
    with pytest.raises(ValueError):
        MeanBaseline().fit((), ())


def test_mean_baseline_refuses_to_predict_before_fit():
    with pytest.raises(RuntimeError):
        MeanBaseline().predict((_vector((0.0,) * len(FEATURE_NAMES)),))


@pytest.mark.parametrize("model_cls", MODEL_CLASSES)
def test_sklearn_models_fit_and_predict_the_right_shape(model_cls):
    features, targets = _training_set(20)
    model = model_cls().fit(features, targets)
    predictions = model.predict(features[:5])
    assert len(predictions) == 5
    assert all(isinstance(p, float) for p in predictions)


@pytest.mark.parametrize("model_cls", MODEL_CLASSES)
def test_sklearn_models_tolerate_missing_features_via_imputation(model_cls):
    """None entries must not reach sklearn as None/NaN-intolerant input."""
    features, targets = _training_set(20)
    model = model_cls().fit(features, targets)

    missing = _vector((None,) * len(FEATURE_NAMES))
    predictions = model.predict((missing,))
    assert len(predictions) == 1
    assert predictions[0] == predictions[0]  # not NaN


@pytest.mark.parametrize("model_cls", MODEL_CLASSES)
def test_sklearn_models_refuse_to_fit_on_nothing(model_cls):
    with pytest.raises(ValueError):
        model_cls().fit((), ())


def test_each_model_declares_a_distinct_name_and_version():
    all_models = [MeanBaseline] + MODEL_CLASSES
    names = [m.model_name for m in all_models]
    versions = [m.model_version for m in all_models]
    assert len(set(names)) == len(all_models)
    assert len(set(versions)) == len(all_models)


def test_selected_model_is_linear_regression():
    """Pinned: the author's choice, made on measured validation MAE
    (random forest edges it out by ~3%, not enough to justify traversing
    100 trees on a Pi Zero instead of one dot product — see
    ``ml.demand.SELECTED_MODEL``'s docstring). Phase 12's real inference
    cost benchmark is what would change this, not a code review."""
    from surya_sync.ml.demand import SELECTED_MODEL

    assert SELECTED_MODEL is LinearDemandModel
