"""Wire protocol between the Raspberry Pi Zero and the ESP32.

Newline-delimited JSON over USB serial. One JSON object per line, no
framing beyond ``\\n``, so a dropped byte costs one message rather than
resynchronization.

Design constraint: the ESP32 is a hardware interface and local safety
node. It samples sensors, drives the pump output, and refuses unsafe
commands on its own authority. **No scheduling logic crosses this
boundary** — the Pi sends a desired state, not a plan.

Phase 0: message shapes only. Serialization lands in Phase 11.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class MessageType(str, Enum):
    TELEMETRY = "telemetry"
    """ESP32 -> Pi. Periodic sensor and actuator report."""

    COMMAND = "command"
    """Pi -> ESP32. Requested actuator state."""

    ACK = "ack"
    """ESP32 -> Pi. Command accepted, rejected, or superseded."""

    HEARTBEAT = "heartbeat"
    """Bidirectional. Loss of heartbeat puts the ESP32 into local safe
    mode — the Pi being down must never leave the pump energized."""

    HANDSHAKE = "handshake"
    """ESP32 -> Pi on connect. Carries firmware and protocol version."""

    EVENT = "event"
    """ESP32 -> Pi. Asynchronous local safety event (float switch, e-stop,
    sensor fault). Not a response to anything the Pi sent."""


class AckStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED_UNSAFE = "rejected_unsafe"
    """The ESP32's local safety logic refused. Authoritative — the Pi does
    not retry a rejected command, it records and re-plans."""

    REJECTED_MALFORMED = "rejected_malformed"
    SUPERSEDED = "superseded"
    """A newer command arrived before this one was applied."""


@dataclass(frozen=True, slots=True)
class Telemetry:
    """One sensor report from the ESP32.

    ``distance_mm`` is the raw ultrasonic reading *after* on-device
    filtering; the Pi converts it to volume using the versioned water
    model, so the conversion stays auditable and re-runnable against
    historical raw data.
    """

    timestamp: datetime
    """Pi-side receipt time. The ESP32 has no reliable clock."""

    sequence: int
    distance_mm: float | None
    sensor_valid: bool
    pump_output_on: bool
    """What the ESP32 has commanded its own output pin to do."""

    float_switch_high: bool | None = None
    current_sensed_a: float | None = None
    """Independent evidence the pump is actually drawing power — this is
    what promotes ``reported`` state to ``confirmed``."""

    uptime_ms: int | None = None
    local_safe_mode: bool = False


@dataclass(frozen=True, slots=True)
class Command:
    """A desired actuator state sent to the ESP32.

    Carries no timing or reasoning: the Pi re-issues the desired state
    every control cycle. A command is a level, not an edge, so a lost
    message self-heals on the next cycle.
    """

    command_id: str
    issued_at: datetime
    pump_on: bool
    max_runtime_seconds: int
    """Deadman timer. The ESP32 de-energizes after this even if the Pi
    goes silent. Defence in depth against the intelligence layer failing."""


@dataclass(frozen=True, slots=True)
class Ack:
    """The ESP32's response to a command."""

    command_id: str
    received_at: datetime
    status: AckStatus
    pump_output_on: bool
    """Actuator state after applying (or refusing) the command."""

    message: str = ""


@dataclass(frozen=True, slots=True)
class Handshake:
    """Identity and capability report sent by the ESP32 on connect."""

    firmware_version: str
    protocol_version: str
    device_id: str
