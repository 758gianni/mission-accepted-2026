# Integration checks: produced bundle -> HTTP API

Final integration checklist and observed mismatches for the vertical slice
`processing.change` (bundle producer) -> `backend.app` (read-only API).

Owned by: `tests/integration/test_produced_bundle_api.py`, `docs/INTEGRATION-CHECKS.md`.
Not owned here: `backend/`, `processing/`, `pyproject.toml`, `uv.lock`, `frontend/`.

## What the tests do

Every response asserted in the suite comes from a bundle that the **real
producer** generated from **generated** prepared rasters, served by the **real
FastAPI app** over `TestClient`. No bundle is hand-written, and no fixture is
published outside pytest `tmp_path` directories.

The suite skips explicitly (module-level `pytest.skip`, not a silent pass) when
`processing.change` or `backend.app` is absent, so it is honest before the lead
merges the implementations:

```
SKIPPED [1] tests/integration/test_produced_bundle_api.py:70: backend.app (API) is not
present in this checkout (No module named 'backend'). ...
```

Run it from the repository root with the shared integration environment:

```
python -m pytest tests/integration -q
```

## Checklist covered (26 tests)

| Area | What is asserted |
| --- | --- |
| Lifecycle | `awaiting_analysis` + `404` on data endpoints before a run; `ready` after the producer publishes into the watched directory, **without restarting the app**; `/health` stays `ok` throughout |
| Changed pair | served `/api/analysis` equals the produced `analysis.json` plus the three relative preview URLs, nothing else added; `/api/regions` equals the produced `regions.geojson`; every region addressable at `/api/regions/{id}` and byte-identical to the collection entry; unknown id `404`; geometry is the generated patch, valid, in WGS84, with the signed dB value of a 2x power ratio |
| No-change pair | `region_count == 0`, `total_changed_area_ha == 0.0`, empty `FeatureCollection`, `/api/regions/R001` -> `404`, previews still served |
| Missing / nodata | `not_evaluable_area_ha > 0`; the reported hectares match the share of cells the published `change.tif` leaves without an observation; the missing interior block is really missing; `mask.tif` declares real nodata metadata and marks those cells |
| Hole handling | an unchanged block inside the changed patch survives as a polygon interior ring; `area_ha` subtracts it (12.6101 ha outer ring vs 11.1689 ha reported) and equals the geodesic area of the shipped geometry |
| Tiny-region filter | `min_area_ha = 0` serves both generated patches (12.6101 ha + 0.4504 ha); `min_area_ha = 0.5` serves only the large one; the `analysis_id` records the parameter |
| Previews | served bytes are the produced PNGs byte for byte, `image/png`, real RGBA images >1 px, `url` fields correct, unknown imagery key `404`; all three PNGs share one grid size; `imagery.*.bounds == analysis.bbox` exactly and matches the published `change.tif` extent in WGS84; before/after declare one shared pooled stretch |
| Corruption / repair | invalid `analysis.json`, `region_count` mismatch, `total_changed_area_ha` that does not reconcile, a non-PNG preview, a half-published bundle (missing `regions.geojson`) and a symlinked bundle directory all become `error` with `503` on data endpoints, then `ready` again after repair, no restart |
| UTC dates | served timestamps are `Z`-suffixed UTC; a `+00:00` manifest input is normalised (not echoed); the two acquisitions are distinct UTC dates and strictly ascending; `baseline_at` < `detected_at`, `observation_interval` brackets them, `time_series` is ascending; a `+01:00` acquisition is refused by the producer and nothing is published |
| Unavailable stays unavailable | `persistence.status == "not_evaluable"`, `rate` serialised as `null` (never `0`), `observations_after_detection == 0`, `historical_anomaly` `null` |
| No invented content | no `confidence`/`probability`/`likelihood`/`deforestation`/`forest_loss`/`cause`/`severity_score`/`risk` key anywhere in the served documents; every served explanation disclaims causation; no "confidence"/"confirmed deforestation" phrasing |
| Areas in hectares | `total_changed_area_ha == sum(served region area_ha)`, `valid + not_evaluable == analysis_area`, changed `<= valid`, `scene_count == len(scenes) == 2`, each `area_ha == geodesic area of its own geometry` - for all five scenarios |
| Read-only surface | OpenAPI exposes no verb other than GET/HEAD/OPTIONS; POST/PUT/DELETE rejected; serving the bundle does not mutate a single file in it |

