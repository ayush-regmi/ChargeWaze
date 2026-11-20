# ADR 0010: On-demand vehicle refresh to fill webhook gaps

- Status: accepted
- Date: 2026-11-19
- Deciders: project lead

## Context

[ADR 0002](0002-smartcar-v3-webhook-telemetry.md) makes Smartcar webhooks the only telemetry path. It leaves one question open: whether Chargewise needs a polling fallback.

On 2026-11-18 the real 2026 Toyota bZ sent 12 readings in the 20 minutes after Connect, then nothing. The local API and the ngrok tunnel had been stopped. A day later the Dashboard said "Data may be stale — last update 29 hours ago", and the user had no way to fix it.

The readings were not lost because the car stopped reporting. They were lost because Chargewise wasn't there to receive them. Smartcar retries failed deliveries for up to 3 days, but only for events it tried to send while our endpoint was failing.

Constraints:

- **Dashboard reads never call Smartcar.** ADR 0002 says so, and `GET /vehicles/{id}/state` is guaranteed to read only the cached state.
- **No workers.** [`architecture.md`](../architecture.md) §7 rules out workers, cron and queues for the MVP.
- **Smartcar caps requests.** Smartcar limits requests per vehicle per month, and the cap depends on the plan. The [API limits](https://smartcar.com/docs/help/api-limits) page gives 500 per vehicle per month only as an example, so our plan's real cap must be read from the Smartcar Dashboard. There is also an app-wide bucket of 120 requests that refills at 2 per minute. Throttled calls return `429` with type `RATE_LIMIT` ([rate-limit errors](https://smartcar.com/docs/errors/api-errors/v3/rate-limit-errors)). The per-vehicle throttle sends `retry-after` in seconds.
- **We still don't know how often the bZ reports.** Its event cadence hasn't been measured ([`phase2_plan.md`](../phase2_plan.md) F8 step 2).

## Decision

We add an **on-demand, user-triggered refresh** and no scheduled polling.

- **The endpoint.** `POST /api/v1/vehicles/{id}/refresh` makes one `GET /v3/vehicles/{id}/signals` call through the existing `SmartcarClient`. The UI offers it as a "Refresh" button only where the data is stale: the Dashboard's stale banner, its "awaiting first update" state, and the vehicle page.
- **The read path doesn't change.** `GET .../state` stays cache-only. A test asserts that it never calls Smartcar.
- **Per-vehicle rate limit, stored in Postgres** (`vehicle_refreshes`), so it holds across API instances:
  - one refresh per `SMARTCAR_REFRESH_MIN_INTERVAL_MINUTES` (default 15);
  - at most `SMARTCAR_REFRESH_MAX_PER_DAY` (default 8) in any rolling 24 hours;
  - the worst case is 8 × 31 = 248 requests per vehicle per month;
  - an attempt counts once Chargewise is about to call Smartcar, whether or not the call succeeds;
  - the vehicle row is locked while the limit is checked, so concurrent clicks serialize. It is locked again, and reloaded, before the results are stored. A webhook or disconnect that lands during the Smartcar call is then merged or respected, not overwritten by the copy loaded before the call.
- **Same ingest rules as webhooks.** Results go through the same code as webhook deliveries (`telemetry_service._store_signals`):
  - same normalization;
  - `recorded_at` is the OEM timestamp;
  - `latest_state` only moves forward;
  - `supported_signals` only grows;
  - `source = 'smartcar'`.
- **Dedup without an event ID.** A refresh has no Smartcar event ID, so it uses three rules:
  - `source_event_id = 'poll:<vehicle_id>:<recorded_at>'`. Refreshing the same OEM reading again hits the existing unique index and inserts nothing. The vehicle is in the ID because that index is global, so two cars reporting at the same second must not collide.
  - A refresh skips a reading already stored from Smartcar with the same `(vehicle, signal_code, recorded_at)`.
  - A webhook skips a reading that a refresh already stored. This covers Smartcar retrying a delivery it couldn't make while we were down. Webhook-vs-webhook behaviour is unchanged.
- **Trip inference runs too.** New rows schedule the [ADR 0009](0009-in-process-post-ingest-hook-for-trip-inference.md) post-ingest hooks after the response, exactly as the webhook does.
- **Smartcar throttling becomes our own error.** A Smartcar `429 RATE_LIMIT` is now `429 SMARTCAR_RATE_LIMITED` everywhere, with `retry_after_seconds` when Smartcar sent one. Before this change it was a `502 SMARTCAR_ERROR`. The monthly `BILLING` cap stays a `502`.

## Consequences

- **The user can recover.** After an outage, the user can bring the Dashboard up to date without reconnecting the car.
- **Reads stay unchanged.** Dashboard latency still doesn't depend on Smartcar.
- **Smartcar spend has a ceiling.** It is bounded per vehicle, and both limits are settings.
- **The log is evidence.** `vehicle_refreshes` records how often refreshes found data that webhooks had missed. That is evidence for the scheduled-polling decision.
- **The history stays incomplete.** A refresh returns Smartcar's *current* snapshot, not the readings missed during the outage. Trip inference sees one jump across the gap: one long leg, or a charge that shows up as a single SoC rise.
- **"Refreshed" can still mean stale.** If the car itself hasn't reported since, the OEM timestamp doesn't move. The response says `inserted: 0`, the state stays `is_stale: true`, and the UI says the car hasn't reported anything newer, instead of implying the data is fresh.
- **Failures cost the allowance.** A failed Smartcar call still counts against the limit. A misconfigured server (`SMARTCAR_NOT_CONFIGURED`) doesn't, because it never reaches Smartcar.
- **Polling is still undecided.** Scheduled background polling is still open. Revisit it once the bZ's cadence is measured (F8 step 2), or if users refresh often.

## Alternatives considered

- **Refresh inside `GET .../state` when the cache is older than N hours.** Rejected: it breaks ADR 0002's read guarantee, makes page loads depend on Smartcar latency and errors, and spends requests on every stale page view.
- **Scheduled polling (cron or a worker).** Rejected for now: architecture §7 rules out the infrastructure, and the need isn't proven until cadence is measured. A poller would call the same `ingest_polled_signals` function.
- **A `last_refreshed_at` column instead of a log table.** Simpler, but it can't enforce a daily cap, and it leaves no evidence for the polling decision.
- **A new telemetry `source` (`smartcar_poll`).** Rejected: it changes the source CHECK and every reader. The `poll:` event-ID prefix already tells the two apart.

## References

- [`docs/api-contracts.md`](../api-contracts.md) §3.5a
- [`docs/database-design.md`](../database-design.md) §3.5, §3.5a
- Smartcar, API limits: https://smartcar.com/docs/help/api-limits
- Smartcar, V3 rate-limit errors: https://smartcar.com/docs/errors/api-errors/v3/rate-limit-errors
