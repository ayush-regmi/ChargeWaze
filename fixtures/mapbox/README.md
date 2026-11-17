# Mapbox fixtures

Real Mapbox responses for the `integrations/mapbox` adapter tests. Tests load them with `httpx.MockTransport`; nothing here touches the network. Facts behind them are in [`docs/maps-stations-notes.md`](../../docs/maps-stations-notes.md) §2.

All files were captured on 2026-11-17 by [`services/api/scripts/maps_stations_spike.py`](../../services/api/scripts/maps_stations_spike.py). Tokens and session tokens only go in request URLs and aren't in any file. The `uuid` request ID was removed from Directions responses. Only public places are used (Frederick, MD; Toronto, ON; public charging sites; Hawaii for the no-route case).

| File | Request | Notes |
|---|---|---|
| `directions_frederick_toronto.json` | `GET /directions/v5/mapbox/driving/-77.4105,39.4143;-79.3832,43.6532?alternatives=true&geometries=polyline6&overview=full&steps=false` | HTTP 200, `code: "Ok"`. Two routes: A 748.1 km / 8.58 h (`US 15, QEW`, 9,719 points) and B 805.0 km / 8.64 h (`US 15, I 99 North`, 10,034 points). `legs[0].admins` lists the countries crossed. |
| `directions_no_route.json` | Oahu → Maui | HTTP 200, `code: "NoRoute"`, `routes: []`. |
| `directions_no_segment.json` | Two points in the Atlantic | HTTP 200, `code: "NoSegment"`, `routes: []` (no road near the input). |
| `error_invalid_token.json` | Directions with an invalid token | HTTP **401**, `{"message": "Not Authorized - Invalid Token"}`. |
| `suggest_toronto.json` | `GET /search/searchbox/v1/suggest?q=Toronto&proximity=-79.38,43.65&country=us,ca&limit=5&language=en&session_token=…` | Places and POIs; each suggestion has `mapbox_id`, `name`, `feature_type`, `place_formatted`, `context`. No coordinates. |
| `retrieve_toronto.json` | `GET /search/searchbox/v1/retrieve/{mapbox_id}?session_token=…` | GeoJSON `FeatureCollection`; `properties.coordinates` has `latitude`/`longitude`, `full_address`. |
| `suggest_tesla_supercharger_frederick.json` / `retrieve_tesla_supercharger_frederick.json` | Same, `q=Tesla Supercharger Frederick`, no `proximity` | POI results with street addresses. |
| `error_session_token_missing.json` | `suggest` without `session_token` | HTTP **400**, `message.error: "Session Token is required"`. |
