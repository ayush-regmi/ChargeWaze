## Summary

<!-- What changed and why. Link the issue and the roadmap phase / PRD section. -->

Closes #

## Acceptance criteria

- [ ] <!-- copy from the issue -->

## Test evidence

<!-- Paste the commands you ran and their real output. Never claim a check that didn't run. -->

- [ ] `uv run pytest` / `uv run ruff check` / `uv run mypy app` (services/api)
- [ ] `npx turbo run lint typecheck test`
- [ ] Clicked through the change in the running app

## Screenshots

<!-- UI changes only. -->

## Review checklist

- [ ] User-owned resources check ownership (not yours → 404)
- [ ] No secrets in `NEXT_PUBLIC_*`, logs, or committed files
- [ ] New tables enable RLS; migrations downgrade cleanly
- [ ] Redirects and URLs from user input are validated
- [ ] Docs / ADRs updated if behaviour or architecture changed

## AI assistance

<!-- Which parts were AI-generated, what you changed after review, and anything you're unsure about. -->
