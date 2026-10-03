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

Preflight is answered. `OPTIONS` on any route from an allowed origin returns
`204` with `Access-Control-Allow-Origin`, `Access-Control-Allow-Methods:
GET, HEAD, OPTIONS` and `Access-Control-Allow-Headers: Accept, Content-Type`,
so a browser will actually let the frontend call the API. Only `GET` and `HEAD`
are answered as a requested method, and no write verb is ever advertised, so the
API stays read-only; `POST`/`PUT`/`PATCH`/`DELETE` on any route remain `405`. A
preflight from a disallowed origin gets no allow header and no `204`.

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

### Published behind a generation pointer

The producer publishes a bundle by renaming a finished generation directory into
place and then swapping a **single relative symlink** at the configured root, so
a reader sees either the old generation or the new one and never a
half-replaced bundle. That is supported, but only for that exact shape and only
for the configured root:

- the configured root itself may be that pointer;
- its target must be a **relative single basename** — no separators, no `..`,
  not absolute — so it cannot address anything outside the resolved parent;
- the target must match the producer's pattern `.<root name>.gen-<suffix>`,
  optionally with a `-<pid>` suffix;
- the target must itself be a **real directory**, not a symlink, so a chained or
  nested pointer cannot be walked further.

Everything else is refused as a controlled `error` / `503`: arbitrary external
targets, absolute links, links escaping the parent, dangling pointers, links
whose target is itself a symlink, and links whose name does not match the
generation pattern. An ordinary direct bundle directory is unaffected and is
never followed. This is not a general "follow symlinks" permission: no
symlink is ever honoured anywhere except this one pointer, and the imagery
symlink protections described below are unchanged.

**One generation per snapshot.** The pointer is resolved exactly once per load.
`analysis.json`, `regions.geojson` and all three previews are then read from
that pinned directory, and imagery requests read from the generation their
snapshot pinned — the pointer is never rediscovered while serving. A swap part
way through a load therefore cannot pair one generation's metadata with
another's pixels.

**The pinned root is verified before every read.** Resolving the pinned path on
each read would follow a symlink that had replaced it after validation and serve
bytes from outside the bundle, so the pinned path is used as given and its
identity is re-checked with one `lstat` first: it must still be a real
directory, not a symlink, and its identity must still match the one recorded at
validation. Identity is `(device, inode, entries)` where entries is each direct
child's name, size, mtime, inode and mode. The inode alone is not enough —
deleting a directory and recreating one at the same path can hand back the same
inode, which is exactly the prune-and-republish case — and on this filesystem
`st_ctime_ns` even repeats across a delete-and-recreate in the same tick, so the
entries are what distinguish a recreated directory. A renamed, deleted, replaced
or modified pinned generation is refused with a controlled `503`; the request
never falls back to the pointer or to an alias of the same path.

**Flips are noticed.** The cache keys on the pointer's own identity plus the
resolved generation's contents, so an A → B flip, a B → A flip back and a new
generation directory are all picked up without a restart, even when names are
reused.

**Pruning limit, reported honestly.** The producer keeps the published
generation plus two superseded ones and deletes anything older. A snapshot that
has already pinned a generation can therefore find its files deleted underneath
it, for instance on a repeated A → B → A cycle. In that case the imagery request
returns a controlled `503`. It is deliberately **not** retried against the
current pointer: serving different pixels under a URL the client already holds
would be worse than an honest failure. The next `/api/status` picks up whatever
is published then.

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
    "threshold_db": 1.0,
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
    "analysis_area_ha": 0.0,
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
- `properties.area_ha`
- `properties.change_db` — the **signed median** of the per-pixel dB change
  inside the region
- `properties.magnitude_db` — the **median of the absolute** per-pixel dB change
  inside the region

  These are two different statistics. `median(|x|)` is not `|median(x)|`, so the
  two fields are independent and need not agree. Both are served exactly as the
  analysis produced them; the API never reconciles or derives one from the other.
- `properties.detected_at` — the **acquisition at which a radar difference was
  first observed** (an observation time, not a change time)
- `properties.baseline_at` — the acquisition used as the baseline reference
- `properties.observation_interval.{start,end}` — the acquisition window the
  observation is bracketed by. It is **not** an onset interval: two scenes
  cannot establish when a change physically began, and nothing in this API
  claims they can. All three timestamps are ISO 8601 UTC and must each name the
  `acquired_at` time of a scene in `analysis.scenes`.
- `properties.priority_score` with `priority_units` (`dB sqrt(ha)`) and
  `priority_formula` (`magnitude_db * sqrt(area_ha)`)
