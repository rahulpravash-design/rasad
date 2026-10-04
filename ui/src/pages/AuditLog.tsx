import { useState } from "react";
import { ApiError, type AuditEntry } from "../api";
import { useApi } from "../useApi";

interface VerifyResult {
  valid: boolean;
  broken_at: number | null;
  entries: number;
}

export default function AuditLog() {
  const [refresh, setRefresh] = useState(0);
  const log = useApi<AuditEntry[]>("/audit", refresh);
  const [check, setCheck] = useState<VerifyResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function verify() {
    setErr(null);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL ?? "/api"}/audit/verify`);
      if (!res.ok) throw new ApiError(res.status, res.statusText);
      setCheck(await res.json());
      setRefresh((n) => n + 1);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Cannot reach the API.");
    }
  }

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Audit Log</h1>
          <p className="muted">
            Every verdict, plan and approval, hash-chained: each entry's hash covers the one before
            it, so any edit breaks the chain from that point on.
          </p>
        </div>
        <button className="btn" onClick={verify}>Verify chain</button>
      </header>
      {err && <div className="notice notice-bad">{err}</div>}
      {check && (
        <div className={`banner ${check.valid ? "banner-ok" : "banner-rejected"}`} role="status">
          <div className="banner-title">
            {check.valid ? `✓ Chain intact: ${check.entries} entries verified` : `✗ Chain broken at entry ${check.broken_at}`}
          </div>
        </div>
      )}
      <div className="panel mt">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Time (UTC)</th>
                <th>Event</th>
                <th>Actor</th>
                <th>Details</th>
                <th>Hash</th>
              </tr>
            </thead>
            <tbody>
              {(log.data ?? []).map((a) => (
                <tr key={a.seq}>
                  <td className="num">{a.seq}</td>
                  <td>{a.ts.replace("T", " ")}</td>
                  <td>{a.event.replace("_", " ")}</td>
                  <td>{a.actor ?? "system"}</td>
                  <td className="mono ellipsis details">{a.payload}</td>
                  <td className="mono" title={`prev ${a.prev_hash}`}>{a.hash.slice(0, 12)}…</td>
                </tr>
              ))}
              {log.data?.length === 0 && (
                <tr><td colSpan={6} className="muted empty">No entries yet. Generate or approve a plan, or inject a report.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
