# ForestWatch result API (`backend/`)

Read-only HTTP service that serves an already-produced result bundle to the
frontend. It performs **no** acquisition, processing or writing: every route is
a `GET`, and there is no raw directory mount.

This document covers the endpoints, the bundle contract the service enforces,
and how the frontend should integrate. Frontend/UI code is owned elsewhere.

## Running

```bash
pip install -r requirements-backend.txt

# bundle location: argument > FORESTWATCH_BUNDLE_DIR > data/processed/current
FORESTWATCH_BUNDLE_DIR=data/processed/current \
  python -m uvicorn backend.app:app --port 8000
```

Programmatic use:

```python
from pathlib import Path
from fastapi import FastAPI
from backend.app import create_app

app: FastAPI = create_app(bundle_dir=Path("data/processed/current"))
```

`backend.app:app` is the module-level application export and uses the
environment/default bundle directory.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `FORESTWATCH_BUNDLE_DIR` | `data/processed/current` | Directory holding `analysis.json`, `regions.geojson` and the PNG previews. |
| `FORESTWATCH_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Comma-separated list of allowed browser origins. |

CORS is restricted to exactly the configured origins; requests from any other
`Origin` get no `Access-Control-Allow-Origin` header.

## Bundle states

`GET /api/status` distinguishes three states. The frontend should branch on
`state` and never assume data exists.

| `state` | Meaning | Data endpoints |
| --- | --- | --- |
| `awaiting_analysis` | No bundle has been produced yet (directory missing or empty). | `404` |
| `ready` | A bundle exists and satisfies the contract. | `200` |
| `error` | A bundle exists but is malformed, incomplete or inconsistent. | `503` |

`404` means "not produced yet"; `503` means "produced but broken". The service
never substitutes defaults, zero values or estimates for missing data.

The bundle is validated once and cached, and revalidated automatically when the
bundle directory changes (per-file size, mtime and inode). Dropping a new bundle
into place is picked up without a restart.

## Endpoints

### `GET /health`

Liveness probe. Deliberately independent of the bundle, so container health
checks keep passing before the first analysis exists.

```json
{ "status": "ok", "service": "forestwatch-api" }
```

### `GET /api/status`

Always `200`.

```json
{
  "state": "ready",
  "schema_version": 1,
  "analysis_id": "<string or null>",
  "scene_count": 2,
  "message": "Result bundle is available."
}
```

`analysis_id` and `scene_count` are `null`/`0` unless `state == "ready"`.

### `GET /api/analysis`

The validated analysis document, served verbatim. The only addition is a
`url` field on each imagery entry:

```json
{
  "schema_version": 1,
  "analysis_id": "...",
  "title": "...",
  "bbox": [west, south, east, north],
  "scenes": [
    {
      "id": "...",
      "acquired_at": "2026-01-15T10:00:00Z",
      "polarization": "VV",
      "beam_mode": "IW",
      "orbit_direction": "ASCENDING",
      "relative_orbit": 12345,
      "product_type": "GRD",
      "source_collection": "...",
      "catalog_url": "..."
    }
  ],
  "method": {
    "quantity": "sigma0",
    "units": "dB",
    "change_definition": "10*log10(after/before)",
    "threshold_db": -2.0,
    "minimum_area_ha": 1.0,
    "speckle_filter": "...",
    "registration": { "status": "...", "residual_pixels": null },
    "preprocessing": ["..."]
  },
  "metrics": {
    "region_count": 0,
    "total_changed_area_ha": 0.0,
    "valid_area_ha": 0.0,
    "not_evaluable_area_ha": 0.0,
    "scene_count": 2
  },
  "imagery": {
    "before": { "path": "before.png", "bounds": [w, s, e, n], "label": "...", "url": "/api/imagery/before" },
    "after":  { "path": "after.png",  "bounds": [w, s, e, n], "label": "...", "url": "/api/imagery/after" },
    "change": { "path": "change.png", "bounds": [w, s, e, n], "label": "...", "url": "/api/imagery/change" }
  },
  "demo_region_id": "region-id or null",
  "limitations": ["..."]
}
```

### `GET /api/regions`

A WGS84 `FeatureCollection`, served verbatim. Feature order follows
`regions.geojson` and is stable across requests. Each feature carries:

- `id` (identical to `properties.region_id`)
- `geometry`: `Polygon` or `MultiPolygon`
- `properties.area_ha`, `change_db` (signed median dB), `magnitude_db`
  (median absolute dB)
- `properties.detected_at`, `last_observed_unchanged_at`,
  `onset_interval.{start,end}` (ISO 8601 UTC)
- `properties.priority_score` with `priority_units` (`dB sqrt(ha)`) and
  `priority_formula` (`magnitude_db * sqrt(area_ha)`)
- `properties.persistence.{status,observations_after_detection,changed_observations,rate}`
- `properties.historical_anomaly`
- `properties.explanation`
- `properties.time_series[]` with `acquired_at`, `mean_backscatter_db`,
  `change_from_baseline_db`, `valid_fraction`

**Null is not zero.** With only two dates, `persistence.status` is
`"not_evaluable"`, `persistence.rate` is `null` and `historical_anomaly` is
`null`. That is "unavailable", not "no change". The same applies to
`time_series[].change_from_baseline_db` for the baseline acquisition. Render
these as unavailable / not assessed; do not coerce to `0`, and do not present
them as probabilities or causal statements — the API emits no such claims.

### `GET /api/regions/{region_id}`

One GeoJSON `Feature`. `404` when the id is not in the current bundle, with a
`{"detail": "..."}` body. The id is used as a dictionary key only; it is never
interpreted as a path.

### `GET /api/imagery/{key}`

`key` must be `before`, `after` or `change`; anything else is `404`. The bytes
are read from the bundle file declared by the analysis document, served as
`image/png`. Only declared, contained, regular (non-symlink) PNG files are ever
read; there is no directory listing and no static mount.

### Errors

All error bodies are `{"detail": "<human readable message>"}`. Messages describe
the contract failure in terms of field names only — never filesystem paths,
raw parser output or environment contents.

| Code | Meaning |
| --- | --- |
| `404` | No result bundle has been produced yet, unknown region id, or unknown imagery key. |
| `405` | A non-`GET` method was used; the API is read-only. |
| `503` | A bundle exists but fails validation, or a declared preview became unreadable. |

OpenAPI is at `/openapi.json`, and `/docs` for the generated explorer.

## What validation enforces

A bundle is served only if all of the following hold. Anything else is reported
as `error` / `503` rather than served partially.

**Structure and identifiers**

- `analysis.schema_version == 1`; `regions.type == "FeatureCollection"`.
- Every contract field is present; imagery declares exactly `before`, `after`,
  `change`, each with exactly `path`, `bounds`, `label`.
- `feature.id == properties.region_id`; region ids are unique.
- `metrics.region_count == len(features)` and `metrics.scene_count == len(scenes)`.
- `demo_region_id` is `null` or an existing region id.
- `method.quantity` is `sigma0` or `gamma0`; `units == "dB"`;
  `change_definition == "10*log10(after/before)"`.
- `priority_units == "dB sqrt(ha)"` and
  `priority_formula == "magnitude_db * sqrt(area_ha)"`.

**Numbers and dates**

- Every number is finite: `NaN` and `Infinity` are rejected, whether they arrive
  as JSON literals or as values.
- Areas are non-negative; `valid_fraction` and a `persistence.rate` are within
  `[0, 1]`; `changed_observations <= observations_after_detection`.
- Dates are ISO 8601 **UTC** (`...Z` or `+00:00`); other offsets and
  date-only strings are rejected.
- At least two **separate** acquisition dates are required; duplicate dates are
  rejected. Each region's `time_series` must be strictly ascending.
- `persistence.rate` is `null` exactly when `status == "not_evaluable"`; a
  `"observed"` status requires a rate. `historical_anomaly` must be present
  (may be `null`).

**Geometry**

- `bbox` is `[west, south, east, north]` with `west < east`, `south < north`,
  longitude in `[-180, 180]`, latitude in `[-90, 90]`; imagery bounds likewise.
- Polygon rings have at least four positions, are closed, and every position is
  a numeric `[lon, lat]` pair inside the WGS84 ranges. `MultiPolygon` is
  supported; other geometry types are rejected.

**Imagery safety**

- `path` must be exactly `<key>.png` — a plain file name, no separators, no
  `..`, no leading dot.
- The resolved path must sit directly in the bundle directory, must not be a
  symlink (the bundle directory itself must not be a symlink either), must be a
  regular file, and must begin with the PNG signature.
- The same containment and PNG checks run again on every read, so a file swapped
  for a symlink after validation is refused with `503`.

## Frontend integration

1. Poll or fetch `GET /api/status` on load.
2. If `state === "awaiting_analysis"`, show the awaiting state; do not call the
   data endpoints and do not render zeros as measurements.
3. If `state === "error"`, show the returned `message` as a problem to report;
   the data is not trustworthy.
4. If `state === "ready"`, fetch `/api/analysis` and `/api/regions` in parallel.
5. Use `analysis.imagery.<key>.url` as the `<img>` source. Those URLs are
   relative, so no bundle path is ever needed client-side.
6. Use `analysis.demo_region_id` to preselect a region, and
   `/api/regions/{id}` to fetch one region on demand.
7. Treat `null` metrics as unavailable: render "not assessed" and never `0`.
8. `analysis.bbox` and every `bounds` array is `[west, south, east, north]` in
   WGS84 (EPSG:4326) — map libraries usually want the same order, but check.
9. `analysis.limitations` and each `properties.explanation` are provided for
   display as-is; do not synthesise additional claims.

## Tests

```bash
python -m pytest
```

Tests live in `tests/backend/` and use synthetic fixtures written to pytest
`tmp_path` directories only. No synthetic data exists outside `tests/`, and no
complete prepared raster inputs exist in the repository yet.
