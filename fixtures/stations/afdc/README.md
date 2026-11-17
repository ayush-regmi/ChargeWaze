# AFDC station fixtures

Real AFDC responses for the `integrations/stations` adapter tests. Tests load them with `httpx.MockTransport`; nothing here touches the network. Facts behind them are in [`docs/maps-stations-notes.md`](../../../docs/maps-stations-notes.md).

## Provenance

All files were captured on 2026-11-16 (the real-route cross-border file on 2026-11-17, the `nearest` file on 2026-11-17 for F3) by [`services/api/scripts/maps_stations_spike.py`](../../../services/api/scripts/maps_stations_spike.py) against `https://developer.nlr.gov/api/alt-fuel-stations/v1`. Station data is public, and the route runs between public test points, so there's nothing personal to redact. API keys only ever go in the request URL and aren't in any file.

To keep the files small, keys for other fuel types (`cng_*`, `lng_*`, `e85_*`, `bd_*`, `hy_*`, `lpg_*`, `rd_*`, `ng_*`, `nps_*`) and French-language copies (`*_fr`) were removed from each station. Everything else is as returned.

| File | Request |
|---|---|
| `nearby_route_cross_border_dc_fast.json` | `POST nearby-route.json`, `fuel_type=ELEC&status=E&access=public&distance=2&limit=200&country=all&ev_charging_level=dc_fast`, with the **real Mapbox route A** (Frederick → Toronto via the QEW, [`../../mapbox/directions_frederick_toronto.json`](../../mapbox/directions_frederick_toronto.json) `routes[0]`) simplified to 237 points: [`route_frederick_toronto_simplified.wkt`](route_frederick_toronto_simplified.wkt). 122 stations: 41 US, 81 CA. This is the E2 fixture. |
| `route_frederick_toronto_simplified.wkt` | The `route` sent for the file above: Douglas–Peucker at 0.25 km, 9,719 → 237 points, longitude first. |
| `nearby_route_all_levels_first_page.json` | The hand-made 9-point route below, without `ev_charging_level`. `total_results` is 955; the file keeps the **first 25** of the 200 returned (all US), with a `_fixture_note` saying so. Used for paging and Level 2 cases (unknown power). |
| `nearest_frederick_dc_fast.json` | `GET nearest.json`, `fuel_type=ELEC&status=E&access=public&country=all&ev_charging_level=dc_fast&latitude=39.4143&longitude=-77.4105&radius=15&limit=25` (the public Frederick test point; `radius` in miles). `total_results` 26, 25 returned, all US, sorted by `distance` ascending. Top-level `latitude`/`longitude`/`offset` echo the request. Used by `search_nearby`. |
| `station_448148.json` | `GET /v1/448148.json`, a Canadian Electrify Canada station (Burlington, ON), fetched without `country`. |
| `station_not_found.json` | `GET /v1/999999999.json`: HTTP **404**. |
| `error_api_key_missing.json` | `POST` with `api_key` in the form body instead of the query string: HTTP **403**. |

Hand-made route for `nearby_route_all_levels_first_page.json` (WKT, longitude first), a line along US-15 / I-99 / I-390 / I-90 / QEW:

```
LINESTRING(-77.41050 39.41430, -76.88670 40.27320, -77.00110 41.24120, -77.05470 42.14290, -77.61090 43.15660, -78.87840 42.88640, -79.08490 43.08960, -79.87110 43.25570, -79.38320 43.65320)
```
