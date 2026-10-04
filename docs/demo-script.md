# Demo script (2 minutes, Wi-Fi off the whole time)

Before: `make setup && make data && make train && make eval`, then `make dev` (UI on :5173).
Sign-in for step 5: user `lo`, password from `DEMO_PASSWORD` (default `demo`).

| # | Screen | Do | Say |
|---|---|---|---|
| 1 | **Dashboard** | Point at the map, KPI tiles, pass status | "42 posts, three formations, four passes. Consumption is synthetic; weather is synthetic in this build" (say "real weather" only if `/health` shows `open-meteo`) |
| 2 | **Reports & Verification** | Select a verified report, "Verify again". Inject *Forged signature*, then *Inflated consumption* | "Signature, arithmetic, replay, continuity, then an anomaly model. Forged and replayed: always caught. A validly signed insider inflating ammunition is the hard case, and we show that" |
| 3 | **Forecasting** | HANLE-03, fuel; point at the band and the federated panel | "P90 band against what actually happened. Formations share model weights, never reports" |
| 4 | **Route Planning** | Generate plan; read one reason aloud. Edit `modes.heli.payload_t` in `config/constraints.yaml` (1.0 -> 0.4), Re-plan | "Trucks while the passes are open; helicopters after. Lower payload: more sorties, higher cost, about 5 seconds" |
| 5 | **Approve** | Sign in as Logistics Officer, Approve plan | "Only the Logistics Officer can approve; a Staff Officer gets 403" |
| 6 | **Audit Log** | Verify chain | "Every verdict, plan and approval is hash-chained; any edit breaks it" |

Numbers for the pitch come only from `eval/results.md`.
