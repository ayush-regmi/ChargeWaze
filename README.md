# Chargewise

A full-stack EV intelligence and route-planning platform. Chargewise combines connected-vehicle data (Smartcar V3), driving directions (Mapbox), charging-station data (AFDC/NREL and partners), and its own charging-aware route optimizer to answer one question:

> Given my vehicle's current battery level, where I want to go, the weather, and available charging infrastructure, what is the most efficient and reliable way to complete my trip?

**Status: specification phase (branch `starter`).** This branch contains the specs, ADRs, and a committed repo skeleton. No application code has been written yet.

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
│   └── web/                 # Next.js + TypeScript frontend (empty — Phase 1)
├── services/
│   └── api/                 # FastAPI backend, modular monolith (empty — Phase 1)
├── packages/
│   └── api-client/          # Typed client generated from FastAPI's OpenAPI (empty — Phase 1)
├── ml/
│   ├── notebooks/
│   ├── training/
│   ├── evaluation/
│   └── models/              # Trained model artifacts (gitignored contents)
├── docs/                    # Specs + ADRs
├── fixtures/
│   ├── smartcar/            # Mocked Smartcar responses + signed webhook payloads
│   └── routes/              # Mapbox / AFDC fixtures
├── .env.example
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

- [x] Starter branch with specs and ADRs
- [ ] Phase 1 — Foundation (monorepo tooling, auth, `GET /api/v1/me`)
- [ ] Phase 2 — Vehicle integration (Smartcar V3 Connect + webhooks)
- [ ] Phase 3 — Maps and chargers
- [ ] Phase 4 — Charging-aware trip planner
- [ ] Phase 5 — History and analytics
- [ ] Phase 6 — Production and public demo

See [`docs/development-roadmap.md`](docs/development-roadmap.md) for per-phase completion criteria.

## Local development

Coming in Phase 1. Once the scaffolds exist:

- Copy `.env.example` to `.env` and fill in real values. **Never commit `.env`.**
- Backend commands live under `services/api/`, driven by **`uv`** (FastAPI + SQLAlchemy + Alembic). Install uv with `brew install uv` on macOS.
- Frontend and shared JS workspaces use **npm workspaces + Turborepo**. From the repo root, run `npm install` once, then use `npm run dev`, `npm run lint`, `npm run test`, `npm run typecheck` (each one calls `turbo run <task>`).
- Tooling rationale: [`docs/adr/0005-npm-turborepo-uv-tooling.md`](docs/adr/0005-npm-turborepo-uv-tooling.md).

## Notes for contributors (human and AI)

- Repository-wide engineering rules live in [`docs/engineering-guidelines.md`](docs/engineering-guidelines.md). Read it before your first PR.
- AI assistants pick up the same rules from local `CLAUDE.md` / `AGENTS.md` files. Those files are intentionally **gitignored** so each contributor keeps their own copy; the committed source of truth is [`docs/engineering-guidelines.md`](docs/engineering-guidelines.md).
- Secrets never leave backend code. In particular, no Smartcar secret ever ends up in a `NEXT_PUBLIC_*` env var or the browser bundle.
