# Prepared-pair change detection -> result bundle

This document covers one slice of the vertical slice only: turning **two verified
prepared SAR scenes** into a derived change raster, thresholded change polygons,
and the shared result bundle that the API serves. Frontend, inventory,
acquisition, and configuration are owned elsewhere.

There are **no real prepared inputs in this repository yet**. No raw calibration
adapter exists, and no production or demo data is checked in. The only inputs
are synthetic rasters generated inside `tests/processing/test_change.py`, which
are test fixtures and must never be published as results.

## CLI

```bash
python -m processing.change \
    --manifest data/manifests/pair.json \
    --out data/processed/current \
    --threshold-db 2.0 \
    --min-area-ha 1.0
```

* `--manifest` — prepared pair manifest, `schema_version: 1` (below).
* `--out` — bundle directory, created if needed. Published atomically.
* `--threshold-db` — absolute signed dB change threshold. Must be finite and
  **strictly positive**. There is no default and no implicit threshold: the
  caller always supplies the sensitivity explicitly.
* `--min-area-ha` — minimum region area in hectares. Must be finite and
  **non-negative** (`0` keeps every thresholded component).

Invalid parameters, unverified provenance, and unusable inputs exit with status
`2` and a single-line `error: ...` message on stderr, with no traceback and no
files published. Success prints a one-line summary and exits `0`.

Requirements (installed as scratch tooling, not yet recorded in shared
requirements): `numpy`, `scipy`, `rasterio`, `shapely`, `pyproj`, `Pillow`,
`pytest`.

## Manifest schema (`schema_version: 1`)

```json
{
  "schema_version": 1,
  "scenes": [
    {
      "id": "R2-2026-03-04-ASC",
      "acquired_at": "2026-03-04T10:15:00Z",
      "polarization": "C",
      "beam_mode": "S",
      "orbit_direction": "DESCENDING",
      "relative_orbit": 12345,
      "product_type": "SLC",
      "source_collection": "Radarsat-2_Tropical_Forest_Products",
      "catalog_url": "https://...",
      "path": "data/prepared/2026-03-04/before.tif",
      "radiometry": {
        "quantity": "sigma0",
        "representation": "linear_power",
        "calibration": "verified",
        "geocoding": "verified",
        "terrain_correction": "verified",
        "processing_steps": ["calibrated to sigma0", "terrain corrected", "speckle filtered"]
      }
    },
    { "...": "the second scene, on a later distinct UTC date" }
  ],
  "registration": {
    "status": "passed",
    "residual_pixels": 0.8,
    "diagnostic": "reference: inspected processing log and correlation product ..."
  }
}
```

Rules enforced before any pixel is read:

| Field | Rule |
| --- | --- |
| `schema_version` | must be `1` |
| `scenes` | exactly 2, ascending by `acquired_at`, distinct UTC dates, unique ids |
| `acquired_at` | ISO-8601 with an explicit UTC offset (`Z` or `+00:00`) |
| `source_collection` | must be `Radarsat-2_Tropical_Forest_Products` |
| `relative_orbit` | finite number or `null` |
| `radiometry.quantity` | `sigma0` or `gamma0` |
| `radiometry.representation` | must be `linear_power`; `amplitude` and dB are rejected |
| `radiometry.calibration` / `geocoding` | must be `verified` |
| `radiometry.terrain_correction` | `verified` or `not_required` |
| `radiometry.processing_steps` | non-empty list of strings |
| `registration.status` | must be `passed` |
| `registration.residual_pixels` | finite number `>= 0` |
| `registration.diagnostic` | non-empty string naming the inspected evidence |

### The `verified` flags are upstream attestations, not CLI findings

`calibration: "verified"`, `geocoding: "verified"`, `terrain_correction:
"verified"`, and `registration.status: "passed"` are **claims made by whoever
prepared the inputs**. They are only as good as the prior inspected evidence:
a reviewed processing run, a calibration/validation report, a correlation or
registration diagnostic. **This CLI cannot prove any of them.** It never
recalibrates, never re-geocodes, never re-registers, and never inspects the
referenced diagnostics; it only refuses to run when the flags are absent or not
`verified`.

