# ChargeWaze Master Plan: Remaining Work and Parallel Agent Sessions

Status: **Approved 2026-11-18** (P4/P5 decision tables stay recommendations until P4-F0 / P5-F0) · Baseline: `main` at `314ae9e` (Phase 3 F0–F4 merged) · Owner: project lead

This plan covers everything left after Phase 3 F4: the rest of Phase 3, Phases 4–6, and the Phase 2 carry-over. It does two things:

1. Splits the remaining work into **sub-sessions** (one feature = one branch = one PR = one Claude Code session), each with its dependencies and the files it owns.
2. Defines **how several sessions run at the same time** without merge conflicts or dependency problems (§2), and in what order (§3).

The per-phase plans stay the detailed source for their features: [`phase3_plan.md`](phase3_plan.md) for P3-F5…F9. Phases 4 and 5 each get their own `phaseN_plan.md`, written by their design session (P4-F0, P5-F0) from the outline here. When this plan and a phase plan disagree, the phase plan wins for *what* to build and this plan wins for *when* and *which files*.

Feature IDs are `P<phase>-F<n>`. Phase 3 keeps its existing numbers (F5–F9).

---

## 0. Remaining work at a glance

| Phase | Done | Left |
|---|---|---|
| 2 | F0–F8 code | Manual real-car checks only (P2-R1, P2-R2). Not agent work. |
| 3 | F0–F4 | F5 stations API, F6 charging page, F7 trip-planner map, F8 favorites, F9 close-out. Plus F4's real-key `curl` check. |
| 4 | — | Design, trips schema, energy model, optimizer, `/routes/plan`, planner UI, trip history UI, preferences editor, close-out. |
| 5 | — | Design, observed-trips schema, trip inference, ingestion, plan↔observation matching, analytics API, analytics UI, close-out. |
| 6 | — | Sentry, deployment, demo mode, monitoring, account deletion and privacy, portfolio close-out. |
| ML | — | Post-MVP (roadmap §3). Not scheduled here; gets its own plan after Phase 5 has real trips. |

---

## 1. Can sessions run in parallel? Yes, with four conditions

Two features can be built at the same time when **all four** hold:

| # | Condition | Why |
|---|---|---|
| C1 | **Dependencies merged.** Every feature in its *Depends on* column is merged to `main` before the session starts. | No session codes against an interface that may still change. Contract-only dependencies count as met once the contract text is merged. |
| C2 | **Disjoint files.** The two features' *Owns* lists don't overlap. | No two branches edit the same file, so there is nothing to conflict. |
| C3 | **Lanes respected.** At most one in-flight feature per lane (§2.3): migrations, dependencies, CI workflow, E2E harness. | These files are either a strict chain (Alembic) or a lockfile that can't be merged by hand. |
| C4 | **Isolated runtime.** Each session uses its own worktree, its own databases, and (for E2E) its own ports (§2.4). | Shared databases and ports make one session's test run break or *silently test* another session's code. |

Everything below is built on those four rules. Phase order is still respected where it matters: nothing in Phase 4 calls a Phase 3 endpoint before that endpoint is merged, and a phase is only "complete" when its close-out feature merges. What changes is that **pure, self-contained pieces of a later phase** (energy model, optimizer, trip inference, preferences editor, design docs) start while the earlier phase's UI is still being built.

---

## 2. How to run it

### 2.1 Roles

- **Coordinator session** (one, long-lived, on `main` or its own worktree). Owns this file's status board (§6). Picks the ready set (§3.1), starts feature sessions, relays approvals, and after each merge tells the in-flight sessions to rebase. It does not write feature code.
- **Feature sessions** (one per feature, each in its own git worktree on `feature/<phase>-<slug>`). Each one follows the per-feature workflow in §2.2.
- **You** approve each feature session's plan (CLAUDE.md: substantial features need approval before editing), review PRs, merge, and do the manual real-key and real-car checks.

### 2.2 Per-feature session workflow

Every feature session starts from this brief (the coordinator fills in the blanks):

> You are building **{ID} {title}** from `docs/plan.md` §{section} (and `docs/phase{N}_plan.md` §{section} if it exists). Read `CLAUDE.md` and `docs/engineering-guidelines.md` first.
> Branch: `feature/phase{N}-{slug}` from current `origin/main`.
> **You may edit only the files in your *Owns* list**, plus your own feature section in the phase plan (for "As built" notes). If you need to touch any other file, stop and ask the coordinator.
> Runtime isolation: export the env vars in plan.md §2.4 with slug `{slug}` before running tests.
> Steps: propose a short plan and wait for approval → implement → run the checks in §2.5 → write "As built" notes in your feature section → open a PR (do not merge). Report: PR link, checks run with results, anything left manual.

Feature sessions **never** edit: the §6 status board, another feature's section, the roadmap status line (close-out features only), or any shared hotspot outside the rules in §2.3.

### 2.3 Shared files: hotspot rules and lanes

These files are touched by many features. Each has one rule so parallel branches never collide.

| File(s) | Rule |
|---|---|
| `services/api/app/main.py` (router list) | **Pre-wired by W0** (§3.2): every planned router module is listed now. `_register_v1_routers` skips modules that don't exist yet (`except ImportError`), so later features don't edit this file. |
| `services/api/app/core/config.py`, `services/api/.env.example`, `apps/web/.env.example` | **Pre-sectioned by W0**: one commented block per upcoming feature group (`# ---- energy (Phase 4) ----`, …). A feature adds settings only inside its own block, so hunks never touch. |
| `services/api/alembic/versions/*`, `services/api/alembic/env.py`, `docs/database-design.md` | **Migration lane** (one at a time). The chain is linear; two parallel migrations both pointing at the current head make two heads and break `alembic upgrade head`. Before opening the PR, `uv run alembic heads` must print exactly one head. |
| `apps/web/package.json`, `package-lock.json`, `services/api/pyproject.toml`, `services/api/uv.lock` | **Dependency lane** (one at a time). Lockfiles can't be merged by hand. Dependency-adding features: P3-F6 (`mapbox-gl`), P6-F1 (Sentry). P5-F6 needs none (phase5_plan D5: plain SVG charts). If a lockfile conflicts anyway: take `main`'s lockfile and re-run `npm install` / `uv lock`. |
| `.github/workflows/*` | **CI lane** (one at a time): P3-F9, P6-F3, P6-F4. |
| `apps/web/e2e/support/*`, `apps/web/playwright.config.ts` | **E2E-harness lane**: P3-F7 builds the provider stub; W0 makes ports configurable. Later features only *add* `e2e/*.spec.ts` files. |
| `docs/api-contracts.md` | Each feature edits only its own `§3.x` sections. The endpoint index table (§2) and the error-code table (§1.1): add rows next to your module's rows. If two PRs add rows at the same spot, keep both rows (the only acceptable "conflict"). |
| `services/api/tests/conftest.py` | Don't edit. Put new fixtures in a module-local `conftest.py` (e.g. `tests/trips/conftest.py`). |
| `docs/development-roadmap.md` | Close-out features only (P3-F9, P4-F9, P5-F8, P6-F6). |
| `docs/plan.md` §6 status board | Coordinator only. |
| `docs/phaseN_plan.md` | A feature session edits only its own feature section. |

The four lanes as a list: **migration**, **dependency**, **CI**, **E2E harness**. A feature in a lane holds it from branch creation until merge.

### 2.4 Runtime isolation per session

Each desktop session already gets its own git worktree. What the worktrees still share is the local Postgres container and the dev/E2E ports. Use a per-session slug (e.g. `p4-energy`, with `-` → `_` in database names):

```bash
export TEST_DATABASE_URL="postgresql+psycopg://chargewaze:chargewaze@localhost:5432/chargewaze_p4_energy_test"
```

```bash
export DATABASE_URL="postgresql+psycopg://chargewaze:chargewaze@localhost:5432/chargewaze_p4_energy"
```

