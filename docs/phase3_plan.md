# Phase 3 Plan: Maps and Chargers

Status: **Approved 2026-11-16**: all recommendations in §2 (D1–D16) accepted, F8 (favorites) stays in Phase 3 · Created: 2026-11-16 · Phase owner: project lead

This is the working plan for [Phase 3 of the roadmap](development-roadmap.md#phase-3--maps-and-chargers-weeks-56). It splits the phase into features. Each feature is its own vertical slice (DB → backend → frontend → tests) with its own branch and acceptance criteria. We build and test them one at a time, in the order below, the same way as [Phase 2](phase2_plan.md).

The plan is based on: [`development-roadmap.md`](development-roadmap.md) Phase 3 and §2 slices 4–5, [`product-requirements.md`](product-requirements.md) §4.2, §4.3, §6, §7, §8 and §10, [`architecture.md`](architecture.md) §1–3, §8, §9 and §11, [`api-contracts.md`](api-contracts.md) §1 and §3.10, [`database-design.md`](database-design.md) §3.6 and §6, and ADRs [0001](adr/0001-nextjs-fastapi-modular-monolith.md) and [0003](adr/0003-own-charging-optimizer-over-mapbox-ev-routing.md).

**Not in Phase 3:** energy model, feasibility, charging-stop insertion, `POST /routes/plan` and trip persistence (`planned_trips` / `planned_stops`). Those are Phase 4. Phase 3 delivers the map, the routes and the stations that Phase 4's optimizer will use.

---

## 0. Carry-over from Phase 2

These items don't block Phase 3 code, but they stay open in [`phase2_plan.md`](phase2_plan.md) F8 until they're done:

- E3 tail: trigger an event from the bZ after disconnecting, and confirm nothing is stored.
- Measure the event cadence. Record the number and the chosen freshness threshold in the roadmap risk table.
- Update the roadmap status line to "Phase 2 complete" once both are recorded.

---

## 1. Goal and exit criteria

**Goal:** a signed-in user searches for a place, sees one or more driving routes on a Mapbox map, and sees charging stations near each route that are compatible with their vehicle. Canadian stations work too. The Charging page lets the user browse and filter stations without planning a trip.

Phase 3 is done when all of these are true. E1–E3 come from the roadmap; the rest come from PRD §4.2, §4.3, §6 and §7.

| # | Exit criterion | Verified by |
|---|---|---|
| E1 | The user can search a destination, see one or more driving routes on the map, and see compatible charging stations near each route. | Playwright (fixture-backed), plus a manual run with real keys and a screenshot in the PR. |
| E2 | A Frederick, MD → Toronto, ON test trip returns stations on both sides of the border. | Manual live run, recorded as a redacted fixture; a pytest case on that fixture asserts stations with `country` `US` and `CA`. |
| E3 | No Mapbox server token or AFDC key reaches the browser bundle. The only Mapbox token in the browser is the public one. | The existing CI sentinel grep, extended to the new secrets. |
| E4 | Connector filters default to the active vehicle's connectors. Stations show no availability unless the provider gives live data. The station detail page shows connector types, counts, operator and address, and leaves missing values empty. | pytest + Vitest + Playwright. |
| E5 | Station fetches are cached in `charging_stations`, unique on (`provider`, `external_id`), and IDs stay the same across fetches. | pytest. |
| E6 | When Mapbox or the station provider is unreachable, the API returns `MAPBOX_UNREACHABLE` / `STATIONS_UNREACHABLE` and the UI shows an error state (PRD §6). | pytest + Vitest. |
| E7 | CI is green on `main` (ruff, mypy, pytest, Alembic round-trip, lint, typecheck, Vitest, Playwright, bundle secret check). | GitHub Actions. |

---

## 2. Decisions and doc conflicts to settle before coding

`CLAUDE.md` says doc conflicts must be settled before the affected feature starts. D1, D5, D6, D9 and D10 depend on provider facts that F0 confirms. If F0 contradicts a recommendation, this table is updated before F1.

**F0 AFDC run (2026-11-16), see [`maps-stations-notes.md`](maps-stations-notes.md):**
- **D1 resolved: AFDC only.** With `country=all`, the cross-border route returned 49 Ontario + 47 US DC fast stations. No OCM adapter, no OCM key. The PRD, architecture doc, roadmap and contract are corrected. `charging_stations.provider` allows `afdc` and `simulated` only (F1).
- **Host moved:** `developer.nrel.gov` no longer resolves; the API is `https://developer.nlr.gov/api/alt-fuel-stations/v1` (setting `AFDC_API_BASE_URL`). On `POST`, `api_key` goes in the query string.
- **`country` defaults to `US`:** the adapter must always send `country=all` (F3 test).
- **D6:** `nearby-route` results come back *roughly* in route order, not by distance (the docs are wrong), so F5 sorts by distance along the route itself. Along-route defaults to `charging_level=dc_fast` (96 results on the test route vs 955 at all levels) and pages with `offset` when asked for Level 2.
- **D9:** `TESLA` → `NACS`, `J1772COMBO` → `CCS1`, `J1772`, `CHADEMO` → `CHAdeMO`; drop `NEMA*` and `J3271` (MCS, trucks). Per-connector `count` and `power_kw` come from `ev_charging_units`, which lists every connector type on every unit, so only connectors with `port_count > 0` count. No field says whether a Supercharger accepts non-Tesla cars, so compatibility is the connector match only.
- **D10:** DC fast power was known for every DC connector in the sample; unknown power is mostly Level 2. AFDC's `ev_power_kw_min` matches if *any* port qualifies, so we filter on our side (on the station's known maximum) to keep unknown-power stations, as decided.
- **D8:** also map `restricted_access` / `access_detail_code` (kept in the API response as `access_detail`), and keep `ev_pricing` text as `pricing_text` (shown as-is, never parsed).

