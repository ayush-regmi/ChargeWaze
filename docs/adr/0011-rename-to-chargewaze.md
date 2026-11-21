# ADR 0011: Rename the project from Chargewise to ChargeWaze

- Status: accepted
- Date: 2026-11-21
- Deciders: project lead

## Context

The project shipped under the name "Chargewise" (with "ChargeWise" in the GitHub repository name and `chargewise` in code). The first production web deploy (P6-F3) landed on `chargetrip.vercel.app`. The project lead then made `chargewaze.vercel.app` the main Vercel domain. `chargetrip.vercel.app` still works, but Vercel now answers it with a `307` to the same path and query on `chargewaze.vercel.app`. The Smartcar redirect URI and the Mapbox public-token URL restriction were updated for the new domain by hand.

Running two names (Chargewise in the product, ChargeWaze in the URL) confuses users and reviewers, so the project takes the new domain's name everywhere it can.

Some names are fixed outside the repository and can't change:

- **Render service URL.** A Render service's `onrender.com` subdomain is set when the service is created and never changes, even if the service is renamed. `chargewise-api.onrender.com` is already in Vercel (`NEXT_PUBLIC_API_BASE_URL`), GitHub (`API_BASE_URL`) and the Smartcar webhook URL.
- **Render Blueprint name.** Render matches a Blueprint's services by `name`. Changing `name: chargewise-api` in `render.yaml` would make the next Blueprint sync create a second, empty service instead of renaming the existing one.

## Decision

We will rename the project to **ChargeWaze**, keeping only the identifiers that live outside our control:

- **Product text:** "Chargewise" and "ChargeWise" become "ChargeWaze" in the UI, API title, error messages, docs and ADRs.
- **Code identifiers:** `chargewise` becomes `chargewaze`. This covers the npm scope (`@chargewaze/web`, `@chargewaze/api-client`, `@chargewaze/api`), the Python project (`chargewaze-api`), the Docker image user, the Smartcar connect `state` issuer, the CSV file name (`chargewaze-trips-….csv`), the demo seed's `chargewaze_demo_auth_guard` trigger, and the local and CI Postgres role and database names (`chargewaze`, `chargewaze_test`, `chargewaze_e2e`).
- **GitHub repository:** `ayush-regmi/ChargeWise` becomes `ayush-regmi/ChargeWaze`.
- **Kept as is:** the Render service `chargewise-api` and its URL `https://chargewise-api.onrender.com`. `render.yaml` and [`deployment.md`](../deployment.md) say why next to the name.
- **Production web domain:** `https://chargewaze.vercel.app`. `chargetrip.vercel.app` stays as a redirecting alias. Every place that lists the web origin uses the new domain: Render `ALLOWED_ORIGINS`, Render `SMARTCAR_REDIRECT_URI`, the Smartcar redirect URI, Supabase Site URL and Redirect URLs, the Mapbox token's URL restriction, and the GitHub `production` variable `WEB_URL`.

## Consequences

- **Easier:** one name across the domain, the UI, the code and the repository.
- **Old GitHub URLs keep working.** GitHub redirects the old repository URL for web, API and `git` access. Local clones should still switch their `origin` to the new URL. Vercel and Render follow the rename through their GitHub app installation. Links to old PRs in the docs now use the new name.
- **Local databases need a one-time change.** `docker-compose.yml` now names the container `chargewaze-postgres` and the volume `chargewaze-postgres-data`, with role and database `chargewaze`. An existing `chargewise-postgres` container holds port 5432, so stop it before `docker compose up -d`. Then either start fresh (the new volume is empty) or add the new role and databases to the old container instead:

  ```bash
  docker exec chargewise-postgres psql -U chargewise -d chargewise -c "CREATE ROLE chargewaze LOGIN SUPERUSER PASSWORD 'chargewaze'"
  docker exec chargewise-postgres createdb -U chargewise -O chargewaze chargewaze
  docker exec chargewise-postgres createdb -U chargewise -O chargewaze chargewaze_test
  ```

  `TEST_DATABASE_URL` and `E2E_DATABASE_URL` can still point at the old names if you set them explicitly.
- **In-flight Smartcar Connect attempts fail once.** Connect `state` tokens are signed with the issuer `chargewaze-api` now. A user who started Connect before the deploy and finishes after it sees the "connect again" error. States live 10 minutes, so this only affects a deploy window.
- **Demo seed trigger.** No demo database had been seeded when this was decided (M-18 was open), so the trigger is created only under the new name. A database seeded before this change would keep the old `chargewise_demo_auth_guard` trigger as well; drop it by hand.
- **Mixed names stay in a few places:** the Render service and URL, the local folder name of existing clones, and the Supabase, Vercel and Sentry project names in their dashboards. None of them is shown to users.

## Alternatives considered

- **Create a new Render service named `chargewaze-api`.** That would give a matching URL, but it means a new service ID, new environment values, a new Smartcar webhook (with a new `VERIFY` and a new `SMARTCAR_WEBHOOK_ID`), and new `NEXT_PUBLIC_API_BASE_URL` and `API_BASE_URL` values, for a name users never see. Reopen this if we buy a custom API domain (for example `api.chargewaze.app`); then the Render name stops mattering anyway.
- **Rename the brand text only and keep `chargewise` in code.** That's less churn, but two names in one codebase confuse new contributors and AI agents, and the cost of doing it later only grows.
- **Keep `chargetrip.vercel.app` as the main domain.** Rejected by the project lead; the new name goes with the new domain.

## References

- Render: [Blueprint spec](https://render.com/docs/blueprint-spec) (services are matched by `name`) and [service names and URLs](https://render.com/docs/web-services#your-onrender-com-subdomain).
- GitHub: [Renaming a repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository) (redirects).
- Vercel: [Redirecting domains](https://vercel.com/docs/projects/domains/deploying-and-redirecting).
- [`deployment.md`](../deployment.md) §3–§6 for where each URL is set.
