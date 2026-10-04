import Placeholder from "./Placeholder";

export default function ReportsVerification() {
  return (
    <Placeholder
      title="Reports & Verification"
      day="Day 3-4"
      summary="Signed field reports passing through the verified data gate."
      items={[
        "Field report card and a list with VERIFIED / FLAGGED / REJECTED verdicts",
        "Inject a tampered report (forged, replayed, inflated, deflated) and see why it fails",
        "Detection rate per attack type and the false-alarm rate on clean reports",
      ]}
    />
  );
}