**F0 Mapbox run (2026-11-17). F0 is done:**
- **E2 confirmed live:** both real Frederick → Toronto routes return DC fast stations on both sides of the border: route A (QEW) 41 US / 81 CA, route B (I-99) 81 US / 66 CA. The route A response is the E2 fixture.
- **D2:** Search Box returns addresses, places and POIs from one endpoint; `suggest` has no coordinates, `retrieve` does. `session_token` is required. Without `proximity`, Mapbox biases by the *caller's IP*, which would be our server's, so the client always sends `lat`/`lon` (map centre, or the other trip endpoint) and the backend passes it as `proximity`. `lat`/`lon` stay optional in the contract; when they're missing the backend sends `proximity=ip`, which is still the server's IP, so this is a fallback only.
- **D5:** two failure codes, both HTTP 200 with `routes: []`: `NoRoute` → `no_route_reason: "no_route"`, `NoSegment` (point not near a road) → `"no_road_near_point"`. Invalid token → 401 → `MAPBOX_NOT_CONFIGURED`. Each route is about 10,000 points and 43 KB of `polyline6`.
- **D6:** AFDC accepted the full 9,719-point route (194 KB); there's no practical size limit. Simplify at tolerance **0.05–0.1 km** with a cap of **1,000 points** (`STATIONS_ROUTE_MAX_POINTS=1000`); at 0.25 km one corridor-edge station was lost. Each route's geometry is about 43 KB, well under the 200 KB request limit on `/stations/along-route`.
- **D15 quotas:** Mapbox free tiers are 50,000 web map loads, 100,000 Directions requests, 2,500 Search Box sessions per month; AFDC is 1,000 requests/hour per key.

