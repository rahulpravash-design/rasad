import { useEffect, useState } from "react";
import {
  ApiError,
  postJson,
  type AttackType,
  type InjectResult,
  type ReportList,
  type ReportsSummary,
  type StoredReport,
  type Verdict,
  type VerdictOut,
} from "../api";
import KpiTile from "../components/KpiTile";
import VerdictChip from "../components/VerdictChip";
import { useApi } from "../useApi";

const ATTACKS: { value: AttackType; label: string; hint: string }[] = [
  { value: "forged_signature", label: "Forged signature", hint: "consistent report signed with the wrong key" },
  { value: "replay", label: "Replay", hint: "an earlier genuine report sent again" },
  { value: "inflated_consumption", label: "Inflated consumption (insider)", hint: "valid key, overstated use" },
  { value: "deflated_stock", label: "Deflated stock (insider)", hint: "valid key, understated stock" },
];

const FILTERS: { value: Verdict | "ALL"; label: string }[] = [
  { value: "ALL", label: "All" },
  { value: "VERIFIED", label: "Verified" },
  { value: "FLAGGED", label: "Flagged" },
  { value: "REJECTED", label: "Rejected" },
];

const time = (ts: string) => ts.replace("T", " ").replace("Z", "Z");

function message(err: unknown): string {
  return err instanceof ApiError ? err.message : "Cannot reach the API.";
}

