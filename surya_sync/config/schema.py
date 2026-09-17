"""Typed configuration schema.

Plain dataclasses, validated at load time. Every physical parameter that
affects a result lives here rather than in code, so an experiment is
reproducible from its config hash alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, TypeVar

from surya_sync.domain import RunMode


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed or physically
    impossible. Always fatal — SuryaSync never starts on a config it
    cannot fully validate."""


@dataclass(frozen=True, slots=True)
class SystemConfig:
    mode: RunMode = RunMode.SIMULATED
    timezone: str = "Asia/Kolkata"
    control_interval_seconds: int = 60
    """How often the control loop runs. MPC re-plans every cycle."""

    def __post_init__(self) -> None:
        if self.control_interval_seconds <= 0:
            raise ConfigError("system.control_interval_seconds must be > 0")


@dataclass(frozen=True, slots=True)
class TankConfig:
    """Physical geometry and hard service levels of the tank.

    The levels are fractions of usable volume, not litres, so the same
    numbers carry over to any other ``FlexibleResource``.
    """

    capacity_l: float = 1000.0
    height_mm: float = 1200.0
    sensor_offset_mm: float = 100.0
    """Distance from the sensor face to the full-water surface."""

    critical_level: float = 0.20
    min_level: float = 0.30
    max_level: float = 0.95

    def __post_init__(self) -> None:
        if self.capacity_l <= 0 or self.height_mm <= 0:
            raise ConfigError("tank.capacity_l and tank.height_mm must be > 0")
        if self.sensor_offset_mm < 0:
            raise ConfigError("tank.sensor_offset_mm must be >= 0")
        if not 0.0 <= self.critical_level < self.min_level < self.max_level <= 1.0:
            raise ConfigError(
                "require 0 <= critical_level < min_level < max_level <= 1, got "
                f"{self.critical_level} / {self.min_level} / {self.max_level}"
            )


@dataclass(frozen=True, slots=True)
class PumpConfig:
    rated_power_kw: float = 0.75
    flow_rate_lpm: float = 30.0
    min_on_minutes: float = 5.0
    min_off_minutes: float = 10.0
    max_starts_per_day: int = 12
    max_runtime_seconds: int = 1800
    """Deadman timer enforced on the ESP32, independent of the Pi."""

    def __post_init__(self) -> None:
        if self.rated_power_kw <= 0 or self.flow_rate_lpm <= 0:
            raise ConfigError("pump.rated_power_kw and pump.flow_rate_lpm must be > 0")
        if self.min_on_minutes < 0 or self.min_off_minutes < 0:
            raise ConfigError("pump min_on/min_off minutes must be >= 0")
        if self.max_starts_per_day <= 0:
            raise ConfigError("pump.max_starts_per_day must be > 0")


@dataclass(frozen=True, slots=True)
class SolarConfig:
    pv_capacity_kw: float = 3.0
    latitude: float = 12.97
    longitude: float = 77.59
    surplus_threshold_kw: float = 0.1
    """Surplus below this is treated as zero — sensor noise, not energy."""

    def __post_init__(self) -> None:
        if self.pv_capacity_kw <= 0:
            raise ConfigError("solar.pv_capacity_kw must be > 0")
        if not -90.0 <= self.latitude <= 90.0:
            raise ConfigError("solar.latitude out of range")
        if not -180.0 <= self.longitude <= 180.0:
            raise ConfigError("solar.longitude out of range")


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    active: str = "threshold"
    """Highest tier to attempt. Lower tiers remain available as fallback."""

    horizon_minutes: float = 720.0
    step_minutes: float = 15.0
    solver_time_limit_seconds: float = 5.0
    """Exceeding this is a ``TIMEOUT``, which degrades to the next tier
    rather than shipping a half-solved plan."""

    safety_reserve_level: float = 0.05
    """Static margin above ``min_level``. Phase 10 makes this dynamic,
    scaling it with demand-forecast uncertainty."""

    def __post_init__(self) -> None:
        if self.step_minutes <= 0 or self.horizon_minutes <= 0:
            raise ConfigError("scheduler horizon and step must be > 0")
        if self.horizon_minutes < self.step_minutes:
            raise ConfigError("scheduler.horizon_minutes must be >= step_minutes")
        if not 0.0 <= self.safety_reserve_level < 1.0:
            raise ConfigError("scheduler.safety_reserve_level must be in [0, 1)")


