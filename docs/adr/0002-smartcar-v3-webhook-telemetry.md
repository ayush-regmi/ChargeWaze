# ADR 0002: Smartcar V3 with webhook-based telemetry

- Status: accepted
- Date: 2026-11-06
- Deciders: project lead

## Context

Chargewise needs vehicle data (SoC, range, odometer, charging status) for the 2026 Toyota bZ and, later, for other EVs.

Relevant facts from Smartcar as of planning:

- Smartcar introduced **API V3** in April 2026. It uses an application-level token (client-credentials OAuth), user-scoped requests via `sc-user-id`, standardized signal names, and an improved webhook system.
- V2 is scheduled for deprecation in Q4 2026; Smartcar recommends V3 for new projects.
- Smartcar recommends webhook delivery for ongoing data instead of repeated polling.
- Event frequency depends on what the manufacturer exposes. Smartcar is not a guaranteed high-frequency real-time GPS stream.

These details are from a planning document; implementation must verify them against the official Smartcar documentation at build time.

## Decision

We will:

- Build the Smartcar integration against **API V3**. Ignore V2 tutorials and per-vehicle refresh-token patterns.
- Treat **webhooks as the primary telemetry path**: the backend subscribes each connected vehicle to a Chargewise webhook endpoint after Connect.
- Store a cached `latest_state` on the `vehicles` row, updated by the webhook ingestor. Dashboard reads never trigger live Smartcar calls on the hot path.
- Require every webhook request to pass signature verification before persisting anything.
- Deduplicate events by Smartcar event ID.
- Preserve provider timestamps and raw units on every stored reading.
- Design the UI to show reading freshness and ask the user to confirm starting SoC when telemetry is stale.

## Consequences

- Easier: lower Smartcar request volume; Dashboard latency is independent of Smartcar availability.
- Easier: following the vendor's own recommended direction keeps us on supported APIs.
- Harder: webhooks require a public HTTPS endpoint during development (a tunnel) and robust signature + dedup handling before anything else.
- Harder: we depend on manufacturer event frequency. If the Toyota produces events too rarely for the Dashboard's freshness target, we'll need a polling fallback. This has to be measured in Phase 2 before the Dashboard commits to a freshness threshold.

## Alternatives considered

- **Smartcar V2.** Rejected: deprecating, less uniform API, and a reasonable Chargewise should not launch on a sunset path.
- **Polling-only telemetry.** Rejected as primary: higher request volume, worse latency between vehicle state and UI, and against Smartcar's own guidance. Polling stays available as a fallback if events turn out to be too sparse.
- **Skip Smartcar entirely and go OEM-direct.** Rejected: OEM APIs are not standardized and would lock Chargewise to one manufacturer.

## References

- [`docs/architecture.md`](../architecture.md) — webhook ingestion rules
- [`docs/api-contracts.md`](../api-contracts.md) — `/api/v1/webhooks/smartcar`
- Smartcar API docs — https://smartcar.com/docs
