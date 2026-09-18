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
├── control_loop.py  (one cycle: safety -> scheduler -> safety)
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

`Phase: 5 complete, and hardened. ml/evaluation.py (chronological_split,
MAE, RMSE — shared with Phase 6) and ml/demand/dataset.py
(build_demand_dataset: fits SlotProfile on the train slice only, builds
lag/rolling features from the full series since those only ever look
backward) turn observed_demand_series into a train/val/test dataset.
ml/demand/baselines.py (MeanBaseline) and ml/demand/models.py (Linear/
RandomForest/GradientBoosting, scikit-learn, median-imputed) share one
fit/predict shape. Asked to debug and optimize rather than accept the
first-pass numbers, a real bug turned up: models.tank.observed_demand_lpm
read previous.actuator_on instead of current.actuator_on, silently
recovering demand from the wrong interval's pump state — fabricating
values up to +/-30 L/min against a true profile max under 1 L/min, which
is what had made random forest's first-pass 0.003 MAE look implausibly
good (it was fitting service_level, an artifact of the bug, at 51%
importance). Fixed; a new simulator.scenarios.realistic_household (30
days, real day-to-day demand jitter, unlike the frozen extended_set
scenarios which hold demand fixed for controller-comparison reasons) is
now what --train-demand-model trains against, reporting validation *and*
test MAE/RMSE and flagging any tier that fails to beat the previous one's
validation score. Corrected numbers: mean 0.204, linear 0.018, random
forest 0.018 (wins by ~3%), gradient boosting 0.019 (flagged — doesn't
beat random forest) — all L/min, validation MAE. Given random forest's
edge is marginal and linear regression is far cheaper on a Pi Zero, linear
regression is Phase 5's chosen model, confirmed by the author
(`ml.demand.SELECTED_MODEL`), pending Phase 12's actual inference-cost
benchmark. 484 tests pass, up from 455. See
ROADMAP.md's Phase 5 entry and PROJECT_JOURNEY.md's "Phase 5 hardened"
entry for the full debugging trail. Phase 6 (solar forecast) is cleared
to start.`

Carried out of Phase 5's hardening pass — a real bug, found by checking
first-pass numbers that looked too good rather than accepting them (same
discipline as Phase 4's hardening pass):
- **`observed_demand_lpm` read the wrong observation's `actuator_on`.**
  It used `previous.actuator_on` to infer the pump's inflow during
  `[previous.timestamp, current.timestamp)`. But a real control loop (and
  the simulator, identically) observes, *then* decides, *then* acts — so
  an observation reports the actuator's state as left by the *previous*
  decision, not the one about to govern the interval starting there.
  `current.actuator_on`, recorded after that governing decision was
  applied, is the correct one. `tests/test_temporal.py` had a hand-built
  fixture helper (`_observations_from_steps`) that manually shifted
  `actuator_on` forward by one step, with a comment explaining why —
  proof the mismatch was known but only ever patched around in a test
  fixture, never fixed at the source, because no production path
  exercised the raw stream until this phase's `ScenarioRun.observations`
  field did. Fixed by reading `current.actuator_on`; the fixture
  workaround was deleted and the affected tests now run against
  `run.observations` directly — the real stream, not a corrected stand-in.
- **Measured effect of the fix**, `realistic_household`, `sunny`-style
  demand: recovered values ranged -29.7 to +30.6 L/min before the fix
  (against a true profile maximum under 1 L/min) and 0.01 to 0.96 L/min
  after. Random forest's first-pass MAE (0.003-0.13 depending on
  scenario, implausibly good) was fitting the bug, not the household:
  `service_level` carried 51% of its feature importance before the fix,
  an artifact of the corrupted target, and dropped to a normal supporting
  role (~0.1%) after — `slot_mean_demand_lpm` (98%) took over, the sane
  result for a mostly-deterministic curve plus jitter.
- **The frozen `extended_set`/`standard_set` scenarios were the wrong
  data for ML from the start, independent of the bug.**
  `_household_demand()`'s default `jitter=0.0` means every one of their
  30 days is bit-for-bit identical at the same time-of-day — a model can
  memorize one day and replay it, which was flattering tree ensembles
  before the bug ever mattered. Added `simulator.scenarios.
  realistic_household` (real day-to-day demand jitter via the existing
  `signed_noise`, passing-cloud solar via the existing `IntermittentProfile`
  — both still pure functions of time, no hard-rule conflict) as a
  separate scenario for ML use; `extended_set`/`standard_set` are
  untouched and still frozen for Phase 2/3's controller-comparison
  figures.
- **The validation split existed since the first pass but was never
  actually used for anything.** `main.py --train-demand-model` now
  reports validation MAE/RMSE alongside test, and warns (rather than
  silently listing as a peer) when a tier fails to beat the previous
  tier's validation MAE — gradient boosting currently trips this warning
  against random forest.
