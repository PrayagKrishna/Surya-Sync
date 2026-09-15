# SuryaSync — Project Memory

You are working on **SuryaSync**: a solar-aware residential flexible-load
scheduling system. It learns temporal household behaviour, predicts future
service demand and rooftop solar availability, quantifies the flexibility of
controllable loads, and schedules them with uncertainty-aware MPC.

**The relay is not the innovation. The decision about when the relay should
operate is the innovation.** Do not spend effort proving `ESP32 → relay →
pump ON` works — that is infrastructure, not the contribution.

The current physical validation platform is an overhead water tank + pump.
It is a test platform for load flexibility, not the final scope. The same
`FlexibleResource` interface must later support washing machines, geysers,
EV chargers, batteries, HVAC.

## Priority order — do not reorder

1. Scheduling algorithm
2. System architecture
3. Backend and data pipeline
4. Physical-system modelling
5. ML forecasting
6. Optimization / MPC
7. Raspberry Pi Zero deployment
8. ESP32 integration
9. Experimentation and validation
10. Logging, observability, reproducibility
11. API layer
12. Minimal usable frontend
13. UI polish

When choosing between "better scheduler" and "prettier dashboard," choose
the scheduler — unless missing UI is actually blocking testing or a demo.

## Hardware split — strict separation

**ESP32** = real-time hardware interface + local safety node only: ultrasonic
sampling/filtering, tank-level calc, float switch, pump output, local
failsafe/e-stop, ack of commands. **Never** put the scheduling algorithm here.

**Raspberry Pi Zero** = all intelligence: state management, temporal
history, forecasting (demand/solar/base-load), uncertainty, tank-state
prediction, flexibility estimation, optimization/MPC, experiment execution,
logging, database, backend API.

Pi Zero constraint: lightweight modular monolith. No Docker/K8s stack, no
microservices, no heavyweight DB (SQLite only), no large NN models, no
on-device training.

## Core algorithm loop

```
Observe → Build temporal context → Forecast demand → Forecast solar →
Forecast base load → Estimate uncertainty → Predict tank trajectory →
Estimate flexibility → Optimize (MPC, receding horizon) → Validate
constraints → Execute FIRST action only → Observe again → repeat
```

ML answers "what will likely happen." The optimizer/MPC answers "should the
pump run now." Never merge these two layers.

## Directory structure (extend, don't restructure without reason)

```
surya_sync/
├── main.py
├── config/
├── hardware/        (esp32_serial.py, commands.py, interfaces.py)
├── state/           (system_state.py, state_manager.py)
├── temporal/        (context.py, profiles.py, history.py, adaptation.py)
├── ml/              (features/, demand/, solar/, base_load/, uncertainty/, anomaly/)
├── models/          (tank.py, pump.py, flexibility.py, generic_resource.py)
├── scheduler/       (base.py, threshold.py, fixed_time.py, reactive.py,
│                     heuristic.py, optimizer.py, mpc.py)
├── safety/          (rules.py, validator.py)
├── storage/         (database.py, repositories.py)
├── analytics/       (metrics.py, comparison.py)
├── simulator/        (tank.py, solar.py, demand.py, grid.py, noise.py,
│                     scenarios.py)
├── experiments/      (runner.py, comparison.py, ablation.py)
├── api/
├── frontend/
└── tests/
```

## Hard rules

- Tank constraints (`V_critical ≤ V_k ≤ V_max`) are **hard constraints**,
  never soft penalty terms.
- Safety layer runs **before** the scheduler, in this exact priority:
  electrical safety > actuator protection (pump) > overflow protection >
  sensor validity > critical service availability (water) > manual
  override > equipment constraints > MPC scheduling > solar utilization >
  grid optimization.
- Fallback chain must always exist: Risk-Aware MPC → Predictive Heuristic →
  Reactive Solar Scheduler → Conventional Threshold Controller → ESP32
  local safe mode / manual. The household must keep functioning if the
  sophisticated algorithm fails.
- Every `Scheduler` implementation shares one interface (`generate_plan`)
  and returns: first action, future plan, reason, objective value,
  predicted states, constraint status, solver status, algorithm version.
- Simulated and real resources (`SimulatedTankResource` / `RealTankResource`)
  must run through the **exact same** scheduling code via adapters — never
  fork the algorithm.
- Time-series validation is always chronological (walk-forward). Never
  shuffle.
- Baselines are mandatory before any fancier model: mean → linear
  regression → random forest → gradient boosting. Solar forecast must beat
  persistence and historical-profile baselines before it's trusted.
- Deep learning (LSTM/GRU/Transformer/RL) is off-limits until simpler
  models plateau with measured evidence, and Pi deployment stays practical.
- Every scheduler decision produces a structured explanation object
  (`decision`, `reason_code`, tank state, solar surplus, expected best
  time, flexibility_minutes) — this is what the "Why?" frontend screen
  renders. Never skip it.
- `command sent ≠ command executed`: track desired / reported / confirmed
  pump state as three separate fields.
- Version everything (backend, scheduler, water-model, PV-model, firmware,
  DB schema, experiment config). No `final_v4_REAL_new.pkl` filenames.
- Distinguish Measured / Simulated / Predicted / Estimated / Derived in all
  logs and reports. Never present simulation results as physical results.

## Non-goals — do not add unless explicitly requested

Leak detection, water quality, tank-cleaning prediction, Alexa/Google Home,
voice control, cameras, facial recognition, chatbots, blockchain, NILM,
complex native mobile apps, unrelated smart-home automation.