```bash
docker exec chargewaze-postgres createdb -U chargewaze chargewaze_p4_energy
```

- `TEST_DATABASE_URL`: pytest creates this database itself and refuses any name not ending in `_test` (`tests/conftest.py`). Verified 2026-11-17 with a scratch database.
- `DATABASE_URL`: used by the Alembic round-trip. **Always set it explicitly**: `services/api/.env` points at hosted Supabase, and `alembic downgrade base` there would drop real data. The database must exist first (`createdb` above; verified 2026-11-17). Drop it when the feature merges: `docker exec chargewaze-postgres dropdb -U chargewaze chargewaze_p4_energy`.
- **E2E (Playwright)** defaults to ports 3100/8765/54399 and reuses already-running servers locally, so a second session on the defaults would silently test the first session's branch. Each session that runs Playwright sets its own ports and database (W0 added the port variables), for example:

```bash
export E2E_WEB_PORT=3200 E2E_API_PORT=8865 E2E_AUTH_PORT=54499 E2E_DATABASE_URL="postgresql+psycopg://chargewaze:chargewaze@localhost:5432/chargewaze_p4_energy_e2e"
```

  The E2E database is created by the suite itself. Sessions pick ports by adding 100 per session (3200/8865/54499, 3300/8965/54599, …).
- **Dev servers** (`npm run dev`, `uv run fastapi dev`) default to 3000/8000. Only one session runs them at a time, or pass another port.

### 2.5 Checks before every PR

From the guidelines and phase 3 plan §5, with the isolation env vars set:

- Backend (`services/api/`): `uv run ruff check`, `uv run mypy app`, `uv run pytest`, `uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head`, `uv run alembic heads` (one head).
- Web (repo root): `npm run lint`, `npm run typecheck`, `npm run test`; regenerate the client after any API change (`uv run --project services/api python services/api/scripts/export_openapi.py && npm run generate --workspace=packages/api-client`).
- Playwright (`npm run test:e2e --workspace=apps/web`) when a UI flow changes, respecting §2.4.
- The PR description lists what ran and the results. Nothing is reported as passing unless it ran in that session.

### 2.6 Merging and rebasing

1. PRs merge **one at a time**, in the order they turn green. You merge (or tell the coordinator to).
2. After each merge, the coordinator updates §6 and tells every in-flight session: `git fetch origin && git rebase origin/main`, then re-run its checks. Because owned files are disjoint, this rebase is normally conflict-free; the only expected conflicts are the "keep both rows" cases in §2.3.
3. If a rebase shows a real conflict in a file outside the session's *Owns* list, the plan was wrong: stop, tell the coordinator, and fix the ownership here before continuing.
4. Newly unblocked features (C1) are started right after the merge that unblocks them.

### 2.7 Starting sessions in the Claude desktop app

- **From the coordinator**: ask it to "start the ready set". It starts one session per ready feature (each in its own worktree, with the §2.2 brief), shows them in the sidebar, and gets a note when each one reports back.
- **By hand**: open a new Code session per feature with worktree isolation on, and paste the §2.2 brief.
- **From a terminal**: `git worktree add ../cw-p4-energy -b feature/phase4-energy-model origin/main`, then run `claude` in that folder with the brief.

Recommended concurrency: **3 feature sessions at a time** (4 at most). Above that, your review time becomes the bottleneck, not the agents.

---

## 3. Dependency graph and schedule

### 3.1 Picking the ready set

A feature is **ready** when C1 holds (all *Depends on* merged), its lane (if any) is free, and its *Owns* list doesn't overlap any in-flight feature. Among ready features, prefer the ones on the critical path (marked ★ in §4): P3-F5 → P3-F6 → P3-F7 → P4-F6 → P4-F9.

```mermaid
flowchart LR
  W0[W0 pre-wiring]
  subgraph P3[Phase 3]
    F5[P3-F5 stations API] --> F6[P3-F6 charging page] --> F7[P3-F7 trip map] --> F9[P3-F9 close-out]
    F6 --> F8[P3-F8 favorites] --> F9
  end
  subgraph P4[Phase 4]
    P4F0[P4-F0 design] --> P4F1[P4-F1 trips schema+read API]
    P4F0 --> P4F2[P4-F2 energy model] --> P4F3[P4-F3 optimizer]
    P4F1 --> P4F4[P4-F4 /routes/plan]
    P4F3 --> P4F4
    P4F4 --> P4F6[P4-F6 planner UI]
    P4F1 --> P4F7[P4-F7 trip history UI]
    P4F5[P4-F5 preferences editor]
    P4F6 --> P4F9[P4-F9 close-out]
    P4F7 --> P4F9
    P4F4 --> P4F8[P4-F8 per-vehicle baseline]
  end
  subgraph P5[Phase 5]
    P5F0[P5-F0 design] --> P5F2[P5-F2 inference engine]
    P5F0 --> P5F1[P5-F1 observed schema]
    P5F1 --> P5F3[P5-F3 ingestion]
    P5F2 --> P5F3
    P5F3 --> P5F4[P5-F4 matching]
    P5F1 --> P5F5[P5-F5 analytics API] --> P5F6[P5-F6 analytics UI]
    P5F3 --> P5F7[P5-F7 history+observations UI]
    P5F4 --> P5F8[P5-F8 close-out]
    P5F6 --> P5F8
    P5F7 --> P5F8
  end
  subgraph P6[Phase 6]
    P6F1[P6-F1 Sentry]
    P6F3[P6-F3 deploy] --> P6F4[P6-F4 monitoring]
    P6F2[P6-F2 demo mode]
    P6F5[P6-F5 account deletion+privacy]
    P6F6[P6-F6 portfolio close-out]
  end
  F5 --> P4F4
  F7 --> P4F6
  P4F1 --> P5F1
  P4F4 --> P5F4
  P4F0 --> P5F0
  P4F7 --> P5F7
  P4F9 --> P6F3
  P5F1 --> P6F5
  P5F8 --> P6F2
  F8 --> P5F1
  P6F2 --> P6F6
  P6F4 --> P6F6
  P6F5 --> P6F6
  P6F1 --> P6F6
```

(`F8 → P5-F1` is a migration-lane ordering, not a code dependency.)

### 3.2 Suggested waves

A wave is a set of sessions that run together. Waves are a guide; the real rule is §3.1, so a feature starts as soon as it's ready even if its wave hasn't "begun". **L** = holds a lane.

| Wave | Sessions in parallel | Lanes held | Notes |
|---|---|---|---|
| **W0** | **W0 pre-wiring** (alone, small) | CI-free; touches E2E harness | Must merge first. Contents below. |
| **W1** | P3-F5 ★ · P4-F0 · P4-F5 | — | Backend API, a docs-only design, and a Profile-page UI. No shared files. |
| **W2** | P3-F6 ★ · P4-F1 · P4-F2 · P5-F0 | dependency (F6), migration (P4-F1) | F6 is web-only; P4-F1 is DB+trips module; P4-F2 is a pure Python module; P5-F0 is docs-only. |
| **W3** | P3-F7 ★ · P4-F3 · P4-F7 · P5-F2 | E2E harness (F7) | F7 owns `/trips` planner page; P4-F7 owns the separate saved-trips pages. P4-F3 and P5-F2 are pure. |
| **W4** | P3-F8 · P4-F4 ★ | migration (F8) | P4-F4 composes F5 + P4-F1 + P4-F3. |
| **W5** | P3-F9 · P4-F6 ★ · P5-F1 | CI (F9), migration (P5-F1) | Phase 3 closes here. |
| **W6** | P4-F9 · P5-F3 · P5-F5 · P6-F1 | dependency (P6-F1) | Phase 4 closes here. |
| **W7** | P5-F4 · P5-F6 · P6-F3 · P4-F8 | CI (P6-F3), migration (P4-F8) | P5-F6 adds no dependency (phase5_plan D5), so it can start as soon as P5-F5 merges. P4-F8 is a Phase 4 follow-up (added by P4-F0); it can start any time after P4-F4 once the migration lane is free. **As run:** P4-F8 ran in W6, and P5-F7 moved up from W8 into its slot. |
| **W8** | P5-F7 · P6-F4 · P6-F5 | CI (P6-F4), migration (P6-F5) | **As run:** P5-F7 ran in W7, so P5-F8 (ready once W7 merged) joins W8. |
| **W9** | P5-F8 · then P6-F2 | — | Phase 5 closes here. P6-F2 seeds every table, so it waits for P5-F8. |
| **W10** | P6-F6 | — | MVP done. |

