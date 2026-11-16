# Chargewise — API Contracts

Status: **Draft (specification phase)** · Last updated: 2026-11-06

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
| `SMARTCAR_ERROR` | 502 | Smartcar rejected the request; Smartcar's error code is in `details`. |
| `SMARTCAR_NOT_CONFIGURED` | 503 | Smartcar credentials, application ID or state secret are not set on the server. |
| `MAPBOX_UNREACHABLE` | 502/504 | Mapbox failed or timed out. |
| `STATIONS_UNREACHABLE` | 502/504 | AFDC/OCM failed or timed out. |
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
| `GET` | `/api/v1/vehicles` | List the caller's connected vehicles. |
| `POST` | `/api/v1/vehicles/connect` | Start a Smartcar Connect flow. |
| `POST` | `/api/v1/vehicles/connect/complete` | Finish Connect for the signed-in user. |
| `GET` | `/api/v1/vehicles/{id}` | Vehicle metadata and specs. |
| `PATCH` | `/api/v1/vehicles/{id}/specs` | Edit usable capacity, connectors and charging curve. |
| `GET` | `/api/v1/vehicles/{id}/state` | Latest cached vehicle state. |
| `DELETE` | `/api/v1/vehicles/{id}` | Disconnect vehicle and stop telemetry. |
| `POST` | `/api/v1/routes/plan` | Generate a charging-aware trip plan. |
| `GET` | `/api/v1/trips` | List saved / completed trips. |
| `GET` | `/api/v1/trips/{id}` | One trip with stops and observations. |
| `GET` | `/api/v1/stations` | Search compatible charging stations. |
| `GET` | `/api/v1/analytics/consumption` | Aggregated consumption trends. |
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

- `units` is `imperial` or `metric`. `reserve_soc` and `preferred_charge_to` are fractions in `[0, 1]`, and after the update `reserve_soc` must be lower than `preferred_charge_to`. `max_detour_minutes` is `0`–`240`. `preferred_networks` holds at most 20 distinct, non-empty names (≤ 100 chars each).
- `primary_vehicle_id` must be one of the caller's **connected** vehicles; `null` clears it. The Dashboard shows the primary vehicle, else the most recently connected one.
- Disconnecting the primary vehicle (§3.6) clears `primary_vehicle_id`.
- `null` is only accepted for `primary_vehicle_id`; other keys must be omitted rather than set to `null`.

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` for unknown keys, out-of-range values, or `reserve_soc >= preferred_charge_to`; `NOT_FOUND` if `primary_vehicle_id` doesn't exist, belongs to another user, or isn't connected.

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
| `denied` | The user declined in Smartcar (`error=access_denied`). |
| `error` | Bad/expired `state`, `state` belongs to another user, Smartcar unreachable, no matching connection, or the vehicle is connected to another account. |

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
    ]
  }
}
```

`connectors` reflects the physical port plus adapters the user has configured. `charging_curve` is a piecewise representation consumed by the energy module.

