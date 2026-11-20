# Chargewise — API Contracts

Status: **Draft (specification phase)** · Last updated: 2026-11-17

This document defines the public REST surface the FastAPI backend exposes to the Next.js frontend (and, later, to the React Native client). The actual contract of record is the OpenAPI document FastAPI generates at runtime; this file is the design intent, written before any code.

## 1. Conventions

- **Base URL:** all endpoints live under `/api/v1`. Breaking changes land under a new major version (`/api/v2`), never in place.
- **Authentication:** `Authorization: Bearer <Supabase JWT>`. The `auth` module validates the JWT and resolves the Chargewise user. The one exception is `POST /api/v1/webhooks/smartcar`, which authenticates via Smartcar's signed webhook payload.
- **Content type:** request and response bodies are JSON (`application/json`).
- **Timestamps:** ISO-8601 in UTC with a `Z` suffix (`2026-11-06T19:00:00Z`).
- **Units:** every quantity field includes the unit in the field name (`range_km`, `odometer_km`, `soc_percent`, `power_kw`, `energy_kwh`). The server stores raw units from providers; conversion to the user's display preference happens in the frontend.
- **IDs:** opaque strings. External IDs (Smartcar, AFDC) are stored on their parent record, not surfaced as the primary ID.
- **Pagination:** list endpoints that can grow unbounded accept `?limit=` (default 50, max 200) and `?cursor=`. Responses include `next_cursor` (nullable).
- **Freshness:** any endpoint returning cached provider data includes `recorded_at` (ISO-8601) and `is_stale` (boolean) so the UI can mark it. Staleness thresholds are defined in [`architecture.md`](architecture.md).
- **Ownership:** every endpoint that touches a user-owned resource (`vehicles`, `planned_trips`, `observed_trips`, `vehicle_telemetry`) rejects with `404` when the record isn't owned by the caller. No "resource exists but you can't see it" leaks.

### 1.1 Error envelope

Every non-2xx response uses the same shape:

```json
{
  "error": {
    "code": "SMARTCAR_UNREACHABLE",
    "message": "Smartcar did not respond in time.",
    "details": { "upstream_status": 504 }
  }
}
```

Reserved codes (grow this list as the API grows):

| Code | HTTP | Meaning |
|---|---|---|
| `UNAUTHENTICATED` | 401 | Missing or invalid Supabase JWT. |
| `FORBIDDEN` | 403 | Authenticated but not allowed. |
| `NOT_FOUND` | 404 | Resource does not exist for the caller. |
| `VALIDATION_ERROR` | 422 | Request body failed Pydantic validation. |
| `CONFLICT` | 409 | Already-saved trip, etc. (not used for vehicle connect; see §3.3). |
| `SMARTCAR_UNREACHABLE` | 502/504 | Smartcar failed or timed out. |
| `SMARTCAR_ERROR` | 502 | Smartcar rejected the request; Smartcar's error code is in `details`. Includes Smartcar's monthly per-vehicle cap (`BILLING`/`VEHICLE_REQUEST_LIMIT`). |
| `SMARTCAR_RATE_LIMITED` | 429 | Smartcar throttled the request (`429` of type `RATE_LIMIT`, per vehicle or app-wide). `details.retry_after_seconds` and a `Retry-After` header when Smartcar said how long to wait. |
| `RATE_LIMITED` | 429 | One of Chargewise's own limits: the per-vehicle refresh limit (§3.5a; `details.next_refresh_at`) or the per-user limit on `/routes/plan` and `/places/suggest` (§3.7, §3.13; `details.limit` is `plan` or `suggest`). Always `details.retry_after_seconds` and a `Retry-After` header. |
| `VEHICLE_NOT_CONNECTED` | 409 | The action needs a connected, real (non-simulated) vehicle (§3.5a). |
| `SMARTCAR_NOT_CONFIGURED` | 503 | Smartcar credentials, application ID or state secret are not set on the server. |
| `MAPBOX_UNREACHABLE` | 502/504 | Mapbox failed or timed out. |
| `MAPBOX_ERROR` | 502 | Mapbox rejected the request or returned an unexpected response; Mapbox's error code (if any) is in `details`. |
| `MAPBOX_NOT_CONFIGURED` | 503 | The server's Mapbox token is missing or Mapbox rejected it (401/403). |
| `STATIONS_UNREACHABLE` | 502/504 | The station provider (AFDC) failed, timed out or returned an unexpected response; the upstream HTTP status (if any) is in `details`. |
| `STATIONS_NOT_CONFIGURED` | 503 | The server's AFDC API key is missing or AFDC rejected it (401/403). |
| `FAVORITES_LIMIT` | 409 | The caller already has the maximum number of favorite stations (500; see §3.19a). |
| `ROUTE_INFEASIBLE` | 200 | Returned by `/routes/plan` when no feasible plan exists (see §3.7). |
| `WEBHOOK_SIGNATURE_INVALID` | 401 | Smartcar webhook signature did not verify. |
| `WEBHOOK_PAYLOAD_INVALID` | 400 | Smartcar webhook was signed but is not a usable event (not JSON, no `eventType`, `VEHICLE_STATE` without vehicle/user or `eventId`). |
| `INTERNAL_ERROR` | 500 | Unhandled exception; logged to Sentry. |

`ROUTE_INFEASIBLE` is deliberately **not** an HTTP error — infeasibility is a legitimate planning result, returned with `status: "infeasible"` in the response body (see §3.7).

## 2. Endpoint index

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/me` | Authenticated profile and preferences. |
| `PATCH` | `/api/v1/me/preferences` | Partially update preferences, including the primary vehicle. |
| `DELETE` | `/api/v1/me` | Delete the account and all of its data; disconnect its cars. |
| `GET` | `/api/v1/me/privacy` | Consent state and data-retention windows. |
| `POST` | `/api/v1/me/consents` | Record an answer to the privacy notice. |
| `GET` | `/api/v1/vehicles` | List the caller's connected vehicles. |
| `POST` | `/api/v1/vehicles/connect` | Start a Smartcar Connect flow. |
| `POST` | `/api/v1/vehicles/connect/complete` | Finish Connect for the signed-in user. |
| `GET` | `/api/v1/vehicles/{id}` | Vehicle metadata and specs. |
| `PATCH` | `/api/v1/vehicles/{id}/specs` | Edit usable capacity, connectors and charging curve. |
| `GET` | `/api/v1/vehicles/{id}/state` | Latest cached vehicle state. |
| `POST` | `/api/v1/vehicles/{id}/refresh` | User-triggered live read from Smartcar to fill a webhook gap (rate-limited). |
| `DELETE` | `/api/v1/vehicles/{id}` | Disconnect vehicle and stop telemetry. |
| `POST` | `/api/v1/routes/plan` | Generate and store a charging-aware trip plan. |
| `GET` | `/api/v1/trips` | List saved (default) or unsaved planned trips. |
| `GET` | `/api/v1/trips/{id}` | One trip with its stored plan and observation. |
| `DELETE` | `/api/v1/trips/{id}` | Delete a trip. |
| `POST` | `/api/v1/trips/{id}/save` | Save a stored plan to trip history. |
| `GET` | `/api/v1/observed-trips` | List the caller's observed driving legs. |
| `GET` | `/api/v1/observed-trips/{id}` | One observed leg. |
| `POST` | `/api/v1/observed-trips/recompute` | Re-run trip inference for a vehicle (catch-up). |
| `POST` | `/api/v1/observed-trips/{id}/confirm` | Confirm an observed leg. |
| `POST` | `/api/v1/observed-trips/{id}/reject` | Reject an observed leg. |
| `GET` | `/api/v1/stations` | Search compatible charging stations near a point. |
| `GET` | `/api/v1/stations/{id}` | One cached station (refreshed after the cache TTL). |
| `POST` | `/api/v1/stations/along-route` | Stations within a corridor of a route, in route order. |
| `GET` | `/api/v1/stations/favorites` | The caller's favorite stations, newest first. |
| `PUT` | `/api/v1/stations/{id}/favorite` | Add a station to the caller's favorites (idempotent). |
| `DELETE` | `/api/v1/stations/{id}/favorite` | Remove a station from the caller's favorites (idempotent). |
| `GET` | `/api/v1/places/suggest` | Autocomplete addresses, places and POIs (Mapbox Search Box). |
| `GET` | `/api/v1/places/{id}` | Coordinates and address for a suggestion. |
| `POST` | `/api/v1/routes/directions` | Driving routes between two points (no charging plan). |
| `GET` | `/api/v1/analytics/consumption` | Consumption trends and model error per day/week/month. |
| `GET` | `/api/v1/analytics/trips` | Per-trip estimated vs observed energy (the analytics table). |
| `GET` | `/api/v1/analytics/trips.csv` | The same rows as CSV. |
| `POST` | `/api/v1/webhooks/smartcar` | Smartcar-signed telemetry delivery. |

## 3. Endpoints

All schema snippets are **sketches**, not final Pydantic models. The authoritative shapes live in `services/api/app/schemas/`.

### 3.1 `GET /api/v1/me`

Returns the authenticated profile and user preferences (units, reserve SoC, preferred networks).

**Response 200**
```json
{
  "id": "profile-uuid",
  "display_name": "Ayush",
  "created_at": "2026-11-01T12:00:00Z",
  "preferences": {
    "units": "imperial",
    "reserve_soc": 0.15,
    "max_detour_minutes": 20,
    "preferred_networks": ["Tesla Supercharger", "EVgo"],
    "preferred_charge_to": 0.8,
    "primary_vehicle_id": "vehicle-uuid"
  }
}
```

### 3.1a `PATCH /api/v1/me/preferences`

Partially updates the caller's preferences. Only the keys present in the body change; an empty body is a no-op. Returns the full preferences object (same shape as `preferences` in §3.1).

**Request** (every key optional)
```json
{
  "units": "metric",
  "reserve_soc": 0.1,
  "preferred_charge_to": 0.8,
  "max_detour_minutes": 15,
  "preferred_networks": ["EVgo"],
  "primary_vehicle_id": "vehicle-uuid"
}
```

**Response 200** — the updated `preferences` object.

- `units` is `imperial` or `metric`. `reserve_soc` and `preferred_charge_to` are fractions in `[0, 1]` with at most three decimal places (their stored precision), and after the update `reserve_soc` must be lower than `preferred_charge_to`. `max_detour_minutes` is `0`–`240`. `preferred_networks` holds at most 20 distinct, non-empty names (≤ 100 chars each).
- `primary_vehicle_id` must be one of the caller's **connected** vehicles; `null` clears it. The Dashboard shows the primary vehicle, else the most recently connected one.
- Disconnecting the primary vehicle (§3.6) clears `primary_vehicle_id`.
- `null` is only accepted for `primary_vehicle_id`; other keys must be omitted rather than set to `null`.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` for unknown keys, out-of-range values, or `reserve_soc >= preferred_charge_to`; `NOT_FOUND` if `primary_vehicle_id` doesn't exist, belongs to another user, or isn't connected.