Registering a scene's radiometry as `verified` therefore requires that a human or
a reviewed upstream step has already examined the processing output and
diagnostics and recorded that evidence in `processing_steps` and
`registration.diagnostic`. Flipping the flag to make the CLI run converts an
unverified input into a *asserted* input, not into a verified one, and every
downstream number inherits that claim. This is stated again in
`analysis.json` → `limitations`.

### Rasters are inspected too

Each `path` must open as a single-band **real** linear-power raster:

* complex dtype -> rejected;
* any dataset or band tag mentioning `amplitude` or `db`/`decibel` -> rejected;
* more than 0.1% of valid samples negative (impossible for linear power, typical
  of dB data) -> rejected;
* no CRS, or a missing/degenerate transform (identity, zero or sub-micrometre
  pixel size) -> rejected;
* no valid samples at all -> rejected.

Both scenes must also agree on `quantity`, `polarization`, `beam_mode`,
`orbit_direction`, and on `relative_orbit` when both are known (a `null`
`relative_orbit` is accepted, since an unknown orbit is not a mismatch).

## Preprocessing is decided by the actual acquisition mode

This CLI has **no generic SLC compression or deburst step**. An SLC is already
focused, and what a scene needs before it can be differenced depends on the
mode the product was actually acquired in: multilooking or resolution
adaptation for a GRD-like product, coregistration bookkeeping for an SLC pair,
terrain correction where the geometry requires it, and nothing at all when the
prepared product is already fit for comparison. `radiometry.processing_steps`
records what was actually done upstream for these two scenes, and this CLI
consumes the result rather than assuming a fixed chain. Adding a mode-specific
step here would mean inventing evidence about the inputs, which is exactly what
the provenance rules below forbid.

## Method

1. **Common reference grid.** The two footprints are intersected in WGS84, the
   intersection is expressed in the baseline scene CRS, and the bounds are
   snapped outward to the baseline pixel size. Each scene is reprojected and
   cropped onto that grid (bilinear, nodata-aware), then **remasked**: the
   source validity mask is resampled with nearest-neighbour resampling and
   reapplied, so bilinear interpolation cannot blend neighbours into a cell
   whose centre lands on a nodata source pixel. A missing observation stays
   missing after reprojection.
   *This is grid alignment only. It is not, and is never reported as, a
   measurement or correction of geometric registration.* Residual
   coregistration error comes from the manifest's `registration` evidence and is
   carried straight into the change rasters.
2. **Speckle filter.** A 3x3 mean in **linear power**, nodata-aware. An output
   sample is defined only when **the original centre pixel is itself a valid
   observation** *and* at least 5 of the 9 window samples are valid; the value is
   the mean of the valid (finite, positive) window samples. The centre
   requirement is what stops the kernel from *inventing* a value at a nodata
   pixel out of its neighbours, which would fabricate a measurement exactly
   where the data is missing (regression-tested; at commit 9d27d506 a 5x5 array
   of ones with a single NaN centre returned 1.0 there). This is the
   `method.speckle_filter` string in the bundle.
3. **Change.** `change_db = 10 * log10(after / before)` in dB, evaluated only
   where both filtered scenes are valid, which now also means both *original*
   pixels exist. Every other pixel is nodata and is *not evaluable*, never zero.
   Neighbours of a missing pixel are unaffected: the nodata neighbour is dropped
   from the kernel mean rather than treated as zero backscatter, so a cell with a
   valid centre and 8 of 9 valid window samples keeps its full value.
4. **Threshold and label.** `abs(change_db) >= threshold_db`, then connected
   components with 8-neighbour connectivity, then the minimum area filter
   (using the mean geodesic cell area, and the exact geodesic polygon area as a
   second check). Region geometry is vectorised with **holes preserved**.
