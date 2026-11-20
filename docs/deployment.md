# Deployment

How Chargewise runs in production, and the one-time setup the project lead does by hand. Built in P6-F3 ([`plan.md`](plan.md) §4.4); hosts follow [`architecture.md`](architecture.md#7-deployment) §7.

| Piece | Where | Config in this repo |
|---|---|---|
| FastAPI backend | Render web service, Docker runtime | [`render.yaml`](../render.yaml), [`services/api/Dockerfile`](../services/api/Dockerfile) |
| Next.js web app | Vercel, prebuilt deploys from GitHub Actions | [`apps/web/vercel.json`](../apps/web/vercel.json) |
| Database + Auth | Supabase (separate production project) | Alembic migrations in `services/api/alembic/` |
| Errors | Sentry (two projects, from P6-F1) | `app/core/observability.py`, `apps/web/sentry.*.config.ts` |
| Pipeline | GitHub Actions | [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), `docker-build` job in [`ci.yml`](../.github/workflows/ci.yml) |
| Monitoring | JSON logs, `GET /metrics`, smoke workflow (from P6-F4) | `app/core/monitoring.py`, [`.github/workflows/smoke.yml`](../.github/workflows/smoke.yml) (§12) |

**Scope:** production only. The preview/staging environment in architecture §7.1 is deferred (no second Supabase project yet, and Render preview environments are a paid feature). Vercel's Git deployments, including PR previews, are switched off in `apps/web/vercel.json`.

## 1. How a deploy runs

Every push to `main` runs CI. When CI passes, `deploy.yml` runs one job in the GitHub environment `production`, in this order:

1. **Migrate.** `alembic upgrade head` against the production database (`PROD_DATABASE_URL`).
2. **Deploy the API.** It calls the Render API to deploy the exact commit CI tested, then waits for the deploy to reach `live`. Auto-deploy is off in `render.yaml`, so Render never deploys on its own.
3. **Health check.** `GET <API_BASE_URL>/health` must return `{"status": "ok", "env": "production"}`.
4. **Build and deploy the web app.** The typed API client is generated, not committed (`packages/api-client/src/schema.d.ts` is gitignored). So the job runs `export_openapi.py` and `npm run generate`, then `vercel pull`, `vercel build --prod`, a scan of the browser bundle for server-secret names, and `vercel deploy --prebuilt --prod`.

If a step fails, the later steps don't run. You can also run it by hand: **Actions → Deploy (production) → Run workflow** on `main`. That deploys the current `main` without waiting for CI.

Until every secret in §5 exists, the workflow ends with a notice ("Production deploy skipped …") instead of failing, so merging this before setup is harmless.

## 2. Supabase (production project)

Use a **new** Supabase project for production, not the development one.

1. Create the project. Note the project ref (`<ref>` below) and the database password.
2. **Database URL.** Go to Project Settings → Database → Connection string, and pick **Session pooler** (port 5432). GitHub Actions and Render both need IPv4, and Supabase's direct connection is IPv6-only, so don't use the direct connection. Don't use the transaction pooler (port 6543) either. Change the scheme to the SQLAlchemy psycopg form:

   ```text
   postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

   This one value goes in two places: Render `DATABASE_URL` (§3) and the GitHub secret `PROD_DATABASE_URL` (§5).
3. **Auth URLs.** Go to Authentication → URL Configuration:
   - Site URL: `https://<web-domain>` (the Vercel production URL from §4).
   - Redirect URLs: add `https://<web-domain>/auth/callback`. The web app sends users to `/auth/callback?next=…`.
4. **OAuth providers.** If Google sign-in is on (`NEXT_PUBLIC_AUTH_GOOGLE_ENABLED=true`), set it up under Authentication → Providers → Google. Use the callback URL Supabase shows there, `https://<ref>.supabase.co/auth/v1/callback`, as the authorized redirect URI in Google Cloud.
5. Keys you'll need: Project URL `https://<ref>.supabase.co`, JWKS URL `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`, the **anon/publishable** key for the web app, and the **service-role** key (Project Settings → API) for the API. The API needs the service-role key for account deletion and `scripts/retention.py` (P6-F5): it deletes the Supabase auth user through the admin API. It is server-only: set it on Render (§3) and never in Vercel or any `NEXT_PUBLIC_*` variable.

Row level security is turned on by the migrations themselves (guidelines → Security).

## 3. Render (API)

1. In Render: **New → Blueprint**. Connect the GitHub repository and pick branch `main`. Render reads `render.yaml` and proposes one web service, `chargewise-api` (Docker, free plan, Oregon).
2. Fill in the values Render asks for. These are the `sync: false` keys; Render only asks for them when the Blueprint is first created. After that, change them under the service's **Environment** tab.

   | Key | Value |
   |---|---|
   | `ALLOWED_ORIGINS` | `https://<web-domain>`: the Vercel production URL, no trailing slash. Separate several with commas. |
   | `LIVE_ALLOWED_USER_IDS` | **Required.** Your Supabase user ID (Authentication → Users in the production project; sign up there first). Several IDs are comma-separated. With `APP_ENV=production` an empty list locks everyone out, you included: every signed-in request gets `403 FORBIDDEN` (`LIVE_ACCESS_RESTRICTED`). See §13. |
   | `DATABASE_URL` | Session-pooler URL from §2 |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_JWKS_URL` | `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` |
   | `SUPABASE_SERVICE_ROLE_KEY` | The production project's service-role key (§2). Server-only. Without it, `DELETE /me` still deletes the account's data, but the Supabase auth user stays owed, and `scripts/retention.py` exits 1 until it can delete it (P6-F5). |
   | `SMARTCAR_CLIENT_ID`, `SMARTCAR_CLIENT_SECRET`, `SMARTCAR_APPLICATION_ID` | Smartcar Dashboard (production app) |
   | `SMARTCAR_REDIRECT_URI` | `https://<web-domain>/garage/connect/callback` |
   | `SMARTCAR_WEBHOOK_ID` | Smartcar Dashboard → Webhooks (after §6) |
   | `SMARTCAR_MANAGEMENT_TOKEN` | Smartcar Dashboard → Application Management Token |
   | `MAPBOX_SECRET_TOKEN` | Mapbox secret token (server side only) |
   | `AFDC_API_KEY` | NLR developer key |
   | `SENTRY_DSN_BACKEND` | DSN of the Sentry **backend** project (§7). Leave empty to keep Sentry off. |
   | `MONITORING_METRICS_TOKEN` | A random string (`openssl rand -hex 32`) that guards `GET /metrics` (§12). Same value as the GitHub secret. Leave empty to hide the endpoint. |

   The Blueprint sets `APP_ENV=production`, `APP_PUBLIC_DEMO_MODE=false`, `SMARTCAR_MODE=live` and `SUPABASE_JWT_AUDIENCE=authenticated`. It also generates random values for `SMARTCAR_STATE_SECRET` and `STATIONS_CURSOR_SECRET`. Every other setting uses its default from `app/core/config.py`. `SMARTCAR_REFRESH_MIN_INTERVAL_MINUTES` (15) and `SMARTCAR_REFRESH_MAX_PER_DAY` (8) bound the user-triggered Refresh ([ADR 0010](adr/0010-on-demand-vehicle-refresh.md)). Lower the daily cap if the Smartcar plan's monthly per-vehicle allowance is below about 250.
3. Render deploys once when the Blueprint is created. That's fine: `/health` doesn't touch the database. The first real deploy (§8) runs the migrations.
4. Note two values for GitHub (§5):
   - **Service ID**: the `srv-…` part of the service's dashboard URL.
   - **Service URL**: e.g. `https://chargewise-api.onrender.com`.
5. Create an API key: Account Settings → **API Keys**. Its value goes in `RENDER_API_KEY` (§5).

**Free-plan caveat.** Render spins a free service down after 15 minutes without traffic, and the next request waits about a minute for a cold start. Users see a slow first request. Smartcar retries non-2xx webhook deliveries with backoff for up to 3 days ([`smartcar-v3-notes.md`](smartcar-v3-notes.md)), so a delivery that times out during a cold start is delivered again, not lost. If either becomes a problem, change `plan:` in `render.yaml` to a paid instance type (`starter`) and re-sync the Blueprint.

## 4. Vercel (web app)

1. **Add New → Project**, import the GitHub repository, then set:
   - Root Directory: `apps/web`
   - Framework Preset: Next.js (also set in `vercel.json`)
   - Node.js version (Settings → General): 22.x

   Vercel's Git integration stays connected, but `"git": {"deploymentEnabled": false}` in `apps/web/vercel.json` stops it from deploying. That covers PR previews too. Only `deploy.yml` deploys. If the first import tries to build and fails, ignore it; the generated client isn't there.
2. **Environment variables.** Settings → Environment Variables, environment **Production**. `vercel pull` fetches these during the workflow, and `NEXT_PUBLIC_*` values are inlined into the browser bundle at build time.

   | Key | Value |
   |---|---|
   | `NEXT_PUBLIC_API_BASE_URL` | Render service URL from §3, e.g. `https://chargewise-api.onrender.com` |
   | `NEXT_PUBLIC_SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon/publishable key |
   | `NEXT_PUBLIC_MAPBOX_PUBLIC_TOKEN` | Mapbox **public** token, URL-restricted to the web domain |
   | `NEXT_PUBLIC_AUTH_GOOGLE_ENABLED` | `true` or `false` |
   | `NEXT_PUBLIC_AUTH_APPLE_ENABLED` | `false` |
   | `NEXT_PUBLIC_SENTRY_DSN` | DSN of the Sentry **web** project (§7). Leave empty to keep Sentry off. |
   | `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` | Optional, for source-map upload (§7). Mark the token **Sensitive**. Never give it a `NEXT_PUBLIC_` name. |

   Never add Smartcar, Mapbox-secret, AFDC or Supabase service-role values here. The workflow fails the deploy if one of those names shows up in the browser bundle.
3. Note three values for GitHub (§5):
   - **Project ID**: Settings → General → Project ID (`prj_…`).
   - **Team/Org ID**: team Settings → General → Team ID. For a personal account, use the user ID under Account Settings.
   - **Production URL**, e.g. `https://chargewise.vercel.app`. This is `<web-domain>` in §2, §3 and §6.
4. Create a token: Account Settings → **Tokens**, scoped to the team that owns the project.

## 5. GitHub environment `production`

Repository → Settings → **Environments → New environment** → `production`:

- **Deployment branches:** "Selected branches" → `main` only.
- **Required reviewers** (optional): add yourself if you want to approve every production deploy.
- **Environment secrets:**

  | Secret | From |
  |---|---|
  | `PROD_DATABASE_URL` | §2 (same value as Render `DATABASE_URL`) |
  | `RENDER_API_KEY` | §3 step 5 |
  | `RENDER_SERVICE_ID` | §3 step 4 (`srv-…`) |
  | `VERCEL_TOKEN` | §4 step 4 |
  | `VERCEL_ORG_ID` | §4 step 3 (Team/Org ID) |
  | `VERCEL_PROJECT_ID` | §4 step 3 (`prj_…`) |
  | `MONITORING_METRICS_TOKEN` | Optional. Same value as on Render (§3). Lets the smoke workflow check the alert thresholds (§12). |

- **Environment variables** (not secret):

  | Variable | Value |
  |---|---|
  | `API_BASE_URL` | Render service URL, e.g. `https://chargewise-api.onrender.com` |
  | `WEB_URL` | Vercel production URL (optional; shown as the deployment link in Actions) |

Store these only in GitHub, Render and Vercel. Never in a committed file, an issue or a PR.

## 6. Smartcar (production app)

In the Smartcar Dashboard for the production application:

1. **Redirect URI:** add `https://<web-domain>/garage/connect/callback`. It must match Render's `SMARTCAR_REDIRECT_URI` exactly.
2. **Webhook:** create one with this URL:

   ```text
   https://<api-domain>/api/v1/webhooks/smartcar
   ```

   Smartcar sends a signed `VERIFY` challenge. The API only answers it when `SMARTCAR_MANAGEMENT_TOKEN` is set on Render (unsigned or wrongly signed requests get `401`). So set the token and deploy before you save the webhook.
3. Copy the webhook's ID into Render `SMARTCAR_WEBHOOK_ID`.
4. Subscribe vehicles: either turn on auto-subscribe or subscribe each vehicle (see `smartcar-v3-notes.md`).

To check delivery without a car, sign a fixture with the production management token and post it to the production URL. Run this from `services/api/`, with that token in your local `services/api/.env` for this run only, and remove it afterwards:

```bash
uv run python scripts/send_test_webhook.py --fixture webhook_verify_challenge.json --url https://<api-domain>/api/v1/webhooks/smartcar
```

## 7. Sentry (from P6-F1)

Sentry stays off until a DSN is set. With a DSN, both apps scrub user IDs, coordinates and route geometry before sending.

1. Create two Sentry projects: **backend** (Python/FastAPI) and **web** (Next.js).
2. In each project, under Security & Privacy, turn on the **Data Scrubber** and **Prevent Storing of IP Addresses**.
3. Backend DSN goes in **Render** `SENTRY_DSN_BACKEND`. Events are tagged with environment `production`, from `APP_ENV`.
4. Web DSN goes in **Vercel** `NEXT_PUBLIC_SENTRY_DSN` (Production). A web DSN is public by design.
5. Optional source maps: create an org auth token with the `project:releases` and `org:read` scopes. In **Vercel** (Production), set `SENTRY_AUTH_TOKEN` (Sensitive), `SENTRY_ORG` (org slug) and `SENTRY_PROJECT` (the web project slug). These are only read during `vercel build` in the workflow. Without the token, nothing is uploaded.

Changing a `NEXT_PUBLIC_*` value only takes effect after the next web build: re-run the deploy workflow.

## 8. First deploy and checks

1. Finish §2–§7. The web domain is needed in Supabase, Render (`ALLOWED_ORIGINS`, `SMARTCAR_REDIRECT_URI`) and Smartcar. Vercel assigns it when you create the project, before any deploy.
2. Actions → **Deploy (production)** → Run workflow on `main`. Watch for: migrations applied, Render deploy `live`, `/health` OK, a Vercel URL printed.
3. Check it:

   ```bash
   curl -s https://<api-domain>/health
   ```

   That should print `{"status":"ok","env":"production"}`. Then open `https://<web-domain>`, sign in, connect a vehicle, and plan a trip. If every page says the deployment is private, `LIVE_ALLOWED_USER_IDS` (§3) doesn't contain your user ID yet: set it under the service's **Environment** tab and redeploy.
4. Save the Smartcar webhook (§6) and confirm `VERIFY` passed in the Smartcar Dashboard.

## 9. Migrations

- They run in `deploy.yml` **before** the new API image ships, so for a few minutes the **old** API runs on the **new** schema. Each migration must keep the previous release working: add columns as nullable or with a default, and drop or rename only in a later release, after no deployed code uses the old shape.
- Production is never downgraded by the pipeline. A bad migration is fixed with a new forward migration.
- To run one by hand: from `services/api/`, with `DATABASE_URL` set explicitly to the production URL (never rely on a local `.env`):

  ```bash
  DATABASE_URL='<session-pooler URL>' uv run alembic upgrade head
  ```

## 10. Rollback

- **API:** Render dashboard → the service → **Rollback** to a previous deploy. The free plan keeps the last two. Or revert the commit on `main` and let the pipeline redeploy it.
- **Web:** Vercel dashboard → Deployments → an earlier production deploy → **Instant Rollback**.
- **Database:** not rolled back automatically. Because migrations are backward compatible (§9), rolling the code back is safe. For data loss, use Supabase's backups (Database → Backups).

## 11. Secrets checklist

- Committed: none. `render.yaml` lists key names only (`sync: false` or generated), and `.dockerignore` keeps `.env*` out of the image.
- Server-only (Render, GitHub): Smartcar client secret and management token, Mapbox secret token, AFDC key, database URL, Render API key, Vercel token.
- Server-only (Render, GitHub), from P6-F4: `MONITORING_METRICS_TOKEN`.
- Server-only (Render), from P6-F5: `SUPABASE_SERVICE_ROLE_KEY`. It bypasses row level security, so it never goes in Vercel, a `NEXT_PUBLIC_*` variable or the browser bundle (the deploy workflow fails if the name shows up there).
- Build-time only (Vercel, Sensitive): `SENTRY_AUTH_TOKEN`.
- Public (Vercel, `NEXT_PUBLIC_*`): API base URL, Supabase URL and anon key, Mapbox public token, web Sentry DSN, auth toggles. On the demo deployment only (§13): `NEXT_PUBLIC_DEMO_MODE` and the shared demo account's `NEXT_PUBLIC_DEMO_EMAIL` / `NEXT_PUBLIC_DEMO_PASSWORD`, which are public on purpose.
- Not secret, but required on the production API: `LIVE_ALLOWED_USER_IDS` (§3).

## 12. Monitoring and alerts (from P6-F4)

Architecture §9 asks for structured logs, request metrics and error reporting. They are all built into the API, with no extra service to pay for.

### 12.1 What the API records

- **Logs.** The API writes one JSON object per line to stdout. Render shows these under the service's **Logs** tab; the free plan keeps them for a limited time only. Each API request gets an `http_request` line with `method`, `route` (the template, e.g. `/api/v1/trips/{trip_id}`), `status` and `duration_ms`. Query strings, request bodies, user and vehicle IDs and coordinates are never logged: extra fields go through the same scrubber as Sentry. `/health` and `/metrics` aren't logged. Useful searches:
  - `planner_slow`: a `/routes/plan` call took longer than `MONITORING_PLANNER_SLOW_MS` (default 10 s).
  - `smartcar_webhook_` (`_rejected`, `_ingest_failed`, `_not_accepted`, `_ignored`): webhook outcomes, by Smartcar event ID.
  - `rate_limited`: a user hit the plan or search limit (`bucket` says which).
- **`GET /metrics`.** A JSON snapshot since the process started: per-route counts, status classes, p50/p95/max latency over the last 500 requests, plus two summaries:
  - `planner`: latency of `POST /routes/plan`. This is time inside the API; Render's cold start comes before it and isn't included.
  - `webhooks`: `deliveries`, `succeeded`, `failed` and `success_rate` for Smartcar webhooks. A delivery succeeded when it got a 2xx; `400` and `5xx` are failures. `401` (missing or wrong signature) is counted separately as `signature_rejected` and left out of the rate, since those aren't Smartcar deliveries.

  It answers `404` unless `MONITORING_METRICS_TOKEN` is set on Render, and then only with that bearer token:

  ```bash
  curl -s -H "Authorization: Bearer $MONITORING_METRICS_TOKEN" https://<api-domain>/metrics
  ```

  **The counters reset on every restart.** On the free plan the service sleeps after 15 idle minutes (§3), so a snapshot only covers the time since the last cold start. That's why the thresholds below need a minimum sample before they can fail.
- **Rate limits.** `POST /routes/plan` and `GET /places/suggest` have a per-user limit and answer `429 RATE_LIMITED` with `Retry-After` when it's used up ([`api-contracts.md`](api-contracts.md) §3.7, §3.13). The defaults (`RATE_LIMIT_*` in `app/core/config.py`) are 10 plans at once refilled at 30 per hour, and 30 searches at once refilled at 60 per minute. To change one, set the variable on Render's **Environment** tab. The buckets live in memory, one set per instance. They reset on every deploy or restart, including each free-plan spin-down, so a user can get a fresh burst after one. A second instance would give each user a second allowance, so running more than one instance needs a shared store (a Postgres table) first.

### 12.2 Smoke workflow

[`.github/workflows/smoke.yml`](../.github/workflows/smoke.yml) checks production from outside. It runs after every successful **Deploy (production)**, every 6 hours, and by hand (**Actions → Smoke (production) → Run workflow**). It uses the `production` environment's `API_BASE_URL`, `WEB_URL` and, if set, `MONITORING_METRICS_TOKEN`. Without `API_BASE_URL` it ends with a notice.

| Check | Fails when |
|---|---|
| API health | `/health` doesn't answer `env: production` within 4 attempts of 90 s each. That allows for the ~1 min cold start. |
| Auth and error envelope | `GET /api/v1/me` without a token isn't `401 UNAUTHENTICATED`. |
| Webhook URL | An unsigned `POST /api/v1/webhooks/smartcar` isn't `401 WEBHOOK_SIGNATURE_INVALID`. Nothing is stored for it. |
| Web app | `WEB_URL` doesn't answer 200 (skipped when `WEB_URL` is empty). |
| Webhook success rate | Below 95 % with at least 20 deliveries since the last restart. |
| Planner latency | p95 above 15 s with at least 5 plans since the last restart. |

The thresholds are `env` values at the top of the job. The scheduled run wakes the API, so the metric checks usually see few requests; they matter most right after a deploy and during active use.

If you turned on **required reviewers** for the `production` environment (§5), each smoke run also waits for your approval. Leave reviewers off, or approve the runs.

GitHub turns off scheduled workflows in a repository with no activity for 60 days. Re-enable it under **Actions → Smoke (production)**.

### 12.3 Alerts: where they go

No pager. Three channels, set up once:

1. **Failed smoke runs (uptime, webhook rate, planner latency).** GitHub notifies you when a workflow run fails. Under your GitHub **Settings → Notifications → Actions**, choose email and "Only notify for failed workflows". For a scheduled run, GitHub notifies the user who last edited the `cron` line in `smoke.yml`.
2. **Errors (Sentry, §7).** Unhandled exceptions, and every log line at ERROR such as `smartcar_webhook_ingest_failed`, become Sentry events. In the **backend** project, under **Alerts → Create Alert → Issues**, create:
   - "A new issue is created" → email (the default rule; keep it).
   - "The issue is seen more than 10 times in 1 hour" → email, so a webhook ingest failure loop or a crashing endpoint pages you once, not per event.
   - In the **web** project, keep the default new-issue alert.
3. **Render.** In Render's notification settings (workspace or service), have failed deploys and service failures emailed. The deploy workflow fails too, but this also covers a crash loop after a deploy has gone live.

### 12.4 Turning it off

- `MONITORING_METRICS_TOKEN` empty: `/metrics` answers 404; the smoke workflow skips the threshold check.
- `RATE_LIMIT_ENABLED=false` on Render: no per-user limits.
- Disable the smoke workflow under **Actions → Smoke (production) → ⋯ → Disable workflow**.

## 13. Live allowlist and the public demo (from P6-F2)

Architecture §7.2: a deployment runs either **private live mode** or **public demo mode**, chosen by `APP_PUBLIC_DEMO_MODE`. The two never share a database. The gate is in `app/modules/auth/access.py` and runs inside `get_current_user`, so it covers every signed-in route. Webhooks and `/health` aren't gated.

### 13.1 Live mode (the production service above)

- Only the Supabase user IDs in `LIVE_ALLOWED_USER_IDS` are served. Anyone else who signs in gets `403 FORBIDDEN` with `details.reason = "LIVE_ACCESS_RESTRICTED"`.
- **The list must be set before the first deploy.** With `APP_ENV=production`, an empty list fails closed and locks everyone out, the owner included. In development and test, an empty list means no restriction.
- To find your ID: Supabase → Authentication → Users → your row → **User UID**. To add someone, append their ID with a comma and redeploy (Render restarts the service with the new value).

### 13.2 Public demo (optional second deployment)

The demo is a separate API service, web project and database. A recruiter signs in with one shared demo account, sees a simulated 2026 Toyota bZ with about four weeks of drives, a saved Frederick → Toronto road trip matched to its drive, and analytics. They can also plan new trips live.

In demo mode the API:

- **Refuses to start** if `SMARTCAR_CLIENT_SECRET` or `SMARTCAR_MANAGEMENT_TOKEN` is set. No real car can be connected and no webhook can be verified.
- **Is read-only except planning.** Every write answers `403 FORBIDDEN` with `details.reason = "DEMO_READ_ONLY"`, except `POST /routes/plan`, `POST /routes/directions` and `POST /stations/along-route`. Because the account is shared, one visitor can't delete trips, change preferences or delete the account for the next one.
- **Serves nothing while the database holds a real vehicle.** Every signed-in request gets `503 DEMO_MODE_UNSAFE` and an error is logged. Turning demo mode on against the production database exposes nothing.
- **Shares the planner rate limit** across all visitors, because they're one user (30 plans an hour by default, P6-F4).

Setup, all by hand:

1. **Database and Auth.** Create a second Supabase project for the demo, as in §2. Never use the production one. Then:
   - Under Authentication → Providers → Email, turn off **Allow new users to sign up** and turn on **Secure password change**, so a visitor can't change the shared account's password.
   - Create the demo user under Authentication → Users → **Add user**. Use an address on a domain you don't use for anything else, and a throwaway password. Note its **User UID**.
2. **Migrate** the demo database, from `services/api/` with the URL set explicitly:

   ```bash
   DATABASE_URL='<demo session-pooler URL>' uv run alembic upgrade head
   ```

3. **Seed it** from a full checkout (the seed reads the recorded fixtures in the repository's `fixtures/` folder). The seed only reads the process environment, never `services/api/.env`. It refuses unless `APP_PUBLIC_DEMO_MODE=true` is set, `--target` repeats the database URL's `host/database`, and the database has no real (`is_simulated = false`) row:

   ```bash
   APP_PUBLIC_DEMO_MODE=true DATABASE_URL='<demo session-pooler URL>' uv run python scripts/seed_demo.py --user-id <demo User UID> --target aws-0-<region>.pooler.supabase.com/postgres
   ```

   Re-running replaces the demo account's data with a fresh four weeks ending now. Run it daily if you want the demo to keep looking recent; scheduling it is up to you. The car's last reading goes stale 15 minutes after a seed, so the planner asks for a starting SoC. That's expected.
4. **API.** Create a second Render web service from the same repository (Docker, `services/api/Dockerfile`), or a copy of the Blueprint with a different `name`. Set:
   - `APP_ENV=production` and `APP_PUBLIC_DEMO_MODE=true`.
   - The demo database URL, and the demo Supabase project's `SUPABASE_URL` and `SUPABASE_JWKS_URL`.
   - `MAPBOX_SECRET_TOKEN`, `AFDC_API_KEY`, `STATIONS_CURSOR_SECRET`, and `ALLOWED_ORIGINS` set to the demo web URL.

   Leave every `SMARTCAR_*` secret unset. `LIVE_ALLOWED_USER_IDS` isn't used in demo mode. The deploy workflow only deploys the production service, so deploy the demo service from the Render dashboard (or turn on its auto-deploy).
5. **Web.** Create a second Vercel project from the repository with the same settings as §4, pointed at the demo API and the demo Supabase project. Set `NEXT_PUBLIC_DEMO_MODE=true`, `NEXT_PUBLIC_DEMO_EMAIL` and `NEXT_PUBLIC_DEMO_PASSWORD` (the demo user's). The sign-in page then shows **Explore the demo**, every app page shows the demo banner, and Analytics includes simulated data by default.
6. **Check it.** Open the demo URL, click **Explore the demo**, then:
   - The Dashboard shows the simulated bZ.
   - Saved trips → Frederick → Toronto shows planned vs actual.
   - Analytics shows about four weeks.
   - Planning a new trip works.
   - Any other action (for example deleting a trip) shows "The public demo is read-only".