| # | Issue | Where | Recommendation |
|---|---|---|---|
| D1 | **Canadian station provider (doc conflict).** *Resolved in F0: AFDC only.* The PRD (§10), architecture (§1, §11) and the roadmap risk table say "AFDC has no Canadian coverage". That looks wrong. NREL partnered with Natural Resources Canada in 2018: NRCan's locator uses AFDC data, and Canadian stations are published in the same API. NREL's docs show a `country` parameter (`US` / `CA` / `all`), at least on the electric-networks endpoint. I couldn't call the API from this session, so this is unverified. | prd §10, arch §1/§11, roadmap §5 | F0 calls AFDC with `country=CA` and `country=all` on the all-stations, `nearest` and `nearby-route` endpoints, using a route that crosses into Ontario. **If Canadian stations come back, use AFDC only** (it already includes NRCan's data), close the open question, and fix the three docs. If not, add **Open Charge Map for `CA` only** behind the same provider interface. Its licence is CC BY-SA 4.0, so we'd show attribution and need an API key. |
| D2 | **Where autocomplete runs.** Architecture §2 allows the browser to call Mapbox search with the public token. The roadmap puts "Search Box autocomplete" inside the backend `integrations/mapbox` adapter. | arch §2, roadmap Phase 3 | **Proxy through the backend**: `GET /api/v1/places/suggest` and `GET /api/v1/places/{id}`. This keeps "the browser talks to one backend", gives us fixtures, structured logs and future rate limits, lets the future React Native client reuse it, and lets the public token be limited to map tiles and styles. The cost is one extra hop (about 50 ms), so the UI debounces by 250 ms. Search Box session tokens are created in the browser and passed through, so billing stays per session. |
| D3 | **Mapbox tokens.** `MAPBOX_SECRET_TOKEN` is already in `.env.example` and the CI grep. Mapbox URL restrictions only work for browser requests, so a URL-restricted token fails when the server uses it. | env templates, prd §8 | Two tokens. `NEXT_PUBLIC_MAPBOX_PUBLIC_TOKEN`: `styles:read` and `fonts:read` scopes plus tiles, URL-restricted to `localhost:3000` and the production origin. `MAPBOX_SECRET_TOKEN`: server only, with no URL restriction, used for Directions and Search Box. Keep the existing name so CI and docs don't change. Record both setups in the F0 notes. |
| D4 | **Endpoints the contract doesn't have yet.** `api-contracts.md` only has `GET /stations`. Phase 3 also needs place search, plain directions (no planning), stations along a route, and station detail. | api §2 | Add to the contract **before** each feature. `GET /places/suggest` and `GET /places/{id}` (F4); `POST /routes/directions` (F4); `GET /stations/{id}` and `POST /stations/along-route` (F5). Along-route is a `POST` because an encoded route is too long for a URL. It is stateless: the client sends the `polyline6` it got from `/routes/directions`, with a size limit. `POST /routes/plan` stays Phase 4. |
| D5 | **Directions options.** | arch §6.3 | Mapbox `driving` profile, `alternatives=true` (up to 3 routes), `geometries=polyline6`, `overview=full`, `steps=false`. No live traffic: `driving-traffic` depends on departure time and doesn't fit planning. Return `distance_m` and `duration_s` per route. Phase 4 adds the annotations it needs. "No route" (Mapbox `NoRoute`) returns **200 with `routes: []`** and a `no_route_reason`, not an error, in the same spirit as `ROUTE_INFEASIBLE`. |
| D6 | **Corridor and route simplification for "stations near a route".** | arch §6.3 | `corridor_km` defaults to 3 (about 2 mi), range 0.5–16. Before calling AFDC `nearby-route`, the backend simplifies the route with Douglas–Peucker to at most `STATIONS_ROUTE_MAX_POINTS` (default 1,000 after F0; tolerance ≤ 0.1 km). We implement simplification and polyline encoding ourselves (about 60 lines), with no new dependency. |
| D7 | **Station caching policy.** Roadmap: cache fetches in `charging_stations`, unique on `provider` + `external_id`. The contract's freshness rule says cached provider data carries `recorded_at` / `is_stale`. | db §3.6, api §1 | Searches always go to the provider. Each result is **upserted** into `charging_stations`, so the station `id` is a stable Chargewise UUID (needed by favorites and Phase 4's `planned_stops`). `GET /stations/{id}` reads the cache and refreshes from the provider if `fetched_at` is older than `STATIONS_CACHE_TTL_HOURS` (default 24). If the refresh fails, it returns the cached row with `is_stale: true`. If a search fails, it returns `STATIONS_UNREACHABLE`; Phase 3 doesn't fall back to the cache for searches. |
| D8 | **`charging_stations` columns.** The spec lacks provenance and fields the UI needs. | db §1, §3.6 | Add `network`, `street_address`, `city`, `region`, `postal_code`, `country` (ISO-2), `access` (`public` / `private` / `unknown`), `status` (`open` / `planned` / `temporarily_unavailable` / `unknown`), `source_updated_at` (provider timestamp), `raw_payload` (jsonb), `is_simulated` (default false) and `created_at`. Turn on RLS. Keep the `(lat, lon)` index and leave PostGIS out (db §8). Default searches return only `status = open` and `access = public`. |
| D9 | **Connector mapping and counts.** AFDC reports connector *types* per station and port counts per *level*. It may also report per-port power through `ev_charging_units` (F0 checks). The contract's `count` is a required integer. | api §3.10 | Map `TESLA`/`NACS` → `NACS`, `J1772COMBO` → `CCS1`, `J1772` → `J1772`, `CHADEMO` → `CHAdeMO`. Drop NEMA outlets. Make `count` and `power_kw` **nullable** in the contract and fill them only when the provider gives them; never derive per-connector counts from level totals. A station is *compatible* if any of its connector types is in the vehicle's `vehicle_specs.connectors`. Whether a Tesla Supercharger site is open to non-Tesla cars is shown only if the provider says so; F0 checks whether AFDC exposes it. |
| D10 | **Power filter when power is unknown.** | prd §4.3, §7 | `min_power_kw` hides stations whose *known* maximum power is lower. Stations with unknown power **stay in the results** with a "Power unknown" label, and the response counts them in `unknown_power_count`. Also add `charging_level=level2|dc_fast`, which maps to AFDC's level filter and always has data. |
| D11 | **Map library.** | arch §1 | Use `mapbox-gl` v3 directly; no `react-map-gl` wrapper. It goes in one client-only component (`components/map/map-view.tsx`) loaded with `next/dynamic({ ssr: false })`. Polyline decoding is a small helper in `lib/polyline.ts`. Vitest replaces the map with a stub. Mapbox and AFDC attribution is always visible. New runtime dependency: `mapbox-gl` (justified in the PR). |
| D12 | **"Use my location".** PRD §4.2 wants the vehicle's last known location as an origin. Phase 2 D7 didn't request Smartcar location. | prd §4.2, §8 | Vehicle location stays deferred (Phase 5 revisits the scope). Phase 3 offers an **opt-in browser geolocation** button for the trip origin and for "near me" on the Charging page. Coordinates are only sent to our API in the request. No feature in Phase 3 stores them. |
| D13 | **Location privacy in logs.** | guidelines Security, db §6 | Adapter logs include provider, endpoint template, duration and status, as in Phase 2. Logs never include search text, addresses, polylines, or coordinates beyond 2 decimal places. Mapbox session tokens and API keys are never logged. A pytest case checks a captured log for these. |
| D14 | **Where the new code lives.** | arch §3 | `integrations/mapbox/` (directions, Search Box) and `integrations/stations/` (`StationProvider` protocol, `afdc.py`) are adapters only. New module `modules/places/` (suggest/retrieve endpoints). `modules/routing/` gets only `POST /routes/directions` now; Phase 4 builds the optimizer there. New module `modules/stations/` owns `charging_stations`, the cache, search and along-route endpoints, and favorites. These are subfolders inside existing directories, not new top-level directories. |
| D15 | **Cost and rate limits.** AFDC allows about 1,000 requests/hour per key. Mapbox has free tiers per API. | roadmap §5 | F0 records the real quotas. The client debounces autocomplete (250 ms, at least 3 characters) and uses session tokens. Along-route is fetched only for the selected route first, then the others in the background. No server-side rate limiting until Phase 6, as the roadmap says. |
| D16 | **Tests without real providers.** | arch §8, guidelines | Default CI uses fixtures and fake clients only (dependency overrides, as with Smartcar). Playwright points `MAPBOX_API_BASE_URL` / `AFDC_API_BASE_URL` at a small local stub that serves fixtures, like the Auth stub. Live tests get the `live` pytest marker and are skipped by default. A `workflow_dispatch` job can run them with real keys later. |