- `properties.persistence.{status,observations_after_detection,changed_observations,rate}`
  where `observations_after_detection` counts real acquisitions *later than*
  `detected_at`, and `rate` is
  `changed_observations / observations_after_detection`
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

**A two-date bundle cannot report observed persistence.** Detection happens at
the later of the two acquisitions, so there is nothing after it to observe.
Every region in such a bundle has `status: "not_evaluable"`,
`observations_after_detection: 0`, `changed_observations: 0` and `rate: null`.
`"observed"` requires at least one acquisition after `detected_at`, and the
count can never exceed the number of scenes acquired after `detected_at`.

### `GET /api/regions/{region_id}`

One GeoJSON `Feature`. `404` when the id is not in the current bundle, with a
`{"detail": "..."}` body. The id is used as a dictionary key only; it is never
interpreted as a path.

### `GET /api/imagery/{key}`

`key` must be `before`, `after` or `change`; anything else is `404`. The bytes
are read from the bundle file declared by the analysis document, served as
`image/png`, out of the generation the request's snapshot pinned. Only declared,
contained, regular (non-symlink) PNG files are ever read; there is no directory
listing and no static mount.

### Generation safety across requests

Because each HTTP request re-reads the pointer, two separate requests can
legitimately see two different generations. To stop a client silently receiving
imagery from an analysis it never looked at, `/api/analysis` returns each image
URL with the current `analysis_id` already attached:

```
/api/imagery/before?analysis_id=<analysis_id>
```

`/api/imagery/{key}` therefore accepts an optional `analysis_id` query
parameter. If it is supplied and does not match the published analysis, the
answer is **`409`** with a `detail` naming the stale id — never different
pixels. Omitting it keeps the original behaviour and returns whatever is
published now, so existing bare URLs keep working.

Frontend guidance: use the URLs `/api/analysis` hands you rather than building
your own, and on a `409` re-fetch `/api/analysis` and reload with the URLs it
returns. No new endpoint path is involved.

The three previews are identically warped onto the analysis grid, so each
`imagery.<key>.bounds` must be **exactly equal** to `analysis.bbox`. A mismatch
is a validation error, not a warning: otherwise the frontend overlay would be
georeferenced to the wrong extent.

### Errors

All error bodies are `{"detail": "<human readable message>"}`. Messages describe
the contract failure in terms of field names only — never filesystem paths,
raw parser output or environment contents.

| Code | Meaning |
| --- | --- |
| `404` | No result bundle has been produced yet, unknown region id, or unknown imagery key. |
| `405` | A non-`GET` method was used; the API is read-only. |
| `409` | The `analysis_id` on an image request is no longer the published analysis; reload. |
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
- `metrics.analysis_area_ha` is required and equals
  `valid_area_ha + not_evaluable_area_ha` (see the area tolerance below).
- `metrics.total_changed_area_ha` equals the sum of the `area_ha` values in
  `regions.geojson`, and is not greater than `valid_area_ha`.
- `demo_region_id` is `null` or an existing region id.
- `method.quantity` is `sigma0` or `gamma0`; `units == "dB"`;
  `change_definition == "10*log10(after/before)"`.
- `priority_units == "dB sqrt(ha)"` and
  `priority_formula == "magnitude_db * sqrt(area_ha)"`.

**Persistence and priority**

- `persistence.status == "observed"` requires
  `observations_after_detection >= 1`, capped by the number of acquisitions
  later than `detected_at`, with `changed_observations <= observations` and
  `rate ~= changed_observations / observations_after_detection`.
- `persistence.status == "not_evaluable"` requires zero observations, zero
  changed observations and a `null` rate.
- `priority_score ~= magnitude_db * sqrt(area_ha)`.

**Detection gates**

- `method.threshold_db` must be strictly positive. It is a threshold on the
  **absolute** magnitude, i.e. the median of `|dB|`.
- Each region must satisfy `magnitude_db >= threshold_db` and
  `area_ha >= minimum_area_ha`.
- `abs(change_db)` is deliberately **not** compared against `threshold_db`. The
  signed median can cancel out inside a heterogeneous region while individual
  pixels still fall below the threshold, so `change_db` near zero with a large
  `magnitude_db` is a real, reportable situation and passes validation.

**Tolerances for derived values**

Derived numbers a producer may legitimately round are compared with
`math.isclose`, never corrected:

| Value | Tolerance |
| --- | --- |
| `persistence.rate` | `rel_tol=1e-3`, `abs_tol=0.01` |
| `priority_score` | `rel_tol=1e-3`, `abs_tol=0.01` |
| `magnitude_db` vs `threshold_db` | `abs_tol=0.01` dB |
| `area_ha` vs `minimum_area_ha` | `abs_tol=0.01` ha |
| area sums | `abs_tol=0.01` ha (see below) |