That's 11 waves for 31 sessions (W0 + 30 features; one at a time would be 31 steps). The critical path (W0 → F5 → F6 → F7 → P4-F4/F6 → P4-F9) sets the minimum; adding sessions beyond 3–4 doesn't shorten it.

**W0 pre-wiring** (one small PR, branch `feature/phase3-parallel-prewiring`, includes this plan):
- `app/main.py`: `V1_ROUTER_MODULES` lists `app.modules.stations.router`, `app.modules.trips.router`, `app.modules.trips.observed_router` and `app.modules.analytics.router` up front. Registration now skips only a router module (or package) that doesn't exist; a router whose own imports fail raises instead of silently dropping its endpoints (`tests/test_router_registration.py`). `DELETE /me` (P6-F5) goes in the existing users router.
- `app/core/config.py` and `services/api/.env.example`: empty commented blocks for stations API, energy, planner, trips, analytics, demo, privacy (observability already existed). `apps/web/.env.example`: demo-mode and observability blocks. Two branches adding settings in neighbouring blocks merge cleanly (checked with `git merge-file`).
- `apps/web/e2e/support/e2e-servers.ts`, `e2e-auth.mjs`, `playwright.config.ts`: read `E2E_WEB_PORT` / `E2E_API_PORT` / `E2E_AUTH_PORT` with today's values as defaults; a non-numeric value fails fast.
- `docs/engineering-guidelines.md`: link to this plan for parallel sessions.
- Checks run 2026-11-18: ruff, mypy, pytest (554 passed), Alembic round-trip + single head, lint/typecheck/Vitest (105 passed), Playwright 4/4 on default ports and 4/4 on ports 3200/8865/54499 with a separate E2E database.

---

## 4. Feature catalogue

Each entry is a session brief: scope, dependencies, files it **owns**, lane, and acceptance. "Owns" lists directories or files; a session may create anything inside an owned directory. Sizes: S ≈ half a day, M ≈ 1–2 days, L ≈ 3+ days of agent+review time.

### 4.0 Phase 2 carry-over (manual, you)

| ID | Task | Blocks |
|---|---|---|
| P2-R1 | E3 tail: trigger an event from the bZ after disconnecting; confirm nothing is stored. | Phase 2 "complete" status only. |
| P2-R2 | Measure event cadence over a few days; record it and the freshness threshold in the roadmap risk table. | P4-F0 should use the measured threshold for the stale-SoC rule; until then it uses `telemetry_stale_after_minutes = 15`. |
| P3-R1 | F4 acceptance: real-key `curl` for `/places/suggest?q=Toronto` and Frederick → Toronto directions. | P3-F9 close-out. |

### 4.1 Phase 3 (details in [`phase3_plan.md`](phase3_plan.md) §4)

**P3-F5 ★ Stations API + cache** · M · Depends: F1, F3 (merged) · Lane: — · Branch `feature/phase3-stations-api`
- Owns: `services/api/app/modules/stations/{service,router,schemas}.py`, `tests/stations/` (new), `docs/api-contracts.md` §3.10, §3.16, §3.17, phase3_plan §F5, config block "stations API".
- Acceptance: phase3_plan F5 tests list (E2, E5, E6). Exposes a service function `search_along_route(...)` that P4-F4 will call directly (not over HTTP); its signature is noted in "As built".

**P3-F6 ★ Map foundation + Charging page** · L · Depends: P3-F4, P3-F5 · Lane: dependency (`mapbox-gl`) · Branch `feature/phase3-charging-page`
- Owns: `apps/web/components/map/`, `components/places/`, `components/stations/`, `app/(app)/charging/` (incl. `[id]`), `lib/polyline.ts`, `lib/stations.ts`, `lib/geo-format.ts`, matching `tests/*`, `apps/web/package.json`, `package-lock.json`.
- Acceptance: phase3_plan F6. `map-view.tsx` must already accept `routes`/`selectedRouteIndex` (F7 and P4-F6 use it without changing its props).

**P3-F7 ★ Trip Planner map** · L · Depends: P3-F6 · Lane: E2E harness · Branch `feature/phase3-trip-planner-map`
- Owns: `app/(app)/trips/page.tsx` and `app/(app)/trips/_components/` (new), `lib/trip-url.ts` (new), `e2e/support/provider-stub.mjs` (new), `e2e/support/e2e-servers.ts`, `playwright.config.ts`, `e2e/trip-planner.spec.ts`, matching unit tests.
- May **use** but not edit F6's components. If a change is needed in `components/stations/*` or `components/map/*`, it's a small follow-up PR after F7, not part of F7.
- Acceptance: phase3_plan F7 (E1 via Playwright on the stub).

**P3-F8 Favorite stations** · S · Depends: P3-F5, P3-F6; must start after P3-F7 merges (both touch the station card) · Lane: migration · Branch `feature/phase3-favorites`
- Owns: new migration, `alembic/env.py` import if needed, `modules/stations/favorites*.py` (or additions to stations router/service, since F5 is merged by then), `components/stations/favorite-toggle.tsx`, edits to `components/stations/station-card.tsx` and `charging/[id]` page, `docs/database-design.md`, contract section for favorites, `e2e/favorites.spec.ts`.
- Acceptance: phase3_plan F8.

**P3-F9 Phase 3 close-out** · S · Depends: P3-F7, P3-F8, P3-R1 · Lane: CI · Branch `feature/phase3-closeout`
- Owns: `.github/workflows/ci.yml` (AFDC sentinel), optional live workflow, `e2e/charging.spec.ts`, docs listed in phase3_plan F9, roadmap status line.
- Acceptance: phase3_plan F9; E1–E7 all checked. Also records Directions and along-route latency (input for P4).

### 4.2 Phase 4: Charging-aware trip planner

Detailed plan: [`phase4_plan.md`](phase4_plan.md), written by P4-F0. **Settled 2026-11-18 in phase4_plan.md §2**: D1, D2, D3, D4 and D6 changed from the recommendations below (catalog-only baseline with a per-vehicle follow-up P4-F8; no flat-curve fallback; route-ordered DP, ADR 0008; road-factor detour; `saved_at` + `POST /trips/{id}/save`), and D10–D16 were added. The table below is kept as the original recommendation; the phase plan wins. The decisions it had to settle (with my recommendation) were:

| # | Decision | Recommendation |
|---|---|---|
| P4-D1 | Baseline consumption | A per-vehicle `baseline_kwh_per_km` setting with a catalog default for the bZ, user-editable. Speed/elevation/temperature adjustments off in v1; the response says which factors were used. |
| P4-D2 | Charging curve source | The `vehicle_specs.charging_curve` piecewise list. Fill the bZ default from a cited public source in `spec_catalog.py`. If a vehicle has no curve, fall back to a flat `min(station kW, vehicle max kW)` and flag the plan `charging_curve: "assumed_flat"`. |
| P4-D3 | Algorithm | Architecture §6.3 bounded enumeration first (max 3 stops), candidates = compatible stations along the selected route, ordered by `distance_along_route_km`. Graph search (§6.5) is a stretch inside P4-F3 only if the matrix passes first. |
| P4-D4 | Detour time | Estimate as `2 × distance_from_route_km / 60 km/h`, labeled an estimate. No extra Mapbox calls per candidate in v1 (cost, latency). |
| P4-D5 | One plan or several (contract §6 open question) | One best plan plus `alternatives: []` reserved in the schema, empty in v1. |
| P4-D6 | Persistence | `/routes/plan` stores every *feasible or infeasible* plan in `planned_trips` (`status = 'planned'`) and returns `trip_id`; "Save" in the UI is just "keep" vs `DELETE /trips/{id}`. Avoids trusting a client-sent plan payload. |
| P4-D7 | Stale SoC | If cached state is older than `telemetry_stale_after_minutes` (or missing), `/routes/plan` without `starting_soc_override` returns `VALIDATION_ERROR` with `details.reason = "STARTING_SOC_REQUIRED"`. |
| P4-D8 | Hand-checked sample trips | Three fixture trips (no-charge local, one-stop ~250 mi, Frederick → Toronto multi-stop), each with a spreadsheet-style worked calculation in the phase plan. |
| P4-D9 | Interfaces between P4-F2/F3/F4 | Defined as Python signatures in the phase plan so F2 and F3 can be reviewed against them. |

