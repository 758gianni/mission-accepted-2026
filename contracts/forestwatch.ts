/**
 * ForestWatch dashboard integration contract (read-only HTTP API).
 *
 * These types mirror, field for field, the response models and the bundle
 * validator at published head 87cfacb58d5c4896389eb80749e6222f687db4cb
 * (`backend/models.py`, `backend/validation.py`, `backend/app.py`) plus the
 * producer contract at b8d9059dc23efaf122ee85bd4403bcb1df350ce1
 * (`docs/CHANGE.md`). Every field below was read out of that source; none is
 * invented.
 *
 * Status: NOT CERTIFIED. The API, acquisition and inventory authors were still
 * correcting review findings when this file was written, so the heads above are
 * the best available reference rather than an approved final contract. Treat
 * unknown or renamed fields as a contract question for the backend owner, not as
 * something to paper over in the dashboard. `/openapi.json` on a running service
 * is the machine-readable version of the same contract.
 *
 * This module contains types and one fetch example only. It is not UI code and
 * it is owned by the backend contract author, not by the frontend team.
 */

/** ISO 8601 timestamp that the validator only accepts in UTC (`Z` or `+00:00`). */
export type UtcTimestamp = string;

/** `[west, south, east, north]` in WGS84 (EPSG:4326) decimal degrees. */
export type Wgs84Bounds = [number, number, number, number];

/** Backscatter quantity the change was computed from. */
export type Quantity = "sigma0" | "gamma0";

/** The only three previews the bundle may declare. */
export type ImageryKey = "before" | "after" | "change";

// ---------------------------------------------------------------------------
// /health
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: "ok";
  service: string;
}

// ---------------------------------------------------------------------------
// /api/status  (always HTTP 200)
// ---------------------------------------------------------------------------

/**
 * - `awaiting_analysis` -> no bundle produced yet; data endpoints answer 404
 * - `ready`              -> bundle valid; data endpoints answer 200
 * - `error`              -> bundle present but invalid; data endpoints answer 503
 *
 * `analysis_id` is null and `scene_count` is 0 in every non-`ready` state.
 */
export type BundleState = "awaiting_analysis" | "ready" | "error";

export interface StatusResponse {
  state: BundleState;
  schema_version: 1;
  analysis_id: string | null;
  scene_count: number;
  /** Human readable, safe to display verbatim as plain text. */
  message: string;
}

// ---------------------------------------------------------------------------
// /api/analysis
// ---------------------------------------------------------------------------

export interface Registration {
  /** Upstream attestation, not a measurement this API performs. */
  status: string;
  /** null means unavailable, not zero. */
  residual_pixels: number | null;
}

export interface Method {
  quantity: Quantity;
  /** Always `"dB"`. */
  units: "dB";
  /** Always `"10*log10(after/before)"`. */
  change_definition: string;
  /** Signed dB threshold actually applied; the caller supplies it, no default. */
  threshold_db: number;
  minimum_area_ha: number;
  speckle_filter: string;
  registration: Registration;
  preprocessing: string[];
}

export interface Metrics {
  region_count: number;
  total_changed_area_ha: number;
  valid_area_ha: number;
  not_evaluable_area_ha: number;
  /** Total extent = valid_area_ha + not_evaluable_area_ha (0.01 ha tolerance). */
  analysis_area_ha: number;
  scene_count: number;
}

export interface Scene {
  id: string;
  acquired_at: UtcTimestamp;
  polarization: string;
  beam_mode: string;
  orbit_direction: string;
  /** null when the acquisition is not on a known relative orbit. */
  relative_orbit: number | null;
  product_type: string;
  source_collection: string;
  /**
   * Public EODMS/STAC catalogue link for the scene. Observed values in the
   * catalogue selection artefacts are `https://`. Treat it as untrusted text:
   * only `http:`/`https:` is renderable, and it is a metadata link, not a
   * download that works without credentials.
   */
  catalog_url: string;
}

export interface ImageryItem {
  /** Bundle-relative file name, always exactly `<key>.png`. */
  path: string;
  /** WGS84 bounds; validated to equal `analysis.bbox` exactly. */
  bounds: Wgs84Bounds;
  label: string;
  /**
   * Added by the API (it is not in analysis.json). Relative path such as
   * `/api/imagery/before`; resolve against the API origin and use as the
   * `<img>` src.
   */
  url: string;
}

export interface AnalysisResponse {
  schema_version: 1;
  analysis_id: string;
  title: string;
  bbox: Wgs84Bounds;
  /** The producer emits exactly 2 for a two-date pair. */
  scenes: Scene[];
  method: Method;
  metrics: Metrics;
  imagery: Record<ImageryKey, ImageryItem>;
  /** null until an independent review of real results nominates a region. */
  demo_region_id: string | null;
  /** Display as-is; do not synthesise additional claims. */
  limitations: string[];
}