**Numbers and dates**

- Every number is finite, everywhere. `NaN` and `Infinity` literals are
  rejected, and the JSON reader is given a `parse_float` guard so an overflowing
  literal such as `1e400` or `1e999` is refused **at parse time**, including
  inside unknown extension keys that no field validator ever inspects. Integer
  literals too large for a double (for example 400 digits) are refused too.
  This matters because a non-finite number that slips through is not merely
  wrong data: it makes the response fail while it is being serialised, which
  turns a broken bundle into a `500` instead of a reported `error` / `503`.
  Legal extension keys are untouched: arbitrary nested objects, arrays, nulls
  and large but finite numbers such as `1e308` still load and are served
  verbatim.
- Areas are non-negative; `valid_fraction` and a `persistence.rate` are within
  `[0, 1]`; `changed_observations <= observations_after_detection`.
- Dates are ISO 8601 **UTC** (`...Z` or `+00:00`); other offsets and
  date-only strings are rejected. Comparisons are on instants, so `...Z` and
  `...+00:00` for the same moment are equivalent.
- At least two **separate** acquisition dates are required. Scene ids are
  unique, scene acquisition times are unique, and `scenes` is sorted ascending
  by `acquired_at`.
- Every `time_series[].acquired_at` must be a scene acquisition, and each
  region's series is strictly ascending and non-empty. **Missing points are
  never fabricated**: a region observed at a subset of the acquisitions is
  served with exactly those points, gaps and all.
- `persistence.rate` is `null` exactly when `status == "not_evaluable"`; a
  `"observed"` status requires a rate. `historical_anomaly` must be present
  (may be `null`).

**Observation semantics**

- `baseline_at`, `detected_at`, `observation_interval.start` and
  `observation_interval.end` must each be the `acquired_at` time of a scene in
  `analysis.scenes` (compared as instants, so `...Z` and `...+00:00` are
  equivalent). This keeps the reported times tied to real acquisitions instead
  of inferred dates.
- `baseline_at < detected_at`, and `observation_interval` brackets both:
  `start <= baseline_at` and `detected_at <= end`.
- There is deliberately no field claiming stability or an onset time. Two scenes
  cannot support either claim.

**Areas and the floating tolerance**

Area relations are compared with `math.isclose(rel_tol=1e-9, abs_tol=0.01)`,
i.e. an absolute tolerance of **0.01 ha (100 m²)**, because rasterised region
areas accumulate small floating point error. The tolerance is applied to:

- `valid_area_ha + not_evaluable_area_ha` vs `analysis_area_ha`
- `total_changed_area_ha` vs `sum(region.area_ha)`

and the comparison is never loosened beyond 0.01 ha. The
`total_changed_area_ha <= valid_area_ha` bound allows the same 0.01 ha slack so
float noise cannot fail a physically exact result. Nothing else is rounded or
adjusted; every served number is the one the analysis declared.

**Geometry**

- `bbox` is `[west, south, east, north]` with `west < east`, `south < north`,
  longitude in `[-180, 180]`, latitude in `[-90, 90]`; imagery bounds likewise.
- Each `imagery.<key>.bounds` must equal `analysis.bbox` exactly. The previews
  are identically warped, so any other extent is an error.
- Polygon rings have at least four positions, are closed, and every position is
  a numeric `[lon, lat]` pair inside the WGS84 ranges. `MultiPolygon` is
  supported; other geometry types are rejected.

**Links**

- `scenes[].catalog_url` must be an absolute `http` or `https` URL with a host.
  `javascript:`, `data:`, `file:` and relative URLs are rejected.

**Imagery safety**

- `path` must be exactly `<key>.png` — a plain file name, no separators, no
  `..`, no leading dot.
- The resolved path must sit directly in the bundle directory, must not be a
  symlink (the bundle directory itself must not be a symlink either) and must be
  a regular file.
- Every declared preview is **fully decoded and integrity checked**, not merely
  sniffed. A file that starts with the PNG signature is not enough: a truncated
  43-byte file and a signature followed by zeros both pass a signature check
  while no decoder can read them. Each preview must therefore survive all of:
  the signature; the chunk framing; a correct CRC for **every** chunk; exactly
  one `IHDR`, first, with sane width, height, colour type, bit depth and
  compression/filter/interlace methods; non-empty, consecutive `IDAT` chunks;
  a `PLTE` when the image is palette-based; a terminating `IEND` with nothing
  after it; a pixel stream that inflates cleanly to *exactly* the byte count
  implied by the declared geometry (Adam7 arithmetic for interlaced images);
  and, when Pillow happens to be importable, a successful decode there too.
