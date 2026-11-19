# Phase 4 Plan: Charging-Aware Trip Planner

Status: **Approved 2026-11-18**: P4-D1…D9 settled as in §2 (D1, D2, D3, D4 and D6 changed from the [`plan.md`](plan.md#42-phase-4-charging-aware-trip-planner) recommendations; D10–D16 added; per-vehicle baseline added as follow-up F8) · Created: 2026-11-18 by P4-F0 · Phase owner: project lead

This is the working plan for [Phase 4 of the roadmap](development-roadmap.md#phase-4--charging-aware-trip-planner-weeks-79). [`plan.md`](plan.md) §4.2 decides *when* each feature runs and *which files* it owns; this file decides *what* each feature builds. Each feature session edits only its own section here (for "As built" notes).

Based on: roadmap Phase 4, [`product-requirements.md`](product-requirements.md) §4.2, §4.6, §7 and §9, [`architecture.md`](architecture.md) §5, §6 and §10, [`api-contracts.md`](api-contracts.md) §1 and §3.7–3.9b, [`database-design.md`](database-design.md) §3.4 and §3.7–3.8, [`phase3_plan.md`](phase3_plan.md) D5–D10 and F5, and ADRs [0003](adr/0003-own-charging-optimizer-over-mapbox-ev-routing.md), [0004](adr/0004-deterministic-energy-baseline-before-ml.md) and [0008](adr/0008-route-ordered-dp-charging-optimizer.md).

**Not in Phase 4:** speed/elevation/temperature/weather adjustments, several alternative plans, live charger availability, traffic, observed trips and prediction scoring (Phase 5).

---

## 1. Goal and exit criteria

**Goal:** a signed-in user picks an origin, a destination and one of the driving routes, and gets back either a battery-feasible plan with charging stops (arrival and departure SoC, charging time per stop, detour, total time) or an explicit "not feasible" with the reason. Plans are stored and can be saved, listed, opened and deleted.

| # | Exit criterion (roadmap Phase 4 + PRD §9) | Verified by |
|---|---|---|
| X1 | The planner inserts charging stops on real long-distance routes and matches the three hand-checked trips in §5. | F2/F3 unit tests on the §5 numbers; F9 real-key run |
| X2 | `starting_soc_override` is required when the SoC reading is stale or missing; the UI asks for it. | F4 endpoint tests; F6 Playwright |
| X3 | Latency measured and recorded for a ~500 mi trip. | F9 (roadmap deliverables) |
| X4 | Every PRD §9 case has a passing automated test. | §6 mapping table, filled in by F9 |
| X5 | An infeasible trip is never returned as a valid route. | F3, F4, F6 tests |

---

## 2. Decisions

### 2.1 The nine decisions from plan.md

| # | Decision | Settled | Changed from plan.md? |
|---|---|---|---|
| **D1** | Baseline consumption | A flat `baseline_kwh_per_km` per vehicle **from the static catalog only** (`spec_catalog.py`). 2026 bZ XLE AWD: **0.20 kWh/km** (≈ 322 Wh/mi), EV Database's mild-weather *highway* figure (323 Wh/mi), because planned trips are mostly highway. EPA's 28 kWh/100 mi is measured at the wall and averages city and highway, so it would be optimistic for road trips. No speed/elevation/temperature adjustments; the response lists `adjustments_applied: []`. A vehicle without a catalog baseline **cannot be planned** (`VEHICLE_SPECS_INCOMPLETE`); there is no generic default, because every vehicle is different. | **Yes.** plan.md said "user-editable". Editing needs a new `vehicle_specs` column and changes to `PATCH /vehicles/{id}/specs`, so it is its own follow-up feature, **F8**, which runs after F4 in the migration lane. Until F8 lands, the catalog is the only source. |
| **D2** | Charging curve | Piecewise-linear `vehicle_specs.charging_curve`, falling back to the catalog curve when the row's curve is empty (D14). The 2026 bZ curve is **derived** from published figures, not measured (§2.3), and every plan says so (`charging_curve_source: "catalog_derived"`). The vehicle's power is capped by the station's power. **A vehicle with no curve cannot be planned** (`VEHICLE_SPECS_INCOMPLETE`). | **Yes.** No "flat `min(station kW, vehicle max kW)`" fallback: with no curve we don't know the vehicle's power, and guessing it is fabricating data. |
| **D3** | Algorithm | **Dynamic programming over candidate stations in route order**, with state = (station, departure SoC on a 1 % grid). It finds the minimum-time plan at that resolution, chooses how much to charge at each stop, and has no fixed stop limit. See [ADR 0008](adr/0008-route-ordered-dp-charging-optimizer.md) and §4.3. | **Yes.** Bounded enumeration with up to 3 stops over the ~100 DC stations along Frederick → Toronto is about 167,000 orderings (C(100,1) + C(100,2) + C(100,3)) *before* choosing charge amounts, and still needs a rule for how much to charge. The DP is simpler to get right and runs in well under 500 ms. It is architecture §6.5's graph search, reduced to one dimension because the stations sit along a single route. |
| **D4** | Detour | Estimate: `detour_km = 2 × distance_from_route_km × 1.3` (road factor), `detour_minutes = detour_km / 50 km/h`. Both factors are config settings. The response marks detours as estimates. A station whose detour exceeds `max_detour_minutes` is excluded. No extra Mapbox calls. The detour's energy is charged to the leg that arrives at the station. | Small change: adds the 1.3 road factor and uses 50 km/h (local roads) instead of 60, so off-route distance isn't under-counted. |
| **D5** | One plan or several | One best plan; `alternatives: []` is in the schema and always empty in v1. Closes contract §6 question 1. | No |
| **D6** | Persistence | `POST /routes/plan` stores **every** result (feasible or infeasible, including "no driving route") as a `planned_trips` row with `saved_at = null` and returns its `trip_id`. "Save" is `POST /trips/{id}/save` (sets `saved_at`); "discard" is `DELETE /trips/{id}`. `GET /trips` lists saved trips by default. The client never sends a plan back to the server. Unsaved rows are deleted by the Phase 6 retention job (P6-F5, 30 days). | **Yes.** With plan.md's version, every re-plan (for example after changing preferences) would add a row to trip history, and the UI would have to delete the ones it replaced. `saved_at` keeps the same "no client-sent payload" property without that clutter. |
| **D7** | Stale SoC | Starting SoC is `starting_soc_override` when given (it always wins, even over fresh telemetry: the user may plan a later trip after charging). Otherwise the cached `soc_percent`, **only if its own `signal_recorded_at` is no older than `telemetry_stale_after_minutes` (15)**. Missing or stale → `422 VALIDATION_ERROR` with `details.reason = "STARTING_SOC_REQUIRED"`, plus `details.last_soc` and `details.last_recorded_at` (both nullable) as a hint for the UI. Threshold stays 15 until P2-R2 measures the real event cadence. | No (adds the per-signal timestamp and the hint) |
| **D8** | Hand-checked trips | Three trips with full worked calculations in §5: local, no charge; ~250 mi, one stop; Frederick → Toronto (the real Mapbox fixture distance), three stops. F2 and F3 assert these numbers. | No |
| **D9** | Interfaces | Python signatures in §4. F2 and F3 are reviewed against them; any change is written in the feature's "As built" notes and flagged in its PR. | No |

### 2.2 Decisions added by P4-F0

| # | Decision | Settled |
|---|---|---|
| **D10** | Where the reserve applies | `reserve_soc` is the minimum SoC at **every arrival**, at chargers and at the destination. Arriving at a charger at 3 % isn't a plan we should recommend. |
| **D11** | Per-stop overhead | Every stop adds `planner_stop_overhead_minutes` (default **5**) for leaving the road, parking, plugging in, starting the session and the charger's ramp-up. Without it the optimizer treats stopping as free and splits a charge into several stops to save seconds. Other planners do the same: ABRP's "charging overhead" defaults to 5 minutes, and TomTom's EV routing "charging time offset" defaults to 180 s (§8). It is shown in `assumptions`. It is **not** queue or waiting time: waiting stays 0 and Chargewise never invents availability (architecture §6.3). |
| **D12** | Stations with unknown power | **Never planning candidates.** A station counts only if at least one of its connectors that the vehicle is compatible with has a known `power_kw`. Its planning power is the highest known `power_kw` among those connectors, and the stop uses that connector type. Excluded stations are counted in `candidates.excluded_unknown_power` so the UI can say so; nothing is assumed. (The Charging page still shows them as "power unknown", per phase 3 D10.) |
| **D13** | Which route; drive time | The request names a `route_index` (default 0) from `POST /routes/directions`. The server calls Mapbox again with the same options and plans on that route. It never trusts geometry or distances sent by the client. Drive time is the same for every plan on one route, so the optimizer minimizes `charging + detour + overhead`. Times at points along the route are interpolated linearly from Mapbox's total duration. No Mapbox annotations and no adapter change in v1. |
| **D14** | Vehicle specs at plan time | For each of `usable_capacity_kwh`, `connectors` and `charging_curve`: use the `vehicle_specs` row's value if set, otherwise the catalog value for the vehicle's make/model/year, otherwise it is missing. `baseline_kwh_per_km` comes from the catalog only until F8 adds the column; from then on it follows the same row-then-catalog rule. Any missing → `422 VALIDATION_ERROR`, `details.reason = "VEHICLE_SPECS_INCOMPLETE"`, `details.missing = [...]`. This matters for the real bZ: its specs row was created while the catalog was empty, so it holds `NULL` and `[]`. Each value's source is reported in `assumptions`. |
| **D15** | What the energy model ignores (v1) | Charging losses (the curve is power *into the battery*), the time to reach the station's power, temperature, speed, elevation, HVAC, battery ageing, and SoC above the curve's last point (a plan never charges above it). All listed under "not modelled" in architecture §6.7. |
| **D16** | SoC grid and rounding | Departure SoC is chosen on a 1 % grid (`planner_soc_grid_step = 0.01`) and is the smallest grid value that works, so plans round **up**. Arrival SoC is computed exactly. The API returns SoC as fractions with 3 decimals and minutes as whole minutes, rounded half-up; totals are computed from unrounded values, so per-stop minutes can differ from the total by 1 minute. |

### 2.3 Cited 2026 bZ numbers (catalog entry, F2)

One catalog entry: `make="Toyota"`, `model_prefix="bZ XLE AWD"` (the Smartcar model string seen on the real car, [`smartcar-v3-notes.md`](smartcar-v3-notes.md) §7), years 2026–2026. Other bZ trims (57.7 kWh FWD, Limited) get no entry yet, so they can't be planned until they have cited numbers.

| Field | Value | Source |
|---|---|---|
| `usable_capacity_kwh` | **71.0** | EV Database lists 74.7 kWh nominal / 71.0 kWh usable for the same 74.7 kWh pack (bZ4X AWD MY26). Toyota publishes only the 74.7 kWh total. |
| `connectors` | `NACS`, `CCS1`, `J1772` | PRD §2: NACS port, with CCS1 and J1772 adapters supplied by Toyota. |
| `baseline_kwh_per_km` | **0.20** | EV Database, mild-weather highway: 323 Wh/mi = 0.2007 kWh/km, rounded to 0.20. For comparison: EPA 28 kWh/100 mi (combined, at the wall); EV Database's cold-weather highway figure is 418 Wh/mi. |
| `charging_curve` | `(0.00, 100)`, `(0.10, 150)`, `(0.30, 150)`, `(0.50, 120)`, `(0.80, 55)`, `(1.00, 15)` (SoC fraction, kW) | **Derived, not measured.** Built so that: peak 150 kW (EV Database), 10→80 % in about 30 minutes (Toyota), 10→80 % average about 110 kW (EV Database). With 71.0 kWh it gives 10→80 % in **27.4 min** (49.7 kWh at 109 kW average). No measured per-SoC curve for the 2026 bZ was published as of 2026-11-18. The `source` string says this and `charging_curve_source = "derived"`. Replace it with a measured curve (for example from a P2 real charging session) when one exists. |
| `range` check | 288 mi EPA (Toyota) vs 71.0 / 0.20 = 355 km = 221 mi | Our highway baseline gives less range than EPA's combined figure, as expected (EV Database's highway estimate is 220 mi). |

`CatalogSpec` gains `baseline_kwh_per_km: Decimal | None` and `charging_curve_source: Literal["measured", "derived"] | None` (F2).

---

## 3. Features

Owns lists are in [`plan.md`](plan.md#42-phase-4-charging-aware-trip-planner) §4.2 and are not repeated here. Order and dependencies: F0 → (F1, F2) → F3 → F4 (also needs P3-F5) → F6 (also needs P3-F7); F1 → F7; F5 is independent; F6 + F7 → F9. F8 (per-vehicle baseline) follows F4 and doesn't block F9.

| ID | Feature | Branch | Size |
|---|---|---|---|
| F0 | Design + contracts (this file) | `feature/phase4-design` | S |
| F1 | Trips schema + read/save/delete API | `feature/phase4-trips-schema` | M |
| F2 | Energy model + bZ catalog entry | `feature/phase4-energy-model` | M |
| F3 | Optimizer | `feature/phase4-optimizer` | L |
| F4 | `POST /routes/plan` | `feature/phase4-plan-endpoint` | M |
| F5 | Preferences editor | `feature/phase4-preferences-editor` | S |
| F6 | Planner UI | `feature/phase4-planner-ui` | L |
| F7 | Trip history UI | `feature/phase4-trip-history` | M |
| F8 | Per-vehicle consumption baseline (follow-up) | `feature/phase4-vehicle-baseline` | M |
| F9 | Close-out | `feature/phase4-closeout` | S |

### F1 — Trips schema + read/save/delete API

**Contract:** [`api-contracts.md`](api-contracts.md) §3.8, §3.9, §3.9a, §3.9b (written by F0).

**Database** (migration lane; F1 also updates `database-design.md` §3.7–3.8 to match):
- `planned_trips` as in db-design §3.7, plus:
  - `plan_status text NOT NULL CHECK (plan_status IN ('feasible','infeasible'))`
  - `saved_at timestamptz NULL` (D6)
  - `departure_time timestamptz NULL` (stored from the request, not used in v1)
  - `status` keeps `'planned'|'completed'|'abandoned'`, default `'planned'`.
- Partial index `(user_id, saved_at DESC, id) WHERE saved_at IS NOT NULL` for the default list. Keep `(user_id, created_at DESC)`.
- `planned_stops` as in db-design §3.8. `station_id` FK → `charging_stations.id` **ON DELETE SET NULL**. `charging_minutes` is the rounded API value. `CHECK` on SoC columns (0..1).
- RLS on both tables. Cascades: profile → trips, vehicle → trips, trip → stops.

**Backend** (`modules/trips/`):
- `service.create_planned_trip(...)` (§4.4): one transaction, trip + stops.
- `GET /trips`, `GET /trips/{id}`, `DELETE /trips/{id}`, `POST /trips/{id}/save`, exactly as in the contract. Cursor = opaque base64 of `(saved_at or created_at, id)`.
- Ownership: every query filters `user_id`; not owned → 404.

**Tests:** RLS present; Alembic round-trip and one head; ownership 404s on all four routes; cursor pagination (stable order, last page has `next_cursor: null`); `saved=false` lists unsaved; save is idempotent (keeps the first `saved_at`); delete then get → 404; cascade on profile delete; `create_planned_trip` stores stops in order and the `plan_payload` unchanged.

**As built:**
- **Migration and models:** migration `20261118_01` (revises `20261117_01`). Models are in `app/modules/trips/models.py`.
- **Two extra indexes:** besides the two in the plan, `planned_trips_vehicle_idx (vehicle_id)` and `planned_stops_station_idx (station_id)`. They keep the vehicle → trips cascade and the station → stops SET NULL from scanning the whole table.
- **Other constraints:** `planned_stops.sequence >= 1` and `charging_minutes >= 0`. All SoC and minute columns are `NOT NULL`.
- **`create_planned_trip`:** the signature is exactly §4.4.
  - It flushes but **doesn't commit**, so F4 owns the transaction.
  - It raises `ValueError` unless the stop sequences are `1..n` in order.
  - `plan_payload` must be JSON-serializable (dump the §3.7 model with `mode="json"`). It is stored as given.
  - `TripEndpoint`, `NewPlannedStop` and `create_planned_trip` are importable from `app.modules.trips.service`.
- **List order and cursor:**
  - Saved trips are ordered by `saved_at DESC, id ASC` and unsaved ones by `created_at DESC, id ASC`, which matches the partial index. Paging is keyset.
  - The cursor is unsigned base64url JSON `{m: "saved"|"unsaved", k: <timestamp>, i: <id>}`. Queries always filter on the caller, so a forged cursor can only move within the caller's own rows. No config was needed.
  - A malformed cursor, or one from the other list (`saved=` mismatch), returns 422 `VALIDATION_ERROR` with `details.reason = "INVALID_CURSOR"`.
- **List-item fields:**
  - `distance_km` is read from `plan_payload.route.distance_km`, which is null only when there is no route. An infeasible plan that has a route still shows its distance.
  - `total_minutes` is read from `plan_payload.totals.total_minutes`, which is null for every infeasible plan.
  - `stop_count` counts the `planned_stops` rows.
- **`vehicle_id` filter:** ownership is checked through `vehicles.garage.get_vehicle_detail`, so a disconnected vehicle still counts as the caller's.
- **Save:** a single `UPDATE … SET saved_at = coalesce(saved_at, now())`, which is idempotent and race-free.
- **Delete:** a single `DELETE … RETURNING`; the stops cascade in the database.
- **Tests:** `tests/trips/` has 47 tests covering every item in the list above. It also checks that the trip models match the migrated schema, that a station delete sets `station_id` to null, the CHECK constraints, 401 and 422 responses, and that the saved list follows `saved_at`, not `created_at`.

### F2 — Energy model + bZ catalog entry

Pure functions, no DB, no HTTP. Signatures in §4.2. Also adds the bZ entry and the two new `CatalogSpec` fields (§2.3). Config block "energy": none needed in v1 (left empty; everything comes from the catalog).

**Charging time** (architecture §6.4): with `P_eff(s) = min(P_vehicle(s), P_station)`, split the curve into pieces where `P_eff` is linear (curve points, plus the SoC where the vehicle curve crosses the station power). On each piece from `s1` to `s2`, with powers `p1`, `p2`:

```
minutes = 60 · B · (s2 − s1) / p1                          if p1 == p2
minutes = 60 · B · (s2 − s1) / (p2 − p1) · ln(p2 / p1)      otherwise
```

Below the curve's first point the first point's power holds; above its last point charging isn't allowed (`max_charge_soc`).

**Tests:**
- 10→80 % with the bZ curve, uncapped: **27.36 min**.
- Cap: 20→50 % at a 50 kW station = 60 · 71 · 0.30 / 50 = **25.56 min**.
- Zero energy → zero minutes; `to <= from` → 0.
- Property-style tests over a fixed grid of SoC pairs and station powers (no new dependency; Hypothesis isn't in `pyproject.toml`): minutes increase as the target SoC increases; minutes don't decrease when station power drops; additivity `t(a,c) = t(a,b) + t(b,c)`.
- `to_soc` above `max_charge_soc` → `ValueError`.
- The per-segment numbers in §5.
- `lookup_spec("TOYOTA", "bZ XLE AWD", 2026)` returns the entry (case-insensitive); `"bZ XLE FWD"` returns `None`.

**As built (P4-F2, 2026-11-18):**
- `app/modules/energy/`: `types.py`, `model.py`, `charging.py`, `__init__.py` (re-exports). The §4.2 signatures are implemented **unchanged**.
- `EnergyVehicle.__post_init__` checks its inputs and raises `ValueError` for: capacity or baseline not > 0 (or non-finite), fewer than 2 curve points, SoC outside [0, 1] or not strictly increasing, or power not > 0. F4 can rely on any constructed `EnergyVehicle` being plannable.
- `segment_energy_kwh` raises `ValueError` for a negative or non-finite distance. `arrival_soc` is never clamped.
- `charging_minutes` / `cumulative_charge_minutes`: the station cap is applied by splitting each curve piece where it crosses the station power, then using the exact log formula per piece. `log1p` keeps nearly flat pieces accurate. SoCs within 1e-9 of 0 or `max_charge_soc` are clamped, so float noise from grid arithmetic doesn't raise. `cumulative_charge_minutes` precomputes the breakpoints and looks up a SoC by bisection. Both functions raise `ValueError` for a bad station power at construction time.
- Catalog: `CatalogSpec` gains `baseline_kwh_per_km: Decimal | None = None` and `charging_curve_source: Literal["measured", "derived"] | None = None`. Both have defaults, so existing constructors are unchanged. There is one entry, 2026 `bZ XLE AWD`, with the §2.3 values and `charging_curve_source = "derived"`. Newly connected bZ XLE AWD vehicles now get 71.0 kWh, `NACS/CCS1/J1772` and the curve in their `vehicle_specs` row. The real car's existing row still holds `NULL`/`[]`; D14's row-then-catalog rule in F4 covers it.
- Config: no "energy" settings (the block stays empty).
- Tests (`tests/energy/`, 60 tests):
  - 10→80 % = **27.356 min**, and 20→50 % at 50 kW = **25.56 min**.
  - Zero energy and `to <= from` give 0. Every `ValueError` path is covered.
  - The closed form matches a midpoint-rule integral to 1e-6.
  - Fixed-grid property tests (15 SoCs × 9 station powers): strictly monotonic in target SoC, never faster at a lower station power, additive, and cumulative F(b) − F(a) equal to `charging_minutes`.
  - All §5 per-leg SoCs and every charging piece and stop total, to 5e-4 min: trip 2 B = 16.648, trip 3 W/B/Q = 14.617 / 12.443 / 18.269, sum 45.329, energy 150.92 kWh.
  - The bZ lookup is case-insensitive; `bZ XLE FWD`, `bZ Limited` and 2025 return `None`.
- **Doc discrepancy in §5 trip 2:** the "L only" rejection says arrival 0.46018. The leg is 120 + 2 × 0.2 × 1.3 = 120.52 km, which gives **0.46051**. The conclusion (needs 0.93873 > 0.80) is unchanged. The test asserts 0.46051. The table is left as written because it isn't in F2's section.
- **Out-of-Owns edit (approved by the coordinator):** `tests/test_vehicle_connect.py`. The connect happy-path test asserted `usable_capacity_kwh is None` ("no catalog entry"). It now asserts the catalog's 71.0 kWh and connectors. `scripts/e2e_backend.py` also picks up the entry. No E2E spec asserts capacity, so it is unchanged.

### F3 — Optimizer

`routing/plan_types.py` (shared types, §4.1) and `routing/optimizer.py` (§4.3). Pure. Input is already filtered (only compatible, known-power, within-detour candidates); filtering lives in F4 so its counts are reported.

**Tests** (one per PRD §9 optimizer row, plus):
- Starting at 100 % (charge-to doesn't cap the start); starting at 0 % → `STARTING_SOC_TOO_LOW`.
- No charge needed → zero stops, arrival SoC exact.
- No candidates while charging is needed → `NO_COMPATIBLE_CHARGER_IN_RANGE` with a limiting segment from the origin to where SoC reaches the reserve.
- A charger beyond reach (first candidate past the reserve point) → the same code.
- A gap between chargers that can't be bridged from `charge_to` → `DESTINATION_UNREACHABLE` with that gap as the limiting segment.
- Infeasible with the user's reserve but feasible with reserve 0 → `RESERVE_SOC_UNREACHABLE`.
- **Two short stops beat one long stop:** route 500 km, 300 min, start 0.80, `charge_to` 1.00, overhead 5, stations P at 220 km and Q at 330 km (150 kW, on the route). Expected: P then Q, 0.18→0.46 and 0.15→0.63, 34.2 extra minutes. P only (0.18→0.94) is 45.2.
- The three §5 trips, exact stops and SoCs.
- The departure SoC is on the grid and is the smallest grid value that works.
- Latency of the pure search on the real Frederick → Toronto AFDC fixture (all DC stations) < 500 ms.

**As built (P4-F3, 2026-11-18):**
- **Files:** `app/modules/routing/plan_types.py` (§4.1) and `app/modules/routing/optimizer.py` (§4.3). Both signatures are implemented **unchanged**. The optimizer uses the F2 functions (`cumulative_charge_minutes`, `charging_minutes`, `soc_used`, `segment_energy_kwh`, `max_charge_soc`) and doesn't recompute charging time itself.
- **Search (the §4.3 DP with one extra pruning step):** each label (a departure from a station, or the origin) sits on a single axis, its *potential* `v = departure + along_km · soc_per_km`. Detour energy is charged to the arriving leg, so the arrival at station *j* is `v − (along_j + detour_j) · soc_per_km`, whatever station the label is at.
  - That makes it safe to keep only the Pareto frontier of (potential, cost) across all earlier stations. A label is dropped when another one has at least its potential for no more cost.
  - The predecessors of *j* are then a contiguous slice of the frontier, found by bisection and already sorted by arrival. The §4.3 prefix minimum of `cost − F_j(a)` runs over that slice.
  - On the real fixture this cut the time from about 360 ms (every state from every station within one charge's reach) to about 30 ms, with identical plans. A test checks optimality against an unpruned brute-force search on 12 seeded random instances (0–3 stops plus all three infeasible codes).
- **Tie-breaks (§4.3 step 5):** the comparison key is (minutes rounded to 1e-9, stops, charging minutes, station indices in route order). Every part is additive, so the prefix minimum and the frontier keep it exact. Route order is `(distance_along_route_km, station_id)`.
- **Grid:** departures are `k · soc_grid_step` up to `floor(charge_to / step)`, built from integer `k` so float drift doesn't creep in. A stop must charge (`departure > arrival`). A station where you'd arrive at or above the top grid value is never a stop. Arrivals are compared with the reserve with a 1e-9 tolerance.
- **Limiting segments (where §4.3 left a choice):**
  - `RESERVE_SOC_UNREACHABLE`: uses the leg of the reserve-0 plan with the **largest shortfall**, `reserve + soc used − best SoC available at its start` (the start SoC at the origin, the top grid SoC at a charger). Ties go to the lowest arrival. §4.3 said "lowest arrival", but that leg isn't always the one that can't be crossed.
  - `NO_COMPATIBLE_CHARGER_IN_RANGE`: the segment runs from the origin to the reserve point, as specified. `required_soc` is the SoC needed to reach the **first candidate** (with its detour), or the destination when there are no candidates, so the UI gets a number to compare with `available_soc`. Read literally, the field definition would have made it equal to the start SoC.
  - `DESTINATION_UNREACHABLE`: a forward pass charges to the top grid SoC at every reachable charger. The segment runs from the furthest reachable charger to the next candidate, or to the destination. `available_soc` is the top grid SoC.
- **Input validation:** `optimize` raises `ValueError` for inputs F4 must never send. That covers SoCs outside [0, 1]; `reserve >= charge_to`; `charge_to` above the curve's last point; a grid step outside (0, 1]; negative overhead or road factor; a detour speed ≤ 0; and a candidate with power ≤ 0 or a negative route position. NaN counts as invalid everywhere. Turning user input into a 422 stays in F4.
- **§5 results (exact):**
  - Trip 1: arrival 0.49465, 60 min.
  - Trip 2: B, 0.17296 → 0.66, 16.648 min charging, total 294.77 min, arrival 0.15296.
  - Trip 3: W → B → Q with 0.162 → 0.61, 0.151 → 0.55, 0.152 → 0.68 and 14.617 / 12.443 / 18.269 min. Extra time is 68.13 min; energy is 150.92 kWh.
    - On the fixture's exact route (748.108 km, 514.845 min) the total is **582.97** min (§5 rounds the route and says 582.9; API 583) and the arrival is **0.15012** (§5: 0.15014; API 0.150).
  - Two short stops: P → Q, 0.18 → 0.46 and 0.15 → 0.63, **34.20** extra min. P alone is 0.18 → 0.94, **45.23** min.
- **Latency:** measured on the real fixture: route A distance and duration, and all 121 stations from `nearby_route_cross_border_dc_fast.json` that have a bZ-compatible connector with known power (84 CCS1, 37 NACS; one of the 122 is excluded). Over 20 runs each on an Apple M3 Pro:

  | Start SoC | Median | Max | Stops |
  |---|---|---|---|
  | 0.30 | 37 ms | 38 ms | 5 |
  | 0.50 | 34 ms | 34 ms | 4 |
  | 0.90 | 30 ms | 32 ms | 3 |
  | 1.00 | 29 ms | 29 ms | 3 |

  The test asserts < 500 ms across the four starts.
- **Tests:** `tests/routing/` has 50 tests.
  - `test_optimizer.py`: every F3 bullet above. It also covers the reserve failing on the first leg, a coarser grid, ties between identical stations, a slow station losing to a fast one, detour accounting, unsorted input, validation and the reference search.
  - `test_optimizer_trips.py`: the §5 trips and the two-stop case.
  - `test_optimizer_latency.py`: the latency check.
  - `tests/routing/conftest.py` is new. It maps the AFDC fixture to candidates through the real `normalize_station` and projects them onto the simplified route sent to AFDC. **F4 note:** you can add your own fixtures to this file. Keep the optimizer helpers.
- **No new fixture files.** The existing AFDC and Mapbox fixtures were enough.

### F4 — `POST /routes/plan`

**Contract:** [`api-contracts.md`](api-contracts.md) §3.7. `modules/routing/planner_service.py` follows §4.5, step by step. Config block "planner":

| Setting | Default | Note |
|---|---|---|
| `planner_stop_overhead_minutes` | 5 | D11 |
| `planner_detour_road_factor` | 1.3 | D4 |
| `planner_detour_speed_kmh` | 50 | D4 |
| `planner_soc_grid_step` | 0.01 | D16 |
| `planner_max_corridor_km` | 16 | Upper bound for the along-route corridor (phase 3 D6 range). |

Corridor for the along-route search: `max_detour_minutes · speed / 60 / (2 · road_factor)`, clamped to 0.5–16 km (20 min → 6.4 km). Always `charging_level = dc_fast`.

**Preferred networks:** if the effective `preferred_networks` list is non-empty, plan first with only matching stations. A station matches when its `network` or `operator`, lower-cased and with spaces collapsed, contains a preferred name or is contained in one (the editor stores free text; AFDC stores codes like `Tesla`). If that is infeasible, plan again with all candidates and set `assumptions.preferred_networks_relaxed = true`.

**Tests:**
- Stale or missing SoC → 422 `STARTING_SOC_REQUIRED` with the hint; the override is used when given, even if fresh telemetry exists.
- Preferences: request keys win over stored, stored win over defaults; reserve ≥ charge-to → 422.
- `VEHICLE_SPECS_INCOMPLETE` lists every missing field; catalog fallback fills an empty specs row.
- Another user's vehicle → 404.
- Mapbox down → `MAPBOX_UNREACHABLE`; AFDC down → `STATIONS_UNREACHABLE`; nothing persisted.
- No driving route → 200, infeasible, `NO_DRIVING_ROUTE`, persisted.
- Short trip → no stations call.
- Exclusion counts (incompatible, unknown power, detour).
- Preferred networks relaxed.
- `trip_id` returned; `GET /trips/{id}` returns the same body.
- Latency logged per stage (directions, stations, optimizer, persist) without coordinates.

**As built (P4-F4, 2026-11-18):**
- **Files:**
  - `app/modules/routing/planner_service.py` follows §4.5 step by step. Its `plan_trip` signature is unchanged.
  - `routing/schemas.py` adds the §3.7 request and response models.
  - The `POST /routes/plan` route is in `routing/router.py`. It commits once, after the trip is stored.
  - The five `planner_*` settings are in the "Trip planner" config block, with commented defaults in `.env.example`.
  - No migration and no new dependency.
- **Specs (D14):**
  - Row value, then catalog value, then missing, as specified. Connector strings the planner doesn't know are ignored.
  - A curve that `EnergyVehicle` rejects (fewer than 2 points, unsorted, zero power) is reported as missing `charging_curve`.
  - The baseline comes from the catalog only; F8 changes the baseline lines.
  - **A row value equal to the catalog's is reported as `catalog`.** P4-F2 copies the catalog into newly connected bZs' rows, so otherwise derived figures would show as `vehicle`. The curve source is `vehicle`, `catalog_derived` (the catalog curve is `derived`), or `catalog`.
- **Preferences:**
  - Request keys, then the stored row, then the seeded defaults. `reserve_soc ≥ preferred_charge_to` after merging → 422 with `details.fields`, the same as `PATCH /me/preferences`.
  - `charge_to = min(preferred_charge_to, last curve point)`. If the reserve isn't below that → 422 (`details.max_charge_soc`). The optimizer's `ValueError` paths are never reached from user input.
  - Request `preferences` follow the §3.1a field rules: explicit `null`, more than 3 decimals, duplicate networks, an unknown key (`units`, `primary_vehicle_id`) → 422.
- **Starting SoC (D7):**
  - The override always wins.
  - Otherwise `soc_percent / 100` from `get_vehicle_state`, if `signal_recorded_at["soc_percent"]` is no older than `telemetry_stale_after_minutes`.
  - A reading outside 0–100 counts as missing. `last_recorded_at` is null whenever `last_soc` is null.
- **Order of checks:** specs, then preferences, then SoC, then Mapbox. So 404, specs, preference and SoC errors never call a provider.
- **Short trips:** `optimize` with no candidates runs first. If that plan is feasible, or the result is `STARTING_SOC_TOO_LOW`, no stations call is made (`searched: false`).
- **Along-route km axis (approved deviation):** P3-F5 measures `distance_along_route_km` on the Douglas–Peucker simplified line (0.1 km, `stations_route_max_points`). The optimizer uses Mapbox's road distance.
  - The planner simplifies the decoded route the same way and scales station positions by `distance_km / simplified_length`, clamped to `[0, distance_km]`.
  - Limiting-segment km are converted back with the same scale and interpolated on that line. The response's `from`/`to` are on the route, within 0.1 km.
- **Candidates:**
  - The stations call uses `dc_fast`, the vehicle's connectors as the provider filter, and `max_results` = 1,000 (the §3.17 maximum).
  - The filters run in order: incompatible, unknown power (D12), detour over `max_detour_minutes` (D4). Each station is counted once.
  - The highest-power compatible connector is used, ties by connector name.
  - `considered` = stations handed to the optimizer.
- **Preferred networks:**
  - Matching is case- and whitespace-insensitive containment in either direction, on `network` or `operator`.
  - If every candidate matches, the optimizer runs once and `relaxed = false`.
  - If the preferred-only plan is infeasible, the planner runs again on all candidates and sets `relaxed = true`, even when that plan is infeasible too.
- **Response:**
  - SoC is rounded to 3 decimals and minutes to whole numbers, both half-up through `Decimal`. `energy_kwh_estimated` has 2 decimals; `route.distance_km` and km fields have 3.
  - `stops[].station.location` is the cached station point.
  - `plan_payload` is the response dumped with `mode="json", by_alias=True`, so `GET /trips/{id}` returns the identical body (tested).
- **`is_simulated`:** set when the vehicle's cached state says `source = "simulated"`. A simulated vehicle with no state yet is stored as not simulated. P6-F2 (demo mode) should check this.
- **Errors:** Mapbox and AFDC failures come from the adapters as the existing envelopes. Nothing is committed, so the trip and any station upserts from that request are rolled back.
- **Logging:** one `route_plan` line per stored plan, with status, reason code, stop count, candidate counts, `preferred_networks_relaxed` and `{directions,stations,optimizer,persist}_ms`. Coordinates, geometry and station names are never logged (tested).
- **Latency on fixtures:** Frederick → Toronto route A, the real Mapbox and AFDC fixtures over `MockTransport` (119 stations after the adapter drops restricted ones; 118 considered, 1 excluded for unknown power, corridor 6.41 km), local Postgres, Apple M3 Pro, 5 runs.

  | Stage | Time |
  |---|---|
  | Whole request (TestClient) | 298–302 ms |
  | Stations (decode, simplify, upsert, project) | 153–158 ms |
  | Optimizer (two runs: no-candidate check, then full) | 31 ms |
  | Persist | 10–12 ms |
  | Directions (fixture, no network) | 1.5 ms |

  The test asserts the whole request takes < 3 s. Real provider latency (Mapbox and AFDC paging over the network) is left to F9's real-key run.
- **Tests:** `tests/routing/test_plan_endpoint.py`, 57 tests, covering every F4 test bullet plus:
  - §5 trips 1 and 2 end to end on synthetic straight routes (B at 0.173 → 0.66, 17 min, total 295; trip 1 arrives at 0.495 with no stations call);
  - a vehicle-capped charge-to, a user curve overriding the catalog, and a row copy of the catalog;
  - Mapbox 401/422/500/timeout and an AFDC 503;
  - `route_index` choosing the alternative route;
  - a limiting segment located on the map;
  - body validation (no client geometry accepted);
  - the rounding and corridor helpers.

  `tests/routing/conftest.py` gains `north_of` and `synthetic_directions`; the optimizer helpers are unchanged.

### F5 — Preferences editor

Done (PR #27). No change, except one note for F4: `preferred_networks` is free text, matched as described in F4.

### F6 — Planner UI

Uses `POST /routes/plan` on the `/trips` page after a route is selected. Owns list and acceptance are in plan.md. Contract-driven details:
- `STARTING_SOC_REQUIRED` → SoC prompt, prefilled from `details.last_soc` and showing `details.last_recorded_at`.
- `VEHICLE_SPECS_INCOMPLETE` → message naming the missing fields, with a link to the Garage specs editor.
- Feasible: stops on the map, each labelled with arrival → departure SoC; per-stop charging minutes; detour marked "estimate"; totals; assumptions shown in a collapsible "How this was calculated" section, including "charging curve: derived from published figures" when `charging_curve_source = "catalog_derived"`, and the excluded-station counts.
- Infeasible: a prominent state with `reason.message` and the limiting segment highlighted on the map; no route styled as valid.
- Save → `POST /trips/{id}/save`; "Saved trips" link to F7's pages. Re-plan with preferences sends `preferences` in the request.

### F7 — Trip history UI

`GET /trips` (saved by default) with pagination and empty state; detail from `GET /trips/{id}` rendering the stored plan (no recompute); delete via `DELETE /trips/{id}`. Shows infeasible saved trips with their reason.

**As built:**
- **Routes:** `/trips/saved` (list) and `/trips/[id]` (detail) are server components, built like `/charging/[id]`. Neither touches `trips/page.tsx` or `trips/_components/`. `/trips/saved` is a static segment, so it takes precedence over `[id]`. The detail page returns 404 for a non-UUID id or an API 404. It opens any of the caller's trips, saved or not, so P4-F6 can link to an unsaved plan as well.
- **Pagination:** forward only, through `?cursor=` in the page URL, with an "Older trips →" link and a "← First page" link (`limit=20`).
  - The cursor is passed to the API only if it looks like base64url (at most 512 characters).
  - A 422 for a stale cursor shows "That page link has expired" with a link to the first page.
  - The empty state ("No saved trips yet") links to `/trips`. An empty later page says "No more saved trips." instead.
- **List row:** origin → destination (an unlabelled endpoint shows as "Unnamed start" or "Unnamed destination"), a feasible or infeasible badge, distance in the user's units, total time, stop count, and `saved_at` in the viewer's local time. A null distance or total shows "—".
- **Reading the stored plan:** the API types `plan` as a free-form object, so `components/trips/plan-payload.ts` reads the §3.7 body defensively.
  - A missing or wrong-typed field becomes `null` and shows as "—". It is never guessed and never throws.
  - Stops are sorted by `sequence`.
  - Nothing is recomputed: totals, arrival SoC and minutes are shown exactly as stored. SoC is shown as a whole percent.
- **Detail page:**
  - A map from `LazyMapView`: the stored route, the stops as markers and the origin as centre. There is no map when `route` is null (`NO_DRIVING_ROUTE`).
  - A summary: distance, starting SoC and its source, then arrival SoC, drive, charging, detour (estimate), overhead and total. An infeasible plan shows only drive time.
  - Stops: network, connector, kW, arrival → departure SoC, charging minutes, km along the route, and the detour estimate. Each stop links to `/charging/{station.id}`.
  - A collapsible "How this was calculated" section built from `assumptions` and `candidates`. It includes "derived from published figures, not measured" for `catalog_derived` and the excluded-station counts.
- **Infeasible trips:** a red `role="alert"` block with the reason title, `reason.message` and the limiting segment as "from X to Y along the route (needs N% charge, M% available)". The segment isn't drawn on the map, because `MapViewProps` has no prop for a partial route. P4-F6 needs one for its infeasible state, as a small follow-up in `components/map/`.
- **Delete:** an AlertDialog confirm step, then the `deleteTrip` server action in `components/trips/actions.ts`. It redirects to `/trips/saved` on 204 or 404 (already gone); any other failure keeps the dialog open with an error.
- **Tests:** `tests/trip-plan-payload.test.ts`, `trip-list.test.tsx`, `trip-detail.test.tsx` and `trip-actions.test.ts`, 21 tests in all, with fixtures in `tests/trip-fixture.ts` built from the §3.7 examples. No Playwright spec: nothing creates trips until P4-F4, and seeding rows would need E2E harness changes. P4-F9 or P4-F6 can add one once `/routes/plan` exists.
- **Not checked live:** the pages weren't opened in a browser against real data, because there are no trips to show until P4-F4. `npm run build` compiles both routes.

### F8 — Per-vehicle consumption baseline (follow-up)

Added after P4-F0 review (D1): every vehicle consumes differently, so the user should be able to set their own baseline, and vehicles without a catalog entry should become plannable. Files and lane: plan.md §4.2 P4-F8.

**Contract first** (§3.4, §3.4a): `specs.baseline_kwh_per_km` in `GET /vehicles/{id}` (value from the row, or `null`) and as an optional `PATCH /vehicles/{id}/specs` key: `> 0` and `<= 1` kWh/km, at most 3 decimals, `null` clears it (back to the catalog value). `GET` also returns `baseline_source: "vehicle" | "catalog" | null` so the Garage can show where the effective value comes from.

**Database:** nullable `vehicle_specs.baseline_kwh_per_km numeric(5,3)` with `CHECK (baseline_kwh_per_km > 0 AND baseline_kwh_per_km <= 1)`. No backfill: an empty value falls back to the catalog (D14).

**Planner:** D14 resolution covers `baseline_kwh_per_km` like the other spec fields (row, then catalog, else `VEHICLE_SPECS_INCOMPLETE`); `assumptions.baseline_source` reports `vehicle` or `catalog`.

**Web:** the Garage specs panel gets a "Consumption" field. The user types it in their unit preference (Wh/mi for imperial, kWh/100 km for metric); the client converts to kWh/km. The field shows the catalog value as a placeholder and says it's a highway estimate.

**Tests:** migration round-trip and one head; PATCH validation (bounds, decimals, `null` clears); GET reports the source; a vehicle with no catalog entry plans once a baseline is set and returns `VEHICLE_SPECS_INCOMPLETE` (missing `baseline_kwh_per_km`) before; a user value overrides the catalog in `/routes/plan`; unit conversion in the web form (imperial and metric).

**Later (not F8):** suggest a personal baseline from observed trips once Phase 5 has data (ML Stage C).

### F9 — Close-out

Fills §6 with test names, records the ~500 mi latency in the roadmap, runs the real-key Frederick → Toronto plan with a screenshot, and checks the §5 numbers still pass.

---

## 4. Interfaces (D9)

All in Python 3.12, frozen dataclasses, SoC as fractions in `[0, 1]`, distances in km, power in kW, energy in kWh, time in minutes. No Pydantic in the pure modules (F2, F3); F4 maps to and from the API schemas.

### 4.1 `app/modules/routing/plan_types.py` (F3)

```python
from __future__ import annotations
import uuid
from dataclasses import dataclass
from typing import Literal

from app.modules.energy.types import EnergyVehicle

InfeasibleCode = Literal[
    "STARTING_SOC_TOO_LOW",
    "RESERVE_SOC_UNREACHABLE",
    "NO_COMPATIBLE_CHARGER_IN_RANGE",
    "DESTINATION_UNREACHABLE",
    "NO_DRIVING_ROUTE",  # set by F4 only, never by the optimizer
]

@dataclass(frozen=True)
class RouteProfile:
    distance_km: float
    duration_minutes: float          # Mapbox duration; D13 linear interpolation

@dataclass(frozen=True)
class CandidateStation:
    station_id: uuid.UUID            # charging_stations.id (P3-F5 upsert)
    name: str
    network: str | None
    connector: str                   # NACS | CCS1 | J1772 | CHAdeMO, the type used (D12)
    power_kw: float                  # known, > 0 (D12)
    distance_along_route_km: float
    distance_from_route_km: float
    lat: float
    lon: float

@dataclass(frozen=True)
class PlannerSettings:
    reserve_soc: float
    charge_to_soc: float             # min(preferred_charge_to, vehicle max_charge_soc)
    stop_overhead_minutes: float
    detour_road_factor: float
    detour_speed_kmh: float
    soc_grid_step: float

@dataclass(frozen=True)
class PlannedStop:
    station: CandidateStation
    arrival_soc: float
    departure_soc: float
    charging_minutes: float
    detour_km: float
    detour_minutes: float

@dataclass(frozen=True)
class FeasiblePlan:
    stops: tuple[PlannedStop, ...]
    arrival_soc: float
    drive_minutes: float
    charging_minutes: float
    detour_minutes: float
    overhead_minutes: float
    total_minutes: float
    energy_kwh: float                # route + detours

@dataclass(frozen=True)
class LimitingSegment:
    from_km: float                   # along the route; F4 converts to lat/lon
    to_km: float
    required_soc: float              # SoC needed at from_km to reach to_km with reserve
    available_soc: float             # best SoC the plan can have at from_km

@dataclass(frozen=True)
class InfeasiblePlan:
    code: InfeasibleCode
    message: str
    limiting_segment: LimitingSegment | None

PlanResult = FeasiblePlan | InfeasiblePlan
```

### 4.2 `app/modules/energy/` (F2)

```python
# energy/types.py
@dataclass(frozen=True)
class CurvePoint:
    soc: float
    power_kw: float

@dataclass(frozen=True)
class EnergyVehicle:
    usable_capacity_kwh: float
    baseline_kwh_per_km: float
    charging_curve: tuple[CurvePoint, ...]   # ≥ 2 points, soc strictly increasing, power > 0

# energy/model.py
def segment_energy_kwh(vehicle: EnergyVehicle, distance_km: float) -> float: ...
def soc_used(vehicle: EnergyVehicle, distance_km: float) -> float: ...
def arrival_soc(vehicle: EnergyVehicle, start_soc: float, distance_km: float) -> float:
    """May be negative; callers compare with the reserve."""

# energy/charging.py
def max_charge_soc(vehicle: EnergyVehicle) -> float:
    """SoC of the curve's last point."""

def charging_minutes(
    vehicle: EnergyVehicle, from_soc: float, to_soc: float, station_power_kw: float
) -> float:
    """∫ B / min(P(s), station) ds in minutes; 0 when to_soc <= from_soc.
    ValueError if a SoC is outside [0, max_charge_soc] or station_power_kw <= 0."""

def cumulative_charge_minutes(
    vehicle: EnergyVehicle, station_power_kw: float
) -> Callable[[float], float]:
    """F(s) = charging_minutes(vehicle, 0, s, station_power_kw), precomputed once per
    distinct station power so the optimizer does F(d) − F(a) in O(log n)."""
```

### 4.3 `app/modules/routing/optimizer.py` (F3)

```python
def optimize(
    route: RouteProfile,
    candidates: Sequence[CandidateStation],
    vehicle: EnergyVehicle,
    settings: PlannerSettings,
    starting_soc: float,
) -> PlanResult: ...
```

Algorithm (ADR 0008):
1. If `starting_soc <= reserve_soc` → `STARTING_SOC_TOO_LOW`.
2. If `arrival_soc(start, route.distance_km) >= reserve` → feasible with no stops.
3. Sort candidates by `distance_along_route_km` (ties by `station_id`). For candidate *j*: `leg_km(i→j) = along_j − along_i + 2 · from_route_j · road_factor`. Arrival `a = d_i − soc_used(leg_km)`, valid if `a >= reserve`.
4. State `(j, d)` for departure SoC `d` on the grid, `a < d <= charge_to`. Cost = minimum over predecessors `(i, d_i)` (including the origin with `d = starting_soc`) of `cost(i, d_i) + detour_minutes_j + overhead + F_j(d) − F_j(a)`. For each *j*, sort predecessor arrivals by `a` and use a prefix minimum of `cost − F_j(a)`, so each state costs O(1) after an O(k log k) sort.
5. Destination: minimum over states with `d − soc_used(L − along) >= reserve`. Ties: fewer stops, then lower total charging, then earlier stations.
6. If there is no solution: run again with `reserve = 0`; if that works → `RESERVE_SOC_UNREACHABLE` (limiting segment from that plan's lowest arrival). Otherwise, if no candidate is reachable from the origin → `NO_COMPATIBLE_CHARGER_IN_RANGE` (origin → reserve point). Otherwise → `DESTINATION_UNREACHABLE` with the furthest reachable along-route point and the next gap that can't be crossed.
7. Drive minutes = `route.duration_minutes`; total = drive + Σ(charging + detour + overhead).

### 4.4 `app/modules/trips/service.py` (F1)

```python
@dataclass(frozen=True)
class TripEndpoint:
    lat: float
    lon: float
    label: str | None

@dataclass(frozen=True)
class NewPlannedStop:
    sequence: int                     # 1-based
    station_id: uuid.UUID | None
    arrival_soc: float
    departure_soc: float
    charging_minutes: int

def create_planned_trip(
    db: Session,
    *,
    trip_id: uuid.UUID,               # generated by F4 so plan_payload can include it
    created_at: datetime,
    user_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    origin: TripEndpoint,
    destination: TripEndpoint,
    departure_time: datetime | None,
    plan_status: Literal["feasible", "infeasible"],
    plan_payload: Mapping[str, Any],  # the full §3.7 200 body
    model_version: str,
    stops: Sequence[NewPlannedStop],
    is_simulated: bool = False,
) -> None: ...
```

### 4.5 `app/modules/routing/planner_service.py` (F4)

```python
def plan_trip(
    db: Session,
    *,
    user_id: uuid.UUID,
    request: PlanRequest,             # routing/schemas.py, contract §3.7
    settings: Settings,
    mapbox: MapboxClient,
    station_provider: StationProvider,
    now: datetime | None = None,
) -> PlanResponse: ...
```

Steps (each through the owning module's service function, no cross-module queries):
1. Vehicle + specs: `vehicles.garage.get_vehicle_detail(...)` (404 if not owned), then D14 resolution with `vehicles.spec_catalog.lookup_spec`. Build `EnergyVehicle` or raise `VEHICLE_SPECS_INCOMPLETE`.
2. Preferences: `users.service.get_or_create_profile(...)`, overlaid with request `preferences` (same validation as `PATCH /me/preferences`). `charge_to = min(preferred_charge_to, max_charge_soc)`.
3. Starting SoC (D7): override, or `vehicles.vehicle_state.get_vehicle_state(...)` with the per-signal `soc_percent` timestamp.
4. Directions: `mapbox.directions(...)`; empty → infeasible `NO_DRIVING_ROUTE`; `route_index` beyond the list → 422 `ROUTE_INDEX_UNAVAILABLE`.
5. If no charge is needed (`optimize` with no candidates is feasible), skip step 6.
6. Stations: `stations.service.search_along_route(...)` (P3-F5; use the signature in its "As built" notes) with the route geometry, the D4 corridor, the vehicle's connectors and `dc_fast`. Map to `CandidateStation` with the D4/D12 filters, counting exclusions.
7. `optimizer.optimize(...)`, with the preferred-network retry (F4).
8. Build the §3.7 body (limiting segment km → lat/lon by interpolating along the decoded polyline), then `trips.service.create_planned_trip(...)`. Return.

---

## 5. Hand-checked sample trips (D8)

All three use the §2.3 bZ entry and the default preferences: `reserve_soc` 0.15, `preferred_charge_to` 0.80, overhead 5 min, road factor 1.3, detour speed 50 km/h, 1 % grid.

Constants: B = 71.0 kWh, c = 0.20 kWh/km → **1 km = 0.20 / 71.0 = 0.0028169 SoC** (0.282 %). The vehicle's max charge SoC is 1.00, so `charge_to` = 0.80. Station lists and fixture routes 1–2 are synthetic (F3 builds them inline in its tests); trip 3 uses the real Mapbox directions fixture's distance and duration ([`fixtures/mapbox/directions_frederick_toronto.json`](../fixtures/mapbox/directions_frederick_toronto.json): 748,107.9 m, 30,890.7 s).

Charging pieces use the F2 formula: `t = 60·B·Δs/(p2−p1)·ln(p2/p1)` (or `60·B·Δs/p` when flat), with the bZ curve capped at the station power.

### Trip 1 — Local, no charge

Frederick → Baltimore (synthetic fixture): 80 km, 60 min, start 0.72.

| Step | Calculation | Result |
|---|---|---|
| Energy | 80 × 0.20 | 16.0 kWh |
| SoC used | 16.0 / 71.0 | 0.22535 |
| Arrival | 0.72 − 0.22535 | **0.49465** ≥ 0.15 ✓ |
| Plan | no stops, no stations call (F4 step 5) | `feasible`, 0 stops, **total 60 min**, arrival **0.495** |

### Trip 2 — ~250 mi, one stop

Synthetic 400 km (249 mi) route, 270 min, start 0.80. Candidates:

| ID | Along route | Off route | Power | Connector | Fate |
|---|---|---|---|---|---|
| L | 120 km | 0.2 km | 50 kW | CCS1 | candidate |
| A | 150 km | 0.5 km | 150 kW | CCS1 | candidate |
| X | 200 km | 0.3 km | 150 kW | CHAdeMO | excluded: incompatible |
| B | 220 km | 1.0 km | 250 kW | NACS | candidate (vehicle caps it at 150) |

No charge: 400 km uses 1.12676 SoC, so it doesn't reach the destination at all → a stop is needed.

**Chosen: B**
- Leg to B: 220 + 2 × 1.0 × 1.3 = 222.6 km → SoC used 0.62704 → **arrival 0.17296** ≥ 0.15 ✓
- Needed at departure: 0.15 + (400 − 220) × 0.0028169 = 0.15 + 0.50704 = 0.65704 → grid → **0.66**
- Charging 0.17296 → 0.66 (station 250 kW, so the vehicle curve applies):

| Piece | p1 → p2 (kW) | Minutes |
|---|---|---|
| 0.17296 → 0.30 | 150 → 150 | 60·71·0.12704/150 = **3.608** |
| 0.30 → 0.50 | 150 → 120 | 60·71·0.20/(−30)·ln(0.8) = **6.337** |
| 0.50 → 0.66 | 120 → 85.33 | 60·71·0.16/(−34.667)·ln(0.71111) = **6.703** |
| **Total** | | **16.648** |

- Detour: 2.6 km / 50 km/h = **3.12 min**; overhead **5 min**
- Extra = 16.648 + 3.12 + 5 = **24.77 min** → **total 294.8 min** (API 295)
- Arrival at destination: 0.66 − 0.50704 = **0.15296**
- API: stop 1 = B, arrival 0.173, departure 0.660, charging 17 min, detour 3 min.

**Rejected alternatives**

| Alternative | Why |
|---|---|
| A only | Arrival 0.80 − 151.3 × 0.0028169 = 0.37380; would need 0.15 + 250 × 0.0028169 = 0.85423 > charge-to 0.80 → infeasible |
| L only | Arrival 0.46018; would need 0.15 + 280 × 0.0028169 = 0.93873 > 0.80 → infeasible |
| A then B | Best is A 0.374→0.38, B 0.175→0.66: 31.45 extra min > 24.77 (an extra overhead plus a second detour) |
| X | Never a candidate (CHAdeMO; the vehicle has NACS/CCS1/J1772) |

### Trip 3 — Frederick, MD → Toronto, ON, three stops

Real fixture route: **748.1 km**, **514.8 min**, start **0.90**. Synthetic candidates (positions chosen for the test, not real stations):

| ID | Along | Off | Power | Connector | Fate |
|---|---|---|---|---|---|
| H | 130 | 0.6 | 250 | NACS | candidate |
| W | 260 | 0.8 | 150 | CCS1 | candidate |
| E | 330 | 0.4 | 62.5 | CCS1 | candidate |
| U | 400 | 0.2 | unknown | CCS1 | excluded: unknown power (D12) |
| B | 420 | 1.2 | 250 | NACS | candidate |
| Q | 560 | 0.5 | 150 | CCS1 | candidate |
| X | 600 | 0.3 | 350 | CHAdeMO | excluded: incompatible |
| N | 640 | 0.7 | 250 | NACS | candidate |

**Why at least three stops.** Energy for the route: 748.1 × 0.20 = 149.62 kWh = 2.10732 SoC. Available without detours: start to reserve 0.90 − 0.15 = 0.75, plus at most 0.80 − 0.15 = 0.65 per stop. Two stops give at most 2.05 < 2.107, so two stops can't work. Three stops give 2.70.

**Chosen: W → B → Q**

| Stop | Leg km (along + detour) | SoC used | Arrival | Depart | Charging | Detour |
|---|---|---|---|---|---|---|
| W | 260 + 2.08 = 262.08 | 0.73825 | 0.90 − 0.73825 = **0.16175** | **0.61** | 14.617 min | 2.496 min |
| B | 160 + 3.12 = 163.12 | 0.45949 | 0.61 − 0.45949 = **0.15051** | **0.55** | 12.443 min | 3.744 min |
| Q | 140 + 1.30 = 141.30 | 0.39803 | 0.55 − 0.39803 = **0.15197** | **0.68** | 18.269 min | 1.560 min |
| Dest | 188.1 | 0.52986 | 0.68 − 0.52986 = **0.15014** | | | |

Charging pieces:

| Stop | Pieces (SoC: minutes) | Total |
|---|---|---|
| W (150 kW, no cap) | 0.16175→0.30 flat 150: 3.926 · 0.30→0.50: 6.337 · 0.50→0.61 (120→96.17): 4.353 | **14.617** |
| B (250 kW, capped by vehicle) | 0.15051→0.30 flat 150: 4.246 · 0.30→0.50: 6.337 · 0.50→0.55 (120→109.17): 1.860 | **12.443** |
| Q (150 kW) | 0.15197→0.30 flat 150: 4.204 · 0.30→0.50: 6.337 · 0.50→0.68 (120→81.00): 7.728 | **18.269** |

Totals:
- Charging 45.329 + detour 7.800 + overhead 15 = **68.13 extra min** → **total 582.9 min** (9 h 43 min; API 583)
- Energy 149.62 + (2.08 + 3.12 + 1.30) × 0.20 = **150.92 kWh**
- Arrival **0.150**

Checks: in this plan each departure happens to be the smallest grid value that reaches the next point (W→B needs 0.6095 → 0.61; B→Q needs 0.5480 → 0.55; Q→destination needs 0.6799 → 0.68). The DP doesn't force that; it is what minimizes time here, because charging above 0.50 is slower. E (62.5 kW) is never chosen: charging there is capped below the vehicle's curve. The F3 test asserts the chosen stops and SoCs to 3 decimals, and each minutes value to 0.01.

---

## 6. PRD §9 acceptance mapping

F9 fills in the "Test" column with real test names.

| PRD §9 case | Planned test (feature) | Test |
|---|---|---|
| Start at 100 % | F3: start 1.00 above charge-to, plan valid | |
| Start at 0 % | F3: `STARTING_SOC_TOO_LOW` | |
| Unknown SoC | F4: `STARTING_SOC_REQUIRED` (missing reading) | |
| No charging needed | F3 trip 1; F4 no stations call | |
| Chargers nearby but incompatible | F3/F4: X excluded, `NO_COMPATIBLE_CHARGER_IN_RANGE` when only incompatible | |
| Charger too far to reach | F3: first candidate beyond the reserve point | |
| Two shorter stops beat one long | F3: P+Q case (F3 tests) | |
| Missing station power or pricing | F4: unknown power excluded and counted; pricing is never used | |
| Stale telemetry | F4: `STARTING_SOC_REQUIRED` (stale `soc_percent`) | |
| Smartcar / Mapbox / AFDC failure | F4: `MAPBOX_UNREACHABLE`, `STATIONS_UNREACHABLE`; Smartcar isn't called (cached state, D7) | |
| Reserve unreachable → infeasible | F3: `RESERVE_SOC_UNREACHABLE`; F6: infeasible state | |

---

## 7. Open questions and follow-ups

1. **Per-vehicle consumption baseline**: now feature **F8** (§3). It needs the migration lane, so the coordinator schedules it (plan.md W7).
2. **Measured bZ charging curve**: replace the derived curve after a real DC session is logged (Smartcar doesn't report charging power on the bZ yet, smartcar-v3-notes §7, so this may need a manual log).
3. **Stale threshold**: switch from 15 min to the P2-R2 measured value when it is recorded.
4. **Preferred-network names**: the editor (P4-F5) stores free text; matching is fuzzy (F4). A follow-up could offer AFDC network values in the editor.
5. **Cold weather**: the 0.20 baseline is a mild-weather figure; EV Database's cold highway figure is ~0.26 kWh/km. Temperature adjustment belongs to the ML Stage A/B work, not v1.
6. **Unsaved-plan retention**: P6-F5's retention job deletes `saved_at IS NULL` rows older than 30 days.

## 8. Sources

- Toyota pressroom, "Toyota bZ All Electric SUV Adds Range, Charging, and Exterior Updates for 2026": 74.7 kWh (XLE AWD), 288 mi, 10–80 % in about 30 minutes. https://pressroom.toyota.com/?p=102972
- EV Database, Toyota bZ4X Touring AWD 74.7 kWh (MY26): 71.0 kWh usable, 150 kW peak, 110 kW average 10–80 %, 323 Wh/mi mild highway, 418 Wh/mi cold highway. https://ev-database.org/imp/car/3401/Toyota-bZ4X-Touring-AWD-747-kWh
- fueleconomy.gov, 2026 Toyota: bZ AWD 28 kWh/100 mi combined. https://fueleconomy.gov/feg/byMake/Toyota2026.shtml
- InsideEVs, "Why The 2026 Toyota bZ Doesn't Get Crazy Fast Charging Speeds" (400 V architecture; no full charging test published yet). https://insideevs.com/news/760604/2026-toyota-bz-max-charging-speed-limited/
- ABRP feedback board (staff reply: charging overhead defaults to 5 minutes). https://abrp.featurebase.app/p/reintroduce-manual-charge-overhead-time
- TomTom EV routing preferences ("charging time offset", default 180 s). https://developer.tomtom.com/electric-vehicle/ev-routing/preferences-and-personalization/navsdk
