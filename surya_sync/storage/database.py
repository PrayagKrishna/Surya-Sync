"""SQLite connection management and schema initialization.

SQLite only — no server, no ORM. It is the right database for a Pi Zero
and it makes an experiment portable as a single file.

This module carries real implementation (unlike the rest of Phase 0)
because "SQLite schema created" is a Phase 0 exit criterion.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType

from surya_sync.version import DB_SCHEMA_VERSION

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class SchemaVersionError(RuntimeError):
    """Raised when a database's schema version does not match the code.

    Always fatal. Reading a database written by a different schema is how
    experiments quietly become incomparable.
    """


class Database:
    """A connection to the SuryaSync SQLite database.

    Usable as a context manager. Connections are configured for the
    embedded case: WAL so the API can read while the control loop writes,
    and foreign keys on so the provenance spine is actually enforced.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None

    # --- lifecycle ------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        """Open the connection, creating parent directories as needed."""
        if self._connection is not None:
            return self._connection

        if self.path != Path(":memory:"):
            self.path.parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(
            self.path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level=None,  # explicit transaction control
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        self._connection = connection
        return connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("database is not connected; call connect() first")
        return self._connection

    def __enter__(self) -> Database:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- schema ---------------------------------------------------------

    def initialize_schema(self) -> int:
        """Create the schema if absent and return its version.

        Idempotent: safe to call on every startup. Refuses to touch a
        database whose recorded version differs from ``DB_SCHEMA_VERSION``
        rather than attempting an implicit migration.
        """
        connection = self.connect()
        existing = self.schema_version()

        if existing is None:
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_version (version, applied_at, description) "
                "VALUES (?, datetime('now'), ?)",
                (DB_SCHEMA_VERSION, "initial schema"),
            )
            connection.commit()
            return DB_SCHEMA_VERSION

        if existing != DB_SCHEMA_VERSION:
            raise SchemaVersionError(
                f"database at {self.path} is schema version {existing}, but this "
                f"build expects {DB_SCHEMA_VERSION}. Migrate explicitly — "
                f"SuryaSync will not upgrade a database in place."
            )
        return existing

    def schema_version(self) -> int | None:
        """Return the recorded schema version, or ``None`` if uninitialized."""
        connection = self.connect()
        row = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_version'"
        ).fetchone()
        if row is None:
            return None

        row = connection.execute(
            "SELECT MAX(version) AS version FROM schema_version"
        ).fetchone()
        return None if row is None or row["version"] is None else int(row["version"])

    def table_names(self) -> tuple[str, ...]:
        """Names of all user tables, sorted. Used by tests and diagnostics."""
        rows = self.connect().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return tuple(row["name"] for row in rows)
