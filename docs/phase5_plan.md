# Phase 5 Plan: History and Analytics

Status: **Approved 2026-11-18**: P5-D1…D7 settled as in §2 (D1, D3, D4 and D5 changed from the [`plan.md`](plan.md#43-phase-5-history-and-analytics) recommendations; D2 changed to a background trigger at the user's request; D8–D14 added) · Created: 2026-11-18 by P5-F0 · Phase owner: project lead

This is the working plan for [Phase 5 of the roadmap](development-roadmap.md#phase-5--history-and-analytics-weeks-1011). [`plan.md`](plan.md) §4.3 decides *when* each feature runs and *which files* it owns; this file decides *what* each feature builds. Each feature session edits only its own section here (for "As built" notes).

Based on: roadmap Phase 5, [`product-requirements.md`](product-requirements.md) §4.5 and §7, [`architecture.md`](architecture.md) §3, §5 and §7, [`api-contracts.md`](api-contracts.md) §1, §3.5, §3.7–3.9b and §3.11, [`database-design.md`](database-design.md) §3.3–3.5 and §3.7–3.10, [`phase4_plan.md`](phase4_plan.md) §2 and §4, [`smartcar-v3-notes.md`](smartcar-v3-notes.md) §4–§7, and ADRs [0004](adr/0004-deterministic-energy-baseline-before-ml.md) and [0009](adr/0009-in-process-post-ingest-hook-for-trip-inference.md).

**Not in Phase 5:** vehicle location or trip paths, weather and speed trends, manufacturer-range-vs-predicted-range charts, charging-session records, ML models, manual creation of observed trips.

---

## 1. Goal and exit criteria

**Goal:** real drives turn into `observed_trips` rows on their own, each with its distance, its energy when SoC allows, and an honest quality flag. Plans are matched to the drives that carried them out. The Analytics page compares the deterministic model with what happened, without mixing in simulated data, and the CSV export equals the table on screen.

| # | Exit criterion (roadmap Phase 5 + PRD §4.5) | Verified by |
|---|---|---|
| X1 | A handful of real trips produce `observed_trips` rows **without manual intervention** (no button, no script). | F3 tests (hook fires after ingest); F8 real-car check |
| X2 | Driving and charging are separated: a leg never contains a charging session. | F2 unit tests (§5 example 3) |
| X3 | Every trip shows its measurement quality (`quality_flag` + reasons). | F2 tests; F6/F7 UI tests |
| X4 | Analytics shows estimated vs observed energy and its error, and never mixes simulated data silently. | F5 tests; F6 Playwright |
| X5 | CSV export reproduces the rows in the analytics table. | F5 test (CSV rows = JSON rows) |
| X6 | A plan is matched to the drive that carried it out, and the trip page shows planned vs observed. | F4 tests (§5 example 3); F7 UI |

---

## 2. Decisions

### 2.1 What the data allows

These facts drive most of the decisions below:

- **Signals on the 2026 bZ** ([`smartcar-v3-notes.md`](smartcar-v3-notes.md) §7): `soc` (whole percent, e.g. 61), `odometer` (km, 0.01 resolution, e.g. 24650.32), `charging_status` (bool), `charging_status_detail`, `cable_connected`. **No location** (not requested, Phase 3 D12), **no charging power**, no battery capacity signal.
- **Event cadence is unknown.** P2-R2 hasn't measured it. Smartcar sends `VEHICLE_STATE` when a monitored signal changes, at-least-once and possibly out of order. The design has to work whether events arrive every few minutes while driving or only at key-off.
- **Whole-percent SoC is coarse.** With the 71.0 kWh usable capacity, 1 point = 0.71 kWh. A 10 km drive uses about 2 kWh = about 3 points, so rounding alone makes its energy uncertain by about ±35 %. Quality must depend on how many SoC points a leg used.

### 2.2 The seven decisions from plan.md

| # | Decision | Settled | Changed from plan.md? |
|---|---|---|---|
| **D1** | Trip inference | One `observed_trips` row is one **driving leg**: a stretch of odometer increase with no charging inside it. A leg ends at a charging session, at a stop of `stop_split_minutes` (20) or more, or before a reading gap of more than 20 minutes. Distance comes from the odometer. Energy = (SoC at start − SoC at end) × usable capacity, `energy_source = 'soc_inferred'`, only when both SoC readings exist, SoC didn't rise in between, and capacity is known; otherwise energy is `null`. The start time is stored as a **bound** (`start_time` … `start_time_latest`) because a sparse feed only says the car left "some time after" a reading. Quality rules in D8. Algorithm in §4.1. | **Yes.** (1) Legs instead of whole journeys: a road trip with two DC stops is three legs, so every energy number is pure driving, and a plan is matched to a chain of legs (D4). (2) **`odometer_inferred` is dropped**: energy from distance × baseline is a *prediction*, and storing it as an observation would be fabricating data (PRD §7). (3) Start-time bounds and explicit reason codes, because the cadence is unknown. |
| **D2** | When inference runs | **Automatically, in the background, after each webhook** that stored an `odometer`, `soc` or `charging_status` reading. The webhook router schedules registered post-ingest hooks with FastAPI `BackgroundTasks`, which run in the same process after the response is sent. `vehicles` owns the hook registry and never imports `trips`; `trips` registers its hook ([ADR 0009](adr/0009-in-process-post-ingest-hook-for-trip-inference.md)). Runs for one vehicle are serialized with a Postgres advisory lock and are idempotent. `POST /api/v1/observed-trips/recompute` and `scripts/infer_trips.py` stay as catch-up paths (after a deploy, a bug fix, or a full rebuild with `--full`). No queue, no cron. | **Yes** (user decision). plan.md had on-demand only. The background trigger meets X1 without any page visit, and the webhook response time doesn't change. |
| **D3** | Manual confirmation | `POST /observed-trips/{id}/confirm` and `/reject` on **any** leg, not only low-quality ones (someone else drove, a tow, a ferry). `review_status` is `auto` (high/medium), `needs_review` (low), `confirmed` or `rejected`. Confirmed and rejected legs are **frozen**: inference never changes or deletes them. Confirming doesn't change `quality_flag` or the numbers. Contract §6 question 3: **no** `POST` for manually created observed trips in the MVP (it would invite made-up data); `energy_source = 'manual'` stays reserved. | Small change: any leg, plus the frozen rule. |
| **D4** | Plan ↔ observation matching | One plan matches **one chain of consecutive legs**. Rules: same vehicle; feasible plan with `status = 'planned'`, saved or not; chain start within ±2 h of `departure_time` when the plan has one, else within [`created_at` − 15 min, `created_at` + 24 h]; gaps between legs in the chain ≤ 120 min; chain distance within max(5 km, 10 %) of the planned distance (route + stop detours). A plan matches at most one chain and a leg belongs to at most one plan. A match sets `observed_trips.planned_trip_id` on each leg and `planned_trips.status = 'completed'`, and writes a `kind = 'plan'` row to `prediction_results`. Algorithm in §4.2. | **Yes.** plan.md's "start/end within 5 km of origin/destination" can't work: we have no location (D7). A charging stop splits a drive into legs (D1), so one-to-one plan ↔ leg would miss every multi-stop trip. |
| **D5** | Chart library | **No chart library.** P5-F6 draws its three charts (bars per bucket, an estimated-vs-observed line per bucket, and a per-trip scatter of estimated vs observed kWh) as small React SVG components in `components/analytics/charts/`, each with a visible data table for screen readers and keyboard users. If a later feature needs zoom, brushing or very large series, use **uPlot** (1.6.32: 22 kB gzipped, no dependencies), added under the dependency lane with its own approval. | **Yes.** Recharts 3.11 is 177 kB gzipped (bundlephobia, 2026-11-18) and pulls in Redux Toolkit, Immer and react-redux. That's a lot of bundle and lockfile churn for three simple charts. The guidelines say to avoid unnecessary dependencies, and the roadmap asks for "whatever chart library is lightest". Side effect: P5-F6 no longer holds the dependency lane, so it doesn't wait for P6-F1. |
| **D6** | CSV export | `GET /api/v1/analytics/trips.csv`, with the same filters and the same columns, in the same order, as `GET /api/v1/analytics/trips` (the JSON the table uses). Units stay raw (km, kWh, percent) and are part of the column names. Cells that start with `=`, `+`, `-`, `@`, tab or CR get a leading `'` (CSV injection); this applies to text columns only, never to numbers. At most 10,000 rows; more → `422 VALIDATION_ERROR` with `details.reason = "EXPORT_TOO_LARGE"`, so a file is never silently cut short. | Small change: a JSON twin so "CSV equals the UI" is testable, injection escaping and the row cap. |
| **D7** | Vehicle location | Still not requested. Trips get distance from the odometer, not paths. `observed_trips` has no location column (db-design §9 question 2: no `path` until mobile tracking exists). | No |

### 2.3 Decisions added by P5-F0

| # | Decision | Settled |
|---|---|---|
| **D8** | Quality rules | For each leg: ΔSoC = `soc_start − soc_end` (points); start age = `start_time_latest − soc_start_recorded_at`; end age = `soc_end_recorded_at − end_time`. **high**: energy known, ΔSoC ≥ 10, both ages ≤ 15 min, exact start (`start_time = start_time_latest`). **medium**: energy known, ΔSoC ≥ 4, both ages ≤ 360 min. **low**: everything else. `quality_reasons` lists every rule a leg missed, from: `soc_missing`, `soc_increased`, `capacity_unknown`, `soc_delta_small` (< 10), `soc_reading_stale` (> 15 min), `start_time_bounded`, `odometer_regression`. `energy_kwh_uncertainty = usable_capacity_kwh × soc_resolution_points / 100` (1 point by default: ±0.5 at each end), so 0.71 kWh for the bZ. All thresholds are config settings (§3 F2). |
| **D9** | Backtests for every leg | Most driving has no plan. So, besides plan scoring (D4), every finished (not in-progress) leg with energy gets a `kind = 'leg'` row in `prediction_results`: predicted energy = `energy.segment_energy_kwh(vehicle, distance_km)` with the vehicle's baseline (phase4_plan D14 rule: specs row, then catalog), `model_version = 'deterministic-v1'`. Analytics MAE uses these rows. This is the "baseline judged against reality" that ML Stage B needs (roadmap §3). |
| **D10** | Usable capacity for energy | The same row-then-catalog rule as phase4_plan D14, read when inference runs, and stored on the leg (`usable_capacity_kwh`, `capacity_source`). No capacity → `energy_kwh = null`, reason `capacity_unknown`. The Smartcar SoC is assumed to be a percentage of *usable* capacity (the dashboard figure); this is listed under "not modelled" in §7. |
| **D11** | Simulated data | The engine takes `vehicle_is_simulated` and drops every reading whose `is_simulated` differs (counted in `dropped_simulated`). Legs and prediction rows copy `is_simulated` from the vehicle. Analytics and CSV filter `is_simulated = false` unless `include_simulated=true`, and the response says how many simulated trips were left out (`excluded_simulated_trips`), so nothing is mixed in or hidden silently. |
| **D12** | Which legs count in aggregates | `included_in_aggregates = review_status IN ('auto','confirmed') AND NOT in_progress AND (is_simulated = false OR include_simulated)`. Energy metrics (observed/estimated kWh, MAE) also need `energy_kwh` to be non-null. The analytics table lists every non-rejected leg, low-quality ones included and flagged, with this column, so the table, the chart and the CSV agree. |
| **D13** | Buckets and time zone | `group_by = day | week | month` in the IANA time zone given by `tz` (default `UTC`; an unknown zone → `VALIDATION_ERROR`). A leg is bucketed by its `end_time` (always a real reading). Weeks are ISO weeks (Monday start, label `2026-W45`). Every bucket in `[from, to]` is returned, empty ones with zeros and `null` ratios, so charts have a continuous x-axis. At most 366 buckets; more → `VALIDATION_ERROR`, `details.reason = "RANGE_TOO_LARGE"`. |
| **D14** | In-progress legs | The newest leg can still grow. A leg is `in_progress` while nothing has closed it yet (no split condition seen after it) **and** `now − end_time < stop_split_minutes`. It's stored with `closed = false` and upserted on its start odometer by later runs. In-progress legs are listed (labelled "in progress") but are never matched, backtested or aggregated. |

---

## 3. Features

Owns lists are in [`plan.md`](plan.md#43-phase-5-history-and-analytics) §4.3 and are not repeated here. Order: F0 → (F1, F2) → F3 → F4 (also needs P4-F4); F1 → F5 → F6; F3 + P4-F7 → F7; F4 + F6 + F7 → F8.

| ID | Feature | Branch | Size |
|---|---|---|---|
| F0 | Design + contracts (this file, ADR 0009) | `feature/phase5-design` | S |
| F1 | Observed trips + predictions schema | `feature/phase5-observed-schema` | S |
| F2 | Trip inference engine (pure) | `feature/phase5-trip-inference` | M |
| F3 | Ingestion: background hook, recompute, confirm/reject API | `feature/phase5-trip-ingestion` | M |
| F4 | Plan ↔ observation matching + backtests | `feature/phase5-prediction-matching` | S–M |
| F5 | Analytics API + CSV | `feature/phase5-analytics-api` | M |
| F6 | Analytics UI (SVG charts, no library) | `feature/phase5-analytics-ui` | L |
| F7 | History with observations | `feature/phase5-history-observations` | M |
| F8 | Close-out | `feature/phase5-closeout` | S |

### F1 — Observed trips + predictions schema

Migration lane. F1 writes `database-design.md` §3.9–3.10 to match this section (P5-F0 doesn't edit db-design; the shapes below replace the current draft there).

**`observed_trips`** (one row per driving leg):

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | `gen_random_uuid()` |
| `user_id` | `uuid` FK → `profiles.id` ON DELETE CASCADE | |
| `vehicle_id` | `uuid` FK → `vehicles.id` ON DELETE CASCADE | |
| `start_time` | `timestamptz` NOT NULL | Earliest possible start: the last at-rest reading before motion. |
| `start_time_latest` | `timestamptz` NOT NULL | First reading showing motion when the start is bounded; equals `start_time` when exact. `CHECK (start_time_latest >= start_time)` |
| `end_time` | `timestamptz` NOT NULL | Last reading with an odometer increase. `CHECK (end_time >= start_time_latest)` |
| `odometer_start_km` | `numeric(10,2)` NOT NULL | |
| `odometer_end_km` | `numeric(10,2)` NOT NULL | `CHECK (odometer_end_km > odometer_start_km)` |
| `distance_km` | `numeric(8,2)` NOT NULL | `odometer_end_km − odometer_start_km`. `CHECK (> 0)` |
| `soc_start_percent` | `numeric(5,2)` NULL | `CHECK (0..100)` |
| `soc_start_recorded_at` | `timestamptz` NULL | |
| `soc_end_percent` | `numeric(5,2)` NULL | `CHECK (0..100)` |
| `soc_end_recorded_at` | `timestamptz` NULL | |
| `usable_capacity_kwh` | `numeric(6,2)` NULL | Used for the energy (D10). |
| `capacity_source` | `text` NULL | `'vehicle'`, `'catalog'` (`CHECK`). |
| `energy_kwh` | `numeric(8,3)` NULL | Observed driving energy. Replaces the draft's `energy_kwh_estimated` (that name read like a prediction). |
| `energy_kwh_uncertainty` | `numeric(6,3)` NULL | D8. |
| `energy_source` | `text` NULL | `'soc_inferred'`, `'manual'` (`CHECK`; `manual` reserved). `NULL` iff `energy_kwh` is `NULL`. |
| `quality_flag` | `text` NOT NULL | `'high'`, `'medium'`, `'low'` (`CHECK`). |
| `quality_reasons` | `jsonb` NOT NULL | Array of D8 codes. Default `[]`. |
| `review_status` | `text` NOT NULL | `'auto'`, `'needs_review'`, `'confirmed'`, `'rejected'` (`CHECK`). |
| `reviewed_at` | `timestamptz` NULL | Set by confirm/reject. |
| `closed` | `boolean` NOT NULL | D14. Default `false`. |
| `planned_trip_id` | `uuid` FK → `planned_trips.id` ON DELETE SET NULL | Set by matching (D4). The leg's position in the chain is **not stored**: the API derives `plan_leg_index` by ordering the plan's legs by `start_time`. So deleting a plan (`SET NULL`) leaves nothing behind that could break a constraint. |
| `inference_version` | `text` NOT NULL | `'telemetry-v1'`. |
| `is_simulated` | `boolean` NOT NULL | Default `false`. |
| `created_at`, `updated_at` | `timestamptz` NOT NULL | Default `now()`. |

Constraints and indexes: `UNIQUE (vehicle_id, odometer_start_km)` (the upsert key: two legs can't start at the same odometer reading); `INDEX (user_id, end_time DESC, id)` (lists, analytics); `INDEX (vehicle_id, end_time DESC)`; `INDEX (planned_trip_id) WHERE planned_trip_id IS NOT NULL`. RLS on.

**`prediction_results`** (replaces the draft in db-design §3.10):

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` FK → `profiles.id` ON DELETE CASCADE | Added: ownership filter without a join. |
| `vehicle_id` | `uuid` FK → `vehicles.id` ON DELETE CASCADE | |
| `kind` | `text` NOT NULL | `'plan'` or `'leg'` (`CHECK`). |
| `planned_trip_id` | `uuid` FK → `planned_trips.id` ON DELETE CASCADE, NULL | Set iff `kind = 'plan'`. |
| `observed_trip_id` | `uuid` FK → `observed_trips.id` ON DELETE CASCADE, NULL | Set iff `kind = 'leg'`. (Plan rows reach their legs through `observed_trips.planned_trip_id`.) |
| `model_version` | `text` NOT NULL | `'deterministic-v1'`. |
| `distance_km` | `numeric(8,2)` NOT NULL | Distance the prediction is for: plan route + detours, or the leg's odometer distance. |
| `observed_distance_km` | `numeric(8,2)` NOT NULL | Leg distance, or the chain's sum. |
| `predicted_energy_kwh` | `numeric(8,3)` NOT NULL | |
| `observed_energy_kwh` | `numeric(8,3)` NULL | `NULL` when any leg lacks energy. |
| `error_kwh` | `numeric(8,3)` NULL | `observed − predicted`. |
| `predicted_arrival_soc` / `observed_arrival_soc` | `numeric(4,3)` NULL | Plan rows only. |
| `observed_start_soc` | `numeric(4,3)` NULL | Plan rows only; shows when the drive started at a different SoC than planned. |
| `arrival_soc_error_points` | `numeric(5,2)` NULL | `(observed − predicted) × 100`. Plan rows only. |
| `inputs` | `jsonb` NOT NULL | `{baseline_kwh_per_km, baseline_source, usable_capacity_kwh}` (leg rows) or `{plan_model_version}` (plan rows). |
| `is_simulated` | `boolean` NOT NULL | Default `false`. |
| `created_at`, `updated_at` | `timestamptz` NOT NULL | |

Constraints and indexes: `CHECK ((kind = 'plan') = (planned_trip_id IS NOT NULL))`, `CHECK ((kind = 'leg') = (observed_trip_id IS NOT NULL))`; `UNIQUE (planned_trip_id, model_version) WHERE kind = 'plan'`; `UNIQUE (observed_trip_id, model_version) WHERE kind = 'leg'`; `INDEX (user_id, kind, created_at DESC)`; `INDEX (model_version, created_at)`. RLS on.

**Tests** (`tests/trips/test_observed_models.py`): RLS on both tables; round-trip and one head; every `CHECK` rejects a bad row; the upsert key; cascades (profile → both, vehicle → both, observed trip → its leg prediction, planned trip → its plan prediction; deleting a matched plan keeps its legs, with `planned_trip_id` set to `NULL`).

**Note for P6-F5 (retention):** unsaved plans are deleted after 30 days (phase4_plan D6). Only delete `saved_at IS NULL AND status = 'planned'`, so matched (`completed`) plans and their prediction rows are kept.

**As built (P5-F1, 2026-11-19):** migration `20261120_01_observed_trips_predictions` (revises `20261119_01`), models in `modules/trips/observed_models.py` (plus value tuples such as `REVIEW_STATUSES` and `FROZEN_REVIEW_STATUSES`), `alembic/env.py` imports them, tests in `tests/trips/test_observed_models.py`, and `database-design.md` §3.9–3.10 rewritten to match. Columns, types, FKs and indexes are as in the tables above. Differences, approved with the F1 plan:
- **Plain unique constraints instead of partial unique indexes** on `prediction_results`: `UNIQUE (planned_trip_id, model_version)` and `UNIQUE (observed_trip_id, model_version)`. With the kind ↔ FK checks they are equivalent to `WHERE kind = 'plan' / 'leg'` (`NULL`s never collide), F4 can upsert with `ON CONFLICT ON CONSTRAINT prediction_results_leg_model_uq` and no index predicate, and the FK cascades use them.
- **Extra checks:** `distance_km = odometer_end_km − odometer_start_km`; `capacity_source` is `NULL` iff `usable_capacity_kwh` is `NULL`; plan-row SoC columns (`predicted_arrival_soc`, `observed_arrival_soc`, `observed_start_soc`) are 0..1. `energy_source` `NULL` iff `energy_kwh` `NULL` is a `CHECK`.
- **Extra index** `prediction_results_vehicle_idx (vehicle_id)` for the vehicle cascade (same pattern as `planned_trips_vehicle_idx`).
- **No `energy_kwh >= 0` check**, as F2's notes describe: a SoC rise within tolerance can give a slightly negative value on a `low` leg.
- **`updated_at`** has a `now()` default and ORM `onupdate`; there's no trigger (the repo has none). A Core upsert (`insert … on_conflict_do_update`) must set `updated_at = now()` itself, as the upsert test does.
- **Tests (50):** RLS on both tables; no model/migration drift; one head (`20261120_01`, revises `20261119_01`) plus a downgrade/upgrade round trip inside the test transaction; every `CHECK` and required column rejects a bad row; the upsert key, including an `ON CONFLICT` upsert that extends an open leg; one prediction per leg/plan per model version; cascades (profile → both, vehicle → both, leg → its leg row only, plan → its plan row with its legs kept and unlinked).
- **Not changed (outside F1's files):** the ER diagram in `database-design.md` §2 still shows the old draft columns, and §9 questions 2–3 (`path` column, weather) are answered by D7 and the "Not in Phase 5" list but not marked answered.

### F2 — Trip inference engine

`modules/trips/inference.py`: pure functions, no DB, no HTTP. Signatures in §4.1, algorithm in §4.1, worked examples in §5 (the F2 tests assert them). Settings live in a frozen dataclass with these defaults; F3 builds it from the config block "trips":

| Setting | Default | Note |
|---|---|---|
| `trips_stop_split_minutes` | 20 | D1, D14 |
| `trips_min_move_km` | 0.1 | Odometer change below this counts as stationary (noise). |
| `trips_min_leg_km` | 0.5 | Shorter legs (parking-lot moves) are dropped. |
| `trips_high_min_soc_delta` | 10 | D8 |
| `trips_medium_min_soc_delta` | 4 | D8 |
| `trips_high_max_soc_age_minutes` | 15 | D8 |
| `trips_medium_max_soc_age_minutes` | 360 | D8 |
| `trips_soc_resolution_points` | 1 | D8 |
| `trips_soc_increase_tolerance_points` | 1 | A rise of more than this inside a leg → `soc_increased`. |

**Fixtures:** `fixtures/telemetry/*.json`, one file per §5 example, each a list of `{recorded_at, signal, value, is_simulated}` plus the expected legs.

**Tests:** the five §5 examples exactly (times, km to 0.01, kWh to 0.001, flags, reasons); readings in shuffled order give the same result; exact duplicates are collapsed; `None` values are dropped; an odometer regression is dropped and flagged; a charging session always splits; simulated rows dropped and counted; a leg under `min_leg_km` dropped; an open leg reported with `closed = False` when `now − end < split`; an empty input gives no legs; property-style checks over generated monotonic series (no new dependency): legs never overlap, the sum of leg distances ≤ the total odometer increase, and every leg's energy is `null` or computed from its own SoC readings.

**As built (P5-F2, 2026-11-18):** `modules/trips/inference.py` has the §4.1 types and `infer_legs` exactly as written. It adds one helper, `is_in_progress(leg, *, now, settings)`, which holds the D14 rule so F3, F5 and F7 share it. The fixtures are in `fixtures/telemetry/` (with a README), and the tests are in `tests/trips/test_inference.py`. All five §5 examples match exactly: every leg field, the charging sessions and the counters. Each example is also re-run in 5 shuffled orders, and 200 generated series are checked for the invariants listed above. Choices §4.1 left open, approved with the F2 plan:
- **Counters count `Reading`s** (one per signal, like `vehicle_telemetry` rows). To keep §5's numbers, the example 4 redelivery and the example 5 simulated rows carry only the odometer in the fixtures. With every signal on those rows, the counts would be 2 and 6, and the legs would be the same.
- **`closed` depends only on the readings** (example 2's leg B stays `closed = false` however old it is). `now` must be timezone-aware but doesn't change the output; "in progress" (D14) is `is_in_progress`.
- **`odometer_regression` caps a leg at `medium`**: a leg with a dropped regression is never `high`. D8 left this open; example 4 is unaffected.
- **A moving pair with charging inside it** becomes its own leg with a bounded start (`soc_increased`, energy `null`). The next moving pair starts a fresh leg at that pair's later reading, so the driving after the charge keeps its energy.
- **A charging session with no "not charging" reading ends at the first moving odometer pair** that starts at or after it. Its `end` is that pair's later reading, an upper bound, so only that pair is contaminated. Without this, one lost event would null the energy of every later leg. `end = None` now means "still charging, the car hasn't moved since".
- **SoC endpoints are bounded (added after PR #39 review; §4.1 step 7 doesn't define this).** A leg's start SoC can't be earlier than the previous leg's `end_time` or the end of the last charging session before the leg. Its end SoC can't be later than the next leg's `start_time` or the start of the next charging session. A reading outside these bounds counts as missing, so the leg gets `soc_missing` and `energy_kwh = null`, which is the existing step 7 rule. Without this, two legs with no SoC reading between them both took the same pair of readings and double-counted the energy. A SoC reading taken after a charge could also become the end of a leg driven before it. Short legs dropped by `min_leg_km` still set bounds, because a parking move is still driving. Two neighbouring legs may share one boundary reading (§5 examples 2 and 4), so their SoC windows touch but never overlap. The five §5 examples are unchanged.
- **Two rules about which SoC readings are used:** the end-SoC fallback must be later than `start_time_latest`, so it never reuses the start reading and reports ΔSoC 0. The end age is measured either side of `end_time`.
- **`soc_delta_small` and `soc_reading_stale` are listed whenever both SoC readings exist**, even when energy is `null` for another reason (as example 5 shows).
- **Charging overlap uses open intervals**: a session that starts exactly at a leg's `end_time`, or ends exactly at its `start_time`, doesn't contaminate it.
- **Unusable values are dropped silently**: `None`, NaN and infinity. They aren't counted, and `list_readings` already removes errored rows.
- Two different values with the same timestamp and signal are both kept (they are not duplicates). For SoC, the last one in `(recorded_at, signal, value)` order is used.
- **Not handled:** a SoC rise within the 1-point tolerance can make a leg's energy slightly negative (e.g. −0.710 kWh). That leg has ΔSoC < 4, so it is `low`/`needs_review` and stays out of aggregates (D12). F1's schema has no `energy_kwh ≥ 0` check, so such a value would be stored as is.

### F3 — Ingestion: background hook, recompute, confirm/reject

Contract: [`api-contracts.md`](api-contracts.md) §3.18–3.18d.

**Hook registry (ADR 0009)**, `modules/vehicles/ingest_hooks.py` (new):

```python
@dataclass(frozen=True)
class TelemetryIngested:
    vehicle_id: uuid.UUID
    user_id: uuid.UUID
    signal_codes: frozenset[str]      # codes actually inserted by this delivery
    earliest_recorded_at: datetime    # oldest provider timestamp among those rows (late deliveries)
    is_simulated: bool

IngestHook = Callable[[TelemetryIngested], None]

def register_ingest_hook(hook: IngestHook) -> None: ...   # idempotent per function
def registered_ingest_hooks() -> tuple[IngestHook, ...]: ...
```

- `telemetry_service.IngestResult` gains `vehicle_id`, `user_id`, `is_simulated` and `inserted_codes`.
- `webhook_router.receive_smartcar_webhook` takes FastAPI's `BackgroundTasks` and, when a delivery was accepted and inserted rows, schedules each hook with the `TelemetryIngested` event. Hooks run after the response. A hook exception is logged (no coordinates, no Smartcar IDs) and never reaches Smartcar.
- `trips/observed_router.py` registers `observed_service.on_telemetry_ingested` when it's imported (W0 imports it in `main.py`). A test checks it's registered exactly once.

**Service** (`modules/trips/observed_service.py`):
- `on_telemetry_ingested(event)`: returns at once unless `signal_codes` includes `odometer`, `soc` or `charging_status`. Otherwise it opens its own session (the request session is closed by then), takes `pg_advisory_xact_lock` on the vehicle (waits, so a run never misses rows committed just before it), calls `infer_for_vehicle(..., since=event.earliest_recorded_at)` and then the F4 call site `matching.match_and_score_vehicle(...)`, and commits.
- `infer_for_vehicle(db, *, vehicle_id, user_id, since=None, full=False, now=None) -> InferRunSummary`: picks the **window start**, then rebuilds every non-frozen leg from there on.
  - Normally the window starts at the **anchor**: the `end_time` of the newest leg with `closed = true`.
  - **Late telemetry:** deliveries can arrive out of order, so when `since` (the oldest reading this delivery inserted) is earlier than the anchor, the window moves back to the `start_time` of the earliest non-frozen leg with `end_time ≥ since`, or to `since` itself if no leg reaches it. That leg and everything after it are rebuilt, so a delayed charging event inside an already closed leg still nulls its energy. Legs that ended before `since` are untouched.
  - No legs yet, or `full=True` → all history.
  - It reads readings from the window start to now (plus the last odometer reading before the window start, so the first pair is complete), plus the latest `soc` and `charging_status` reading before that, through the new read-only `vehicles.telemetry_service.list_readings(...)`. That function converts units to km/percent the same way `vehicle_state.py` does, and drops errored rows. It resolves capacity (D10) and calls `inference.infer_legs(...)`. Then it upserts each leg on `(vehicle_id, odometer_start_km)`, skipping legs whose odometer range overlaps a frozen leg (D3), and deletes non-frozen legs that start at or after the window start and weren't produced this time. A leg whose energy or distance changed, or that was deleted, has its backtest recomputed or removed and its plan re-scored or un-matched (F4). Re-running with no new telemetry changes nothing (`updated_at` is touched only when a value changes).
- `confirm(db, user_id, trip_id)`, `reject(...)`: ownership 404, set `review_status` and `reviewed_at`, idempotent. Reject also clears the leg's `planned_trip_id` and asks F4 to re-score that plan.
- `scripts/infer_trips.py --vehicle-id <uuid> | --all [--full]`: the same function, for catch-up. Prints counts only.

**Endpoints:** `GET /observed-trips`, `GET /observed-trips/{id}`, `POST /observed-trips/recompute`, `POST /observed-trips/{id}/confirm`, `POST /observed-trips/{id}/reject`. They live under `/observed-trips`, not `/trips/observed`: W0 registers `trips.router` (with `GET /trips/{id}`) before `trips.observed_router`, so `/trips/observed` would match `/trips/{id}` first and fail UUID validation.

**Tests:** a signed webhook fixture that inserts an odometer reading triggers inference (the test client runs background tasks) and creates a leg; a delivery with only `range` doesn't; a duplicate delivery doesn't re-run; a hook exception still returns 200 to Smartcar; idempotent re-runs; the anchor window (legs before the anchor untouched); a late delivery with a charging event inside an already closed leg sets that leg's energy to `null` (`soc_increased`) and removes its backtest, without a `full` run; a frozen leg survives a `full` re-run; ownership 404s on every route; confirm/reject idempotent; recompute for another user's vehicle → 404; simulated vehicle → simulated legs.

**As built (P5-F3, 2026-11-19):** `vehicles/ingest_hooks.py`, the `BackgroundTasks` scheduling in `vehicles/webhook_router.py`, `IngestResult` fields and `list_readings` in `vehicles/telemetry_service.py`, `trips/observed_service.py`, `trips/observed_router.py`, `scripts/infer_trips.py`, the nine `trips_*` settings in the config block "trips" (defaults as in the F2 table), and tests in `tests/test_ingest_hooks.py`, `tests/trips/test_observed_service.py` and `tests/trips/test_observed_api.py`. The routes and response shapes are as in contract §3.18–3.18d (no contract change). Differences, approved with the F3 plan:
- **Hooks get a session opener.** `IngestHook` is `hook(event, open_session)`, not `hook(event)`. The webhook router resolves `open_session` the same way requests get their session (`get_db`, or the app's dependency override) and passes it in. This is how ADR 0009's "the hook opens its own DB session" is done: the hook still opens and commits its own session after the response, and `vehicles` still imports nothing from `trips`. The opener is passed in rather than taken from the global engine, so a hook always uses the request's database. In tests that's the rolled-back test connection, so the webhook → hook → leg path is tested end to end and never reaches the `DATABASE_URL` in `.env`. `run_ingest_hooks` logs a failing hook's name and error type only (no message, no traceback).
- **`IngestResult`** also carries `earliest_recorded_at`, taken from the insert's `RETURNING`, for `TelemetryIngested.earliest_recorded_at`.
- **`list_readings`** returns `vehicles`' own `TelemetryReading` dataclass (`code` is `odometer` / `soc` / `charging_status`, values in km / percent / 1.0–0.0). `trips` maps them to `inference.Reading`. Odometer units use the same km/mile table as `vehicle_state.py`; an unknown unit, a SoC unit other than `percent`, or a `NULL` value drops the row. With `since`, it adds the latest usable reading of each of the three codes before `since`.
- **Window details** (§3 F3 left these open):
  - If there's no closed leg yet (none, or only open ones), the run rebuilds the whole history, so an open leg can always grow from its own start.
  - Legs that the engine produces but that **start before the window** are never written. The engine sees only their tail (from the last odometer reading before the window), so writing them would create a partial copy of a leg outside the window.
  - A produced leg whose odometer range overlaps a confirmed or rejected leg is skipped (`legs_skipped_frozen` in the run summary; not in the API response).
- **Writes** go through the ORM, keyed on `(vehicle_id, odometer_start_km)`, under the advisory lock. A row is written only when a column value changed, so `updated_at` moves only then. Values are rounded to the column scales first, and `distance_km` is computed as `odometer_end_km − odometer_start_km` after rounding, so the F1 equality check always holds. `review_status` is recomputed from `quality_flag` on every write: `auto` for high/medium, `needs_review` for low.
- **Backtests:** when an existing leg's `energy_kwh` or `distance_km` changes, its `kind = 'leg'` rows are deleted and F4 writes fresh ones. A deleted leg loses its rows through the FK cascade.
- **F4 call site:** `observed_service._match_and_score_vehicle(db, *, vehicle_id, user_id, now) -> int` is the one placeholder. It returns `0`, so `plans_matched` is always `0` until F4 replaces its body with `matching.match_and_score_vehicle(...)`. It is called after every inference run and after a reject that removed a plan link. Until F4 lands, two things are left to it: a plan whose leg was rejected stays `completed`, and a matched leg whose numbers changed keeps its `planned_trip_id`. F4 re-scores or un-matches in both cases.
- **Review:** confirm and reject take the same per-vehicle advisory lock as inference, so a run can't overwrite a leg mid-review. `reviewed_at` changes only when the status changes, so repeating confirm or reject keeps the first time.
- **Reads:** `in_progress` is computed at read time with D14's rule, using `trips_stop_split_minutes`. `plan_leg_index` is a correlated count over the plan's legs ordered by `(start_time, id)`. The cursor is unsigned base64 JSON of `(end_time, id)`, the same approach as `trips.service`. The order is `end_time` descending, ties by `id` ascending.
- **Advisory lock:** `pg_advisory_xact_lock(5003, hashtext(vehicle_id))`. The first key keeps these locks apart from any other feature's.
- **Capacity (D10):** the specs row first, then the catalog. A row value equal to the catalog's is reported as `catalog`, the same rule as the planner's `resolve_specs`. It's read inside `trips` from `VehicleSpecs` and `spec_catalog.lookup_spec`, because `routing` already imports `trips`, so importing `routing` from `trips` would create a cycle.
- **Not changed (outside F3's files):** `services/api/.env.example` has no `TRIPS_*` lines (the defaults apply). Contract §6 question 3 is still open there.

### F4 — Plan ↔ observation matching + backtests

`modules/trips/matching.py`: a pure core (§4.2) plus `match_and_score_vehicle(db, *, vehicle_id, user_id, now)`, called from the F3 hook (one call site) and after confirm/reject.

- **Candidates:** feasible plans of the vehicle with `status = 'planned'` created in the last 7 days; legs that aren't in progress (D14), aren't rejected and have no `planned_trip_id`, ending in the same period.
- **Backtests (D9):** for each leg that isn't in progress, has energy and no `kind = 'leg'` row for `deterministic-v1`, write one. Baseline and capacity use the D14 rule through `vehicles` service functions; if P4-F4 has added a shared resolver to `vehicles`, use it, otherwise read the specs row and `spec_catalog.lookup_spec` the same way. A vehicle with no baseline gets no backtest rows (counted in the run summary). Recompute a leg's row when its energy or distance changes.
- **Plan scoring:** predicted energy = the plan's `totals.energy_kwh_estimated`; predicted arrival = the plan's `arrival_soc`; observed energy = the sum of the chain's leg energies (null if any is null); observed arrival = the last leg's `soc_end_percent / 100`; observed start = the first leg's `soc_start_percent / 100`.
- **Trip detail:** `GET /trips/{id}` (contract §3.9) fills `observation` with the §3.18e object when the plan has a match, else `null`. F4 edits `trips/service.py` and `trips/router.py` for this (P4-F1 is merged by then).

**Tests:** §5 example 3 (match, chain of two legs, scores to 0.01); no match outside the time window; no match outside the distance tolerance; two plans for one drive → the one whose `departure_time` fits, else the saved one, else the latest created; one leg can't be in two chains; the chain stops at a gap over 120 min; rejecting a matched leg un-matches and re-scores; an infeasible plan never matches; backtests for every finished leg with energy, none for in-progress legs.

### F5 — Analytics API + CSV

Contract: [`api-contracts.md`](api-contracts.md) §3.11–3.11b. `modules/analytics/` reads through `modules/trips/observed_queries.py` (new, owned by F5, read-only functions returning dataclasses), so analytics never queries another module's tables directly. Aggregation runs in Python over the legs in range (a year of daily driving is a few thousand rows).

Config block "analytics": `analytics_max_buckets = 366`, `analytics_export_max_rows = 10000`.

**Definitions** (for each bucket, over legs with `included_in_aggregates`):
- `trips` = count; `distance_km` = Σ distance.
- `energy_trips` = count with energy and a backtest; `observed_energy_kwh` and `estimated_energy_kwh` = Σ over those same legs, so the two are comparable.
- `observed_kwh_per_km` = Σ observed / Σ their distance (distance-weighted); `estimated_kwh_per_km` likewise.
- `mae_kwh` = mean |error_kwh|; `bias_kwh` = mean error_kwh (negative = the model over-predicts).
- Ratios are `null` when `energy_trips = 0`.

**Tests:** day/week/month in `UTC` and `America/New_York` (a leg ending 2026-11-02 03:30Z lands on 2026-11-01 in New York); ISO week labels across a year boundary; empty buckets; the bucket cap; `include_simulated` default false plus the `excluded_simulated_trips` count; needs_review/rejected/in-progress excluded from aggregates; CSV rows = JSON rows (same filters, all pages); CSV injection escaping on `origin_label`; the export cap; ownership (another user's `vehicle_id` → 404); MAE on §5 example 3 = 0.485 kWh.

### F6 — Analytics UI

Replaces the placeholder `app/(app)/analytics/page.tsx`. No chart library (D5): `components/analytics/charts/` has `bar-chart.tsx`, `line-chart.tsx` and `scatter-chart.tsx`, plain SVG with linear scales, "nice" axis ticks, a `<title>` per mark, `role="img"` and an `aria-label`, plus a data table under each chart (visually collapsible, always in the DOM). They follow the theme tokens in light and dark.

- Vehicle picker (primary vehicle by default), date range and `group_by` controls, `tz` from the browser.
- Charts: distance per bucket (bars); observed vs estimated kWh/km per bucket (two lines); per-trip estimated vs observed kWh (scatter with a y = x line, low-quality points hollow).
- Consumption in the user's unit (Wh/mi for imperial, kWh/100 km for metric); the API stays in kWh/km.
- Trips table from `GET /analytics/trips` with quality badges, reasons on hover/focus, "needs review" links to the confirm flow (F7's component if merged, else a link to the trip), and "Download CSV".
- Simulated: when `excluded_simulated_trips > 0`, a notice says so; when `include_simulated` is on (demo), a persistent "Includes simulated data" banner.
- Empty state: "No trips yet. Trips appear here automatically after you drive."

**Tests:** Vitest for scales/ticks and unit conversion; components render the table data; Playwright: open Analytics on seeded legs, switch to weekly, download the CSV and compare its rows with the table.

### F7 — History with observations

- Trip detail (`trips/[id]`): when `observation` is present, a "Planned vs actual" panel: each planned leg next to the observed leg (distance, SoC used, arrival SoC), total energy predicted vs observed with the error, and quality badges.
- Saved trips list: a "Completed" badge for matched plans.
- An "Observed drives" list (`GET /observed-trips`) with a "Needs review" filter and Confirm/Reject buttons (`components/trips/observation-*`). Bounded start times are shown as "between 18:12 and 08:41", never as a fake exact time.

**Tests:** Vitest for the panel (matched, partially missing energy, no match), the bounded-time format, confirm/reject states and errors.

### F8 — Close-out

Real-car check: a few real drives (and one charging session) produce legs with no manual step, after P2-R2's cadence is recorded. Record the observed cadence and the resulting quality mix (how many high/medium/low) in the roadmap risk table; if most legs are low because of the cadence, open a follow-up to tune D8 or the split. Fill in the §6 mapping table and update the roadmap status. Playwright analytics flow (F6) green.

---

## 4. Interfaces

Python 3.12, frozen dataclasses, no Pydantic in the pure modules. Distances in km, SoC in **percent** inside inference (that's what the vehicle reports) and as fractions in prediction rows and the API (like Phase 4), energy in kWh, times as aware UTC `datetime`.

### 4.1 `app/modules/trips/inference.py` (F2)

```python
from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

SignalKind = Literal["odometer_km", "soc_percent", "is_charging"]
QualityFlag = Literal["high", "medium", "low"]
QualityReason = Literal[
    "soc_missing", "soc_increased", "capacity_unknown", "soc_delta_small",
    "soc_reading_stale", "start_time_bounded", "odometer_regression",
]

@dataclass(frozen=True)
class Reading:
    recorded_at: datetime            # provider timestamp (vehicle_telemetry.recorded_at)
    signal: SignalKind
    value: float                     # km, percent, or 1.0/0.0 for is_charging
    is_simulated: bool

@dataclass(frozen=True)
class InferenceSettings:
    stop_split_minutes: float = 20
    min_move_km: float = 0.1
    min_leg_km: float = 0.5
    high_min_soc_delta: float = 10
    medium_min_soc_delta: float = 4
    high_max_soc_age_minutes: float = 15
    medium_max_soc_age_minutes: float = 360
    soc_resolution_points: float = 1
    soc_increase_tolerance_points: float = 1

@dataclass(frozen=True)
class InferredLeg:
    start_time: datetime
    start_time_latest: datetime
    end_time: datetime
    odometer_start_km: float
    odometer_end_km: float
    distance_km: float
    soc_start_percent: float | None
    soc_start_recorded_at: datetime | None
    soc_end_percent: float | None
    soc_end_recorded_at: datetime | None
    energy_kwh: float | None
    energy_kwh_uncertainty: float | None
    quality_flag: QualityFlag
    quality_reasons: tuple[QualityReason, ...]   # in the order of the Literal above
    closed: bool

@dataclass(frozen=True)
class ChargingSession:               # returned for tests and F4; not stored in v1
    start: datetime
    end: datetime | None             # None while still charging
    soc_start_percent: float | None
    soc_end_percent: float | None

@dataclass(frozen=True)
class InferenceResult:
    legs: tuple[InferredLeg, ...]                # by start_time
    charging_sessions: tuple[ChargingSession, ...]
    dropped_simulated: int
    dropped_duplicates: int
    dropped_odometer_regressions: int
    dropped_short_legs: int

def infer_legs(
    readings: Iterable[Reading],
    *,
    vehicle_is_simulated: bool,
    usable_capacity_kwh: float | None,
    now: datetime,
    settings: InferenceSettings = InferenceSettings(),
) -> InferenceResult: ...
```

**Algorithm:**
1. **Clean.** Drop readings with `is_simulated != vehicle_is_simulated` (count). Sort by `(recorded_at, signal)`. Collapse exact duplicates `(recorded_at, signal, value)` (count). For the odometer, drop any reading lower than the running maximum by more than `min_move_km` (count; the leg that spans it gets `odometer_regression`).
2. **Charging sessions.** Walk the `is_charging` readings: a 1 after a 0 (or first) starts a session; the next 0 ends it. An unmatched 1 is an open session.
3. **Odometer pairs.** For consecutive odometer readings `p → q`: `Δkm = q − p`, `Δt = q.t − p.t`. The pair is *moving* if `Δkm ≥ min_move_km`, else *stationary*.
4. **Legs.** Walk the pairs in order, holding the current leg:
   - A moving pair with `Δt ≤ split` extends the current leg, or starts one at `p` with an **exact** start (`start_time = start_time_latest = p.t`).
   - A moving pair with `Δt > split` closes the current leg at `p` and starts a new one with a **bounded** start: `start_time = p.t`, `start_time_latest = q.t`. (We can't tell whether the car stopped during the gap.)
   - A run of stationary pairs lasting `≥ split` in total closes the current leg at the run's first reading. The next leg can start at the run's last reading.
   - A charging session that starts after the current leg's last reading closes the leg at that reading; the next moving pair starts a new leg.
   - A moving pair `p → q` with a charging session strictly inside it (no odometer reading at the session's start) can't be split: we don't know how much was driven before and after the charge. It starts its own leg with a bounded start (`start_time = p.t`, `start_time_latest = q.t`); the SoC rise inside it makes step 7 set `soc_increased`, so its energy is `null`.
   - `end_time` = the time of the leg's last moving reading.
5. **Open leg.** The last leg is `closed = False` if no closing condition followed it; it's still emitted (D14).
6. **Short legs.** Drop legs with `distance_km < min_leg_km` (count).
7. **SoC and energy** for each leg:
   - `soc_start` = the latest SoC reading with `recorded_at ≤ start_time`. `soc_end` = the earliest with `recorded_at ≥ end_time`, or, if there is none yet, the latest one at or before `end_time` that is later than every SoC reading before `start_time_latest`.
   - If either is missing → `soc_missing`. If, walking the SoC readings in `[start_time, end_time]` in time order from `soc_start`, any reading is more than `tolerance` above the **lowest reading before it** (not only above `soc_start`: 80 → 20 → 60 → 50 is caught at 60), **or** a charging session (step 2) overlaps `[start_time, end_time]` → `soc_increased`. If capacity is `None` → `capacity_unknown`. Any of these → `energy_kwh = None`.
   - Otherwise `energy_kwh = (soc_start − soc_end) / 100 × capacity`, rounded to 3 decimals, and `energy_kwh_uncertainty = capacity × soc_resolution_points / 100`.
   - Quality and reasons by D8.

### 4.2 `app/modules/trips/matching.py` (F4)

```python
@dataclass(frozen=True)
class PlanCandidate:
    trip_id: uuid.UUID
    created_at: datetime
    departure_time: datetime | None
    saved: bool
    planned_distance_km: float        # route.distance_km + Σ 2 × distance_from_route_km × road_factor
    predicted_energy_kwh: float       # totals.energy_kwh_estimated
    predicted_arrival_soc: float      # fraction
    starting_soc: float               # fraction

@dataclass(frozen=True)
class LegCandidate:
    leg_id: uuid.UUID
    start_time: datetime
    start_time_latest: datetime
    end_time: datetime
    distance_km: float
    energy_kwh: float | None
    soc_start_percent: float | None
    soc_end_percent: float | None

@dataclass(frozen=True)
class MatchSettings:
    window_before_created_minutes: float = 15
    window_after_created_hours: float = 24
    departure_tolerance_minutes: float = 120
    max_gap_minutes: float = 120
    distance_tolerance_fraction: float = 0.10
    distance_tolerance_min_km: float = 5

@dataclass(frozen=True)
class PlanMatch:
    trip_id: uuid.UUID
    leg_ids: tuple[uuid.UUID, ...]    # in drive order; the API's plan_leg_index is this order (not stored)
    observed_distance_km: float
    distance_error_km: float          # observed − planned

@dataclass(frozen=True)
class PlanScore:
    observed_energy_kwh: float | None
    error_kwh: float | None
    observed_start_soc: float | None
    observed_arrival_soc: float | None
    arrival_soc_error_points: float | None

def match_plans(
    plans: Sequence[PlanCandidate], legs: Sequence[LegCandidate], settings: MatchSettings = MatchSettings()
) -> tuple[PlanMatch, ...]: ...

def score_plan(plan: PlanCandidate, chain: Sequence[LegCandidate]) -> PlanScore: ...

def backtest_leg(vehicle: EnergyVehicle, distance_km: float) -> float:
    """Predicted kWh = energy.model.segment_energy_kwh(vehicle, distance_km)."""
```

**`match_plans` algorithm:**
1. Sort legs by `start_time`. For each plan, its **window** is `departure_time ± departure_tolerance` if set, else `[created_at − before, created_at + after]`. A leg can **start a chain** if `[start_time, start_time_latest]` overlaps the window.
2. For each (plan, starting leg): extend with the following legs while the gap `next.start_time − prev.end_time ≤ max_gap` and the sum ≤ `planned + tolerance`. Keep the prefix whose distance is closest to `planned_distance_km`. It's a candidate if `|observed − planned| ≤ max(min_km, fraction × planned)`.
3. Sort candidates by: `|distance_error|` / planned, then plan preference (departure-time window used > saved > later `created_at`), then earlier first leg, then `trip_id`. Take them greedily, skipping any whose plan or legs are already taken.

---

## 5. Worked examples

All use the bZ (§2.3 of phase4_plan): usable capacity **71.0 kWh**, baseline **0.20 kWh/km**, so 1 SoC point = **0.71 kWh** and the uncertainty is 0.71 kWh. Default settings (§4.1). Times are UTC on 2026-11-09 (Monday) unless stated. Each becomes a fixture in `fixtures/telemetry/`. "odo" is the odometer in km, "soc" in percent, "chg" is `is_charging`.

### Example 1 — Dense feed, one commute leg (high)

| # | Time | odo | soc | chg | Pair from previous |
|---|---|---|---|---|---|
| 1 | Sun 18:12 | 24650.30 | 80 | 0 | |
| 2 | 07:58 | 24650.30 | 80 | | stationary 13 h 46 min → split (no leg open) |
| 3 | 08:03 | 24655.10 | 79 | | moving, 5 min |
| 4 | 08:10 | 24664.80 | 77 | | moving, 7 min |
| 5 | 08:20 | 24679.90 | 74 | | moving, 10 min |
| 6 | 08:31 | 24696.00 | 70 | | moving, 11 min |
| 7 | 08:40 | 24708.50 | 67 | | moving, 9 min |
| 8 | 08:44 | 24712.30 | 66 | | moving, 4 min |
| 9 | 09:30 | 24712.30 | 66 | | stationary 46 min → closes the leg at #8 |

| Field | Calculation | Result |
|---|---|---|
| Start | first moving pair #2→#3 is 5 min ≤ 20 → exact | `start_time = start_time_latest = 07:58` |
| End | last moving reading | `08:44`, `closed = true` |
| Distance | 24712.30 − 24650.30 | **62.00 km** |
| SoC | start #2 (80, age 0), end #8 (66, age 0) | ΔSoC **14** |
| Energy | 14 / 100 × 71.0 | **9.940 kWh** ± 0.710 |
| Quality | ΔSoC ≥ 10, ages 0, exact | **high**, reasons `[]`, `review_status = auto` |
| Observed rate | 9.940 / 62.00 | 0.160 kWh/km |
| Backtest (D9) | 62.00 × 0.20 = 12.400; error 9.940 − 12.400 | **−2.460 kWh** (the highway baseline over-predicts a mild commute) |

### Example 2 — Key-off-only feed (low)

The car reports only when parked. Times: Monday evening, then Tuesday 2026-11-10.

| # | Time | odo | soc | Pair |
|---|---|---|---|---|
| 1 | Mon 18:12 | 24712.30 | 66 | |
| 2 | Tue 08:41 | 24740.90 | 61 | moving, 14 h 29 min > 20 → new leg A, bounded |
| 3 | Tue 17:55 | 24769.60 | 56 | moving, 9 h 14 min > 20 → closes A at #2, new leg B, bounded |

| Leg | Start bound | End | km | ΔSoC | Energy | Start age | Quality |
|---|---|---|---|---|---|---|---|
| A | Mon 18:12 … Tue 08:41 | 08:41 | **28.60** | 5 | **3.550** | 14 h 29 min | **low**: `soc_delta_small`, `soc_reading_stale`, `start_time_bounded` → `needs_review` |
| B | Tue 08:41 … 17:55 | 17:55 | **28.70** | 5 | **3.550** | 9 h 14 min | **low**, same reasons; `closed = false` until another reading arrives (it is not `in_progress`: 17:55 is more than 20 min ago at the next run) |

Leg A's energy includes any overnight drain; with a SoC age of 14 h it can't be called a measurement of the drive, which is exactly what `low` says. If P2-R2 finds the bZ only reports at key-off, most legs will look like this, and F8 records it (§3 F8).

### Example 3 — Road trip with one DC stop, matched to a plan

Plan: phase4_plan §5 trip 2 (400 km, stop B at 220 km along / 1.0 km off route, start 0.80). Created 2026-11-07 21:10, `departure_time` 2026-11-08 08:00, saved. Planned distance = 400 + 2 × 1.0 × 1.3 = **402.6 km**; predicted energy **80.52 kWh** (402.6 × 0.20); predicted arrival **0.153** (0.15296); predicted arrival at B 0.173, departure 0.66.

Readings on Sunday 2026-11-08 (odometer every 15 min while driving; only the boundary rows are shown, and F2's fixture has all of them):

| # | Time | odo | soc | chg | Note |
|---|---|---|---|---|---|
| 1 | 08:02 | 30000.00 | 80 | 0 | at rest; the previous reading is the night before (stationary) |
| 2 | 08:17 | 30022.10 | 77 | | moving, 15 min → leg 1, exact start 08:02 |
| … | every 15 min | rising | falling | | |
| 11 | 10:32 | 30222.40 | 18 | | last moving reading of leg 1 |
| 12 | 10:33 | | 18 | 1 | charging starts → closes leg 1 at #11 |
| 13 | 10:51 | | 66 | 0 | charging ends (session 10:33–10:51, 18 → 66) |
| 14 | 10:53 | 30222.40 | 66 | | at rest after charging |
| 15 | 10:55 | 30223.10 | 66 | | moving, 2 min → leg 2, exact start 10:53 |
| … | every 15 min | | | | |
| 24 | 13:00 | 30406.00 | 15 | | last moving reading |
| 25 | 13:40 | 30406.00 | 15 | | stationary 40 min → closes leg 2 |

| Leg | Start → end | km | SoC | Energy | Quality | Backtest | Error |
|---|---|---|---|---|---|---|---|
| 1 | 08:02 → 10:32 | **222.40** | 80 → 18 (62) | **44.020** | high | 222.40 × 0.20 = 44.480 | **−0.460** |
| 2 | 10:53 → 13:00 | **183.60** | 66 → 15 (51) | **36.210** | high | 183.60 × 0.20 = 36.720 | **−0.510** |

The 48-point SoC rise during the charging session is never counted as driving energy (X2).

**Matching (D4):**
- Window: `departure_time` 08:00 ± 2 h; leg 1 starts 08:02 ✓.
- Chain: leg 1 + leg 2 (gap 10:32 → 10:53 = 21 min ≤ 120) = **406.00 km**. Error 406.00 − 402.6 = **+3.40 km**, within max(5, 40.26) ✓. Leg 1 alone (222.40 km) is outside the tolerance.
- Result: legs get `planned_trip_id` = the plan (the API derives `plan_leg_index` 1 and 2 from their start times); the plan becomes `completed`.

**Plan score (`kind = 'plan'` row):**

| Field | Calculation | Value |
|---|---|---|
| `observed_energy_kwh` | 44.020 + 36.210 | **80.230** |
| `error_kwh` | 80.230 − 80.520 | **−0.290** |
| `observed_start_soc` | 80 / 100 | 0.800 (same as planned) |
| `observed_arrival_soc` | 15 / 100 | 0.150 |
| `arrival_soc_error_points` | (0.150 − 0.15296) × 100 | **−0.30** |
| Per leg (F7 panel) | planned arrival at B 0.173 vs observed 0.18; planned departure 0.66 vs observed 0.66 | +0.7 pts; 0 |

**Analytics for that day** (`group_by=day`, `tz=UTC`, bucket `2026-11-08`):

| Field | Calculation | Value |
|---|---|---|
| `trips` / `energy_trips` | | 2 / 2 |
| `distance_km` | 222.40 + 183.60 | 406.00 |
| `observed_energy_kwh` | | 80.230 |
| `estimated_energy_kwh` | 44.480 + 36.720 | 81.200 |
| `observed_kwh_per_km` | 80.230 / 406.00 | 0.198 |
| `estimated_kwh_per_km` | 81.200 / 406.00 | 0.200 |
| `mae_kwh` | (0.460 + 0.510) / 2 | **0.485** |
| `bias_kwh` | (−0.460 − 0.510) / 2 | −0.485 |

### Example 4 — Gaps, out-of-order and bad readings

Received in this order: #3, #1, #2, #5, #4, #6, #7, #2 again (a redelivery with a new event ID), then #8. One SoC reading errored (stored with `value = NULL`, so `list_readings` drops it before the engine sees it).

| # | Time | odo | soc | Pair (after sorting) |
|---|---|---|---|---|
| 0 | 11:20 | 24800.00 | 76 | |
| 1 | 12:00 | 24800.00 | 75 | stationary 40 min → split (no leg open) |
| 2 | 12:10 | 24812.00 | 73 | moving, 10 min → leg X, exact start 12:00 |
| 3 | 12:45 | 24850.00 | 66 | moving, **35 min** > 20 → closes X at #2; new leg Y, bounded 12:10 … 12:45 |
| 4 | 12:50 | 24849.20 | (errored) | 0.80 below the max → **dropped**, `odometer_regression` on Y |
| 5 | 12:55 | 24862.00 | 64 | moving, 10 min (from #3) |
| 6 | 13:30 | 24862.00 | 64 | stationary 35 min → closes Y at #5 |
| 7 | 13:31 | 24862.30 | 64 | moving 0.30 km … |
| 8 | 14:10 | 24862.30 | 64 | stationary 39 min → closes the 0.30 km leg → **dropped** (< 0.5 km) |

| Leg | Start bound | End | km | ΔSoC | Energy | Quality and reasons |
|---|---|---|---|---|---|---|
| X | 12:00 (exact) | 12:10 | **12.00** | 75 → 73 = 2 | **1.420** | **low**: `soc_delta_small` → `needs_review` |
| Y | 12:10 … 12:45 | 12:55 | **50.00** | 73 → 64 = 9 | **6.390** | **medium**: `soc_delta_small`, `soc_reading_stale` (35 min from the 12:10 reading to 12:45), `start_time_bounded`, `odometer_regression` → `auto` |

Counters: `dropped_duplicates = 1`, `dropped_odometer_regressions = 1`, `dropped_short_legs = 1`. Feeding the readings in any order gives the same result. X and Y are separate legs, but a plan's chain (D4) can still join them (gap 0).

### Example 5 — Simulated rows, unknown capacity

A real vehicle (`is_simulated = false`) whose specs row and catalog have no capacity. Three readings in its window carry `is_simulated = true` (for example from a misconfigured seed).

| # | Time | odo | soc | `is_simulated` |
|---|---|---|---|---|
| 1 | 16:00 | 5100.00 | 90 | false |
| 2 | 16:05 | 5104.00 | 89 | false |
| 3 | 16:07 | 5300.00 | 20 | **true** → dropped |
| 4 | 16:10 | 5108.50 | 88 | false |
| 5 | 16:12 | 5400.00 | 10 | **true** → dropped |
| 6 | 16:15 | 5113.00 | 87 | false |
| 7 | 16:20 | 5500.00 | 5 | **true** → dropped |
| 8 | 16:50 | 5113.00 | 87 | false (stationary 35 min → closes) |

Result: one leg 16:00 → 16:15, **13.00 km**, SoC 90 → 87, `energy_kwh = null`, **low**: `capacity_unknown`, `soc_delta_small`. `dropped_simulated = 3`. No backtest row (no baseline either). Without the simulated filter, the dropped rows would have made a 400 km "trip".

---

## 6. Acceptance mapping

F8 fills in the "Test" column with real test names.

| Requirement | Planned test (feature) | Test |
|---|---|---|
| PRD 4.5: view completed or confirmed trips | F3 `GET /observed-trips`; F7 list | |
| PRD 4.5: estimated vs observed per trip | F4 backtests; F5 `/analytics/trips`; F6 scatter | |
| PRD 4.5: Wh/mi or kWh/100 km | F6 unit conversion | |
| PRD 4.5: CSV export | F5 CSV = JSON; F6 download | |
| PRD 4.5: driving separate from charging | F2 example 3 | |
| PRD 4.5: quality flag on each trip | F2 examples 1–5; F6/F7 badges | |
| PRD 4.5 / §7: simulated excluded by default | F2 example 5; F5 `include_simulated`; F6 banner | |
| Roadmap: real trips without manual intervention | F3 webhook → hook → leg; F8 real car | |
| Roadmap: prediction vs observation error | F4 example 3; F5 MAE | |
| Ownership (404) on every observed/analytics route | F3, F5 | |

---

## 7. Open questions and follow-ups

1. **Event cadence (P2-R2).** Quality depends on it (example 2). F8 records the real mix; tune D8 or `stop_split_minutes` in a follow-up if needed.
2. **Is the Smartcar SoC "usable" or "gross"?** Assumed usable (D10). If it turns out to be gross, observed energy is scaled by the wrong capacity; check against a real charging session's kWh.
3. **Charging sessions as records.** The engine finds them; storing them (observed charging minutes vs planned) would let analytics score the charging-curve model too. Not in v1.
4. **Manufacturer range vs predicted range over time** (PRD 4.5 story). `range` is recorded, but PRD §7 says it isn't ground truth; a chart of it belongs with ML Stage A/B. Deferred.
5. **Weather and speed trends** (PRD 4.5). There is no weather integration yet and no speed signal on the bZ; db-design §9's `trip_weather` question moves to the ML plan.
6. **Personal baseline from observed legs** (phase4_plan §7 item and ML Stage C). The backtest bias (`bias_kwh`) is the input.
7. **Not modelled in inference v1:** SoC drift while parked (included in a bounded leg's energy), regen-heavy legs where SoC barely moves, odometer unit changes mid-series, and a second driver.

## 8. Sources

- bundlephobia, `recharts@3.11.0`: 658,837 B minified, 176,724 B gzipped; `uplot@1.6.32`: 50,830 B minified, 21,856 B gzipped (checked 2026-11-18). https://bundlephobia.com/package/recharts@3.11.0 · https://bundlephobia.com/package/uplot@1.6.32
- npm registry metadata for `recharts@3.11.0` (dependencies: `@reduxjs/toolkit`, `react-redux`, `immer`, `victory-vendor`, …), checked with `npm view` 2026-11-18.
- Smartcar webhook event types (`VEHICLE_STATE` on signal change; at-least-once, out of order): https://smartcar.com/docs/api-reference/webhooks/event-types
- FastAPI background tasks (run after the response, same process): https://fastapi.tiangolo.com/tutorial/background-tasks/
- PostgreSQL advisory locks (`pg_advisory_xact_lock`): https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