// ---------------------------------------------------------------------------
// /api/regions and /api/regions/{region_id}
// ---------------------------------------------------------------------------

/**
 * With exactly two acquisitions this is always `not_evaluable` with a null
 * rate. `null` means unavailable, NOT zero and NOT "no change".
 */
export interface Persistence {
  status: "not_evaluable" | "observed";
  observations_after_detection: number;
  changed_observations: number;
  /** null means unavailable, not zero. */
  rate: number | null;
}

export interface TimeSeriesPoint {
  acquired_at: UtcTimestamp;
  /** null means unavailable. */
  mean_backscatter_db: number | null;
  /** null on the baseline acquisition itself; means unavailable. */
  change_from_baseline_db: number | null;
  /** In [0, 1]. */
  valid_fraction: number;
}

/** Bracketing acquisitions. NOT an inferred physical onset interval. */
export interface ObservationInterval {
  start: UtcTimestamp;
  end: UtcTimestamp;
}

export interface RegionProperties {
  region_id: string;
  /** Geodesic area on the WGS84 ellipsoid, hectares, holes subtracted. */
  area_ha: number;
  /** Signed median of per-pixel dB change. dB. */
  change_db: number;
  /**
   * Median of the ABSOLUTE per-pixel dB change. dB.
   * `median(|x|)` is not `|median(x)|`: a separate statistic, not derivable
   * from `change_db`, and the two need not agree.
   */
  magnitude_db: number;
  /** Acquisition at which a radar difference was first OBSERVED. */
  detected_at: UtcTimestamp;
  /** Acquisition used as the baseline reference. Always precedes detected_at. */
  baseline_at: UtcTimestamp;
  /** Acquisition window bracketing the observation; `start <= baseline_at`, `detected_at <= end`. */
  observation_interval: ObservationInterval;
  /** magnitude_db * sqrt(area_ha) */
  priority_score: number;
  /** Always `"dB sqrt(ha)"`. */
  priority_units: string;
  /** Always `"magnitude_db * sqrt(area_ha)"`. */
  priority_formula: string;
  persistence: Persistence;
  /** null means unavailable (no history); not zero. */
  historical_anomaly: number | null;
  /** Plain-language summary; states that the cause is undetermined. */
  explanation: string;
  time_series: TimeSeriesPoint[];
}

export interface PolygonOrMultiPolygonGeometry {
  type: "Polygon" | "MultiPolygon";
  /** GeoJSON position arrays; the union keeps the two cases structurally open. */
  coordinates: unknown;
}

export interface RegionFeature {
  type: "Feature";
  /** Always identical to `properties.region_id`. */
  id: string;
  geometry: PolygonOrMultiPolygonGeometry;
  properties: RegionProperties;
}

export interface RegionFeatureCollection {
  type: "FeatureCollection";
  /** Order follows regions.geojson and is stable across requests. */
  features: RegionFeature[];
}

// ---------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------

/**
 * All error bodies. 404 = nothing produced yet / unknown region id / unknown
 * imagery key. 405 = a non-GET was used. 503 = bundle present but failing
 * validation. `detail` never contains a filesystem path or raw tool output.
 */
export interface ErrorResponse {
  detail: string;
}

// ---------------------------------------------------------------------------
// fetch example
// ---------------------------------------------------------------------------

export type DashboardPhase =
  | { kind: "awaiting"; message: string }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      analysis: AnalysisResponse;
      regions: RegionFeatureCollection;
    };

const API_ORIGIN = "http://localhost:8000";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_ORIGIN}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    // Body is {detail}. Never fabricate a substitute payload here: an absent
    // bundle and a broken bundle are different states and must stay visible.
    const body = (await response.json().catch(() => null)) as ErrorResponse | null;
    throw new Error(body?.detail ?? `GET ${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * Branch on status first, and only then call the data endpoints. `state`
 * defaults to the awaiting case if the response cannot be read at all, so the
 * dashboard never renders numbers it did not receive.
 */
export async function loadDashboard(): Promise<DashboardPhase> {
  let status: StatusResponse;
  try {
    status = await getJson<StatusResponse>("/api/status");
  } catch (cause) {
    return { kind: "awaiting", message: `Status unreachable: ${String(cause)}` };
  }

  if (status.state === "awaiting_analysis") {
    return { kind: "awaiting", message: status.message };
  }
  if (status.state === "error") {
    return { kind: "error", message: status.message };
  }

  const [analysis, regions] = await Promise.all([
    getJson<AnalysisResponse>("/api/analysis"),
    getJson<RegionFeatureCollection>("/api/regions"),
  ]);
  return { kind: "ready", analysis, regions };
}

/**
 * Returns a value usable as an anchor href, or null when the scheme is not
 * http/https. Scene `catalog_url` comes from catalogue metadata, so it is
 * untrusted: reject `javascript:`, `data:` and anything else rather than
 * sanitising it into something that looks safe.
 */
export function safeExternalHref(url: string): string | null {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}