import { Suspense, lazy } from "react";
import type { Kpis, PassesResponse, SectorGeoJson } from "../api";
import KpiTile from "../components/KpiTile";
import PassList from "../components/PassList";
import { useApi } from "../useApi";

// MapLibre is most of the bundle; load it only when the dashboard needs it.
const SectorMap = lazy(() => import("../components/SectorMap"));

export default function Dashboard() {
  const kpis = useApi<Kpis>("/dashboard/kpis");
  const passes = useApi<PassesResponse>("/passes");
  const sector = useApi<SectorGeoJson>("/sector/geojson");

  const error = kpis.error ?? passes.error ?? sector.error;
  const k = kpis.data;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="muted">
            {k ? (
              <>
                Replaying <strong>{k.as_of}</strong> from simulated data
              </>
            ) : (
              "Sector overview"
            )}
          </p>
        </div>
      </header>

      {error && (
        <div className="notice notice-bad" role="alert">
          {error}
        </div>
      )}

      <section className="tiles" aria-label="Key figures">
        <KpiTile
          label="Posts"
          value={k?.posts ?? "–"}
          sub={k ? `${k.formations} formations · ${k.depots} depots` : undefined}
        />
        <KpiTile
          label="Upcoming convoys"
          value={k?.convoys ?? "–"}
          sub={k ? `scheduled in the next ${k.convoy_window_days} days` : undefined}
        />
        <KpiTile
          label="Passes at risk"
          value={k ? k.passes_at_risk : "–"}
          sub={k ? `${k.passes_closed} closed` : undefined}
          tone={k && (k.passes_at_risk > 0 || k.passes_closed > 0) ? "warn" : undefined}
        />
        <KpiTile
          label="Readiness"
          value={k ? `${k.readiness_pct}%` : "–"}
          sub={k ? `${k.post_classes_ready} of ${k.post_classes_total} post-class stocks above minimum` : undefined}
          tone={k ? (k.readiness_pct >= 90 ? "ok" : k.readiness_pct >= 75 ? "warn" : "bad") : undefined}
        />
      </section>

      <section className="grid">
        <div className="panel panel-map">
          <h2>Posts, depots and passes</h2>
          {sector.data ? (
            <Suspense fallback={<div className="map map-empty" />}>
              <SectorMap sector={sector.data} />
            </Suspense>
          ) : (
            <div className="map map-empty" />
          )}
          <div className="legend">
            <span><i className="sw sw-ok" /> stock ≥ minimum</span>
            <span><i className="sw sw-warn" /> 70–100%</span>
            <span><i className="sw sw-bad" /> &lt; 70%</span>
            <span><i className="sw sw-depot" /> depot</span>
            <span><i className="sw sw-ring" /> pass (ring = status)</span>
          </div>
          <p className="muted small">
            Offline map: GeoJSON over a blank background, no tiles. Positions are approximate
            place-name centroids, not surveyed or deployment locations.
          </p>
        </div>

        <div className="panel">
          <h2>Pass status</h2>
          {passes.data ? <PassList passes={passes.data.passes} /> : <p className="muted">Loading…</p>}
          <p className="muted small">
            Closure probability from a logistic model trained on the stated closure rule (earlier
            winters); CLOSED means the rule says the pass is closed.
          </p>
        </div>
      </section>
    </>
  );
}
