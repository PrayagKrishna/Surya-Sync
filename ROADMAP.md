# SuryaSync Roadmap

One phase = one focused Claude Code session (or a tight cluster of them).
Do not start phase N+1 until phase N's exit criteria are met and tested.
Update the checkbox and the "Current phase" line in CLAUDE.md when a phase
closes.

- [x] **Phase 0 — Architecture** ✅
  Interfaces (`Scheduler`, `FlexibleResource`), config schema, DB schema.
  Exit: directory tree exists, interfaces are stubbed, `config/` loads,
  SQLite schema created, no logic yet.
  *Met: tree + stubs in place; `load_config()` validates and hashes;
  `python -m surya_sync.main --init-db` creates schema v1 idempotently;
  72 tests pass. Only `config/loader.py` and `storage/database.py` carry
  implementation — required by the exit criteria themselves.*

- [x] **Phase 1 — Simulator** ✅
  Tank simulator, pump model, demand profiles, solar profiles, grid model.
  Exit: `SimulatedTankResource` runs a multi-day trajectory deterministically;
  unit tests for tank conversion + physical model + pump model pass.
  *Met: `SimulatedTankResource` runs the four standard scenarios over three
  simulated days and reproduces them exactly; `predict_trajectory` and
  `TankSimulator.advance` are asserted to agree to floating point because
  both call `TankModel.step`; 262 tests pass, of which 181 are physical —
  up from 0.*

- [x] **Phase 2 — Conventional control** ✅
  Threshold controller implementing `Scheduler`.
  Exit: runs against the simulator, never violates hard tank constraints
  across the standard scenario set (sunny/cloudy/spike/low-start).
  *Met: `ThresholdScheduler` runs all four scenarios over three simulated
  days with **0 violating steps, 0 L spilled and 0 L unmet demand**, within
  the daily start budget (`--run-scenarios`, and asserted per scenario in
  `tests/test_control_loop.py`). The safety layer, the fallback chain and the
  shared `control_loop.ControlCycle` landed with it; 379 tests pass, up from
  277. The exit criterion holds at the configured 15-minute step and is not a
  claim about any step — 30 minutes and above provably cannot hold the band,
  and a test pins that.*

- [x] **Phase 3 — Solar-reactive control** ✅
  Simple current-surplus scheduler.
  Exit: beats threshold controller on grid-powered pump energy in
  simulation; comparison logged via `analytics/comparison.py`.
  *Met: `ReactiveScheduler` beats the threshold controller on the
  extended-set aggregate (21.537 -> 15.872 kWh grid, `[simulated]`, 30
  days) and on 3 of 5 individual scenarios (`low_start`, `spike`, `sunny`),
  with the other 2 (`cloudy`, `monsoon`) tying — never losing — for a
  measured, structural reason recorded below. Comparison logged via
  `analytics/comparison.py` and reproducible with `--compare-schedulers`.
  Zero hard-constraint violations at every duration checked (3-60 days).
  412 tests pass. Closed 2026-09-16 on the extended-set evidence, after an
  earlier close-or-not-yet decision was made and then revisited once the
  3-day benchmark was found to be too short a window (see below).*
  *Note from Phase 2: the threshold controller already draws 0.00 kWh from
  the grid in `sunny`, so that scenario cannot be beaten. Judge on `cloudy`
  (0.51 kWh), `low_start` (0.75 kWh) and the aggregate.*
  *Measured [simulated] at the original 3-day standard set: `low_start`
  0.75 -> 0.19 kWh, aggregate 1.64 -> 1.07 kWh, `sunny`/`cloudy`/`spike` all
  tied. **3 days turned out to be too short to judge this fairly** — see
  below.*
  *Re-measured [simulated] on `extended_set` (30 days, five scenarios —
  `sunny`/`cloudy`/`spike`/`low_start` plus `monsoon`, sustained heavy
  overcast) via `--compare-schedulers`:*
  | scenario | threshold kWh | reactive kWh | saved |
  |---|---|---|---|
  | cloudy | 5.443 | 5.443 | 0.000 (tied) |
  | low_start | 3.586 | 1.137 | 2.449 |
  | monsoon | 5.813 | 5.813 | 0.000 (tied) |
  | spike | 4.045 | 2.148 | 1.896 |
  | sunny | 2.651 | 1.331 | 1.320 |
  | **aggregate** | **21.537** | **15.872** | **5.666** |
  *`spike` and `sunny` — tied at 3 days — are clear wins once run long
  enough (confirmed from 7 days onward across 3/7/14/30/60-day checks); the
  3-day tie was a duration artifact, not a real limit. `cloudy` and
  `monsoon` (a second, harsher poor-solar scenario added specifically to
  test this) tie exactly at every duration checked, 3 through 60 days — a
  stable, structural limit: the reactive scheduler only tops up on surplus
  that fully covers the pump's rated draw (0.75 kW), and neither scenario
  ever offers such a window before the level-triggered run would fire
  anyway. Zero hard-constraint violations across the extended set at any
  tested duration. **Decision (2026-09-16): closed.** The first close-or-
  not-yet call was made against the weaker 3-day numbers and came down
  "not yet"; re-measured on the 30-day extended set, the case is a clean
  win (3 of 5 scenarios improve, 2 tie for a stated structural reason,
  none regress), and the author confirmed closing on this evidence. See
  `PROJECT_JOURNEY.md`.*

