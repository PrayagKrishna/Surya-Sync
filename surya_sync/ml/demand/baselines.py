"""Mean demand baseline. MANDATORY before any ML — nothing in ``models.py``
is trusted until it beats this.

A model that cannot beat "predict the training mean, always" adds
complexity for nothing. This is deliberately the dumbest possible
predictor: it ignores every feature, including the time of day.
"""

from __future__ import annotations

from surya_sync.ml.features.builder import FeatureVector


class MeanBaseline:
    """Predicts the training-set mean demand for every input, unconditionally."""

    model_name = "mean_baseline"
    model_version = "demand-mean-1.0.0"

    def __init__(self) -> None:
        self._mean: float | None = None

    def fit(self, features: tuple[FeatureVector, ...], targets: tuple[float, ...]) -> MeanBaseline:
        if len(targets) == 0:
            raise ValueError("cannot fit on an empty training set")
        self._mean = sum(targets) / len(targets)
        return self

    def predict(self, features: tuple[FeatureVector, ...]) -> tuple[float, ...]:
        if self._mean is None:
            raise RuntimeError("MeanBaseline.predict called before fit")
        return tuple(self._mean for _ in features)
