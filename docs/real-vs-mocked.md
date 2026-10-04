# Real vs mocked

What the prototype actually does, and what it only pretends to. Update this file in the same pull
request as any change that moves something from one column to the other.

| Real (runs, measured) | Mocked / assumed |
|---|---|
| Ed25519 signing, rule checks, anomaly detection, attack injection (`gate/`) | Consumption: synthetic. Only the rations rate (2.5 kg/soldier/day) is sourced; every other rate, factor and surge parameter is an assumption in `config/consumption.yaml` |
| Quantile forecasting, federated averaging, baselines (`forecast/`) | Closure labels: rule-based (`closure/labels.py`); the risk model learns that rule, not observed closures |
| Closure-risk model, MILP planner with reasons (`closure/risk.py`, `planner/`) | Signing keys: derived from the seed by the simulator, not held on devices |
| Hash-chained audit log, two JWT roles | Roles and password: stub (`api/auth.py`); offline sync, mTLS, Keycloak, full RBAC: design only |
| Experiment results in `eval/results.md`, all script-generated | Costs, payloads, mule capacity, sortie budget: assumptions in `config/constraints.yaml` |
| Weather and terrain: **conditional**, see below | Federated learning is a single-process simulation of three clients (hand-written FedAvg, not Flower) |

## Weather: real only when Open-Meteo was reachable

`make data` fetches daily weather per post and pass from Open-Meteo's archive API. If it cannot, it
falls back to a deterministic **synthetic climatology** and says so:

* every weather row carries a `source` column (`open-meteo` or `synthetic-climatology`);
* the build logs a warning, `/health` reports `weather_source`, and the UI sidebar shows it;
* a build never mixes the two sources.

**Status of the committed prototype:** the development sandbox could not reach Open-Meteo (its
egress proxy answered 403), so every number produced there uses the synthetic climatology. The
live-fetch code is tested only against a mocked Open-Meteo response. Before the weather can be
called real in the pitch: run `make data` on a machine with internet, confirm `/health` shows
`weather_source: open-meteo`, and re-pick `scenario.as_of` in `config/constraints.yaml`.

## Other things to know

* **Locations.** Post, depot and pass coordinates are approximate place-name centroids (about
  +/-5-10 km), not surveyed positions and not real deployment sites. Troop numbers are invented.
  `via_pass` names the one pass that gates a post's main lane; real lanes have alternates.
* **Pass closure.** Under the stated rule (15 cm snow in 3 days after 1 Nov closes a pass; it
  reopens when the 14-day mean exceeds 0 C), a pass that closes stays closed for the rest of the
  1 Oct - 31 Mar window, because pass-altitude temperatures stay below 0 C until late spring.
  This is what makes pre-positioning stock worthwhile in the model; it is stricter than reality,
  where passes are cleared and reopen.
* **Historical operation.** The stock history behind the reports uses a naive fixed-schedule
  restock (trucks while the pass is open, helicopter after). Because of the closure behaviour above
  helicopters carry about half of all deliveries (seed 42). It is not the Day 9 baseline and its
  mode mix should not be quoted as realistic.
* **Stock-outs.** 0.24% of post-class-days end at zero (seed 42; 352 of the 368 are ammunition)
  because surge events outrun a thin buffer. Clipping censors the demand signal on those days.
* **Replay.** The app treats `scenario.as_of` as "today" and ignores later rows. The date sits in
  the held-out winter (2025-26), so forecasts can be scored against simulated actuals.
* **Seed.** Everything is seeded (`SEED=42`); `make data-check` proves two builds are identical.

* **Weather forecast.** Forecasts for days 1-16 use the observed temperature as if it were a perfect
  16-day weather forecast; days 17-30 use climatology. Real forecasts would do somewhat worse.
* **Planning experiment.** The 100-winter simulation applies the planner's need rule and mode order
  weekly; it does not solve the MILP each week. The baseline is fixed-scale and trucks-only by
  design, so it shows what forecasting plus closure awareness adds, not how a real formation plans.
* **Federated gain is small** on this data because every formation's consumption comes from the same
  formula; real formations would differ more.
