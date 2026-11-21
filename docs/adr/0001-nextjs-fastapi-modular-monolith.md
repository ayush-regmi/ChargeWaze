# ADR 0001: Next.js frontend + FastAPI modular monolith backend

- Status: accepted
- Date: 2026-11-06
- Deciders: project lead

## Context

ChargeWaze needs:

- A production-quality web UI with routing, layouts, auth-friendly patterns, and a realistic React ecosystem.
- Business logic for routing optimization, Smartcar integration, and (later) ML — all easier in Python.
- The ability to reuse the same backend from a later React Native client.
- Minimal operational overhead while the project is in portfolio / solo-developer phase.

Options for shape:

1. Everything in Next.js (API routes do business logic).
2. Next.js frontend + a separate Node/TypeScript backend.
3. Next.js frontend + a separate FastAPI (Python) backend.
4. Microservices split per module from day one.

## Decision

We will build ChargeWaze as:

- **Next.js + TypeScript** frontend, used primarily as a frontend.
- **FastAPI** backend organized as a **modular monolith** under `services/api/app/modules/*` and `services/api/app/integrations/*`.
- One deployable backend. No microservices until a specific scaling or ownership reason forces the split.

All business logic lives in FastAPI. The frontend talks to FastAPI via a typed client generated from FastAPI's OpenAPI document.

## Consequences

- Easier: a future React Native client reuses the same backend, authentication, and generated types; Python-based routing and ML live where they're natural.
- Easier: shipping pace — one process to deploy, migrate, and monitor.
- Harder: we maintain two languages (TypeScript and Python); CI has to lint/type-check/test both.
- Harder: network hop between Next.js and FastAPI compared to a single Next.js app. Acceptable for an app whose slow path is external APIs.
- We must enforce the "no direct provider calls from the browser" rule in code review; the ESLint config and CODEOWNERS should make violations loud.

## Alternatives considered

- **Everything in Next.js.** Rejected: ML and routing would be awkward in TypeScript; a future RN app couldn't reuse server logic without extraction.
- **Next.js + Node backend.** Rejected: loses Python's ML ecosystem; adds no portfolio differentiation.
- **Microservices from day one.** Rejected: pure cost with no payoff at single-developer scale. Revisit only when a module has distinct scaling or deploy ownership needs.

## References

- [`docs/architecture.md`](../architecture.md)
- FastAPI docs — https://fastapi.tiangolo.com/
- Next.js docs — https://nextjs.org/docs
