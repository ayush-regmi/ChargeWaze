# Maps and charging-station providers: verified notes (Phase 3 F0)

Status: **Verified 2026-11-16/17 (F0 complete)** · Spike script: [`services/api/scripts/maps_stations_spike.py`](../services/api/scripts/maps_stations_spike.py) · Plan: [`phase3_plan.md`](phase3_plan.md)

Facts here come from real calls made during the F0 spike, unless marked *docs only*. Raw responses stay in the gitignored `services/api/scripts/spike_out/phase3/`. Trimmed copies are in [`fixtures/stations/afdc/`](../fixtures/stations/afdc/) and [`fixtures/mapbox/`](../fixtures/mapbox/).

## 1. AFDC (Alternative Fuels Data Center) stations API

### 1.1 Host and keys

- **The host moved.** NREL is now the National Laboratory of the Rockies, and `developer.nrel.gov` **no longer resolves**. The API is at **`https://developer.nlr.gov/api/alt-fuel-stations/v1`**. Docs: [`developer.nlr.gov/docs/transportation/alt-fuel-stations-v1/`](https://developer.nlr.gov/docs/transportation/alt-fuel-stations-v1/). The error bodies still say "Get one at https://developer.nrel.gov/signup/". The adapter's base URL must be a setting (`AFDC_API_BASE_URL`), not a hard-coded host.
- `api_key` is required. Without it you get HTTP **403** `{"error": {"code": "API_KEY_MISSING", …}}` ([fixture](../fixtures/stations/afdc/error_api_key_missing.json)).
- **On `POST`, `api_key` must go in the query string.** A key in the form body is ignored, and you get the 403 above.
- Rate limit: 1,000 requests/hour per key (*docs only*). `DEMO_KEY` was limited to **10/hour** (`X-Ratelimit-Limit: 10`). Responses carry `X-Ratelimit-Limit` / `X-Ratelimit-Remaining`; the adapter should log `remaining`.

### 1.2 Canada (plan D1)

- AFDC includes Canadian stations (from Natural Resources Canada). On 2026-11-16, `fuel_type=ELEC` returned **91,691 US**, **15,253 CA** and **106,944 `all`** stations.
- **`country` defaults to `US`.** The same cross-border route returned 47 DC fast stations with `country` omitted, and 96 (47 US + **49 ON**) with `country=all`. The adapter must always send `country=all`, or Canadian stations silently disappear.
- Station lookup by ID (`/v1/{id}.json`) works for a Canadian station with no `country` parameter.
- **Result: Open Charge Map is not needed.** The statement "AFDC has no Canadian coverage" in the PRD, architecture doc and roadmap was wrong and is now corrected.

### 1.3 `nearby-route`

- `POST /v1/nearby-route.json` with `application/x-www-form-urlencoded` body; `GET` also works (*docs*). `route` is WKT `LINESTRING(lon lat, lon lat, …)`, **longitude first** (verified with the cross-border route).
- `distance`: miles from the route, default 5, maximum 100 (*docs*). `limit` 0–200 or `all`; `offset` for paging.
- **Order:** the docs say "ascending distance from the route", but results come back **roughly in order along the route**. On the real Mapbox route, 120 of 121 consecutive pairs were in route order. `distance` (mi) and `distance_km` are the offsets from the route, and they're not sorted. **Don't rely on the order:** the stations module computes each station's distance along the route itself (projection onto the polyline) and sorts by that.
- Volume (2 mi corridor, `status=E`, `access=public`): on the hand-made 9-point line, **955 stations** at all levels and **96** DC fast. On the real Mapbox routes, DC fast only: **route A 122 (41 US / 81 CA)**, **route B 147–148 (81 US / 66–67 CA)**. A Level 2 search needs several pages; DC fast fits in one page of 200.
- **Route size:** AFDC accepted the **full 9,719-point route** (194 KB of WKT in the POST body) and returned the same 122 stations as the 237-point simplified route. There's no practical limit; simplification only keeps the request small.
- **Simplification tolerance:** Douglas–Peucker at 0.25 km (≤ 300 points) gave 122 / 147 stations; at 0.05 km (611–655 points) 122 / 148. One station at the corridor edge was lost at 0.25 km. With a 2 mi (3.2 km) corridor that's acceptable; use a tolerance of at most **0.1 km** and a cap of **1,000 points** (about 20 KB), which matches the full route here.
- Latency: 0.5–2.4 s per call (2.4 s for the full 194 KB route).

### 1.4 Station fields we use

| Field | Notes |
|---|---|
| `id` | Integer; it becomes `charging_stations.external_id` (as text). |
| `station_name`, `street_address`, `city`, `state` (province for CA), `zip` (postal code for CA), `country` (`US`/`CA`) | |
| `latitude`, `longitude` | |
| `ev_network` | Name, e.g. `Tesla`, `Tesla Destination`, `ChargePoint Network`, `FLO`, `Electrify Canada`, `Non-Networked`, `eVgo Network`. Codes are listed by the `electric-networks` endpoint. |
| `ev_connector_types` | `J1772`, `TESLA` (= NACS/J3400), `J1772COMBO` (= CCS1), `CHADEMO`, `J3271` (MCS), `NEMA1450`, `NEMA515`, `NEMA520`. |
| `ev_charging_units[]` | `{charging_level: "2"\|"dc_fast"\|"1", port_count, network, funding_sources, connectors: {TYPE: {port_count, power_kw}}}`. **Every unit lists all 8 connector types**; types the unit doesn't have have `port_count: 0`, so filter on `port_count > 0`. In the sample, the per-unit types matched `ev_connector_types` and DC port totals matched `ev_dc_fast_num` on all 200 stations. |
| `ev_dc_fast_num`, `ev_level2_evse_num`, `ev_level1_evse_num` | Port counts by level; `null` means zero. |
| `status_code` | `E` open, `P` planned, `T` temporarily unavailable. |
| `access_code`, `access_detail_code`, `restricted_access` | `public`/`private`; detail codes `CALL`, `FLEET`, `RESIDENTIAL`, …; `restricted_access: true` on 9 of 200 (e.g. a dealer). |
| `ev_pricing` | Free text, often `null`. Shown as text if present; never parsed into numbers. |
| `updated_at` (ISO-8601 UTC), `date_last_confirmed` (date) | Provider timestamps; `updated_at` becomes `source_updated_at`. |
| `facility_type`, `maximum_vehicle_class`, `unverified`, `related_stations` | Kept in `raw_payload` only. |

### 1.5 Power coverage (plan D9/D10)

Counts are connectors with `port_count > 0`.

| Connector / level | `power_kw` known |
|---|---|
| CCS1 (`J1772COMBO`) DC fast | 51 / 51 |
| NACS (`TESLA`) DC fast | 57 / 57 |
| CHAdeMO DC fast | 8 / 8 |
| J1772 Level 2 | 432 / 470 |
| NACS Level 2 (Tesla Destination) | 29 / 50 |

- DC fast power was known for every connector in the sample. Unknown power is mainly a Level 2 issue.
- `ev_power_kw_min=150` keeps a station if **any** of its ports is ≥ 150 kW (46 of 96). Stations with mixed 50 kW and 350 kW ports pass. Because of plan D10 (unknown-power stations stay listed), we filter on our side and don't send `ev_power_kw_min`.
- A "dc_fast" unit can also list low-power connectors (e.g. CCS 20 kW; J1772 6.2 kW on the same unit). Show power per connector, not per station.

### 1.6 Tesla Superchargers and the bZ

- Superchargers appear as `ev_network: "Tesla"` with `TESLA` connectors (some newer sites also list `J1772COMBO`). Tesla Destination chargers are `Tesla Destination`, Level 2.
- **There is no field saying whether a Supercharger site accepts non-Tesla vehicles.** `groups_with_access_code` is just `"Public"`, and `restricted_access` is `false`. We show the connector type and network and never claim "bZ-compatible" beyond the connector match (plan D9). This is an open gap.

### 1.7 Errors

- Unknown station ID → HTTP **404** with `{"errors": [...], "error_details": [{"code": "INVALID_PARAM", "status": "422", …}]}` ([fixture](../fixtures/stations/afdc/station_not_found.json)). Treat the HTTP 404 as "gone" (the cache keeps the row and marks it stale), not as a 422.
- Invalid parameter → HTTP 422 with details (*docs*).

## 2. Mapbox

### 2.1 Directions (plan D5)

- `GET /directions/v5/mapbox/driving/{lon,lat;lon,lat}?alternatives=true&geometries=polyline6&overview=full&steps=false` ([fixture](../fixtures/mapbox/directions_frederick_toronto.json)).
- Frederick → Toronto returned **2 routes**: A 748.1 km / 8.58 h (`US 15, QEW`) and B 805.0 km / 8.64 h (`US 15, I 99 North`), 1.2 s.
- Route geometry is large: **9,719 and 10,034 points**, 42–44 KB of `polyline6` each. A response with both is 92 KB. That fits plan D4 (client sends the geometry back to `/stations/along-route`, limit 200 KB), but the frontend shouldn't keep resending it; along-route requests are made once per route.
- Response: `code`, `routes[] {distance (m), duration (s), geometry, legs[] {summary, admins[], distance, duration, …}, weight, weight_name}`, `waypoints[] {name, location, distance}` (how far the input point was snapped, in m), `uuid`. `legs[0].admins` lists the countries crossed (`US`, `CA`), handy for showing "crosses the border".
- **Failure modes (all HTTP 200 with `routes: []`):** `code: "NoRoute"` when there's no road connection (Oahu → Maui; Frederick → Honolulu) and `code: "NoSegment"` when a point isn't near any road (mid-Atlantic). Both map to `no_route_reason` (`no_route` / `no_road_near_point`), not an error.
- Invalid token → HTTP **401** `{"message": "Not Authorized - Invalid Token"}` → `MAPBOX_NOT_CONFIGURED` (503).

### 2.2 Search Box (plan D2)

- `GET /search/searchbox/v1/suggest?q=&session_token=&proximity=&country=us,ca&limit=5&language=en` → `{suggestions[], attribution, response_id}`. Each suggestion: `mapbox_id`, `name`, `feature_type` (`place`, `locality`, `poi`, `address`, …), `place_formatted`, `full_address` (addresses and POIs), `context` (country/region codes), `maki`. **No coordinates.**
- `GET /search/searchbox/v1/retrieve/{mapbox_id}?session_token=` → GeoJSON `FeatureCollection`; `features[0].properties` has `name`, `full_address`, `feature_type`, `coordinates {latitude, longitude}`, `bbox`, `context`.
- Addresses, cities and POIs (e.g. "Tesla Supercharger Frederick" → five POIs with street addresses) all come from the same endpoint, which meets PRD §4.2.
- `session_token` is **required** (HTTP 400 "Session Token is required" without it), but any string is accepted. Our API requires a UUID v4 so billing sessions are well formed.
- **Proximity:** without `proximity`, Mapbox biases results by the **caller's IP address**. In the spike, a business near the caller ranked second for "Toronto". Through our backend, the caller is the server, so the bias would be wrong. `proximity=none` is rejected (HTTP 400: must be `lon,lat` or `ip`). **The frontend always sends `lat`/`lon` (the map centre or the other trip endpoint) and the backend passes it as `proximity`.** With `proximity=-79.38,43.65`, all five "Toronto" results were in Toronto.
- Latency: suggest 0.3–0.7 s, retrieve 0.1–0.15 s from this machine.

### 2.3 Free tiers (from [mapbox.com/pricing](https://www.mapbox.com/pricing), 2026-11-17)

| Service | Free per month | Then |
|---|---|---|
| Map loads for web (Mapbox GL JS) | 50,000 | $5.00 per 1,000 |
| Directions API | 100,000 requests | $2.00 per 1,000 |
| Search Box API | 2,500 sessions (standard pricing; 500 on introductory preview pricing). A session is up to 50 `suggest` calls + 1 `retrieve`, and expires after 2 minutes of inactivity. | |

AFDC: 1,000 requests/hour per key (the real key returned `X-Ratelimit-Limit: 1000`).

### 2.4 Token setup (plan D3)

- `NEXT_PUBLIC_MAPBOX_PUBLIC_TOKEN` (`apps/web/.env.local`): a public token (`pk.`) with the default public scopes (`styles:tiles`, `styles:read`, `fonts:read`). **URL-restricted** to `http://localhost:3000` (and the production origin later). It's only used by Mapbox GL JS to draw the map.
- `MAPBOX_SECRET_TOKEN` (`services/api/.env`): a separate token with **no URL restriction**, because URL restrictions only work for browser requests. Only the backend uses it, for Directions and Search Box. It never goes in a `NEXT_PUBLIC_*` variable.

## 3. Open gaps

- Tesla non-Tesla access (§1.6): no data source yet.
- AFDC exposes no live availability, so `live_availability` stays `null` (PRD §4.3).
- Real-world AFDC order along the route is only "roughly" ordered (§1.3); we sort ourselves.
