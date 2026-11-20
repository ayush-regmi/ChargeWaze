# Manual test log

Checks that automated tests can't cover: they need the real Toyota bZ, real Mapbox/AFDC keys, or a person looking at the screen. Agent sessions can't run them, so they collect here.

- **Who updates it:** you, after running a check (fill in the result row). The coordinator adds new rows when a merged PR leaves a check manual.
- **Where results go:** a check's result row here, plus wherever its "Record in" says (PR, roadmap, phase plan). Screenshots go in the named PR or close-out PR, not in the repo.
- **Status values:** `open`, `blocked (<what>)`, `pass`, `fail (<issue/PR>)`.

Last updated 2026-11-19 (P5-F8 close-out: M-11 added).

---

## Summary

| ID | Check | Needs | Blocks | Status |
|---|---|---|---|---|
| M-01 | Disconnected car's events are not stored (P2-R1) | Real car | Phase 2 "complete" | open |
| M-02 | Smartcar event cadence over a few days (P2-R2) | Real car, several days | Stale-SoC threshold (P4), P5 quality mix | open |
| M-03 | Places + directions `curl` with real keys (P3-R1) | Mapbox key | P3-F9 | open |
| M-04 | Along-route Frederick → Toronto returns US and CA stations (E2 live) | Mapbox + AFDC keys | P3-F9 | open |
| M-05 | Charging page near Frederick, MD (P3-F6) | Mapbox public + secret, AFDC | P3-F9 | open |
| M-06 | Trip Planner Frederick → Toronto, both sides of the border (P3-F7, E1/E2) | Mapbox public + secret, AFDC | P3-F9 | open |
| M-07 | Saved-trips list and detail pages look right (P4-F7) | Mapbox public + secret, AFDC, connected vehicle | P4-F9 | open |
| M-08 | Real DC charging session logged for the bZ curve | Real car, DC charger | phase4_plan §7 item 2 (not blocking) | open |
| M-09 | Clean up local per-session databases | Docker | Nothing | open |
| M-10 | Real-key Frederick → Toronto **plan** with charging stops, latency and screenshot (P4-F9) | Mapbox public + secret, AFDC, connected vehicle | Phase 4 "complete" (roadmap) | open |
| M-11 | Real drives and a charge produce observed legs with no manual step; record the quality mix (P5-F8) | Real car, a few days, M-02 first | Phase 5 "complete" (roadmap) | open |

