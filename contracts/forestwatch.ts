/**
 * ForestWatch dashboard integration contract (read-only HTTP API).
 *
 * Reference API head: a3f94b345434cfc76bddefb4a3e0d6692957ccff. Producer
 * contract: b8d9059dc23efaf122ee85bd4403bcb1df350ce1 (`docs/CHANGE.md`).
 *
 * Status: NOT CERTIFIED, AND NOT AUTO-CERTIFYING. The field-for-field source
 * check behind this file was performed against the previous API head
 * 87cfacb58d5c4896389eb80749e6222f687db4cb, which is the newest API head present
 * in the local object store. The reviewed head a3f94b3 could not be inspected:
 * the object is absent locally and no ref points to it, and no fetch or auth was
 * attempted. The field-level statements below are therefore verified against
 * 87cfacb, while the behavioural corrections below come from review and are
 * marked as such per field. If a further API head appears, do NOT treat this
 * file as certified against it automatically: re-run the parity check and
 * re-confirm every "reported by review" claim first.
 *
 * The producer still has an open `relative_orbit` float-vs-integer blocker
 * (see docs/FRONTEND-HANDOFF.md section 12), so producer/API agreement is NOT
 * complete and this contract must not be treated as final.
 *
 * Treat unknown or renamed fields as a contract question for the backend owner,
 * not as something to paper over in the dashboard. `/openapi.json` on a running
 * service is the machine-readable version of the contract.
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
  /**
   * Strictly positive ABSOLUTE per-pixel threshold on `|change_db|`. A pixel is
   * retained when `abs(change_db) >= threshold_db`, so the threshold selects on
   * magnitude and never on sign. Always > 0, and always caller-supplied (the
   * producer has no default). Do not render it as a signed quantity or compare
   * `change_db` against it without taking the absolute value first.
   */
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
  /**
   * null when the acquisition is not on a known relative orbit.
   *
   * KNOWN OPEN BLOCKER: the producer's manifest permits a finite number here
   * while the API validates an integer. A JSON float such as `98.0` is not an
   * `int` and is rejected, which would surface as `state: "error"` and `503`.
   * The producer fix (emit an integer, or `null`) is under way and is NOT yet
   * reflected in the API or producer heads available locally. Until it lands,
   * producer/API agreement is incomplete: a bundle built today may fail to
   * serve for this field alone.
   */
  relative_orbit: number | null;
  product_type: string;
  source_collection: string;
  /**
   * Public EODMS/STAC catalogue link for the scene. Reported by review: the
   * reviewed API head validates that this source URL is valid, which is not
   * observable at 87cfacb (unavailable locally) — treat a bundle as servable
   * only once `/api/status` reports `ready`.
   *
   * Every href observed in the catalogue selection artefacts is `https://`.
   * Treat it as untrusted text regardless: only `http:`/`https:` is renderable
   * (see `safeExternalHref`), and it is a metadata link, not a download that
   * works without credentials.
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
 * Persistence of the change after detection.
 *
 * Reported by review: with the producer's current two-acquisition output this
 * is `not_evaluable` with a null rate. That behaviour is unchanged here.
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
  /** null means unavailable (e.g. no valid ROI support), not zero. */
  mean_backscatter_db: number | null;
  /**
   * `mean_backscatter_db` minus the BASELINE scene's value for this region.
   *
   * At the baseline acquisition itself this is 0.0 whenever the baseline value
   * is valid: a scene does not differ from itself. That 0.0 is a real computed
   * difference against the baseline reference, NOT a claim that the region was
   * historical stability, and NOT an invented value for a missing observation.
   *
   * It stays null when the baseline value is genuinely unavailable (the region
   * had no valid ROI support in the baseline scene). So at the baseline date,
   * null means "not computable" and 0.0 means "computed, zero difference":
   * keep those two apart in the UI rather than defaulting one to the other.
   */
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
  /**
   * Signed MEDIAN of per-pixel dB change over the region. dB.
   *
   * The per-pixel threshold is absolute (see `Method.threshold_db`), but this
   * reported statistic is signed and can therefore be EITHER 0.0 (a region whose
   * pixels balance brightening against darkening) or mixed in sign across
   * regions. A value of 0.0 does NOT mean "no change": every pixel in the region
   * passed `|change_db| >= threshold_db`. Never render 0.0 as unchanged, and
   * never derive `magnitude_db` from it.
   */
  change_db: number;
  /**
   * MEDIAN OF THE ABSOLUTE per-pixel dB change. dB. Always >= 0, and for a
   * retained region at least `threshold_db`.
   *
   * `median(|x|)` is not `|median(x)|`: a separate statistic, not derivable
   * from `change_db`, and the two need not agree. This is the value that drives
   * `priority_score`, so `priority_score` can be substantial while `change_db`
   * is 0.0.
   */
  magnitude_db: number;
  /** Acquisition at which a radar difference was first OBSERVED. */
  detected_at: UtcTimestamp;
  /** Acquisition used as the baseline reference. Always precedes detected_at. */
  baseline_at: UtcTimestamp;
  /** Acquisition window bracketing the observation; `start <= baseline_at`, `detected_at <= end`. */
  observation_interval: ObservationInterval;
  /** Exactly `magnitude_db * sqrt(area_ha)`, from `magnitude_db` and never `change_db`. */
  priority_score: number;
  /** Always `"dB sqrt(ha)"`. */
  priority_units: string;
  /** Always `"magnitude_db * sqrt(area_ha)"`. */
  priority_formula: string;
  persistence: Persistence;
  /** Null means unavailable (no history); not zero. Reported by review: always null for a two-date run. */
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