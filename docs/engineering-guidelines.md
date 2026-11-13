# Chargewise Engineering Guidelines

These are the rules every contributor follows, human or AI. This file is the single source of truth; [`CLAUDE.md`](../CLAUDE.md) and [`AGENTS.md`](../AGENTS.md) point AI assistants here (see [ADR 0007](adr/0007-commit-agent-instructions.md)).

Before you start a task:

1. Read this document.
2. Read the relevant files in [`docs/`](.) and the ADRs in [`docs/adr/`](adr/).
3. Check the acceptance criteria for the current phase in [`development-roadmap.md`](development-roadmap.md).

If the docs contradict each other, flag it in your PR and propose a fix before writing code.

## Stack

- Frontend: Next.js, React, TypeScript.
- Backend: Python 3.12+, FastAPI, Pydantic.
- Database: PostgreSQL, SQLAlchemy, Alembic.
- Authentication: Supabase Auth.
- Vehicle API: Smartcar V3 (see [`adr/0002-smartcar-v3-webhook-telemetry.md`](adr/0002-smartcar-v3-webhook-telemetry.md)).
- Maps: Mapbox.
- Testing: pytest, Vitest, Playwright.
- Monorepo tooling: npm workspaces + Turborepo on the JS side, `uv` on the Python side (see [`adr/0005-npm-turborepo-uv-tooling.md`](adr/0005-npm-turborepo-uv-tooling.md)).

## Architecture

- Business logic lives inside FastAPI, not Next.js.
- The browser never calls private provider APIs directly. All third-party calls (Smartcar, Mapbox server-side, AFDC, weather) go through adapters in [`services/api/app/integrations/`](../services/api). Nothing else imports vendor SDKs directly.
- Smartcar is V3 only. Ignore V2 tutorials and per-vehicle refresh-token patterns.
- Chargewise is a modular monolith. Modules depend down, not across. See [`architecture.md`](architecture.md#3-module-layout-modular-monolith).
- REST endpoints live under `/api/v1/*`. Breaking changes land under a new major version, not in place. See [`api-contracts.md`](api-contracts.md).
- API schemas stay consistent across endpoints (timestamps, units, error envelope, freshness fields).

## Workflow

1. Read the relevant documentation before editing anything.
2. For substantial features, propose a plan first (a short design note or GitHub issue).
3. Work on one feature at a time. Vertical slices — database, backend, frontend, tests — in that order.
4. Define acceptance criteria before you implement. Link them to the phase in the roadmap.
5. Add unit tests for business logic. Add integration tests for anything that crosses a module boundary.
6. Run the relevant tests and lint checks locally before opening a PR.
7. Summarize changed files and test results in the PR description.
8. Never claim a test passed unless it actually ran.

## Security

- Never commit secrets or credentials. The only committed env files are the templates [`services/api/.env.example`](../services/api/.env.example) and [`apps/web/.env.example`](../apps/web/.env.example).
- Smartcar secrets (`SMARTCAR_CLIENT_SECRET`, `SMARTCAR_WEBHOOK_SECRET`, the Mapbox secret token) never go into `NEXT_PUBLIC_*` env vars or the browser bundle.
- Check ownership for every user-owned resource (`vehicles`, `planned_trips`, `observed_trips`, `vehicle_telemetry`). "Not owned by the caller" returns 404, not 403 with a leak.
- Validate Smartcar webhook signatures before persisting anything. See [`architecture.md`](architecture.md#5-webhook-ingestion-rules).
- Minimize and protect vehicle location data. Logs and Sentry breadcrumbs round coordinates; raw lat/lon never leaves the database untouched.
- Never send vehicle commands (lock, unlock, charge control) without explicit per-request authorization. Vehicle commands are not in MVP scope.

## Data and ML

- Keep simulated data separate from observed data. Everything seeded for the public demo is tagged `is_simulated = true`. Analytics and ML training sets filter it out by default.
- Store source timestamps and units on every externally derived row (`source`, `recorded_at`, `received_at`, raw `unit`).
- Never fabricate telemetry, charger availability, or route measurements. Missing data is shown as missing.
- Handle stale or missing vehicle signals explicitly in the UI — ask the user to confirm starting SoC when telemetry is stale.
- The deterministic energy model stays available as a fallback even after ML ships. See [`adr/0004-deterministic-energy-baseline-before-ml.md`](adr/0004-deterministic-energy-baseline-before-ml.md).
- ML work is reproducible: trip-based chronological splits, versioned model artifacts, documented baselines, reported MAE/RMSE.

## Project quality

- Prefer simple, maintainable implementations. Readable beats clever.
- Avoid unnecessary dependencies. Every new runtime dependency needs a reason in the PR.
- Document meaningful architectural decisions with a new ADR in [`adr/`](adr/).
- Do not add features outside the current task. If you find something that needs fixing, open a separate issue.

## Commands

Tooling is **npm workspaces + Turborepo** on the JS side and **`uv`** on the Python side. See [`adr/0005-npm-turborepo-uv-tooling.md`](adr/0005-npm-turborepo-uv-tooling.md).

### Setup (once per machine)

- Install Node (see [`.nvmrc`](../.nvmrc)) and `npm`.
- `brew install uv` on macOS, or use the official uv installer elsewhere.
- Install Docker (only needed for the local Postgres used by the backend test suite; day-to-day dev points at hosted Supabase).
- From the repo root: `npm install`.
- Copy [`services/api/.env.example`](../services/api/.env.example) → `services/api/.env` and fill in Supabase values.
- Copy [`apps/web/.env.example`](../apps/web/.env.example) → `apps/web/.env.local` and fill in `NEXT_PUBLIC_*` values.

### Daily tasks

- Root (runs through Turborepo across all workspaces):
  - `npm run dev` — start every long-running dev server.
  - `npm run build` / `npm run lint` / `npm run test` / `npm run typecheck`.
- Workspace-scoped:
  - Frontend: `npm run dev --workspace=apps/web`, `npm run test --workspace=apps/web`, `npm run test:e2e --workspace=apps/web`.
  - Backend (from [`services/api/`](../services/api)): `uv sync --group dev`, `uv run fastapi dev app/main.py`, `uv run pytest`, `uv run ruff check`, `uv run mypy app`.
  - Migrations: `uv run alembic upgrade head`, `uv run alembic downgrade -1`.
- Generated typed API client: `uv run --project services/api python services/api/scripts/export_openapi.py && npm run generate --workspace=packages/api-client`.
- Local Postgres: `docker compose up -d` (see [`../docker-compose.yml`](../docker-compose.yml)).

Never guess commands — run them to confirm they work before adding them here or to a PR description.

## Git workflow

- One branch per substantial feature. Branch name: `feature/<phase>-<slug>` (e.g. `feature/phase2-smartcar-connect`).
- Commits describe why, not just what. Reference the relevant ADR or doc section when a change makes an architectural choice.
- PRs include: summary, acceptance criteria met, test results, screenshots for UI changes, and notes on any new ADR.
- Mock fixtures for Smartcar / Mapbox / AFDC in the default test run. Live-API tests run in a separately triggered workflow with its own credentials.

## Agent-assisted development

[`CLAUDE.md`](../CLAUDE.md), [`AGENTS.md`](../AGENTS.md), and `.cursor/rules/` are committed and link here instead of repeating these rules. Add or change rules in this file only. Personal agent overrides go in gitignored `CLAUDE.local.md` / `AGENTS.local.md`.
