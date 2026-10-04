import { useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { FederatedSummary, ForecastItem, ForecastResponse, SectorGeoJson } from "../api";
import { useApi } from "../useApi";

const CLASSES = ["rations", "fuel", "medical", "ammunition", "spares"];
const pct = (v: number) => `${(100 * v).toFixed(1)}%`;

export default function Forecasting() {
  const sector = useApi<SectorGeoJson>("/sector/geojson");
  const posts = (sector.data?.features ?? [])
    .filter((f) => f.properties.kind === "post")
    .map((f) => f.properties.id)
    .sort();
  const [post, setPost] = useState("HANLE-03");
  const [cls, setCls] = useState("fuel");

  const fc = useApi<ForecastResponse>(`/forecast/${post}?class=${cls}`);
  const items = useApi<{ items: ForecastItem[] }>(`/forecast/items/${post}`);
  const fed = useApi<FederatedSummary>("/federated/summary");

  const chart = (fc.data?.days ?? []).map((d) => ({
    date: d.date.slice(5),
    band: [d.p10, d.p90] as [number, number],
    p50: d.p50,
    p90: d.p90,
    actual: d.actual,
  }));
  const error = fc.error ?? items.error;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Forecasting</h1>
          <p className="muted">
            30-day demand from the federated model: P10-P90 band, P50 and P90, against what the
            simulated post actually consumed.
          </p>
        </div>
      </header>

      {error && <div className="notice notice-bad" role="alert">{error}</div>}

      <section className="grid">
        <div className="panel">
          <div className="toolbar">
            <label className="field inline">
              <span>Post</span>
              <select value={post} onChange={(e) => setPost(e.target.value)}>
                {(posts.length ? posts : [post]).map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </label>
            <div className="tabs" role="tablist" aria-label="Supply class">
              {CLASSES.map((c) => (
                <button key={c} role="tab" aria-selected={cls === c} className={`tab${cls === c ? " active" : ""}`} onClick={() => setCls(c)}>
                  {c}
                </button>
              ))}
            </div>
          </div>
          <h2>
            Demand forecast · {post} · {cls} {fc.data ? `(${fc.data.unit}/day, from ${fc.data.as_of})` : ""}
          </h2>
          <div className="chart">
            <ResponsiveContainer width="100%" height={320}>
              <ComposedChart data={chart} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#1f3547" vertical={false} />
                <XAxis dataKey="date" stroke="#8da2b3" fontSize={12} interval={4} />
                <YAxis stroke="#8da2b3" fontSize={12} width={56} />
                <Tooltip
                  contentStyle={{ background: "#14263a", border: "1px solid #1f3547", borderRadius: 8 }}
                  formatter={(v: unknown, name: string) =>
                    Array.isArray(v) ? [`${v[0]} – ${v[1]}`, "P10–P90"] : [String(v), name]
                  }
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Area dataKey="band" name="P10–P90" fill="#5aa9e6" fillOpacity={0.18} stroke="none" isAnimationActive={false} />
                <Line dataKey="p50" name="P50" stroke="#5aa9e6" dot={false} strokeWidth={2} isAnimationActive={false} />
                <Line dataKey="p90" name="P90" stroke="#e0a43a" dot={false} strokeDasharray="5 4" strokeWidth={2} isAnimationActive={false} />
                <Line dataKey="actual" name="Actual" stroke="#e4edf4" dot={{ r: 2 }} strokeWidth={1} isAnimationActive={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="muted small">
            Days 1-16 use the observed temperature as a stand-in for a weather forecast (optimistic);
            days 17-30 use climatology.
          </p>

          <h2 className="mt">Item-wise requirement · next 30 days</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Class</th>
                  <th className="num">Stock</th>
                  <th className="num">Cover (days)</th>
                  <th className="num">P50 30 d</th>
                  <th className="num">P90 30 d</th>
                  <th className="num">Recommended</th>
                </tr>
              </thead>
              <tbody>
                {(items.data?.items ?? []).map((i) => (
                  <tr key={i.class} className={i.class === cls ? "selected" : ""} onClick={() => setCls(i.class)}>
                    <td>{i.class}</td>
                    <td className="num">{i.stock.toLocaleString()} {i.unit}</td>
                    <td className="num">{i.days_of_cover ?? "–"}</td>
                    <td className="num">{i.p50_30d.toLocaleString()}</td>
                    <td className="num">{i.p90_30d.toLocaleString()}</td>
                    <td className="num"><strong>{i.recommended.toLocaleString()}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">Recommended = P90 demand over 30 days + minimum stock days of P50 − stock.</p>
        </div>

        <div className="panel">
          <h2>Federated learning</h2>
          {fed.error && <p className="muted">{fed.error}</p>}
          {fed.data && (
            <>
              <div className="fed-cards">
                {fed.data.formations.map((f) => (
                  <div key={f.formation} className="fed-card">
                    <div className="fed-name">{f.formation}</div>
                    <div className="muted small">{f.training_rows.toLocaleString()} rows stay local</div>
                    <div className="fed-arrow" aria-hidden="true">↓ weights only</div>
                  </div>
                ))}
              </div>
              <div className="fed-global">
                Global model · FedAvg · {fed.data.rounds} rounds · raw report bytes shared:{" "}
                <strong>{fed.data.raw_bytes_shared}</strong>
              </div>
              <table className="table compact">
                <thead>
                  <tr>
                    <th>WAPE</th>
                    <th className="num">Local</th>
                    <th className="num">Federated</th>
                    <th className="num">Central</th>
                  </tr>
                </thead>
                <tbody>
                  {fed.data.formations.map((f) => (
                    <tr key={f.formation}>
                      <td>{f.formation}</td>
                      <td className="num">{pct(f.local_wape)}</td>
                      <td className="num"><strong>{pct(f.federated_wape)}</strong></td>
                      <td className="num">{pct(f.central_wape)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small">
                Measured on the held-out winter {fed.data.holdout_winter}, {fed.data.horizon}. F3 is
                newly inducted (one training winter). Central pools everyone's raw data and is the
                privacy-violating upper bound.
              </p>
            </>
          )}
        </div>
      </section>
    </>
  );
}