5. **Area.** `area_ha` is the geodesic area on the WGS84 ellipsoid of the
   thresholded pixels, with holes subtracted. The projection is only a working
   grid, never an area claim.
6. **Per-region reporting.** `change_db` is the signed median dB over the
   region's pixels, `magnitude_db` the median absolute dB, and
   `priority_score = magnitude_db * sqrt(area_ha)` with
   `priority_units: "dB sqrt(ha)"` and `priority_formula: "magnitude_db * sqrt(area_ha)"`.
7. **Time series.** For each region, the ROI is rasterised on the reference
   grid; `mean_backscatter_db` is `10*log10(mean linear power)` over the valid
   ROI pixels of each scene, `change_from_baseline_db` is that value minus the
   baseline scene's, and `valid_fraction` is valid pixels over ROI pixels.
8. **Unevaluable area.** Pixels whose original observation is missing — at a
   scene's valid-mask edge, inside a nodata hole, or beyond the snapped grid
   footprint — are excluded from the region set and from `valid_area_ha`, and
   are reported in `not_evaluable_area_ha`. They are never reconstructed, and
   they can never contribute a detected change.
9. **Ordering.** Regions are ordered by descending `priority_score`, then
   descending `area_ha`, then centroid latitude/longitude, and ids are assigned
   `R001`, `R002`, ... in that order. The order and ids are deterministic for a
   given input, so repeated runs and repeated bundles agree.

Failure to build a usable grid is an error, not an empty result: no footprint
overlap, an all-invalid grid, no pixel with valid support in both scenes, or a
grid larger than 40,000,000 cells all abort before anything is published.

## Outputs

`--out` receives, all on the reference grid unless stated:

| File | Content |
| --- | --- |
| `change.tif` | `float32` signed change in dB, nodata `NaN`, in the reference CRS |
| `mask.tif` | `float32` 0/1 threshold mask (1 = detected change) |
| `before.png` | pre-event backscatter, dB, resampled to WGS84 |
| `after.png` | post-event backscatter, dB, resampled to WGS84 |
| `change.png` | signed change, dB, resampled to WGS84, diverging red/blue around 0 |
| `regions.geojson` | WGS84 `FeatureCollection` of detected regions |
| `analysis.json` | shared result bundle contract v1 |

### Previews are really resampled

`before.png`, `after.png`, and `change.png` are produced by warping each raster
onto a **WGS84 pixel grid** and recording that grid's bounds in
`analysis.imagery.*.bounds` (identical for all three, and identical to
`analysis.bbox`; the staged bundle is rejected if they differ). A projected rectangular extent is never passed off as image
pixels in degrees. Nodata is fully transparent; before/after use a 2nd-98th
percentile dB stretch whose range is recorded in `limitations`; the change
preview is clipped at the 98th percentile of detected |dB| (never below the
threshold).

### Publishing is atomic

The bundle is built in a staging directory next to `--out`, checked for
completeness and internal consistency (every file present and non-empty,
`metrics.region_count` equal to the GeoJSON feature count, feature ids matching
`region_id`), and only then moved into place with `os.replace`, with
`analysis.json` written **last** as the completion sentinel. A failed run leaves
no bundle and no staging directory behind; consumers that require
`analysis.json` to exist therefore never observe a half-bundle.

## Shared result bundle contract v1

`analysis.json` (`schema_version: 1`):

