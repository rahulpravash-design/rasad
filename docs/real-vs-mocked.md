# Real vs mocked

What the prototype actually does, and what it only pretends to. Update this file in the same pull
request as any change that moves something from one column to the other.

| Real | Mocked / assumed |
|---|---|
| Signing, gate, forecasting, optimiser *(built from Day 3 on; none exist yet)* | Consumption: synthetic. Only the rations rate (2.5 kg/soldier/day) is sourced; every other rate, factor and surge parameter is an assumption in `config/consumption.yaml` |
| Measured experiment results *(none yet; `eval/results.md` is empty until Day 9)* | Closure labels: rule-based (`closure/labels.py`), not observed closures |
| Terrain, weather, road geometry: **conditional**, see below | Offline sync, mTLS, full RBAC: design only |

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
