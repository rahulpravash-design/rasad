import { LngLatBounds, Map as MapLibreMap, NavigationControl, Popup } from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import type { SectorGeoJson } from "../api";

// Offline basemap: there are no tiles and no remote fonts. The map is a blank style with a
// graticule and the sector's GeoJSON drawn on top, so it renders with the network off. (To add
// terrain later, put a raster image or MBTiles source under this style.)

const STATUS_COLOR = { OPEN: "#3fb27f", AT_RISK: "#e0a43a", CLOSED: "#e5534b" } as const;

function graticule(): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (let lon = 75; lon <= 80; lon += 1) {
    features.push({
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: [[lon, 32], [lon, 35.5]] },
    });
  }
  for (let lat = 32; lat <= 35; lat += 1) {
    features.push({
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: [[75, lat], [80, lat]] },
    });
  }
  return { type: "FeatureCollection", features };
}

const STYLE: StyleSpecification = {
  version: 8,
  sources: { graticule: { type: "geojson", data: graticule() } },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#0c1822" } },
    {
      id: "graticule",
      type: "line",
      source: "graticule",
      paint: { "line-color": "#1c3040", "line-width": 1 },
    },
  ],
};

export default function SectorMap({ sector }: { sector: SectorGeoJson }) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const map = new MapLibreMap({
      container: container.current,
      style: STYLE,
      center: [77.6, 33.9],
      zoom: 5.2,
      attributionControl: false,
    });
    mapRef.current = map;
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");

    map.on("load", () => {
      map.addSource("sector", { type: "geojson", data: sector as unknown as GeoJSON.FeatureCollection });

      // Posts coloured by their worst stock cover (cover / minimum days over the five classes).
      map.addLayer({
        id: "posts",
        type: "circle",
        source: "sector",
        filter: ["==", ["get", "kind"], "post"],
        paint: {
          "circle-radius": 5,
          "circle-color": [
            "case",
            [">=", ["coalesce", ["get", "cover_ratio"], 9], 1],
            "#3fb27f",
            [">=", ["coalesce", ["get", "cover_ratio"], 9], 0.7],
            "#e0a43a",
            "#e5534b",
          ],
          "circle-stroke-width": 1,
          "circle-stroke-color": "#0c1822",
        },
      });
      map.addLayer({
        id: "depots",
        type: "circle",
        source: "sector",
        filter: ["==", ["get", "kind"], "depot"],
        paint: {
          "circle-radius": 8,
          "circle-color": "#5aa9e6",
          "circle-stroke-width": 2,
          "circle-stroke-color": "#dbeeff",
        },
      });
      map.addLayer({
        id: "passes",
        type: "circle",
        source: "sector",
        filter: ["==", ["get", "kind"], "pass"],
        paint: {
          "circle-radius": 8,
          "circle-color": "#0c1822",
          "circle-stroke-width": 3,
          "circle-stroke-color": [
            "match",
            ["get", "status"],
            "CLOSED",
            STATUS_COLOR.CLOSED,
            "AT_RISK",
            STATUS_COLOR.AT_RISK,
            STATUS_COLOR.OPEN,
          ],
        },
      });

      // The stylesheet for this lazily loaded chunk can land after the map measured its container,
      // so re-measure before fitting or the view is computed for the wrong size.
      map.resize();
      const bounds = new LngLatBounds();
      for (const f of sector.features) bounds.extend(f.geometry.coordinates);
      map.fitBounds(bounds, { padding: 48, duration: 0 });

      for (const layer of ["posts", "depots", "passes"]) {
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
        map.on("click", layer, (e) => {
          const f = e.features?.[0];
          if (!f) return;
          const p = f.properties as Record<string, unknown>;
          const rows: string[] = [`<strong>${p.name ?? p.id}</strong>`, `${p.kind}`];
          if (p.altitude_m) rows.push(`${Number(p.altitude_m).toLocaleString()} m`);
          if (p.kind === "post") {
            rows.push(`${p.formation} · ${p.troops} troops`);
            rows.push(`depot ${p.depot} · via ${String(p.via_pass).replace("_", " ")}`);
            if (p.cover_ratio !== undefined && p.cover_ratio !== null)
              rows.push(`worst cover ${Math.round(Number(p.cover_ratio) * 100)}% of minimum`);
          }
          if (p.kind === "pass" && p.status) rows.push(String(p.status).replace("_", " "));
          new Popup({ closeButton: false })
            .setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
            .setHTML(rows.join("<br/>"))
            .addTo(map);
        });
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [sector]);

  return <div ref={container} className="map" role="img" aria-label="Map of posts, depots and passes" />;
}