```jsonc
{
  "schema_version": 1,
  "analysis_id": "chg-<sha256-16>",        // deterministic from scenes + parameters
  "title": "Radiometric change 2026-03-04 to 2026-09-19 (sigma0, C, S)",
  "bbox": [west, south, east, north],      // WGS84, the preview grid
  "scenes": [
    {
      "id": "...", "acquired_at": "2026-03-04T10:15:00Z", "polarization": "C",
      "beam_mode": "S", "orbit_direction": "DESCENDING", "relative_orbit": 12345,
      "product_type": "SLC", "source_collection": "Radarsat-2_Tropical_Forest_Products",
      "catalog_url": "https://..."
    }
    // exactly 2
  ],
  "method": {
    "quantity": "sigma0", "units": "dB", "change_definition": "10*log10(after/before)",
    "threshold_db": 2.0, "minimum_area_ha": 1.0, "speckle_filter": "3x3 ...",
    "registration": {"status": "passed", "residual_pixels": 0.8},
    "preprocessing": ["..."]
  },
  "metrics": {
    "region_count": 0, "total_changed_area_ha": 0.0, "analysis_area_ha": 0.0,
    "valid_area_ha": 0.0, "not_evaluable_area_ha": 0.0, "scene_count": 2
  },
  "imagery": {
    "before": {"path": "before.png", "bounds": [w, s, e, n], "label": "..."},
    "after":  {"path": "after.png",  "bounds": [w, s, e, n], "label": "..."},
    "change": {"path": "change.png", "bounds": [w, s, e, n], "label": "..."}
  },
  "demo_region_id": null,
  "limitations": ["..."]
}
```

`regions.geojson` is a WGS84 `FeatureCollection`; each feature carries `id` and:

| Property | Meaning |
| --- | --- |
| `region_id` | stable id, equal to the feature `id` |
| `area_ha` | geodesic area of the thresholded pixels, holes subtracted |
| `change_db` | **signed** median dB over the region |
| `magnitude_db` | median **absolute** dB over the region |
| `detected_at` | the later acquisition's timestamp — when the radar difference was **observed** |
| `baseline_at` | the baseline acquisition's timestamp |
| `observation_interval` | `{start: baseline_at, end: detected_at}` — the interval that was observed |
| `priority_score`, `priority_units`, `priority_formula` | `magnitude_db * sqrt(area_ha)`, `"dB sqrt(ha)"` |
| `persistence` | `{"status": "not_evaluable", "observations_after_detection": 0, "changed_observations": 0, "rate": null}` |
| `historical_anomaly` | `null` |
| `explanation` | plain-language summary, including that the cause is undetermined |
| `time_series` | one entry per scene: `acquired_at`, `mean_backscatter_db`, `change_from_baseline_db`, `valid_fraction` |

`detected_at` is the acquisition in which the radar backscatter difference was
**observed**, not the onset of any event. A change could have begun at any time
inside `observation_interval`, including long before `detected_at`; two dates
cannot bound it any tighter. The region `explanation` says so explicitly, and
`analysis.limitations` repeats it.

`change_db` (signed median dB) and `magnitude_db` (median **absolute** dB) are
two distinct metrics, both valid. They coincide only for single-sign regions: a
region containing both brightening and darkening pixels keeps its dominant sign
in `change_db` while `magnitude_db` stays at the median absolute value, and
`priority_score` is built from `magnitude_db`, never from `change_db`.

### Area metrics partition the analysis extent

```jsonc
"metrics": {
  "region_count": 1,
  "total_changed_area_ha": 12.61,
  "analysis_area_ha": 158.76,   // geodesic area of the common reference grid
  "valid_area_ha": 143.75,       // evaluable in both scenes after filtering
  "not_evaluable_area_ha": 15.13, // analysis_area_ha - valid_area_ha
  "scene_count": 2
}
```

The API enforces these invariants, so the bundle satisfies them by construction:

* `valid_area_ha + not_evaluable_area_ha == analysis_area_ha` (geodesic areas on
  the same WGS84 ellipsoid, computed on the same reference grid; only float
  noise is clamped);
* `total_changed_area_ha == sum(region.area_ha)` over the retained regions;
* `total_changed_area_ha <= valid_area_ha`, because regions are subsets of the
  evaluable area.

A violation is treated as a processing error rather than published, and the
same checks are re-run against the staged bundle before it is moved into place.

## Two dates: what is unavailable, and what must not be faked