**P4-F0 Phase 4 design + contracts** · S · Depends: — · Lane: — · Branch `feature/phase4-design`
- Owns: `docs/phase4_plan.md` (new), `docs/api-contracts.md` §3.7–3.9 (+ `DELETE /trips/{id}` if P4-D6 holds), `docs/architecture.md` §6, a new ADR only if the algorithm differs from §6.3.
- Docs only, no code. Acceptance: you approve P4-D1…D9.

**P4-F1 Trips schema + read API** · M · Depends: P4-F0 · Lane: migration · Branch `feature/phase4-trips-schema`
- Owns: `services/api/app/modules/trips/` (new: `models.py`, `schemas.py`, `service.py` with `create_planned_trip()`, `router.py` with `GET /trips`, `GET /trips/{id}`, `DELETE /trips/{id}`), new migration (`planned_trips`, `planned_stops`, RLS on), `alembic/env.py` import, `tests/trips/`, `docs/database-design.md` §3.7–3.8.
- Acceptance: RLS test, round-trip, ownership 404s, cursor pagination, cascade on vehicle/profile delete.

**P4-F2 Energy model** · M · Depends: P4-F0 · Lane: — · Branch `feature/phase4-energy-model`
- Owns: `services/api/app/modules/energy/` (new, pure functions: segment energy, arrival SoC, feasibility, charging-time integral over the piecewise curve), `tests/energy/`, config block "energy", and the bZ curve/baseline in `modules/vehicles/spec_catalog.py`.
- No DB, no HTTP. Acceptance: unit tests incl. property tests (charging time is monotonic in target SoC; zero energy → zero time; flat-curve fallback), and the three P4-D8 sample numbers to within rounding.

**P4-F3 Optimizer** · L · Depends: P4-F2 · Lane: — · Branch `feature/phase4-optimizer`
- Owns: `services/api/app/modules/routing/optimizer.py` and `routing/plan_types.py` (new), `tests/routing/test_optimizer*.py`.
- Pure: input = route distance/duration profile + candidate stations (with `distance_along_route_km`, `distance_from_route_km`, connectors, `max_power_kw`) + vehicle + preferences + starting SoC; output = plan or infeasibility with reason code and `limiting_segment`.
- Acceptance: every optimizer row of the PRD §9 matrix as a unit test (100 %/0 % SoC, no charge needed, incompatible chargers, unreachable charger, two short stops beat one long stop, missing power, reserve unreachable → infeasible). Latency of the pure search on the Frederick → Toronto fixture < 500 ms.

**P4-F4 ★ `POST /routes/plan`** · M · Depends: P3-F5, P4-F1, P4-F3 · Lane: — · Branch `feature/phase4-plan-endpoint`
- Owns: `modules/routing/planner_service.py` (new), the `/routes/plan` route in `modules/routing/router.py`, `routing/schemas.py` additions, `tests/routing/test_plan_endpoint.py`, config block "planner".
- Calls: Mapbox adapter (directions), `stations.service.search_along_route`, `vehicles` service (specs + latest state), `users` service (preferences), `energy`, `optimizer`, `trips.service.create_planned_trip`. All through service functions, no cross-module queries.
- Acceptance: stale-SoC rule (P4-D7), preference fallback, upstream errors (Mapbox/AFDC down → envelope), ownership, `trip_id` returned, plan persisted, latency measured on fixtures.

**P4-F5 Preferences editor** · S · Depends: — (uses existing `PATCH /me/preferences`) · Lane: — · Branch `feature/phase4-preferences-editor`
- Owns: `apps/web/app/(app)/profile/` and `apps/web/components/profile/` (new), `tests/profile*.test.tsx`.
- The Profile page shows preferences read-only today. Make units, reserve SoC, charge-to, max detour and preferred networks editable (PRD §4.6), with validation matching the DB checks.
- Acceptance: Vitest for form validation and save/error states; units change is visible on Dashboard after reload.

**P4-F6 ★ Planner UI** · L · Depends: P3-F7, P4-F4 · Lane: — · Branch `feature/phase4-planner-ui`
- Owns: `app/(app)/trips/_components/plan-*` (new files), edits to `app/(app)/trips/page.tsx`, `lib/plan-format.ts` (new), `e2e/planner.spec.ts`, matching unit tests. Extends the F7 provider stub only by adding fixture files (no harness changes).
- Acceptance: stops on the map with arrival/departure SoC, charging time per stop, total time, detour, prominent infeasible state with reason, stale-SoC prompt for `starting_soc_override`, "re-plan with preferences" panel. Removes the F7 "planned in the next release" note.

**P4-F7 Trip history UI** · M · Depends: P4-F1 · Lane: — · Branch `feature/phase4-trip-history`
- Owns: `app/(app)/trips/saved/` and `app/(app)/trips/[id]/` (new routes), `components/trips/` (new), matching tests.
- Acceptance: list with pagination and empty state, detail page rendering a stored `plan_payload` (static map + stops), delete. Doesn't touch `trips/page.tsx`; a link from the planner page to "Saved trips" is added by P4-F6.

**P4-F8 Per-vehicle consumption baseline** (follow-up from P4-F0 D1) · M · Depends: P4-F2, P4-F4 · Lane: migration · Branch `feature/phase4-vehicle-baseline`
- Owns: new migration (`vehicle_specs.baseline_kwh_per_km`, nullable, `CHECK (> 0 AND <= 1)`), `alembic/env.py` if needed, `modules/vehicles/{models,schemas,garage}.py` (column, `PATCH /vehicles/{id}/specs` field, `GET /vehicles/{id}` output), the baseline-resolution lines in `modules/routing/planner_service.py` (P4-F4 is merged by then), `apps/web/components/garage/specs-panel.tsx`, `apps/web/lib/vehicle-specs.ts`, matching tests (`tests/test_vehicle_settings.py`, `tests/test_vehicle_garage.py`, `tests/routing/test_plan_endpoint.py` additions, `apps/web/tests/specs-editor.test.tsx`, `vehicle-specs.test.ts`), `docs/api-contracts.md` §3.4 and §3.4a, `docs/database-design.md` §3.4, phase4_plan §F8.
- Acceptance: phase4_plan F8. Vehicles without a catalog entry can be planned once the user enters a baseline; plans report `baseline_source: "vehicle"`. Not a dependency of P4-F9.

**P4-F9 Phase 4 close-out** · S · Depends: P4-F6, P4-F7 · Lane: — · Branch `feature/phase4-closeout`
- Owns: acceptance-matrix mapping table in `phase4_plan.md`, roadmap status, latency record for a ~500 mi trip, real-key manual run with screenshot.
- Acceptance: every PRD §9 case maps to a named passing test; three hand-checked trips match.

### 4.3 Phase 5: History and analytics

