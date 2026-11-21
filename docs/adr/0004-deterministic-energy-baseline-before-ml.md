# ADR 0004: Deterministic energy baseline before ML

- Status: accepted
- Date: 2026-11-06
- Deciders: project lead

## Context

A later ChargeWaze milestone is a personalized ML consumption model. Reasons to resist starting with ML:

- No training data exists yet. Smartcar readings are not automatically a clean energy dataset — SoC rounding, usable-capacity uncertainty, and charging intervals all contaminate naïve SoC-difference estimates.
- Routing can't ship without an energy model, but it doesn't need a learned one.
- Without a baseline, "the ML model is good" has no meaning.

## Decision

We will:

- Ship a **deterministic energy model** in Phase 4: segment length × per-vehicle consumption baseline, with a piecewise charging curve.
- Make the deterministic model the **fallback that never goes away**, even after ML models ship.
- Only introduce ML after the roadmap's data-quality rules are met (separated driving vs charging intervals, flagged low-quality trips, trip-based chronological splits).
- Judge every ML version against the deterministic baseline on the same test split, with MAE and RMSE as primary metrics plus arrival-SoC error in percentage points.
- Not claim an ML improvement anywhere (README, resume, UI) until the test-set metric beats the baseline.

## Consequences

- Easier: Phase 4 can ship without blocking on ML data collection.
- Easier: a reliable fallback keeps the planner usable when a learned model regresses or when prediction uncertainty is high (Stage D will use this explicitly).
- Easier: ML wins are measurable — they're always "better than the baseline on the same data".
- Harder: we have to maintain two prediction paths once ML ships.
- Harder: tempting shortcuts (pretty UI graphs claiming "AI prediction") are forbidden until a passing comparison exists.

## Alternatives considered

- **Start with ML immediately.** Rejected: we have no data, and nothing to compare against.
- **Only ship ML, no baseline.** Rejected: no fallback, no evaluation anchor, and ML behavior would silently degrade over time without being noticed.
- **Use a public EV consumption dataset as ground truth.** Rejected as primary: vehicle-to-vehicle differences mean public datasets are not reliable ground truth for the specific Toyota. May still be used as a secondary signal.

## References

- [`docs/development-roadmap.md`](../development-roadmap.md#3-machine-learning-plan-post-mvp)
- [`docs/architecture.md`](../architecture.md#6-routing-algorithm)
- [`CLAUDE.md`](../../CLAUDE.md) — "Data and ML" rules
