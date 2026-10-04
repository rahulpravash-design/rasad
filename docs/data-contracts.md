# Data and API contracts

Fixed on Day 1 so that backend, ML and UI can work in parallel. Where the code differs from the
original plan, the difference is listed under **Deviations**.

## `config/sector.yaml`

```yaml
timeline: {first_winter: 2021, last_winter: 2025, season_start: "10-01", season_end: "03-31", holdout_winter: 2025}
formations: {F1: {name, history_start}, F2: ..., F3: {history_start: "2024-10-01"}}
depots: [{id, name, lat, lon, altitude_m}]          # 5
passes: [{id, name, lat, lon, altitude_m}]          # 4: ZOJI_LA KHARDUNG_LA CHANG_LA TANGLANG_LA
posts:  [{id, formation, lat, lon, altitude_m, troops, depot, via_pass, access: [truck|mule|heli]}]   # 42
```

A winter is named by its start year (2021 = 1 Oct 2021 to 31 Mar 2022). Data exists only for these
winter days. F3 has data only from winter 2024-25, and the last winter is held out.

## Field report

```json
{"report_id":"uuid","post_id":"HANLE-03","ts":"2026-10-10T06:20Z","class":"rations",
 "opening":420,"received":0,"consumed":31,"closing":389,"nonce":"hex","sig":"base64"}
```

Balance identity: `closing = opening + received - consumed`. Generated reports are one per post,
class and day, filed around 06:00Z and attributed to the day they are filed. `sig` is null until
signing lands (Day 3). In the database and DataFrames the field is called `supply_class`.

Classes and units: rations (kg), fuel (litre), medical (kg), ammunition (kg), spares (kg).

## SQLite (`api/schema.sql`)

Contract tables: `posts`, `passes`, `reports`, `gate_verdicts`, `forecasts`, `pass_status`, `plans`,
`plan_items`, `audit`. Supporting tables: `depots`, `weather_daily`, `deliveries`, `meta`.
Populated today: everything except `gate_verdicts`, `forecasts`, `plans`, `plan_items`, `audit`.

## HTTP API

Every data endpoint is evaluated as of `scenario.as_of` (or `?as_of=YYYY-MM-DD`) and returns 503
with a "run `make data`" message when there is no database.

| Endpoint | Status | Notes |
|---|---|---|
| `GET /health` | built | works with no database; reports offline mode and data provenance |
| `GET /dashboard/kpis` | built | posts, formations, depots, convoys (next 7 days), passes at risk / closed, readiness % |
| `GET /passes` | built | `{pass, status, p_close, days}`; `p_close` is null and `days` counts days closed until the Day 7 model |
| `GET /sector/geojson` | built (added) | posts, depots, passes for the map |
| `POST /reports`, `POST /reports/inject`, `GET /reports?status=` | Day 3-4 | |
| `GET /forecast/{post_id}`, `GET /forecast/items/{post_id}`, `GET /federated/summary` | Day 5-6 | |
| `POST /plan`, `POST /plan/{id}/approve` | Day 7-8 | approve needs the Logistics Officer role |
| `GET /audit`, `GET /audit/verify` | Day 8 | |

Definitions used by the KPIs:

* **Readiness %:** share of post-class stocks whose days of cover is at least
  `min_stock_days[class]`. Days of cover = closing stock on `as_of` / mean consumption over the
  trailing 28 days.
* **Convoys:** distinct (depot, day, mode) groups among deliveries scheduled in the next 7 days.
* **Pass status (interim):** CLOSED by the closure rule; AT_RISK when 3-day snowfall reaches half
  the closing threshold; otherwise OPEN.

## Deviations from the original plan

* **5 depots, not 3.** The repo plan says 5 depots; its YAML example lists 3. I used five: LEH,
  KARGIL, DRASS, TANGTSE, NYOMA.
* **Passes are objects with coordinates**, not bare ids, so the map and weather fetch can place them.
* **Weather and data cover Oct-Mar only**, one continuous range per winter.
* **Added** `/sector/geojson`, `?as_of=`, a `meta` table, `make dev` and `make data-check`.
* **`as_of`** is a replay date inside the held-out winter, not wall-clock time.
