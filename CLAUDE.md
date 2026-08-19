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
├── simulator/        (tank.py, solar.py, demand.py, scenarios.py)
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

`Phase: 0 complete, reviewed and pushed. Interface changes from the review
are in. Phase 1 (simulator) is cleared to start.`

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
