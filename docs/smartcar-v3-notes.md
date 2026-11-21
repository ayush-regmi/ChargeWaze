# Smartcar V3 — Verified Notes (Phase 2 · F0)

Status: **F0 done 2026-11-13** — docs pass + real-vehicle read on the 2026 bZ; live webhook checks moved to F5 (see §7) · Feeds: [`phase2_plan.md`](phase2_plan.md), [ADR 0002](adr/0002-smartcar-v3-webhook-telemetry.md)

This file records what Smartcar's official docs say about API V3, with links, so F1–F6 build on checked facts instead of the planning docs. Every item is marked:

- ✅ **Docs** — stated in Smartcar's docs (link given).
- 🔬 **Verify** — docs are silent, contradictory, or incomplete; must be confirmed with the real app / real Toyota in the spike.
- 🚗 **Real car** — filled in from the real 2026 Toyota bZ run (empty until the spike script is run).

---

## 1. Authentication

| Fact | Status | Source |
|---|---|---|
| One **application-level** access token via OAuth2 client credentials: `POST https://iam.smartcar.com/oauth2/token`, form body `grant_type=client_credentials&client_id=…&client_secret=…`. | ✅ | [API authentication](https://smartcar.com/docs/getting-started/how-to/api-authentication) |
| Response `{ access_token, token_type: "Bearer", expires_in: 3600 }`. **No refresh token**; request a new one on expiry. | ✅ | same |
| One token reaches every user's vehicles for the app; the `sc-user-id` header picks the user. `sc-user-id` is **required** on signal reads and commands. | ✅ | [Making API requests](https://smartcar.com/docs/getting-started/how-to/making-api-requests), [list signals](https://smartcar.com/docs/api-reference/list-signals) |
| Per-vehicle access/refresh tokens are gone. Per user you store only the **Smartcar user ID** (`smartcar_user_id`). | ✅ | [M2M migration guide](https://smartcar.com/docs/getting-started/how-to/m2m/migration-guide) |
| Apps created before 2025-06-01 may need to be upgraded to V3 in the Dashboard. | ✅ | API authentication |
| V3 base URL: `https://vehicle.api.smartcar.com/v3`. V2 (`api.smartcar.com/v2.0`) deprecated **2026-12-01**. | ✅ | migration guide |

**→ Answers D1:** no Smartcar tokens are stored per user. `vehicles.smartcar_user_id` (already in `database-design.md`) is enough; **no `smartcar_connections` table**, and no token encryption dependency. We also store the `connectionId` (see §3) so disconnect can call `DELETE /connections/{id}` — add a nullable `smartcar_connection_id` column to `vehicles` in F1.

## 2. Connect

| Fact | Status | Source |
|---|---|---|
| URL: `https://connect.smartcar.com/oauth/authorize`. | ✅ | [Build the Connect URL](https://smartcar.com/docs/connect/redirect-to-connect) |
| Params: `application_id` (required), `response_type` (required: `code`, or `none` for apps using application tokens), `redirect_uri`, `scope` (space-separated; `required:` prefix locks a permission), `state`, `mode` (`live` \| `simulated`), `make`, `approval_prompt`, `flags` (e.g. `country:US`), `external_id` (recommended; `^[a-zA-Z0-9._-]+$`, ≤100 chars). | ✅ | same |
| Success redirect carries `user_id` (Smartcar user UUID), `state`, and `external_id` if sent. No vehicle IDs. | ✅ | [Handle the response](https://smartcar.com/docs/connect/handle-the-response) |
| Error redirect carries `error` (e.g. `access_denied`), `error_description`, `state`; incompatible vehicle adds `vin`, `make`, `model`, `year`. | ✅ | same + [connect errors](https://smartcar.com/docs/errors/connect-errors/access-denied) |
| Success redirect param is **`user_id`** (with `state` and `external_id`). The migration guide's `userId` is wrong for our app. | 🚗 | spike run 2026-11-13 |
| `application_id` is a separate value from `client_id` (both set in `.env`). | 🚗 | spike run 2026-11-13 |
| `mode` must be `live` or `simulated`. Our old `.env.example` default `SMARTCAR_MODE=test` produced a Connect that completed but authorized **no vehicle** (0 connections). Template fixed to `live`; F2 settings must validate this. | 🚗 | spike run 2026-11-13 |
| Permission names `read_battery`, `read_charge`, `read_odometer`, `read_vehicle_info` are accepted and cover SoC, range, odometer, charging status and make/model/year. | 🚗 | spike run 2026-11-13 |

**→ D3 confirmed:** the callback only learns the vehicles *after* the redirect, by calling the Connections API, so duplicate detection belongs in the callback.

**→ Change to F3:** use `response_type=none` (no code exchange — nothing to exchange with application tokens), send `external_id = <our profile id>` and our signed `state`. The callback verifies `state`, reads `user_id`, then lists the user's connections.

## 3. Connections and vehicles

| Fact | Status | Source |
|---|---|---|
| `GET /v3/connections?filter[userId]={userId}`; vehicle ID at `data[].relationships.vehicle.data.id`. Page-number pagination: `page[number]`, `page[size]` (default 50), `meta.totalCount`. | ✅ | API authentication |
| `GET /v3/vehicles/{vehicleId}` for vehicle details. | ✅ | same |
| `DELETE /v3/connections/{connectionId}` → `204`. Errors return `{ errors: [...] }`. | ✅ | [Remove connection](https://smartcar.com/docs/api-reference/remove-connection) |
| Whether removing a connection also stops webhook deliveries for that vehicle. | 🔬 | not stated — test in spike; our ingestor ignores disconnected vehicles regardless (D12) |

## 4. Signals

`GET /v3/vehicles/{vehicleId}/signals` (all) or `/signals/{signalCode}` (one). Headers `Authorization: Bearer …` and `sc-user-id`. ([list signals](https://smartcar.com/docs/api-reference/list-signals)) ✅

Response item shape ✅:

```json
{
  "id": "…", "type": "signal",
  "attributes": {
    "code": "charge-voltage", "name": "Voltage", "group": "Charge",
    "status": { "value": "SUCCESS | ERROR | UNAVAILABLE", "error": {} },
    "body": { "unit": "volts", "value": 85 }
  },
  "meta": { "retrievedAt": "…", "oemUpdatedAt": "…", "ingestedAt": "…" }
}
```

`included.vehicle.attributes` carries `make`, `model`, `year`, `powertrainType`, `mode` — so make/model/year come with the signals call.

### Signal mapping for ChargeWaze

| ChargeWaze code | Smartcar signal | Body | Unit | Source |
|---|---|---|---|---|
| `soc` | `tractionbattery-stateofcharge` | `value` | percent (0–100) | ✅ [TractionBattery](https://smartcar.com/docs/api-reference/signals/tractionbattery) |
| `range` | `tractionbattery-range` | `value` (+ `type`, `additionalValues`) | `unit` field (km in example) | ✅ same |
| `odometer` | `odometer-traveleddistance` | `value` | `unit` field (km in example) | ✅ [Odometer](https://smartcar.com/docs/api-reference/signals/odometer) |
| `charging_status` | `charge-ischarging` | `value` | boolean | ✅ [Charge](https://smartcar.com/docs/api-reference/signals/charge) |
| `charging_status_detail` | `charge-detailedchargingstatus` | `value` | string | ✅ same |
| `charging_power` | `charge-wattage` | `value` | **watts** (we expose `charging_power_kw`, so ÷1000 at the API layer; store raw W) | ✅ same |
| `charge_limit` | `charge-chargelimits` | `activeLimit` | percent | ✅ same |
| `cable_connected` | `charge-ischargingcableconnected` | `value` | boolean | ✅ same |
| — (spec input) | `tractionbattery-nominalcapacity` | `capacity`, `availableCapacities[]`, `source` | kWh | ✅ TractionBattery. **Nominal, not usable** capacity — informs D8 default, user can edit. |

- Each signal has its own `status`; `ERROR`/`UNAVAILABLE` signals must be stored as missing, never as a value (PRD §7). 🚗 The bZ returns `SUCCESS` or `ERROR` (no `UNAVAILABLE` seen); see §7.
- `supported_signals` on `vehicles` = codes with status `SUCCESS` on a read, **excluding** `ERROR` with `VEHICLE_NOT_CAPABLE` / `UNKNOWN`. Treat `NOT_CHARGING`-type errors as supported-but-currently-empty. Because the first read can be empty (§7), recompute `supported_signals` when the first webhook/read with data arrives.
- Units are per-reading (`body.unit`); store raw and convert in the API layer. 🚗 The bZ reports `km` and `percent`.

## 5. Webhooks

| Fact | Status | Source |
|---|---|---|
| Event types: `VERIFY` (URL check), `VEHICLE_STATE` (monitored signal changed), `VEHICLE_ERROR` (signal retrieval failed/recovered). | ✅ | [Event types](https://smartcar.com/docs/api-reference/webhooks/event-types) |
| Top level: `eventId`, `eventType`, `data`, `meta`. `data.user.id`, `data.vehicle.id`, `data.signals[]` (each `code`, `name`, `group`, `body`, `meta`), `data.triggers[]`. | ✅ | same |
| Timestamps: `meta.deliveredAt`; per signal `meta.oemUpdatedAt` and `meta.retrievedAt` (ms). `meta.sequence` on `VEHICLE_STATE`. Also `meta.deliveryId`, `webhookId`, `version` (`4.0`), `mode`. | ✅ | same |
| `deliveredAt` is a ms integer in `VEHICLE_STATE` examples but ISO-8601 in the `VERIFY` example. | 🔬 | parse both |
| Signature: header **`SC-Signature`** = hex HMAC-SHA256 of the **raw body**, keyed with the **Application Management Token**. Compare in constant time; 401 on mismatch. | ✅ | [Payload verification](https://smartcar.com/docs/integrations/webhooks/payload-verification) |
| `VERIFY` handshake: respond 200, `application/json`, `{"challenge": hex(HMAC-SHA256(management_token, data.challenge))}`; otherwise the webhook isn't activated. **Check `SC-Signature` first, VERIFY included** — answering an unsigned challenge turns the endpoint into an HMAC oracle that can forge signatures for any body. | ✅ | Event types |
| Non-2xx deliveries retried with exponential backoff for **up to 3 days**. Delivery is **at-least-once** and may be **out of order** — dedupe on `eventId`, order by timestamps or `sequence`. | ✅ | same |
| How a vehicle gets subscribed under V3 (per-vehicle subscribe call vs automatic for the webhook's configured vehicles). The subscribe page exists ("Subscribe a vehicle to a webhook", needs `webhook_id`, returns `vehicleId`, `webhookId`) but its method/URL/token didn't render. | 🔬 | [Subscribe](https://smartcar.com/docs/api-reference/webhooks/subscribe-webhook) — check in the Dashboard + spike |
| Whether a per-vehicle unsubscribe exists in V3. | 🔬 | listed in docs index; contents not confirmed |

**Consequences for the plan**

- **Secret naming:** the HMAC key is the Application Management Token. Rename `SMARTCAR_WEBHOOK_SECRET` → `SMARTCAR_MANAGEMENT_TOKEN` in `.env.example` (F2). Keep `SMARTCAR_WEBHOOK_ID`.
- **D5 refined:** a signal's `recorded_at` = its `meta.oemUpdatedAt` (fallback `retrievedAt`); `received_at` = our clock. Out-of-order rule applies per signal using `recorded_at`.
- **Dedup key:** `source_event_id = eventId` with `signal_code` (matches the `database-design.md` unique index).
- **F5 adds:** `VERIFY` handshake; `VEHICLE_ERROR` stored as an error marker (no `latest_state` value change) — confirm shape in the spike.
- **Vehicle lookup in webhooks:** `data.vehicle.id` + `data.user.id` together.

## 6. Limits and SDK

- Rate/usage limits: [usage limits](https://smartcar.com/docs/help/api-limits), [rate-limit errors](https://smartcar.com/docs/errors/api-errors/v3/rate-limit-errors). 🔬 record our plan's numbers from the Dashboard.
  - **Monthly cap per vehicle.** Set by the plan and not published. The docs' only example is "500 API calls per vehicle per month". Successful calls to vehicle endpoints count. Whether webhook deliveries count isn't stated.
  - **App-wide bucket.** 120 requests, refilling at 2 per minute.
  - **Throttled calls** return `429`, body `type: "RATE_LIMIT"`, `code: "VEHICLE"` (per-vehicle, `retry-after` header in seconds) or `"SMARTCAR_API"` (app-wide, no header). ChargeWaze maps both to `429 SMARTCAR_RATE_LIMITED`.
  - **Monthly cap reached.** `BILLING`/`VEHICLE_REQUEST_LIMIT` stays `502 SMARTCAR_ERROR`.
  - **Our own spend.** The on-demand refresh (ADR 0010) spends at most `SMARTCAR_REFRESH_MAX_PER_DAY` (8) signals reads per vehicle per day, about 248 per month.
- **D10:** the docs show plain HTTP for every V3 call; no SDK is needed. Stay on `httpx`.

## 7. Real-vehicle results (🚗 fill in after running the spike)

| Item | Result |
|---|---|
| Smartcar app created in V3 mode | ✅ app token works: `POST /oauth2/token` → 200, `token_type=bearer` (lowercase — compare case-insensitively), `expires_in=3600` |
| Connect redirect param name (`user_id` / `userId`) | ✅ `user_id`; also `state`, `external_id` |
| `application_id` == `client_id`? | ✅ No — separate values |
| `GET /v3/connections?filter[userId]=…` | ✅ 200; shape `{data, links{first,last,next,prev,self}, meta{orderBy, orderDirection, pageNumber, pageSize, totalCount}}`. Pagination links embed the user ID → never log them. |
| First run result | ⚠️ 0 connections — Connect ran with invalid `mode=test`. Fixed by `SMARTCAR_MODE=live`. |
| Connection | ✅ 1 connection, `status.name = "ACTIVE"`, `vehicle.mode = "live"`, `attributes.permissions` = the 4 requested. Vehicle: **TOYOTA / "bZ XLE AWD" / 2026 / `powertrainType: BEV`** (make is upper-case; model includes trim). |
| Permissions requested | `read_battery read_charge read_odometer read_vehicle_info` — all granted. |
| Signals right after Connect | ⚠️ `GET /signals` returned `data: []`, `totalCount: 0` when called seconds after Connect. A few minutes later it returned 27 signals. **F3/F6 must not expect data immediately after connecting** — show "Awaiting first update". |
| Signals with status `SUCCESS` on the bZ | `tractionbattery-stateofcharge` (61, `percent`), `tractionbattery-range` (230.14, `km`, `type: DEFAULT`, `additionalValues: []`), `odometer-traveleddistance` (24650.32, `km`), `charge-ischarging` (false), `charge-detailedchargingstatus` (`NOT_CHARGING`), `charge-ischargingcableconnected` (false), `charge-ischargingportflapopen` (false). |
| Signals listed but `ERROR` | 20 of 27. Error codes: `VEHICLE_NOT_CAPABLE` (e.g. `closure-reartrunk`), `UNKNOWN` (doors, windows, lock, HVAC, tires, speed, VIN, ICE signals, `location-preciselocation`), `NOT_CHARGING` (`charge-timetocomplete` while parked). Error object: `{code, title, type, detail, resolution{type}}`. |
| Not returned at all | `charge-wattage` (→ **`charging_power` unavailable on the bZ for now**; re-check while DC fast charging), `tractionbattery-nominalcapacity` (→ **D8: capacity must come from the static catalog / user input**), `charge-chargelimits`. |
| Location | `location-preciselocation` is listed but `ERROR` because we didn't request location — matches D7. Redaction strips it regardless. |
| Signal timestamps | Each signal `meta` has `oemUpdatedAt` (when Toyota recorded it — **our `recorded_at`**), `retrievedAt` (Smartcar fetched it), `ingestedAt` (Smartcar stored it). ISO-8601 strings in the REST API. Here `oemUpdatedAt` was ~1 min before `retrievedAt`. |
| Signal resource `id` | Equals the signal code (e.g. `"closure-reartrunk"`), not a UUID. |
| Units | km and percent, exactly as in `body.unit`. Store raw; convert in the API/UI. |
| Latency | Token ~0.5–0.7 s; connections / vehicle / signals ~0.3–0.6 s each. |
| How the vehicle gets subscribed to a webhook | Docs: Dashboard auto-enrolment ("Automatically subscribe all vehicles"), a per-vehicle Subscribe button, or `POST /webhooks/{webhookId}/subscriptions` ([subscribing vehicles](https://smartcar.com/docs/integrations/webhooks/subscribing-vehicles)). Data only flows after `VERIFY` passes. **Not tested yet → done in F5.** |
| `VERIFY` handshake, real `VEHICLE_STATE` capture, event cadence, webhooks after `DELETE /connections` | **Deferred to F5**, which needs a public tunnel and the real endpoint anyway. The spike's `webhook` command already passes `VERIFY` and the signature check against locally signed test payloads. |

### F0 verdict

The PRD's assumptions hold for the core Dashboard signals: **SoC, range, odometer and charging status are available on the 2026 bZ through V3**, in km/percent, with per-signal OEM timestamps. Gaps to design around: no charging power, no battery capacity, and no data for the first minutes after connecting.