---

## 3. Feature breakdown and order

Each feature is one branch (`feature/phase3-<slug>`) and one PR, and is merged before the next starts. The order follows the roadmap's "map-and-search slice → compatible-stations slice". Adapters come before endpoints, and endpoints before UI, so each PR can be tested on its own.

| # | Feature (session) | Branch | Depends on | Size |
|---|---|---|---|---|
| F0 | Provider spike: Mapbox + AFDC (+ OCM if needed), tokens, fixtures (no production code) | `feature/phase3-provider-spike` | — | S |
| F1 | Station data model + migration | `feature/phase3-station-schema` | F0 | S |
| F2 | Mapbox adapter (`integrations/mapbox`) + fixtures | `feature/phase3-mapbox-adapter` | F0 | M |
| F3 | Stations adapter (`integrations/stations`) + fixtures | `feature/phase3-stations-adapter` | F0 | M |
| F4 | Places + directions API: `/places/suggest`, `/places/{id}`, `/routes/directions` | `feature/phase3-places-directions` | F2 | M |
| F5 | Stations API: `GET /stations`, `GET /stations/{id}`, `POST /stations/along-route` + cache | `feature/phase3-stations-api` | F1, F3 | M |
| F6 | Map foundation + Charging Stations page | `feature/phase3-charging-page` | F4, F5 | L |
| F7 | Trip Planner: search, routes, stations along each route | `feature/phase3-trip-planner-map` | F6 | L |
| F8 | (Optional) Favorite stations | `feature/phase3-favorites` | F5, F6 | S |
| F9 | Phase close-out: cross-border run, E2E, docs | `feature/phase3-closeout` | all | S |

F2 and F3 don't share files, and neither do F4 and F5. Either pair could run in parallel, but we'll do them one at a time unless you say otherwise.

---

## 4. Feature details

### F0 — Provider spike

**Why first:** D1 can change the provider design, and D5, D6, D9 and D10 depend on real response shapes and limits we haven't seen yet.

**Steps**
1. Accounts and keys (nothing committed): an AFDC/NREL key, the two Mapbox tokens from D3, and an OCM key only if step 3 shows we need one.
2. **AFDC** (with a real key, saving redacted responses to the gitignored `scripts/spike_out/`):
   - `country=CA` / `all` on `/v1.json`, `/v1/nearest.json` and `/v1/nearby-route.json` → decides **D1**.
   - `nearby-route`: route format (WKT `LINESTRING`), maximum points or body size, GET vs POST, `distance` units and limits, pagination (`limit`/`offset`), and the order of results along the route.
   - Connector type values (`TESLA`, `NACS`?, `J1772COMBO`, …), `ev_charging_units` / per-port power, `ev_network` values (the `electric-networks` endpoint), status/access codes, `updated_at`, and whether Tesla sites say they're open to non-Tesla cars → decides **D9 / D10**.
   - Rate-limit headers.
