# Chargewise — Development Roadmap

Status: **Phase 2 code complete; real-car verification and cadence measurement pending, then Phase 3** · Last updated: 2026-11-16

Delivery plan for Chargewise. Estimates assume part-time development with Claude Code / Cursor and real testing. They are planning estimates, not deadlines.

## 0. Immediate actions (do these first, in order)

1. **Verify Smartcar V3 end-to-end against the real 2026 Toyota bZ.** Confirm which signals the manufacturer actually exposes, make one real authorized read, and persist one normalized record. If V3 doesn't deliver what the PRD assumes, the rest of the plan needs to change before Phase 1 starts.
2. **Finalize the starter spec.** Done: `CLAUDE.md`, the five `docs/*.md` files, and the ADRs are on `main`.
3. **Build vertical slice 1** (Phase 1 ends here): a user can sign in, navigate the app, and call an authenticated FastAPI endpoint.
4. **Build vertical slice 2** (Phase 2 ends here): the Toyota is connected, telemetry flows via webhooks, and the Dashboard shows real battery state.
5. **Map routing before charging-aware routing.** Get plain Mapbox directions and station search working before adding the optimizer.

Do not start ML training, complex animations, or a React Native app until the core planner is working.

## 1. Phases

Each phase ends when its **completion criteria** are satisfied. "Done" means tests run and pass, not "it rendered once".

### Phase 1 — Foundation (Weeks 1–2) — complete