The vehicle may be disconnected (it's still the caller's). `last_state_recorded_at`, `is_stale` and `disconnected_at` are the same as in §3.2. `usable_capacity_kwh` is `null` when it isn't known (no catalog entry and not entered by the user); `specs` is `null` if the row has none. Never calls Smartcar.

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
  ]
}
```

**Response 200** — the updated `specs` object.

- `usable_capacity_kwh`: `> 0` and `≤ 300`, at most two decimal places; `null` clears it (back to "unknown").
- `connectors`: distinct values from `NACS`, `CCS1`, `J1772`, `CHAdeMO`; `[]` clears them.
- `charging_curve`: at most 20 points; `soc` in `[0, 1]` and strictly increasing; `power_kw` `> 0` and `≤ 1000`; `[]` clears it.
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

**Errors:** `UNAUTHENTICATED`; `VALIDATION_ERROR` if `id` isn't a UUID; `NOT_FOUND` if it doesn't exist **or belongs to another user**.

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

The main planning call. Deterministic MVP implementation; later upgraded to the SoC-discretized graph search (see [`architecture.md`](architecture.md#6-routing-algorithm)).

**Request**
```json
{
  "vehicle_id": "vehicle-uuid",
  "origin": { "lat": 39.4143, "lon": -77.4105, "label": "Frederick, MD" },
  "destination": { "lat": 43.6532, "lon": -79.3832, "label": "Toronto, ON" },
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

- `starting_soc_override` is required when the cached vehicle state is stale.
- `preferences` falls back to the user's stored defaults when omitted.

**Response 200 (feasible)**
```json
{
  "status": "feasible",
  "route": {
    "distance_km": 743.2,
    "duration_minutes": 512,
    "polyline": "<encoded>",
    "arrival_soc": 0.21
  },
  "stops": [
    {
      "sequence": 1,
      "station": {
        "id": "station-uuid",
        "name": "EVgo - Harrisburg",
        "connector": "CCS1",
        "power_kw": 150,
        "location": { "lat": 40.2732, "lon": -76.8867 }
      },
      "arrival_soc": 0.18,
      "departure_soc": 0.80,
      "charging_minutes": 28,
      "detour_minutes": 4
    }
  ],
  "total_minutes": 554,
  "energy_kwh_estimated": 148.6,
  "model_version": "deterministic-v1"
}
```

**Response 200 (infeasible)**
```json
{
  "status": "infeasible",
  "reason": {
    "code": "NO_COMPATIBLE_CHARGER_IN_RANGE",
    "message": "No charger compatible with the vehicle's connectors is reachable from the current SoC.",
    "limiting_segment": {
      "from": { "lat": 44.0, "lon": -78.0 },
      "to":   { "lat": 44.5, "lon": -78.3 },
      "required_soc": 0.42
    }
  }
}
```

Infeasibility codes (grow as needed): `NO_COMPATIBLE_CHARGER_IN_RANGE`, `STARTING_SOC_TOO_LOW`, `RESERVE_SOC_UNREACHABLE`, `DESTINATION_UNREACHABLE`.

**Errors:** `VALIDATION_ERROR`, `NOT_FOUND` (vehicle), `MAPBOX_UNREACHABLE`, `STATIONS_UNREACHABLE`.

### 3.8 `GET /api/v1/trips`

Lists saved plans and observed trips for the caller.

Query params: `?vehicle_id=...&status=planned|completed&limit=&cursor=`.

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
      "created_at": "2026-11-06T18:00:00Z"
    }
  ],
  "next_cursor": null
}
```

### 3.9 `GET /api/v1/trips/{id}`

Returns the full plan and, when present, the matched observation.

**Response 200**
```json
{
  "id": "trip-uuid",
  "vehicle_id": "vehicle-uuid",
  "status": "planned",
  "plan": { "...": "same shape as /routes/plan 200" },
  "observation": null
}
```

### 3.10 `GET /api/v1/stations`

Search chargers independent of trip planning.

Query params: `?lat=&lon=&radius_km=&connector=&network=&min_power_kw=&limit=&cursor=`.

**Response 200**
```json
{
  "items": [
    {
      "id": "station-uuid",
      "provider": "afdc",
      "external_id": "AFDC-12345",
      "name": "Tesla Supercharger - Frederick",
      "operator": "Tesla",
      "connectors": [
        { "type": "NACS", "count": 8, "power_kw": 250 },
        { "type": "CCS1", "count": 0, "power_kw": null }
      ],
      "location": { "lat": 39.4150, "lon": -77.4120 },
      "live_availability": null
    }
  ],
  "next_cursor": null
}
```

`live_availability` is `null` unless the provider returns verified live data. It is **never** fabricated.

### 3.11 `GET /api/v1/analytics/consumption`

Aggregated consumption trends for the caller's observed trips.

Query params: `?vehicle_id=&from=&to=&group_by=day|week|month`.

**Response 200**
```json
{
  "vehicle_id": "vehicle-uuid",
  "range": { "from": "2026-10-01", "to": "2026-11-06" },
  "series": [
    {
      "bucket": "2026-10",
      "trips": 12,
      "distance_km": 842.1,
      "observed_energy_kwh": 171.4,
      "estimated_energy_kwh": 164.2,
      "mae_kwh_per_trip": 2.1
    }
  ]
}
```

Rows where every included trip is `is_simulated = true` are excluded by default. A `?include_simulated=true` flag can opt in for the public demo.

### 3.12 `POST /api/v1/webhooks/smartcar`

Smartcar-signed payload. The only endpoint not using Supabase auth. Behavior detailed in [`architecture.md`](architecture.md#5-webhook-ingestion-rules).

Verification, deduplication, and persistence happen here. The response body is intentionally minimal:

**Response 200**
```json
{ "accepted": true, "event_id": "smartcar-event-id" }
```

`accepted: false` means the event was signed and understood but deliberately not stored: unknown vehicle, a vehicle that isn't `connected` (phase2_plan.md D12), an event type Chargewise doesn't ingest, or a `VEHICLE_ERROR` whose shape couldn't be parsed. Smartcar treats it like any 200 and stops retrying. A duplicate delivery returns `accepted: true` and writes nothing.

**`VERIFY` response 200** (only after the signature checks out)
```json
{ "challenge": "<hex HMAC-SHA256 of data.challenge, keyed with the management token>" }
```

**Errors:** `WEBHOOK_SIGNATURE_INVALID` (401, missing or wrong `SC-Signature`), `WEBHOOK_PAYLOAD_INVALID` (400, signed but malformed). Smartcar retries on 5xx, so transient failures must return 5xx (not swallow) to trigger retry; a database failure returns `INTERNAL_ERROR` (500).

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
- Server-side rate limits for `/routes/plan` to control Mapbox and AFDC spend — pick a bucket strategy in Phase 6.
- Whether `/api/v1/trips` should expose a `POST` for manually confirmed observed trips, or whether that stays an internal ingestion path. Decide during Phase 5.