3. **OCM** (only if AFDC fails D1): Ontario coverage along the 401/QEW, licence and attribution text, connector type IDs, key requirement.
4. **Mapbox:** Directions response for Frederick → Toronto with `alternatives=true`; the `NoRoute` response; Search Box `suggest` / `retrieve` shapes, session-token rules, and how POIs appear next to addresses (PRD §4.2 needs both); free-tier quotas.
5. Run the cross-border check by hand: get the route, simplify it, call `nearby-route`, and count stations by country.

**Deliverables**
- `docs/maps-stations-notes.md`: verified facts with links, quotas, open gaps (like `smartcar-v3-notes.md`).
- Redacted fixtures for F2 and F3: `fixtures/mapbox/` and `fixtures/stations/afdc/`, each with a `README.md` on where the data came from.
- An updated D1/D5/D6/D9/D10 in this plan. If D1 is AFDC-only, fix PRD §10, architecture §1/§11 and the roadmap risk row in the same PR.

**Acceptance:** D1 is answered with evidence; one real cross-border along-route response is captured; the token setup is documented.

---

### F1 — Station data model + migration

**Backend**
- `modules/stations/models.py`: `ChargingStation` with the §3.6 columns plus the D8 additions. `CHECK`s on `provider` (`afdc`, `simulated`; F0 removed `ocm` / `nrcan`), `access` and `status`. `UNIQUE (provider, external_id)`. `INDEX (lat, lon)`.
- An Alembic migration (`2026111x_01_charging_stations.py`) that turns on RLS (enforced by `test_migrations.py`) and has a working `downgrade()`. Register it in `alembic/env.py`.
- Update `database-design.md` §3.6 and the ER diagram.

**Tests:** the RLS test (automatic), the Alembic round-trip, the uniqueness constraint, and the check constraints.

**Acceptance:** `uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head` passes locally and in CI.

---

### F2 — Mapbox adapter

**Backend:** `services/api/app/integrations/mapbox/`
- `client.py`: a `MapboxClient` using sync `httpx` (like `SmartcarClient`). It's the **only** code that talks to Mapbox.
  - `directions(origin, destination) -> list[DirectionsRoute]` with the D5 options.
  - `suggest(query, session_token, proximity?, country?, limit)` and `retrieve(mapbox_id, session_token)` (Search Box).
- `schemas.py`: Pydantic models; `DirectionsRoute { geometry_polyline6, distance_m, duration_s, summary }`; `PlaceSuggestion { id, name, full_address, kind: address|poi|place, … }`; `Place { id, name, full_address, lat, lon }`.
- `errors.py`: timeout → `MAPBOX_UNREACHABLE` 504; connection error or 5xx → 502; 401/403 → `MAPBOX_NOT_CONFIGURED` 503 (new code); other 4xx → `MAPBOX_ERROR` 502 (new code). `NoRoute` → empty list.
- Settings: `mapbox_secret_token`, `mapbox_api_base_url` (default `https://api.mapbox.com`), `mapbox_timeout_seconds`. An empty token → `MAPBOX_NOT_CONFIGURED`.
- A `get_mapbox_client()` dependency for test overrides. Logs follow D13.
- `app/core/geo.py`: polyline6 encode/decode, Douglas–Peucker simplification, haversine. Shared by the stations module now and routing later; it's pure math with no vendor code.

**Tests** (`tests/integrations/test_mapbox_*.py`, `httpx.MockTransport`): request building (profile, options, the token only in the query, never logged); parsing Directions with alternatives; `NoRoute` → `[]`; suggest/retrieve parsing (addresses and POIs); timeout/5xx/401 mapping; log redaction. `tests/test_geo.py`: polyline round-trip on fixture geometry; simplification keeps the endpoints and stays within tolerance; point cap.

**Acceptance:** the adapter is fully covered by fixture tests, and nothing outside `integrations/mapbox` calls Mapbox.

---

### F3 — Stations adapter

**Backend:** `services/api/app/integrations/stations/`
- `base.py`: a `StationProvider` protocol with `search_nearby(lat, lon, radius_km, filters)`, `search_along_route(points, corridor_km, filters)` and `get(external_id)`, and a normalized `ProviderStation` (D8 fields, `connectors: [{type, count|null, power_kw|null}]`, `source_updated_at`, `raw`).
- `afdc.py`: `AfdcProvider`. Always sends `fuel_type=ELEC` and the `country` from D1. Sends the simplified route as WKT through the transport F0 confirmed. Maps connectors and network per D9. Translates filters (`connector` → `ev_connector_type`, `network` → `ev_network`, `charging_level` → `ev_charging_level`).
- `errors.py`: `STATIONS_UNREACHABLE` 502/504; missing key → `STATIONS_NOT_CONFIGURED` 503 (new code).
- Settings: `afdc_api_key`, `afdc_api_base_url` (default `https://developer.nlr.gov/api/alt-fuel-stations/v1`), `stations_timeout_seconds`, `stations_route_max_points`. A `get_station_provider()` dependency.

