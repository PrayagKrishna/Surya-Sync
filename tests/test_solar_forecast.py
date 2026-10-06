"""Phase 6: PVGIS loader, solar features, walk-forward split, baselines, models.

The synthetic series below has *persistent* weather (one cloud level per
day, drawn per day), which is the structure the real data has and the
simulator's per-slot noise does not. Nothing here is a result about real
weather; those numbers come from ``--train-solar-model``.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from surya_sync.config.schema import SolarConfig
from surya_sync.domain import Provenance, TimedValue
from surya_sync.ml.features.builder import FeatureVector, to_matrix
from surya_sync.ml.solar.baselines import (
    HistoricalProfileBaseline,
    PersistenceBaseline,
    SmartPersistenceBaseline,
)
from surya_sync.ml.solar.data import load_pvgis_hourly, require_matches_config
from surya_sync.ml.solar.dataset import (
    DAYLIGHT_FLOOR_KW,
    HEADLINE_LEADS,
    LEAD_HOURS,
    SOLAR_FEATURE_NAMES,
    PvHistory,
    build_solar_dataset,
    build_solar_features,
)
from surya_sync.ml.solar.evaluation import MethodScore, earns_its_place, score_method
from surya_sync.ml.solar.models import (
    GradientBoostingSolarModel,
    LinearSolarModel,
    RandomForestSolarModel,
)
from surya_sync.models.solar import ClearSkyModel
from surya_sync.simulator.noise import unit_noise
from surya_sync.temporal.profiles import build_slot_profile

REAL_CSV = Path(__file__).resolve().parent.parent / "data/reference/pvgis_bangalore_2020_2022.csv"
CLEAR = ClearSkyModel(pv_capacity_kw=3.0)
START = datetime(2022, 1, 1)

HEADER = """Latitude (decimal degrees):\t12.970
Longitude (decimal degrees):\t77.590
Elevation (m):\t916
Radiation database:\tPVGIS-ERA5

Nominal power of the PV system (c-Si) (kWp):\t3.0

