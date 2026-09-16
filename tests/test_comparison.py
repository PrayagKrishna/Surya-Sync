"""``analytics/comparison.py`` — pairing runs, never conflating them."""

from __future__ import annotations

import pytest

from surya_sync.analytics.comparison import aggregate_grid_kwh, compare_grid_energy
from surya_sync.config.schema import Config
from surya_sync.experiments.runner import run_scenario
from surya_sync.scheduler.reactive import ReactiveScheduler
from surya_sync.scheduler.threshold import ThresholdScheduler
from surya_sync.simulator import scenarios


@pytest.fixture
def config() -> Config:
    return Config()


def test_mismatched_scenario_sets_refuse_to_compare(config):
    baseline = (run_scenario(config, scenarios.cloudy(config), ThresholdScheduler(config.scheduler)),)
    candidate = (
        run_scenario(
            config,
            scenarios.sunny(config),
            ReactiveScheduler(config.scheduler, config.solar.surplus_threshold_kw),
        ),
    )
    with pytest.raises(ValueError):
        compare_grid_energy(baseline, candidate)


def test_grid_kwh_saved_is_baseline_minus_candidate(config):
    scenario = scenarios.cloudy(config)
    baseline = (run_scenario(config, scenario, ThresholdScheduler(config.scheduler)),)
    candidate = (
        run_scenario(
            config,
            scenario,
            ReactiveScheduler(config.scheduler, config.solar.surplus_threshold_kw),
        ),
    )
    rows = compare_grid_energy(baseline, candidate)
    assert len(rows) == 1
    row = rows[0]
    assert row.grid_kwh_saved == pytest.approx(
        row.baseline_grid_kwh - row.candidate_grid_kwh
    )
    assert row.beats_baseline == (row.candidate_grid_kwh < row.baseline_grid_kwh)


def test_aggregate_sums_every_row(config):
    scenario_set = scenarios.standard_set(config)
    baseline = tuple(
        run_scenario(config, s, ThresholdScheduler(config.scheduler)) for s in scenario_set
    )
    candidate = tuple(
        run_scenario(
            config, s, ReactiveScheduler(config.scheduler, config.solar.surplus_threshold_kw)
        )
        for s in scenario_set
    )
    rows = compare_grid_energy(baseline, candidate)
    baseline_total, candidate_total = aggregate_grid_kwh(rows)
    assert baseline_total == pytest.approx(sum(r.baseline_grid_kwh for r in rows))
    assert candidate_total == pytest.approx(sum(r.candidate_grid_kwh for r in rows))