Upcoming checks that close-out features will add (listed so they aren't a surprise): see [Upcoming](#upcoming).

---

## Setup

Shared by several checks. Do it once per sitting.

1. **API keys** in `services/api/.env` (never commit it): `MAPBOX_SECRET_TOKEN`, `AFDC_API_KEY`. In `apps/web/.env.local`: `NEXT_PUBLIC_MAPBOX_PUBLIC_TOKEN`. Leave `MAPBOX_API_BASE_URL` / `AFDC_API_BASE_URL` unset so the real providers are used.
2. **Database:** `services/api/.env` points at Supabase. Bring it up to date first (the Phase 2 real-car run lost time to a dev database one migration behind):

```bash
cd services/api && uv run alembic upgrade head
```

3. **Servers** (two terminals):

```bash
cd services/api && uv run fastapi dev app/main.py
```

```bash
npm run dev --workspace=apps/web
```

4. **An access token for `curl`** (M-03, M-04). The browser never calls `/api/v1/` itself (Server Components and server actions attach the token server-side), so DevTools → Network won't show it. Read it from the Supabase session cookie instead: `@supabase/ssr` stores it, not `httpOnly`, as `sb-<project>-auth-token` (split into `.0`, `.1`, … chunks when long, value prefixed `base64-`). Sign in at http://localhost:3000, open the DevTools **Console** on that tab, and paste:

```js
(() => {
  const parts = document.cookie.split("; ")
    .filter((c) => /^sb-.+-auth-token(\.\d+)?=/.test(c))
    .map((c) => [c.slice(0, c.indexOf("=")), c.slice(c.indexOf("=") + 1)])
    .sort(([a], [b]) => Number(a.split(".")[1] ?? 0) - Number(b.split(".")[1] ?? 0));
  let v = decodeURIComponent(parts.map(([, val]) => val).join(""));
  if (v.startsWith("base64-")) {
    const bin = atob(v.slice(7).replace(/-/g, "+").replace(/_/g, "/"));
    v = new TextDecoder().decode(Uint8Array.from(bin, (ch) => ch.charCodeAt(0)));
  }
  copy(JSON.parse(v).access_token);
  return "access token copied";
})();
```

   It copies the token to the clipboard (`copy` is a DevTools console helper). Then, in the terminal you'll run `curl` from (macOS):

```bash
export CW_AUTH="Bearer $(pbpaste)"
```

   The token expires after about an hour; repeat both steps if the API starts returning `UNAUTHENTICATED`. Don't paste it anywhere else.

5. **Real car only** (M-01, M-02, M-08): Smartcar app in `live` mode, and the webhook reachable through the ngrok tunnel as in the 2026-11-16 run ([`phase2_plan.md`](phase2_plan.md) F8).

---

## Real car (Smartcar)

### M-01 · Disconnected car's events are not stored (P2-R1)

Source: [`phase2_plan.md`](phase2_plan.md) F8 item 1, E3 tail. Everything else in E1–E3 passed on 2026-11-16.

Steps:
1. Note the car's latest `vehicle_telemetry.recorded_at` (Supabase SQL editor).
2. In Garage, disconnect the bZ.
3. Make the car emit an event: unlock/lock, start charging, or drive a short way.
4. Wait 15–30 min. Check the API log for webhook deliveries and query `vehicle_telemetry` again.

Expected: no new `vehicle_telemetry` rows and no `latest_state` change for that vehicle. Also note whether Smartcar delivers anything at all after the disconnect (the log tells you).

Record in: this row, and `phase2_plan.md` F8 progress notes.

| Date | Result | Smartcar still delivering? | Notes |
|---|---|---|---|
| | | | |

### M-02 · Event cadence over a few days (P2-R2)

Source: [`phase2_plan.md`](phase2_plan.md) F8 item 2; plan.md §4.0.

Steps:
1. Keep the car connected for at least 3 days of normal use (some drives, at least one charge).
2. Query the gaps between consecutive events, split by parked / driving / charging:

```sql
select recorded_at,
       recorded_at - lag(recorded_at) over (order by recorded_at) as gap
from vehicle_telemetry
where vehicle_id = '<id>'
order by recorded_at;
```

3. Pick a freshness threshold from the numbers (today it's `telemetry_stale_after_minutes = 15`).

Expected: a measured typical gap while driving, while charging and while parked.

Record in: the roadmap risk table, ADR 0002 if the polling decision changes, and phase4_plan §7 item 3 (stale threshold). If gaps while driving are much longer than ~15 min, note it for P5 too (inference quality depends on it, phase5_plan F8).

| Dates | Driving gap | Charging gap | Parked gap | Threshold chosen | Notes |
|---|---|---|---|---|---|
| | | | | | |

### M-08 · Real DC charging session for the bZ curve

Source: [`phase4_plan.md`](phase4_plan.md) §7 item 2; P4-F2 (#32). The catalog curve is derived, not measured. Not blocking anything.

Steps: on a DC fast charger, write down SoC % and charger kW every 2–3 minutes from plug-in to unplug (Smartcar doesn't report charging power on the bZ, so read it off the charger or the car). Note start/end SoC, total time, kWh delivered, and outside temperature.

Record in: a new follow-up issue with the log attached; the curve gets replaced in a later PR.

| Date | Charger | Start → end SoC | Minutes | kWh | Temp | Notes |
|---|---|---|---|---|---|---|
| | | | | | | |

### M-11 · Real drives produce observed legs (P5-F8)

Source: [`phase5_plan.md`](phase5_plan.md) F8 and §6 ("real trips without manual intervention"). Do it after M-02, over the same days if you like. Automated evidence that webhooks alone are enough is `tests/trips/test_phase5_closeout.py`; this check confirms it with the real car and records how good the legs are.

Steps:
1. With the bZ connected (and the API receiving webhooks), drive normally for a few days: some short drives, at least one longer one, and one charging session. Don't press anything in the app or run any script.
2. Optionally, plan one of the drives first on `/trips` and save it, then drive it within 2 h.
3. Check the legs (Supabase SQL editor):

```sql
select start_time, start_time_latest, end_time, distance_km, energy_kwh,
       quality_flag, quality_reasons, review_status, planned_trip_id is not null as matched
from observed_trips
where vehicle_id = '<id>'
order by start_time;
```

```sql
select quality_flag, count(*) from observed_trips
where vehicle_id = '<id>' and not is_simulated
group by quality_flag;
```

4. Open `/trips/saved?view=observed` and `/analytics`. The drives are listed with quality badges, and the CSV matches the table. If you planned one in step 2, its trip page shows "Planned vs actual".

Expected: one leg per drive (a charge splits a drive), with no manual step. The distances match the odometer. Most legs are `medium` or better if M-02 found events every ≤15 min while driving. A key-off-only feed gives mostly `low` (§5 example 2).

Record in: this row; the cadence and the quality mix in the roadmap risk table (§5). If most legs are `low` because of the cadence, open a follow-up to tune phase5_plan D8 or `stop_split_minutes` (phase5_plan §7 item 1).

| Dates | Drives / charges | Legs created | High / medium / low | Matched plan | Notes |
|---|---|---|---|---|---|
| | | | | | |

---

## Real provider keys (Mapbox, AFDC)

### M-03 · Places suggest and directions with real keys (P3-R1)

Source: [`phase3_plan.md`](phase3_plan.md) F4 acceptance. Blocks P3-F9.

```bash
curl -s -H "Authorization: $CW_AUTH" "http://localhost:8000/api/v1/places/suggest?q=Toronto&session_token=$(uuidgen)&lat=39.4143&lon=-77.4105" | jq '.items[] | {name, kind, country_code}'
```

Run these from the repo root. Save the directions response to a file; M-04 reuses it. `services/api/scripts/spike_out/` is gitignored, so real responses stay out of commits.

```bash
mkdir -p services/api/scripts/spike_out
```

```bash
curl -s -w 'directions: %{time_total}s\n' -o services/api/scripts/spike_out/directions.json -X POST -H "Authorization: $CW_AUTH" -H "Content-Type: application/json" -d '{"origin":{"lat":39.4143,"lon":-77.4105},"destination":{"lat":43.6532,"lon":-79.3832}}' http://localhost:8000/api/v1/routes/directions
```

```bash
jq '.routes[] | {index, distance_m, duration_s, countries}' services/api/scripts/spike_out/directions.json
```

Expected: suggestions include Toronto, ON; directions return 1–3 routes, each with `countries` containing `US` and `CA`. Also check the API log shows `/places/suggest` **without** its query string (the D13 log-redaction fix).

Record in: this row and phase3_plan F4 "As built" (replace "still to run").

| Date | Suggest OK | Routes returned | Query string hidden in log | Notes |
|---|---|---|---|---|
| | | | | |

### M-04 · Along-route Frederick → Toronto returns stations in both countries (E2 live)

Source: phase3_plan E2, F5 "Still manual" (#29). Blocks P3-F9.

Steps: build the request body from M-03's saved directions response with `jq`. Don't paste the polyline into a shell command: the real Frederick → Toronto geometry contains hundreds of backticks and backslashes, which the shell would run or mangle.

```bash
jq '{geometry_polyline6: .routes[0].geometry_polyline6, corridor_km: 3, charging_level: "dc_fast"}' services/api/scripts/spike_out/directions.json > services/api/scripts/spike_out/along-route.json
```

```bash
curl -s -w 'along-route: %{time_total}s\n' -o services/api/scripts/spike_out/along-route-response.json -X POST -H "Authorization: $CW_AUTH" -H "Content-Type: application/json" --data-binary @services/api/scripts/spike_out/along-route.json http://localhost:8000/api/v1/stations/along-route
```

```bash
jq '{total_results, truncated, by_country: ([.items[].country] | group_by(.) | map({(.[0]): length}) | add)}' services/api/scripts/spike_out/along-route-response.json
```

For the other routes, change `.routes[0]` to `.routes[1]` or `.routes[2]` and repeat.

Expected: counts for both `US` and `CA`. The `-w` output prints the directions and along-route latencies; P3-F9 records them in the roadmap as input for P4.

Record in: this row; P3-F9 puts the counts and latencies in the roadmap.

| Date | Route | US stations | CA stations | Directions latency | Along-route latency |
|---|---|---|---|---|---|
| | | | | | |

### M-05 · Charging page near Frederick, MD (P3-F6)

Source: phase3_plan F6 acceptance; #35 unchecked box.

Steps:
1. Open `/charging`, search "Frederick, MD", pick the result.
2. Check compatible stations appear on the map **and** in the list, and that the map tiles load (not the "Map unavailable" fallback).
3. Change a filter (e.g. charging level), then open a station's detail page and go back.
4. Hard reload (Cmd+Shift+R): the filters and search stay.
5. Take a screenshot.

Expected: all of the above, and no Mapbox secret token in the browser (DevTools → Network: only `pk.` tokens in tile requests).

Record in: screenshot on the P3-F9 close-out PR (or a comment on #35); result row here.

| Date | Map + list | Detail page | Reload keeps filters | Only public token | Screenshot |
|---|---|---|---|---|---|
| | | | | | |

### M-06 · Trip Planner Frederick → Toronto (P3-F7, E1/E2 manual half)

Source: phase3_plan F7 acceptance; #41 unchecked box. Blocks P3-F9.

Steps:
1. Open `/trips`, set origin "Frederick, MD" and destination "Toronto, ON".
2. Check 1–3 routes are listed and drawn on the map, with a border label.
3. Check stations appear along route A on **both** sides of the border.
4. Select route B: the station list and markers update.
5. Reload: the same plan comes back (URL state).
6. Screenshot showing US and CA stations together.

Expected: as above; the Playwright run on the stub already covers the flow, so this is about real data and real tiles.

Record in: screenshot on the P3-F9 close-out PR; result row here.

| Date | Routes shown | US + CA stations | Route switch | Reload keeps plan | Screenshot |
|---|---|---|---|---|---|
| | | | | | |

### M-10 · Real-key Frederick → Toronto plan (P4-F9)

Source: phase4_plan X1 and F9; roadmap Phase 4 completion criteria. Blocks the Phase 4 roadmap status moving from "awaiting manual checks" to complete.

Before you start: Setup steps 1–3 (keys, `alembic upgrade head`, both servers) and a connected vehicle in the Garage. Never paste keys or the access token into a file, a PR or a chat.

**Part A · In the browser**

1. Open http://localhost:3000/trips.
2. In **From**, type `Frederick, MD` and pick the first suggestion. In **To**, type `Toronto, ON` and pick the first suggestion.
3. Route A is selected. In the panel "Charging plan for Route A", click **Plan charging stops**.
4. If the panel asks for the starting charge ("too old to plan with" or no reading), type `90` in **Starting battery (%)** and click **Plan with this charge**.
5. Check: the result is feasible, with one or more stops. Each stop shows `NN% → NN%`, a charge time and "detour N min (estimate)". Every arrival is at or above your reserve (15 % by default). The totals show **Total time**. The stop markers on the map sit on the route.
6. Click **Save trip**. Take a screenshot of the map and the plan panel together.

**Part B · Latency with `curl`** (Setup step 4 first, for `CW_AUTH`). Run from the repo root; responses go to the gitignored `spike_out/`.

```bash
mkdir -p services/api/scripts/spike_out
```

```bash
export CW_VEHICLE=$(curl -s -H "Authorization: $CW_AUTH" http://localhost:8000/api/v1/vehicles | jq -r '.items[0].id') && echo "$CW_VEHICLE"
```

```bash
jq -n --arg vid "$CW_VEHICLE" '{vehicle_id: $vid, origin: {lat: 39.4143, lon: -77.4105, label: "Frederick, MD"}, destination: {lat: 43.6532, lon: -79.3832, label: "Toronto, ON"}, starting_soc_override: 0.9}' > services/api/scripts/spike_out/plan-request.json
```

```bash
curl -s -w 'plan: HTTP %{http_code} in %{time_total}s\n' -o services/api/scripts/spike_out/plan-response.json -X POST -H "Authorization: $CW_AUTH" -H "Content-Type: application/json" --data-binary @services/api/scripts/spike_out/plan-request.json http://localhost:8000/api/v1/routes/plan
```

```bash
jq '{status, distance_km: .route.distance_km, countries: .route.countries, stops: [.stops[] | {name: .station.name, network: .station.network, km: .distance_along_route_km, arrival_soc, departure_soc, charging_minutes}], arrival_soc, total_minutes: .totals.total_minutes, candidates, reason}' services/api/scripts/spike_out/plan-response.json
```

Run the `curl` line three times and note each time (the first one fills the station cache, so it is usually the slowest).

Expected: `HTTP 200`, `status: "feasible"`, `distance_km` near 748, `countries` `["US","CA"]`, two or more stops (the §5 math says three at 0.90 if stops were spaced ideally; real stations may need more), every `arrival_soc` ≥ 0.15 and the final `arrival_soc` ≥ 0.15, `reason: null`. On fixtures the same request takes about 0.25 s (roadmap Phase 4); the real one adds Mapbox and AFDC network time.

Record in: this row; the screenshot and the three latencies on the P4-F9 PR (or a comment on it); the real-provider latency in the roadmap Phase 4 latency table.

| Date | Feasible + stops | Arrivals ≥ reserve | US + CA | Latency (3 runs) | Screenshot |
|---|---|---|---|---|---|
| | | | | | |

---

## Visual checks after later merges

### M-07 · Saved-trips pages (P4-F7)

Source: #38 "Left manual". Unblocked by P4-F4 (#43) and P4-F6 (#47). Do it straight after M-10, which saves a trip.

1. Open http://localhost:3000/trips/saved. The Frederick, MD → Toronto, ON trip saved in M-10 is listed, with its distance and total time.
2. Click it. The detail page (`/trips/<id>`) shows the static map with the route and stop markers, each stop with its arrival → departure charge and charge time, and the totals. Take a screenshot.
3. Make an infeasible plan to check its detail page: back on http://localhost:3000/trips with the same From/To, open **Re-plan with different preferences**, set **Reserve (%)** to `50` and **Charge to (%)** to `55`, click **Re-plan**. The panel shows a red "Not feasible" alert with no stops or totals. Click **Save trip**.
4. Open http://localhost:3000/trips/saved and click the infeasible trip. The detail page says it isn't feasible and describes the limiting segment in text. Take a screenshot.
5. On each trip's detail page click **Delete**, then **Delete** again in the "Delete this trip?" dialog. You land back on the list without that trip.
6. With no saved trips left, http://localhost:3000/trips/saved shows "No saved trips yet". (Delete any older saved trips first if you have them.) Take a screenshot.

Record in: this row; screenshots on the P4-F9 PR (or a comment on it).

| Date | List | Empty state | Detail (feasible) | Detail (infeasible, segment text) | Delete | Screenshot |
|---|---|---|---|---|---|---|
| | | | | | | |

### M-09 · Clean up local per-session databases

Not a test, but left over from merged sessions (plan.md §2.4). Check what exists, then drop the ones for merged features:

```bash
docker exec chargewise-postgres psql -U chargewise -lqt | cut -d '|' -f1 | grep chargewise_
```

```bash
docker exec chargewise-postgres dropdb -U chargewise <name>
```

Merged so far: `p3_stations`, `p3_charging`, `p3_planner`, `p3_favorites`, `p4_energy`, `p4_trips`, `p4_optimizer`, `p4_history`, `p4_prefs`, `p4_plan_endpoint`, `p4_planner_ui`, `p4_closeout`, `p4_vehicle_baseline`, `p5_inference`, `p5_observed_schema`, `p5_trip_ingestion`, `p5_analytics_api`, `p6_sentry` (exact names vary by session; each has `_test` and sometimes `_e2e` twins). Leave the ones for the W7 sessions (P5-F4, P5-F6, P5-F7, P6-F3), which are running now.

---

## Upcoming

Checks that later close-outs will need; they'll move into the summary when their feature merges.

| Feature | Check |
|---|---|
| P3-F8 favorites | Favorite a station, sign out and back in: still favorited (Playwright covers it; a quick real-data look is enough). |
| P4-F8 | Set a per-vehicle baseline on the real bZ and re-plan; numbers change as expected. |
| P6-F3 / F4 | Deployed app smoke test: sign in, Garage, Charging, Trip Planner on the production URL; alerts fire. |
| P6-F5 | Delete a test account end to end; its rows are gone. |
