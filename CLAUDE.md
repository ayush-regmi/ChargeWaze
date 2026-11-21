# ChargeWaze agent instructions

Instructions for AI coding agents (Claude Code, Cursor, Codex, etc.) working in this repository. The rules themselves live in [`docs/engineering-guidelines.md`](docs/engineering-guidelines.md); this file only points to them and adds agent-specific behaviour.

## Read first

1. [`docs/engineering-guidelines.md`](docs/engineering-guidelines.md) — stack, architecture, workflow, security, data/ML, commands, git workflow. **Single source of truth.**
2. The relevant specs in [`docs/`](docs/):
   - [`product-requirements.md`](docs/product-requirements.md) — features and acceptance criteria.
   - [`architecture.md`](docs/architecture.md) — modules, provider adapters, routing, deployment.
   - [`api-contracts.md`](docs/api-contracts.md) — the `/api/v1/*` surface and conventions.
   - [`database-design.md`](docs/database-design.md) — tables, provenance, retention.
3. The ADRs in [`docs/adr/`](docs/adr/).
4. The acceptance criteria for the current phase in [`docs/development-roadmap.md`](docs/development-roadmap.md).

If the docs contradict each other, flag it and propose a fix before writing code.

## Agent-only rules

- Never guess commands. Run them to confirm they work before adding them to docs or a PR description.
- Never claim a test, lint, or build passed unless it actually ran in this session. Report failures and skipped checks plainly.
- Do not create a new top-level directory without asking first. New code lands in the existing module layout (see the README).
- For substantial features, propose a plan and wait for approval before editing.
- Never stage or commit `.env`, `.env.local`, or any file containing keys, tokens, or database passwords.
- If you add or change a rule, edit `docs/engineering-guidelines.md`, not this file.

## Personal overrides

Put machine- or person-specific instructions in `CLAUDE.local.md` (gitignored).
