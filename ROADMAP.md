# SuryaSync Roadmap

One phase = one focused Claude Code session (or a tight cluster of them).
Do not start phase N+1 until phase N's exit criteria are met and tested.
Update the checkbox and the "Current phase" line in CLAUDE.md when a phase
closes.

- [ ] **Phase 0 — Architecture**
  Interfaces (`Scheduler`, `FlexibleResource`), config schema, DB schema.
  Exit: directory tree exists, interfaces are stubbed, `config/` loads,
  SQLite schema created, no logic yet.

- [ ] **Phase 1 — Simulator**
  Tank simulator, pump model, demand profiles, solar profiles, grid model.
  Exit: `SimulatedTankResource` runs a multi-day trajectory deterministically;
  unit tests for tank conversion + physical model + pump model pass.

- [ ] **Phase 2 — Conventional control**
  Threshold controller implementing `Scheduler`.
  Exit: runs against the simulator, never violates hard tank constraints
  across the standard scenario set (sunny/cloudy/spike/low-start).

- [ ] **Phase 3 — Solar-reactive control**
  Simple current-surplus scheduler.
  Exit: beats threshold controller on grid-powered pump energy in
  simulation; comparison logged via `analytics/comparison.py`.

- [ ] **Phase 4 — Temporal behaviour**
  Cyclic time encoding, historical slot means, lag/rolling features.
  Exit: `temporal/` produces feature vectors matching section 12's feature
  list; tests for temporal encoding pass.

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
