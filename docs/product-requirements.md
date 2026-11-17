# Chargewise — Product Requirements

Status: **Draft (specification phase)** · Owner: project lead · Last updated: 2026-11-06

## 1. Vision

Chargewise is a full-stack EV intelligence and route-planning platform that combines connected-vehicle data, charging-aware routing, trip analytics, and (later) personalized machine learning.

The core question it must answer:

> Given my vehicle's current battery level, where I want to go, the weather, and available charging infrastructure, what is the most efficient and reliable way to complete my trip?

Chargewise is **not** a dashboard that only shows a battery percentage and a station map. The routing intelligence is the product.

## 2. Target users

- **v1 (private mode):** a single owner of a **2026 Toyota bZ** (NACS port with supplied J1772 and CCS1 adapters, access to compatible Tesla Superchargers). The data model must not hard-code one vehicle — it should already support multiple users and vehicles.
- **v1 (public mode):** recruiters and visitors browsing a public demo backed by clearly labeled simulated data. No real vehicle required.
- **Future:** additional connected EVs via Smartcar-supported manufacturers; eventually a React Native mobile client reusing the same backend.

## 3. Core demo flow (MVP definition of done)

Chargewise is "MVP complete" when a user can, in one session:

1. Sign in with Supabase Auth.
2. Connect the Toyota through Smartcar Connect (V3).
3. See real, timestamped battery state on the Dashboard.
4. Search a destination in the Trip Planner.
5. Receive a battery-feasible route with recommended charging stops, estimated arrival SoC, and total trip time — or an explicit "not feasible" result.

If any one of these steps uses fabricated data, the MVP is not complete.

## 4. Feature areas

The app is organized into six areas. Each one lists the primary user stories and acceptance criteria.

### 4.1 Dashboard — Vehicle overview

**Purpose:** show the selected vehicle's latest known state at a glance.

**User stories**
- As a signed-in owner, I can see my primary vehicle's current SoC, estimated range, charging status, and odometer.
- As an owner, I can see when the last telemetry update happened and whether the connection is healthy.
- As an owner, I get a clear "data may be stale" warning when readings are older than the configured freshness threshold.
- As an owner, I can jump to **Plan Trip**, **Find Chargers**, or **View Vehicle** from the Dashboard.

**Acceptance criteria**
- All displayed values show their source timestamp (local time) and unit.
- Chargewise-predicted range is rendered only when the energy model has enough signal; otherwise only the manufacturer estimate is shown and the UI says so.
- Stale readings (older than the freshness threshold in [`architecture.md`](architecture.md)) are visually marked and never silently treated as current.
- The page renders with no telemetry available ("Not yet connected", "Awaiting first webhook event").

### 4.2 Trip Planner — Main feature

**Purpose:** generate charging-aware trip plans on an interactive map.

**User stories**
- I can search for an origin and destination by city, address, landmark, or business.
- I can use my vehicle's last known location as the origin, with explicit permission.
- I get driving directions and reasonable alternatives.
- I see compatible charging stations near each candidate route.
- Chargewise automatically inserts charging stops when needed.
- I see estimated arrival SoC, charging duration per stop, total trip time, and detour versus the shortest route.
- I can adjust charging preferences (minimum reserve SoC, max detour, preferred networks, charge-to limit) and re-plan.
- I can save a planned trip and open the driving directions in an external navigation app.

**Acceptance criteria**
- Autocomplete suggests both addresses and POIs.
- The map renders the selected route's polyline with charging stops, each labeled with planned arrival and departure SoC.
- When a trip is infeasible given current SoC and preferences, the UI says so explicitly and does **not** return a route marked as valid.
- The planner never invents real-time stall availability. It uses only what the station provider actually returns.
- Preferences persist per user via `user_preferences` (see [`database-design.md`](database-design.md)).

### 4.3 Charging Stations

**Purpose:** browse and filter charging stations outside of trip planning.

**User stories**
- I can view chargers near me or near a searched location.
- I can filter by compatible connector, network/operator, and charging speed (kW).
- I can open a station's detail page to see connector types, counts, operator, and address.
- I can mark stations as favorites.
- I can see real-time availability **only** when the provider for that station supplies verified live data.

**Acceptance criteria**
- Connector filters default to the active vehicle's compatible connectors (for the 2026 bZ: NACS natively, J1772 and CCS1 via adapters).
- Stations without verified availability data must not display a fabricated "available now" badge.
- Favorites are per user and survive a logout/login cycle.

### 4.4 My Garage — Connected vehicles

**Purpose:** manage Smartcar-connected vehicles.

**User stories**
- I can add a vehicle through Smartcar Connect.
- I can see all my connected vehicles with make, model, year, connection status, and supported Smartcar signals.
- I can choose a primary vehicle for the Dashboard and Planner.
- I can edit vehicle configuration: usable battery capacity, compatible adapters, charging preferences.
- I can disconnect a vehicle; disconnecting also stops ongoing telemetry collection.

**Acceptance criteria**
- The supported-signals list reflects what Smartcar actually returned for that vehicle, not a hard-coded assumption.
- Disconnection revokes the Smartcar authorization, deletes the webhook subscription, and marks the vehicle record disconnected (it is not hard-deleted, so historical trips still resolve).
- Attempting to connect the same Smartcar vehicle twice produces a clean "already connected" result instead of a duplicate record.

### 4.5 Trip History & Analytics

**Purpose:** compare what was predicted against what actually happened.

