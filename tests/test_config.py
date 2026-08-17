"""Config loading and validation — Phase 0 exit criterion: `config/` loads."""

from __future__ import annotations

import pytest

from surya_sync.config.loader import (
    DEFAULT_CONFIG_PATH,
    build_config,
    config_hash,
    load_config,
)
from surya_sync.config.schema import Config, ConfigError
from surya_sync.domain import RunMode


def test_packaged_defaults_load():
    config = load_config()
    assert isinstance(config, Config)
    assert config.system.mode is RunMode.SIMULATED
    assert config.tank.capacity_l > 0


def test_default_toml_is_shipped_with_the_package():
    assert DEFAULT_CONFIG_PATH.is_file()


def test_missing_file_is_fatal():
    with pytest.raises(ConfigError, match="not found"):
        load_config("does/not/exist.toml")


def test_unknown_section_rejected():
    with pytest.raises(ConfigError, match="unknown config section"):
        build_config({"tank": {}, "nonsense": {}})


def test_unknown_key_rejected():
    """A typo'd physical parameter must not silently fall back to a
    default — every experiment run under it would be invalid."""
    with pytest.raises(ConfigError, match="unknown key"):
        build_config({"tank": {"capcity_l": 500.0}})


def test_partial_config_uses_defaults_for_absent_sections():
    config = build_config({"tank": {"capacity_l": 500.0}})
    assert config.tank.capacity_l == 500.0
    assert config.pump.rated_power_kw == 0.75


def test_invalid_run_mode_rejected():
    with pytest.raises(ConfigError, match="system.mode"):
        build_config({"system": {"mode": "turbo"}})


def test_run_mode_parsed_to_enum():
    config = build_config({"system": {"mode": "real"}})
    assert config.system.mode is RunMode.REAL


@pytest.mark.parametrize(
    "section,values,match",
    [
        ("tank", {"capacity_l": -1.0}, "must be > 0"),
        ("tank", {"critical_level": 0.9}, "critical_level < min_level"),
        ("tank", {"min_level": 0.99}, "critical_level < min_level"),
        ("pump", {"flow_rate_lpm": 0.0}, "must be > 0"),
        ("pump", {"max_starts_per_day": 0}, "must be > 0"),
        ("solar", {"latitude": 120.0}, "latitude"),
        ("scheduler", {"step_minutes": 0.0}, "must be > 0"),
        ("scheduler", {"horizon_minutes": 5.0}, "step_minutes"),
        ("scheduler", {"safety_reserve_level": 1.5}, "safety_reserve_level"),
        ("hardware", {"max_missed_telemetry": 0}, "must be > 0"),
        ("storage", {"database_path": ""}, "must not be empty"),
        ("logging", {"level": "CHATTY"}, "logging.level"),
    ],
)
def test_section_validation(section, values, match):
    with pytest.raises(ConfigError, match=match):
        build_config({section: values})


def test_ordering_of_tank_levels_is_enforced():
    """critical < min < max is a hard project invariant, not a preference."""
    with pytest.raises(ConfigError):
        build_config(
            {"tank": {"critical_level": 0.5, "min_level": 0.4, "max_level": 0.9}}
        )


def test_cross_section_validation_pump_run_must_fit_horizon():
    with pytest.raises(ConfigError, match="min_on_minutes exceeds"):
        build_config(
            {
                "pump": {"min_on_minutes": 900.0},
                "scheduler": {"horizon_minutes": 720.0, "step_minutes": 15.0},
            }
        )


def test_cross_section_validation_reserve_must_leave_operating_band():
    with pytest.raises(ConfigError, match="leaves no operating band"):
        build_config(
            {
                "tank": {"min_level": 0.80, "max_level": 0.85},
                "scheduler": {"safety_reserve_level": 0.10},
            }
        )


def test_config_hash_is_stable_and_sensitive():
    a = load_config()
    b = load_config()
    assert config_hash(a) == config_hash(b)

    changed = build_config({"tank": {"capacity_l": 999.0}})
    assert config_hash(changed) != config_hash(a)


def test_config_is_frozen():
    """Config must not drift mid-run, or the recorded hash would lie."""
    config = load_config()
    with pytest.raises(Exception):
        config.tank.capacity_l = 1.0  # type: ignore[misc]
