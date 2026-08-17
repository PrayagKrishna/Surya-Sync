"""Ownership and lifecycle of ``SystemState``.

The only writer of system state. Everything else reads an immutable
snapshot, so no component can mutate the world out from under the
scheduler mid-cycle.

Phase 0: interface only. Implemented in Phase 1.
"""

from __future__ import annotations

from datetime import datetime

from surya_sync.domain import ControlAction, RunMode
from surya_sync.models.generic_resource import ResourceObservation
from surya_sync.state.system_state import (
    ActuationState,
    ElectricalState,
    SystemState,
)
from surya_sync.version import VersionStamp


class StateManager:
    """Maintains the current ``SystemState`` and its history."""

    def __init__(self, mode: RunMode, versions: VersionStamp) -> None:
        self._mode = mode
        self._versions = versions
        self._state: SystemState | None = None

    def snapshot(self) -> SystemState:
        """Return an immutable view of the current state."""
        raise NotImplementedError("Phase 1 — see ROADMAP.md")

    def record_observation(self, observation: ResourceObservation) -> None:
        raise NotImplementedError("Phase 1 — see ROADMAP.md")

    def record_electrical(self, electrical: ElectricalState) -> None:
        raise NotImplementedError("Phase 1 — see ROADMAP.md")

    def record_desired_action(
        self, resource_id: str, action: ControlAction, at: datetime
    ) -> None:
        """Record what the scheduler decided. Does not imply execution."""
        raise NotImplementedError("Phase 1 — see ROADMAP.md")

    def record_reported_state(
        self, resource_id: str, state: ActuationState, at: datetime
    ) -> None:
        """Record what the hardware says it is doing."""
        raise NotImplementedError("Phase 11 — see ROADMAP.md")

    def record_confirmed_state(
        self, resource_id: str, state: ActuationState, at: datetime
    ) -> None:
        """Record independently corroborated actuator state."""
        raise NotImplementedError("Phase 11 — see ROADMAP.md")
