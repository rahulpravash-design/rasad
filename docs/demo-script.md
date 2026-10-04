# Demo script (2 minutes, Wi-Fi off the whole time)

Status column: what works at this commit. Anything not marked **works** is planned, not built.

| # | Step | Say | Status |
|---|---|---|---|
| 1 | **Dashboard**: map, KPI tiles, pass status | "42 posts, real terrain and weather, synthetic consumption." *(Only say "real weather" if `/health` shows `open-meteo`; see `real-vs-mocked.md`.)* | **works**: tiles, map, pass list from SQLite |
| 2 | **Reports & Verification**: a clean report verifies, inject a forged one, red cross with reason | | Day 3-4 |
| 3 | **Forecasting**: federated panel with measured errors, P90 chart, item table | | Day 5-6 |
| 4 | **Route Planning**: generate a plan, read one reason aloud, edit helicopter payload, re-plan | | Day 7-8 |
| 5 | **Approve** a convoy, open **Audit Log**, "Verify chain" | | Day 8 |

Run it: `make setup && make data && make dev`, then open http://localhost:5173.
`make run` starts the same stack in Docker (untested so far).
