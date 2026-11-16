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

## Receiving Smartcar webhooks locally

`POST /api/v1/webhooks/smartcar` checks `SC-Signature`, an HMAC-SHA256 of the raw body keyed with `SMARTCAR_MANAGEMENT_TOKEN`. Set that token in `.env` before testing. See [`../../docs/smartcar-v3-notes.md`](../../docs/smartcar-v3-notes.md) §5.

### Without a tunnel: signed fixtures

`scripts/send_test_webhook.py` signs a file from [`fixtures/smartcar/`](../../fixtures/smartcar/) with your `.env` token and posts it to `http://localhost:8000`. Start the API first (`uv run fastapi dev app/main.py`). The fixtures use a placeholder vehicle, so pass the Smartcar IDs of a vehicle connected in your database (`vehicles.smartcar_vehicle_id` and `vehicles.smartcar_user_id`).

```bash
uv run python scripts/send_test_webhook.py --fixture webhook_verify_challenge.json
```

```bash
uv run python scripts/send_test_webhook.py --vehicle-id <smartcar-vehicle-id> --user-id <smartcar-user-id> --now
```

- `--now` stamps every signal with the current time, so the Dashboard shows the readings as fresh.
- Each run gets a new `eventId`. Pass `--event-id <id>` twice to see a duplicate get accepted without writing anything.
- `--bad-signature` should get a 401.
- If the IDs don't match a connected vehicle, the API answers `{"accepted": false}` and stores nothing.

### With the real car: tunnel

Smartcar needs a public HTTPS URL. Phase 2 uses ngrok with a free static domain (`docs/phase2_plan.md` D9), so the URL registered with Smartcar stays the same across restarts. The exact tunnel commands will be added after the first live run (F5 acceptance).

1. Start the API on port 8000 and a tunnel to it.
2. In the Smartcar Dashboard, set the webhook URL to `https://<your-domain>/api/v1/webhooks/smartcar`. Smartcar then sends a `VERIFY` event, which the API answers once the signature checks out.
3. Telemetry from the car arrives as `VEHICLE_STATE` events. Each reading is stored in `vehicle_telemetry` and updates `vehicles.latest_state`.
