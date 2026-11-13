"""Throwaway Smartcar V3 spike for Chargewise Phase 2 (F0). Not production code.

Standard library only. Reads credentials from services/api/.env (never printed).

Run from services/api/:

  uv run python scripts/smartcar_spike.py connect  # local callback on :8000; prints Connect URL
  uv run python scripts/smartcar_spike.py read     # app token -> connections -> signals
  uv run python scripts/smartcar_spike.py webhook  # receiver on :8001; put a tunnel in front

Outputs go to scripts/spike_out/ (gitignored):
  raw/       unredacted responses (stay on this machine; never commit)
  redacted/  IDs replaced, VIN and location removed (safe to copy into fixtures/)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "spike_out"
STATE_FILE = OUT / "session.json"
TOKEN_URL = "https://iam.smartcar.com/oauth2/token"
CONNECT_URL = "https://connect.smartcar.com/oauth/authorize"
API = "https://vehicle.api.smartcar.com/v3"
DEFAULT_SCOPES = "read_battery read_charge read_odometer read_vehicle_info"
PROBE_SIGNALS = (
    "tractionbattery-stateofcharge",
    "tractionbattery-range",
    "odometer-traveleddistance",
    "charge-ischarging",
)


# ---------- config ----------


def load_env() -> dict[str, str]:
    env_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if env_path is None:
        for parent in [Path.cwd(), *Path.cwd().parents]:
            candidate = parent / "services" / "api" / ".env"
            if candidate.exists():
                env_path = candidate
                break
    if env_path is None or not env_path.exists():
        sys.exit("Could not find services/api/.env. Run from the repo or pass its path as arg 2.")
    values: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.split(" #", 1)[0].strip().strip('"').strip("'")
        values[key.strip()] = val
    return values


def need(env: dict[str, str], key: str) -> str:
    val = env.get(key, "")
    if not val:
        sys.exit(f"{key} is empty in services/api/.env")
    return val


def save(kind: str, name: str, data: Any) -> Path:
    path = OUT / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    return path


# ---------- redaction ----------

_ID_KEYS = {
    "id",
    "userId",
    "user_id",
    "vehicleId",
    "connectionId",
    "eventId",
    "deliveryId",
    "webhookId",
    "external_id",
    "externalId",
}
_DROP_KEYS = {"vin", "VIN", "latitude", "longitude", "lat", "lon", "heading"}
_aliases: dict[str, str] = {}


def _alias(value: str) -> str:
    if value not in _aliases:
        _aliases[value] = f"redacted-id-{len(_aliases) + 1}"
    return _aliases[value]


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _looks_like_id(value: str) -> bool:
    # Signal resources use their code as ``id`` (e.g. "closure-reartrunk"); keep those.
    return bool(_UUID_RE.fullmatch(value)) or not re.fullmatch(r"[a-z]+(-[a-z]+)+", value)


def _redact_tree(obj: Any) -> Any:
    if isinstance(obj, dict):
        group = str(obj.get("group", "")) + str(obj.get("code", ""))
        if "location" in group.lower():
            return {"redacted": "location signal removed"}
        out = {}
        for k, v in obj.items():
            if k in _DROP_KEYS:
                continue
            if k in _ID_KEYS and isinstance(v, str) and _looks_like_id(v):
                out[k] = _alias(v)
            elif k in {"self", "challenge", "state"} and isinstance(v, str):
                out[k] = "redacted"
            else:
                out[k] = _redact_tree(v)
        return out
    if isinstance(obj, list):
        return [_redact_tree(x) for x in obj]
    return obj


def redact(obj: Any) -> Any:
    """Alias ID fields, then scrub any known ID or UUID left inside other strings
    (e.g. pagination links that embed ``filter[userId]=...``)."""
    text = json.dumps(_redact_tree(obj))
    for original, alias in sorted(_aliases.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(original, alias)
    text = _UUID_RE.sub(lambda m: _alias(m.group(0)), text)
    return json.loads(text)


# ---------- http ----------


def http(method: str, url: str, *, headers: dict[str, str], body: bytes | None = None) -> Any:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    took = int((time.monotonic() - started) * 1000)
    path = urllib.parse.urlsplit(url).path
    print(f"  {method} {_UUID_RE.sub('<id>', path)} -> {status} ({took} ms)")
    try:
        data = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        data = {"non_json_body": raw.decode("utf-8", "replace")[:500]}
    if status >= 400:
        print("  error body:", json.dumps(redact(data))[:800])
    return data


def app_token(env: dict[str, str]) -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": need(env, "SMARTCAR_CLIENT_ID"),
            "client_secret": need(env, "SMARTCAR_CLIENT_SECRET"),
        }
    ).encode()
    data = http(
        "POST", TOKEN_URL, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    if not data or "access_token" not in data:
        sys.exit("Token request failed (see above).")
    print(f"  token_type={data.get('token_type')} expires_in={data.get('expires_in')}")
    return str(data["access_token"])


# ---------- commands ----------


def cmd_connect(env: dict[str, str]) -> None:
    redirect_uri = need(env, "SMARTCAR_REDIRECT_URI")
    parts = urllib.parse.urlsplit(redirect_uri)
    if parts.hostname not in {"localhost", "127.0.0.1"}:
        sys.exit("SMARTCAR_REDIRECT_URI must point at localhost for this spike.")
    mode = env.get("SMARTCAR_MODE") or "live"
    if mode not in {"live", "simulated"}:
        sys.exit(f"SMARTCAR_MODE={mode!r} is not a V3 Connect mode. Use 'live' or 'simulated'.")
    state = secrets.token_urlsafe(24)
    params = {
        "application_id": env.get("SMARTCAR_APPLICATION_ID") or need(env, "SMARTCAR_CLIENT_ID"),
        "response_type": "none",
        "redirect_uri": redirect_uri,
        "scope": env.get("SMARTCAR_SCOPES") or DEFAULT_SCOPES,
        "mode": mode,
        "state": state,
        "external_id": "chargewise-spike",
        "flags": "country:US",
    }
    print("\nOpen this URL in your browser and authorize the vehicle:\n")
    print(CONNECT_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote))
    print(f"\nWaiting for the redirect on {redirect_uri} ...")

    result: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urllib.parse.urlsplit(self.path)
            if url.path != parts.path:
                self.send_response(404)
                self.end_headers()
                return
            query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            result.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Chargewise spike: redirect received. You can close this tab.")

        def log_message(self, *_: Any) -> None:
            pass

    server = HTTPServer((parts.hostname or "127.0.0.1", parts.port or 80), Handler)
    while not result:
        server.handle_request()

    print("\nRedirect query parameter NAMES:", sorted(result))
    if result.get("state") != state:
        sys.exit("state mismatch - ignoring this redirect.")
    if "error" in result:
        print("Connect error:", result.get("error"), "-", result.get("error_description"))
        save("redacted", "connect_error_redirect.json", redact(result))
        return
    user_id = result.get("user_id") or result.get("userId")
    if not user_id:
        sys.exit("No user_id/userId in the redirect. See the parameter names above.")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"user_id": user_id}))
    save("redacted", "connect_success_redirect.json", redact(result))
    print("Saved the Smartcar user ID to scripts/spike_out/session.json.")
    print("Next: uv run python scripts/smartcar_spike.py read")


def cmd_read(env: dict[str, str]) -> None:
    if not STATE_FILE.exists():
        sys.exit("Run `uv run python scripts/smartcar_spike.py connect` first.")
    user_id = json.loads(STATE_FILE.read_text())["user_id"]
    _alias(user_id)
    print("Getting application token")
    token = app_token(env)
    auth = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    print("Listing connections")
    q = urllib.parse.urlencode({"filter[userId]": user_id})
    conns = http("GET", f"{API}/connections?{q}", headers={**auth, "sc-user-id": user_id})
    save("raw", "connections.json", conns)
    save("redacted", "connections.json", redact(conns))

    vehicle_ids = []
    for item in (conns or {}).get("data", []):
        vid = (item.get("relationships", {}).get("vehicle", {}).get("data", {}) or {}).get("id")
        if vid:
            vehicle_ids.append(vid)
    print(f"  {len(vehicle_ids)} vehicle(s)")
    if not vehicle_ids:
        print("Diagnostic: listing ALL connections for this app (counts only)")
        everything = http("GET", f"{API}/connections", headers=auth)
        save("redacted", "connections_all_app.json", redact(everything))
        items = (everything or {}).get("data", [])
        total = (everything or {}).get("meta", {}).get("totalCount", len(items))
        print(f"  app has {total} connection(s)")
        for item in items:
            attrs = item.get("attributes", {})
            print("  connection attributes:", sorted(attrs), "mode:", attrs.get("mode"))
        print("  If 0: Connect finished without authorizing a vehicle (check mode and the")
        print("  vehicle chosen on the Smartcar screen), then run `connect` again.")

    for n, vid in enumerate(vehicle_ids, 1):
        headers = {**auth, "sc-user-id": user_id}
        print(f"Vehicle {n}: details")
        veh = http("GET", f"{API}/vehicles/{vid}", headers=headers)
        save("raw", f"vehicle_{n}.json", veh)
        save("redacted", f"vehicle_{n}.json", redact(veh))

        print(f"Vehicle {n}: signals")
        sig = http("GET", f"{API}/vehicles/{vid}/signals?page%5Bsize%5D=100", headers=headers)
        save("raw", f"vehicle_{n}_signals.json", sig)
        save("redacted", f"vehicle_{n}_signals.json", redact(sig))

        print("\n  code                                   status        value / unit")
        for s in (sig or {}).get("data", []):
            a = s.get("attributes", {})
            if "location" in (a.get("group", "") + a.get("code", "")).lower():
                continue
            status = (a.get("status") or {}).get("value", "?")
            body = a.get("body") or {}
            shown = body.get("value", body.get("capacity", body.get("activeLimit", "")))
            print(f"  {a.get('code', '?'):<38} {status:<13} {shown} {body.get('unit', '')}")
        if (sig or {}).get("meta", {}).get("totalCount", 0) > len((sig or {}).get("data", [])):
            print("  NOTE: more signals than one page; totalCount =", sig["meta"]["totalCount"])
        if not (sig or {}).get("data"):
            print("  Signal list is empty. Probing individual signals:")
            for code in PROBE_SIGNALS:
                one = http("GET", f"{API}/vehicles/{vid}/signals/{code}", headers=headers)
                save("raw", f"vehicle_{n}_signal_{code}.json", one)
                save("redacted", f"vehicle_{n}_signal_{code}.json", redact(one))
    print("\nSaved to spike_out/raw (private) and spike_out/redacted (safe for fixtures).")


def cmd_webhook(env: dict[str, str]) -> None:
    key = need(env, "SMARTCAR_MANAGEMENT_TOKEN").encode()
    port = int(env.get("SPIKE_WEBHOOK_PORT") or 8001)
    log = OUT / "webhook_log.jsonl"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            got = self.headers.get("SC-Signature") or ""
            expected = hmac.new(key, raw, hashlib.sha256).hexdigest()
            sig_ok = hmac.compare_digest(got, expected)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
            etype = payload.get("eventType")
            received = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            print(f"[{received}] {etype} signature_ok={sig_ok} header_present={bool(got)}")

            if etype == "VERIFY":
                challenge = str((payload.get("data") or {}).get("challenge", ""))
                answer = hmac.new(key, challenge.encode(), hashlib.sha256).hexdigest()
                self._reply(200, {"challenge": answer})
                save("redacted", "webhook_verify.json", redact(payload))
                return
            if not sig_ok:
                self._reply(401, {"error": "bad signature"})
                return

            n = sum(1 for _ in log.open()) + 1 if log.exists() else 1
            save("raw", f"webhook_{n:03d}_{etype}.json", payload)
            save("redacted", f"webhook_{n:03d}_{etype}.json", redact(payload))
            with log.open("a") as fh:
                fh.write(
                    json.dumps(
                        {
                            "n": n,
                            "received_at": received,
                            "eventType": etype,
                            "sequence": (payload.get("meta") or {}).get("sequence"),
                            "signals": [
                                s.get("code")
                                for s in (payload.get("data") or {}).get("signals", [])
                            ],
                        }
                    )
                    + "\n"
                )
            for s in (payload.get("data") or {}).get("signals", []):
                if "location" in (str(s.get("group")) + str(s.get("code"))).lower():
                    continue
                print(f"    {s.get('code')}: {s.get('body')}  meta={s.get('meta')}")
            self._reply(200, {"ok": True})

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_: Any) -> None:
            pass

    print(f"Webhook receiver on http://127.0.0.1:{port}/ (Ctrl+C to stop)")
    print("Point a tunnel at this port; set the tunnel URL as the webhook URL in the Dashboard.")
    try:
        HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        got = sum(1 for _ in log.open()) if log.exists() else 0
        print(f"\nStopped. {got} signed event(s) logged this far.")


def main() -> None:
    commands = {"connect": cmd_connect, "read": cmd_read, "webhook": cmd_webhook}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]](load_env())


if __name__ == "__main__":
    main()
