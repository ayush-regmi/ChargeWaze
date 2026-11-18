# ADR 0009: In-process post-ingest hook for trip inference

- Status: accepted
- Date: 2026-11-18
- Deciders: project lead

## Context

Phase 5 turns telemetry into observed trips ([`phase5_plan.md`](../phase5_plan.md) D1). The roadmap's exit criterion is that real trips produce `observed_trips` rows **without manual intervention**. plan.md's recommendation was to run inference on demand (an endpoint and a script). That only creates trips when someone calls it.

Constraints:

- [`architecture.md`](../architecture.md) §7: no message queues, Redis or background workers in the MVP until load clearly needs them.
- Modules depend down, not across (architecture §3). `trips` already depends on `vehicles`; the webhook path lives in `vehicles` and must not import `trips`.
- Smartcar retries non-2xx webhook deliveries for up to 3 days and expects a quick answer. Inference must not slow the response or make it fail.
- Deliveries are at-least-once and can arrive out of order, and two can be processed at once for the same vehicle.

## Decision

We will run trip inference **in the API process, after the webhook response**, through a small hook registry owned by `vehicles`:

- `modules/vehicles/ingest_hooks.py` holds `register_ingest_hook(fn)` and a `TelemetryIngested` event (vehicle, user, inserted signal codes, `is_simulated`). It imports nothing from other modules.
- When a delivery is accepted and inserts rows, the webhook router schedules every registered hook with FastAPI `BackgroundTasks`. They run after the response is sent. A hook's exception is logged and never reaches Smartcar.
- `trips` registers its hook when `trips/observed_router.py` is imported. The hook ignores events without `odometer`, `soc` or `charging_status`, opens its own DB session, takes a per-vehicle `pg_advisory_xact_lock` so runs for one vehicle are serialized, and re-infers from the newest closed leg onward. Every run is idempotent.
- `POST /api/v1/observed-trips/recompute` and `scripts/infer_trips.py` remain as catch-up paths.

## Consequences

- Trips appear a few seconds after the webhook that ends them, with no page visit or button. Webhook latency doesn't change.
- The dependency direction stays `trips → vehicles`: `vehicles` calls functions it was handed, not `trips` code.
- Background tasks are lost if the process restarts before they run. Because each run re-infers from the newest closed leg, the next delivery (or the recompute endpoint) catches up; nothing is lost permanently.
- With several API instances, two runs for one vehicle can happen at once on different instances; the advisory lock serializes them in Postgres.
- Hook registration is an import-time side effect. A test checks it's registered exactly once, and test clients run background tasks synchronously, so the behaviour is covered end to end.
- If inference ever gets slow (long histories, many vehicles), the same hook can enqueue a job instead; only the registry's caller changes.

## Alternatives considered

- **On demand only (plan.md recommendation):** simplest, but trips appear only when someone opens a page or runs a script. Fails the "without manual intervention" criterion in spirit.
- **Run inference inside the webhook request:** adds inference time and failure modes to Smartcar's delivery; a bug in inference would cause retries for days.
- **`vehicles` imports `trips` directly:** breaks the "depend down" rule and couples the webhook path to a later-phase module.
- **Cron or a worker with a queue:** extra infrastructure the MVP avoids (architecture §7). Revisit if load or reliability needs it (trigger: lost runs or inference latency becoming visible).
- **Database trigger or `LISTEN/NOTIFY`:** puts business logic in SQL or needs a long-lived listener process; no simpler than the hook.

## References

- FastAPI, Background Tasks: https://fastapi.tiangolo.com/tutorial/background-tasks/
- PostgreSQL, Advisory Locks: https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
- Smartcar, Webhook event types (delivery guarantees and retries): https://smartcar.com/docs/api-reference/webhooks/event-types