- **Final numbers** [simulated], `realistic_household`, 30 days,
  validation MAE (L/min): mean 0.204, linear 0.018, random forest 0.018
  (edges out linear by ~3%), gradient boosting 0.019 (flagged). Given the
  random-forest edge is marginal and linear regression is far cheaper on
  a Pi Zero (one dot product vs. traversing 100 trees), linear regression
  is the tentative choice pending Phase 12's actual inference-cost
  benchmark — "final model chosen by measured performance + Pi inference
  cost" is now a real joint call, not accuracy standing in for an
  unmeasured cost.
- **`ml/evaluation.py` is new, at `ml/` top level, not inside
  `ml/demand/`.** `chronological_split`, `mean_absolute_error` and
  `root_mean_squared_error` have nothing demand-specific in them, and
  Phase 6's solar forecast needs the identical split and the identical
  metrics — duplicating either into `ml/solar/` would let the two targets'
  definitions of "the test set" or "the error" drift apart independently.
- **`SlotProfile` walk-forward split stays train-only; lag/rolling features
  stay full-series.** `SlotProfile.stats_for` keys only by
  `(day_type, slot_index)`, not by date, so fitting it on the whole series
  would leak a val/test-period mean into a training-period feature.
  `demand_lag_*`/`demand_roll_*` only ever look strictly backward from
  `moment`, so building them from the full series is safe for every
  example regardless of split — a val/test moment's lookup can never
  reach a timestamp later than itself.

