"""Ownership and lifecycle of ``SystemState``.

The only writer of system state. Everything else reads an immutable
snapshot, so no component can mutate the world out from under the
scheduler mid-cycle.

The manager is deliberately dumb: it records what it is told and hands
back a frozen snapshot. It contains no physics, no policy and no clock of
its own — the caller supplies every timestamp, because in replay the
"current time" is a recorded value, not ``datetime.now()``.

Phase 2 implements the in-memory half: observations, actuator timing,
electrical state and the desired action. The reported/confirmed halves
need an ESP32 to report anything, so they land in Phase 11. Persistence
lands with ``storage/repositories.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from surya_sync.domain import ControlAction, RunMode
from surya_sync.models.generic_resource import ActuationHistory, ResourceObservation
from surya_sync.state.system_state import (
    ActuationState,
    ActuatorState,
    ElectricalState,
    SystemState,
)
from surya_sync.version import VersionStamp


def actuator_state_from_action(action: ControlAction) -> ActuatorState:
    """Map a control action onto the actuator state it commands.

    ``WAIT`` and ``STOP`` both mean de-energized; they differ only in what
    the actuator was doing before, which the action itself does not carry.
    """
    return ActuatorState.ON if action is ControlAction.RUN else ActuatorState.OFF


class StateManager:
    """Maintains the current ``SystemState`` and its history."""

    def __init__(self, mode: RunMode, versions: VersionStamp) -> None:
        self._mode = mode
        self._versions = versions
        self._timestamp: datetime | None = None
        self._run_id: int | None = None
        self._observations: dict[str, ResourceObservation] = {}
        self._history: dict[str, ActuationHistory] = {}
        self._actuation: dict[str, ActuationState] = {}
        self._electrical: ElectricalState | None = None
        self._last_action: ControlAction | None = None
        self._manual_override: ControlAction | None = None
        self._hardware_link_healthy: bool = mode is RunMode.SIMULATED
        """A simulated link cannot drop. A real one starts unhealthy and is
        promoted only by an actual ESP32 handshake, so a Phase 11 bug that
        never sets it fails closed."""

        self._anomaly_flagged: bool = False

    # --- lifecycle ------------------------------------------------------

    def begin_run(self, run_id: int | None, at: datetime) -> None:
        """Stamp the run this state belongs to, and set the initial clock."""
        self._run_id = run_id
        self._timestamp = at

    def snapshot(self) -> SystemState:
        """Return an immutable view of the current state.

        The dictionaries are copied. Handing out the live ones would let a
        reader mutate the state the decision was justified against, which
        is exactly the failure this class exists to prevent.
        """
        if self._timestamp is None:
            raise RuntimeError(
                "no state recorded yet — call begin_run() or record_observation() "
                "before snapshot(); an empty snapshot would look like a healthy "
                "system with nothing connected"
            )
        return SystemState(
            timestamp=self._timestamp,
            run_id=self._run_id,
            mode=self._mode,
            versions=self._versions,
            observations=dict(self._observations),
            actuation=dict(self._actuation),
            actuation_history=dict(self._history),
            electrical=self._electrical,
            last_action=self._last_action,
            manual_override=self._manual_override,
            hardware_link_healthy=self._hardware_link_healthy,
            anomaly_flagged=self._anomaly_flagged,
        )

    # --- writers --------------------------------------------------------

    def record_observation(self, observation: ResourceObservation) -> None:
        self._observations[observation.resource_id] = observation
        self._advance_clock(observation.timestamp)

    def record_actuation_history(self, history: ActuationHistory) -> None:
        """Record actuator timing. ``state/`` owns this; everything else reads it."""
        self._history[history.resource_id] = history

    def record_electrical(self, electrical: ElectricalState) -> None:
        self._electrical = electrical
        self._advance_clock(electrical.timestamp)

    def record_desired_action(
        self, resource_id: str, action: ControlAction, at: datetime
    ) -> None:
        """Record what the scheduler decided. Does not imply execution.

        Only the ``desired`` third of ``ActuationState`` is touched. The
        reported and confirmed thirds stay whatever the hardware last said,
        which is the entire point of tracking three fields: a command that
        was sent and never executed must remain visible as a divergence.
        """
        current = self._actuation.get(resource_id) or ActuationState.unknown()
        self._actuation[resource_id] = replace(
            current,
            desired=actuator_state_from_action(action),
            desired_at=at,
        )
        self._last_action = action
        self._advance_clock(at)

    def record_reported_state(
        self, resource_id: str, state: ActuatorState, at: datetime
    ) -> None:
        """Record what the hardware says it is doing."""
        current = self._actuation.get(resource_id) or ActuationState.unknown()
        self._actuation[resource_id] = replace(
            current, reported=state, reported_at=at
        )

    def record_confirmed_state(
        self, resource_id: str, state: ActuatorState, at: datetime
    ) -> None:
        """Record independently corroborated actuator state."""
        current = self._actuation.get(resource_id) or ActuationState.unknown()
        self._actuation[resource_id] = replace(
            current, confirmed=state, confirmed_at=at
        )

    def record_simulated_actuation(
        self, resource_id: str, actuator_on: bool, at: datetime
    ) -> None:
        """Mark a simulated command as reported and confirmed.

        In simulation the relay cannot fail, so all three states agree by
        construction. This is a statement about the simulator, not evidence
        about hardware, and the resulting observations carry
        ``Provenance.SIMULATED`` to keep that distinction in the record.
        """
        state = ActuatorState.ON if actuator_on else ActuatorState.OFF
        self.record_reported_state(resource_id, state, at)
        self.record_confirmed_state(resource_id, state, at)

    def set_manual_override(self, action: ControlAction | None) -> None:
        """Set or clear the user's override. Outranks the scheduler only."""
        self._manual_override = action

    def set_hardware_link_healthy(self, healthy: bool) -> None:
        self._hardware_link_healthy = healthy

    def set_anomaly_flagged(self, flagged: bool) -> None:
        self._anomaly_flagged = flagged

    # --- internals ------------------------------------------------------

    def _advance_clock(self, at: datetime) -> None:
        """Move the state clock forward, never backward.

        Out-of-order arrivals are normal (telemetry lags a command), and
        letting one rewind the clock would make a snapshot claim to be
        older than the observation it contains.
        """
        if self._timestamp is None or at > self._timestamp:
            self._timestamp = at


__all__ = ["StateManager", "actuator_state_from_action"]
