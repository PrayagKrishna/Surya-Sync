"""Repository interfaces over the SQLite schema.

One repository per bounded concern. Everything else in the codebase talks
to these rather than writing SQL, so that provenance and version stamping
cannot be forgotten at a call site.

Phase 0: signatures only. Implemented alongside the phase that first needs
each one.
"""

from __future__ import annotations

from datetime import datetime

from surya_sync.config.schema import Config
from surya_sync.domain import Forecast, Provenance, RunMode
from surya_sync.models.generic_resource import (
    FlexibilityEstimate,
    FlexibleResource,
    ResourceObservation,
)
from surya_sync.safety.validator import SafetyDecision
from surya_sync.scheduler.base import SchedulingPlan
from surya_sync.state.system_state import ActuationState, ElectricalState
from surya_sync.storage.database import Database
from surya_sync.version import VersionStamp


class Repository:
    """Common base: holds the connection, owns nothing else."""

    def __init__(self, database: Database) -> None:
        self._db = database


class RunRepository(Repository):
    """Runs and the component versions they were produced under."""

    def start_run(
        self,
        mode: RunMode,
        versions: VersionStamp,
        config: Config,
        scenario: str | None = None,
        experiment_id: int | None = None,
    ) -> int:
        """Record a new run and return its id.

        Writes the ``component_versions`` row first, so no observation can
        ever exist without a traceable version stamp.
        """
        raise NotImplementedError("Phase 1 — see ROADMAP.md")

    def end_run(self, run_id: int, ended_at: datetime) -> None:
        raise NotImplementedError("Phase 1 — see ROADMAP.md")


class ResourceRepository(Repository):
    """The registry of controllable resources."""

    def register(self, resource: FlexibleResource) -> None:
        raise NotImplementedError("Phase 1 — see ROADMAP.md")


class ObservationRepository(Repository):
    """Sensor readings, resource state, and the electrical picture."""

    def record_sensor_reading(
        self,
        run_id: int,
        timestamp: datetime,
        channel: str,
        value: float | None,
        unit: str,
        provenance: Provenance,
        resource_id: str | None = None,
        valid: bool = True,
        sequence: int | None = None,
    ) -> None:
        raise NotImplementedError("Phase 1 — see ROADMAP.md")

    def record_observation(
        self, run_id: int, observation: ResourceObservation
    ) -> None:
        raise NotImplementedError("Phase 1 — see ROADMAP.md")

    def record_electrical(self, run_id: int, electrical: ElectricalState) -> None:
        raise NotImplementedError("Phase 1 — see ROADMAP.md")

    def history(
        self,
        resource_id: str,
        since: datetime,
        until: datetime,
        provenance: Provenance | None = None,
    ) -> tuple[ResourceObservation, ...]:
        """Observations in chronological order, ``since <= timestamp < until``.

        Time-series validation is always walk-forward, so history is
        returned ordered and never shuffled downstream.

        ``provenance=None`` returns every row regardless of provenance.
        From Phase 13 onward, measured and simulated rows for the same
        resource can coexist in this table; a temporal feature built from
        a silent mixture of the two would train on data with no way to
        tell which instrument produced which sample, so a caller that
        cares must filter explicitly rather than relying on a default.
        """
        query = (
            "SELECT timestamp, resource_id, service_level, native_value, "
            "native_unit, actuator_on, sensor_valid, provenance "
            "FROM resource_observations "
            "WHERE resource_id = ? AND timestamp >= ? AND timestamp < ?"
        )
        params: list[object] = [resource_id, since.isoformat(), until.isoformat()]
        if provenance is not None:
            query += " AND provenance = ?"
            params.append(provenance.value)
        query += " ORDER BY timestamp ASC"

        rows = self._db.connection.execute(query, params).fetchall()
        return tuple(_observation_from_row(row) for row in rows)


class ActuationRepository(Repository):
    """Desired / reported / confirmed actuator state."""

    def record_desired(
        self,
        run_id: int,
        resource_id: str,
        command_id: str,
        state: ActuationState,
        decision_id: int | None = None,
    ) -> int:
        raise NotImplementedError("Phase 1 — see ROADMAP.md")

    def record_reported(
        self, command_id: str, state: ActuationState, ack_status: str, message: str
    ) -> None:
        raise NotImplementedError("Phase 11 — see ROADMAP.md")

    def record_confirmed(self, command_id: str, state: ActuationState) -> None:
        raise NotImplementedError("Phase 11 — see ROADMAP.md")


class DecisionRepository(Repository):
    """Scheduling decisions, plans, predicted trajectories, explanations."""

    def record_plan(
        self,
        run_id: int,
        resource_id: str,
        plan: SchedulingPlan,
        provenance: Provenance,
        fallback_engaged: bool = False,
    ) -> int:
        """Persist a plan in full and return the decision id.

        Writes ``scheduler_decisions``, ``planned_actions`` and
        ``predicted_states`` in one transaction — a decision without its
        plan and predicted trajectory is not auditable.
        """
        raise NotImplementedError("Phase 2 — see ROADMAP.md")

    def record_flexibility(
        self, run_id: int, estimate: FlexibilityEstimate
    ) -> None:
        raise NotImplementedError("Phase 7 — see ROADMAP.md")

    def latest_decision(self, resource_id: str) -> SchedulingPlan | None:
        raise NotImplementedError("Phase 15 — see ROADMAP.md")


class ForecastRepository(Repository):
    """Forecasts and their realized errors."""

    def record_forecast(self, run_id: int, forecast: Forecast) -> int:
        raise NotImplementedError("Phase 5 — see ROADMAP.md")

    def score_against_actuals(self, forecast_id: int) -> None:
        """Join a past forecast to what actually happened.

        The basis for the walk-forward MAE/RMSE reporting that decides
        whether a model earns its place over the baselines.
        """
        raise NotImplementedError("Phase 5 — see ROADMAP.md")


class SafetyRepository(Repository):
    """Safety verdicts, overrides and anomaly flags."""

    def record_decision(
        self,
        run_id: int,
        resource_id: str | None,
        decision: SafetyDecision,
        scheduler_decision_id: int | None = None,
    ) -> None:
        """Persist every verdict, triggered or not.

        An untriggered rule that ran is evidence the check happened; a
        missing row means the check was skipped.
        """
        raise NotImplementedError("Phase 2 — see ROADMAP.md")


class MetricsRepository(Repository):
    """Computed metrics for analytics and experiment comparison."""

    def record_metric(
        self,
        run_id: int,
        name: str,
        value: float,
        provenance: Provenance,
        unit: str = "",
        experiment_id: int | None = None,
    ) -> None:
        raise NotImplementedError("Phase 3 — see ROADMAP.md")


def _observation_from_row(row: object) -> ResourceObservation:
    return ResourceObservation(
        timestamp=datetime.fromisoformat(row["timestamp"]),
        resource_id=row["resource_id"],
        service_level=row["service_level"],
        native_value=row["native_value"],
        native_unit=row["native_unit"],
        actuator_on=bool(row["actuator_on"]),
        provenance=Provenance(row["provenance"]),
        sensor_valid=bool(row["sensor_valid"]),
    )
