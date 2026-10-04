# RASAD: every report verified, every post stocked before the pass closes

SIH 2026 · PS SIH26251 · Indian Army predictive logistics and forward supply chain

> **Status: Day 1-2 of the 10-day plan.** The data layer, API skeleton and dashboard exist. The
> verified data gate, forecasting, federated learning, planner and audit log do not yet. Nothing
> here has been measured on real Army data, and consumption is synthetic.

## What it is

An AI decision layer for resupplying high-altitude posts. Field reports are verified before they
are trusted, demand is forecast per post and supply class, and movement is planned around passes
that will close. It runs offline on one laptop. *(Architecture diagram: to be added at
`docs/architecture.png`.)*

## Three pillars

| Pillar | What it does | Built? |
|---|---|---|
| Verified data gate | Ed25519-signed reports, rule checks, anomaly detection | Day 3-4 |
| Federated forecasting | Per-formation quantile models averaged without sharing raw data | Day 5-6 |
| Closure-aware planner | Truck / mule / helicopter plan with a stated reason for each choice | Day 7-8 |

## Quick start

```bash
make setup     # Python 3.11 venv + pinned deps, npm ci
make data      # weather + synthetic data -> SQLite (about 15 s)
make dev       # API on :8000, UI on :5173
```

`make run` starts the same stack in Docker (untested so far). `make help` lists everything.

`make data` tries Open-Meteo for real weather and **falls back to a synthetic climatology if it
cannot**, flagged in the log, in `/health` and in the UI. See `docs/real-vs-mocked.md`.

## What works now

* 42 posts, 3 formations, 5 depots, 4 passes in `config/`, with consumption model and live-editable
  constraints
* Deterministic data pipeline: weather, five winters of synthetic daily consumption, a stock
  simulation and about 153k balance-checked daily reports (`make data-check` proves repeatability)
* Pass closure labels from a stated rule (`closure/labels.py`)
* API: `/health`, `/dashboard/kpis`, `/passes`, `/sector/geojson`, replayed as of a date in the
  held-out winter
* Dashboard: KPI tiles, offline map of posts, depots and passes, pass status; four placeholder pages
* A pytest suite (config, weather, closure rule, generator, stock balance, API, determinism), plus
  a CI workflow (ruff, pytest, determinism check, UI build) that has not run on GitHub yet

## Demo flow

[docs/demo-script.md](docs/demo-script.md), with what works today marked.

## Results

None yet. `eval/results.md` is filled from measured runs on Day 4-9, and only measured numbers go
into it.

## Real vs mocked

[docs/real-vs-mocked.md](docs/real-vs-mocked.md). In short: consumption is synthetic (only the
2.5 kg/day rations rate is sourced), closure labels are rule-based, locations are approximate
place-name centroids rather than deployment sites, and weather is real only when Open-Meteo was
reachable at build time.

## Architecture (5 tiers)

Field report → signed-report gate → federated forecasting → closure-aware planner → officer
dashboard with hash-chained audit log. Today: SQLite data layer, FastAPI, React + MapLibre.
Contracts: [docs/data-contracts.md](docs/data-contracts.md).

## Limitations

* Consumption is synthetic; no real Army data was used.
* Closure labels follow a stated rule, not observed closures. Under that rule a closed pass stays
  closed until the end of March.
* Weather in the committed prototype build is synthetic (Open-Meteo was unreachable when it was
  built); re-run `make data` with internet to change that.
* Offline map is GeoJSON over a blank background; terrain tiles are not included.
* Docker files are untested.

## Team

CTRL ALT ELITE

## License

Apache-2.0
