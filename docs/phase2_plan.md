# Phase 2 Plan — Vehicle Integration

Status: **Approved 2026-11-13** — all recommendations in §2 (D1–D12) and the F4 "local disconnect wins" behaviour accepted · Created: 2026-11-13 · Phase owner: project lead

This is the working plan for [Phase 2 of the roadmap](development-roadmap.md#phase-2--vehicle-integration-weeks-34). It breaks the phase into features that each ship as their own vertical slice (DB → backend → frontend → tests), on their own branch, with their own acceptance criteria. We build and test them one at a time, in the order below.

Sources this plan is derived from: [`development-roadmap.md`](development-roadmap.md), [`product-requirements.md`](product-requirements.md) §4.1 / §4.4 / §7 / §8 / §9, [`architecture.md`](architecture.md) §4 / §5 / §7, [`api-contracts.md`](api-contracts.md) §3.2–3.6 / §3.12, [`database-design.md`](database-design.md) §3.3–3.5, and [ADR 0002](adr/0002-smartcar-v3-webhook-telemetry.md).

---

## 1. Goal and exit criteria

**Goal:** the real 2026 Toyota bZ is connected through Smartcar V3, telemetry arrives by signed webhook, and the Dashboard shows real battery state from the cached latest state.

Phase 2 is done when all of these are true (copied from the roadmap, plus the PRD §9 Garage/Dashboard cases):

| # | Exit criterion | Verified by |
|---|---|---|
| E1 | The real Toyota's supported data appears on the Dashboard with timestamps. | Manual run against the real vehicle, screenshot in the PR. |
| E2 | Dashboard values survive a page reload, served from cached `latest_state` (no live Smartcar call). | Integration test asserting the Smartcar adapter is never called by `GET /vehicles/{id}/state`; manual reload. |
| E3 | Disconnecting removes the vehicle from Garage and stops accepting its telemetry. | Integration test + manual run. |
| E4 | Tests cover: invalid signature (rejected), duplicate event (idempotent), out-of-order event (latest = max `recorded_at`), stale-data flag. | pytest, in CI. |
| E5 | Garage and Dashboard handle: first connect with no telemetry yet, Smartcar authorization failure, duplicate connect. | pytest + Vitest + one Playwright flow. |
| E6 | No Smartcar secret reaches the browser bundle. | Build-output grep check in CI. |
| E7 | CI green on `main` (ruff, mypy, pytest, Alembic round-trip, lint, typecheck, Vitest, Playwright). | GitHub Actions. |

---

## 2. Decisions and doc conflicts to settle before coding

The docs disagree or are silent in a few places. Per `CLAUDE.md` these need a decision before the affected feature starts. **All recommendations below were accepted on 2026-11-13.** D1, D2 and D10 still depend on facts F0 confirms; if F0 contradicts them, this table gets updated before F1.

**F0 docs pass (2026-11-13), see [`smartcar-v3-notes.md`](smartcar-v3-notes.md):**
- **D1 resolved:** V3 uses one application token + `sc-user-id`; no per-user tokens to store. No `smartcar_connections` table; F1 adds a nullable `vehicles.smartcar_connection_id` for disconnect.
- **D3 confirmed:** vehicle IDs are only known after the redirect (via the Connections API).
- **D10 confirmed:** plain HTTP everywhere; no SDK.
- **F2/F3/F5 changes:** Connect uses `response_type=none` (no code exchange); the webhook HMAC key is the Application Management Token (`SMARTCAR_WEBHOOK_SECRET` → `SMARTCAR_MANAGEMENT_TOKEN`); F5 must answer the `VERIFY` challenge; a signal's `recorded_at` is its `oemUpdatedAt`.

**F0 real-vehicle run (2026-11-13):** F0 is **done**. The 2026 bZ returns SoC, range (km), odometer (km) and charging status through V3. Consequences:
- **F3:** signal data can be empty for the first minutes after Connect; `supported_signals` must be recomputed when data first arrives, and the UI shows "Awaiting first update".
- **F6:** `charging_power_kw` is `null` on the bZ (no `charge-wattage`); the UI shows it as unavailable.
- **D8:** Smartcar gives no battery capacity for the bZ, so the static catalog + user edit (F7) is the only source.
- **F5:** the live `VERIFY` handshake, a real `VEHICLE_STATE` capture, cadence measurement and "webhooks stop after disconnect" moved here from F0 (F5 needs a public tunnel and the real endpoint anyway).
- **Config:** `SMARTCAR_MODE` must be `live` or `simulated` (F2 validates it); `SMARTCAR_APPLICATION_ID` is separate from `SMARTCAR_CLIENT_ID`.

| # | Issue | Where | Recommendation |
|---|---|---|---|
| D1 | **Where the Smartcar connection credential lives.** Architecture §4 says the backend exchanges the code "for the Smartcar user token", but `database-design.md` has no table or column for tokens. Under V3 we may only need the application token + `sc-user-id`, or we may need to store per-user tokens. | arch §4, db §3.3 | Decide from the spike (F0). If tokens must be stored, add a `smartcar_connections` table (one row per Chargewise user × Smartcar user) with tokens **encrypted at rest** (Fernet key from env), and document it in `database-design.md`. |
| D2 | **Connect `state` nonce storage.** API §3.3 says "the backend stores a short-lived state nonce", but there's no table. | api §3.3 | Stateless signed state: a short-lived (10 min) HS256 JWT signed with a new `SMARTCAR_STATE_SECRET`, carrying `user_id`, `redirect_after` and a random `nonce`. Uses PyJWT (already a dependency). No table needed. *(Post-F0: there is no code exchange to make `state` single-use; a replayed `state` within its 10 min lifetime can only re-run the callback for the same user, which D3's duplicate check makes harmless. **F3 resolution:** Connect completes on the web origin through an authenticated endpoint that requires the signed-in user to own `state` (so an attacker's Connect link can't be completed in a victim's browser), and only connections whose `externalId` matches the `state` user are accepted (so a valid `state` can't be paired with someone else's Smartcar `user_id`).)* |
| D3 | **Duplicate connect error.** API §3.3 lists `CONFLICT` on `POST /vehicles/connect`, but we can only know it's a duplicate in the callback, after Smartcar returns vehicle IDs. | api §3.3 | `POST /connect` never returns `CONFLICT`. The callback redirects to `/garage?connect=already_connected` (and `connect=denied`, `connect=error`, `connect=success`). Update the contract text. |
| D4 | **Reconnecting a disconnected vehicle.** Disconnect is a soft delete, but `UNIQUE (user_id, smartcar_vehicle_id)` blocks inserting the same car again. | db §3.3 | Reconnect **reactivates** the existing row (`connection_status='connected'`, new `connected_at`, re-subscribe webhook). Duplicate only means "already connected *and* status is connected". |
| D5 | **Out-of-order semantics.** Arch §5 says "latest state is the row with the maximum `recorded_at`". Telemetry is stored per signal, and one webhook may carry only some signals. | arch §5, api §3.5 | Apply the rule **per signal**: `latest_state.signals.<code>` only moves forward if the incoming `recorded_at` is ≥ the stored one for that signal. Store a per-signal `recorded_at` in `latest_state`; the top-level `recorded_at` in the API response is the max across signals. Add per-signal `recorded_at` to the `/state` response as an additive field. |
| D6 | **Primary vehicle and spec editing.** PRD §4.4 asks for "choose a primary vehicle" and "edit usable capacity / adapters", but neither has an endpoint in `api-contracts.md`, and the roadmap Phase 2 scope doesn't list them. `user_preferences.primary_vehicle_id` also has no FK yet. | prd §4.4, db §3.2 | Phase 2 adds the FK (`ON DELETE SET NULL`). Dashboard uses the primary vehicle, else the most recently connected one. Ship **F7 (primary vehicle + spec editing)** as an optional last feature in Phase 2 with `PATCH /api/v1/me/preferences` and `PATCH /api/v1/vehicles/{id}/specs`; they're needed before Phase 4 anyway. |
| D7 | **Location permission.** Smartcar can expose GPS location; nothing in Phase 2 needs it, and location is sensitive (PRD §8). | prd §8 | Do **not** request location scope in Phase 2. Revisit in Phase 5 (observed trips). |
| D8 | **Default vehicle specs.** `vehicle_specs.usable_capacity_kwh` is "defaults seeded per make/model", but we can't fabricate numbers. | db §3.4, prd §7 | A small static catalog (`vehicles/spec_catalog.py`) keyed by make/model/year with a `source` note per entry. Unknown models get `NULL` capacity and the UI asks the user to fill it in (F7). Prefer a Smartcar battery-capacity signal if the spike shows it exists. |
| D9 | **Tunnel tool.** Arch §7 says "a temporary HTTPS tunnel" without picking one. | arch §7 | **ngrok with a free static domain** so the webhook URL registered in the Smartcar dashboard stays the same across restarts. Alternative: `cloudflared` quick tunnel (no account, but the URL changes every run). |
| D10 | **Smartcar SDK vs plain HTTP.** | guidelines "avoid unnecessary dependencies" | Plain `httpx` (already a dependency) behind the adapter. Only adopt the official SDK if the spike shows it has first-class V3 support that saves real work. |
| D11 | **Freshness threshold.** Arch §5 says 15 min "subject to Smartcar plan reality". | arch §5, ADR 0002 | Make it a setting (`TELEMETRY_STALE_AFTER_MINUTES`, default 15). Measure the bZ's real event cadence during F0/F6 and record the measured number in the roadmap risk table before Phase 2 closes. |
| D12 | **Webhook for a disconnected / unknown vehicle.** | arch §5 | Verify signature, then return **200** with `accepted: false` and do not persist. Returning 4xx/5xx would make Smartcar retry forever. Log the event ID only. |

---

## 3. Feature breakdown and order

Each feature is one branch (`feature/phase2-<slug>`), one PR, and is merged before the next starts unless noted. Order follows the roadmap's "Garage slice → Dashboard slice" and keeps each PR independently testable.

| # | Feature | Branch | Depends on | Size |
|---|---|---|---|---|
| F0 | Smartcar V3 spike against the real Toyota (no production code) | `feature/phase2-smartcar-spike` | — | S |
| F1 | Vehicle data model + migrations | `feature/phase2-vehicle-schema` | F0 | S |
| F2 | Smartcar adapter (`integrations/smartcar`) + fixtures | `feature/phase2-smartcar-adapter` | F0, F1 | M |
| F3 | Connect flow: `POST /vehicles/connect` + `/connect/complete` + web callback | `feature/phase2-smartcar-connect` | F2 | M |
| F4 | Garage read + disconnect: `GET /vehicles`, `GET /vehicles/{id}`, `DELETE /vehicles/{id}` + Garage UI | `feature/phase2-garage` | F3 | M |
| F5 | Webhook ingestor: `POST /webhooks/smartcar` + local tunnel docs | `feature/phase2-webhook-ingest` | F2, F1 | L |
| F6 | Vehicle state + Dashboard: `GET /vehicles/{id}/state` + Dashboard UI | `feature/phase2-dashboard-state` | F5 | M |
| F7 | (Optional) Primary vehicle + spec editing | `feature/phase2-vehicle-settings` | F4 | S |
| F8 | Phase close-out: real-car verification, docs, E2E | `feature/phase2-closeout` | all | S |

F4 and F5 don't touch the same files and could run in parallel once F3 lands; we'll still do them one at a time unless you say otherwise.

---

## 4. Feature details

### F0 — Smartcar V3 spike (roadmap "Immediate action 1")

**Why first:** the roadmap says if V3 doesn't deliver what the PRD assumes, the plan must change before we build. Everything in F2–F6 depends on facts we have not verified.

**Steps**
1. Create a Smartcar developer app; set mode `test` first, then `live` for the real car. Record the redirect URI and webhook settings (no secrets in the repo).
2. Read the official V3 docs and write down, with links:
   - Application token: endpoint, grant type, lifetime.
   - Connect URL format, required/optional scopes (permissions) for SoC, range, odometer, charging status; how `state` is round-tripped.
   - Code exchange: what it returns (user token? `sc-user-id`? vehicle list?) → answers **D1**.
   - How to list a user's vehicles and fetch make/model/year.
   - Signal names for battery %, range, odometer, charging state, charging power; their units.
   - Webhook: subscribe/unsubscribe API, payload shape, event ID field, timestamp field, signature header + algorithm, any verification/challenge handshake.
   - Disconnect/revoke API.
   - Rate limits and plan quotas.
3. With the spike script [`services/api/scripts/smartcar_spike.py`](../services/api/scripts/smartcar_spike.py) (stdlib only; outputs in gitignored `scripts/spike_out/`), connect the real bZ, make **one** authorized read, and save the redacted responses. The script stays in the repo so F8 can reuse its webhook receiver to measure event cadence.
4. Point a test webhook at a tunnel and capture one real signed delivery (redact IDs).
5. Note which signals the bZ actually supports and the observed event cadence (→ **D11**).

**Deliverables**
- `docs/smartcar-v3-notes.md`: verified facts + links + open gaps. Any change to the plan or ADR 0002 goes here first.
- Redacted real payloads that seed the fixtures in F2.
- An update to this plan if anything above is wrong.

**Acceptance:** one real read of the bZ's SoC has been made; D1, D2, D10 have answers; the list of supported signals is written down.

---

### F1 — Vehicle data model + migrations

**Backend**
- New module `services/api/app/modules/vehicles/` with `models.py` for `Vehicle`, `VehicleSpecs`, `VehicleTelemetry`, matching [`database-design.md`](database-design.md) §3.3–3.5 exactly:
  - `vehicles`: all columns in §3.3; `CHECK connection_status IN ('connected','disconnected','pending')`; partial unique index `(user_id, smartcar_vehicle_id) WHERE smartcar_vehicle_id IS NOT NULL`; index `(user_id, connection_status)`. Add `disconnected_at timestamptz NULL` (needed for E3 auditing; document it).
  - `vehicle_specs`: §3.4, PK = FK to `vehicles.id` `ON DELETE CASCADE`.
  - `vehicle_telemetry`: §3.5 with both indexes and the partial unique dedup index `(source, source_event_id, signal_code) WHERE source_event_id IS NOT NULL`.
  - If D1 says tokens must be stored: `smartcar_connections` table.
- Add the `user_preferences.primary_vehicle_id → vehicles.id ON DELETE SET NULL` FK (D6).
- Alembic migration `20261113_01_vehicles.py`: creates the tables, **enables RLS on each new table** (enforced by `tests/test_migrations.py`), and has a working `downgrade()`.
- Register the models in `alembic/env.py`.
- Update `database-design.md` with any added columns/tables.

**Tests**
- Existing RLS test covers the new tables automatically.
- New `tests/test_vehicle_models.py`: unique-connect constraint, dedup unique index, cascade delete from `profiles`, status check constraint.
- Alembic round-trip in CI (already present).

**Acceptance:** `uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head` passes locally and in CI; RLS test passes.

---

### F2 — Smartcar adapter + fixtures

*Updated 2026-11-13 after F0 — see [`smartcar-v3-notes.md`](smartcar-v3-notes.md). Connect uses `response_type=none`, so there is no code exchange; webhook subscription is deferred to F5.*

**Backend:** `services/api/app/integrations/smartcar/`
- `client.py` — `SmartcarClient` class using a synchronous `httpx.Client` (routes and DB sessions are sync). The **only** place that talks to Smartcar. Methods:
  - `get_application_token()` — client-credentials token, cached until 60 s before expiry; no refresh token exists in V3.
  - `build_connect_url(state, scopes, external_id)` — `application_id`, `response_type=none`, `redirect_uri`, `scope`, `state`, `mode`, `external_id`.
  - `list_connections(sc_user_id)` — `GET /v3/connections?filter[userId]=…`, follows page numbers until `totalCount` is reached.
  - `get_vehicle(sc_user_id, vehicle_id)` / `get_signals(sc_user_id, vehicle_id)`.
  - `delete_connection(connection_id)` — `DELETE /v3/connections/{id}`.
  - *Not in F2:* webhook subscribe/unsubscribe. The V3 subscribe call is unverified (notes §5); F5 checks it against the live endpoint and adds it then. Until then vehicles are enrolled via the Dashboard's auto-subscribe.
- `signatures.py` — `verify_webhook_signature(raw_body, headers, management_token) -> bool`: hex HMAC-SHA256 of the raw body in `SC-Signature`, compared with `hmac.compare_digest`. `verify_challenge_response(challenge, management_token)` for the `VERIFY` handshake — only called after the signature check passes.
- `schemas.py` — Pydantic models for connections, vehicles, signals and webhook payloads. `normalize_signals(...) -> list[NormalizedSignal]` maps Smartcar codes to ours (`soc`, `range`, `odometer`, `charging_status`, `charging_status_detail`, `charging_power`, `charge_limit`, `cable_connected`) and keeps the raw `body.unit`. `ERROR`/`UNAVAILABLE` signals become missing values (never a value). `recorded_at` = `oemUpdatedAt`, fallback `retrievedAt`; timestamps accept ISO-8601 or epoch ms. `supported_signal_codes(...)` excludes `VEHICLE_NOT_CAPABLE` and `UNKNOWN` errors.
- `errors.py` — timeouts → `ApiError("SMARTCAR_UNREACHABLE", 504)`; connection errors / 5xx → `SMARTCAR_UNREACHABLE` 502; other 4xx → `SMARTCAR_ERROR` 502 with Smartcar's error code in `details`.
- Structured log line per request: provider, method, endpoint template, duration, status (arch §9). Never log tokens, user/vehicle IDs, pagination links (they embed the user ID) or coordinates.
- Settings in `core/config.py`: `smartcar_client_id`, `smartcar_client_secret`, `smartcar_application_id`, `smartcar_redirect_uri`, `smartcar_webhook_id`, `smartcar_management_token`, `smartcar_mode` (`live` | `simulated`, validated), `smartcar_state_secret` (D2), `smartcar_api_base_url`, `smartcar_auth_url`, `smartcar_connect_url`, `smartcar_timeout_seconds`, `telemetry_stale_after_minutes`. `SMARTCAR_WEBHOOK_SECRET` is removed from `services/api/.env.example`.
- Dependency injection: `get_smartcar_client()` FastAPI dependency so tests can override it with a fake.

**Fixtures:** `fixtures/smartcar/`
- From F0's redacted real responses: `connections_list.json`, `vehicle_bz.json`, `signals_bz.json`.
- Synthetic, built from the documented shapes (labelled as such in the README until F5 captures real ones): `token_response.json`, `webhook_vehicle_state.json` + `.signature`, `webhook_out_of_order_older.json`, `webhook_unknown_signal.json`, `webhook_verify_challenge.json` + `.signature`.
- `fixtures/smartcar/README.md` explaining provenance and the test-only management token.

**Tests** (`tests/integrations/test_smartcar_*.py`), using `httpx.MockTransport` — no network:
- Token caching and refresh; token request uses client credentials.
- Connect URL contains application ID, `response_type=none`, redirect URI, scopes, state, mode, external ID.
- Connections listing parses the fixture and follows pagination.
- Signature: valid passes; tampered body fails; wrong secret fails; missing header fails. VERIFY challenge response matches the documented HMAC.
- Normalization: the real bZ signals map to codes/units; `ERROR` signals are missing; unknown signals preserved in `raw_payload`; supported-signal rules.
- Timeout → 504 `SMARTCAR_UNREACHABLE`; 5xx → 502.
- Settings reject `SMARTCAR_MODE=test`.

**Acceptance:** adapter fully covered by fixture tests; nothing outside `integrations/smartcar` imports `httpx` for Smartcar.

---

### F3 — Connect flow (Garage slice, part 1)

*Updated 2026-11-14 at implementation: there is no code exchange (`response_type=none`), and webhook subscription moved to F5 (as in F2). After PR review, the Smartcar redirect lands on the **web app** instead of a backend callback, so completion can check the signed-in user owns `state` (otherwise an attacker's Connect link, completed by a victim, would attach the victim's car to the attacker).*

**Backend** (`modules/vehicles/`)
- `POST /api/v1/vehicles/connect` (auth required)
  - Body `{ "redirect_after": "/garage" }` (optional); `redirect_after` must be a same-origin path (`redirects.py`, stricter than `safeNextPath`: rejects instead of normalizing). Unsafe → 422 `VALIDATION_ERROR`.
  - Returns `{ authorization_url, state, expires_at }` per api §3.3. Creates the caller's profile row if missing. 503 `SMARTCAR_NOT_CONFIGURED` if `SMARTCAR_STATE_SECRET` is shorter than 32 bytes.
- `POST /api/v1/vehicles/connect/complete` (auth required), body `{ state, user_id, external_id, error }` → `{ result, redirect_after }`:
  1. Verify the signed `state` (D2: HS256, 10 min, `iss`/`aud` checked); invalid/expired → `error` with `/garage`.
  2. `state` user ≠ signed-in user → `error` with `/garage`; Smartcar is not called.
  3. `error=access_denied` → `denied`; any other `error` → `error`.
  4. Missing `user_id`, or an `external_id` that isn't the `state` user → `error`.
  5. `list_connections(user_id)`; keep only `ACTIVE` connections whose `user.externalId` is the `state` user. None → `error`.
  6. Per vehicle: active on another Chargewise user → refused; already `connected` for this user → duplicate; `disconnected`/`pending` → reactivate the same row (D4); else insert `vehicles` (`connected`) + `vehicle_specs` from the catalog (D8). Make/model/year come from the connection; `supported_signals` from a best-effort `get_signals` (often `[]` right after Connect; F5/F6 recompute).
  7. `success` (anything added), `already_connected` (all duplicates) or `error`. Smartcar unreachable → `error`, nothing written.
  - Logs carry outcomes and counts only, never Smartcar IDs.
- `SMARTCAR_REDIRECT_URI` now defaults to `http://localhost:3000/garage/connect/callback`; it must be allowed in the Smartcar Dashboard. Webhook enrolment until F5: Smartcar Dashboard auto-subscribe.
- Fixed in passing: the `VALIDATION_ERROR` handler crashed on custom-validator errors (non-serializable `ctx`) and used a deprecated Starlette constant.

**Frontend** (`apps/web`)
- Garage page: "Add vehicle" form → server action calls `POST /vehicles/connect` with the user's token → `redirect(authorization_url)`; failure → `?connect=error`.
- `/garage/connect/callback` route handler: forwards Smartcar's query to `/connect/complete` with the current session's token, then redirects to `redirect_after` (re-checked with `safeNextPath`) plus `connect=<result>`. Signed out → sign-in.
- `ConnectBanner` reads `?connect=`: success, already connected, denied, error. Unknown values render nothing.

**Tests**
- pytest: connect URL + signed state, default redirect, unauthenticated, unsafe `redirect_after` cases, missing secret; completion unauthenticated, **`state` of another signed-in user (link-sharing attack)**, missing `state`, happy path, profile auto-create, `redirect_after` kept, empty signals, signal read failure, denied, other error, bad/tampered/expired state, missing `user_id`, `external_id` mismatch, **connection belonging to another Chargewise user**, inactive connection, Smartcar unreachable, duplicate, reconnect reactivates the same row, car active on another user, forged unsafe redirect, no Smartcar IDs in logs. Unit tests for redirect rules, state and catalog lookup.
- Vitest: banner for each `connect` value; unknown values (incl. `__proto__`) render nothing; `connectResultUrl` keeps query/fragment and never leaves our origin.

**Acceptance:** with a real Smartcar app, a user can click Add Vehicle, authorize, and land back on Garage with a success banner and a `vehicles` row; duplicate and denied paths show the right banner. *(Manual check with the Toyota still to do.)*

---

### F4 — Garage list, detail, disconnect (Garage slice, part 2)

**Backend** (shipped)
- `GET /api/v1/vehicles` → api §3.2: the caller's `connected` vehicles, most recently connected first; `?include_disconnected=true` adds the rest. `last_state_recorded_at` / `is_stale` come from `latest_state` (`modules/vehicles/latest_state.py`) and `TELEMETRY_STALE_AFTER_MINUTES` (D11).
- `GET /api/v1/vehicles/{id}` → api §3.4 with `specs`, plus the additive `last_state_recorded_at`, `is_stale`, `disconnected_at`. Disconnected vehicles are still readable by their owner.
- `DELETE /api/v1/vehicles/{id}` → api §3.6: mark `disconnected` + `disconnected_at` first, then `delete_connection`; 204 either way ("local disconnect wins", approved 2026-11-13). `smartcar_connection_id` is cleared only when Smartcar confirms (or answers 404), so a disconnected row that still has one is a revocation owed; the retry job is a follow-up. Idempotent: a second DELETE doesn't call Smartcar.
- **Webhook unsubscribe moved to F5:** a per-vehicle V3 unsubscribe isn't confirmed (`smartcar-v3-notes.md` §5). Telemetry for a disconnected vehicle is already refused by D12.
- **`latest_state` shape defined here** (`database-design.md` §3.3) so F4 can read it; F5 writes it.
- Ownership: every handler loads by `(id, user_id)`; another user's vehicle → **404**. Logs carry the revocation outcome only, never Smartcar IDs.
- Logic in `modules/vehicles/garage.py`, models in `schemas.py`; the TS client is regenerated from OpenAPI in CI.

**Frontend** (shipped)
- shadcn/ui is set up (`components.json`, Radix base) with `button` and `alert-dialog` only. Dark mode stays on `prefers-color-scheme`, so the generated `.dark` variant was not adopted. The `shadcn` CLI is not a dependency; add components with `npx shadcn@latest add <name>`.
- Garage page lists vehicle cards: title, status, freshness badge (Up to date / Stale / Awaiting first update), last update in the viewer's local time, supported signals; empty state "No vehicles yet".
- `/garage/[id]`: status, freshness, signals, read-only specs ("Not set" when unknown).
- Disconnect: `AlertDialog` confirm → `disconnectVehicle` server action → redirect to `/garage`; errors stay in the dialog.
- The `(app)` layout's navs are wrapped in Suspense (static fallback without the active highlight): `usePathname()` outside Suspense blocked prerendering the new dynamic route.

**Tests**
- pytest (`test_vehicle_garage.py`, `test_latest_state.py`): auth required; only the caller's vehicles; default vs `include_disconnected`; ordering; no telemetry / fresh / stale / per-signal max / malformed `latest_state`; no Smartcar call on reads; detail with specs, null capacity, no specs, disconnected; another user's vehicle → 404 on GET and DELETE (and untouched); unknown id → 404; non-UUID → 422; disconnect revokes + hides from the list + keeps the row; Smartcar 500/401/unreachable/not configured still disconnects locally and keeps the connection ID; Smartcar 404 counts as revoked; no connection ID skips Smartcar; idempotent; no Smartcar IDs in logs.
- Vitest: vehicle card (signals, link, up-to-date, stale, awaiting, no button when disconnected), empty state, specs panel, confirm dialog (cancel doesn't call the action; failure shows the error and keeps the dialog open), label/freshness helpers.

**Acceptance:** Garage lists the connected car with real supported signals; disconnecting removes it from the list. *(Manual check with the Toyota still to do.)*

---

### F5 — Webhook ingestor (Dashboard slice, part 1)

*Updated 2026-11-15 at implementation:*
- *The vehicle is matched on the Smartcar vehicle ID and user ID together. A `pending` vehicle counts as not connected.*
- *Errored signals (from `VEHICLE_STATE` or `VEHICLE_ERROR`) and unmapped signals are stored with `value` `NULL` and never change `latest_state`.*
- *Text signals keep their text in `latest_state`, and booleans are stored as `true`/`false`.*
- *`supported_signals` only grows, using the codes that report data.*
- *A missing `eventId` gets 400. A signal with no timestamp falls back to `meta.deliveredAt`, and is skipped if that's missing too.*
- *Unknown event types, and a `VEHICLE_ERROR` that doesn't parse, get 200 `accepted:false`.*
- *Webhook subscribe/unsubscribe is still deferred, because the V3 calls are unverified. The Dashboard's auto-subscribe enrols vehicles, and D12 refuses telemetry for disconnected ones.*
- *The concurrency guard (`FOR UPDATE`) is in place but has no automated test, because the test DB runs each test inside one transaction.*

**Backend**
- `POST /api/v1/webhooks/smartcar` (no Supabase auth). Steps exactly per arch §5:
  1. Read the **raw body** (before JSON parsing) and verify the signature. Fail → 401 `WEBHOOK_SIGNATURE_INVALID`, nothing persisted, nothing logged except the reason.
  2. If `eventType == "VERIFY"`, answer `{"challenge": hmac_hex(management_token, data.challenge)}` and return. Only reached after step 1 passes: never answer an unsigned challenge (it would act as an HMAC oracle for forging signatures).
  3. Parse with the F2 Pydantic schema; malformed → 400 (signed but bad payload won't fix itself on retry).
  4. Look up the vehicle by `smartcar_vehicle_id`. Unknown or `disconnected` → 200 `{accepted: false}` (D12).
  5. In **one DB transaction**: insert one `vehicle_telemetry` row per signal with `INSERT … ON CONFLICT DO NOTHING` on the dedup index; then update `vehicles.latest_state` per signal only where incoming `recorded_at` ≥ stored (D5). Use `SELECT … FOR UPDATE` on the vehicle row so concurrent deliveries don't lose updates.
  6. If every signal was a duplicate → 200 `{accepted: true, event_id}` with no writes.
  7. DB error → 5xx so Smartcar retries (api §3.12).
- Ingestion logic lives in `modules/vehicles/telemetry_service.py` (business logic), the route in `modules/vehicles/webhook_router.py`; the router stays thin.
- Units stored raw; `source='smartcar'`, `received_at=now()`, `raw_payload` = the normalized signal entry (db §4).

**Local dev**
- `services/api/README.md` section "Receiving Smartcar webhooks locally": start API, start tunnel (D9), register the tunnel URL in the Smartcar dashboard, send a test event. Commands verified before they go in the doc.
- A `scripts/send_test_webhook.py` that signs a fixture with the local secret and POSTs it to `localhost:8000`, for testing without a tunnel.

**Tests** (`tests/test_webhook_smartcar.py`) — these are the roadmap's required ones:
- Invalid signature → 401, zero rows written.
- Missing signature header → 401.
- Unsigned / badly signed `VERIFY` → 401, no challenge answer returned.
- Valid event → telemetry rows + `latest_state` updated.
- **Duplicate** event delivered twice → same row count, 200 both times.
- **Out-of-order**: newer event then older event → `latest_state` keeps the newer values; both rows stored in `vehicle_telemetry`.
- Partial-signal event only updates the signals it carries.
- Unknown signal stored in `raw_payload`, ignored for `latest_state`.
- Disconnected vehicle → 200 `accepted:false`, zero rows.
- Unknown vehicle → 200 `accepted:false`, zero rows.
- DB failure (forced) → 5xx.

**Acceptance:** all tests pass; a real event from the bZ through the tunnel lands in `vehicle_telemetry` and `latest_state`.

---

### F6 — Vehicle state + Dashboard (Dashboard slice, part 2)

**Backend**
- `GET /api/v1/vehicles/{id}/state` → api §3.5, read **only** from `vehicles.latest_state` (never calls Smartcar — test asserts the fake client is untouched).
- `is_stale = now - recorded_at > telemetry_stale_after_minutes`. No telemetry ever → every signal `null`, `is_stale: true`, `recorded_at: null`. *(Updated 2026-11-16 at implementation: `signals` always carries all five keys instead of `{}`, so the typed client has one shape; per-signal timestamps are `signal_recorded_at`; `range`/`odometer` are converted from km/miles and power from W/kW, anything else is `null`.)*
- Fields: `soc_percent`, `range_km`, `odometer_km`, `is_charging`, `charging_power_kw`, plus per-signal `recorded_at` (D5). Missing signals are `null`, never guessed (PRD §7).
- Ownership → 404.

**Frontend**
- Dashboard picks the primary vehicle (D6) or the most recent connected vehicle.
- Cards: SoC (%), manufacturer range (converted to the user's unit preference), odometer, charging status/power. Each card shows its unit and source timestamp in local time.
- Label range as "Manufacturer estimate" (PRD §4.1 — Chargewise-predicted range is Phase 4+).
- Stale banner when `is_stale`: "Data may be stale — last update X ago".
- Empty states: "No vehicle connected" (link to Garage) and "Connected — awaiting first update from your vehicle".
- Quick links: Plan Trip, Find Chargers, View Vehicle.
- Unit conversion helper `lib/units.ts` (km↔mi) with tests.

**Tests**
- pytest: fresh state → `is_stale=false`; old state → `true`; no telemetry → empty + stale; not owned → 404; Smartcar client not called.
- Vitest: units helper; Dashboard renders values with units and timestamps; stale banner; both empty states.

**Acceptance:** after a webhook, the Dashboard shows timestamped values; a hard reload shows the same values (E2).

---

### F7 — Primary vehicle + spec editing (optional, see D6)

- `PATCH /api/v1/me/preferences` — partial update of preferences, including `primary_vehicle_id` (must be the caller's connected vehicle, else 404).
- `PATCH /api/v1/vehicles/{id}/specs` — `usable_capacity_kwh` (> 0, sane upper bound), `connectors` (enum list), `charging_curve` (sorted SoC points, 0–1, power > 0).
- Garage: "Set as primary" action; spec edit form with validation.
- Add both endpoints to `api-contracts.md` first.
- Tests: validation errors, ownership 404, Dashboard follows the new primary.

---

### F8 — Phase close-out

1. **Real-car verification (E1–E3):** connect the bZ in live mode, drive/charge to trigger events, confirm the Dashboard updates; reload; disconnect and confirm a later event is not stored. Screenshots in the PR.
2. **Measure event cadence** over a few days; write the number and the chosen freshness threshold into the roadmap risk table; if too sparse, open an issue for a polling fallback (ADR 0002 says this decision happens in Phase 2).
3. **Playwright:** extend the E2E suite with a Garage → Dashboard flow against a backend running with a fake Smartcar client and seeded telemetry (no real Smartcar in CI).
4. **Secret-leak check in CI:** after `next build`, grep `.next/` for `SMARTCAR_` and fail if found (E6).
5. **Docs:** update `api-contracts.md` (D3, D5, D6), `database-design.md` (F1 additions), `architecture.md` §4/§5 with verified V3 details, ADR 0002 if anything changed, README run instructions, roadmap status → "Phase 2 complete; Phase 3 next".

**Progress (2026-11-16, `feature/phase2-closeout`):**
- 3 done, with a change: the Garage and Dashboard only read the database, so no fake Smartcar client is needed. The API runs with the Smartcar URLs pointed at an unreachable address, and the seed step delivers a locally signed `VEHICLE_STATE` webhook through the real ingestor. Sign-in uses a small Supabase Auth stub (`apps/web/e2e/support/`) plus the API's HS256 fallback.
- 4 done: the check already ran on `.next/static`; it now also scans prerendered HTML/RSC output. Server-only JS bundles are not scanned, because they aren't sent to the browser.
- 5 partly done: `api-contracts.md` and `database-design.md` were already updated in F3–F7. `architecture.md` §4/§5 and §8 now match the code. ADR 0002 is unchanged until the cadence is measured. The roadmap status says "code complete, real-car verification pending", not "complete".
- 1 and 2 are still open. They need the real car and a few days of webhook deliveries.

---

## 5. Cross-cutting rules for every Phase 2 PR

- Vertical order inside each PR: migration → model → service → router → schema/OpenAPI → regenerated TS client → UI → tests.
- Every new `public` table enables RLS in its migration.
- Every user-owned read/write filters by `user_id`; not owned = 404.
- No real Smartcar calls in default CI; fixtures and a fake client only. A separate manually-triggered workflow for live Smartcar tests can come later if useful.
- No secrets in `NEXT_PUBLIC_*`, logs, fixtures, or commits. Fixtures use a documented test-only webhook secret.
- No raw coordinates in logs (we're not requesting location anyway, D7).
- Before each PR: `uv run ruff check`, `uv run mypy app`, `uv run pytest`, Alembic round-trip, `npm run lint`, `npm run typecheck`, `npm run test`, and Playwright when UI flows change. PR description lists what ran and the results.
- New runtime dependencies need a reason in the PR (expected: none, or `cryptography` promoted to runtime if D1 needs token encryption).

## 6. Test plan summary

| Layer | New tests |
|---|---|
| Migrations | RLS on new tables (existing test), round-trip, constraints |
| Adapter | Token, connect URL, connections listing, signature, normalization, timeouts |
| Connect | Happy, denied, bad/expired state, duplicate, reconnect, subscribe failure |
| Garage API | List, detail, ownership 404, disconnect (incl. Smartcar failure) |
| Webhook | Invalid sig, duplicate, out-of-order, partial, unknown signal/vehicle, disconnected, DB failure |
| State API | Fresh, stale, empty, ownership, no live Smartcar call |
| Web (Vitest) | Connect banner, vehicle card, empty states, Dashboard cards, stale banner, units |
| E2E (Playwright) | Garage → Dashboard with fake backend data |
| Manual | Real Toyota connect, live webhook, reload, disconnect |

## 7. Risks

| Risk | Mitigation |
|---|---|
| V3 details differ from the planning docs (token model, signature scheme, signal names). | F0 first; plan and ADR updated before F1. |
| The bZ exposes fewer signals than expected (e.g. no charging power). | `supported_signals` drives the UI; missing signals show as unavailable. |
| Event cadence too low for a 15-min freshness threshold. | Threshold is a setting; measured in F8; polling fallback opened as an issue if needed. |
| Tunnel URL changes break webhook delivery in dev. | Static ngrok domain (D9) + local `send_test_webhook.py`. |
| Race between concurrent webhook deliveries. | Single transaction, `FOR UPDATE` on the vehicle row, per-signal monotonic update. |
| Smartcar outage during disconnect. | Local disconnect still succeeds; revocation failure logged and the connection ID kept, so a retry job (follow-up) can find owed revocations. |

## 8. How we'll work through it

For each feature: confirm the open decisions it depends on → create the branch → implement the slice → run the checks in §5 → you review the diff and try it → commit and open a PR → merge → next feature.

**Next step:** settle D1–D12 (especially D3, D4, D5, D6 and the F4 disconnect flag), then start **F0**.
