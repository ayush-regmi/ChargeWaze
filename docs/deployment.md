# Deployment

How Chargewise runs in production, and the one-time setup the project lead does by hand. Built in P6-F3 ([`plan.md`](plan.md) §4.4); hosts follow [`architecture.md`](architecture.md#7-deployment) §7.

| Piece | Where | Config in this repo |
|---|---|---|
| FastAPI backend | Render web service, Docker runtime | [`render.yaml`](../render.yaml), [`services/api/Dockerfile`](../services/api/Dockerfile) |
| Next.js web app | Vercel, prebuilt deploys from GitHub Actions | [`apps/web/vercel.json`](../apps/web/vercel.json) |
| Database + Auth | Supabase (separate production project) | Alembic migrations in `services/api/alembic/` |
| Errors | Sentry (two projects, from P6-F1) | `app/core/observability.py`, `apps/web/sentry.*.config.ts` |
| Pipeline | GitHub Actions | [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), `docker-build` job in [`ci.yml`](../.github/workflows/ci.yml) |

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
5. Keys you'll need: Project URL `https://<ref>.supabase.co`, JWKS URL `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`, and the **anon/publishable** key for the web app. The API doesn't need the service-role key; leave it unset.

Row level security is turned on by the migrations themselves (guidelines → Security).

## 3. Render (API)

1. In Render: **New → Blueprint**. Connect the GitHub repository and pick branch `main`. Render reads `render.yaml` and proposes one web service, `chargewise-api` (Docker, free plan, Oregon).
2. Fill in the values Render asks for. These are the `sync: false` keys; Render only asks for them when the Blueprint is first created. After that, change them under the service's **Environment** tab.

   | Key | Value |
   |---|---|
   | `ALLOWED_ORIGINS` | `https://<web-domain>`: the Vercel production URL, no trailing slash. Separate several with commas. |
   | `DATABASE_URL` | Session-pooler URL from §2 |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_JWKS_URL` | `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` |
   | `SMARTCAR_CLIENT_ID`, `SMARTCAR_CLIENT_SECRET`, `SMARTCAR_APPLICATION_ID` | Smartcar Dashboard (production app) |
   | `SMARTCAR_REDIRECT_URI` | `https://<web-domain>/garage/connect/callback` |
   | `SMARTCAR_WEBHOOK_ID` | Smartcar Dashboard → Webhooks (after §6) |
   | `SMARTCAR_MANAGEMENT_TOKEN` | Smartcar Dashboard → Application Management Token |
   | `MAPBOX_SECRET_TOKEN` | Mapbox secret token (server side only) |
   | `AFDC_API_KEY` | NLR developer key |
   | `SENTRY_DSN_BACKEND` | DSN of the Sentry **backend** project (§7). Leave empty to keep Sentry off. |

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

   That should print `{"status":"ok","env":"production"}`. Then open `https://<web-domain>`, sign in, connect a vehicle, and plan a trip.
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
- Build-time only (Vercel, Sensitive): `SENTRY_AUTH_TOKEN`.
- Public (Vercel, `NEXT_PUBLIC_*`): API base URL, Supabase URL and anon key, Mapbox public token, web Sentry DSN, auth toggles.
