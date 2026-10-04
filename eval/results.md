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
