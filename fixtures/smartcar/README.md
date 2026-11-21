# Smartcar V3 fixtures

Canonical Smartcar payloads for the `integrations/smartcar` adapter tests. Tests load them with `httpx.MockTransport`; nothing here touches the network.

## Provenance

| File | Source |
|---|---|
| `connections_list.json` | **Real.** `GET /v3/connections?filter[userId]=…` for the 2026 Toyota bZ, from the F0 spike (2026-11-13). Redacted by `services/api/scripts/smartcar_spike.py`. |
| `vehicle_bz.json` | **Real.** `GET /v3/vehicles/{id}`, same run, redacted. |
| `signals_bz.json` | **Real.** `GET /v3/vehicles/{id}/signals`, same run, redacted. 27 signals: 7 `SUCCESS`, 19 `ERROR`, and `location-preciselocation` replaced by a redaction placeholder (no `code`/`status`), which the parser must skip. |
| `token_response.json` | **Synthetic**, from the documented shape and the F0 observation that `token_type` comes back lower-case. |
| `webhook_vehicle_state.json` | **Synthetic** `VEHICLE_STATE`, built from the [event types](https://smartcar.com/docs/api-reference/webhooks/event-types) docs. Replace with a real capture in F5. |
| `webhook_out_of_order_older.json` | **Synthetic.** Same vehicle, lower `sequence` and an older `oemUpdatedAt` for SoC, but delivered later. |
| `webhook_unknown_signal.json` | **Synthetic.** Includes a signal code ChargeWaze doesn't map (`future-newsignal`). |
| `webhook_verify_challenge.json` | **Synthetic** `VERIFY` event. The F0 spike captured one, but its challenge was redacted. |

Redaction: Smartcar user, vehicle and connection IDs are replaced with stable aliases (`redacted-id-1` user, `redacted-id-2` connection, `redacted-id-3` vehicle); `links.self` URLs, the VIN and location are removed. The synthetic webhooks reuse the same aliases. Synthetic timestamps start at `1794261600000` ms (2026-11-09T22:00:00Z).

## Test-only management token

`*.signature` files hold the hex HMAC-SHA256 of the **exact bytes** of the matching JSON file, keyed with this test-only Application Management Token:

```
chargewaze-test-management-token
```

It is not a real secret and is never used outside tests. If you edit a signed fixture, regenerate its signature:

```bash
python3 -c "import hmac,hashlib,sys;print(hmac.new(b'chargewaze-test-management-token',open(sys.argv[1],'rb').read(),hashlib.sha256).hexdigest())" webhook_vehicle_state.json > webhook_vehicle_state.json.signature
```