**Tests** (`tests/integrations/test_stations_afdc.py`): parsing real fixtures; connector mapping, including NEMA dropped and unknown types kept in `raw`; null counts and power stay null (never derived); status/access mapping; filter translation; route WKT built from simplified points; pagination; error mapping; the key never appears in logs.

**Acceptance:** adapter covered by fixtures; the cross-border fixture from F0 parses into stations with both `US` and `CA` country codes.

---

### F4 — Places + directions API (map-and-search slice, backend)

**Contract first:** add §3.13 `GET /places/suggest`, §3.14 `GET /places/{id}` and §3.15 `POST /routes/directions` to `api-contracts.md`, plus the new error codes.

**Backend**
- `modules/places/router.py`:
  - `GET /api/v1/places/suggest?q=&session_token=&lat=&lon=&limit=` → `{ items: [{ id, name, full_address, kind }] }`. `q` is 3–200 characters; `session_token` must be a UUID; `lat`/`lon` are an optional proximity bias.
  - `GET /api/v1/places/{id}?session_token=` → `{ id, name, full_address, location: {lat, lon} }`.
- `modules/routing/router.py`:
  - `POST /api/v1/routes/directions` with body `{ origin: {lat, lon}, destination: {lat, lon} }` → `{ routes: [{ index, geometry_polyline6, distance_m, duration_s, summary }], no_route_reason: null | "no_route" }`. Coordinates are validated for range. Origin and destination must differ by more than 50 m.
- Both require auth. Neither stores anything (D12/D13).
- Regenerate the OpenAPI spec and TS client.

**Tests:** auth required; validation (short `q`, bad session token, out-of-range coordinates, identical points); the happy paths with a fake client; empty routes; each upstream error → the right envelope; logs contain no query text or full-precision coordinates.

**Acceptance:** with real keys, `curl` against local FastAPI returns suggestions for "Toronto" and 1–3 routes from Frederick to Toronto.

---

### F5 — Stations API + cache (compatible-stations slice, backend)

**Contract first:** update §3.10 (nullable `count`/`power_kw`, the new D8 fields, `fetched_at`/`is_stale`, `unknown_power_count`, `charging_level`), and add §3.16 `GET /stations/{id}` and §3.17 `POST /stations/along-route`.

**Backend:** `modules/stations/`
- `service.py`:
  - `search_nearby(...)` and `search_along_route(...)` call the provider, then **upsert** each result on (`provider`, `external_id`): update the fields, `fetched_at = now()` and `raw_payload`. They return rows with Chargewise UUIDs. Upserts are batched in one statement per page.
  - `get_station(id)`: read the cache, refresh if past the TTL; if the refresh fails, return the cache with `is_stale: true` (D7).
  - The D10 power filter and the `unknown_power_count` total.
  - Compatibility: if the request has `vehicle_id`, the caller must own it (else 404). The default connector filter is that vehicle's `vehicle_specs.connectors`, read through a `vehicles` service function (no cross-module query). Each station gets `compatible: bool`.