- [x] **Phase 4 — Temporal behaviour** ✅
  Cyclic time encoding, historical slot means, lag/rolling features.
  Exit: `temporal/` produces feature vectors matching section 12's feature
  list; tests for temporal encoding pass.
  *Met: "section 12" is not in this repo (greps clean), so the 18-feature
  list is pinned as code — `ml.features.builder.FEATURE_NAMES` — and
  `test_feature_names_match_the_pinned_spec_exactly_and_in_order` is the
  exit-criterion test. `temporal/context.py` (cyclic time-of-day/
  day-of-week/day-of-year encoding), `temporal/profiles.py` (per-slot
  historical means, weekday/weekend kept separate), `temporal/history.py`
  (time-based, gap-aware lag and rolling features) and
  `ml/features/builder.py` (the 18-value vector) are all implemented.
  `ObservationRepository.history()` — the one `NotImplementedError` in the
  repo explicitly tagged Phase 4 — is also implemented, with an added
  `provenance` filter. 449 tests pass, up from 412. `--run-scenarios`
  reproduces Phase 2's exact figures unchanged, confirming nothing leaked
  into the decision path. `temporal/adaptation.py` stays a stub —
  deliberately out of scope; see the carried-forward note below.*

- [ ] **Phase 5 — Demand ML**
  Mean baseline → linear regression → random forest → gradient boosting.
  Exit: chronological train/val/test split; MAE/RMSE reported for every
  model; final model chosen by measured performance + Pi inference cost,
  not by default.

- [ ] **Phase 6 — Solar forecast**
  Persistence, historical profile, ML if it earns its place.
  Exit: ML only kept if it beats both baselines on held-out data.

- [ ] **Phase 7 — Predictive heuristic**
  Combines demand + solar forecasts without full optimization.
  Exit: beats reactive scheduler on solar fraction in simulation.

- [ ] **Phase 8 — Optimization**
  MILP (OR-Tools/PuLP) with hard constraints + objective from section 30.
  Exit: solver runtime benchmarked; deterministic heuristic fallback exists
  if MILP proves too heavy.

- [ ] **Phase 9 — MPC**
  Receding-horizon replanning, execute-first-action-only loop.
  Exit: re-optimizes every cycle; event-driven re-optimization triggers
  (section 37) implemented and tested.

- [ ] **Phase 10 — Uncertainty**
  P10/P50/P90 forecasts, conservative scheduling (`Q_P90`, `PV_P10`),
  adaptive residual correction, anomaly detection.
  Exit: dynamic safety reserve scales with demand uncertainty; anomaly
  flag reduces scheduling aggressiveness in a test scenario.

- [ ] **Phase 11 — ESP32 integration**
  Newline-delimited JSON over USB serial; telemetry/command/ack protocol.
  Exit: command sent vs. executed tracked as separate states; integration
  tests for serial telemetry + ack pass.

- [ ] **Phase 12 — Raspberry Pi Zero deployment**
  Full inference + scheduler running locally.
  Exit: CPU/RAM/latency measured and within budget for the chosen model
  from Phase 5/8.

- [ ] **Phase 13 — Real tank validation**
  Controlled physical experiments.
  Exit: zero unintentional hard-constraint violations across a defined
  test window; measured vs. predicted tank trajectory logged.

- [ ] **Phase 14 — Experiment comparison**
  All controllers (A–F, section 58) run under identical conditions.
  Exit: comparison report distinguishes Measured/Simulated/Predicted;
  ablation study (section 59) completed.

- [ ] **Phase 15 — Minimal frontend**
  Live Home, Why?, Forecast, History, Experiments, Settings screens.
  Exit: every scheduler decision renders as a structured explanation
  (section 53 JSON → plain language), not raw parameters.

- [ ] **Phase 16 — Generalization**
  Second `FlexibleResource` implementation (e.g. washing machine or geyser
  as a stub/simulated resource) proves the interface generalizes.
  Exit: new resource type runs through the same scheduler code with zero
  changes to `scheduler/`.

---

## Before starting any phase, ask

Does this improve scheduling intelligence, model accuracy, system
robustness, experimental validity, hardware integration, observability, or
usability required to validate the core algorithm? If no — don't add it yet.