## Observed mismatch (blocks the slice)

**One defect, found against the published reference heads.** Reference
composition used (scratch only, never committed): integration setup
`237d45ea506dc17d3b348f93b7cc13dfa5801ff3`, API
`87cfacb58d5c4896389eb80749e6222f687db4cb`, producer
`b8d9059dc23efaf122ee85bd4403bcb1df350ce1`, environment from the integration
`pyproject.toml` / `uv.lock`.

Each author's own suite passes in that composition (`tests/backend` +
`tests/processing`: **186 passed**), yet the end-to-end suite fails **24 of 26**
with one shared root cause:

```
relative_orbit: producer emits a JSON float, API requires an int
```

* `processing/change.py` casts the manifest value with `float(relative_orbit)`
  when it writes `analysis.scenes[*].relative_orbit`, so a manifest with
  `relative_orbit: 12345` yields `12345.0`.
* `backend/validation.py` validates that field with `_integer(...)`
  (`isinstance(value, int)`, `bool` excluded), so `12345.0` is rejected.
* Observed: `BundleValidationError: analysis.scenes[0].relative_orbit: expected
  an integer`, the store reports `state == "error"`, and `/api/analysis`,
  `/api/regions`, `/api/regions/{id}` and `/api/imagery/{key}` all answer `503`
  for an otherwise valid bundle.

Reproduction (one line, after the merge):

```
python -m pytest tests/integration -k changed_pair_analysis_is_served_verbatim
```

Impact: **any** bundle produced from a manifest that carries a relative orbit -
the normal RADARSAT-2 case - is unservable. The API is correct to refuse a
contract violation, and the producer is correct to report the manifest value it
was given; the two sides disagree about the type of an integer-valued field.

Owner options (not applied here, both are outside this package):

1. Producer: emit `int(relative_orbit)` when the manifest value is integral
   (keep `float` only for genuinely fractional orbits).
2. API: accept an integral float in `_integer` (or normalise it) with a
   documented tolerance, mirroring the existing `AREA_TOLERANCE_HA` approach.

Evidence that the rest of the suite is sound: with `relative_orbit: null` in the
fixture manifest (scratch-only substitution, **not** applied to the committed
test file) all **26** integration tests pass against the same two heads, and the
two tests that pass even with the defect are the ones that assert the *negative*
states (`state == "error"` for a symlinked bundle directory, and a half-published
bundle being refused).

## Observed behaviour worth knowing (not defects)

* **The reference grid is snapped outward to whole pixels**, so a 40x40-cell
  source pair yields a 42x42 analysis grid (1764 cells) whose outer one-cell
  ring has no source support. For a *fully covered* pair this band is reported
  as not evaluable: 168 invalid cells, `not_evaluable_area_ha = 15.1321` of
  `analysis_area_ha = 158.8871` (9.524% vs 9.524% of cells). Consequence for the
  frontend: `not_evaluable_area_ha` is never `0.0` for a complete pair, and the
  previews are legitimately transparent along that border. The test asserts
  precisely this (no *interior* cell unevaluable, reported share == raster
  share), so a regression that silently drops interior data still fails.
* **Status messages carry a sanitised label only** (for example
  `... contract, so no values are served. (bundle)`). The tests assert the
  label, never the detail, and assert that no filesystem path leaks.
* **JSON is serialised compactly** (`"rate":null`, no space), so the raw-body
  checks normalise whitespace before matching.

## Limitations of these checks

* Everything is synthetic and small (40x40 cells, UTM 33N, 30 m). No real
  RADARSAT-2 product is available locally yet, so nothing here exercises real
  radiometry, terrain correction, incidence-angle behaviour or a real
  multi-scene series.
* Only a two-date pair is covered. Persistence and historical anomaly are
  asserted to be *unavailable*; no temporal claim is tested, because none can be
  supported yet.
* Registration is a manifest attestation in these fixtures, not a measurement.
* The suite asserts the published heads. If the API authors change wording
  (`"pooled stretch"` in preview labels, the causal disclaimer phrases), those
  assertions must be revisited deliberately rather than silently relaxed.