Detailed plan: [`phase5_plan.md`](phase5_plan.md), written by P5-F0. **Settled 2026-11-18 in phase5_plan.md §2**: D1, D3, D4 and D5 changed from the recommendations below (observed trips are driving *legs* with no `odometer_inferred` energy; confirm/reject on any leg; matching by time + odometer distance over a chain of legs, since there is no location; no chart library), D2 changed to a background trigger after each webhook at the user's request ([ADR 0009](adr/0009-in-process-post-ingest-hook-for-trip-inference.md)), and D8–D14 were added (quality rules, backtests for every leg, capacity, simulated data, aggregates, buckets/time zone, in-progress legs). The table below is kept as the original recommendation; the phase plan wins. The Owns lists below are updated to match.

| # | Decision | Recommendation |
|---|---|---|
| P5-D1 | Trip inference | Segment telemetry into driving intervals: odometer increases between readings with no `charging_status = charging`; a gap > N minutes or a charge ends a trip. Energy = ΔSoC × usable capacity, `energy_source = soc_inferred`. Quality from reading density and SoC resolution. |
| P5-D2 | When inference runs | On demand per vehicle (idempotent, keyed on telemetry window) from a service function, called by a script and by a `POST /trips/observed/recompute` endpoint. No queue or cron in MVP (architecture §7). |
| P5-D3 | Manual confirmation | `POST /trips/observed/{id}/confirm` and `/reject` for `low` quality rows (closes contract §6 question 3). |
| P5-D4 | Plan ↔ observation matching | Same vehicle, observed start within ±2 h of plan creation or departure, start/end within 5 km of plan origin/destination. One-to-one. |
| P5-D5 | Chart library | Recharts (common, React-native API) unless P5-F0 finds it too heavy; decided in the phase plan. |
| P5-D6 | CSV export | `GET /analytics/trips.csv` with the same filters as the UI table. |
| P5-D7 | Vehicle location | Still not requested (Phase 3 D12); trips get distance from odometer, not paths. |

**P5-F0 Phase 5 design + contracts** · S · Depends: P4-F0 (needs the `planned_trips` shape) · Lane: — · Branch `feature/phase5-design`
- Owns: `docs/phase5_plan.md` (new), `docs/adr/0009-in-process-post-ingest-hook-for-trip-inference.md` (new), contract §3.11–3.11b and §3.18–3.18e (+ endpoint-index rows), this §4.3.

**P5-F1 Observed trips + predictions schema** · S · Depends: P5-F0, P4-F1; after P3-F8 in the migration lane · Lane: migration · Branch `feature/phase5-observed-schema`
- Owns: new migration (`observed_trips`, `prediction_results` as in phase5_plan §3 F1, RLS), `modules/trips/observed_models.py` (new), `alembic/env.py`, `tests/trips/test_observed_models.py`, `docs/database-design.md` §3.9–3.10.

**P5-F2 Trip inference engine** · M · Depends: P5-F0 · Lane: — · Branch `feature/phase5-trip-inference`
- Owns: `modules/trips/inference.py` (new, pure), `tests/trips/test_inference.py`, telemetry series fixtures in `fixtures/telemetry/` (new subfolder of the existing `fixtures/`).
- Acceptance: the five phase5_plan §5 examples exactly; driving vs charging separation, gaps, out-of-order readings, quality flags, simulated rows excluded.

**P5-F3 Observed-trip ingestion + confirm** · M · Depends: P5-F1, P5-F2 · Lane: — · Branch `feature/phase5-trip-ingestion`
- Owns: `modules/trips/observed_service.py`, `modules/trips/observed_router.py` (new; already registered by W0), `modules/vehicles/ingest_hooks.py` (new, ADR 0009), the `BackgroundTasks` scheduling in `modules/vehicles/webhook_router.py`, `IngestResult` fields and a read-only `list_readings` function in `modules/vehicles/telemetry_service.py`, `scripts/infer_trips.py`, config block "trips", `tests/trips/test_observed_*.py`, `tests/test_ingest_hooks.py`.
- Acceptance: a webhook that inserts odometer/SoC/charging readings creates legs with no other call; a hook failure still answers Smartcar 200; idempotent re-runs; frozen legs survive `full`; confirm/reject flow; ownership.

**P5-F4 Plan ↔ observation matching + backtests** · S–M · Depends: P5-F3, P4-F4 · Lane: — · Branch `feature/phase5-prediction-matching`
- Owns: `modules/trips/matching.py`, writes `prediction_results` (plan and leg rows), the one call site in `observed_service.py`, the `observation` field in `GET /trips/{id}` (`modules/trips/service.py`, `modules/trips/router.py`; P4-F1 is merged by then), `tests/trips/test_matching.py`.

