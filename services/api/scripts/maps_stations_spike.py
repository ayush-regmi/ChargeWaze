"""Throwaway Mapbox + AFDC spike for Chargewise Phase 3 (F0). Not production code.

Standard library only. Reads keys from services/api/.env, or the file named by
SPIKE_ENV_FILE (values are never printed):
  AFDC_API_KEY         optional; falls back to NREL's DEMO_KEY (about 10 requests/hour)
  MAPBOX_SECRET_TOKEN  needed for the mapbox-* and cross-border commands

Run from services/api/:

  uv run python scripts/maps_stations_spike.py afdc-route [--post] [--country all|US|CA] [--limit N]
  uv run python scripts/maps_stations_spike.py mapbox-directions
  uv run python scripts/maps_stations_spike.py mapbox-search "Toronto"
  uv run python scripts/maps_stations_spike.py cross-border [--points 300] [--distance 2]
  uv run python scripts/maps_stations_spike.py summarize spike_out/raw/<file>.json

Outputs go to scripts/spike_out/ (gitignored). Station data is public, and the
routes are between public test points (Frederick, MD and Toronto, ON), so nothing
here is personal. Keys only go in request URLs/bodies and are never saved.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "spike_out" / "phase3"
# NREL became the National Laboratory of the Rockies; developer.nrel.gov no longer resolves.
AFDC_BASE = "https://developer.nlr.gov/api/alt-fuel-stations/v1"
MAPBOX_BASE = "https://api.mapbox.com"

FREDERICK = (-77.4105, 39.4143)  # lon, lat
TORONTO = (-79.3832, 43.6532)
# Coarse hand-made cross-border line (US-15 / I-99 / I-390 / I-90 / QEW), lon lat.
COARSE_ROUTE = [
    FREDERICK,
    (-76.8867, 40.2732),  # Harrisburg
    (-77.0011, 41.2412),  # Williamsport
    (-77.0547, 42.1429),  # Corning
    (-77.6109, 43.1566),  # Rochester
    (-78.8784, 42.8864),  # Buffalo
    (-79.0849, 43.0896),  # Niagara Falls, ON
    (-79.8711, 43.2557),  # Hamilton
    TORONTO,
]


# ---------- config / io ----------


def load_env() -> dict[str, str]:
    override = os.environ.get("SPIKE_ENV_FILE")
    if override:
        return _read_env(Path(override))
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / "services" / "api" / ".env"
        if candidate.exists():
            break
        candidate = parent / ".env"
        if candidate.exists() and parent.name == "api":
            break
    else:
        return {}
    return _read_env(candidate)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.split(" #", 1)[0].strip().strip('"').strip("'")
    return values


def save(name: str, data: Any) -> Path:
    path = OUT / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    return path


def http(url: str, *, data: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any, float]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
    if body:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, headers, raw = resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        status, headers, raw = exc.code, dict(exc.headers), exc.read()
    elapsed = time.monotonic() - start
    try:
        parsed: Any = json.loads(raw)
    except ValueError:
        parsed = raw.decode(errors="replace")[:2000]
    return status, headers, parsed, elapsed


def report(label: str, status: int, headers: dict[str, str], elapsed: float, size: int) -> None:
    rate = {k: v for k, v in headers.items() if k.lower().startswith("x-ratelimit")}
    print(f"{label}: HTTP {status} in {elapsed:.2f}s, {size} bytes, rate={rate}")


def arg(flag: str, default: str) -> str:
    if flag in sys.argv:
        return sys.argv[sys.argv.index(flag) + 1]
    return default


def mapbox_token(env: dict[str, str]) -> str:
    token = env.get("MAPBOX_SECRET_TOKEN", "")
    if not token:
        sys.exit("MAPBOX_SECRET_TOKEN is empty in services/api/.env")
    return token


# ---------- geometry ----------


def decode_polyline(encoded: str, precision: int = 6) -> list[tuple[float, float]]:
    """Decode a Google/Mapbox polyline to (lon, lat) pairs."""
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0
    factor = 10**precision
    while index < len(encoded):
        for which in (0, 1):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lon / factor, lat / factor))
    return coords


def _perp_km(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    # Equirectangular projection around the segment; fine for simplification tolerances.
    k = math.cos(math.radians((a[1] + b[1]) / 2)) * 111.32
    ax, ay, bx, by, px, py = (
        a[0] * k,
        a[1] * 110.57,
        b[0] * k,
        b[1] * 110.57,
        p[0] * k,
        p[1] * 110.57,
    )
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def douglas_peucker(points: list[tuple[float, float]], tol_km: float) -> list[tuple[float, float]]:
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        best, idx = 0.0, -1
        for i in range(lo + 1, hi):
            d = _perp_km(points[i], points[lo], points[hi])
            if d > best:
                best, idx = d, i
        if idx >= 0 and best > tol_km:
            keep[idx] = True
            stack += [(lo, idx), (idx, hi)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def simplify_to(points: list[tuple[float, float]], max_points: int) -> tuple[list, float]:
    tol = 0.05
    out = douglas_peucker(points, tol)
    while len(out) > max_points:
        tol *= 1.5
        out = douglas_peucker(points, tol)
    return out, tol


def wkt(points: list[tuple[float, float]]) -> str:
    return "LINESTRING(" + ", ".join(f"{lon:.5f} {lat:.5f}" for lon, lat in points) + ")"


# ---------- AFDC ----------


def afdc_route(
    env: dict[str, str],
    points: list,
    *,
    post: bool,
    country: str,
    limit: str,
    distance: str = "2",
    level: str = "",
) -> tuple[int, dict, Any, float]:
    params = {
        "api_key": env.get("AFDC_API_KEY") or "DEMO_KEY",
        "fuel_type": "ELEC",
        "status": "E",
        "access": "public",
        "distance": distance,
        "limit": limit,
        "route": wkt(points),
    }
    if country:
        params["country"] = country
    if level:
        params["ev_charging_level"] = level
    base = env.get("AFDC_API_BASE_URL") or AFDC_BASE
    url = f"{base}/nearby-route.json"
    if post:
        # AFDC only reads api_key from the query string; a key in the form body is ignored.
        key = params.pop("api_key")
        return http(f"{url}?{urllib.parse.urlencode({'api_key': key})}", data=params)
    return http(f"{url}?{urllib.parse.urlencode(params)}")


def cmd_afdc_route(env: dict[str, str]) -> None:
    post = "--post" in sys.argv
    country = arg("--country", "all")
    country = "" if country == "default" else country
    limit = arg("--limit", "200")
    level = arg("--level", "")
    status, headers, data, elapsed = afdc_route(
        env, COARSE_ROUTE, post=post, country=country, limit=limit, level=level
    )
    method = "post" if post else "get"
    name = f"afdc_route_{method}_{country or 'default'}_{level or 'alllevels'}_{limit}.json"
    report(name, status, headers, elapsed, len(json.dumps(data)))
    print("saved", save(name, data))
    if isinstance(data, dict) and "fuel_stations" in data:
        summarize_afdc(data)


def summarize_afdc(data: dict[str, Any]) -> None:
    stations = data.get("fuel_stations", [])
    print("total_results:", data.get("total_results"), "returned:", len(stations))
    print("by country:", Counter(s.get("country") for s in stations))
    print(
        "connector types:", Counter(t for s in stations for t in s.get("ev_connector_types") or [])
    )
    print("networks:", Counter(s.get("ev_network") for s in stations).most_common(15))
    units = [s.get("ev_charging_units") for s in stations]
    print("with ev_charging_units:", sum(1 for u in units if u), "of", len(units))
    powers = [
        c.get("power_kw")
        for u in units
        if u
        for unit in u
        for c in (unit.get("connectors") or {}).values()
    ]
    print("connector power_kw known:", sum(p is not None for p in powers), "of", len(powers))
    dists = [s.get("distance") for s in stations]
    print("distance sorted ascending:", dists == sorted(d for d in dists if d is not None))
    if stations:
        print("station keys:", sorted(stations[0].keys()))


# ---------- Mapbox ----------


def mapbox_directions(env: dict[str, str]) -> tuple[int, dict, Any, float]:
    coords = f"{FREDERICK[0]},{FREDERICK[1]};{TORONTO[0]},{TORONTO[1]}"
    params = {
        "access_token": mapbox_token(env),
        "alternatives": "true",
        "geometries": "polyline6",
        "overview": "full",
        "steps": "false",
    }
    url = f"{MAPBOX_BASE}/directions/v5/mapbox/driving/{coords}?{urllib.parse.urlencode(params)}"
    return http(url)


def cmd_mapbox_directions(env: dict[str, str]) -> None:
    status, headers, data, elapsed = mapbox_directions(env)
    report("directions", status, headers, elapsed, len(json.dumps(data)))
    print("saved", save("mapbox_directions_frederick_toronto.json", data))
    for i, route in enumerate((data or {}).get("routes", [])):
        pts = decode_polyline(route["geometry"])
        print(
            f"route {i}: {route['distance'] / 1000:.1f} km, {route['duration'] / 3600:.2f} h, "
            f"{len(pts)} points, {len(route['geometry'])} chars, "
            f"summary={route.get('legs', [{}])[0].get('summary')!r}"
        )
    # Same two points but in the middle of the ocean, to see NoRoute.
    params = {"access_token": mapbox_token(env), "geometries": "polyline6"}
    ocean = "-40.0,30.0;-41.0,31.0"
    url = f"{MAPBOX_BASE}/directions/v5/mapbox/driving/{ocean}?{urllib.parse.urlencode(params)}"
    status, headers, data, elapsed = http(url)
    report("directions no-route", status, headers, elapsed, len(json.dumps(data)))
    print("saved", save("mapbox_directions_no_route.json", data))


def cmd_mapbox_search(env: dict[str, str]) -> None:
    query = next((a for a in sys.argv[2:] if not a.startswith("--")), "Toronto")
    session = str(uuid.uuid4())
    token = mapbox_token(env)
    params = {
        "q": query,
        "access_token": token,
        "session_token": session,
        "limit": "5",
        "language": "en",
        "country": "us,ca",
    }
    url = f"{MAPBOX_BASE}/search/searchbox/v1/suggest?{urllib.parse.urlencode(params)}"
    status, headers, data, elapsed = http(url)
    report("suggest", status, headers, elapsed, len(json.dumps(data)))
    slug = "".join(c if c.isalnum() else "_" for c in query.lower())
    print("saved", save(f"mapbox_suggest_{slug}.json", data))
    suggestions = (data or {}).get("suggestions", [])
    for s in suggestions:
        print(
            f"  {s.get('feature_type'):10} {s.get('name')!r} — "
            f"{s.get('full_address') or s.get('place_formatted')!r}"
        )
    if not suggestions:
        return
    mid = suggestions[0]["mapbox_id"]
    params = {"access_token": token, "session_token": session}
    query_string = urllib.parse.urlencode(params)
    url = f"{MAPBOX_BASE}/search/searchbox/v1/retrieve/{urllib.parse.quote(mid)}?{query_string}"
    status, headers, data, elapsed = http(url)
    report("retrieve", status, headers, elapsed, len(json.dumps(data)))
    print("saved", save(f"mapbox_retrieve_{slug}.json", data))


# ---------- cross-border ----------


def cmd_cross_border(env: dict[str, str]) -> None:
    max_points = int(arg("--points", "300"))
    distance = arg("--distance", "2")
    level = arg("--level", "dc_fast")
    level = "" if level == "all" else level
    status, headers, data, elapsed = mapbox_directions(env)
    report("directions", status, headers, elapsed, len(json.dumps(data)))
    save("mapbox_directions_frederick_toronto.json", data)
    summary: list[dict[str, Any]] = []
    for i, route in enumerate(data.get("routes", [])):
        pts = decode_polyline(route["geometry"])
        simple, tol = simplify_to(pts, max_points)
        body = wkt(simple)
        print(
            f"route {i}: {len(pts)} -> {len(simple)} points "
            f"(tol {tol:.2f} km), WKT {len(body)} chars"
        )
        st, hd, afdc, el = afdc_route(
            env, simple, post=True, country="all", limit="200", distance=distance, level=level
        )
        report(f"afdc route {i}", st, hd, el, len(json.dumps(afdc)))
        save(f"afdc_cross_border_route{i}_{max_points}pts_{level or 'all'}.json", afdc)
        if isinstance(afdc, dict) and "fuel_stations" in afdc:
            summarize_afdc(afdc)
            stations = afdc["fuel_stations"]
            summary.append(
                {
                    "route": i,
                    "distance_km": round(route["distance"] / 1000, 1),
                    "duration_h": round(route["duration"] / 3600, 2),
                    "points_full": len(pts),
                    "points_simplified": len(simple),
                    "afdc_seconds": round(el, 2),
                    "total_results": afdc.get("total_results"),
                    "by_country": dict(Counter(s.get("country") for s in stations)),
                }
            )
    print("saved", save(f"cross_border_summary_{max_points}pts_{level or 'all'}.json", summary))


def cmd_summarize(_env: dict[str, str]) -> None:
    data = json.loads(Path(sys.argv[2]).read_text())
    summarize_afdc(data)


def main() -> None:
    commands = {
        "afdc-route": cmd_afdc_route,
        "mapbox-directions": cmd_mapbox_directions,
        "mapbox-search": cmd_mapbox_search,
        "cross-border": cmd_cross_border,
        "summarize": cmd_summarize,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]](load_env())


if __name__ == "__main__":
    main()
