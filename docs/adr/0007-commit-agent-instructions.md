# ADR 0007: Commit agent instructions; guidelines are the single source of truth

- Status: accepted
- Date: 2026-11-12
- Deciders: project lead
- Supersedes: the "agent files are gitignored" choice made when the repo was bootstrapped

## Context

When the repo was bootstrapped, `CLAUDE.md`, `AGENTS.md`, and `.cursor/` were gitignored so that AI-assistant instructions stayed out of the public repo. `docs/engineering-guidelines.md` was added as the committed, human-facing copy of the same rules.

That left two full copies of the rules: a committed one and a private one per machine. They had already started to drift (the private `CLAUDE.md` listed a root `.env.example` that no longer exists), and a fresh clone gave agents no instructions at all. Next.js 16 also generates `apps/web/AGENTS.md` and expects it to be committed.

## Decision

1. Commit `CLAUDE.md`, `AGENTS.md`, `apps/web/AGENTS.md`, and `.cursor/rules/`.
2. Keep `CLAUDE.md` short. It lists what to read first, adds a few agent-only rules (don't guess commands, don't report checks that didn't run, ask before adding top-level directories), and points to `CLAUDE.local.md` for personal overrides.
3. [`docs/engineering-guidelines.md`](../engineering-guidelines.md) is the single source of truth for stack, architecture, workflow, security, data, and commands. `CLAUDE.md` links to it instead of copying it.
4. `AGENTS.md` points to `CLAUDE.md`.
5. Gitignore personal and tool-state files: `CLAUDE.local.md`, `AGENTS.local.md`, everything under `.cursor/` except `rules/`, and `.claude/` (worktrees and local settings).

## Consequences

- Easier: every clone, CI agent, and cloud agent gets the same instructions.
- Easier: one place to change a rule; no drift between a public and a private copy.
- Harder: agent instructions are visible to anyone with repo access, and to everyone if the repo is made public. They must never contain secrets, internal URLs, or personal data. That is already the rule for every committed file.
- Harder: `CLAUDE.md` depends on the guidelines staying complete. Rules removed from `CLAUDE.md` must exist in the guidelines.

## Alternatives considered

- **Keep agent files gitignored.** Rejected: duplicates the rules and leaves fresh clones without instructions.
- **Commit the full rules in `CLAUDE.md` and drop the guidelines.** Rejected: human contributors shouldn't have to read an agent-oriented file to learn project rules.
- **Symlink `CLAUDE.md` to the guidelines.** Rejected: symlinks behave inconsistently across tools and Windows checkouts, and leave no room for agent-only rules.

## References

- [`docs/engineering-guidelines.md`](../engineering-guidelines.md)
- [`CLAUDE.md`](../../CLAUDE.md), [`AGENTS.md`](../../AGENTS.md)
