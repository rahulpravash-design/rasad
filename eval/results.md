# Results

Every number below was written by a script in this repository (`make eval`), never typed by hand.
Data is synthetic (see `docs/real-vs-mocked.md`), so these results describe how the methods behave
on simulated reports and consumption, not on real Army data.

<!-- gate:start -->
## Verified data gate (held-out winter 2025-26, seed 42)

The anomaly layer is trained on earlier winters only. 10.0% of the 38,220 held-out reports were attacked (the four types in rotation); each attack is judged against the same state as the genuine report it imitates.

| Attack | Attempts | Rejected | Flagged | Missed | Detection rate (95% CI) | Caught by |
|---|---:|---:|---:|---:|---:|---|
| Forged signature | 956 | 956 | 0 | 0 | 100.0% (99.6%-100.0%) | bad_signature (956) |
| Replay | 956 | 956 | 0 | 0 | 100.0% (99.6%-100.0%) | timestamp_not_after_previous (956), replay (956) |
| Inflated consumption (insider) | 956 | 0 | 816 | 140 | 85.4% (83.0%-87.5%) | anomalous_consumption (816) |
| Deflated stock (insider) | 953 | 0 | 940 | 13 | 98.6% (97.7%-99.2%) | stock_discontinuity (937), anomalous_consumption (10) |

**False alarms on clean reports:** 445 of 38,220 = 1.2% (95% CI 1.1%-1.3%), all FLAGGED, none rejected (reasons: anomalous_consumption 445).

### Where the gate is blind: inflated consumption by a validly signed insider

| Class | Attempts | Detected |
|---|---:|---:|
| ammunition | 207 | 34.3% |
| fuel | 199 | 98.0% |
| medical | 168 | 100.0% |
| rations | 180 | 100.0% |
| spares | 202 | 100.0% |

| Size of the lie | Attempts | Detected |
|---|---:|---:|
| <2x the genuine value | 120 | 77.5% |
| 2-2.5x the genuine value | 427 | 82.4% |
| >=2.5x the genuine value | 409 | 90.7% |

### Anomaly threshold trade-off (anomaly layer alone)

| Training false-alarm target | Held-out false-alarm rate | Inflated detection |
|---:|---:|---:|
| 0.5% | 0.6% | 80.0% |
| 1.0% (in use) | 1.2% | 85.4% |
| 2.0% | 2.4% | 90.4% |
| 5.0% | 5.9% | 96.3% |
<!-- gate:end -->

<!-- forecast:start -->
## Demand forecasting (held-out winter 2025-26)

Trained on earlier winters only. F3 is newly inducted and has one training winter; F1 and F2 have four. WAPE of the P50 forecast (lower is better), days 1-16 / days 17-30 ahead.

| Model | F1 | F2 | F3 |
|---|---:|---:|---:|
| Local only (own formation) | 9.8% / 11.2% | 8.7% / 9.9% | 10.0% / 10.9% |
| Federated (FedAvg) | 9.8% / 11.2% | 8.6% / 9.8% | 9.8% / 10.8% |
| Central (pooled data) | 9.8% / 11.2% | 8.7% / 9.8% | 9.7% / 10.7% |
| Central LightGBM | 9.9% / 11.1% | 8.6% / 9.8% | 9.7% / 10.8% |
| Naive lagged mean | 14.6% / 14.6% | 13.3% / 13.3% | 13.1% / 13.1% |

Pinball loss (lower is better) and P10-P90 coverage (80% is calibrated), days 17-30:

| Model | F1 pinball | F2 pinball | F3 pinball | F1 cov. | F2 cov. | F3 cov. |
|---|---:|---:|---:|---:|---:|---:|
| Local only (own formation) | 3.448 | 2.777 | 2.914 | 72.9% | 79.3% | 74.1% |
| Federated (FedAvg) | 3.447 | 2.768 | 2.869 | 75.0% | 77.9% | 75.6% |
| Central (pooled data) | 3.463 | 2.784 | 2.862 | 74.1% | 76.7% | 75.2% |
| Central LightGBM | 3.427 | 2.801 | 2.901 | 73.7% | 75.9% | 73.6% |
| Naive lagged mean | 4.467 | 3.744 | 3.368 | 76.4% | 78.4% | 80.4% |

**F3 (data-poor), days 17-30:** local 10.9% -> federated 10.8% WAPE; central 10.7%. Federated training moved model weights only; no report left its formation.
<!-- forecast:end -->

<!-- winters:start -->
## Planning: 100 simulated winters (seed 42)

Weather resampled from the four pre-holdout winters and perturbed (temperature shift, snowfall scaling); demand from the same consumption model with fresh seeds. Mean per winter across the 42 posts; the difference is RASAD minus baseline with a 95% CI; the last column is the share of winters where RASAD was lower (better).

| Metric (per winter) | Fixed-scale baseline | RASAD | Difference (95% CI) | RASAD lower |
|---|---:|---:|---:|---:|
| Stock-out post-class-days | 2.4 | 0.3 | -2.1 (-2.4 to -1.7) | 80% |
| Emergency airlift (t) | 1,361.4 | 0.6 | -1,360.8 (-1,413.3 to -1,308.4) | 100% |
| Planned helicopter (t) | 0.0 | 58.1 | 58.1 (53.2 to 63.0) | 0% |
| Truck (t) | 1,324.4 | 2,097.0 | 772.6 (733.8 to 811.5) | 0% |
| Mule (t) | 0.0 | 687.0 | 687.0 (646.3 to 727.8) | 0% |
| Movement cost (INR) | 1,698.1 L | 291.4 L | -1,406.7 L (-1,461.8 L to -1,351.6 L) | 100% |

The simulation applies the planner's need rule and mode order rather than solving the MILP each week; costs use the assumed rates in config/constraints.yaml.
<!-- winters:end -->