With exactly two acquisitions there is no time series to speak of, so:

* `persistence.status` is always `"not_evaluable"`, with
  `observations_after_detection: 0`, `changed_observations: 0`, and
  `rate: null`. There is no post-detection observation.
* `historical_anomaly` is always `null`. There is no history to compare against.

These are **unavailable, not zero**. A `0` rate or a `0` anomaly would assert
that nothing changed after detection and that nothing unusual ever happened
before it, which the data cannot support. Likewise this slice emits no
probabilities, no confidence scores, and no causal labels: nothing here
distinguishes deforestation from flooding, fire, agriculture, or a processing
artifact, and no ground validation exists.

`demo_region_id` is `null` and stays `null` until an independent review of real
results nominates a region. It is never set from a synthetic or test result.

## Limitations carried in the bundle

Every run records its own `limitations` array, which always includes at least:

1. registration is asserted by the upstream diagnostic; this CLI aligns grids
   only and does not measure or correct misregistration, so coregistration
   residual error propagates into the change rasters;
2. the `verified` calibration/geocoding/terrain-correction flags are upstream
   attestations this CLI has not inspected;
3. regions are thresholded radiometric-change polygons, not classified
   forest-loss polygons, and the cause of change is undetermined with no ground
   validation;
4. with two acquisitions, persistence and historical anomaly are not evaluable;
5. `detected_at` is when the radar difference was observed, not when an event
   began; the event time, if any, is unknown inside `observation_interval`;
6. radiometric change can also arise from geometry, terrain, incidence angle,
   and processing effects, so magnitudes are not loss severity;
7. the before/after preview stretch range is not an absolute radiometric scale;
8. the minimum area filter uses mean geodesic cell area, so the retained set can
   differ marginally from an exact per-cell area filter;
9. pixels with a missing original observation are unevaluable rather than
   interpolated: nodata holes and valid-mask edges appear as gaps in the region
   polygons, so a region can be interrupted by unmeasured ground.

## Tests

```bash
python -m pytest tests/processing/test_change.py
```

All fixtures are synthetic and generated in `tmp_path` at test time. Coverage:
the known 2x power = 3.0103 dB patch and the exact 2.2185 dB kernel-diluted
ring; stable background producing no regions; nodata blocks, filter-edge
support, and preserved polygon holes with hole-subtracted area; an isolated
single-cell nodata hole and a 3x3 nodata block inside a changed patch, each
proving the missing original centre stays nodata in `change.tif` and leaves
`total_changed_area_ha`, `valid_area_ha` and `not_evaluable_area_ha` short by
exactly its own cells while the surrounding cells keep the full 3.0103 dB; a
nodata stripe along a scene's valid-mask edge, proving the post-warp remask
prevents bilinear interpolation from manufacturing observations at the edge and
that the outside-of-footprint ring of the snapped grid is never evaluable; the
unit regression for the kernel rule itself (a valid centre with nodata neighbours
is still defined, a missing centre never is); minimum area
filtering at several thresholds; rejection of unverified provenance, dB and
amplitude rasters, complex rasters, missing CRS, degenerate transforms,
incompatible pairs, wrong scene counts, duplicate dates, unsorted scenes, failed
registration, bad schema versions, wrong source collections, non-overlapping
footprints, and nonpositive/nonfinite parameters; finite-only numeric output;
real WGS84 preview bounds shared by all three PNGs and equal to `analysis.bbox`;
contract v1 field-by-field consistency including `baseline_at` /
`observation_interval` (and the absence of the pre-rename names), `not_evaluable`
persistence, `null` anomaly, `null` `demo_region_id`, absence of
confidence/cause fields, the `valid_area_ha + not_evaluable_area_ha ==
analysis_area_ha` partition with `total_changed_area_ha <= valid_area_ha`, signed
median versus median absolute dB as distinct metrics, and stable region
ids/order across runs; atomic publishing with nothing left behind on failure;
and the CLI's success, error, and argument handling.
