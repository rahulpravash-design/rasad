import { useState } from "react";
import { ApiError, postJson, type PassesResponse, type Plan, type PlanItem } from "../api";
import { getSession, setSession } from "../auth";
import KpiTile from "../components/KpiTile";
import PassList from "../components/PassList";
import { useApi } from "../useApi";

const MODE: Record<string, string> = { truck: "Truck", mule: "Mule", heli: "Helicopter" };
const inr = (v: number) => `₹${(v / 100000).toFixed(1)} L`;

function convoys(items: PlanItem[]) {
  const groups = new Map<string, { post: string; mode: string; tonnes: number; cost: number; date: string; classes: string[]; status: string }>();
  for (const i of items) {
    const key = `${i.post_id}|${i.mode}`;
    const g = groups.get(key) ?? { post: i.post_id, mode: i.mode, tonnes: 0, cost: 0, date: i.depart_date, classes: [], status: i.status };
    g.tonnes += i.qty_t;
    g.cost += i.cost;
    g.classes.push(i.class);
    if (i.status === "SHORTFALL") g.status = "SHORTFALL";
    groups.set(key, g);
  }
  return [...groups.values()].sort((a, b) => b.tonnes - a.tonnes);
}

export default function RoutePlanning() {
  const passes = useApi<PassesResponse>("/passes");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [session, setS] = useState(getSession());
  const [user, setUser] = useState("lo");
  const [password, setPassword] = useState("");
  const [selected, setSelected] = useState<PlanItem | null>(null);

  async function generate() {
    setBusy(true);
    setErr(null);
    try {
      const p = await postJson<Plan>("/plan", { horizon_days: 30 }, session?.token);
      setPlan(p);
      setSelected(p.items[0] ?? null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Cannot reach the API.");
    } finally {
      setBusy(false);
    }
  }

  async function login() {
    setErr(null);
    try {
      const t = await postJson<{ access_token: string; role: string }>("/auth/token", { username: user, password });
      const s = { token: t.access_token, role: t.role, user };
      setSession(s);
      setS(s);
      setPassword("");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Cannot reach the API.");
    }
  }

  async function approve() {
    if (!plan) return;
    setErr(null);
    try {
      setPlan(await postJson<Plan>(`/plan/${plan.id}/approve`, undefined, session?.token));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Cannot reach the API.");
    }
  }

  const groups = plan ? convoys(plan.items) : [];
  const short = plan?.items.filter((i) => i.status === "SHORTFALL") ?? [];

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Route Planning</h1>
          <p className="muted">
            Closure-aware plan for the next 30 days: trucks while the passes are open, mules and
            helicopters after. Edit config/constraints.yaml (e.g. helicopter payload) and re-plan.
          </p>
        </div>
        <button className="btn" onClick={generate} disabled={busy}>
          {busy ? "Solving…" : plan ? "Re-plan" : "Generate plan"}
        </button>
      </header>

      {err && <div className="notice notice-bad" role="alert">{err}</div>}

      {plan && (
        <section className="tiles">
          <KpiTile label="Plan cost" value={inr(plan.cost)} sub={`plan #${plan.id} · ${plan.status}`} />
          <KpiTile label="Truck" value={`${(plan.tonnes_by_mode.truck ?? 0).toFixed(1)} t`} sub="while passes are open" />
          <KpiTile label="Mule / Heli" value={`${(plan.tonnes_by_mode.mule ?? 0).toFixed(1)} / ${(plan.tonnes_by_mode.heli ?? 0).toFixed(1)} t`} sub={plan.sorties_used !== undefined ? `${plan.sorties_used} of ${plan.sortie_budget} sorties` : undefined} />
          <KpiTile label="Shortfall" value={short.length ? `${short.reduce((a, b) => a + b.qty_t, 0).toFixed(2)} t` : "none"} tone={short.length ? "bad" : "ok"} />
        </section>
      )}

      <section className="grid reports-grid">
        <div className="panel">
          <h2>Upcoming convoys</h2>
          {!plan && <p className="muted">Generate a plan to see convoys.</p>}
          {plan && (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Post</th>
                    <th>Mode</th>
                    <th>Depart</th>
                    <th className="num">Tonnes</th>
                    <th className="num">Cost</th>
                    <th>Classes</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((g) => (
                    <tr key={`${g.post}${g.mode}`} onClick={() => setSelected(plan.items.find((i) => i.post_id === g.post && i.mode === g.mode) ?? null)} className={selected?.post_id === g.post && selected?.mode === g.mode ? "selected" : ""}>
                      <td>{g.post}</td>
                      <td>{g.status === "SHORTFALL" ? <span className="chip verdict-rejected">Shortfall</span> : MODE[g.mode]}</td>
                      <td>{g.date}</td>
                      <td className="num">{g.tonnes.toFixed(2)}</td>
                      <td className="num">{inr(g.cost)}</td>
                      <td className="muted small">{g.classes.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="side">
          <div className="panel">
            <h2>Reason for recommendation</h2>
            {selected ? (
              <>
                <div className="report-title">{selected.post_id} · {MODE[selected.mode]}</div>
                <p className="small muted">{selected.class} · {selected.qty_t.toFixed(2)} t · {selected.time_days} days · risk {selected.risk.toFixed(2)}</p>
                <p className="reason">{selected.reason}</p>
              </>
            ) : (
              <p className="muted">Select a convoy.</p>
            )}
          </div>

          <div className="panel">
            <h2>Approval</h2>
            {session ? (
              <p className="small">
                Signed in as <strong>{session.user}</strong> ({session.role.replace("_", " ")}){" "}
                <button className="btn link" onClick={() => { setSession(null); setS(null); }}>sign out</button>
              </p>
            ) : (
              <div className="login">
                <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="Role">
                  <option value="lo">Logistics Officer</option>
                  <option value="staff">Staff Officer</option>
                </select>
                <input type="password" placeholder="password (demo)" value={password} onChange={(e) => setPassword(e.target.value)} aria-label="Password" />
                <button className="btn" onClick={login}>Sign in</button>
              </div>
            )}
            <button className="btn btn-ok" onClick={approve} disabled={!plan || plan.status !== "DRAFT"}>
              {plan?.status === "APPROVED" ? "✓ Approved" : "Approve plan"}
            </button>
            <p className="muted small">Only a Logistics Officer can approve. Approval is written to the audit log.</p>
          </div>

          <div className="panel">
            <h2>Pass status</h2>
            {passes.data && <PassList passes={passes.data.passes} />}
          </div>
        </div>
      </section>
    </>
  );
}
