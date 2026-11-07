# ADR 0006: Supabase Auth with JWKS verification and lazy profile creation

- Status: accepted
- Date: 2026-11-06
- Deciders: project lead
- Related: [ADR 0001](0001-nextjs-fastapi-modular-monolith.md)

## Context

Chargewise needs an authentication system that:

- Works for a single developer during Phase 1 and scales to multiple users later.
- Lets the Next.js frontend and the FastAPI backend agree on a user identity without sharing a session table.
- Supports email/password plus common social providers (Google now, Apple later behind a flag).
- Keeps the backend's user model under Chargewise's control — Supabase owns identity, Chargewise owns domain data.
- Does not fabricate schemas that cross between Supabase's internal tables and Chargewise-owned tables (migrations have to run against a plain Postgres in CI).

Supabase Auth is already in the stack (ADR 0001). Modern Supabase projects sign JWTs with asymmetric keys and publish a JWKS document. Older projects use a shared HS256 secret.

## Decision

1. **Session issuance** happens in Supabase Auth. The Next.js app uses `@supabase/ssr` to maintain the session via secure cookies, and relies on middleware to refresh and gate routes.
2. **Backend JWT verification** uses `PyJWT`'s `PyJWKClient` against `SUPABASE_JWKS_URL`. HS256 via `SUPABASE_JWT_SECRET` stays as a fallback for legacy projects. The JWKS client caches keys automatically; the backend never calls Supabase per request.
3. **Required claims:** signature valid, `exp` not expired, `aud == SUPABASE_JWT_AUDIENCE` (default `authenticated`). Missing or malformed claims produce `401 UNAUTHENTICATED` in the Chargewise error envelope.
4. **Profiles are created lazily.** The first authenticated request to `GET /api/v1/me` upserts `profiles` (keyed by the Supabase `sub` UUID) and seeds a default `user_preferences` row in the same transaction. No webhooks from Supabase into the backend; no service-role calls on the hot path.
5. **No cross-schema foreign key.** `profiles.id` is a plain UUID — not `REFERENCES auth.users(id)`. Supabase Auth's schema is managed by Supabase, not by our Alembic migrations, and our CI Postgres container has no `auth` schema at all. Keeping the relationship conceptual (id equality) means the same migration runs everywhere.
6. **Provider list** for Phase 1: email/password, Google, Apple (behind `NEXT_PUBLIC_AUTH_APPLE_ENABLED` because it requires a paid Apple Developer account).
7. **Sign-out** is a server route (`POST /auth/sign-out`) that calls `supabase.auth.signOut()` and clears cookies. Frontend buttons `POST` to it.

## Consequences

- Easier: the backend scales with the user base — no session table to maintain, no per-request Supabase call, no shared secret to rotate on Supabase's modern key format.
- Easier: migrations are portable. We can run Alembic against a plain Postgres in CI and against Supabase's Postgres in production without schema branching.
- Easier: new users get clean preferences on first visit; there's no "create profile" screen to build.
- Harder: Chargewise and Supabase can drift (e.g. user deleted in Supabase but `profiles` row lingers). We accept this for Phase 1 and schedule a reconciliation job before production.
- Harder: changing the JWKS URL or audience requires a backend redeploy. Acceptable at our scale.
- Harder: if a future requirement needs `auth.users` columns (e.g. last sign-in time) we'll have to fetch them through Supabase APIs, not SQL joins. Also acceptable.

## Alternatives considered

- **Shared HS256 secret everywhere.** Rejected as the primary path — Supabase has been moving to asymmetric JWTs since 2025. The secret still exists as a fallback for legacy projects.
- **Cross-schema foreign key `profiles.id → auth.users.id`.** Rejected: ties migrations to Supabase's schema and breaks CI against vanilla Postgres. The conceptual equality is enough.
- **Supabase webhook creating `profiles` on `auth.users` insert.** Rejected for now: adds operational complexity for one row. Lazy creation on first `/me` call is simpler and self-healing.
- **Server-side session cache in Chargewise.** Rejected: duplicates Supabase's role and adds invalidation complexity.
- **Clerk / Auth0.** Rejected: Supabase is already the database host, and bundling auth with it removes a moving part.

## References

- Supabase SSR + Next.js guide — https://supabase.com/docs/guides/auth/server-side/nextjs
- PyJWT `PyJWKClient` — https://pyjwt.readthedocs.io/en/latest/usage.html#retrieve-rsa-signing-keys-from-a-jwks-endpoint
- [`docs/api-contracts.md`](../api-contracts.md) section 3.1 (`GET /api/v1/me`)
- [`docs/database-design.md`](../database-design.md) sections 3.1 and 3.2
