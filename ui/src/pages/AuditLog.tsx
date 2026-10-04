import Placeholder from "./Placeholder";

export default function AuditLog() {
  return (
    <Placeholder
      title="Audit Log"
      day="Day 8"
      summary="A hash-chained record of every verdict, forecast, plan and approval."
      items={["Entries with prev_hash and hash", "Verify chain: reports the first broken entry, if any"]}
    />
  );
}
