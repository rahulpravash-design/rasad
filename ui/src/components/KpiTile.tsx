import type { ReactNode } from "react";

export default function KpiTile(props: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className={`tile${props.tone ? ` tone-${props.tone}` : ""}`}>
      <div className="tile-label">{props.label}</div>
      <div className="tile-value">{props.value}</div>
      {props.sub && <div className="tile-sub">{props.sub}</div>}
    </div>
  );
}
