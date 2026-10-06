"""Persistence and historical-profile solar baselines. ML must beat all three.

Hard rule (``CLAUDE.md``): the solar forecast must beat persistence and the
historical profile before it is trusted. Three baselines, not two, because
plain persistence alone is a strawman — "the sun now, held forever" is
trivially wrong at any lead that crosses dusk. The clear-sky-index version
is the standard strong baseline in the solar literature and is the real bar
to clear.

All three share one shape with the demand models: ``fit(features, targets)``
and ``predict(features) -> kW``. None is fit on targets here; the
historical profile reads the train-only ``SlotProfile`` it is built with.
A missing input never silently becomes a zero forecast: persistence raises,
the other two name their fallback.
"""

from __future__ import annotations

from datetime import timedelta

from surya_sync.ml.features.builder import FeatureVector
from surya_sync.temporal.profiles import SlotProfile


class _Baseline:
    model_name: str
    model_version: str

    def fit(self, features: tuple[FeatureVector, ...], targets: tuple[float, ...]):
        return self  # nothing to learn

    def predict(self, features: tuple[FeatureVector, ...]) -> tuple[float, ...]:
        return tuple(self._one(fv) for fv in features)

    def _one(self, fv: FeatureVector) -> float:
        raise NotImplementedError


class PersistenceBaseline(_Baseline):
    """The output right now, held for the whole lead. Raises without a
    reading; the dataset never builds an example without one."""

    model_name = "persistence"
    model_version = "solar-persistence-1.0.0"

    def _one(self, fv: FeatureVector) -> float:
        now = fv.as_dict()["observed_now_kw"]
        if now is None:
            raise ValueError("persistence needs an observed reading at issue time")
        return now


class SmartPersistenceBaseline(_Baseline):
    """Hold the clear-sky *index* instead of the output: the cloud state is
    assumed to persist while the sun keeps moving. Falls back to the
    clear-sky curve itself (ksi = 1) when ksi is unknown."""

    model_name = "smart_persistence"
    model_version = "solar-smart-persistence-1.0.0"

    def _one(self, fv: FeatureVector) -> float:
        values = fv.as_dict()
        smart = values["smart_persistence_kw"]
        return values["clear_sky_target_kw"] if smart is None else smart


class HistoricalProfileBaseline(_Baseline):
    """Mean output at the target's hour of day over the training period.

    Takes the train-only ``SlotProfile`` at construction, so it cannot see
    validation or test days. Falls back to the clear-sky curve for an hour
    with too few training samples to be a profile.
    """

    model_name = "historical_profile"
    model_version = "solar-profile-1.0.0"

    def __init__(self, profile: SlotProfile) -> None:
        if profile.slot_minutes != 60.0:
            raise ValueError("solar profile must be on an hourly grid")
        self._profile = profile

    def _one(self, fv: FeatureVector) -> float:
        values = fv.as_dict()
        target_time = fv.moment + timedelta(hours=values["lead_hours"])
        mean = self._profile.mean_for(target_time)
        return values["clear_sky_target_kw"] if mean is None else mean
