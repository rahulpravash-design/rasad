// Typed client for the RASAD API. The API is mounted at /api (Vite proxy in dev, nginx in Docker).
const BASE: string = import.meta.env.VITE_API_URL ?? "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export interface Health {
  status: string;
  offline_mode: boolean;
  data_loaded: boolean;
  data?: {
    seed: string;
    weather_source: string;
    consumption_source: string;
  };
}

export interface Kpis {
  as_of: string;
  posts: number;
  formations: number;
  depots: number;
  convoys: number;
  convoy_window_days: number;
  passes_at_risk: number;
  passes_closed: number;
  readiness_pct: number;
  post_classes_total: number;
  post_classes_ready: number;
}

export type PassState = "OPEN" | "AT_RISK" | "CLOSED";

export interface PassRow {
  pass: string;
  name: string;
  altitude_m: number;
  status: PassState;
  p_close: number | null;
  days: number | null;
  snow_3d_cm: number | null;
  temp_14d_c: number | null;
  source: string;
}

export interface PassesResponse {
  as_of: string;
  passes: PassRow[];
}

export interface SectorFeature {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: Record<string, unknown> & { kind: "post" | "depot" | "pass"; id: string };
}

export interface SectorGeoJson {
  type: "FeatureCollection";
  as_of: string;
  features: SectorFeature[];
}
