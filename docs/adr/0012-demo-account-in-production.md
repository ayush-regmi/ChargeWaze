# ADR 0012: The public demo is an account in the production deployment

- Status: accepted
- Date: 2026-11-27
- Deciders: project lead
- Supersedes: the "one setting picks the mode, each mode has its own database" part of [architecture.md](../architecture.md) §7.2 as built in P6-F2 (#73)

## Context

P6-F2 built the public demo as a **second deployment**. `APP_PUBLIC_DEMO_MODE=true` turned a whole API service into the demo, and it needed its own Supabase project, Render service and Vercel project (M-18). Demo data and real data never shared a database: the demo API refused every request (`503 DEMO_MODE_UNSAFE`) if any real vehicle existed.

That design was safe, but it costs a second copy of the stack, and the project lead would have to share two links: the real app and the demo. M-18 was never set up, so the demo wasn't reachable at all. The goal is one link, `chargewaze.vercel.app`, with an **Explore the demo** button on its sign-in page.

The codebase already scopes every user-owned read and write to the caller: every module filters by `user_id`, and an unowned ID answers `404` (engineering guidelines, "Check ownership for every user-owned resource"). Supabase's Data API can't read ChargeWaze tables with the anon key (RLS on, no policies; Phase 1).

## Decision

We will make the demo **one account** in the production deployment instead of a deployment of its own.

- **API.** `DEMO_USER_ID` names the shared demo account. The access gate (`app/modules/auth/access.py`) applies the demo rules to that one user ID and the live allowlist to everyone else. `APP_PUBLIC_DEMO_MODE` is removed.
  - The demo account is read-only except `POST /routes/plan`, `POST /routes/directions` and `POST /stations/along-route` (`403 FORBIDDEN`, `details.reason = "DEMO_READ_ONLY"`), as before. That also blocks `POST /vehicles/connect`, so the demo account can never connect a real car, even though the deployment now has Smartcar secrets.
  - If the demo account itself ever owns a real (`is_simulated = false`) vehicle, its requests get `503 DEMO_MODE_UNSAFE`. Other accounts are never affected.
  - Settings refuse a `DEMO_USER_ID` that is also in `LIVE_ALLOWED_USER_IDS`.
- **Seed.** `scripts/seed_demo.py` runs only for `--user-id == DEMO_USER_ID`, deletes and writes only that account's rows (all `is_simulated = true`), and refuses if that account owns real vehicles, telemetry or observed trips. It no longer refuses because other accounts in the database have real data. It still installs the `auth.users` trigger that locks the demo account's password, email and phone.
- **Web.** The sign-in page shows **Explore the demo** whenever `NEXT_PUBLIC_DEMO_EMAIL` and `NEXT_PUBLIC_DEMO_PASSWORD` are set. The demo banner and the "include simulated data" Analytics default follow the signed-in account (its email matches the demo email), not a deployment flag. `NEXT_PUBLIC_DEMO_MODE` is removed. The web check is for display only; the API enforces the rules by user ID.

## Consequences

- **Easier:** one link to share, no second Supabase project, Render service or Vercel project, and no second cold start on Render's free plan. Setup is three steps: create the demo user, set three variables, run the seed (deployment.md §13.2).
- **Harder / riskier:**
  - Real and demo rows now share one database. They're kept apart by per-user scoping, not by separate databases. A future endpoint that forgets its ownership check could show a real row to the public demo account. `tests/demo/test_access.py` covers this: the demo account gets `404` for another account's vehicle and analytics, and every demo read returns only simulated rows. New user-owned endpoints must keep the ownership rule.
  - The demo account's credentials are public and sign in to the **production** Supabase project. The seed's auth guard stops visitors changing them; a visitor can still sign the shared account out everywhere or enroll an MFA factor (deployment.md §13.2). The Data API exposes nothing to it (RLS, no policies).
  - Demo visitors' plans spend the production Mapbox and AFDC quotas. They share the demo account's planner rate limit (30 plans an hour by default, P6-F4), which caps that.
  - Unsaved demo plans are deleted by the production retention job like anyone's.
- **Still possible:** a separate demo deployment, if ever wanted, is the same code with only `DEMO_USER_ID` set and an empty live allowlist (which serves nobody else under `APP_ENV=production`).
