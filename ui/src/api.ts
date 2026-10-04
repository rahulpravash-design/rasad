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

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const err = await res.json();
      if (typeof err?.detail === "string") detail = err.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export type Verdict = "VERIFIED" | "FLAGGED" | "REJECTED";
export type AttackType =
  | "forged_signature"
  | "replay"
  | "inflated_consumption"
  | "deflated_stock";

export interface StoredReport {
  report_id: string;
  post_id: string;
  ts: string;
  class: string;
  opening: number;
  received: number;
  consumed: number;
  closing: number;
  nonce: string;
  sig: string | null;
  origin: "field" | "injected";
  verdict: Verdict;
  reasons: string[];
  reason_codes: string[];
  score: number | null;
}

export interface ReportList {
  as_of: string;
  total: number;
  items: StoredReport[];
}

export interface VerdictOut {
  report_id: string;
  verdict: Verdict;
  reasons: string[];
  reason_codes: string[];
  score: number | null;
}

export interface InjectResult {
  attack_type: AttackType;
  target: string;
  detected: boolean;
  verdict: VerdictOut;
  report: StoredReport;
}

export interface ReportsSummary {
  as_of: string;
  field: Record<Verdict, number>;
  injected: Record<Verdict, number>;
}

export interface ForecastDay {
  date: string;
  p10: number;
  p50: number;
  p90: number;
  actual: number | null;
}

export interface ForecastResponse {
  post_id: string;
  class: string;
  unit: string;
  as_of: string;
  model: string;
  days: ForecastDay[];
}

export interface ForecastItem {
  class: string;
  unit: string;
  stock: number;
  p50_30d: number;
  p90_30d: number;
  days_of_cover: number | null;
  recommended: number;
}

export interface FederatedSummary {
  holdout_winter: string;
  rounds: number;
  raw_bytes_shared: number;
  shared: string;
  horizon: string;
  formations: {
    formation: string;
    training_rows: number;
    local_wape: number;
    federated_wape: number;
    central_wape: number;
  }[];
}