Status: done. Merged in [PR #1](https://github.com/ayush-regmi/ChargeWise/pull/1) with CI green on `main`; verified against hosted Supabase on 2026-11-12. A follow-up migration enables Row Level Security (no policies) on every `public` table, so Supabase's Data API cannot read Chargewise tables with the publishable key. FastAPI connects as the table owner and is unaffected.

**Scope**

- Monorepo tooling: **npm workspaces + Turborepo** in `apps/`/`packages/`; Python project under `services/api/` using **`uv`** (install with `brew install uv` on macOS). See [`adr/0005-npm-turborepo-uv-tooling.md`](adr/0005-npm-turborepo-uv-tooling.md).
- Next.js app shell with Tailwind, shadcn/ui, and layout chrome for Dashboard, Trip Planner, Charging, My Garage, Analytics, Profile.
- FastAPI app with health check, CORS config, Supabase JWT verification, and the `auth` and `users` modules.
- Supabase project created; `profiles` and `user_preferences` tables managed via Alembic.
- First `GET /api/v1/me` wired end-to-end.
- Local dev scripts, per-app `.env.example` templates consumed, committed `.env` prohibited.
- CI (GitHub Actions): lint, type-check, pytest, Vitest, Alembic up/down round-trip, Playwright smoke on the auth flow.

**Completion criteria**

- A user can register, sign in, see `GET /api/v1/me` data on the Profile page, and sign out.
- CI green on main.

### Phase 2 — Vehicle integration (Weeks 3–4)

Status: code complete (F0–F7 merged; F8 adds the Playwright Garage → Dashboard test). Still open before this phase is done: the real-car checks for the first three completion criteria, and measuring the Toyota's event cadence for the freshness threshold (risk table, §5). Tracked in [`phase2_plan.md`](phase2_plan.md) F8.

**Scope**

- `integrations/smartcar` adapter: V3 application token, Connect authorization URL (`response_type=none`), connections listing, webhook subscription, signature verification.
- `vehicles` module and tables (`vehicles`, `vehicle_specs`, `vehicle_telemetry`).
- Endpoints: `POST /api/v1/vehicles/connect`, `GET /api/v1/vehicles`, `GET /api/v1/vehicles/{id}`, `GET /api/v1/vehicles/{id}/state`, `DELETE /api/v1/vehicles/{id}`, `POST /api/v1/webhooks/smartcar`.
- Webhook ingestor: signature verification, deduplication by `source_event_id`, out-of-order handling, cached `latest_state`.
- Local HTTPS tunnel for webhook development.
- Frontend: Add Vehicle flow in Garage; Dashboard reads `GET /api/v1/vehicles/{id}/state` and renders SoC, range, odometer, charging status with freshness markers.
- Fixtures: canonical Smartcar responses and a signed webhook payload in [`fixtures/smartcar/`](../fixtures/smartcar/).

**Completion criteria**

- The real 2026 Toyota's supported data appears on the Dashboard with timestamps.
- Dashboard values survive a page reload (served from cached `latest_state`, not a live Smartcar call).
- Disconnecting the vehicle removes it from Garage and stops accepting new telemetry.
- Unit + integration tests cover: signature-invalid webhook (rejected), duplicate event (idempotent), out-of-order event (latest state is the max `recorded_at`), stale data flag.

### Phase 3 — Maps and chargers (Weeks 5–6)

**Scope**

- `integrations/mapbox` adapter: Geocoding, Search Box autocomplete, Directions.
- `integrations/stations` adapter: AFDC/NREL for US; evaluate NRCan or Open Charge Map for Canadian coverage and pick one.
- `GET /api/v1/stations` endpoint.
- Frontend: Mapbox GL JS map in Trip Planner and Charging Stations pages; autocomplete origin/destination; station layer with connector/network/power filters defaulted to the active vehicle's compatible connectors.
- Caching of station fetches into `charging_stations` with `provider` + `external_id` uniqueness.

**Completion criteria**

- A user can search a destination, see one or more driving routes on the map, and see compatible charging stations near each route.
- Cross-border test trip (Frederick, MD → Toronto, ON) returns stations on both sides of the border, confirming the Canadian provider choice works.
- No Mapbox secret token reaches the browser bundle.

### Phase 4 — Charging-aware trip planner (Weeks 7–9)

**Scope**

- `energy` module: deterministic baseline energy model using `vehicle_specs.usable_capacity_kwh`, a configurable kWh/mi baseline, and the piecewise `charging_curve`.
- `routing` module: feasibility check, charging-stop enumeration (bounded), per-stop charging-time estimate, plan ranking by total time.
- `POST /api/v1/routes/plan`, `GET /api/v1/trips`, `GET /api/v1/trips/{id}`; persistence in `planned_trips` and `planned_stops`.
- Explicit infeasibility responses with reason codes.
- Frontend: planner renders the route polyline, charging stops with arrival/departure SoC, charging time per stop, total trip time, and a prominent infeasibility state.
- Full acceptance test matrix from [`product-requirements.md`](product-requirements.md#9-acceptance-test-matrix).

**Completion criteria**

- The planner inserts charging stops on real long-distance routes and matches hand-checked feasibility on at least three sample trips.
- `starting_soc_override` is required when telemetry is stale; the UI prompts for it.
- Latency measured and recorded for a ~500 mi trip.
- Every acceptance test case has a passing automated test.

### Phase 5 — History and analytics (Weeks 10–11)

**Scope**

- `observed_trips` ingestion: SoC-inferred trips from telemetry gaps, with a manual-confirmation path for anything low-quality.
- `prediction_results` populated by matching plans to observations.
- `GET /api/v1/analytics/consumption` and the Analytics page: trends by day/week/month, estimated vs observed energy, per-trip quality flags, CSV export.
- Charts in the frontend using whatever chart library is lightest (decide during the phase).
- Clear separation between observed real trips and simulated demo trips.

**Completion criteria**

- At least a handful of real trips produce `observed_trips` rows without manual intervention.
- Analytics shows prediction vs observation error without silently mixing simulated data.
- CSV export reproduces the data visible in the UI.

### Phase 6 — Production and portfolio (Week 12)

**Scope**

- Deploy Next.js on Vercel, FastAPI on Render or Fly.io, Supabase for DB/auth.
- Sentry wired into both apps.
- Public demo mode toggle: seeded users, vehicles, trips, telemetry — all `is_simulated = true`. Private live mode gated behind a configured allowlist.
- Monitoring / alerting basics: error rate, webhook success rate, planner latency.
- Documented demo script (what the recruiter sees and clicks).
- README polish, architecture diagram, and screenshots.
- Verify account deletion and data-retention paths.

**Completion criteria**

- A recruiter can visit the public URL, run the demo flow, and understand the architecture without needing to connect a real vehicle.
- All smoke tests pass in production.

## 2. Vertical slices

Within each phase, work ships as full vertical slices (DB → backend → frontend → tests), not horizontal layers. First few slices in order:

1. **Sign-in slice** (Phase 1). User signs in → backend verifies JWT → Profile page shows `GET /api/v1/me`.
2. **Garage slice** (Phase 2). User connects Toyota → backend stores `vehicles` + `vehicle_specs` → Garage page lists it.
3. **Dashboard slice** (Phase 2). Smartcar webhook arrives → telemetry persists → Dashboard shows timestamped SoC/range.
4. **Map-and-search slice** (Phase 3). User searches a destination → Mapbox returns a route → map draws it.
5. **Compatible-stations slice** (Phase 3). AFDC returns stations near the route → frontend filters to the vehicle's connectors.
6. **Feasibility slice** (Phase 4). Planner answers "can I make this trip without charging?" for the simplest case.
7. **Charging-stop slice** (Phase 4). Planner inserts the smallest useful set of stops.
8. **Analytics slice** (Phase 5). One trip's predicted vs observed energy renders on the Analytics page.

Each slice: branch, acceptance criteria, tests, review, merge.

## 3. Machine-learning plan (post-MVP)

ML is a measurable improvement on top of the deterministic baseline, not a replacement. The deterministic model stays shipped.

### Stage A — Baseline (built during Phase 4)

Physics-inspired rules plus average consumption. Temperature and highway-speed adjustments added as data allows. All subsequent stages are judged against this baseline.

### Stage B — Supervised regression

Linear / Ridge regression first, then Random Forest and gradient-boosted models (XGBoost). Prediction target: trip energy in kWh (not dashboard range).

Features to start with:

| Feature | Source |
|---|---|
| Trip distance | Mapbox / odometer |
| Average speed | Route estimates or measured GPS |
| Elevation gain | Elevation provider |
| Outside temperature | Open-Meteo |
| Wind speed / direction | Open-Meteo |
| Initial SoC | Smartcar |
| Vehicle model | Smartcar + `vehicle_specs` |
| Historical consumption | Chargewise database |
| Trip duration | Observed trip or route estimate |

### Stage C — Personalization

Learn a per-user correction on top of the vehicle-level baseline. Start with a calibrated multiplier or small learned correction, graduate to more flexible models only when observation count justifies it.

### Stage D — Uncertainty-aware routing

Predict an expected consumption **and** a plausible uncertainty range. The planner uses the upper bound to inflate reserve SoC on high-uncertainty routes instead of treating every prediction as exact.

### Data-quality rules

SoC-to-energy conversion is only an approximation. Rules:

- Preserve raw readings and timestamps.
- Separate driving intervals from charging intervals before computing energy.
- Distinguish measured energy from SoC-inferred energy via `observed_trips.energy_source`.
- Discard or flag low-quality training examples (`observed_trips.quality_flag = 'low'`).
- Manufacturer-estimated range is not ground-truth consumption.

### Evaluation

Primary metrics:

\[
MAE = \frac{1}{n}\sum_{i=1}^{n} |E_i - \hat{E}_i| \qquad RMSE = \sqrt{\frac{1}{n}\sum_{i=1}^{n} (E_i - \hat{E}_i)^2}
\]

Also report arrival-SoC error in percentage points. Splits are trip-based and chronological to avoid leakage. No ML result is claimed in the README or resume unless the test-set metric beats the deterministic baseline on the same split.

### Training thresholds (guidelines, not guarantees)

- First 20–30 useful trips: inspect data quality, distributions, baseline errors.
- About 50+ varied trips: test simple personalized correction models.
- About 100–200+ varied trips: consider more flexible regression; adopt only if validation performance justifies it.

## 4. Per-feature Git workflow

For every substantial feature:

1. Define feature and acceptance criteria (link to the roadmap phase and PRD section).
2. The agent proposes an implementation plan.
3. Implement the smallest useful vertical slice.
4. Run tests, lint, and type checks.
5. Review diffs and real behavior.
6. Commit and merge.

Branch naming: `feature/<phase>-<slug>`. One branch per feature. Mock fixtures for Smartcar / Mapbox / AFDC in the default test run; real-API tests in a separately triggered workflow.

## 5. Risks and open questions

Tracked explicitly here and in the per-doc "open questions" sections.

| Risk | Owner | Resolution step |
|---|---|---|
| Smartcar event frequency on the Toyota is lower than Dashboard needs. | Phase 2 | Measure during Phase 2; if too low, add a polling fallback or widen the freshness threshold. |
| Canadian charging data is missing. | Phase 3 | Pick between NRCan and Open Charge Map before Phase 3 starts. |
| Open-Meteo license is non-commercial. | Pre-ML | Confirm Chargewise stays non-commercial, or pick a different weather provider before Stage B. |
| Mapbox EV Routing becomes GA mid-project. | Ongoing | Stay on the self-built optimizer unless Mapbox EV Routing gives a clear win and acceptable pricing. |
| API costs exceed hobby budget. | Phase 6 | Add per-user rate limits in Phase 6; monitor Mapbox and Smartcar usage. |
| Public demo leaks real location history. | Phase 6 | Public demo loads only `is_simulated = true` rows; live mode is behind an allowlist. |

## 6. Portfolio deliverables

Measurable outcomes to claim (only after they're true):

- [ ] Authenticated, multi-user full-stack web application.
- [ ] Real Smartcar V3 integration with event-driven telemetry ingestion.
- [ ] Automated charging-aware route planner with battery constraints.
- [ ] Real vehicle testing and documented routing edge cases.
- [ ] Persisted trip analytics with provenance and data-quality indicators.
- [ ] Deployed frontend / backend with CI and automated tests.
- [ ] ML model compared against a deterministic energy baseline.

Quantify these with measured numbers (test coverage, MAE, routing latency) after they land. No invented metrics.

## 7. What's explicitly deferred

- React Native app. Web first; PWA before native.
- Live stall availability for providers that don't supply it.
- LLM chatbot features.
- Vehicle command endpoints (lock/unlock/charge control).
- Automatic background trip tracking without a native mobile client.
