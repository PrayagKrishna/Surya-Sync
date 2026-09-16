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
    parser.add_argument(
        "--compare-schedulers",
        action="store_true",
        help="run the standard scenario set through the threshold and "
        "reactive controllers and print grid-energy comparison, then exit",
    )
    return parser.parse_args(argv)


def _build_schedulers(name: str, config: Config) -> tuple:
    """The fallback chain for one configured active tier.

    Returns every tier from ``name`` down to the threshold controller, so
    that a real chain always exists — per the hard rule, the household must
    keep functioning if the sophisticated algorithm fails, and that means
    falling to the next *scheduler* tier, not straight past it to local
    safe mode. ``FallbackChain`` sorts the result itself; order here does
    not matter.
    """
    from surya_sync.scheduler.reactive import ReactiveScheduler
    from surya_sync.scheduler.threshold import ThresholdScheduler

    threshold = ThresholdScheduler(config.scheduler)
    if name == "threshold":
        return (threshold,)
    if name == "reactive":
        reactive = ReactiveScheduler(config.scheduler, config.solar.surplus_threshold_kw)
        return (reactive, threshold)
    raise ValueError(
        f"scheduler.active is {name!r}, but only 'threshold' (Phase 2) and "
        "'reactive' (Phase 3) are implemented"
    )


def run_scenarios(config: Config, versions: VersionStamp) -> int:
    """Run the standard set and print a summary.

    Every number printed here is **simulated**. The header says so on every
    run rather than in a footnote, because a table of figures copied out of
    a terminal loses its caveat immediately.
    """
    from surya_sync.experiments.runner import run_standard_set
    from surya_sync.simulator.scenarios import standard_set

    try:
        _build_schedulers(config.scheduler.active, config)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    runs = run_standard_set(
        config,
        standard_set(config),
        lambda: _build_schedulers(config.scheduler.active, config),
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


def compare_schedulers(config: Config, versions: VersionStamp) -> int:
    """Run threshold and reactive across the standard set and compare.

    This is the Phase 3 exit criterion in executable form: grid-powered
    pump energy, threshold vs. reactive, per scenario and aggregated. Every
    number is **simulated**.

    Each controller runs standalone here, with no fallback beneath it —
    unlike ``run_scenarios``, which runs the real chain. A comparison is
    supposed to isolate one algorithm's own decisions; a chain that quietly
    handed cycles to threshold on any reactive hiccup would flatter
    reactive's number without reactive having earned it.
    """
    from surya_sync.analytics.comparison import aggregate_grid_kwh, compare_grid_energy
    from surya_sync.experiments.runner import run_standard_set
    from surya_sync.simulator.scenarios import standard_set

    scenarios = standard_set(config)
    baseline_runs = run_standard_set(
        config,
        scenarios,
        lambda: _build_schedulers("threshold", config)[0],
        config_hash=versions.config_hash,
    )
    candidate_runs = run_standard_set(
        config,
        scenarios,
        lambda: _build_schedulers("reactive", config)[0],
        config_hash=versions.config_hash,
    )

    worst = max(
        (len(run.violation_steps) for run in baseline_runs + candidate_runs),
        default=0,
    )
    for run in baseline_runs + candidate_runs:
        if run.violation_steps:
            print(
                f"WARNING: {run.scheduler_name} violates hard constraints in "
                f"{run.scenario_name} ({len(run.violation_steps)} steps)",
                file=sys.stderr,
            )

    rows = compare_grid_energy(baseline_runs, candidate_runs)
    baseline_total, candidate_total = aggregate_grid_kwh(rows)

    print("SIMULATED results — not physical measurements")
    print(f"config_hash {versions.config_hash}   step {config.scheduler.step_minutes:g} min")
    print(
        f"{'scenario':10s} {'threshold_kWh':>14s} {'reactive_kWh':>13s} "
        f"{'saved_kWh':>10s}"
    )
    for row in rows:
        print(
            f"{row.scenario_name:10s} {row.baseline_grid_kwh:14.2f} "
            f"{row.candidate_grid_kwh:13.2f} {row.grid_kwh_saved:10.2f}"
        )
    print(
        f"{'aggregate':10s} {baseline_total:14.2f} {candidate_total:13.2f} "
        f"{baseline_total - candidate_total:10.2f}"
    )
    print(
        "note: 'sunny' already draws 0.00 kWh from the grid under threshold "
        "(Phase 2) and cannot be beaten — judge this exit criterion on "
        "'cloudy', 'low_start' and the aggregate."
    )

    if worst:
        return 5
    return 0 if candidate_total < baseline_total else 6


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

    if args.compare_schedulers:
        # Same reasoning as --run-scenarios below: nothing here persists yet.
        return compare_schedulers(config, versions)

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