export default function ReportsVerification() {
  const [filter, setFilter] = useState<Verdict | "ALL">("ALL");
  const [injectedOnly, setInjectedOnly] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState<StoredReport | null>(null);
  const [attack, setAttack] = useState<AttackType>("forged_signature");
  const [busy, setBusy] = useState(false);
  const [injected, setInjected] = useState<InjectResult | null>(null);
  const [recheck, setRecheck] = useState<VerdictOut | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const query = `/reports?limit=60${filter === "ALL" ? "" : `&status=${filter}`}${injectedOnly ? "&origin=injected" : ""}`;
  const list = useApi<ReportList>(query, refresh);
  const summary = useApi<ReportsSummary>("/reports/summary", refresh);

  const items = list.data?.items ?? [];
  const current = selected ?? items[0] ?? null;

  // A selection from a previous filter may no longer be in view; keep showing it until replaced.
  useEffect(() => setRecheck(null), [current?.report_id]);

  async function inject() {
    setBusy(true);
    setActionError(null);
    try {
      const result = await postJson<InjectResult>("/reports/inject", { attack_type: attack });
      setInjected(result);
      setSelected(result.report);
      setRefresh((n) => n + 1);
    } catch (err) {
      setActionError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function reverify(id: string) {
    setActionError(null);
    try {
      setRecheck(await postJson<VerdictOut>(`/reports/${encodeURIComponent(id)}/verify`));
    } catch (err) {
      setActionError(message(err));
    }
  }

  const s = summary.data;
  const total = s ? s.field.VERIFIED + s.field.FLAGGED + s.field.REJECTED : null;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Reports &amp; Verification</h1>
          <p className="muted">
            Every report is checked for a valid signature, honest arithmetic, replay and stock
            continuity before anything downstream trusts it.
          </p>
        </div>
      </header>

      {(list.error ?? summary.error) && (
        <div className="notice notice-bad" role="alert">
          {list.error ?? summary.error}
        </div>
      )}

      <section className="tiles" aria-label="Verdict counts">
        <KpiTile label="Field reports" value={total?.toLocaleString() ?? "–"} sub={s ? `up to ${s.as_of}` : undefined} />
        <KpiTile label="Verified" value={s?.field.VERIFIED.toLocaleString() ?? "–"} tone="ok" />
        <KpiTile
          label="Flagged"
          value={s ? (s.field.FLAGGED + s.injected.FLAGGED).toLocaleString() : "–"}
          sub={s ? `need a person to look${s.injected.FLAGGED ? ` · ${s.injected.FLAGGED} injected` : ""}` : undefined}
          tone={s && s.field.FLAGGED + s.injected.FLAGGED > 0 ? "warn" : undefined}
        />
        <KpiTile
          label="Rejected"
          value={s ? (s.field.REJECTED + s.injected.REJECTED).toLocaleString() : "–"}
          sub={s ? `cannot be true${s.injected.REJECTED ? ` · ${s.injected.REJECTED} injected` : ""}` : undefined}
          tone={s && s.field.REJECTED + s.injected.REJECTED > 0 ? "bad" : undefined}
        />
      </section>

      <section className="grid reports-grid">
        <div className="panel">
          <div className="toolbar">
            <div className="tabs" role="tablist" aria-label="Filter by verdict">
              {FILTERS.map((f) => (
                <button
                  key={f.value}
                  role="tab"
                  aria-selected={filter === f.value}
                  className={`tab${filter === f.value ? " active" : ""}`}
                  onClick={() => setFilter(f.value)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <label className="check">
              <input type="checkbox" checked={injectedOnly} onChange={(e) => setInjectedOnly(e.target.checked)} />
              Injected only
            </label>
          </div>

          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Time (UTC)</th>
                  <th>Post</th>
                  <th>Class</th>
                  <th className="num">Consumed</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <tr
                    key={r.report_id}
                    className={current?.report_id === r.report_id ? "selected" : ""}
                    onClick={() => setSelected(r)}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setSelected(r)}
                  >
                    <td>{time(r.ts)}</td>
                    <td>
                      {r.post_id}
                      {r.origin === "injected" && <span className="tag">injected</span>}
                    </td>
                    <td>{r.class}</td>
                    <td className="num">{r.consumed.toLocaleString()}</td>
                    <td>
                      <VerdictChip verdict={r.verdict} />
                    </td>
                  </tr>
                ))}
                {items.length === 0 && !list.loading && (
                  <tr>
                    <td colSpan={5} className="muted empty">
                      No reports match.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="muted small">
            Showing {items.length} of {list.data?.total.toLocaleString() ?? "–"}, newest first.
          </p>
        </div>

        <div className="side">
          <div className="panel">
            <h2>Field report</h2>
            {current ? (
              <>
                <div className="report-head">
                  <div>
                    <div className="report-title">{current.post_id}</div>
                    <div className="muted small">
                      {current.class} · {time(current.ts)}
                    </div>
                  </div>
                  <VerdictChip verdict={current.verdict} />
                </div>
                <dl className="facts">
                  <dt>Opening</dt><dd>{current.opening.toLocaleString()}</dd>
                  <dt>Received</dt><dd>{current.received.toLocaleString()}</dd>
                  <dt>Consumed</dt><dd>{current.consumed.toLocaleString()}</dd>
                  <dt>Closing</dt><dd>{current.closing.toLocaleString()}</dd>
                  <dt>Nonce</dt><dd className="mono">{current.nonce}</dd>
                  <dt>Signature</dt><dd className="mono ellipsis" title={current.sig ?? ""}>{current.sig ?? "none"}</dd>
                </dl>
                {current.reasons.length > 0 && (
                  <ul className="reasons">
                    {current.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                )}
                <button className="btn" onClick={() => reverify(current.report_id)}>
                  Verify again
                </button>
                {recheck && (
                  <p className="small recheck">
                    Re-checked now: <VerdictChip verdict={recheck.verdict} />
                    {recheck.reasons.length > 0 && <> {recheck.reasons.join("; ")}</>}
                  </p>
                )}
              </>
            ) : (
              <p className="muted">No report selected.</p>
            )}
          </div>

          <div className="panel">
            <h2>Inject a tampered report</h2>
            <label className="field">
              <span>Attack</span>
              <select value={attack} onChange={(e) => setAttack(e.target.value as AttackType)}>
                {ATTACKS.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="muted small">{ATTACKS.find((a) => a.value === attack)?.hint}</p>
            <button className="btn btn-danger" onClick={inject} disabled={busy}>
              {busy ? "Injecting…" : "Inject tampered report"}
            </button>

            {actionError && <div className="notice notice-bad" role="alert">{actionError}</div>}
            {injected && (
              <div className={`banner banner-${injected.detected ? injected.verdict.verdict.toLowerCase() : "missed"}`} role="status">
                <div className="banner-title">
                  {injected.detected ? (
                    <>
                      <span aria-hidden="true">{injected.verdict.verdict === "REJECTED" ? "✗" : "!"}</span>{" "}
                      {injected.verdict.verdict === "REJECTED" ? "Rejected" : "Flagged"}
                    </>
                  ) : (
                    <>Not detected: passed the gate</>
                  )}
                </div>
                <div className="small">{injected.target}</div>
                <ul className="reasons">
                  {injected.verdict.reasons.length ? (
                    injected.verdict.reasons.map((r) => <li key={r}>{r}</li>)
                  ) : (
                    <li>Validly signed and internally consistent; no rule objects.</li>
                  )}
                </ul>
              </div>
            )}
          </div>
        </div>
      </section>
    </>
  );
}
