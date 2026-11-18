# ADR 0008: Route-ordered dynamic programming for the charging-stop optimizer

- Status: accepted
- Date: 2026-11-18
- Deciders: project lead
- Supersedes: the "bounded enumeration" step of [ADR 0003](0003-own-charging-optimizer-over-mapbox-ev-routing.md) (the rest of ADR 0003 stands)

## Context

[`architecture.md`](../architecture.md#6-routing-algorithm) §6.3 and ADR 0003 planned the MVP optimizer as a bounded enumeration of charging-stop sequences (at most 3 stops), with the SoC-discretized graph search (§6.5) as a later upgrade. Phase 4 design ([`phase4_plan.md`](../phase4_plan.md) D3) found two problems with enumeration:

- **Size.** The Frederick → Toronto route has about 100 DC fast stations in the along-route corridor (phase 3 F0/F5). Choosing up to 3 of them in route order is about 167,000 sequences.
- **It doesn't decide how much to charge.** A fixed sequence still needs a rule for each stop's departure SoC. "Charge just enough" is wrong when charging is faster at low SoC: it misses the PRD §9 case where two short stops beat one long one. Adding a charge-amount search on top of every sequence makes the enumeration much larger.

The candidates all lie along **one** chosen route (phase4_plan D13), each with a `distance_along_route_km`. So the search graph has no cycles: you only drive forward.

## Decision

We will use **dynamic programming over candidate stations sorted by distance along the route**. The state is (station, departure SoC on a 1 % grid); the value is the minimum of `charging + detour + per-stop overhead` minutes to reach that state. The optimizer also picks the departure SoC at each stop. Charging time differences use a precomputed cumulative charge-time function per station power, and a prefix minimum over predecessors sorted by arrival SoC keeps each state O(1) after sorting. Details and signatures: [`phase4_plan.md`](../phase4_plan.md) §4.3.

This is architecture §6.5's (charger, SoC bucket) graph search, specialised to one route. A graph with no cycles needs no Dijkstra or A*.

## Consequences

- Easier: the result is optimal at 1 % SoC resolution for the chosen route and assumptions; "two short stops beat one long" works without special cases.
- Easier: no arbitrary stop limit; long trips simply get more stops.
- Easier: fast. About 100 candidates × ~80 grid values × a few dozen reachable predecessors stays well under the 500 ms target (to be measured in F3).
- Harder: harder to explain than "try every combination". The phase plan's hand-checked trips (§5) and the rejected-alternatives tables make each result reviewable.
- Harder: departure SoC is rounded up to the grid, so a plan can charge up to 1 % more than strictly needed.
- Still open: planning across *several* routes (alternatives), time-dependent effects (traffic, weather) and charger queues. Those would need the general graph search of §6.5 with time-dependent edges.

## Alternatives considered

- **Bounded enumeration (≤ 3 stops) with "charge just enough".** Rejected: large, and misses cheaper plans with more but shorter stops.
- **Dijkstra / A* over (charger, SoC) nodes.** Equivalent result on one route, with more machinery (priority queue, heuristic). Revisit when planning across several routes or with time-dependent costs.
- **Mixed-integer programming.** Rejected: a new dependency and a solver for a problem the DP already solves exactly at the chosen resolution.

## References

- [`docs/phase4_plan.md`](../phase4_plan.md) §2 D3, §4.3, §5
- [`docs/architecture.md`](../architecture.md#6-routing-algorithm) §6.3–6.5
- [`docs/product-requirements.md`](../product-requirements.md#9-acceptance-test-matrix) §9
