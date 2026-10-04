import type { Verdict } from "../api";

// Colour is never the only signal: each verdict also has a symbol and a word.
const META: Record<Verdict, { icon: string; label: string }> = {
  VERIFIED: { icon: "✓", label: "Verified" },
  FLAGGED: { icon: "!", label: "Flagged" },
  REJECTED: { icon: "✗", label: "Rejected" },
};

export default function VerdictChip({ verdict }: { verdict: Verdict }) {
  const m = META[verdict];
  return (
    <span className={`chip verdict verdict-${verdict.toLowerCase()}`}>
      <span aria-hidden="true">{m.icon}</span> {m.label}
    </span>
  );
}
