# ADR 0005: npm workspaces + Turborepo (JS) and uv (Python)

- Status: accepted
- Date: 2026-11-06
- Deciders: project lead

## Context

ChargeWaze is a monorepo with two language ecosystems:

- JavaScript/TypeScript under [`apps/web`](../../apps/web) (Next.js) and [`packages/api-client`](../../packages/api-client) (generated TS client).
- Python 3.12+ under [`services/api`](../../services/api) (FastAPI backend) and [`ml/`](../../ml) (training, evaluation, notebooks).

Both halves need a package manager, and the JS side benefits from a task runner that can cache lint/test/build across workspaces. The choice must:

- Stay simple for a single developer working part-time.
- Not block Phase 1 on learning an exotic tool.
- Be popular enough that recruiters and future contributors recognize it.
- Work with Vercel (frontend host) and Render/Fly.io (backend host) without special plugins.

## Decision

- **JavaScript/TypeScript:** **npm** with the built-in **workspaces** feature, plus **Turborepo** for task orchestration and caching.
- **Python:** **`uv`** for dependency management and virtualenv creation in both [`services/api`](../../services/api) and [`ml/`](../../ml).

Concretely:

- Root [`package.json`](../../package.json) declares `"workspaces": ["apps/*", "packages/*"]` and pins `"packageManager": "npm@11.12.1"`.
- Turborepo tasks (`dev`, `build`, `lint`, `test`, `typecheck`) are defined once in [`turbo.json`](../../turbo.json); workspace scripts hook into them.
- [`.nvmrc`](../../.nvmrc) pins the Node major version.
- Each Python project owns a `pyproject.toml` consumed by `uv`. `uv sync` creates `.venv/` in each project; `uv run` wraps commands.
- `uv` is installed per-developer with `brew install uv` (macOS) or the official installer. It is not yet installed on the lead developer's machine; it gets installed as part of Phase 1 kickoff.

## Consequences

- Easier: npm is the default for Node; nothing to install beyond Node itself to get started. Turborepo gives remote-cacheable task graphs without a monorepo rewrite.
- Easier: `uv` is dramatically faster than pip/poetry at resolve + install, has deterministic lockfiles (`uv.lock`), and manages Python versions.
- Easier: Vercel and Render both natively support npm and uv-produced environments.
- Harder: Turborepo adds one more concept (task pipeline) to learn. Its config is small for now.
- Harder: `uv` is newer than `poetry` and its ecosystem is still maturing. If a hard blocker appears (plugin missing, hosting incompatibility), we fall back to `poetry` — the `pyproject.toml` format is compatible enough that migration is mechanical.

## Alternatives considered

- **pnpm workspaces.** Rejected: faster installs and stricter hoisting are nice, but every contributor would need `pnpm` installed; no benefit at a single-developer scale over npm + Turborepo.
- **Plain npm without Turborepo.** Rejected: once a second workspace exists, running `npm run lint --workspaces` loses dependency-order and caching. Turborepo solves both with minimal config.
- **Yarn Berry.** Rejected: PnP would complicate editor/tooling config; classic Yarn adds no clear win over npm here.
- **poetry.** Rejected as primary: slower, older resolver; requires a separate pyenv to pin Python versions. We would still consider it if `uv` turns out to be blocking on a hosting provider.
- **pip + requirements.txt + venv.** Rejected: no lockfile semantics, no dev/prod group separation, no Python-version management.
- **Nx instead of Turborepo.** Rejected: more features than ChargeWaze needs; higher learning cost.

## References

- Turborepo docs — https://turbo.build/repo/docs
- npm workspaces — https://docs.npmjs.com/cli/v10/using-npm/workspaces
- uv docs — https://docs.astral.sh/uv/
- [`docs/architecture.md`](../architecture.md)
- [`docs/development-roadmap.md`](../development-roadmap.md)