### 3.1b `DELETE /api/v1/me`

Deletes the caller's account (PRD §4.6, P6-F5). No body. **Response 204**, empty. Idempotent: calling it again with a still-valid token answers `204` and only retries the steps still owed.

In one transaction, the server:

0. Takes a per-account advisory lock that Connect completion (`POST /vehicles/connect/complete`, §3.3.1) also takes before reading anything. So a Connect that finishes first has its vehicles revoked and deleted here, and a Connect that finishes after sees the tombstone, revokes the connections it just authorized (best effort) and stores nothing (its result is `error`).
1. Records a deletion tombstone ([`database-design.md`](database-design.md) §3.12). From then on, any endpoint that would create the profile answers `401 UNAUTHENTICATED` ("This account has been deleted."), so an access token that hasn't expired can't bring the account back. Read endpoints that never create a profile return empty results.
2. Locks the caller's vehicles (the lock the webhook ingestor and refresh take) and revokes each Smartcar connection (`DELETE /v3/connections/{id}`), including revocations still owed from an earlier disconnect. A `404` from Smartcar counts as revoked.
3. Deletes the profile. Every user-owned table cascades from it in the database: preferences, consent records, favorites, vehicles with their specs (incl. the per-vehicle baseline), telemetry and refresh log, planned trips and stops, observed trips and prediction results (db-design §6). The global `charging_stations` cache is kept.

After the commit it deletes the Supabase auth user (admin API, service-role key), which also ends every session.

- **Failures don't block deletion.** A Smartcar revocation that fails (timeout, 5xx, not configured) and a Supabase deletion that fails or isn't configured are stored on the tombstone; the nightly retention job retries them. The user's data is already gone, and webhooks for their cars are acknowledged with `accepted: false` and dropped (§3.12), so nothing can be re-created meanwhile.
- The response never says which external steps are still owed; logs carry counts only.

**Errors:** `UNAUTHENTICATED`.

### 3.1c `GET /api/v1/me/privacy`

The caller's consent state and the retention windows the server enforces, for the Profile page. Never writes (it doesn't create a profile).

**Response 200**
```json
{
  "consents": [
    {
      "kind": "privacy_notice",
      "current_version": "2026-11-19",
      "granted": true,
      "version": "2026-11-19",
      "recorded_at": "2026-11-19T18:00:00Z",
      "is_current": true
    }
  ],
  "retention": { "telemetry_days": 730, "unsaved_plan_days": 30, "refresh_log_days": 30 }
}
```

- One entry per consent kind; `privacy_notice` is the only kind for now. `current_version` is the server's `PRIVACY_NOTICE_VERSION`.
- `granted`, `version` and `recorded_at` are the caller's latest answer, all `null` before they answer. `is_current` is `true` only when they granted the current version, so bumping the version asks everyone again.
- `retention` comes from the `PRIVACY_*` settings used by the retention job.

**Errors:** `UNAUTHENTICATED`.

### 3.1d `POST /api/v1/me/consents`

Records an answer. Every call appends a row (the audit trail); history is never rewritten.

**Request**
```json
{ "kind": "privacy_notice", "version": "2026-11-19", "granted": true }
```

**Response 201** — the kind's new status, same shape as one `consents` entry in §3.1c.

**Errors:** `UNAUTHENTICATED` (including for a deleted account); `VALIDATION_ERROR` for an unknown kind, unknown keys, a missing `granted`, or a `version` that isn't the current one (`details.current_version` says which is), so a stale page can't record agreement to a notice the user didn't see.

### 3.2 `GET /api/v1/vehicles`

Lists only the caller's vehicles, most recently connected first. By default only `connected` ones; `?include_disconnected=true` also returns disconnected (and pending) rows. Never calls Smartcar.

**Response 200**
```json
{
  "items": [
    {
      "id": "vehicle-uuid",
      "make": "Toyota",
      "model": "bZ",
      "year": 2026,
      "connection_status": "connected",
      "supported_signals": ["soc", "range", "odometer", "charging_status"],
      "last_state_recorded_at": "2026-11-06T18:52:00Z",
      "is_stale": false
    }
  ]
}
```

- `last_state_recorded_at` is the newest provider timestamp in the cached `latest_state` ([`database-design.md`](database-design.md) §3.3), or `null` before the first webhook.
- `is_stale` is `true` when there is no data yet or the newest reading is older than `TELEMETRY_STALE_AFTER_MINUTES` (default 15).

**Errors:** `UNAUTHENTICATED`.

### 3.3 `POST /api/v1/vehicles/connect`

Starts a Smartcar Connect flow and returns the authorization URL the frontend redirects the user to. The `state` is a short-lived signed token tied to the authenticated user (no server-side storage).

**Request**
```json
{ "redirect_after": "/garage" }
```

**Response 200**
```json
{
  "authorization_url": "https://connect.smartcar.com/oauth/authorize?...",
  "state": "opaque-state-nonce",
  "expires_at": "2026-11-06T19:10:00Z"
}
```

`redirect_after` is optional (default `/garage`) and must be a same-origin path: absolute URLs, `//host`, backslashes, control characters, `//` inside the path and dot segments are rejected with `VALIDATION_ERROR`. `state` is a signed 10-minute token; the frontend doesn't need to store it.

Smartcar redirects the browser to the web app's `/garage/connect/callback` (the registered `redirect_uri`), which calls §3.3.1 with the signed-in user's token and then redirects to `redirect_after` with a `connect` query flag the Garage page reads.

#### 3.3.1 `POST /api/v1/vehicles/connect/complete`

Finishes Connect. Called by the web app's callback route with the query Smartcar redirected back with. With `response_type=none` there is no code exchange.

**Request**
```json
{ "state": "opaque-state", "user_id": "smartcar-user-uuid", "external_id": "profile-uuid", "error": null }
```

**Response 200**
```json
{ "result": "success", "redirect_after": "/garage" }
```

The backend verifies `state` **and requires its user to be the caller**, so a Connect link started by one account can't be completed by another account's browser. It then lists the Smartcar user's connections (accepting only those whose `externalId` is the caller's profile ID) and stores or reactivates the vehicles. Flow outcomes are always `200` with `result`; `redirect_after` falls back to `/garage` when `state` can't be trusted:

| `result` | Meaning |
|---|---|
| `success` | At least one vehicle was added or reconnected. |
| `already_connected` | Every authorized vehicle was already connected to this user. |
| `owned_elsewhere` | Every authorized vehicle is already connected to another Chargewise account (one car belongs to one account at a time). Nothing is stored, and the connections this Connect just created are deleted on Smartcar, best-effort (a failed delete doesn't change the result). |
| `denied` | The user declined or backed out of Smartcar Connect (`error=access_denied` or `error=user_manually_returned_to_application`). |
| `error` | Bad/expired `state`, `state` belongs to another user, Smartcar unreachable, no matching connection, or a mix of outcomes with nothing added (for example one duplicate and one car owned by another account). |

**Errors:** `UNAUTHENTICATED` without a JWT; `VALIDATION_ERROR` if `state` is missing.

Webhook subscription is not done here yet; see F5 in [`phase2_plan.md`](phase2_plan.md).

**Errors (§3.3):** `UNAUTHENTICATED` without a JWT; `VALIDATION_ERROR` for an unsafe `redirect_after`; `SMARTCAR_NOT_CONFIGURED` (503) if the Smartcar application ID or state secret isn't set. Duplicates never return `CONFLICT` (they can only be detected after authorization).

### 3.4 `GET /api/v1/vehicles/{id}`

**Response 200**
```json
{
  "id": "vehicle-uuid",
  "make": "Toyota",
  "model": "bZ",
  "year": 2026,
  "connection_status": "connected",
  "connected_at": "2026-11-02T09:15:00Z",
  "disconnected_at": null,
  "supported_signals": ["soc", "range", "odometer", "charging_status"],
  "last_state_recorded_at": "2026-11-06T18:52:00Z",
  "is_stale": false,
  "specs": {
    "usable_capacity_kwh": 71.4,
    "connectors": ["NACS", "J1772", "CCS1"],
    "charging_curve": [
      { "soc": 0.1, "power_kw": 150 },
      { "soc": 0.5, "power_kw": 120 },
      { "soc": 0.8, "power_kw": 60 }
    ],
    "baseline_kwh_per_km": null,
    "baseline_source": "catalog",
    "catalog_baseline_kwh_per_km": 0.2
  }
}
```

`connectors` reflects the physical port plus adapters the user has configured. `charging_curve` is a piecewise representation consumed by the energy module.

The vehicle may be disconnected (it's still the caller's). `last_state_recorded_at`, `is_stale` and `disconnected_at` are the same as in §3.2. `usable_capacity_kwh` is `null` when it isn't known (no catalog entry and not entered by the user); `specs` is `null` if the row has none. Never calls Smartcar.

Consumption baseline (phase4_plan F8), in kWh/km:
- `baseline_kwh_per_km`: the user's own value, or `null` if they haven't entered one. Never copied from the catalog.
- `catalog_baseline_kwh_per_km`: read-only catalog value for the vehicle's make/model/year (a mild-weather highway estimate), or `null` if the catalog has none. The Garage shows it as the placeholder.
- `baseline_source`: where the value the planner will use comes from: `"vehicle"` if `baseline_kwh_per_km` is set, else `"catalog"` if `catalog_baseline_kwh_per_km` is set, else `null` (the vehicle can't be planned: §3.7 returns `VEHICLE_SPECS_INCOMPLETE`).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID; `NOT_FOUND` if it doesn't exist **or belongs to another user** (the two are indistinguishable).

### 3.4a `PATCH /api/v1/vehicles/{id}/specs`

Partially updates a vehicle's specs. Only the keys present in the body change. Returns the full `specs` object (same shape as `specs` in §3.4). Works for connected and disconnected vehicles the caller owns; creates the specs row if the vehicle has none. Never calls Smartcar.

**Request** (every key optional)
```json
{
  "usable_capacity_kwh": 71.4,
  "connectors": ["NACS", "CCS1"],
  "charging_curve": [
    { "soc": 0.1, "power_kw": 150 },
    { "soc": 0.8, "power_kw": 60 }
  ],
  "baseline_kwh_per_km": 0.185
}
```

**Response 200** — the updated `specs` object.

- `usable_capacity_kwh`: `> 0` and `≤ 300`, at most two decimal places; `null` clears it (back to "unknown").
- `connectors`: distinct values from `NACS`, `CCS1`, `J1772`, `CHAdeMO`; `[]` clears them.
- `charging_curve`: at most 20 points; `soc` in `[0, 1]` and strictly increasing; `power_kw` `> 0` and `≤ 1000`; `[]` clears it.
- `baseline_kwh_per_km`: `> 0` and `≤ 1` kWh/km, at most three decimal places; `null` clears it (back to the catalog value). `catalog_baseline_kwh_per_km` and `baseline_source` are read-only and rejected as unknown keys.
- `connectors` and `charging_curve` must be omitted rather than set to `null`.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID, a key is unknown, or a value breaks the rules above; `NOT_FOUND` if the vehicle doesn't exist or belongs to another user.

### 3.5 `GET /api/v1/vehicles/{id}/state`

Returns the latest cached state. **Never** triggers a live Smartcar call on the hot path.

**Response 200**
```json
{
  "vehicle_id": "vehicle-uuid",
  "recorded_at": "2026-11-06T18:52:00Z",
  "received_at": "2026-11-06T18:52:08Z",
  "source": "smartcar",
  "is_stale": false,
  "signals": {
    "soc_percent": 72,
    "range_km": 296,
    "odometer_km": 20082.5,
    "is_charging": false,
    "charging_power_kw": null
  },
  "signal_recorded_at": {
    "soc_percent": "2026-11-06T18:52:00Z",
    "range_km": "2026-11-06T18:51:00Z",
    "odometer_km": "2026-11-06T18:50:00Z",
    "is_charging": "2026-11-06T18:52:00Z"
  }
}
```

- Reads only the cached `latest_state` ([`database-design.md`](database-design.md) §3.3); a reload returns the same values.
- `signals` always has all five keys. A key is `null` when the vehicle hasn't reported it, or reported it in a unit the server doesn't recognise; values are never guessed ([PRD §7](product-requirements.md)). Distances are converted from the provider's `km`/`miles` to km and power from W/kW to kW; conversion to the user's display unit happens in the frontend.
- `recorded_at` is the newest provider timestamp across signals. `signal_recorded_at` gives the provider timestamp for each non-null field in `signals` (signals move forward independently; see [`phase2_plan.md`](phase2_plan.md) D5).
- `source` is `smartcar`, `simulated` for demo vehicles, or `null` before any data.
- `is_stale` follows §3.2: `true` with no data yet, or when `recorded_at` is older than `TELEMETRY_STALE_AFTER_MINUTES`.
- If no telemetry has ever arrived, responds `200` with every signal `null`, `signal_recorded_at: {}`, `recorded_at: null` and `is_stale: true`.
- A disconnected vehicle still returns its last cached state to its owner.
- To bring stale data up to date the client calls §3.5a explicitly; this endpoint never does.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID; `NOT_FOUND` if it doesn't exist **or belongs to another user**.

### 3.5a `POST /api/v1/vehicles/{id}/refresh`

Reads the vehicle once, live, from Smartcar (`GET /v3/vehicles/{id}/signals`). It fills gaps when webhooks were missed, for example while the API or its tunnel was down ([ADR 0010](adr/0010-on-demand-vehicle-refresh.md)). Only the user triggers it: the web app offers it where the data is stale. No body.

**Response 200**
```json
{
  "refreshed_at": "2026-11-19T15:02:11Z",
  "inserted": 4,
  "next_refresh_at": "2026-11-19T15:17:11Z",
  "state": { "vehicle_id": "vehicle-uuid", "recorded_at": "2026-11-19T15:00:40Z", "is_stale": false, "...": "same shape as §3.5" }
}
```

- **What gets stored.** Readings are stored with the same rules as a webhook delivery (§3.12, [`architecture.md`](architecture.md) §5):
  - `recorded_at` is the OEM timestamp;
  - `latest_state` only moves forward;
  - signals without a provider timestamp are skipped.
- **`inserted`** is the number of new `vehicle_telemetry` rows. It is `0` when Smartcar had nothing newer than what is already stored: the same reading from an earlier refresh, or one a webhook already delivered. The returned `state` can then still be `is_stale: true`. The car hasn't reported since, and the API doesn't pretend otherwise.
- **`state`** is exactly what §3.5 would now return.
- **Post-ingest hooks.** When rows were inserted, the post-ingest hooks (trip inference, [ADR 0009](adr/0009-in-process-post-ingest-hook-for-trip-inference.md)) run after the response, as for a webhook.
- **Rate limit per vehicle:**
  - one refresh per `SMARTCAR_REFRESH_MIN_INTERVAL_MINUTES` (default 15);
  - at most `SMARTCAR_REFRESH_MAX_PER_DAY` (default 8) in any rolling 24 h;
  - `next_refresh_at` is the earliest time another refresh is allowed;
  - an attempt counts once it is about to call Smartcar, even if Smartcar then fails. It doesn't count when the server has no Smartcar credentials.
- **Gaps stay gaps.** A refresh returns Smartcar's current snapshot. Readings missed during an outage are not recovered.

**Errors:**
- `UNAUTHENTICATED`.
- `VALIDATION_ERROR` if `id` isn't a UUID.
- `NOT_FOUND` if the vehicle doesn't exist or belongs to another user.
- `VEHICLE_NOT_CONNECTED` (409) if it is disconnected, pending or simulated.
- `RATE_LIMITED` (429), with a `Retry-After` header.
- `SMARTCAR_RATE_LIMITED` (429).
- `SMARTCAR_UNREACHABLE` (502/504), `SMARTCAR_ERROR` (502), `SMARTCAR_NOT_CONFIGURED` (503).

### 3.6 `DELETE /api/v1/vehicles/{id}`

Marks the vehicle row `disconnected` (keeping it for historical trips) and revokes its Smartcar connection (`DELETE /v3/connections/{id}`).

**Response 204** — no body.

- **Local disconnect wins:** the row is marked disconnected even if Smartcar is unreachable or refuses, so the webhook ingestor stops accepting the vehicle's telemetry. The failure is logged and the connection ID is kept for a later retry (see [`database-design.md`](database-design.md) §3.3). A `404` from Smartcar (connection already gone) counts as revoked.
- **Idempotent:** disconnecting an already-disconnected vehicle returns `204` without calling Smartcar again.
- **Webhooks:** Smartcar V3's per-vehicle unsubscribe isn't confirmed yet ([`smartcar-v3-notes.md`](smartcar-v3-notes.md) §5), so nothing is unsubscribed here; deliveries for a disconnected vehicle are acknowledged and dropped (`accepted: false`, phase2_plan.md D12). F5 settles unsubscription.
- Reconnecting the same car later reactivates this row (D4).
- If this vehicle was the caller's `primary_vehicle_id`, it is cleared (§3.1a).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID; `NOT_FOUND` if it doesn't exist or belongs to another user.

### 3.7 `POST /api/v1/routes/plan`

The main planning call (Phase 4). The server gets the driving route itself, finds compatible chargers along it, runs the deterministic optimizer ([`architecture.md`](architecture.md#6-routing-algorithm), [ADR 0008](adr/0008-route-ordered-dp-charging-optimizer.md)), **stores the result** and returns it with its `trip_id`. Design decisions: [`phase4_plan.md`](phase4_plan.md) §2.

**Request**
```json
{
  "vehicle_id": "vehicle-uuid",
  "origin": { "lat": 39.4143, "lon": -77.4105, "label": "Frederick, MD" },
  "destination": { "lat": 43.6532, "lon": -79.3832, "label": "Toronto, ON" },
  "route_index": 0,
  "departure_time": "2026-11-07T08:00:00Z",
  "preferences": {
    "reserve_soc": 0.15,
    "preferred_charge_to": 0.8,
    "max_detour_minutes": 20,
    "preferred_networks": ["Tesla Supercharger", "EVgo"]
  },
  "starting_soc_override": null
}
```

- `origin` / `destination`: same rules as §3.15 (more than 50 m apart); `label` is optional, at most 200 characters, and stored.
- `route_index` (default `0`): which route from §3.15 to plan on. The server calls Mapbox again with the same options; it never uses geometry sent by the client.
- `departure_time` is optional and stored; v1 doesn't use it (no traffic or weather).
- `preferences` is optional and partial: each key falls back to the caller's stored preferences (§3.1), then to the defaults. Same rules as §3.1a, including `reserve_soc < preferred_charge_to` after merging.
- `starting_soc_override` is a fraction in `[0, 1]` or `null`. When given it is always used. When `null`, the vehicle's cached `soc_percent` is used only if its own `signal_recorded_at` (§3.5) is no older than `TELEMETRY_STALE_AFTER_MINUTES`; otherwise the request fails with `STARTING_SOC_REQUIRED` (below).

**Response 200 (feasible)**
```json
{
  "trip_id": "trip-uuid",
  "created_at": "2026-11-06T18:00:00Z",
  "status": "feasible",
  "model_version": "deterministic-v1",
  "vehicle_id": "vehicle-uuid",
  "origin": { "lat": 39.4143, "lon": -77.4105, "label": "Frederick, MD" },
  "destination": { "lat": 43.6532, "lon": -79.3832, "label": "Toronto, ON" },
  "departure_time": "2026-11-07T08:00:00Z",
  "starting_soc": 0.9,
  "starting_soc_source": "telemetry",
  "starting_soc_recorded_at": "2026-11-06T17:55:00Z",
  "route": {
    "route_index": 0,
    "geometry_polyline6": "iwsdjAdlwsrCg@...",
    "distance_km": 748.1,
    "drive_minutes": 515,
    "summary": "US 15, QEW",
    "countries": ["US", "CA"]
  },
  "stops": [
    {
      "sequence": 1,
      "station": {
        "id": "station-uuid",
        "name": "Example station",
        "network": "Electrify America",
        "connector": "CCS1",
        "power_kw": 150,
        "location": { "lat": 40.2732, "lon": -76.8867 }
      },
      "distance_along_route_km": 260.0,
      "distance_from_route_km": 0.8,
      "arrival_soc": 0.162,
      "departure_soc": 0.61,
      "charging_minutes": 15,
      "detour_minutes": 2,
      "detour_is_estimate": true
    }
  ],
  "arrival_soc": 0.15,
  "totals": {
    "drive_minutes": 515,
    "charging_minutes": 45,
    "detour_minutes": 8,
    "stop_overhead_minutes": 15,
    "waiting_minutes": 0,
    "total_minutes": 583,
    "energy_kwh_estimated": 150.92
  },
  "assumptions": {
    "usable_capacity_kwh": 71.0,
    "usable_capacity_source": "catalog",
    "baseline_kwh_per_km": 0.2,
    "baseline_source": "catalog",
    "charging_curve_source": "catalog_derived",
    "adjustments_applied": [],
    "reserve_soc": 0.15,
    "charge_to_soc": 0.8,
    "max_detour_minutes": 20,
    "preferred_networks": ["Tesla Supercharger", "EVgo"],
    "preferred_networks_relaxed": false,
    "stop_overhead_minutes": 5,
    "detour_road_factor": 1.3,
    "detour_speed_kmh": 50,
    "soc_grid_step": 0.01
  },
  "candidates": {
    "searched": true,
    "corridor_km": 6.41,
    "considered": 41,
    "excluded_incompatible": 12,
    "excluded_unknown_power": 3,
    "excluded_detour": 5
  },
  "alternatives": [],
  "reason": null
}
```

- **Units and rounding:** SoC values are fractions (0..1) with 3 decimals. Minutes are whole numbers, rounded half-up; totals are computed before rounding, so per-stop minutes can differ from the totals by a minute. `total_minutes = drive + charging + detour + stop_overhead + waiting`.
- `starting_soc_source` is `telemetry` or `override`; `starting_soc_recorded_at` is the reading's provider timestamp, or `null` for an override.
- `stops` is in route order; `[]` when no charging is needed. Each `station.id` is the stable Chargewise station ID (§3.16), and `station.power_kw` is the known power of the connector type used. `detour_minutes` is always an estimate (`2 × distance_from_route_km × detour_road_factor` at `detour_speed_kmh`).
- `assumptions` says where each vehicle number came from: `*_source` is `vehicle` (the user's specs, §3.4a) or `catalog`. `charging_curve_source` is `vehicle`, `catalog` or `catalog_derived` (derived from published figures, not measured; the UI says so). `adjustments_applied` lists energy adjustments (speed, temperature, …) and is always `[]` in v1. `preferred_networks_relaxed` is `true` when no plan existed using only preferred networks, so all networks were used.
- `candidates` explains which stations were used. Stations with no known power for a compatible connector are **never** used and are counted in `excluded_unknown_power`. When no charging was needed, the stations search isn't run: `searched: false` and the counts are `0`.
  - `considered` is the number of usable stations handed to the optimizer, after the three exclusions. Each excluded station is counted once, in the order incompatible → unknown power → detour.
  - `corridor_km` is `max_detour_minutes · detour_speed_kmh / 60 / (2 · detour_road_factor)`, clamped to 0.5–16 km and rounded to 3 decimals (20 min → `6.41`).
  - The search is also skipped for `STARTING_SOC_TOO_LOW`, since no charger can help.
- `*_source` values equal to the catalog's are reported as `catalog` even when they are stored on the vehicle (a newly connected catalog vehicle gets the catalog copied into its specs), so derived figures are never shown as the user's own This rule doesn't apply to `baseline_source`: connecting a car never copies the catalog baseline into its specs, so a stored baseline is always the user's own and is reported as `vehicle`.
- `route.distance_km` is Mapbox's road distance (3 decimals). `distance_along_route_km` on stops and `from_km`/`to_km` on a limiting segment use that same axis.
- `alternatives` is reserved for several plans and is always `[]` in v1.
- Every plan, feasible or infeasible, is stored as an **unsaved** trip (§3.8). Saving is §3.9b; discarding is §3.9a.

**Response 200 (infeasible)**

Same top-level shape, with `status: "infeasible"`, `stops: []`, `arrival_soc: null`, `totals: null`, and a `reason`:

```json
{
  "trip_id": "trip-uuid",
  "status": "infeasible",
  "route": { "route_index": 0, "geometry_polyline6": "…", "distance_km": 748.1, "drive_minutes": 515, "summary": "US 15, QEW", "countries": ["US", "CA"] },
  "stops": [],
  "arrival_soc": null,
  "totals": null,
  "reason": {
    "code": "DESTINATION_UNREACHABLE",
    "message": "No charger is close enough to cross the gap after km 412 with your charge-to limit.",
    "limiting_segment": {
      "from": { "lat": 42.11, "lon": -78.21 },
      "to":   { "lat": 42.98, "lon": -78.87 },
      "from_km": 412.0,
      "to_km": 640.0,
      "required_soc": 0.79,
      "available_soc": 0.8
    }
  }
}
```

Infeasibility codes ([`architecture.md`](architecture.md#66-infeasibility) §6.6):

| Code | Meaning | `limiting_segment` |
|---|---|---|
| `NO_DRIVING_ROUTE` | Mapbox found no drivable route; `route` is `null`. | `null` |
| `STARTING_SOC_TOO_LOW` | Starting SoC is at or below `reserve_soc`. | `null` |
| `RESERVE_SOC_UNREACHABLE` | Infeasible with this reserve, feasible with a lower one. | where SoC falls lowest |
| `NO_COMPATIBLE_CHARGER_IN_RANGE` | Charging is needed but no usable charger is reachable from the start. | origin → where SoC reaches the reserve |
| `DESTINATION_UNREACHABLE` | A gap between chargers (or to the destination) can't be crossed from `charge_to_soc`. | the gap |

`limiting_segment.from`/`to` are points on the route (coordinates rounded to 2 decimals in logs only, never in the response); `from_km`/`to_km` are distances along it.

**Errors**
- `UNAUTHENTICATED`.
- `NOT_FOUND`: the vehicle doesn't exist or isn't the caller's.
- `VALIDATION_ERROR` (422), for body rule violations and for these `details.reason` values:
  - `STARTING_SOC_REQUIRED`: no `starting_soc_override`, and the SoC reading is missing or stale. `details` also has `last_soc` (fraction or `null`) and `last_recorded_at` (or `null`) so the UI can prefill a prompt.
  - `VEHICLE_SPECS_INCOMPLETE`: the vehicle lacks a value the model needs, from its specs and from the catalog. `details.missing` lists any of `usable_capacity_kwh`, `connectors`, `charging_curve`, `baseline_kwh_per_km`.
  - `ROUTE_INDEX_UNAVAILABLE`: `route_index` is beyond the routes Mapbox returned. `details.route_count` says how many there were.
  - No `details.reason`, `details.fields` set: `reserve_soc` isn't below `preferred_charge_to` after merging, or isn't below the vehicle's highest chargeable SoC (the last point of its charging curve; `details.max_charge_soc`).
- `RATE_LIMITED` (429): the caller used up their plan allowance (see "Rate limit" below). `details.limit` is `plan`; `details.retry_after_seconds` and the `Retry-After` header say when the next plan is allowed.
- `MAPBOX_NOT_CONFIGURED`, `MAPBOX_UNREACHABLE`, `MAPBOX_ERROR`; `STATIONS_NOT_CONFIGURED`, `STATIONS_UNREACHABLE`.
- Nothing is stored when the request fails.

**Rate limit (P6-F4).** Each plan costs a Mapbox Directions call and AFDC calls, so each user gets a token bucket: `RATE_LIMIT_PLAN_BURST` plans at once (default 10), refilled at `RATE_LIMIT_PLAN_PER_HOUR` (default 30 per hour, one every 2 minutes). Every authenticated request spends a token, including one that then fails validation; a request without a valid JWT is refused (401) before it reaches the bucket. The buckets are kept per API process (one instance in production), so a restart refills them.

### 3.8 `GET /api/v1/trips`

Lists the caller's planned trips, newest first. Observed trips arrive in Phase 5.

Query params: `?vehicle_id=&saved=true|false&status=planned|completed|abandoned&limit=&cursor=`.

- `saved` defaults to `true` (saved trips only); `false` lists only unsaved plans.
- Saved trips are ordered by `saved_at` descending, unsaved by `created_at` descending; ties by `id`.
- `vehicle_id` must be the caller's (else `NOT_FOUND`).

**Response 200**
```json
{
  "items": [
    {
      "id": "trip-uuid",
      "vehicle_id": "vehicle-uuid",
      "origin_label": "Frederick, MD",
      "destination_label": "Toronto, ON",
      "status": "planned",
      "plan_status": "feasible",
      "distance_km": 748.1,
      "total_minutes": 583,
      "stop_count": 3,
      "created_at": "2026-11-06T18:00:00Z",
      "saved_at": "2026-11-06T18:02:00Z"
    }
  ],
  "next_cursor": null
}
```

- `plan_status` is `feasible` or `infeasible`. `distance_km` and `total_minutes` are `null` when there was no route or no feasible plan.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` for bad params or cursor; `NOT_FOUND` for a `vehicle_id` that isn't the caller's.

### 3.9 `GET /api/v1/trips/{id}`

Returns one trip with the stored plan (saved or not) and, from Phase 5, the matched observation. Nothing is recomputed.

**Response 200**
```json
{
  "id": "trip-uuid",
  "vehicle_id": "vehicle-uuid",
  "status": "planned",
  "plan_status": "feasible",
  "created_at": "2026-11-06T18:00:00Z",
  "saved_at": null,
  "plan": { "...": "the stored /routes/plan 200 body, unchanged" },
  "observation": null
}
```

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID; `NOT_FOUND` if it doesn't exist or isn't the caller's.

### 3.9a `DELETE /api/v1/trips/{id}`

Deletes a trip (saved or not) and its stops.

**Response 204** — no body.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID; `NOT_FOUND` if it doesn't exist, isn't the caller's, or was already deleted.

### 3.9b `POST /api/v1/trips/{id}/save`

Marks a stored plan as saved, so it appears in `GET /trips` (§3.8). No body. Idempotent: saving a saved trip keeps its first `saved_at`. Feasible and infeasible plans can both be saved. Unsaved plans are removed by the retention job after 30 days (Phase 6).

**Response 200** — the trip's list item (same shape as an item in §3.8).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID; `NOT_FOUND` if it doesn't exist or isn't the caller's.

### 3.10 `GET /api/v1/stations`

Search chargers near a point, independent of trip planning ([`phase3_plan.md`](phase3_plan.md) F5). Results always come from the provider (AFDC) and are upserted into the `charging_stations` cache, so each station's `id` is a stable Chargewise UUID (D7).

| Param | Required | Meaning |
|---|---|---|
| `lat`, `lon` | yes | Search centre. |
| `radius_km` | no | Greater than 0, at most 80. Default 25. |
| `connector` | no | Repeatable: `NACS`, `CCS1`, `CHAdeMO`, `J1772`. Overrides the vehicle default. |
| `network` | no | Repeatable provider network name (up to 20). |
| `charging_level` | no | `level2` or `dc_fast`. Omitted = every level. |
| `min_power_kw` | no | 0–1000. Hides stations whose *known* maximum power is lower; stations with unknown power stay (D10). |
| `vehicle_id` | no | One of the caller's vehicles (else `404 NOT_FOUND`). Its connectors become the default `connector` filter, and each item gets `compatible`. |
| `limit` | no | 1–200, default 50. |
| `cursor` | no | `next_cursor` from the previous page of the *same* search. |

Defaults: only `status = open`, `access = public`, and not `restricted_access` stations; `country=all` (US and Canada).

**Response 200**
```json
{
  "items": [
    {
      "id": "8f0c7f8e-2b8e-4a55-9c1b-7d1a0e6f3c21",
      "provider": "afdc",
      "external_id": "307447",
      "name": "POTOMAC EDISON MAX KEHNE DC1",
      "operator": null,
      "network": "ChargePoint Network",
      "street_address": "1108 W 7th St",
      "city": "Frederick",
      "region": "MD",
      "postal_code": "21701",
      "country": "US",
      "location": { "lat": 39.427863, "lon": -77.419265 },
      "connectors": [
        { "type": "CCS1", "count": 1, "power_kw": 62.0 },
        { "type": "CHAdeMO", "count": 1, "power_kw": 62.5 }
      ],
      "max_power_kw": 62.5,
      "access": "public",
      "access_detail": null,
      "restricted_access": false,
      "status": "open",
      "pricing_text": null,
      "compatible": true,
      "live_availability": null,
      "source_updated_at": "2026-11-17T02:35:33Z",
      "fetched_at": "2026-11-18T19:00:00Z",
      "is_stale": false
    }
  ],
  "next_cursor": "eyJvIjo1MCwicSI6Ii4uLiJ9.2kH9…",
  "total_results": 26,
  "unknown_power_count": 0
}
```

- `connectors` has one entry per type, at that type's highest known power; `count` is how many connectors of that type run at that power. `count` and `power_kw` are **nullable** and are never derived from level totals (D9).
- `max_power_kw` is the highest *known* connector power, or `null`.
- `compatible` is `true`/`false` only when `vehicle_id` is given and that vehicle's connectors are known; otherwise `null`. It compares connector types only (D9).
- `live_availability` is always `null` in Phase 3: no provider returns verified live data, and it is **never** fabricated.
- Freshness: `fetched_at` is when Chargewise last fetched the station (it plays the role of `recorded_at` from §1); `source_updated_at` is the provider's own timestamp, or `null`. Search results are always fresh (`is_stale: false`).
- `total_results` is the provider's match count (it counts restricted stations, and is taken before `min_power_kw`). `unknown_power_count` counts the items on this page with unknown power. A page can hold fewer than `limit` items because of those two filters.
- `next_cursor` is opaque and HMAC-signed (`STATIONS_CURSOR_SECRET`). It is bound to the caller and the exact query; a tampered, expired (secret rotated) or reused-with-other-params cursor is `422 VALIDATION_ERROR`, and the client should restart the search. When the server has no cursor secret, `next_cursor` is always `null`.
- Coordinates and filters are never logged (D13).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (bad params or cursor); `NOT_FOUND` (`vehicle_id` not the caller's); `STATIONS_NOT_CONFIGURED` (503); `STATIONS_UNREACHABLE` (502/504).

### 3.11 `GET /api/v1/analytics/consumption`

Consumption trends and model error for one vehicle's observed driving legs (Phase 5). Design decisions: [`phase5_plan.md`](phase5_plan.md) §2 (D9, D11–D13) and §3 F5.

Query params: `?vehicle_id=&from=&to=&group_by=day|week|month&tz=&include_simulated=false`.

- `vehicle_id` (required) must be the caller's.
- `from` / `to`: dates (`YYYY-MM-DD`), inclusive, in `tz`. Default: the last 30 days up to today. `from > to` → `VALIDATION_ERROR`.
- `group_by` defaults to `week`. Weeks are ISO weeks (Monday start).
- `tz`: an IANA time zone (default `UTC`); the client sends the browser's zone. A leg belongs to the bucket of its `end_time` in that zone.
- More than 366 buckets → `VALIDATION_ERROR` with `details.reason = "RANGE_TOO_LARGE"`.
- With only `from`, `to` is today (or `from`, if later); with only `to`, `from` is 29 days earlier. Other `details.reason` values: `UNKNOWN_TIME_ZONE`, `INVALID_RANGE` (`from > to`).

**Response 200**
```json
{
  "vehicle_id": "vehicle-uuid",
  "range": { "from": "2026-11-02", "to": "2026-11-08" },
  "group_by": "day",
  "tz": "UTC",
  "model_version": "deterministic-v1",
  "includes_simulated": false,
  "excluded_simulated_trips": 0,
  "series": [
    {
      "bucket": "2026-11-08",
      "bucket_start": "2026-11-08T00:00:00Z",
      "trips": 2,
      "distance_km": 406.0,
      "energy_trips": 2,
      "observed_energy_kwh": 80.23,
      "estimated_energy_kwh": 81.2,
      "observed_kwh_per_km": 0.198,
      "estimated_kwh_per_km": 0.2,
      "mae_kwh": 0.485,
      "bias_kwh": -0.485
    }
  ],
  "totals": {
    "trips": 2,
    "distance_km": 406.0,
    "energy_trips": 2,
    "observed_energy_kwh": 80.23,
    "estimated_energy_kwh": 81.2,
    "observed_kwh_per_km": 0.198,
    "estimated_kwh_per_km": 0.2,
    "mae_kwh": 0.485,
    "bias_kwh": -0.485
  },
  "plans": { "matched": 1, "with_energy": 1, "mae_kwh": 0.29, "mean_arrival_soc_error_points": -0.3 }
}
```

- **Which legs count:** legs with `included_in_aggregates` (§3.11a): `review_status` `auto` or `confirmed`, not in progress, and not simulated unless `include_simulated=true`. `trips` and `distance_km` count all of them. `energy_trips` counts those with an observed energy *and* a model estimate, and the energy, ratio and error fields use only those legs, so observed and estimated always cover the same trips.
- `*_kwh_per_km` are distance-weighted (Σ energy / Σ distance). `mae_kwh` is the mean of |observed − estimated|; `bias_kwh` is the mean of (observed − estimated), negative when the model over-predicts. Ratios and errors are `null` when `energy_trips` is `0`.
- `bucket` labels: `2026-11-08` (day), `2026-W45` (week), `2026-11` (month). `bucket_start` is the bucket's start in `tz`, written in UTC. **Every** bucket in the range is listed, empty ones with `0` counts and `null` ratios.
- `estimated_energy_kwh` is the deterministic model (`model_version`) run on each leg's observed distance (phase5_plan D9), not the planner's route estimate.
- `plans` summarizes matched plans whose chain ended in the range: `matched` counts them, `with_energy` those whose legs all have energy, `mae_kwh` the mean |observed − predicted| of those, and `mean_arrival_soc_error_points` the mean arrival SoC error in percentage points.
- **Simulated data** is never mixed in silently: by default it is left out and `excluded_simulated_trips` says how many simulated legs in the range were skipped; with `include_simulated=true`, `includes_simulated` is `true` and the UI shows a banner.
- Energy is in kWh and distance in km; the client converts to Wh/mi or kWh/100 km.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (missing or bad params, unknown `tz`, `RANGE_TOO_LARGE`); `NOT_FOUND` (`vehicle_id` isn't the caller's).

### 3.11a `GET /api/v1/analytics/trips`

The analytics table: one row per observed leg with its model estimate. Query params: `?vehicle_id=&from=&to=&tz=&include_simulated=false&limit=&cursor=`, with the same meaning as §3.11. Newest first (`end_time` descending, ties by `id`).

**Response 200**
```json
{
  "items": [
    {
      "id": "observed-trip-uuid",
      "vehicle_id": "vehicle-uuid",
      "start_time": "2026-11-08T10:53:00Z",
      "start_time_latest": "2026-11-08T10:53:00Z",
      "end_time": "2026-11-08T13:00:00Z",
      "distance_km": 183.6,
      "soc_start_percent": 66,
      "soc_end_percent": 15,
      "observed_energy_kwh": 36.21,
      "observed_energy_uncertainty_kwh": 0.71,
      "estimated_energy_kwh": 36.72,
      "error_kwh": -0.51,
      "observed_kwh_per_km": 0.197,
      "energy_source": "soc_inferred",
      "quality_flag": "high",
      "quality_reasons": [],
      "review_status": "auto",
      "in_progress": false,
      "included_in_aggregates": true,
      "planned_trip_id": "trip-uuid",
      "plan_origin_label": "Frederick, MD",
      "plan_destination_label": "Toronto, ON",
      "model_version": "deterministic-v1",
      "is_simulated": false
    }
  ],
  "next_cursor": null
}
```

- Lists every leg in the range **except rejected ones**, including `needs_review` and in-progress legs (flagged, with `included_in_aggregates: false`), so the table explains the chart.
- `estimated_energy_kwh` and `error_kwh` are `null` when the leg has no backtest (no energy, or no baseline for the vehicle); `observed_*` are `null` when the leg has no energy.
- `plan_*_label` are the matched plan's labels (`null` when unmatched).

**Errors:** as §3.11, plus `VALIDATION_ERROR` for a bad cursor.

### 3.11b `GET /api/v1/analytics/trips.csv`

The rows of §3.11a as CSV, for the same filters (no `limit`/`cursor`: every row). `Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="chargewise-trips-<from>-<to>.csv"`.

- The header row is exactly the §3.11a field names, in the order shown; `quality_reasons` is joined with `;`. Timestamps are ISO-8601 UTC, numbers use `.` and no thousands separator, `null` is an empty cell, and booleans are `true`/`false`. Parsing the CSV gives the same values as the JSON pages.
- Text cells starting with `=`, `+`, `-`, `@`, tab or carriage return are prefixed with `'` (CSV injection; labels are user text). Numeric cells are never changed.
- At most 10,000 rows; more → `422 VALIDATION_ERROR` with `details.reason = "EXPORT_TOO_LARGE"` and `details.rows` / `details.max_rows` (narrow the range). A file is never silently truncated.

**Errors:** as §3.11a (sent as the JSON error envelope).

### 3.12 `POST /api/v1/webhooks/smartcar`

Smartcar-signed payload. The only endpoint not using Supabase auth. Behavior detailed in [`architecture.md`](architecture.md#5-webhook-ingestion-rules).

Verification, deduplication, and persistence happen here. The response body is intentionally minimal:

**Response 200**
```json
{ "accepted": true, "event_id": "smartcar-event-id" }
```

A reading that an on-demand refresh (§3.5a) already stored with the same signal and provider timestamp is skipped, so Smartcar retrying a delivery after an outage doesn't duplicate it. Readings from two different webhook events are each stored as before.

`accepted: false` means the event was signed and understood but deliberately not stored: unknown vehicle, a vehicle that isn't `connected` (phase2_plan.md D12), an event type Chargewise doesn't ingest, or a `VEHICLE_ERROR` whose shape couldn't be parsed. Smartcar treats it like any 200 and stops retrying. A duplicate delivery returns `accepted: true` and writes nothing.

**`VERIFY` response 200** (only after the signature checks out)
```json
{ "challenge": "<hex HMAC-SHA256 of data.challenge, keyed with the management token>" }
```

**Errors:** `WEBHOOK_SIGNATURE_INVALID` (401, missing or wrong `SC-Signature`), `WEBHOOK_PAYLOAD_INVALID` (400, signed but malformed). Smartcar retries on 5xx, so transient failures must return 5xx (not swallow) to trigger retry; a database failure returns `INTERNAL_ERROR` (500).

### 3.13 `GET /api/v1/places/suggest`

Place autocomplete for the Trip Planner and Charging page search boxes. The backend proxies Mapbox Search Box ([`phase3_plan.md`](phase3_plan.md) D2); the browser never calls Mapbox search directly.

Query params:

| Param | Required | Rules |
|---|---|---|
| `q` | yes | Search text, 3–200 characters after trimming. |
| `session_token` | yes | A UUID the client creates once per search session (typing → picking a result) and sends on every `suggest` and the final `places/{id}` call. Mapbox bills per session. |
| `lat`, `lon` | no (send both or neither) | Proximity bias: the map centre or the other trip endpoint. Without them results are biased by the *server's* IP, so clients should always send them. |
| `limit` | no | 1–10, default 5. |

**Response 200**
```json
{
  "items": [
    {
      "id": "dXJuOm1ieHBsYzpCK3dJSnc",
      "name": "Toronto",
      "full_address": "Toronto, Ontario, Canada",
      "kind": "place",
      "country_code": "CA"
    }
  ]
}
```

- `id` is an opaque Mapbox ID; pass it to §3.14. Suggestions carry **no coordinates**.
- `kind` is `address`, `poi` or `place` (cities, regions, postcodes, …).
- `country_code` is ISO 3166-1 alpha-2, or `null` if Mapbox gives none.
- No matches → `200` with `items: []`.
- Nothing is stored. Search text and coordinates are never logged (D13).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (short/long `q`, `session_token` not a UUID, `lat`/`lon` out of range or only one given, `limit` out of range); `RATE_LIMITED` (429, `details.limit` is `suggest`, with `details.retry_after_seconds` and a `Retry-After` header); `MAPBOX_NOT_CONFIGURED` (503); `MAPBOX_UNREACHABLE` (502/504); `MAPBOX_ERROR` (502).

**Rate limit (P6-F4).** A token bucket per user, as for §3.7: `RATE_LIMIT_SUGGEST_BURST` requests at once (default 30), refilled at `RATE_LIMIT_SUGGEST_PER_MINUTE` (default 60 per minute). That is well above a debounced search box; it stops scripted loops. `GET /places/{id}` is not limited.

### 3.14 `GET /api/v1/places/{id}`

Resolves a suggestion from §3.13 to coordinates. Query params: `?session_token=` (required, the same UUID used for the suggestions).

**Response 200**
```json
{
  "id": "dXJuOm1ieHBsYzpCK3dJSnc",
  "name": "Toronto",
  "full_address": "Toronto, Ontario, Canada",
  "kind": "place",
  "country_code": "CA",
  "location": { "lat": 43.647938, "lon": -79.38355 }
}
```

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (`session_token` not a UUID, `id` longer than 512 characters); `NOT_FOUND` if Mapbox doesn't know the ID; `MAPBOX_*` as in §3.13.

### 3.15 `POST /api/v1/routes/directions`

Plain driving directions between two points: up to three alternatives, best first. No energy model, feasibility or charging stops (that is §3.7, Phase 4). The client passes a route's `geometry_polyline6` to `POST /stations/along-route` (Phase 3 F5).

**Request**
```json
{
  "origin": { "lat": 39.4143, "lon": -77.4105 },
  "destination": { "lat": 43.6532, "lon": -79.3832 }
}
```

- `lat` is −90…90 and `lon` −180…180. Origin and destination must be more than 50 m apart.

**Response 200**
```json
{
  "routes": [
    {
      "index": 0,
      "geometry_polyline6": "iwsdjAdlwsrCg@...",
      "distance_m": 748107.9,
      "duration_s": 30890.7,
      "summary": "US 15, QEW",
      "countries": ["US", "CA"]
    }
  ],
  "no_route_reason": null
}
```

- Mapbox `driving` profile without live traffic ([`phase3_plan.md`](phase3_plan.md) D5).
- `geometry_polyline6` is an encoded polyline with precision 6, `lat,lon` order (about 40 KB for Frederick → Toronto).
- `index` is the route's position in the list (`0` = Mapbox's recommended route). It is only meaningful within this response.
- `summary` is Mapbox's main-roads summary, or `null`. `countries` lists the ISO 3166-1 alpha-2 codes the route passes through, in order, so the UI can mark border crossings.
- **No route is not an error:** `200` with `routes: []` and `no_route_reason` set to `no_route` (no drivable connection) or `no_road_near_point` (an endpoint isn't near a road). `no_route_reason` is `null` whenever `routes` is non-empty.
- Nothing is stored. Coordinates and geometry are never logged (D13).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (missing/out-of-range coordinates, points within 50 m); `MAPBOX_NOT_CONFIGURED` (503); `MAPBOX_UNREACHABLE` (502/504); `MAPBOX_ERROR` (502).

### 3.16 `GET /api/v1/stations/{id}`

One station from the cache (`id` is the Chargewise UUID from §3.10 / §3.17). Optional `?vehicle_id=` sets `compatible`, as in §3.10.

**Response 200:** one station object, the same shape as a §3.10 item.

- If the row's `fetched_at` is older than `STATIONS_CACHE_TTL_HOURS` (default 24), the server refreshes it from the provider first ([`phase3_plan.md`](phase3_plan.md) D7).
- If that refresh fails (provider down or not configured) or the provider no longer lists the station, the cached row is returned with `is_stale: true`. Detail never fails just because the provider is down.
- Restricted stations (`restricted_access: true`) are still returned here, so a cached or favorited one stays viewable.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (`id` is not a UUID); `NOT_FOUND` (unknown station, or `vehicle_id` not the caller's).

### 3.17 `POST /api/v1/stations/along-route`

Stations within a corridor around a route, in order along the route. Stateless: the client sends a `geometry_polyline6` it got from §3.15. It is a `POST` because an encoded route is too long for a URL.

**Request**
```json
{
  "geometry_polyline6": "iwsdjAdlwsrCg@...",
  "corridor_km": 3,
  "connector": ["CCS1"],
  "network": [],
  "charging_level": "dc_fast",
  "min_power_kw": 50,
  "vehicle_id": "3c9e…",
  "limit": 200
}
```

| Field | Required | Meaning |
|---|---|---|
| `geometry_polyline6` | yes | Encoded polyline, precision 6, at most 200,000 characters, at least two in-range points. |
| `corridor_km` | no | 0.5–16, default 3. |
| `connector`, `network`, `min_power_kw`, `vehicle_id` | no | As in §3.10. |
| `charging_level` | no | `dc_fast` (default, D6), `level2` or `all`. |
| `limit` | no | 1–1000, default 200: the most stations collected from the provider. |

**Response 200**
```json
{
  "items": [
    {
      "id": "8f0c7f8e-…",
      "…": "every field of a §3.10 item",
      "distance_along_route_km": 12.481,
      "distance_from_route_km": 0.734
    }
  ],
  "total_results": 122,
  "unknown_power_count": 0,
  "truncated": false
}
```

- The server decodes the route and simplifies it with Douglas–Peucker (0.1 km, at most `STATIONS_ROUTE_MAX_POINTS` points, D6). That simplified line is both sent to the provider and used to measure each station.
- Items are sorted by `distance_along_route_km`, which the server computes by projecting each station onto the route. The provider's own order is only approximate.
- There is no cursor: results are ordered along the whole route, so the server pages through the provider itself until it has every match or `limit` stations. `truncated: true` means the provider had more; because the provider returns stations roughly in route order, a truncated list is usually missing the end of the route, so the client should narrow the filters.
- Every result is upserted into the cache, so `id`s match §3.10 and §3.16.
- The route and coordinates are never logged (D13).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (geometry too large, not a valid polyline, fewer than two points or out of range; other bad fields); `NOT_FOUND` (`vehicle_id` not the caller's); `STATIONS_NOT_CONFIGURED` (503); `STATIONS_UNREACHABLE` (502/504).

### 3.19 `GET /api/v1/stations/favorites`

The caller's favorite stations ([`phase3_plan.md`](phase3_plan.md) F8, PRD §4.3), newest favorite first. Favorites belong to the user, so they survive sign-out and sign-in and are deleted with the account. Optional `?vehicle_id=` sets `compatible`, as in §3.10.

**Response 200**

```json
{
  "items": [
    {
      "id": "6f1c…",
      "name": "Tesla Supercharger Frederick",
      "…": "every field of a §3.10 station",
      "favorited_at": "2026-11-19T14:02:11Z"
    }
  ]
}
```

- Read from the `charging_stations` cache only; no provider call. `is_stale` is `true` when the row's `fetched_at` is older than `STATIONS_CACHE_TTL_HOURS`; opening the station (§3.16) refreshes it.
- Restricted, closed and private stations stay in the list: the list holds what the user starred, not a search.
- Not paged: a user has at most 500 favorites (§3.19a).
- If a station leaves the cache, its favorites go with it (`ON DELETE CASCADE`).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (`vehicle_id` not a UUID); `NOT_FOUND` (`vehicle_id` not the caller's).

### 3.19a `PUT /api/v1/stations/{id}/favorite`

Adds the station (`id` from §3.10 / §3.16 / §3.17) to the caller's favorites. No body. Idempotent: favoriting a favorite changes nothing and keeps its first `favorited_at`.

The cap holds under concurrency: a caller's `PUT`s are serialized (a row lock on the caller's profile, held until commit), so two parallel `PUT`s at 499 favorites add one and the other gets `FAVORITES_LIMIT`.

**Response 204** — no body.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (`id` is not a UUID); `NOT_FOUND` (unknown station); `FAVORITES_LIMIT` (409, the caller already has 500 favorites and this station isn't one of them).

### 3.19b `DELETE /api/v1/stations/{id}/favorite`

Removes the station from the caller's favorites. Idempotent: removing a station that isn't a favorite also returns 204.

**Response 204** — no body.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (`id` is not a UUID); `NOT_FOUND` (unknown station).

### 3.18 `GET /api/v1/observed-trips`

The caller's observed driving legs (Phase 5), inferred automatically from telemetry. One item is one leg: driving only, never a charging session; a road trip with charging stops is several legs. Design decisions: [`phase5_plan.md`](phase5_plan.md) §2 (D1–D3, D8, D14) and §3 F3. These routes live under `/observed-trips`, not `/trips/observed`, so they can't collide with `GET /trips/{id}` (§3.9).

Query params: `?vehicle_id=&review_status=auto|needs_review|confirmed|rejected&from=&to=&limit=&cursor=`.

- `vehicle_id` is optional (all the caller's vehicles); if given it must be the caller's.
- `review_status` may repeat; by default every status except `rejected` is listed.
- `from` / `to` are ISO-8601 timestamps that filter on `end_time` (inclusive / exclusive).
- Newest first (`end_time` descending, ties by `id`). Cursor pagination as §1.

**Response 200**
```json
{
  "items": [
    {
      "id": "observed-trip-uuid",
      "vehicle_id": "vehicle-uuid",
      "start_time": "2026-11-09T18:12:00Z",
      "start_time_latest": "2026-11-10T08:41:00Z",
      "end_time": "2026-11-10T08:41:00Z",
      "start_time_is_exact": false,
      "distance_km": 28.6,
      "odometer_start_km": 24712.3,
      "odometer_end_km": 24740.9,
      "soc_start_percent": 66,
      "soc_start_recorded_at": "2026-11-09T18:12:00Z",
      "soc_end_percent": 61,
      "soc_end_recorded_at": "2026-11-10T08:41:00Z",
      "energy_kwh": 3.55,
      "energy_kwh_uncertainty": 0.71,
      "energy_source": "soc_inferred",
      "usable_capacity_kwh": 71.0,
      "capacity_source": "catalog",
      "quality_flag": "low",
      "quality_reasons": ["soc_delta_small", "soc_reading_stale", "start_time_bounded"],
      "review_status": "needs_review",
      "reviewed_at": null,
      "in_progress": false,
      "planned_trip_id": null,
      "plan_leg_index": null,
      "inference_version": "telemetry-v1",
      "is_simulated": false
    }
  ],
  "next_cursor": null
}
```

- **Times:** the leg started some time in `[start_time, start_time_latest]` and its last odometer increase was recorded at `end_time`. When the start is known (`start_time_is_exact: true`) the two are equal. The UI shows a bounded start as "between …", never as an exact time.
- **Energy** is observed: `(soc_start_percent − soc_end_percent) / 100 × usable_capacity_kwh`. It is `null`, with `energy_source: null`, when a SoC reading is missing, SoC rose during the leg, or capacity is unknown; it is never derived from distance. `energy_kwh_uncertainty` comes from the SoC resolution (1 point by default).
- **Quality:** `quality_flag` is `high`, `medium` or `low`, and `quality_reasons` lists every rule the leg missed: `soc_missing`, `soc_increased`, `capacity_unknown`, `soc_delta_small`, `soc_reading_stale`, `start_time_bounded`, `odometer_regression` (definitions in phase5_plan D8).
- **Review:** `auto` (high/medium), `needs_review` (low), `confirmed` or `rejected` (§3.18c–d). Low-quality legs don't count in analytics until confirmed.
- `in_progress` is `true` for the newest leg while it may still grow (fewer than 20 minutes since its last reading, with nothing after it yet). It can change on the next telemetry event.
- `planned_trip_id` is set when the leg was matched to a plan (§3.18e). `plan_leg_index` is derived, not stored: the leg's 1-based position among that plan's legs ordered by `start_time`.
- No location is ever returned; legs have none.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` (bad params or cursor); `NOT_FOUND` (`vehicle_id` isn't the caller's).

### 3.18a `GET /api/v1/observed-trips/{id}`

One leg, same shape as an item in §3.18 (rejected legs included).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID; `NOT_FOUND` if it doesn't exist or isn't the caller's.

### 3.18b `POST /api/v1/observed-trips/recompute`

Re-runs trip inference and plan matching for one vehicle, synchronously. Inference already runs on its own after each telemetry webhook ([ADR 0009](adr/0009-in-process-post-ingest-hook-for-trip-inference.md)); this is a catch-up path (after a deploy or a fix) and is idempotent.

**Request**
```json
{ "vehicle_id": "vehicle-uuid", "full": false }
```

- `full: false` re-infers from the newest finished leg onward (the webhook path also goes back further for late readings); `full: true` rebuilds the vehicle's whole history. Confirmed and rejected legs are never changed or deleted.

**Response 200**
```json
{
  "vehicle_id": "vehicle-uuid",
  "legs_created": 2,
  "legs_updated": 1,
  "legs_deleted": 0,
  "legs_unchanged": 14,
  "plans_matched": 1,
  "dropped_simulated_readings": 0
}
```

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR`; `NOT_FOUND` (`vehicle_id` isn't the caller's).

### 3.18c `POST /api/v1/observed-trips/{id}/confirm`

Marks a leg as a real trip the caller made (`review_status: "confirmed"`, `reviewed_at` set). Any leg can be confirmed, including a rejected one. No body. Idempotent: confirming a confirmed leg keeps its first `reviewed_at`. Confirming changes neither the numbers nor `quality_flag`. A confirmed leg is frozen: inference won't change or delete it.

**Response 200** — the leg (§3.18a).

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID; `NOT_FOUND` if it doesn't exist or isn't the caller's.

### 3.18d `POST /api/v1/observed-trips/{id}/reject`

Marks a leg as not a real trip of the caller's (someone else drove, a tow, a bad reading): `review_status: "rejected"`. It's frozen, hidden from lists by default and never counted in analytics. If it was matched to a plan, the match is removed and the plan is re-matched. No body. Idempotent. Rejecting a confirmed leg is allowed.

There is no endpoint for creating an observed trip by hand (contract §6 question 3, answered in phase5_plan D3).

**Response 200** — the leg (§3.18a).

**Errors:** as §3.18c.

### 3.18e Trip observation (in `GET /api/v1/trips/{id}`)

From Phase 5, `observation` in §3.9 is `null` until a plan is matched to the legs that carried it out (phase5_plan D4), and then:

```json
{
  "observation": {
    "matched_at": "2026-11-08T13:41:00Z",
    "legs": [
      { "id": "observed-trip-uuid-1", "plan_leg_index": 1, "start_time": "2026-11-08T08:02:00Z", "end_time": "2026-11-08T10:32:00Z", "distance_km": 222.4, "soc_start_percent": 80, "soc_end_percent": 18, "energy_kwh": 44.02, "quality_flag": "high", "review_status": "auto" },
      { "id": "observed-trip-uuid-2", "plan_leg_index": 2, "start_time": "2026-11-08T10:53:00Z", "end_time": "2026-11-08T13:00:00Z", "distance_km": 183.6, "soc_start_percent": 66, "soc_end_percent": 15, "energy_kwh": 36.21, "quality_flag": "high", "review_status": "auto" }
    ],
    "observed_distance_km": 406.0,
    "planned_distance_km": 402.6,
    "predicted_energy_kwh": 80.52,
    "observed_energy_kwh": 80.23,
    "error_kwh": -0.29,
    "observed_start_soc": 0.8,
    "predicted_arrival_soc": 0.153,
    "observed_arrival_soc": 0.15,
    "arrival_soc_error_points": -0.3,
    "model_version": "deterministic-v1"
  }
}
```

- `legs` are in drive order; leg *n* is the drive from stop *n − 1* (or the origin) to stop *n* (or the destination) when the counts line up, which the UI uses to compare each planned leg with what happened.
- `observed_energy_kwh` and `error_kwh` are `null` when any leg has no energy. SoC values here are fractions, like the plan.
- A matched plan's list item (§3.8) has `status: "completed"`.

## 4. OpenAPI and typed client

- FastAPI auto-generates the OpenAPI document at `/openapi.json`.
- The TypeScript client in [`packages/api-client`](../packages/api-client/) is generated from that document. The frontend imports it; the frontend does **not** hand-write request/response types.
- Client regeneration is part of the release workflow (exact command added when `packages/api-client` is wired up).

## 5. Versioning rules

- Additive, backward-compatible changes (new optional fields, new endpoints) stay on `/api/v1`.
- Breaking changes (removed fields, renamed fields, changed semantics) require a new major version. Old versions stay available for at least one release cycle.
- Every schema change ships with an updated OpenAPI and a regenerated TS client in the same PR.

## 6. Open contract questions

- Should `/routes/plan` return multiple candidate plans (shortest-time vs fewest-stops), or always one best plan with explicit trade-off hints? Decide during Phase 4 design.
- ~~Server-side rate limits for `/routes/plan` to control Mapbox and AFDC spend — pick a bucket strategy in Phase 6.~~ **Decided (P6-F4):** an in-memory token bucket per user on `/routes/plan` and `/places/suggest`, answering `RATE_LIMITED` (429) with `Retry-After` (§3.7, §3.13). Per process is enough on one Render instance; with several instances the buckets move to Postgres.
- Whether `/api/v1/trips` should expose a `POST` for manually confirmed observed trips, or whether that stays an internal ingestion path. Decide during Phase 5.
