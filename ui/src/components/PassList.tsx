import type { PassRow, PassState } from "../api";

const LABEL: Record<PassState, string> = { OPEN: "Open", AT_RISK: "At risk", CLOSED: "Closed" };

function detail(p: PassRow): string {
  if (p.status === "CLOSED") return `closed ${p.days ?? 0} d`;
  const risk = p.p_close !== null ? `P(close in 14 d) ${p.p_close.toFixed(2)}` : "";
  return p.days ? `${risk} · likely within ${p.days} d` : risk;
}

export default function PassList({ passes }: { passes: PassRow[] }) {
  return (
    <ul className="pass-list">
      {passes.map((p) => (
        <li key={p.pass}>
          <div>
            <div className="pass-name">{p.name}</div>
            <div className="pass-meta">
              {p.altitude_m.toLocaleString()} m · {detail(p)}
            </div>
          </div>
          <span className={`chip chip-${p.status.toLowerCase()}`}>{LABEL[p.status]}</span>
        </li>
      ))}
    </ul>
  );
}
