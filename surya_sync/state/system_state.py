"""System state representation.

The single source of truth about what SuryaSync currently believes is
happening. Schedulers read it; only ``StateManager`` writes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from surya_sync.domain import ControlAction, Provenance, RunMode
from surya_sync.models.generic_resource import ResourceObservation
from surya_sync.version import VersionStamp


class ActuatorState(str, Enum):
    OFF = "off"
    ON = "on"
    UNKNOWN = "unknown"
    """No fresh report from the hardware. Never assume OFF — an unknown
    actuator is treated as potentially energized by the safety layer."""


@dataclass(frozen=True, slots=True)
class ActuationState:
    """Three-way tracking of a single actuator.

    Hard rule: ``command sent != command executed``. Collapsing these into
    one boolean is how control systems silently lie about reality, so the
    three are tracked separately and always logged together.
    """

    desired: ActuatorState
    """What the scheduler decided."""

    desired_at: datetime | None

    reported: ActuatorState
    """What the ESP32 last said the output pin is doing."""

    reported_at: datetime | None

    confirmed: ActuatorState
    """What independent evidence corroborates — current sensing, or a
    rising tank level while the pump is supposedly running. The only field
    that constitutes physical proof."""

    confirmed_at: datetime | None

    @property
    def is_consistent(self) -> bool:
        """True when all three agree. Divergence is a first-class signal:
        it means a command was lost, the relay failed, or the pump is
        running dry."""
        return self.desired == self.reported == self.confirmed

    @classmethod
    def unknown(cls) -> ActuationState:
        return cls(
            desired=ActuatorState.UNKNOWN,
            desired_at=None,
            reported=ActuatorState.UNKNOWN,
            reported_at=None,
            confirmed=ActuatorState.UNKNOWN,
            confirmed_at=None,
        )


@dataclass(frozen=True, slots=True)
class ElectricalState:
    """Instantaneous household electrical picture.

    ``surplus_kw`` is what the scheduler actually spends: generation the
    household is not already consuming.
    """

    timestamp: datetime
    pv_generation_kw: float | None
    base_load_kw: float | None
    grid_import_kw: float | None
    provenance: Provenance

    @property
    def surplus_kw(self) -> float | None:
        if self.pv_generation_kw is None or self.base_load_kw is None:
            return None
        return max(0.0, self.pv_generation_kw - self.base_load_kw)


@dataclass(frozen=True, slots=True)
class SystemState:
    """A complete snapshot of the system at one instant."""

    timestamp: datetime
    run_id: int | None
    mode: RunMode
    versions: VersionStamp

    observations: dict[str, ResourceObservation] = field(default_factory=dict)
    """Keyed by ``resource_id``."""

    actuation: dict[str, ActuationState] = field(default_factory=dict)
    """Keyed by ``resource_id``."""

    electrical: ElectricalState | None = None

    last_action: ControlAction | None = None
    manual_override: ControlAction | None = None
    """Set by the user via the API. Outranks the scheduler but not the
    safety layer — see ``safety.rules.SafetyPriority``."""

    hardware_link_healthy: bool = False
    anomaly_flagged: bool = False