**P5-F5 Analytics API + CSV** · M · Depends: P5-F1 · Lane: — · Branch `feature/phase5-analytics-api`
- Owns: `services/api/app/modules/analytics/` (new), `modules/trips/observed_queries.py` (new, read-only functions analytics calls, so it doesn't query trips tables itself), `tests/analytics/`, config block "analytics".
- Acceptance: day/week/month buckets in a given time zone, MAE and bias, `include_simulated` default false with the excluded count, CSV equals JSON rows, injection escaping, export cap, units stay raw in the API.

**P5-F6 Analytics UI** · L · Depends: P5-F5 · Lane: — (no chart library, phase5_plan D5) · Branch `feature/phase5-analytics-ui`
- Owns: `app/(app)/analytics/`, `components/analytics/` (new, including `charts/`), `e2e/analytics.spec.ts`, tests.
- Acceptance: trends, estimated vs observed, per-trip quality flags, CSV download, simulated data never mixed silently. Adding any chart dependency needs a new approval and the dependency lane.

**P5-F7 History with observations** · M · Depends: P5-F3, P4-F7 · Lane: — · Branch `feature/phase5-history-observations`
- Owns: `components/trips/observation-*` (new), edits to `app/(app)/trips/[id]/` and `trips/saved/` pages (P4-F7 is merged by then), confirm/reject UI, tests.

**P5-F8 Phase 5 close-out** · S · Depends: P5-F4, P5-F6, P5-F7 · Lane: — · Branch `feature/phase5-closeout`
- Real trips produce `observed_trips` rows without manual help; record the measured cadence and quality mix; phase5_plan §6 mapping; roadmap status; Playwright analytics flow.

### 4.4 Phase 6: Production and portfolio

Phase 6 has no design session of its own; each feature's session proposes its plan for approval as usual. Deployment needs your accounts, so those sessions prepare config and docs and you do the clicks.

**P6-F1 Sentry** · S · Depends: — (scheduled W6 to keep the dependency lane free earlier) · Lane: dependency · Branch `feature/phase6-sentry`
- Owns: `app/core/observability.py` (new) + one init call in `app/main.py` (declared exception to the W0 rule), `apps/web/sentry.*.config.ts`, `next.config.ts`, both lockfiles, config block "observability". Scrubs user IDs and coordinates (architecture §9).
- As built (2026-11-19):
  - SDKs: `sentry-sdk[fastapi]` 2.75.0, `@sentry/nextjs` 11.15.0 (lockfile changes are additions only). Coordinator-approved additions to the Owns list: `apps/web/instrumentation.ts`, `apps/web/instrumentation-client.ts` (Next 16 loads Sentry only through these), `services/api/tests/test_observability.py`, `apps/web/tests/sentry-scrub.test.ts`.
  - Off by default: the backend starts Sentry only with `SENTRY_DSN_BACKEND` set, never under `APP_ENV=test` or pytest (so a developer `.env` with a DSN can't send from the suite). The web app starts it only with `NEXT_PUBLIC_SENTRY_DSN` set and never under `NODE_ENV=test`. Tracing defaults to 0 (errors only); no Session Replay.
  - Scrubbing: one rule set in `app/core/observability.py` and `apps/web/sentry.shared.config.ts`, run in `beforeSend`, `beforeSendTransaction` and `beforeBreadcrumb`. It drops `event.user`, request cookies, bodies and query strings; strips the query from any `url`/`to`/`from` value; filters keys for IDs (`user_id`, `vehicleId`, …), coordinates (`lat`, `origin_lon`, `destLat`, `coordinates`, `polyline`, …) and credentials; and replaces UUIDs, coordinate pairs and `lat=`/`lat:` values in free text and serialized stack locals. Coordinates are removed, not rounded (stricter than the guidelines' "round"). Stack-frame locals are never sent (`include_local_variables=False`; `includeLocalVariables: false` on the web server), because `repr()` of objects such as `Settings` would carry secrets. Any key containing `polyline` or `geometry` (e.g. `geometry_polyline6`) is filtered, as are such assignments in serialized text (Codex review fixes on PR #46).
  - `next.config.ts` wraps the config in `withSentryConfig` with `telemetry: false`; source maps upload and a release is created only when `SENTRY_AUTH_TOKEN` is set at build time.
  - Manual (you): create the Sentry projects (backend + web); set `SENTRY_DSN_BACKEND` and `NEXT_PUBLIC_SENTRY_DSN` in the deploy secret stores (optionally `SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN` for source maps); in each project's Security & Privacy settings turn on the Data Scrubber and "Prevent Storing of IP Addresses".

**P6-F2 Demo mode + seed** · M · Depends: P5-F8 · Lane: — · Branch `feature/phase6-demo-mode`
- Owns: `services/api/scripts/seed_demo.py` (db-design §5), a demo/live gate (`APP_PUBLIC_DEMO_MODE` + allowlist) in `modules/auth/`, a demo banner component, tests that no real row is returned in demo mode.
- As built (2026-11-20):
  - **⚠ Set `LIVE_ALLOWED_USER_IDS` on Render before this deploys.** It holds the owner's Supabase user ID; several IDs are comma-separated. With `APP_ENV=production` an empty list fails closed: every signed-in request gets `403 FORBIDDEN` (`LIVE_ACCESS_RESTRICTED`), the owner's included. `render.yaml` lists it as `sync: false`, and `deployment.md` §3, §8 and §13.1 say so.
  - **Gate.** `app/modules/auth/access.py`, called by `get_current_user` once the token verifies, so every signed-in route is gated with no router changes. Webhooks and `/health` aren't gated. `get_current_user` now also depends on `get_settings` and `get_db`; the session is lazy and live mode never queries.
    - **Live mode:** only `LIVE_ALLOWED_USER_IDS` (Supabase UUIDs; anything else fails settings validation). An empty list means open in development/test and nobody in production.
    - **Demo mode:** first, any real vehicle in the database gives `503 DEMO_MODE_UNSAFE` on every request (one `EXISTS` on `vehicles`; telemetry, plans and legs all cascade from a vehicle). Then every non-GET is refused with `403 FORBIDDEN` (`DEMO_READ_ONLY`), except `POST /routes/plan`, `/routes/directions` and `/stations/along-route`.
    - Settings refuse `APP_PUBLIC_DEMO_MODE=true` together with `SMARTCAR_CLIENT_SECRET` or `SMARTCAR_MANAGEMENT_TOKEN`.
  - **Demo access: one shared, seeded account (D1).** `NEXT_PUBLIC_DEMO_MODE`, `NEXT_PUBLIC_DEMO_EMAIL` and `NEXT_PUBLIC_DEMO_PASSWORD` (public on purpose) show **Explore the demo** on the sign-in page (`components/demo/demo-sign-in.tsx`). `components/demo/demo-banner.tsx` sits in `app/(app)/layout.tsx`. In demo mode Analytics includes simulated data by default, and `simulated=0` leaves it out (`components/analytics/params.ts`).
  - **Seed.** `scripts/seed_demo.py --user-id UUID --target HOST/DB [--now ISO]`. It reads the process environment only, never `services/api/.env`. It refuses (exit 2) without `APP_PUBLIC_DEMO_MODE=true`, with a `--target` that doesn't match the URL's host/database, or when any of `vehicles`, `vehicle_telemetry`, `planned_trips` or `observed_trips` holds an `is_simulated = false` row. Re-running replaces the account's data (and clears a tombstone) in one transaction. On a Supabase database it also installs `chargewaze_demo_auth_guard`, a `BEFORE UPDATE` trigger on `auth.users` that refuses changes to the demo user's password, email and phone. It refuses to seed if `--user-id` isn't an auth user or the trigger can't be created. This is the fix for Codex's P1 on #73: Supabase's "Secure password change" only applies to sessions older than 24 hours, so any visitor could have changed the shared password. The SQL is tested against a stand-in `auth.users` table, not a real Supabase project. What it writes:
    - A simulated 2026 Toyota bZ XLE AWD with catalog specs.
    - About 28 days of `source = 'simulated'` odometer, SoC, range and charging readings (about 3,300 rows): weekday commutes, Wednesday errands, weekend drives and home L2 charging.
    - A Frederick → Toronto road trip four days before `--now`. Its plan comes from the real `plan_trip()` over the recorded `fixtures/mapbox` and `fixtures/stations/afdc` files through `httpx.MockTransport` (no network), starting at 90 % and saved. The car then drives the plan, charging at its 3 stops, plus a day in Toronto and an unplanned drive home.
    - `run_for_vehicle(full=True)` then builds about 62 legs (high, medium and low, the low ones needing review), matches the plan to its 4 legs (+7 kWh, −1.3 pts arrival in the local run), and writes the backtests.
    - Values come from a fixed RNG seed, so the same `--now` gives the same rows. PRD §10 "Public demo data" is closed.
  - **Tests.** `tests/demo/test_access.py` (16):
    - Allowlist parsing, the Smartcar-secret validator, live open, fail-closed and allowlisted cases.
    - Every mutating route in the OpenAPI document refused in demo mode except the three planning routes.
    - **No real row returned:** with a real vehicle, telemetry and owner present, every read as either user is 503 and never contains the real ID.
    - Every vehicle, plan, leg and backtest ID that the demo user's reads return is `is_simulated`.

    `tests/demo/test_seed_demo.py` (10): only simulated rows; the plan matched through the real pipeline with all three quality flags; repeatable and replaces the account; refuses real data, live mode and a wrong `--target`; the auth guard refuses every credential column for the demo user but not sign-in updates or other users, and a `--user-id` missing from `auth.users` is refused. `apps/web/tests/demo-mode.test.tsx` (9): env flags, banner, demo sign-in success and failure, the analytics default both ways.
  - **Approved Owns additions:** `app/modules/auth/access.py`; `get_current_user` in `auth/dependencies.py`; `services/api/tests/demo/`; `apps/web/components/demo/`, `app/(app)/layout.tsx`, `app/(auth)/sign-in/page.tsx`, `lib/env.ts`, `components/analytics/params.ts`, `tests/demo-mode.test.tsx`; `render.yaml`; `docs/deployment.md` §3, §8, §11 and new §13; `docs/api-contracts.md` §1 and §1.1; `docs/architecture.md` §7.2; `docs/product-requirements.md` §10. Two unavoidable lines outside the config block: the `uuid` and pydantic validator imports at the top of `config.py`.
  - **Browser check (local, auth stub plus provider stub, demo API and web on 8865/3200).**
    - Sign-in showed the demo card, and **Explore the demo** opened the Dashboard with the banner and the simulated bZ (stale-data warning, 62 %).
    - Saved trips → Frederick → Toronto showed "Completed, 4 drives matched" with the planned vs actual table.
    - Analytics opened with simulated data included and a working "Show real trips only" (`simulated=0`).
    - Planning Frederick → Toronto asked for the stale starting SoC, then returned 4 stops; the new plan was stored `is_simulated = true`.
    - Deleting the saved trip got a 403 from the API and the trip stayed. But the dialog shows the trips UI's generic "Couldn't delete the trip. Please try again." rather than the read-only message. Follow-up: map `FORBIDDEN`/`DEMO_READ_ONLY` to the API message in the web write actions (`components/*/actions.ts`; outside this feature's files).
  - **Not done:** no Playwright spec for demo mode, because the harness runs one live-mode API and a demo spec needs E2E-harness lane changes.
  - **Manual (you):**
    1. Set `LIVE_ALLOWED_USER_IDS` on the production API.
    2. For a public demo, follow `deployment.md` §13.2: a second Supabase project (sign-ups off, Secure email change on, a demo user); migrate and seed its database; a second Render service with `APP_PUBLIC_DEMO_MODE=true` and no Smartcar secrets; a second Vercel project with the three `NEXT_PUBLIC_DEMO_*` values.
    3. Optionally re-run the seed daily.

**P6-F3 Deployment** · M · Depends: P4-F9 · Lane: CI · Branch `feature/phase6-deploy`
- Owns: deployment config files (Render/Fly, Vercel), a deploy workflow, `docs/deployment.md` (new). Manual: you create the accounts and secrets.

**P6-F4 Monitoring + rate limits** · M · Depends: P6-F3 · Lane: CI · Branch `feature/phase6-monitoring`
- Owns: request metrics/logging for planner latency and webhook success rate, per-user rate limit on `/routes/plan` and `/places/suggest` (contract §6 question 2), alert config, production smoke workflow.

**P6-F5 Account deletion + privacy** · M · Depends: P5-F1 · Lane: migration (consent table) · Branch `feature/phase6-account-privacy`
- Owns: `DELETE /me` (+ contract), Smartcar disconnect for each vehicle on deletion, a consent record table, retention job script, Profile "Delete account" UI in `components/profile/` (P4-F5 is merged by then), tests for cascades across every user-owned table.
- As built (2026-11-19):
  - `DELETE /api/v1/me` → 204, idempotent (api §3.1b). One transaction: insert an `account_deletions` tombstone, lock the user's vehicles (`FOR UPDATE`, the ingest lock), revoke each Smartcar connection (incl. ones owed from an earlier disconnect), delete `profiles` (every user-owned table cascades in the database). After the commit, delete the Supabase auth user through a new admin adapter (`app/integrations/supabase/admin.py`, service-role key).
  - Failure policy: deletion never waits on Smartcar or Supabase. Failed revocations and an unfinished auth-user deletion are stored on the tombstone and retried by the retention job. Revocations give up after 30 days (logged); the cars' webhooks are dropped meanwhile because the vehicles are gone.
  - Connect vs deletion (PR #69 review): both take a per-account `pg_advisory_xact_lock` (`users.service.lock_account`) before reading anything, so a vehicle Connect stores is either revoked by the deletion or Connect sees the tombstone and revokes what it just authorized. Tombstone age counts from `auth_user_deleted_at`, not `requested_at`, so a token issued just before a late auth deletion has expired before the tombstone goes.
  - No resurrection: `get_or_create_profile` answers 401 for a tombstoned user, so a still-valid JWT can't recreate the profile. Webhooks for deleted vehicles get `accepted: false`, refresh ingest and post-ingest hooks write nothing (tested).
  - Consent: append-only `consent_records` (`privacy_notice` + version), `GET /me/privacy` (consents + retention windows), `POST /me/consents` (current version only). Profile has a Privacy card and a "Delete account" dialog that needs DELETE typed.
  - `scripts/retention.py [--dry-run]`, nightly: unsaved unmatched plans > 30 days, telemetry `recorded_at` > 730 days (batched per vehicle), refresh log > 30 days, then the deletion backlog; tombstones removed 7 days after the auth user is deleted and nothing is owed. Exit 1 while deletion work is owed. Scheduling it on the host is manual.
  - Additions to the Owns list (approved with the plan): new `app/modules/vehicles/account_deletion.py`, `app/modules/{vehicles,trips}/retention.py`, `app/modules/users/privacy*.py`, the tombstone check in `users/service.py`, two cards in `app/(app)/profile/page.tsx`, blank `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` in `e2e/support/e2e-servers.ts`, and the E2E seed clearing the tombstone in `scripts/e2e_backend.py`.
  - Manual (you): set `SUPABASE_SERVICE_ROLE_KEY` (and `SUPABASE_URL`) in the API's deploy secrets; schedule `uv run python scripts/retention.py` daily; keep `PRIVACY_TOMBSTONE_DAYS` above the Supabase JWT expiry; check a real Smartcar revocation on deletion with the real car.

**P6-F6 Portfolio close-out** · S · Depends: P6-F1, P6-F2, P6-F4, P6-F5 · Lane: — · Branch `feature/phase6-closeout`
- README, architecture diagram, screenshots, demo script, roadmap §6 deliverables with measured numbers only.

---

## 5. Risks of running in parallel

| Risk | Mitigation |
|---|---|
| Two branches change the same file. | *Owns* lists + §2.3 rules; a real conflict on rebase means the plan is wrong, so stop and fix ownership (§2.6). |
| Two migrations both point at the old head. | Migration lane + `alembic heads` check before every PR. |
| A session codes against an interface that later changes. | C1 (only merged dependencies), and design sessions (P4-F0, P5-F0) pin interfaces first. |
| Tests in one worktree wipe another's database or reuse its servers. | §2.4 per-session database names and E2E ports. |
| Rebase churn: every merge forces N sessions to rebase. | Max 3–4 sessions; disjoint files keep rebases trivial; coordinator batches the "rebase now" message. |
| Review becomes the bottleneck. | One PR per feature, small sizes, PR template with checks; merge in the order they turn green. |
| A later-phase pure module (energy, optimizer, inference) drifts from what the endpoint needs. | Its interface is written in the phase plan before coding and reviewed against it. |
| Later-phase work starts before an earlier phase meets its exit criteria. | Phase "complete" status is only set by its close-out; Phase 4 endpoint and UI work still depend on the Phase 3 pieces they call. |

---

## 6. Status board (coordinator updates this)

| ID | Status | Branch / PR | Notes |
|---|---|---|---|
| W0 | merged 2026-11-18 | `feature/phase3-parallel-prewiring` / #26 | This plan + pre-wiring |
| P3-F5 | merged 2026-11-18 | `feature/phase3-stations-api` / #29 | Adds `stations_cursor_secret` (signed pagination cursor); polyline overflow → 422 fix (Codex review). |
| P4-F0 | merged 2026-11-18 | `feature/phase4-design` / #28 | Settles P4-D1…D16 in `phase4_plan.md` §2; adds P4-F8 and ADR 0008. |
| P4-F5 | merged 2026-11-18 | `feature/phase4-preferences-editor` / #27 | Flaky save-error test fixed in #36 (race in `PreferencesEditor`). |
| P3-F6 | merged 2026-11-18 | `feature/phase3-charging-page` / #35 | Dependency lane released. Codex fixes: in-flight filter edits, pending place suggest. Real-key Frederick check still manual. |
| P4-F1 | merged 2026-11-18 | `feature/phase4-trips-schema` / #33 | Migration lane released. Codex fix: wrong-typed cursor fields → 422. |
| P4-F2 | merged 2026-11-18 | `feature/phase4-energy-model` / #32 | Also edited `tests/test_vehicle_connect.py` (coordinator-approved). |
| P5-F0 | merged 2026-11-18 | `feature/phase5-design` / #34 | Settles P5-D1…D14 in `phase5_plan.md` §2; ADR 0009. |
| P3-F7 | merged 2026-11-18 | `feature/phase3-trip-planner-map` / #41 | E2E-harness lane released. Adds the provider stub; Playwright now `workers: 1`. Codex fix: along-route cache keyed by route geometry. Real-key Frederick → Toronto screenshot still manual. |
| P4-F3 | merged 2026-11-18 | `feature/phase4-optimizer` / #40 | Route-ordered DP; ~35 ms median on the Frederick → Toronto fixture. Notes for P4-F4 in the PR (pre-filtered candidates; `ValueError` on bad input). |
| P4-F7 | merged 2026-11-18 | `feature/phase4-trip-history` / #38 | No Playwright spec or screenshot yet (no trips exist before P4-F4). Limiting-segment highlight needs a `components/map/` follow-up, picked up by P4-F6. |
| P5-F2 | merged 2026-11-18 | `feature/phase5-trip-inference` / #39 | Pure engine. Codex fix: each leg's SoC endpoints bounded to its own window. |
| P3-F8 | merged 2026-11-19 | `feature/phase3-favorites` / #44 | Migration lane released (head `20261119_01`). New `FAVORITES_LIMIT` (409) at 500 favorites. Codex fixes: cap race (profile row lock), Favorites tab kept through the station page. Nothing manual left. |
| P4-F4 | merged 2026-11-19 | `feature/phase4-plan-endpoint` / #43 | ★ `POST /routes/plan`. Unblocks P4-F6 and P4-F8. |
| P3-F9 | blocked (P3-R1: M-03) | — | Code dependencies are merged. To start, it needs M-03 (Places + directions with real keys). To finish, it also needs M-04, M-05 and M-06 (live cross-border stations, Charging page, Trip Planner), which phase3_plan §F9 requires. |
| P4-F6 | merged 2026-11-19 | `feature/phase4-planner-ui` / #47 | ★ Planner on `/trips`, with the limiting-segment highlight. Codex fixes: map draws the plan's own geometry; plan settings kept per route. Real-key screenshot moves to P4-F9. |
| P5-F1 | merged 2026-11-19 | `feature/phase5-observed-schema` / #48 | Migration `20261120_01`. Migration lane released. Coordinator fixed the ER diagram and §9 questions in `database-design.md`. |
| P4-F9 | merged 2026-11-19 | `feature/phase4-closeout` / #50 | ★ Phase 4 code complete. PRD §9 cases mapped to passing tests; ~465 mi plan 241–272 ms on fixtures. Phase 4 "complete" waits on your M-07 and M-10. |
| P4-F8 | merged 2026-11-19 | `feature/phase4-vehicle-baseline` / #52 | Migration `20261121_01`; migration lane released. Adds `catalog_baseline_kwh_per_km` and `baseline_source` to specs. Coordinator fixed the contract §3.7 `*_source` wording to exclude `baseline_source`. |
| P5-F3 | merged 2026-11-19 | `feature/phase5-trip-ingestion` / #53 | Webhooks now create legs via ADR 0009 hooks. Hooks take `(event, open_session)`. F4 replaces the placeholder `observed_service._match_and_score_vehicle`. |
| P5-F5 | merged 2026-11-19 | `feature/phase5-analytics-api` / #51 | No migration. Analytics reads only via `trips/observed_queries.py`. Extra 422 reasons `UNKNOWN_TIME_ZONE`, `INVALID_RANGE`, `INVALID_CURSOR`. |
| P6-F1 | merged 2026-11-19 | `feature/phase6-sentry` / #46 | Dependency lane released. Off without a DSN; scrubs IDs, coordinates and route geometry; frame locals off (Codex fixes). Sentry projects created and local DSNs set by you; hosted DSNs go in at P6-F3. |
| P5-F4 | merged 2026-11-19 | `feature/phase5-prediction-matching` / #56 | `modules/trips/matching.py` replaces F3's placeholder; `GET /trips/{id}` now returns the typed `observation`. Approved Owns addition: `trips/schemas.py`. Codex fix: clearing the only baseline removes the vehicle's leg backtests (plan rows kept). |
| P5-F6 | merged 2026-11-19 | `feature/phase5-analytics-ui` / #58 | Plain-SVG charts (no chart library), quality badges with reasons, CSV route, simulated-data banner. Codex fix: drive times shown in the analytics time zone. |
| P6-F3 | merged 2026-11-19 | `feature/phase6-deploy` / #55 | CI lane released. Render (Docker API) + Vercel (web built in `deploy.yml`); `docker-build` job in `ci.yml`; production only (architecture §7.1). Nothing deploys until you finish `docs/deployment.md` §2–§8. |
| P5-F7 | merged 2026-11-19 | `feature/phase5-history-observations` / #57 | Observed drives tab on `/trips/saved`; planned-vs-actual panel on `/trips/[id]`. Codex fix: cross-midnight start times. Follow-up for P5-F8: switch `parseObservation` to the typed `TripObservation` (now in the client after #56) and add a Playwright check of the panel on a matched plan. |
| P6-F4 | merged 2026-11-20 | `feature/phase6-monitoring` / #67 | CI lane released. `GET /metrics` (token-gated), per-user rate limits on `/routes/plan` and `/places/suggest`, production smoke workflow. Follow-ups: web shows `RATE_LIMITED` as a generic error; metrics reset on spin-down. |
| P6-F5 | merged 2026-11-20 | `feature/phase6-account-privacy` / #69 | Migration lane released (head `20261123_01`). `DELETE /me`, consent records, `scripts/retention.py`. Connect and deletion serialized by a per-account advisory lock (review fix). Manual: service-role key in deploy secrets, schedule the retention job, real Smartcar revocation check. |
| P5-F8 | merged 2026-11-20 | `feature/phase5-closeout` / #68 | Phase 5 code complete. Webhook-only pipeline test, `planned-vs-actual.spec.ts`, typed `TripObservation`. Phase 5 "complete" waits on your M-11 (needs M-02). |
| Fixes | merged 2026-11-20/21 | #62, #66, #70, #71, #72, #76 | Outside the plan: `owned_elsewhere` Connect result (#62); on-demand Smartcar refresh, ADR 0010 (#66); late backlog built whole (#70, P5-F3 follow-up); planner place-search hydration flake (#71); "try again in N minutes" for `RATE_LIMITED` (#72, P6-F4 follow-up); favorite-star `viewBox` and a message for failed OAuth sign-ins (#76, found in the M-14 check). |
| P6-F2 | merged 2026-11-20 | `feature/phase6-demo-mode` / #73 | Live allowlist (`LIVE_ALLOWED_USER_IDS`), read-only public demo mode, `seed_demo.py`. Codex fix: the seed locks the shared demo account's credentials with an `auth.users` trigger. The demo deployment itself is your M-18. |
| Rename | merged 2026-11-21 | `chore/rename-chargewaze` / #74 | ChargeWaze everywhere, repository `ayush-regmi/ChargeWaze` ([ADR 0011](adr/0011-rename-to-chargewaze.md)). The Render service keeps the name `chargewise-api`. |
| P6-F6 | in progress | `feature/phase6-closeout` | Started 2026-11-21. Plans first and waits for approval. Its demo script marks what needs the demo URL (M-18) as pending. |
| M-16 workflow | in progress | — | Started 2026-11-21, outside the plan: a scheduled GitHub Actions run of `scripts/retention.py` against production. Owns `.github/workflows/`, `docs/deployment.md` and M-16 in `docs/manual-test-log.md`. |
| P2-R1, P2-R2, P3-R1 | manual (you) | — | Every open manual check is listed in [`manual-test-log.md`](manual-test-log.md). |

## 7. Next step

W9 is merged (P5-F8 #68, P6-F2 #73) along with W8, six fixes outside the plan (#62, #66, #70–#72, #76) and the rename to ChargeWaze (#74, ADR 0011). In flight (W10): **P6-F6** and the M-16 retention workflow, which don't share files. Production is live at `https://chargewaze.vercel.app` (`chargetrip.vercel.app` redirects there) with the API at `https://chargewise-api.onrender.com`. Checked on 2026-11-21: deploy and smoke runs green, Google sign-in, Dashboard, Garage, Charging, Trip Planner and the start of Smartcar Connect work; the record is M-14 in [`manual-test-log.md`](manual-test-log.md). **P6-F6** (portfolio close-out) is the last feature and finishes the MVP. Its measured numbers and screenshots come from production, and its demo script from the public demo (M-18), so set up M-18 first or in parallel. Phase 4 becomes "complete" once you pass M-07 and M-10, and Phase 5 once you pass M-11. P3-F9 starts once you've passed M-03 (P3-R1) and can't close until M-04, M-05 and M-06 pass too.