- Sizes are bounded, so a broken or hostile file cannot make the service
  allocate without end:

  | Bound | Limit | Checked |
  | --- | --- | --- |
  | Preview file size | 32 MiB | from `lstat`, before the bytes are read |
  | Single chunk | 8 MiB | while walking the chunk stream |
  | Total pixels (`width * height`) | 64 MP (about 8000x8000) | from the IHDR header, before inflating or decoding |
  | Decompressed pixel bytes | 128 MiB | from the declared geometry, before inflating |
  | Width or height | 100000 | from the IHDR header |

  The file size is read one byte past the limit rather than whole, so an
  oversize preview is never materialised in memory.
- The **total pixel count** needs its own cap because the packed-byte budget is
  not sufficient on its own: a 1-bit image packs eight pixels into a byte, so
  200 megapixels is only about 25 MB of packed scanlines and passes a byte budget
  while still demanding an enormous allocation from any decoder. A 24 kB file
  of that shape is refused from its header, before any inflate or decode. An
  ordinary map preview near 1024px (about 1 MP) has generous headroom.
- Decoder failures are always normalised. `DecompressionBombError` and
  `DecompressionBombWarning` are both turned into a controlled rejection, as is
  any other exception the decoder raises, so a hostile or broken image always
  produces a reported `error` / `503` bundle and never an uncaught `500`.
- Pillow is an optional extra cross-check. It is never required: the checks are
  implemented against the standard library, so no dependency was added.
- The per-read hot path re-checks containment, the size bound and the signature;
  full integrity is established when the bundle is validated, and the snapshot
  is revalidated whenever a declared file changes. So a file swapped for a
  corrupt one, or for a symlink, after validation is refused with `503`.
- A bundle missing or failing a required preview is rejected **whole**. Serving
  the two healthy previews alongside a broken third was considered and rejected:
  a partially trustworthy bundle is harder for a reader to reason about than a
  clean "no analysis yet" or "bundle is broken" state.

## Text handling: neutral JSON, not sanitisation

The API is **not** an HTML sanitiser and does not pretend to be one. Text fields
(`title`, `labels`, `explanation`, `limitations`, scene metadata) are neutral
strings and are served **verbatim**, including any characters they happen to
contain. A string that looks like markup is not made safe by the server, and
blocking such strings would be arbitrary: it would mangle legitimate text
without removing the actual requirement, which is that whoever renders these
fields escapes them.

What the API does do:

- every text response is `application/json` and nothing declares `text/html`;
- `X-Content-Type-Options: nosniff` is set on every response, so a browser will
  not sniff a JSON body into an executable type;
- the only value reflected back from client input is a region id in a `404`
  `detail`, JSON-encoded and stripped of control characters.

So: **render every text field as text.** In React use a text node or
`dangerouslySetInnerHTML` is never warranted here; in a template engine keep
autoescaping on. Do not `innerHTML`, `v-html` or `document.write` these fields.
Review finding: a raw HTML-looking string in a JSON response is not itself an XSS
vector when the consumer escapes it, and pretending otherwise by stripping tags
server-side would be the wrong fix.

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
   Each `imagery.<key>.bounds` equals `analysis.bbox`, so the preview `<img>` and
   the map extent can be registered from the same numbers.
9. Render all text fields (`title`, `label`, `explanation`, `limitations`) as
   text nodes with normal escaping. The server does not sanitise markup out of
   them; see "Text handling" above.
10. Label the per-region times as observations, not events: `baseline_at` is the
   baseline acquisition, `detected_at` is the acquisition where the radar
   difference was first seen, and `observation_interval` is the acquisition
   window it sits in. Do not label them "date of change" or "onset", and do not
   infer a change date between them.
11. Display `change_db` and `magnitude_db` as two separate statistics (signed
    median vs median of absolute values). Do not derive one from the other, and
    do not assume `magnitude_db == abs(change_db)`.
12. `analysis.metrics.analysis_area_ha` is the total analysis extent; report
    `valid_area_ha` and `not_evaluable_area_ha` as a breakdown of it rather than
    as separate totals.
13. `analysis.limitations` and each `properties.explanation` are provided for
    display as-is; do not synthesise additional claims.

## Tests

```bash
python -m pytest tests/backend
```

Tests live in `tests/backend/` and use synthetic fixtures written to pytest
`tmp_path` directories only. No synthetic data exists outside `tests/`, and no
complete prepared raster inputs exist in the repository yet.

The suite is configuration-free: `tests/backend/conftest.py` puts the repository
root on `sys.path` itself, so it runs under any pytest configuration the team
leads add later (including none). `tests/backend/conftest.py` is also where the
synthetic fixture documents live, so the contract in this document and the
fixtures cannot drift apart unnoticed.