## When implementing anything

Understand contribution to SuryaSync → identify affected modules → check
existing interfaces first → state assumptions → design before touching
architecture → implement the smallest complete solution → add tests →
explain validation → identify failure modes → preserve backward
compatibility.

## When debugging

Observe → form hypothesis → gather evidence → test ONE change → confirm
root cause → fix → regression test. No shotgun debugging.

## Current phase

> Update this line at the start/end of each session so the next session
> knows where things stand.

`Phase: 1 complete and hardened. Simulator, physical models and the standard scenario
set are in; 277 tests pass. Phase 2 (threshold controller) is cleared to
start.`

Carried out of Phase 1:
- **A cold start deadlocks the pump.** `changed_at` is written by a
  transition, and unknown timing forbids the only transition that would
  write it. The simulator sidesteps this (a simulated world provably
  begins at `clock`; `TankSimulator(cold_start=True)` exercises the real
  path). Phase 11's `state/` must establish a timestamp from the actuation
  log or from boot time, or a real Pi will never start the pump.
- **15-minute control steps cannot hold the target band.** At 30 lpm the
  pump fills the 1000 L tank in 33 minutes, so one `scheduler.step_minutes`
  moves the level 45 points. Phase 2's controller must predict the fill and
  stop early; a purely reactive one overshoots for reasons that are not its
  fault. Do not "fix" this by editing the physics.
- **All shared physics lives in `models/`, never in `simulator/`.**
  `models/tank.py` holds `predict_tank_trajectory`, `tank_constraints`,
  `violates_hard_constraint`, `require_demand_forecast`;
  `models/flexibility.py` holds the flexibility maths; `domain.py` holds
  `forecast_value_at` / `forecast_step_minutes`. `SimulatedTankResource` is
  a thin adapter over them and `RealTankResource` will be the same.
  `tests/test_interfaces.py` fails if any production layer imports
  `simulator/`, because the moment real code has to reach into the
  simulator, the fix someone reaches for is a copy — and the algorithm has
  forked without anyone deciding to.
- `predict_tank_trajectory` and `TankSimulator.advance` both call
  `TankModel.step` and a test asserts they agree exactly. Never give the
  optimizer its own copy of the mass balance.
- Profiles are **pure functions of time**, never call-ordered random
  streams (`simulator/noise.py`), because MPC re-queries the same instant
  across replans and replay re-queries out of order.
- Grid attribution: the pump may claim only surplus PV left after the base
  household load. That rule is what makes Phase 3's exit criterion a real
  test rather than bookkeeping.
- `must_run_by is None` means **unconstrained**, never "too late". A tank
  nothing can save reports a deadline of *now*. Do not collapse the two.
- A demand forecast is checked by `target`, not assumed. Handing the tank a
  PV series used to produce a plausible, entirely wrong trajectory.
- **Phase 14 trap:** `Scenario.n_steps` truncates when the step does not
  divide the run, so controllers benchmarked at different step sizes cover
  different durations. Fix the step across a comparison, or normalize
  per-minute before reporting.
- `flexibility_minutes` reports the horizon length when no floor is breached
  within it — a lower bound presented as a value. Conservative, so accepted;
  do not read it as exact.

Carried out of the Phase 0 review (`PHASE0_REVIEW.md` section 5):
- `admissible_actions(observation, history, now)` takes an
  `ActuationHistory`. A `None` history means *unknown*, and every
  time-based equipment constraint must then read as unsatisfied — hold the
  actuator rather than assume the cooldown elapsed.
- Schedulers read state **only** from the request. Calling
  `request.resource.observe()` is a contract violation; `resource` is for
  physics (`predict_trajectory`, `constraints`, `admissible_actions`).
- No `FALLBACK_ENGAGED` reason code. Falling back is not a rationale —
  keep the substantive reason and set `SchedulingPlan.fallback_engaged`.
- `ReasonCode` and `SolverStatus` are `CHECK`-constrained in
  `scheduler_decisions`. Adding a code means editing the schema too;
  tests compare the two and fail on drift.
- Known limit, accepted: `ResourceConstraints` has no `deadline` or
  `interruptible`. The tank does not need them. Phase 16 adds them as
  optional fields if a second resource type is ever built.

Landed in Phase 0:
- `Scheduler` / `FlexibleResource` interfaces (`scheduler/base.py`,
  `models/generic_resource.py`); everything else in those trees is a stub.
- Shared vocabulary in `domain.py` (`ControlAction`, `Provenance`,
  `Forecast`, `Horizon`) — imports nothing, so it can't cause cycles.
- TOML config via stdlib `tomllib`, validated + hashed (`config/`).
  Unknown keys are fatal. Zero third-party deps so far.
- SQLite schema v1, 19 tables (`storage/schema.sql`). Provenance and
  action values are CHECK-constrained at the DB level, not by convention.
- 72 tests, all passing (`.venv/bin/python -m pytest`).

See `ROADMAP.md` for the full phase list and exit criteria.

`docs/PROJECT_JOURNEY.md` is the portfolio-facing development log — what
actually happened, in what order, and why. **Append an entry at the end of
every phase, and of every session that produces a commit.** If a session is
ending without one, say so before it closes. Timeline entries are
append-only; a reversed decision gets a new entry linking back, never an
edit. Highlights may be rewritten only after showing the diff and getting
confirmation. Every claim needs a referent (commit hash, test count, file
measurement, or an explicit `[simulated]` / `[measured]` tag), and AI
assistance is recorded per entry, separated from the author's design calls.
