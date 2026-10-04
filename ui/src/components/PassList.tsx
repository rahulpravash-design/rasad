import type { PassRow, PassState } from "../api";

const LABEL: Record<PassState, string> = { OPEN: "Open", AT_RISK: "At risk", CLOSED: "Closed" };

function detail(p: PassRow): string {
  if (p.status === "CLOSED") return `closed ${p.days ?? 0} d`;
  if (p.snow_3d_cm !== null) return `${p.snow_3d_cm.toFixed(1)} cm snow / 3 d`;
  return "";
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
