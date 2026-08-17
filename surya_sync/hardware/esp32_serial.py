"""ESP32 link over USB serial (newline-delimited JSON).

Phase 11 stub. The interface is fixed now so that Phases 1-10 can develop
against ``simulator/`` without any later change to calling code.
"""

from __future__ import annotations

from surya_sync.hardware.commands import Ack, Command, Handshake, Telemetry
from surya_sync.hardware.interfaces import HardwareInterface


class ESP32SerialInterface(HardwareInterface):
    """Newline-delimited JSON over a serial port."""

    def __init__(self, port: str, baud_rate: int, timeout_s: float) -> None:
        self._port = port
        self._baud_rate = baud_rate
        self._timeout_s = timeout_s
        self._connected = False

    def connect(self) -> Handshake:
        raise NotImplementedError("Phase 11 — see ROADMAP.md")

    def disconnect(self) -> None:
        raise NotImplementedError("Phase 11 — see ROADMAP.md")

    def read_telemetry(self, timeout_s: float) -> Telemetry | None:
        raise NotImplementedError("Phase 11 — see ROADMAP.md")

    def send_command(self, command: Command) -> Ack:
        raise NotImplementedError("Phase 11 — see ROADMAP.md")

    def heartbeat(self) -> None:
        raise NotImplementedError("Phase 11 — see ROADMAP.md")

    @property
    def is_connected(self) -> bool:
        return self._connected