Carried out of Phase 4's hardening pass — five latent bugs, all found by
probing rather than by the test suite the same author wrote alongside the
code (same blind spot as Phase 1's `715db70`):
- **`rolling_stats` silently dropped the oldest sample on every single
  call at a normal control cadence, not as a rare edge case.** The window
  excluded both edges; on a 15-minute step with a 60-minute window, the
  4th sample lands exactly on `now - 60` and was being thrown out every
  time. Fixed to `[now - window, now)` — inclusive of the far edge,
  exclusive of `now`. Pinned by
  `test_rolling_window_includes_the_sample_exactly_at_its_far_edge`.
- **`build_feature_vector` took a separate `slot_minutes` argument
  alongside `slot_profile`, and nothing stopped them disagreeing** —
  `slot_index` computed on one grid, `slot_mean_demand_lpm` looked up on
  the profile's own different one, silently, in the same vector. Fixed by
  deleting the redundant argument; the slot grid now comes from
  `slot_profile.slot_minutes` alone, structurally, not by a runtime check.
  Pinned by `test_the_slot_grid_comes_only_from_the_profile_no_separate_argument`.
- **`observed_demand_lpm` returned `None` for a reversed pair — the same
  branch as a genuinely zero-duration interval — instead of raising.** A
  caller passing `current` before `previous` has a bug, not an
  unidentifiable interval, and `observed_demand_series` already raised for
  the equivalent condition; the single-pair function now matches it.
  `dt == 0` (truly degenerate, not a caller mistake) still returns `None`.
  Pinned by `test_a_reversed_pair_raises_rather_than_returns_unknown` and
  `test_two_readings_at_the_same_instant_are_unidentifiable_not_an_error`.
- **`observed_demand_lpm` never checked `native_unit`** — the same failure
  class `require_demand_forecast` exists to prevent on the `Forecast`
  side (a PV series read as demand), just missing on the raw-observation
  side. Now raises unless both readings are litres (case-insensitive —
  the simulator writes `"l"`). Pinned by
  `test_a_non_litres_reading_is_rejected`.
- **`TimedValue` and `FeatureVector` carried no provenance at all** — a
  hard-rule violation ("distinguish Measured / Simulated / Predicted /
  Estimated / Derived in all logs and reports"), found on a fresh re-read
  of `CLAUDE.md` against the diff, not by probing. `TimedValue.provenance`
  is now required (no default, matching `ResourceObservation`);
  `observed_demand_series` stamps its output `Provenance.DERIVED` — the
  same category CLAUDE.md's own example ("mm -> litres") names.
  `FeatureVector.provenance` is a single top-level flag, not one per
  feature, mirroring `SchedulingRequest.provenance` — a cyclic-time
  feature has no data-quality question to answer, and the demand/
  service-level features all come from the same caller-assembled history.
  Pinned by `test_derived_demand_is_stamped_as_derived_provenance` and
  the `provenance` assertion in
  `test_a_feature_vector_carries_its_names_values_and_version_in_lockstep`.

Carried out of Phase 4:
- **"Section 12" is not in this repo.** `ROADMAP.md` Phase 4's exit
  criterion names a feature list from an external design doc that greps
  turn up nowhere in the codebase. Decision: the 18-feature list is
  pinned as code (`ml.features.builder.FEATURE_NAMES`, fixed order) and
  tested exactly (`test_feature_names_match_the_pinned_spec_exactly_and_in_order`).
  If section 12 turns up later, that test is the diff point — don't
  silently reorder the vector to match it without checking what already
  depends on the current order.
- **`TankModel.step`'s clamp destroys exactly the information its own
  inverse needs.** `models.tank.observed_demand_lpm` recovers demand from
  two consecutive tank readings, but when an interval ends at capacity
  (with the pump on) or at empty, the clamp that protects the trajectory
  is the same clamp that makes the true demand unrecoverable — spill or
  unmet demand could have absorbed any amount, and the raw arithmetic
  would silently mis-report by that amount. Both cases return `None`
  rather than a number that looks plausible and isn't
  (`test_an_overflowing_interval_is_unidentifiable`,
  `test_a_run_dry_interval_is_unidentifiable`). Phase 5's training data
  will have gaps at exactly the demand spikes and dry-outs that matter
  most — expected, not a bug to "fix" by guessing.
- **A `standard_set` run (Monday start, 3 days) supplies zero weekend
  samples.** `temporal.profiles.SlotProfile` keeps weekday/weekend
  separate because `DiurnalDemandProfile` already scales them
  differently, and a profile built from a standard-set run must report
  every weekend slot as unknown rather than borrowing the weekday mean —
  pinned by `test_a_standard_set_run_leaves_every_weekend_slot_unknown`.
  Use `extended_set` (30 days) for anything that needs weekend coverage.
- **The feature vector is not on `SchedulingRequest`.** Temporal features
  feed a Phase 5 model, the model emits a `Forecast`, and the `Forecast`
  is what the request carries — adding raw features to the request would
  let a scheduler read them directly and merge the ML/optimizer layers
  CLAUDE.md keeps apart. `FEATURE_SET_VERSION` similarly lives in
  `version.py` as a module constant, not on `VersionStamp` — every
  `VersionStamp` field maps to a `component_versions` column, and nothing
  persists a feature vector yet, so adding one would force a schema bump
  for a value with nowhere to be read from.
- **`ObservationRepository.history()` takes an optional `provenance`
  filter, added to the Phase-0-declared signature.** From Phase 13 the
  `resource_observations` table holds measured and simulated rows for the
  same resource side by side; `provenance=None` (the default) returns
  both, unchanged from what the original signature implied, and a caller
  that cares — training data, in particular — must filter explicitly
  rather than get a silent mixture with no way to tell which row came
  from which instrument.
- **`temporal/adaptation.py` stays a stub, on purpose.** Recency
  weighting on `SlotProfile` cannot be shown to help until a demand model
  exists to measure it against — that's Phase 5. Building it now would be
  tuning against nothing.

Carried out of Phase 3:
- **3 days was too short a benchmark window; `extended_set` (30 days, five
  scenarios) is now the comparison standard from Phase 3 onward.** Measured
  [simulated]: at 3 days, `spike` and `sunny` looked tied to threshold on
  grid energy, same as `cloudy`. Re-run at 7/14/30/60 days, both `spike`
  and `sunny` are clear, growing reactive wins — the 3-day tie was an
  artifact of the window being too short to see the effect, not a real
  limit. `standard_set` (3 days, four scenarios) is left untouched and
  frozen, because it is the exact record Phase 2's exit criterion was
  measured and pinned against; do not edit its default duration.
  `monsoon` (sustained heavy overcast, no intermittent breaks — harsher
  than `cloudy`) was added to `extended_set` specifically to test whether
  `cloudy`'s tie was a quirk of that one scenario; it isn't — `monsoon`
  ties too, at every duration, which is what "structural, not artifactual"
  means in practice.
- **The reactive scheduler only tops up on surplus that fully covers the
  pump's rated draw (0.75 kW), not any nonzero surplus.** Measured: a
  first version that topped up on any surplus above the sensor-noise floor
  (0.1 kW) *lost* to the threshold controller on `spike` and `sunny` at 3
  days [simulated] — a partial surplus still draws the rest from the grid,
  and those extra opportunistic starts were grid draw the threshold
  controller's later, better-covered run would not have needed. Requiring
  full coverage fixed both regressions with no scenario ever worse than
  threshold, at any duration tested, but is also why `cloudy`/`monsoon`
  tie rather than improve (`ReactiveScheduler._has_surplus`,
  `test_reactive_vs_threshold_scenarios.py`). A current-surplus scheduler
  only earns the name if it declines a run the grid would have to
  subsidize; getting `cloudy`/`monsoon` to actually improve needs a solar
  *forecast* to defer into a still-building surplus, which is Phase 7's
  job, not this tier's.
- **`ReasonCode.AWAITING_SOLAR` must never be emitted by this tier.** Its
  own docstring is "a better-lit window is forecast" — this scheduler reads
  only `request.solar_surplus_kw`, never `request.solar_forecast`, so it
  has no forecast to justify that reason. Pinned by
  `test_it_never_emits_awaiting_solar`.
- **`run_scenario`'s fallback chain was a chain of one.** Before this phase,
  every call passed a single `Scheduler`, so `FallbackChain` never had a
  second tier to fall to — harmless while threshold was the only tier that
  existed, but it would have silently violated the hard rule ("the fallback
  chain must always exist... down to local safe mode") the moment a second
  tier was added. `run_scenario` now accepts a single scheduler or a tuple;
  `main.py --run-scenarios` with `scheduler.active = "reactive"` now chains
  (reactive, threshold), while `--compare-schedulers` still runs each tier
  standalone on purpose — a comparison that let threshold quietly cover for
  a reactive failure would flatter a number reactive did not earn.

Carried out of Phase 2:
- **`sunny` gives the threshold controller 100% solar already** (0.00 kWh
  grid, `--run-scenarios`). Its refill happens to land at 16:00 every day.
  Phase 3's exit criterion — "beats the threshold controller on grid-powered
  pump energy" — is therefore **unwinnable in `sunny`** and must be judged on
  `cloudy` (0.51 kWh grid), `low_start` (0.75) and the aggregate. Do not
  tune the scenario to fix this; report it.
- **A control step of 30 min or more cannot hold the band.** At 30 lpm the
  pump delivers 900 L in one step, so running overflows and waiting breaches
  the floor. The loop degrades honestly (declines to overflow, the critical
  rule then forces a run that overflows anyway: 23 violations, 142 L spilled
  [simulated]), but it cannot win. `test_a_control_step_this_coarse_cannot_hold_the_band`
  pins it so nobody raises `scheduler.step_minutes` and reads a clean board.
- **The safety layer is a floor, not a plan.** Every rule is reactive: it
  sees a level only after it has fallen. On a cold start the tank breaches
  critical once (min 0.199) before anything starts the pump. No safety rule
  can fix that; only a scheduler that predicts can.
- **`max_starts_per_day` is not a hard bound.** Critical service (priority 5)
  outranks equipment constraints (7), so the safety layer will start the pump
  past its daily budget to keep water in the tank. Measured: budget 1, actual
  4 starts over three days [simulated]. Phase 8's optimizer must not encode it
  as inviolable.
- **`SchedulerTier.SAFETY_LAYER` (0) exists so a safety override is not filed
  as `LOCAL_SAFE_MODE`.** "The Pi's rules decided" and "the Pi is gone and the
  ESP32 is on its own" are opposite states of health, and Phase 14 counts
  tiers. This was a real labelling bug, found by probing rather than by a test.
- **`ReasonCode.BELOW_TARGET_LEVEL` was added, and with it DB schema v2.** A
  refill in progress is not an alarm; reporting it as `CRITICAL_LEVEL` would
  have the "Why?" screen cry wolf at 45% full. Any local `data/surya_sync.db`
  written under v1 is refused, by design — delete it and re-run `--init-db`.
- The threshold controller does a **one-step lookahead** with
  `predict_trajectory` and refuses its own proposal if it breaches a hard
  constraint. That is the `Scheduler` contract (it must return a
  `ConstraintStatus`), not intelligence smuggled into the baseline. With no
  demand forecast it degrades to plain hysteresis and reports
  `checked_constraints=()` rather than implying the check passed.
- `ELECTRICAL_SAFETY` has **no rule yet** and that gap is declared in
  `safety.rules.UNCOVERED_PRIORITIES` and asserted by a test. It needs an
  electrical fault signal, which arrives with the ESP32 in Phase 11. A rule
  that checked nothing would make the layer look complete.

Carried out of Phase 1:
- **A cold start deadlocks the pump.** `changed_at` is written by a
  transition, and unknown timing forbids the only transition that would
  write it. The simulator sidesteps this (a simulated world provably
  begins at `clock`; `TankSimulator(cold_start=True)` exercises the real
  path). Phase 11's `state/` must establish a timestamp from the actuation
  log or from boot time, or a real Pi will never start the pump.
  **Phase 2 measured the cost:** with the full safety layer running, a
  cold-started run holds the pump for ~21 hours and breaches the critical
  floor once (min 0.199) before the critical-service rule forces a start
  [simulated]. Pinned by `test_a_cold_start_holds_the_pump_until_the_floor_forces_it`.
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
