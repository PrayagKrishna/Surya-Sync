"""SuryaSync entry point.

Loads and validates config, initializes the database schema, reports the
version stamp, and — from Phase 2 — runs the standard scenario set through
a controller so that a phase's claims can be reproduced from the command
line rather than from a test.

One cycle of the loop lives in ``control_loop.ControlCycle``, which is
shared by the simulator and, in Phase 11, by the ESP32. What is still
missing here is the part above it: forecasting, uncertainty and MPC.

The loop this will become:

    Observe -> temporal context -> forecast demand -> forecast solar ->
    forecast base load -> estimate uncertainty -> predict trajectory ->
    estimate flexibility -> optimize (MPC) -> validate constraints ->
    execute FIRST action only -> observe again

Run with::

    python -m surya_sync.main --init-db
    python -m surya_sync.main --run-scenarios
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
    parser.add_argument(
        "--run-scenarios",
        action="store_true",
        help="run the standard scenario set through the active controller "
        "and print a simulated summary, then exit",
    )
    return parser.parse_args(argv)


def run_scenarios(config: Config, versions: VersionStamp) -> int:
    """Run the standard set and print a summary.

    Every number printed here is **simulated**. The header says so on every
    run rather than in a footnote, because a table of figures copied out of
    a terminal loses its caveat immediately.
    """
    from surya_sync.experiments.runner import run_standard_set
    from surya_sync.scheduler.threshold import ThresholdScheduler
    from surya_sync.simulator.scenarios import standard_set

    if config.scheduler.active != "threshold":
        print(
            f"scheduler.active is {config.scheduler.active!r}, but only "
            "'threshold' is implemented (Phase 2)",
            file=sys.stderr,
        )
        return 4

    runs = run_standard_set(
        config,
        standard_set(config),
        lambda: ThresholdScheduler(config.scheduler),
        config_hash=versions.config_hash,
    )

    print("SIMULATED results — not physical measurements")
    print(f"config_hash {versions.config_hash}   step {config.scheduler.step_minutes:g} min")
    print(
        f"{'scenario':10s} {'viol':>5s} {'unmet_L':>8s} {'spill_L':>8s} "
        f"{'starts':>7s} {'pump_kWh':>9s} {'grid_kWh':>9s} {'solar_%':>8s}"
    )
    worst = 0
    for run in runs:
        share = (
            100.0 * run.solar_energy_kwh / run.pump_energy_kwh
            if run.pump_energy_kwh > 0.0
            else 0.0
        )
        worst = max(worst, len(run.violation_steps))
        print(
            f"{run.scenario_name:10s} {len(run.violation_steps):5d} "
            f"{run.unmet_demand_l:8.1f} {run.spilled_l:8.1f} {run.starts:7d} "
            f"{run.pump_energy_kwh:9.2f} {run.grid_energy_kwh:9.2f} {share:8.1f}"
        )
    return 0 if worst == 0 else 5


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

    if args.run_scenarios:
        # Deliberately before the database is opened: a scenario run persists
        # nothing yet, so requiring a schema-matched database would make a
        # pure simulation fail for a reason that has nothing to do with it.
        # Phase 14 persists results, and then this moves back down.
        return run_scenarios(config, versions)

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
        "continuous control loop not wired up yet — Phase 9 (MPC). "
        "Use --run-scenarios to drive the simulator. See ROADMAP.md"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
