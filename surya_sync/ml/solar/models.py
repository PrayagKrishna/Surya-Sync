"""Linear regression -> random forest -> gradient boosting solar models.

Same tiering rule as demand: each must beat every baseline on held-out
error to be kept, and a tier that does not beat the previous one is not
worth its extra Pi inference cost. Missing features arrive as ``None`` and
are median-imputed by a ``SimpleImputer`` fit on train only.

Design, chosen on **validation** error only (test was not consulted), by
trying four shapes on the same data, validation MAE in kW, all leads
pooled:

====================================  ======  =========
shape                                 linear  grad boost
====================================  ======  =========
one pooled model, predict kW          0.167   0.131
one model per lead, predict kW        0.152   0.125
one pooled model, predict ksi         0.133   0.126
**one model per lead, predict ksi**   0.128   0.122
====================================  ======  =========

Two ideas, both physical. *Predict the clear-sky index, not kW*: the sun's
daily and seasonal motion is geometry we already know exactly, so the model
only has to learn the weather, and kW is recovered by multiplying back by
the clear-sky output at the target. *One model per lead*: how far to trust
"it is cloudy right now" depends on how far ahead you look (almost fully at
1 h, barely at 12 h), and a single pooled linear model has one coefficient
for all leads, so it cannot express that. The per-lead linear model's 1 h
error is 0.090 kW against 0.133 pooled. Weighting the fit toward high-sun
hours was also tried and did not help.

Linear per-lead lands within 5% of gradient boosting here, at 14 dot
products against hundreds of tree traversals per lead: that is the point
for a Raspberry Pi Zero.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline

from surya_sync.ml.features.builder import FeatureVector, to_matrix
from surya_sync.ml.solar.dataset import DAYLIGHT_FLOOR_KW


class _PerLeadKsiModel:
    model_name: str
    model_version: str

    def __init__(self) -> None:
        self._by_lead: dict[int, Pipeline] = {}

    def _make_estimator(self):
        raise NotImplementedError

    @staticmethod
    def _lead_and_clear(fv: FeatureVector) -> tuple[int, float]:
        values = fv.as_dict()
        return int(values["lead_hours"]), values["clear_sky_target_kw"]

    def fit(
        self, features: tuple[FeatureVector, ...], targets: tuple[float, ...]
    ) -> "_PerLeadKsiModel":
        if len(features) == 0:
            raise ValueError("cannot fit on an empty training set")
        rows: dict[int, list[int]] = defaultdict(list)
        for index, fv in enumerate(features):
            lead, clear = self._lead_and_clear(fv)
            if clear <= DAYLIGHT_FLOOR_KW:
                raise ValueError(
                    f"training example at lead {lead} h has clear-sky output "
                    f"{clear:.3f} kW at its target; night is geometry, not data"
                )
            rows[lead].append(index)

        self._by_lead = {}
        for lead, indices in rows.items():
            subset = tuple(features[i] for i in indices)
            ksi = np.array([targets[i] / self._lead_and_clear(features[i])[1] for i in indices])
            pipeline = Pipeline(
                [
                    # keep_empty_features: at some leads a feature is *never*
                    # observed (e.g. no daylight reading exists 12 h before a
                    # midday target). Keep it as a constant so the column
                    # layout cannot shift between fit and predict.
                    ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                    ("estimate", self._make_estimator()),
                ]
            )
            self._by_lead[lead] = pipeline.fit(to_matrix(subset), ksi)
        return self

    def predict(self, features: tuple[FeatureVector, ...]) -> tuple[float, ...]:
        """kW, floored at zero. At or below ``DAYLIGHT_FLOOR_KW`` of
        clear-sky output the answer is exactly 0.0: the sun is down."""
        if not self._by_lead:
            raise RuntimeError(f"{type(self).__name__}.predict called before fit")
        out = [0.0] * len(features)
        pending: dict[int, list[int]] = defaultdict(list)
        for index, fv in enumerate(features):
            lead, clear = self._lead_and_clear(fv)
            if clear <= DAYLIGHT_FLOOR_KW:
                continue
            if lead not in self._by_lead:
                raise ValueError(
                    f"no model was trained for lead {lead} h "
                    f"(trained: {sorted(self._by_lead)})"
                )
            pending[lead].append(index)
        for lead, indices in pending.items():
            ksi = self._by_lead[lead].predict(to_matrix(tuple(features[i] for i in indices)))
            for index, k in zip(indices, ksi):
                out[index] = max(0.0, float(k)) * self._lead_and_clear(features[index])[1]
        return tuple(out)


class LinearSolarModel(_PerLeadKsiModel):
    model_name = "linear_regression"
    model_version = "solar-linear-2.0.0"

    def _make_estimator(self):
        return LinearRegression()


class RandomForestSolarModel(_PerLeadKsiModel):
    model_name = "random_forest"
    model_version = "solar-rf-2.0.0"

    def __init__(self, n_estimators: int = 100, random_state: int = 0) -> None:
        super().__init__()
        self._n_estimators = n_estimators
        self._random_state = random_state

    def _make_estimator(self):
        return RandomForestRegressor(
            n_estimators=self._n_estimators,
            min_samples_leaf=20,
            random_state=self._random_state,
            n_jobs=-1,
        )


class GradientBoostingSolarModel(_PerLeadKsiModel):
    model_name = "gradient_boosting"
    model_version = "solar-gb-2.0.0"

    def __init__(self, n_estimators: int = 100, random_state: int = 0) -> None:
        super().__init__()
        self._n_estimators = n_estimators
        self._random_state = random_state

    def _make_estimator(self):
        return GradientBoostingRegressor(
            n_estimators=self._n_estimators, random_state=self._random_state
        )
