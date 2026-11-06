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
| `CONFLICT` | 409 | Duplicate connect, already-saved trip, etc. |
| `SMARTCAR_UNREACHABLE` | 502/504 | Smartcar failed or timed out. |
| `MAPBOX_UNREACHABLE` | 502/504 | Mapbox failed or timed out. |
| `STATIONS_UNREACHABLE` | 502/504 | AFDC/OCM failed or timed out. |
| `ROUTE_INFEASIBLE` | 200 | Returned by `/routes/plan` when no feasible plan exists (see §3.7). |
| `WEBHOOK_SIGNATURE_INVALID` | 401 | Smartcar webhook signature did not verify. |
| `INTERNAL_ERROR` | 500 | Unhandled exception; logged to Sentry. |

`ROUTE_INFEASIBLE` is deliberately **not** an HTTP error — infeasibility is a legitimate planning result, returned with `status: "infeasible"` in the response body (see §3.7).

## 2. Endpoint index

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/me` | Authenticated profile and preferences. |
| `GET` | `/api/v1/vehicles` | List the caller's connected vehicles. |
| `POST` | `/api/v1/vehicles/connect` | Start a Smartcar Connect flow. |
| `GET` | `/api/v1/vehicles/{id}` | Vehicle metadata and specs. |
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

### 3.2 `GET /api/v1/vehicles`

Lists only the caller's vehicles.

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
      "supported_signals": ["soc", "odometer", "range", "charging"],
      "last_state_recorded_at": "2026-11-06T18:52:00Z",
      "is_stale": false
    }
  ]
}
```

### 3.3 `POST /api/v1/vehicles/connect`

Starts a Smartcar Connect flow and returns the authorization URL the frontend redirects the user to. The backend stores a short-lived state nonce tied to the authenticated user.

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

The Smartcar redirect comes back to a backend-owned callback (not a frontend route), which exchanges the code, persists the Smartcar connection, subscribes the webhook, and then 302s back to `redirect_after`. The callback endpoint is **not** part of the public v1 surface — it is an internal URL. Any failure (duplicate connect, authorization denied) surfaces to the frontend as a query-string flag that the Garage page reads.

**Errors:** `CONFLICT` for a duplicate connect; `UNAUTHENTICATED` without a JWT.

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
  "supported_signals": ["soc", "odometer", "range", "charging"],
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
  }
}
```

If no telemetry has ever arrived, responds `200` with `signals: {}` and `is_stale: true`.

### 3.6 `DELETE /api/v1/vehicles/{id}`

Disconnects the Smartcar authorization, unsubscribes webhooks, and marks the vehicle row disconnected (keeping it for historical trips).

**Response 204** — no body.

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

**Errors:** `WEBHOOK_SIGNATURE_INVALID` (401). Smartcar retries on 5xx, so transient failures must return 5xx (not swallow) to trigger retry.

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