**User stories**
- I can view completed or manually confirmed trips.
- I can compare estimated vs observed energy consumption per trip.
- I can view consumption in Wh/mi or kWh/100 mi (based on my unit preference).
- I can view manufacturer range vs Chargewise-predicted range over time.
- I can see consumption trends by weather, route, and speed where measurements exist.
- I can export trip data as CSV.

**Acceptance criteria**
- Observed-trip records separate driving intervals from charging intervals.
- Each trip record flags its measurement quality (e.g. "SoC-inferred", "odometer-based", "low confidence").
- Simulated trips used in the public demo are excluded from analytics by default and never silently mixed into training data.

### 4.6 Profile & Settings

**Purpose:** account, preferences, and data controls.

**User stories**
- I can manage account details and authentication.
- I can set my preferred vehicle and preferred charging networks.
- I can choose units: miles/kilometers, Fahrenheit/Celsius.
- I can set minimum destination battery percentage, maximum charging detour, and preferred charge-to limit.
- I can review data privacy settings, consent, retention, and request account deletion.

**Acceptance criteria**
- Unit preference is honored across every screen (Dashboard, Planner, History).
- Account deletion removes authored records or anonymizes them per the retention rules in [`database-design.md`](database-design.md), and also disconnects Smartcar vehicles.
- Consent state is stored and auditable.

## 5. Out of scope for MVP

To keep Phase 1–4 shippable, we explicitly defer:

- Machine-learning consumption models (planned for post-MVP; see roadmap ML stages).
- A React Native mobile client (web-only, with a responsive PWA as an intermediate step).
- Live stall availability for stations whose provider doesn't supply it.
- Mapbox EV Routing (currently private preview; Chargewise builds its own charging-aware optimizer instead — see [`docs/adr/0003-own-charging-optimizer-over-mapbox-ev-routing.md`](adr/0003-own-charging-optimizer-over-mapbox-ev-routing.md)).
- Automatic background trip tracking (requires a native mobile capability; MVP relies on Smartcar telemetry and optional manual confirmation).
- An LLM chatbot bolted on for "AI" branding.

## 6. Non-functional requirements

- **Availability:** best-effort hobby-grade; no SLA. The app must degrade cleanly when Smartcar, Mapbox, or AFDC is unreachable.
- **Latency target:** `POST /api/v1/routes/plan` under ~3 s for trips under ~500 mi on the deterministic MVP optimizer. Document actual measured latency in the roadmap deliverables once Phase 4 ships.
- **Accessibility:** keyboard-navigable primary flows (sign-in, Dashboard, Planner, Garage); respect `prefers-reduced-motion`; usable contrast in both themes.
- **Responsive design:** desktop with sidebar, mobile with simplified navigation; neutral light theme with green accents and a dark-mode option.
- **Internationalization:** copy in English for MVP, but all user-facing numbers use the user's unit preference.
- **Observability:** every external-API call logs provider, endpoint, latency, and status; errors go to Sentry (or equivalent) in deployed environments.

## 7. Data integrity requirements

These bind every feature, not just analytics:

- Every stored vehicle reading preserves `source`, provider `recorded_at`, server `received_at`, and the raw unit.
- Simulated / demo data is tagged `is_simulated = true` and never merged with real observations in analytics or ML training sets.
- The planner never fabricates station availability, charger power, or energy measurements. Missing data is shown as missing.
- The manufacturer-estimated remaining range is **not** treated as ground-truth consumption.
- The deterministic energy model stays available as a fallback even after ML ships.

## 8. Privacy and security requirements

- No Smartcar client secret, webhook secret, or Mapbox secret token ever reaches the browser bundle. See [`CLAUDE.md`](../CLAUDE.md) Security section.
- Location history and vehicle identifiers are treated as sensitive. The public demo uses clearly labeled simulated coordinates; no real home address or personal trip history is exposed.
- Every user-owned resource (`vehicles`, `planned_trips`, `observed_trips`, `vehicle_telemetry`) is checked for ownership on read and write.
- Smartcar webhook requests are rejected unless their signature verifies against the stored webhook secret.
- Vehicle command endpoints (lock/unlock/charge start, if ever added) require an explicit per-request authorization and are **not** in MVP scope.
- Users can request account deletion; the server honors it and documents what it removes vs anonymizes.

## 9. Acceptance test matrix

The planner has non-obvious edge cases that must be covered before Phase 4 is considered complete:

- Starting at 100 %, 0 %, and an unknown SoC.
- A destination that requires no charging at all.
- Chargers nearby but incompatible with the vehicle's connectors.
- A charger that is too far away to reach from the current SoC.
- Two shorter charging stops outperforming one long stop.
- Missing station power or pricing information.
- Stale telemetry or a failed external API request (Smartcar, Mapbox, AFDC).
- No possible route satisfying the reserve-SoC requirement — must return "infeasible", not a silent route.

Dashboard and Garage must also handle: first-time connect with no telemetry yet, Smartcar authorization failure, and duplicate-connect attempts.

## 10. Open product questions

Carry these into the roadmap as Phase-level decisions:

- ~~**Canadian charger data.**~~ Resolved 2026-11-16 (Phase 3 F0): AFDC includes Natural Resources Canada's stations when called with `country=all`; a Frederick, MD → Toronto, ON route returned stations on both sides of the border. No second provider is needed. See [`maps-stations-notes.md`](maps-stations-notes.md).
- **Weather provider licensing.** Open-Meteo is free for non-commercial use. Confirm whether Chargewise remains non-commercial before relying on it long-term.
- **Smartcar entitlements.** Confirm the plan's event frequency and request quotas are enough for the Dashboard's freshness target. May require a telemetry-polling fallback.
- **Public demo data.** Decide the exact seeded routes and simulated vehicle for the demo mode in Phase 6.
