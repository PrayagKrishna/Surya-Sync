"""Load, validate and hash configuration.

TOML via the standard library's ``tomllib`` — no third-party dependency,
which matters on a Pi Zero.

Unknown keys are a hard error rather than a warning: a typo in a physical
parameter that silently falls back to a default would invalidate every
experiment run under it.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from surya_sync.config.schema import (
    Config,
    ConfigError,
    HardwareConfig,
    LoggingConfig,
    PumpConfig,
    SchedulerConfig,
    SolarConfig,
    StorageConfig,
    SystemConfig,
    TankConfig,
    TemporalConfig,
    to_dict,
)
from surya_sync.domain import RunMode

DEFAULT_CONFIG_PATH = Path(__file__).parent / "default.toml"

_SECTIONS: dict[str, type] = {
    "system": SystemConfig,
    "tank": TankConfig,
    "pump": PumpConfig,
    "solar": SolarConfig,
    "scheduler": SchedulerConfig,
    "hardware": HardwareConfig,
    "storage": StorageConfig,
    "temporal": TemporalConfig,
    "logging": LoggingConfig,
}


def load_config(path: str | Path | None = None) -> Config:
    """Read and validate a config file.

    ``path=None`` loads the packaged defaults, which is what tests and a
    fresh simulator run use.
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    return build_config(raw, source=str(config_path))


def build_config(raw: dict[str, Any], source: str = "<dict>") -> Config:
    """Build a validated ``Config`` from a plain dict."""
    unknown_sections = set(raw) - set(_SECTIONS)
    if unknown_sections:
        raise ConfigError(
            f"unknown config section(s) in {source}: {sorted(unknown_sections)}"
        )

    sections: dict[str, Any] = {}
    for name, section_type in _SECTIONS.items():
        values = raw.get(name, {})
        if not isinstance(values, dict):
            raise ConfigError(f"config section [{name}] in {source} must be a table")
        sections[name] = _build_section(section_type, name, values, source)

    return Config(**sections)


def _build_section(
    section_type: type, name: str, values: dict[str, Any], source: str
) -> Any:
    """Instantiate one config dataclass, rejecting unknown keys."""
    if not is_dataclass(section_type):
        raise ConfigError(f"internal: {section_type!r} is not a dataclass")

    known = {f.name for f in fields(section_type)}
    unknown = set(values) - known
    if unknown:
        raise ConfigError(
            f"unknown key(s) in [{name}] of {source}: {sorted(unknown)} "
            f"(known keys: {sorted(known)})"
        )

    coerced = dict(values)
    if section_type is SystemConfig and "mode" in coerced:
        coerced["mode"] = _parse_run_mode(coerced["mode"])

    try:
        return section_type(**coerced)
    except ConfigError:
        raise
    except TypeError as exc:
        raise ConfigError(f"invalid value in [{name}] of {source}: {exc}") from exc


def _parse_run_mode(value: Any) -> RunMode:
    try:
        return RunMode(value)
    except ValueError as exc:
        valid = [m.value for m in RunMode]
        raise ConfigError(f"system.mode must be one of {valid}, got {value!r}") from exc


def config_hash(config: Config) -> str:
    """Stable hash of a resolved config.

    Recorded with every run so results can be traced to the exact
    parameters that produced them. Key order is normalized, so two configs
    that differ only in file layout hash identically.
    """
    payload = json.dumps(to_dict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
