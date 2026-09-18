"""Linear regression -> random forest -> gradient boosting demand models.

Each must beat ``baselines.MeanBaseline`` on held-out MAE/RMSE before it's
trusted (see ``ROADMAP.md`` Phase 5), and the next tier must beat the
previous one — a random forest that only ties linear regression is not
worth its extra Pi inference cost.

Missing feature values arrive as ``None`` from
``ml.features.builder.build_feature_vector`` (by design — see that
module's docstring: imputation is a modelling choice deliberately left to
Phase 5, not buried in Phase 4). This is where that choice is made:
missing values are imputed with the **median of that feature in the
training set**, via a ``SimpleImputer`` fit on train only. Median, not
mean, because the features most likely to be missing early in a series —
``demand_lag_7_day``, the rolling windows — are lag/rolling statistics
that can be skewed by a single spike; a median is not moved by one.
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline

from surya_sync.ml.features.builder import FeatureVector


def _to_matrix(features: tuple[FeatureVector, ...]) -> np.ndarray:
    """``None`` -> ``np.nan``, so ``SimpleImputer`` can see the gaps."""
    return np.array(
        [[np.nan if v is None else v for v in fv.values] for fv in features],
        dtype=float,
    )


class _SklearnDemandModel:
    """Shared fit/predict plumbing for every sklearn-backed demand model.

    Not exported directly — each subclass below fixes the estimator and
    names its own version, matching how ``scheduler.algorithm_version``
    lives on the scheduler instance rather than centrally in
    ``version.py``.
    """

    model_name: str
    model_version: str

    def __init__(self, estimator) -> None:
        self._pipeline = Pipeline(
            [("impute", SimpleImputer(strategy="median")), ("estimate", estimator)]
        )

    def fit(
        self, features: tuple[FeatureVector, ...], targets: tuple[float, ...]
    ) -> "_SklearnDemandModel":
        if len(features) == 0:
            raise ValueError("cannot fit on an empty training set")
        self._pipeline.fit(_to_matrix(features), np.array(targets, dtype=float))
        return self

    def predict(self, features: tuple[FeatureVector, ...]) -> tuple[float, ...]:
        predictions = self._pipeline.predict(_to_matrix(features))
        return tuple(float(p) for p in predictions)


class LinearDemandModel(_SklearnDemandModel):
    model_name = "linear_regression"
    model_version = "demand-linear-1.0.0"

    def __init__(self) -> None:
        super().__init__(LinearRegression())


class RandomForestDemandModel(_SklearnDemandModel):
    model_name = "random_forest"
    model_version = "demand-rf-1.0.0"

    def __init__(self, n_estimators: int = 100, random_state: int = 0) -> None:
        super().__init__(
            RandomForestRegressor(n_estimators=n_estimators, random_state=random_state)
        )


class GradientBoostingDemandModel(_SklearnDemandModel):
    model_name = "gradient_boosting"
    model_version = "demand-gb-1.0.0"

    def __init__(self, n_estimators: int = 100, random_state: int = 0) -> None:
        super().__init__(
            GradientBoostingRegressor(n_estimators=n_estimators, random_state=random_state)
        )
