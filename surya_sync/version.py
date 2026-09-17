"""Component version registry.

Every artefact SuryaSync produces — a decision, a forecast, an experiment
result — is stamped with the versions of the components that produced it.
This is what makes results reproducible and comparable across sessions.

Hard rule: no ``final_v4_REAL_new.pkl`` filenames. Versions live here.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

# --- Component versions -------------------------------------------------
# Bump the relevant constant whenever the behaviour of that component
# changes in a way that could alter results.

BACKEND_VERSION = "0.1.0"
"""Overall application version."""

DB_SCHEMA_VERSION = 2
"""Integer, monotonically increasing. Matches ``storage/schema.sql``.

v2 (Phase 2) adds ``below_target_level`` to the ``reason_code`` vocabulary.
The ``ReasonCode`` enum is ``CHECK``-constrained in ``scheduler_decisions``,
so a new code is a schema change by construction — that friction is the
point, not an accident."""

SERIAL_PROTOCOL_VERSION = "1.0"
"""Newline-delimited JSON protocol spoken over USB serial to the ESP32."""

WATER_MODEL_VERSION = "1.0.0"
"""Tank + pump physical model. Covers ``models.tank`` (``tank-1.0.0``) and
``models.pump`` (``pump-1.0.0``) together, because a trajectory depends on
both and neither is useful alone."""

PV_MODEL_VERSION = "1.0.0"
"""Rooftop PV generation model — the clear-sky geometry plus the grid
attribution rule in ``simulator/``. Refined in Phase 6, when a forecast
replaces the geometry."""

FEATURE_SET_VERSION = "features-1.0.0"
"""``ml.features.builder.FEATURE_NAMES`` — the ordering and definition of
the feature vector. Not a ``VersionStamp`` field: every ``VersionStamp``
field maps to a ``component_versions`` column, and nothing persists a
feature vector yet in Phase 4, so adding one would force a schema bump for
a value with nowhere to be read from. Follows the precedent of
``forecasts.model_version``, which is stored per-row once Phase 5 has rows
to store it on."""


@dataclass(frozen=True, slots=True)
class VersionStamp:
    """The full set of versions in play for a single run.

    Persisted to ``component_versions`` at the start of every run and
    referenced by every row produced during it.
    """

    backend_version: str = BACKEND_VERSION
    db_schema_version: int = DB_SCHEMA_VERSION
    serial_protocol_version: str = SERIAL_PROTOCOL_VERSION
    water_model_version: str = WATER_MODEL_VERSION
    pv_model_version: str = PV_MODEL_VERSION
    scheduler_version: str | None = None
    """Set from the active ``Scheduler.algorithm_version``."""
    demand_model_version: str | None = None
    solar_model_version: str | None = None
    firmware_version: str | None = None
    """Reported by the ESP32 in its handshake; ``None`` in simulation."""
    config_hash: str | None = None
    """Hash of the resolved config — see ``config.loader.config_hash``."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
