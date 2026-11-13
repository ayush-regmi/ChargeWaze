# chargewise-api

FastAPI backend for Chargewise (modular monolith). See [`../../docs/architecture.md`](../../docs/architecture.md) for module layout and the broader architecture, and [`../../docs/api-contracts.md`](../../docs/api-contracts.md) for the public API surface.

## Setup

```bash
# From services/api/
uv sync --group dev
cp .env.example .env   # fill in local values; never commit .env
uv run fastapi dev app/main.py
```

Run tests, lint, and types. The tests need the local Postgres container
(`docker compose up -d` from the repo root). They create and reset a
`chargewise_test` database and refuse to run against any non-local host, so
they can never touch Supabase. Override with `TEST_DATABASE_URL` if needed.

```bash
uv run pytest
uv run ruff check
uv run mypy app
```

Database migrations:

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
```
