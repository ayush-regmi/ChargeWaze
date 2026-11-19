# Telemetry series fixtures (Phase 5 F2)

Inputs and expected outputs for the trip inference engine (`services/api/app/modules/trips/inference.py`). `services/api/tests/trips/test_inference.py` loads them.

| File | Worked example in [`docs/phase5_plan.md`](../../docs/phase5_plan.md) §5 |
|---|---|
| `example1_dense_commute.json` | 1: dense feed, one commute leg (high) |
| `example2_key_off_only.json` | 2: key-off-only feed, two bounded legs (low) |
| `example3_road_trip_dc_stop.json` | 3: road trip with one DC stop (two high legs, one charging session) |
| `example4_gaps_out_of_order.json` | 4: gap, out-of-order delivery, duplicate, odometer regression, short leg |
| `example5_simulated_no_capacity.json` | 5: simulated rows dropped, unknown capacity |

## Provenance

**Synthetic**: every reading comes from the §5 tables. Where a table only shows boundary rows (example 3 reports the odometer every 15 min while driving), the rows in between are made up, rising odometer and falling SoC, with the boundary values unchanged. The expected values come from the §5 results, not from running the engine.

## Shape

```json
{
  "description": "…",
  "source": "docs/phase5_plan.md §5 (synthetic)",
  "inputs": {"vehicle_is_simulated": false, "usable_capacity_kwh": 71.0, "now": "<UTC ISO time>"},
  "readings": [{"recorded_at": "<UTC ISO time>", "signal": "odometer_km | soc_percent | is_charging", "value": 0.0, "is_simulated": false}],
  "expected": {"legs": [{"…": "InferredLeg fields"}], "charging_sessions": [], "dropped_simulated": 0, "dropped_duplicates": 0, "dropped_odometer_regressions": 0, "dropped_short_legs": 0}
}
```

One reading is one signal, like a `vehicle_telemetry` row, and the counters count readings. So example 4's redelivery of reading #2 carries only the odometer, and example 5's three simulated rows carry only the odometer, to keep §5's counters (1 and 3). Example 4's errored SoC reading at 12:50 is left out, as `list_readings` drops it before the engine sees it. Example 4's readings are listed in the order they were received.
