# ADR 0003: Build our own charging-aware optimizer instead of using Mapbox EV Routing

- Status: accepted
- Date: 2026-11-06
- Deciders: project lead

## Context

Mapbox publishes an EV Routing API that handles energy prediction and automatically inserted charging stops. As of planning, Mapbox EV Routing is labeled **private preview**.

Chargewise needs:

- Reliable charging-aware routing on day one for the portfolio demo.
- An algorithm whose details we can explain and iterate on.
- Freedom to swap or combine station-data sources (AFDC, Open Charge Map, NRCan) without vendor lock-in.

## Decision

We will:

- Use Mapbox for **plain road routing** (Directions) and geocoding/search only.
- Implement Chargewise's own **charging-aware optimizer** in Python, inside the `routing` and `energy` modules.
- Start with a deterministic energy model (segment length × consumption, piecewise charging curve) and a bounded search over feasible charging-stop sequences.
- Upgrade later (Phase 4 end or later) to a SoC-discretized Dijkstra / A* search over (charger, SoC-bucket) nodes.
- Keep station data behind the `integrations/stations` adapter so provider swaps don't touch `routing`.

## Consequences

- Easier: project ships without waiting on vendor access; the routing algorithm becomes a visible, explainable, portfolio-grade component.
- Easier: we can combine US and Canadian charging data in one planner.
- Easier: ML improvements plug into the same optimizer by swapping the energy model.
- Harder: we own every edge case — infeasibility, incompatible connectors, missing station power, stale data. The acceptance matrix in the PRD exists precisely for this.
- Harder: we won't match Mapbox's internal datasets for energy consumption until we build our own (that's the point).

## Alternatives considered

- **Use Mapbox EV Routing from day one.** Rejected: private preview, access risk, and it hides the most interesting technical work.
- **Pure heuristic (no model) with hand-placed stops.** Rejected: unreliable, not portfolio-worthy.
- **Full graph search on day one.** Rejected as premature: adds complexity before we have real data to tune it against. The MVP uses a bounded enumeration; the graph search is a planned upgrade.

## References

- [`docs/architecture.md`](../architecture.md) — routing algorithm
- [`docs/product-requirements.md`](../product-requirements.md#9-acceptance-test-matrix) — routing acceptance cases
- Mapbox EV Routing status — https://docs.mapbox.com/ (confirm current GA status when revisiting)
