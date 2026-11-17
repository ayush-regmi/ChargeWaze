# Chargewise

A full-stack EV intelligence and route-planning platform. Chargewise combines connected-vehicle data (Smartcar V3), driving directions (Mapbox), charging-station data (AFDC/NREL and partners), and its own charging-aware route optimizer to answer one question:

> Given my vehicle's current battery level, where I want to go, the weather, and available charging infrastructure, what is the most efficient and reliable way to complete my trip?

**Status: Phase 2 code complete; real-car verification pending.** On `main`: Supabase Auth, Smartcar V3 Connect, the Garage, the signed-webhook ingestor, the Dashboard with freshness markers, primary vehicle and spec editing, and a Playwright Garage → Dashboard test. Phase 2 closes once the real Toyota has been checked end to end and its event cadence measured; Phase 3 (maps and chargers) is next.

## MVP demo flow

Chargewise reaches its MVP definition of done when a user can, in one session:

1. Sign in with Supabase Auth.
2. Connect their vehicle through Smartcar Connect (V3).
3. See real, timestamped battery state on the Dashboard.
4. Search a destination in the Trip Planner.
5. Receive a battery-feasible route with recommended charging stops, estimated arrival SoC, and total trip time — or an explicit "not feasible" result.

Full acceptance criteria are in [`docs/product-requirements.md`](docs/product-requirements.md).

## Repository layout

```
chargewise/
├── apps/
│   └── web/                 # Next.js + TypeScript frontend (.env.example inside)
├── services/
│   └── api/                 # FastAPI backend, modular monolith (.env.example inside)
├── packages/
│   └── api-client/          # Typed client generated from FastAPI's OpenAPI
├── ml/
│   ├── notebooks/
│   ├── training/
│   ├── evaluation/
│   └── models/              # Trained model artifacts (gitignored contents)
├── docs/                    # Specs + ADRs
├── fixtures/
│   ├── smartcar/            # Mocked Smartcar responses + signed webhook payloads
│   └── routes/              # Mapbox / AFDC fixtures
├── CLAUDE.md                # Agent instructions (points to docs/engineering-guidelines.md)
├── AGENTS.md
└── README.md
```

## Documentation

Start here, in this order:

1. [`docs/product-requirements.md`](docs/product-requirements.md) — vision, features, acceptance criteria, MVP scope.
2. [`docs/architecture.md`](docs/architecture.md) — stack, system diagram, modules, routing algorithm, deployment.
3. [`docs/api-contracts.md`](docs/api-contracts.md) — the `/api/v1/*` surface and conventions.
4. [`docs/database-design.md`](docs/database-design.md) — tables, relationships, provenance, retention.
5. [`docs/development-roadmap.md`](docs/development-roadmap.md) — phases 1–6, vertical slices, ML plan, risks.
6. [`docs/engineering-guidelines.md`](docs/engineering-guidelines.md) — rules every contributor (human or AI) follows.
7. [`docs/adr/`](docs/adr/) — architecture decision records.

## Current status

- [x] Specs and ADRs
- [x] Phase 1 slice 1 — monorepo scaffolds (Next.js app shell, FastAPI `/health`, typed API client)
- [x] Phase 1 slice 2 — Supabase Auth (email/password + Google; Apple behind a flag) with `GET /api/v1/me`
- [x] Phase 1 slice 3 — CI green on `main`
- [ ] Phase 2 — Vehicle integration (Smartcar V3 Connect + webhooks): code complete (Garage, webhook ingestor, Dashboard, primary vehicle, E2E); real-car verification and event-cadence measurement pending
- [ ] Phase 3 — Maps and chargers
- [ ] Phase 4 — Charging-aware trip planner
- [ ] Phase 5 — History and analytics
- [ ] Phase 6 — Production and public demo

See [`docs/development-roadmap.md`](docs/development-roadmap.md) for per-phase completion criteria.

## Local development

Setup once per machine:

1. Install Node (see [`.nvmrc`](.nvmrc)) and `npm`.
2. `brew install uv` on macOS (or use the official uv installer).
3. Install Docker. The backend test suite runs against the local Postgres container (`docker compose up -d`); hosted Supabase handles day-to-day dev.
4. From the repo root, run `npm install`.
5. Copy [`services/api/.env.example`](services/api/.env.example) → `services/api/.env` and fill in your Supabase values.
6. Copy [`apps/web/.env.example`](apps/web/.env.example) → `apps/web/.env.local` and fill in `NEXT_PUBLIC_*` values.

Daily commands:

- Everything through Turborepo: `npm run dev`, `npm run build`, `npm run lint`, `npm run test`, `npm run typecheck`.
- Frontend only: `npm run dev --workspace=apps/web`.
- Backend only (from `services/api/`): `uv run fastapi dev app/main.py`.
- Regenerate the typed API client: `uv run --project services/api python services/api/scripts/export_openapi.py && npm run generate --workspace=packages/api-client`.

**Never commit `.env*` files** other than the `.env.example` templates. See [`docs/engineering-guidelines.md`](docs/engineering-guidelines.md) for the full contributor workflow and [`docs/adr/0005-npm-turborepo-uv-tooling.md`](docs/adr/0005-npm-turborepo-uv-tooling.md) for the tooling rationale.

## Notes for contributors (human and AI)

- Repository-wide engineering rules live in [`docs/engineering-guidelines.md`](docs/engineering-guidelines.md). Read it before your first PR.
- AI assistants load the committed [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md), which point back to the guidelines rather than copying them (see [ADR 0007](docs/adr/0007-commit-agent-instructions.md)). Personal overrides go in a gitignored `CLAUDE.local.md`.
- Secrets never leave backend code. In particular, no Smartcar secret ever ends up in a `NEXT_PUBLIC_*` env var or the browser bundle.