time,P,G(i),H_sun,T2m,WS10m,Int
"""


def synthetic_series(days: int = 60) -> tuple[TimedValue, ...]:
    """Clear sky times a cloud level that holds for a whole day."""
    points = []
    for hour in range(days * 24):
        at = START + timedelta(hours=hour)
        level = 0.2 + 0.8 * unit_noise(7, at.toordinal())
        points.append(TimedValue(at=at, value=CLEAR.kw_at(at) * level, provenance=Provenance.ESTIMATED))
    return tuple(points)


# --- loader ----------------------------------------------------------------


def test_loader_converts_watts_to_kw_and_utc_to_local(tmp_path):
    csv = tmp_path / "p.csv"
    csv.write_text(HEADER + "20200101:0030,0.0,0,0,18,2,0\n20200101:0630,1500.0,0,0,18,2,0\n\nP: footer\n")
    meta, series = load_pvgis_hourly(csv)
    assert (meta.latitude, meta.longitude, meta.peak_power_kw, meta.database) == (
        12.97, 77.59, 3.0, "PVGIS-ERA5"
    )
    assert [p.at for p in series] == [datetime(2020, 1, 1, 6, 0), datetime(2020, 1, 1, 12, 0)]
    assert series[1].value == pytest.approx(1.5)
    assert all(p.provenance is Provenance.ESTIMATED for p in series)


def test_loader_rejects_empty_unordered_and_headerless_files(tmp_path):
    empty = tmp_path / "e.csv"
    empty.write_text(HEADER)
    with pytest.raises(ValueError, match="no PVGIS data rows"):
        load_pvgis_hourly(empty)

    backwards = tmp_path / "b.csv"
    backwards.write_text(HEADER + "20200101:0630,1.0,0,0,0,0,0\n20200101:0530,1.0,0,0,0,0,0\n")
    with pytest.raises(ValueError, match="strictly increasing"):
        load_pvgis_hourly(backwards)

    headerless = tmp_path / "h.csv"
    headerless.write_text("20200101:0030,1.0,0,0,0,0,0\n")
    with pytest.raises(ValueError, match="Latitude"):
        load_pvgis_hourly(headerless)


def test_the_committed_data_file_loads_and_matches_the_default_config():
    meta, series = load_pvgis_hourly(REAL_CSV)
    assert len(series) == 26304 and series[0].at.year == 2020
    require_matches_config(meta, SolarConfig())


@pytest.mark.parametrize("kwargs", [{"pv_capacity_kw": 5.0}, {"latitude": 28.6, "longitude": 77.2}])
def test_data_for_a_different_site_or_array_is_refused(kwargs):
    meta, _ = load_pvgis_hourly(REAL_CSV)
    with pytest.raises(ValueError, match="does not match config"):
        require_matches_config(meta, SolarConfig(**kwargs))


# --- features --------------------------------------------------------------


def test_feature_vector_has_the_pinned_names_in_order():
    assert SOLAR_FEATURE_NAMES == (
        "lead_hours", "clear_sky_target_kw", "clear_sky_now_kw", "observed_now_kw", "ksi_now",
        "smart_persistence_kw", "ksi_mean_3h", "ksi_mean_24h", "ksi_yesterday_at_target",
        "tod_sin", "tod_cos", "doy_sin", "doy_cos",
    )
    series = synthetic_series(10)
    fv = build_solar_features(START + timedelta(days=5, hours=9), 3, PvHistory(series, CLEAR), CLEAR,
                              Provenance.ESTIMATED)
    assert fv.names == SOLAR_FEATURE_NAMES and fv.provenance is Provenance.ESTIMATED


@pytest.mark.parametrize("lead", [1, 6, 12, 24])
def test_no_feature_ever_reads_the_future(lead):
    """Rewrite every reading after the issue time; the vector must not move."""
    series = synthetic_series(20)
    issue = START + timedelta(days=10, hours=9)
    wrecked = tuple(
        TimedValue(at=p.at, value=p.value + 99.0 if p.at > issue else p.value, provenance=p.provenance)
        for p in series
    )
    a = build_solar_features(issue, lead, PvHistory(series, CLEAR), CLEAR, Provenance.ESTIMATED)
    b = build_solar_features(issue, lead, PvHistory(wrecked, CLEAR), CLEAR, Provenance.ESTIMATED)
    assert a.values == b.values


@pytest.mark.parametrize("lead", [0, -1, 25])
def test_a_lead_outside_zero_to_24_hours_is_rejected(lead):
    series = synthetic_series(5)
    with pytest.raises(ValueError, match="lead_hours"):
        build_solar_features(START + timedelta(days=2), lead, PvHistory(series, CLEAR), CLEAR,
                             Provenance.ESTIMATED)


def test_ksi_is_unknown_at_night_not_zero():
    series = synthetic_series(5)
    history = PvHistory(series, CLEAR)
    assert history.ksi(START + timedelta(days=2, hours=1)) is None
    assert history.ksi(START + timedelta(days=2, hours=12)) is not None


# --- dataset / walk-forward ------------------------------------------------


def _target_time(fv: FeatureVector) -> datetime:
    return fv.moment + timedelta(hours=fv.as_dict()["lead_hours"])


def test_split_is_chronological_by_target_time_and_daylight_only():
    ds = build_solar_dataset(synthetic_series(), CLEAR)
    spans = [(min(map(_target_time, s.features)), max(map(_target_time, s.features)))
             for s in (ds.train, ds.val, ds.test)]
    assert spans[0][1] < spans[1][0] and spans[1][1] < spans[2][0]
    every = ds.train.features + ds.val.features + ds.test.features
    assert all(fv.as_dict()["clear_sky_target_kw"] > DAYLIGHT_FLOOR_KW for fv in every)
    assert {int(fv.as_dict()["lead_hours"]) for fv in every} == set(LEAD_HOURS)


def test_the_historical_profile_never_sees_validation_or_test_days():
    series = synthetic_series()
    cut = int(len(series) * 0.85)  # everything after the train+val boundary
    altered = series[:cut] + tuple(
        TimedValue(at=p.at, value=p.value + 50.0, provenance=p.provenance) for p in series[cut:]
    )
    a = build_solar_dataset(series, CLEAR).slot_profile
    b = build_solar_dataset(altered, CLEAR).slot_profile
    assert a.stats == b.stats


def test_a_series_too_short_to_split_is_refused():
    with pytest.raises(ValueError, match="too little"):
        build_solar_dataset(synthetic_series(10), CLEAR)


# --- baselines -------------------------------------------------------------


def _fv(moment=datetime(2022, 3, 3, 9), **overrides) -> FeatureVector:
    values = {name: 0.0 for name in SOLAR_FEATURE_NAMES}
    values.update(lead_hours=3.0, clear_sky_target_kw=2.0, observed_now_kw=1.0, ksi_now=0.5,
                  smart_persistence_kw=1.0)
    values.update(overrides)
    return FeatureVector(moment=moment, names=SOLAR_FEATURE_NAMES,
                         values=tuple(values[n] for n in SOLAR_FEATURE_NAMES),
                         feature_set_version="t", provenance=Provenance.ESTIMATED)


def test_persistence_holds_the_current_reading_and_refuses_to_guess():
    assert PersistenceBaseline().predict((_fv(observed_now_kw=1.7),)) == (1.7,)
    with pytest.raises(ValueError, match="observed reading"):
        PersistenceBaseline().predict((_fv(observed_now_kw=None),))


def test_smart_persistence_scales_the_clear_sky_target_by_the_current_index():
    assert SmartPersistenceBaseline().predict((_fv(smart_persistence_kw=0.8),)) == (0.8,)
    # unknown index -> the clear-sky curve itself, i.e. ksi = 1
    assert SmartPersistenceBaseline().predict(
        (_fv(smart_persistence_kw=None, clear_sky_target_kw=2.5),)) == (2.5,)


def test_historical_profile_reads_the_hour_of_the_target_not_the_issue_time():
    samples = tuple(
        TimedValue(at=datetime(2022, 1, day, 12), value=1.25, provenance=Provenance.ESTIMATED)
        for day in range(1, 11)
    )
    baseline = HistoricalProfileBaseline(build_slot_profile(samples, 60.0, 5))
    issue_09 = datetime(2022, 1, 5, 9)  # +3 h -> 12:00, which has history
    assert baseline.predict((_fv(moment=issue_09, lead_hours=3.0),)) == (1.25,)
    # +1 h -> 10:00, no history -> falls back to the clear-sky curve value
    assert baseline.predict((_fv(moment=issue_09, lead_hours=1.0, clear_sky_target_kw=2.0),)) == (2.0,)


def test_historical_profile_needs_an_hourly_grid():
    with pytest.raises(ValueError, match="hourly"):
        HistoricalProfileBaseline(build_slot_profile((), 30.0, 1))


# --- models ----------------------------------------------------------------


def test_to_matrix_turns_missing_values_into_nan():
    matrix = to_matrix((_fv(ksi_now=None),))
    assert math.isnan(matrix[0, SOLAR_FEATURE_NAMES.index("ksi_now")])


@pytest.mark.parametrize("cls", [LinearSolarModel, RandomForestSolarModel, GradientBoostingSolarModel])
def test_each_tier_fits_predicts_nonnegative_and_survives_missing_features(cls):
    ds = build_solar_dataset(synthetic_series(), CLEAR)
    model = cls().fit(ds.train.features, ds.train.targets)
    holes = tuple(_fv(ksi_now=None, ksi_mean_3h=None, observed_now_kw=None) for _ in range(3))
    predictions = model.predict(ds.test.features) + model.predict(holes)
    assert len(predictions) == len(ds.test) + 3 and min(predictions) >= 0.0


def test_with_day_long_weather_the_model_beats_the_profile_at_short_leads():
    """The synthetic day-long cloud level is exactly what a profile cannot see.
    At 1 h smart persistence is nearly exact on this data, and a per-lead model
    must at least clearly beat the profile there."""
    ds = build_solar_dataset(synthetic_series(), CLEAR)
    profile = score_method(HistoricalProfileBaseline(ds.slot_profile), ds)
    smart = score_method(SmartPersistenceBaseline(), ds)
    linear = score_method(LinearSolarModel().fit(ds.train.features, ds.train.targets), ds)
    assert smart.test_mae_by_lead[1] < 0.05
    assert linear.test_mae_by_lead[1] < 0.5 * profile.test_mae_by_lead[1]


def test_the_model_forecasts_exactly_zero_when_the_sun_is_down():
    ds = build_solar_dataset(synthetic_series(), CLEAR)
    model = LinearSolarModel().fit(ds.train.features, ds.train.targets)
    assert model.predict((_fv(clear_sky_target_kw=0.0),)) == (0.0,)
    assert model.predict((_fv(clear_sky_target_kw=DAYLIGHT_FLOOR_KW),)) == (0.0,)


def test_a_lead_the_model_was_not_trained_on_is_refused_not_guessed():
    ds = build_solar_dataset(synthetic_series(), CLEAR, leads=(1, 2))
    model = LinearSolarModel().fit(ds.train.features, ds.train.targets)
    with pytest.raises(ValueError, match="lead 5 h"):
        model.predict((_fv(lead_hours=5.0),))


def test_training_on_a_night_target_is_refused():
    ds = build_solar_dataset(synthetic_series(), CLEAR)
    with pytest.raises(ValueError, match="night is geometry"):
        LinearSolarModel().fit((_fv(clear_sky_target_kw=0.0),), (0.0,))


def test_predicting_before_fitting_raises():
    with pytest.raises(RuntimeError, match="before fit"):
        LinearSolarModel().predict((_fv(),))


def test_scores_are_reported_for_every_lead():
    ds = build_solar_dataset(synthetic_series(), CLEAR)
    score = score_method(HistoricalProfileBaseline(ds.slot_profile), ds)
    assert set(score.val_mae_by_lead) == set(LEAD_HOURS) == set(score.test_mae_by_lead)


# --- the keep-ML rule ------------------------------------------------------


def _score(name, val, test, by_lead=None):
    by_lead = by_lead or {lead: val for lead in LEAD_HOURS}
    return MethodScore(name, val, val, test, test, by_lead, by_lead)


def test_ml_is_kept_only_if_it_beats_the_best_baseline_at_every_headline_lead_and_on_test():
    baselines = [_score("a", 0.5, 0.5), _score("b", 0.4, 0.4)]
    assert earns_its_place(_score("ml", 0.3, 0.3), baselines) == (True, [])

    one_bad_lead = {lead: 0.3 for lead in LEAD_HOURS} | {HEADLINE_LEADS[-1]: 0.45}
    ok, why = earns_its_place(_score("ml", 0.3, 0.3, one_bad_lead), baselines)
    assert not ok and len(why) == 1 and f"lead {HEADLINE_LEADS[-1]}h" in why[0]

    ok, why = earns_its_place(_score("ml", 0.3, 0.45), baselines)
    assert not ok and "test overall" in why[0]


def test_best_baseline_is_chosen_per_lead_not_overall():
    good_early = _score("early", 0.9, 0.9, {lead: (0.1 if lead <= 3 else 0.9) for lead in LEAD_HOURS})
    good_late = _score("late", 0.9, 0.9, {lead: (0.9 if lead <= 3 else 0.1) for lead in LEAD_HOURS})
    ml = _score("ml", 0.2, 0.2)  # beats both overall, but not each at its own lead
    ok, why = earns_its_place(ml, [good_early, good_late])
    assert not ok and len(why) >= 2


# --- CLI -------------------------------------------------------------------


def test_the_cli_refuses_missing_or_mismatched_solar_data(tmp_path, capsys):
    from surya_sync.main import main

    assert main(["--train-solar-model", "--solar-data", str(tmp_path / "nope.csv")]) == 2
    other_site = tmp_path / "x.csv"
    other_site.write_text(HEADER.replace("12.970", "28.600") + "20200101:0030,0,0,0,0,0,0\n")
    assert main(["--train-solar-model", "--solar-data", str(other_site)]) == 2
    assert "does not match config" in capsys.readouterr().err
