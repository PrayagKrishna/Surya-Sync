"""SuryaSync entry point.

Phase 0 does exactly what Phase 0 promises: load and validate config,
initialize the database schema, report the version stamp. The control
loop itself lands in Phase 1 once there is a simulator to drive it.

The loop this will become:

    Observe -> temporal context -> forecast demand -> forecast solar ->
    forecast base load -> estimate uncertainty -> predict trajectory ->
    estimate flexibility -> optimize (MPC) -> validate constraints ->
    execute FIRST action only -> observe again

Run with::

    python -m surya_sync.main --init-db
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from surya_sync.config.loader import config_hash, load_config
from surya_sync.config.schema import Config, ConfigError
from surya_sync.storage.database import Database, SchemaVersionError
from surya_sync.version import BACKEND_VERSION, VersionStamp

logger = logging.getLogger("surya_sync")


def build_version_stamp(config: Config) -> VersionStamp:
    """Version stamp for this process, including the config hash."""
    return VersionStamp(config_hash=config_hash(config))


def configure_logging(config: Config) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if config.logging.file_path:
        path = Path(config.logging.file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path))
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper()),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="surya_sync",
        description="Solar-aware residential flexible-load scheduling.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="path to a TOML config file (defaults to the packaged defaults)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="create the SQLite schema if it does not exist, then exit",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="print the resolved config hash and key parameters, then exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    configure_logging(config)
    versions = build_version_stamp(config)

    logger.info(
        "SuryaSync %s starting (mode=%s, config_hash=%s)",
        BACKEND_VERSION,
        config.system.mode.value,
        versions.config_hash,
    )

    if args.show_config:
        print(f"backend_version   {BACKEND_VERSION}")
        print(f"config_hash       {versions.config_hash}")
        print(f"mode              {config.system.mode.value}")
        print(f"tank capacity     {config.tank.capacity_l} L")
        print(
            "tank levels       "
            f"critical={config.tank.critical_level} "
            f"min={config.tank.min_level} max={config.tank.max_level}"
        )
        print(f"scheduler         {config.scheduler.active}")
        print(
            "horizon           "
            f"{config.scheduler.horizon_minutes} min "
            f"@ {config.scheduler.step_minutes} min steps"
        )
        print(f"database          {config.storage.database_path}")
        return 0

    try:
        with Database(config.storage.database_path) as database:
            version = database.initialize_schema()
            logger.info(
                "database ready at %s (schema version %d, %d tables)",
                database.path,
                version,
                len(database.table_names()),
            )
    except SchemaVersionError as exc:
        logger.error("%s", exc)
        return 3

    if args.init_db:
        return 0

    logger.info(
        "control loop not implemented yet — Phase 1 (simulator). See ROADMAP.md"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