- `router.py`:
  - `GET /api/v1/stations?lat=&lon=&radius_km=&connector=&network=&charging_level=&min_power_kw=&vehicle_id=&limit=&cursor=`. `radius_km` is at most 80; `limit` defaults to 50 with a maximum of 200. The cursor is an opaque, signed provider offset.
  - `GET /api/v1/stations/{id}`. An unknown ID → 404.
  - `POST /api/v1/stations/along-route` with body `{ geometry_polyline6, corridor_km, connector[], network[], charging_level, min_power_kw, vehicle_id, limit }`. Encoded geometry is at most 200 KB. The server decodes, simplifies (D6) and calls the provider. Results are sorted by distance along the route, computed by projecting each station onto the decoded polyline (AFDC's own order is only approximate), and each includes `distance_along_route_km` and `distance_from_route_km`.
- `live_availability` is always `null` in Phase 3; neither provider gives verified live data (PRD §4.3).
- Settings: `stations_cache_ttl_hours`.

**Tests:** the upsert creates a row and a second fetch keeps the same UUID (E5); changed provider data updates the row; TTL refresh; a failed refresh gives the stale cached row; the vehicle's connectors become the default filter; another user's `vehicle_id` → 404; an explicit `connector` overrides the default; the power filter keeps unknown-power stations; along-route simplification is passed through and the order is correct; geometry too large → 422; provider down → 502/504 (E6); `live_availability` is always null; the cross-border fixture returns US and CA stations through the endpoint (E2, automated half).

**Acceptance:** all of the above pass; with a real key, along-route for the Frederick → Toronto route returns stations in both countries.

---

### F6 — Map foundation + Charging Stations page

**Frontend** (`apps/web`)
- Add the `mapbox-gl` dependency (D11). `components/map/map-view.tsx` is a client-only component that takes `routes`, `selectedRouteIndex`, `stations`, `selectedStationId` and `onSelect`, and puts the map inside an error boundary. A missing `NEXT_PUBLIC_MAPBOX_PUBLIC_TOKEN` shows a "Map unavailable" panel, and the list view still works.
- `lib/polyline.ts` (decode), `lib/stations.ts` (connector labels, power formatting, filter state ↔ URL query), and `lib/geo-format.ts` (distance in the user's units, through `lib/units.ts`).
- `components/places/place-search.tsx`: an accessible combobox (keyboard support, `aria-activedescendant`) using `/places/suggest` and `/places/{id}`. It debounces input and makes one session token per search session (D2, D15). It's used by both pages.
- `/charging` page:
  - A location source: place search or "Use my location" (D12, opt-in). There's no default location; the user picks one.
  - Filters for connector (multi-select), network, charging level, min power and radius. **Connector filters default to the primary vehicle's connectors.** With no vehicle, all connectors are shown, with a hint to connect one. Filter state lives in the URL query.
  - The map shows station markers; a list sorted by distance is shown beside it on desktop and below on mobile.
  - Each station card: name, network, connector chips with count/power or "unknown", distance, and a "Power unknown" label. **No availability badge** unless `live_availability` is set.
  - Empty, loading and error states (`STATIONS_UNREACHABLE` → retry).
- `/charging/[id]` detail page: connectors table, operator/network, address, access, status, `fetched_at` in local time, a stale note, and a "Directions in Google Maps / Apple Maps" external link (PRD §4.2 asks for external navigation; it's only a link here).
- Add the provider attribution line.

**Tests (Vitest; the map is stubbed):** place search (debounce, keyboard selection, one session token per session, error state); filters default to the vehicle's connectors; URL ↔ filter round-trip; station card with unknown count/power and no fabricated availability; detail page with missing fields and stale data; `lib/polyline` and `lib/geo-format` units.

**Acceptance:** with real keys, searching "Frederick, MD" shows nearby compatible stations on the map and in the list; the detail page opens; a hard reload keeps the filters.

---

### F7 — Trip Planner: search, routes, stations along each route

**Frontend**
- `/trips` page (it replaces the placeholder):
  - Origin and destination use `PlaceSearch`, plus an opt-in "Use my location" for the origin (D12). Swap button. Coordinates are kept in the URL so links can be shared and reloaded; the URL holds rounded coordinates and a label, never the session token.
  - `POST /routes/directions` returns 1–3 routes. All of them are drawn, with the selected one highlighted. A route list shows distance and duration in the user's units, labelled "Route A/B/C" with the shortest marked.
  - Stations along the **selected** route are fetched immediately; the other routes' are fetched afterwards (D15). Each route row shows "N compatible chargers within X km". The station layer and list use the same filters as F6, defaulted to the vehicle's connectors, plus a corridor slider.
  - States: no route found (`no_route_reason`), Mapbox down, stations down (routes still show), no vehicle (stations aren't filtered for compatibility, with a banner).
  - A clear note: "Charging stops and arrival battery are planned in the next release". Phase 3 must not suggest feasibility (PRD §4.2: never present an unchecked route as valid for the trip).
- Dashboard "Plan Trip" / "Find Chargers" quick links point to these pages (they may already).

**Tests:** Vitest for the route list (units, selection, shortest label), lazy station loading per route, every error/empty state, and URL state round-trip. Playwright `e2e/trip-planner.spec.ts` against the provider stub (D16): sign in → search origin/destination → 2 routes listed → select route B → station list updates → the station count matches the fixture (E1).

**Acceptance:** E1 manually with real keys, with a screenshot of Frederick → Toronto showing stations on both sides of the border (E2, manual half).

---

### F8 — Favorite stations (optional)

PRD §4.3 asks for favorites; the roadmap's Phase 3 scope doesn't list them. They're small and the station UUIDs from F5 make them easy, so they go here unless you'd rather move them to Phase 5.

- `favorite_stations (user_id FK profiles ON DELETE CASCADE, station_id FK charging_stations ON DELETE CASCADE, created_at, PK (user_id, station_id))`. RLS on. Update `database-design.md`.
- `GET /api/v1/stations/favorites`, `PUT /api/v1/stations/{id}/favorite` and `DELETE /api/v1/stations/{id}/favorite`, all idempotent. An unknown station → 404. Add them to the contract first.
- A star toggle on station cards and the detail page; a "Favorites" filter tab on `/charging`.
- Tests: idempotency, per-user isolation, cascade on account deletion, and favorites survive sign-out/sign-in (Playwright).

---

### F9 — Phase close-out

1. **Cross-border live run (E2):** Frederick, MD → Toronto, ON with real keys. Save a screenshot. Record station counts per country and per route, and record API latency for directions and along-route in the roadmap (as data for Phase 4's latency target).
2. **Playwright:** the trip-planner flow from F7 and the Charging page flow (search → filter → detail), both against the provider stub.
3. **Secret check (E3):** add an `AFDC_API_KEY` sentinel to the CI build step, which already greps `MAPBOX_SECRET_TOKEN`. Check that the only Mapbox token in `.next/static` is the public one.
4. **Optional live workflow:** `workflow_dispatch` that runs the `live`-marked pytest cases with repository secrets.
5. **Docs:** `api-contracts.md` (all new endpoints and codes), `database-design.md`, `architecture.md` §1/§2/§3/§11 (provider choice, the autocomplete proxy, the new modules), PRD §10 and the roadmap risk table (D1), README (how to get keys), `.env.example` files, and the roadmap status → "Phase 3 complete; Phase 4 next".

---

## 5. Cross-cutting rules for every Phase 3 PR

- Each PR builds in this order: contract text → migration → model → adapter → service → router → OpenAPI + regenerated TS client → UI → tests.
- Every new `public` table turns on RLS in its migration.
- Every user-owned read checks ownership (`vehicle_id`, favorites); "not owned" returns 404. `charging_stations` is global cache data with no `user_id`.
- No real Mapbox or AFDC calls in default CI; fixtures, fake clients and the stub server only.
- No secrets in `NEXT_PUBLIC_*`, logs, fixtures or commits. Fixtures are redacted (no account IDs, no home or real trip coordinates except the public Frederick/Toronto test points).
- No search text, addresses, polylines or full-precision coordinates in logs (D13).
- Never fabricate availability, power, counts or pricing; missing data shows as missing (PRD §7).
- Before each PR, run: `uv run ruff check`, `uv run mypy app`, `uv run pytest`, the Alembic round-trip, `npm run lint`, `npm run typecheck`, `npm run test`, and Playwright when UI flows change. The PR description lists what ran and the results.
- New runtime dependencies need a reason in the PR. Expected: `mapbox-gl` (web) only.

## 6. Test plan summary

| Layer | New tests |
|---|---|
| Migrations | RLS on `charging_stations` (and `favorite_stations`), round-trip, constraints |
| Geo helpers | Polyline round-trip, simplification tolerance and point cap, haversine |
| Mapbox adapter | Directions/alternatives, NoRoute, suggest/retrieve, error mapping, log redaction |
| Stations adapter | Normalization, connector mapping, null counts/power, filters, route WKT, cross-border fixture |
| Places/Directions API | Auth, validation, happy paths, upstream errors |
| Stations API | Upsert/stable IDs, TTL refresh, stale fallback, compatibility default, ownership, power filter, along-route |
| Web (Vitest) | Place search, filters, station card/detail, route list, error/empty states, polyline/units |
| E2E (Playwright) | Trip planner flow, Charging flow, favorites (if F8) |
| Manual | Real keys: Frederick → Toronto, Charging page near Frederick |

## 7. Risks

| Risk | Mitigation |
|---|---|
| ~~AFDC doesn't cover Canada.~~ | Resolved in F0: it does, with `country=all`. A test pins `country=all` so it can't regress. |
| AFDC `nearby-route` limits the route size. | Server-side simplification with a configurable point cap; F0 measures the real limit. |
| Mapbox GL can't render in headless CI. | E2E asserts the DOM (lists, counts), not the canvas; the map has a "Map unavailable" fallback. |
| Autocomplete costs add up. | Session tokens, debounce, minimum query length; quotas recorded in F0. |
| AFDC Tesla data doesn't say whether non-Tesla cars can charge. | Show only what the provider says; flag it as an open gap in the notes and don't mark sites as "bZ-accessible" ourselves. |
| The map page grows into Phase 4 work. | F7 explicitly shows no feasibility or stops; `/routes/plan` stays Phase 4. |

## 8. How we'll work through it

For each feature: confirm the decisions it depends on → create the branch → implement the slice → run the checks in §5 → you review the diff and try it → commit and open a PR → merge → next feature.

**Next step:** approve or change D1–D16 (especially D1, D2, D4 and D10, and whether F8 stays in Phase 3), then start **F0**.