@dataclass(frozen=True, slots=True)
class HardwareConfig:
    serial_port: str = "/dev/ttyUSB0"
    baud_rate: int = 115200
    read_timeout_seconds: float = 2.0
    heartbeat_interval_seconds: float = 5.0
    max_missed_telemetry: int = 5
    """Consecutive misses before the link is declared failed and the
    fallback chain degrades to local safe mode."""

    def __post_init__(self) -> None:
        if self.baud_rate <= 0:
            raise ConfigError("hardware.baud_rate must be > 0")
        if self.max_missed_telemetry <= 0:
            raise ConfigError("hardware.max_missed_telemetry must be > 0")


@dataclass(frozen=True, slots=True)
class StorageConfig:
    database_path: str = "data/surya_sync.db"
    retain_telemetry_days: int = 365
    """Long retention on purpose: temporal learning needs history, and a
    year covers seasonal variation in both demand and solar."""

    def __post_init__(self) -> None:
        if not self.database_path:
            raise ConfigError("storage.database_path must not be empty")
        if self.retain_telemetry_days <= 0:
            raise ConfigError("storage.retain_telemetry_days must be > 0")


@dataclass(frozen=True, slots=True)
class TemporalConfig:
    """Knobs for ``temporal/`` and ``ml.features.builder``.

    Does not change the feature vector's *shape* — ``FEATURE_NAMES`` is
    code, not config, because a model trained on one ordering cannot be
    fed another. This only tunes how the values are computed.
    """

    slot_minutes: float = 15.0
    """Must divide 1440 evenly — see ``temporal.context.slots_per_day``."""

    profile_min_samples: int = 3
    """Below this many samples, a slot's historical mean reports unknown
    rather than an echo of one or two days."""

    history_days: int = 28
    """How much observation history a caller should keep on hand to build
    features. The longest lag feature reaches back 7 days; 28 leaves room
    for a 24h rolling window computed from a sample 7 days back."""

    def __post_init__(self) -> None:
        if self.slot_minutes <= 0:
            raise ConfigError("temporal.slot_minutes must be > 0")
        slots = 1440.0 / self.slot_minutes
        if slots != int(slots):
            raise ConfigError("temporal.slot_minutes must divide 1440 evenly")
        if self.profile_min_samples < 1:
            raise ConfigError("temporal.profile_min_samples must be >= 1")
        if self.history_days < 7:
            raise ConfigError(
                "temporal.history_days must be >= 7 — the longest lag "
                "feature (demand_lag_7_day) needs a full week of history"
            )


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str = "INFO"
    file_path: str | None = None

    def __post_init__(self) -> None:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.level.upper() not in valid:
            raise ConfigError(f"logging.level must be one of {sorted(valid)}")


@dataclass(frozen=True, slots=True)
class Config:
    """The fully resolved configuration for one run."""

    system: SystemConfig = field(default_factory=SystemConfig)
    tank: TankConfig = field(default_factory=TankConfig)
    pump: PumpConfig = field(default_factory=PumpConfig)
    solar: SolarConfig = field(default_factory=SolarConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def __post_init__(self) -> None:
        # Cross-section checks: individual sections are each valid, but
        # their combination may not be.
        if self.pump.min_on_minutes > self.scheduler.horizon_minutes:
            raise ConfigError(
                "pump.min_on_minutes exceeds scheduler.horizon_minutes — the "
                "scheduler could never plan a legal pump run"
            )
        reserve_floor = self.tank.min_level + self.scheduler.safety_reserve_level
        if reserve_floor >= self.tank.max_level:
            raise ConfigError(
                f"tank.min_level + scheduler.safety_reserve_level ({reserve_floor}) "
                f"leaves no operating band below tank.max_level ({self.tank.max_level})"
            )


T = TypeVar("T")


def to_dict(obj: Any) -> Any:
    """Recursively convert a config dataclass to plain JSON-able types.

    Used for hashing and for persisting the exact config a run used.
    ``dataclasses.asdict`` is avoided because it does not unwrap enums.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, RunMode):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}
    return obj
