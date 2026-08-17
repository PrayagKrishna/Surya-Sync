"""The hardware boundary.

``HardwareInterface`` is the seam between the intelligence layer and
physical reality. A simulated implementation and a real ESP32 satisfy the
same interface, which is what lets the identical scheduling code run in
both environments.

Phase 0: interface only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from surya_sync.hardware.commands import Ack, Command, Handshake, Telemetry


class HardwareError(Exception):
    """Raised on transport failure (port gone, protocol violation).

    Never raised for a *rejected* command — rejection is a legitimate
    answer from the ESP32's safety logic and arrives as an ``Ack``.
    """


class HardwareInterface(ABC):
    """Transport to the real-time hardware node."""

    @abstractmethod
    def connect(self) -> Handshake:
        """Open the link and complete the version handshake.

        Raises ``HardwareError`` if the firmware's protocol version is
        incompatible — a mismatched protocol is a stop condition, not
        something to negotiate around.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Close the link, leaving the actuator in a safe state."""

    @abstractmethod
    def read_telemetry(self, timeout_s: float) -> Telemetry | None:
        """Return the next telemetry frame, or ``None`` on timeout.

        A timeout is normal operation, not an error — the caller decides
        how many consecutive misses constitute a link failure.
        """

    @abstractmethod
    def send_command(self, command: Command) -> Ack:
        """Send a desired actuator state and wait for acknowledgement."""

    @abstractmethod
    def heartbeat(self) -> None:
        """Tell the ESP32 the intelligence layer is alive.

        Silence here is a safety feature: it drives the ESP32 into local
        safe mode rather than leaving stale commands in effect.
        """

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...
