# Integration checks: produced bundle -> HTTP API

Final integration checklist and observed mismatches for the vertical slice
`processing.change` (bundle producer) -> `backend.app` (read-only API).

Owned by: `tests/integration/test_produced_bundle_api.py`, `docs/INTEGRATION-CHECKS.md`.
Not owned here: `backend/`, `processing/`, `pyproject.toml`, `uv.lock`, `frontend/`.

## Exact composition these results come from

Executed in a scratch checkout (`/tmp/opencode/latest`), never merged into the test
branch and never committed as source:

| component | head |
| --- | --- |
| producer | `8027d7bd2b84f553a9d904f6473c7f8db5ae8d5d` (bundle `/tmp/producer-8027d7bd.bundle`) |
| API | `b88dd5b7218575280e83b118d0a260661f61bbbd` (bundle `/tmp/api-b88-full.bundle`) |
| integration setup (`pyproject.toml`, `uv.lock`) | `237d45ea506dc17d3b348f93b7cc13dfa5801ff3` (bundle `/tmp/setup-237-full.bundle`) |
| base required by all three bundles | `3262dcb8d9fb79b1d35e2fc94cbba13557e41d33` |

Environment: `uv sync --frozen` from the integration lock; CPython 3.12.3,
rasterio 1.5.2, numpy 2.5.3, scipy 1.18.1, shapely 2.1.2, pyproj 3.8.0,
pillow 12.3.0, fastapi 0.142.2, pydantic 2.13.5, pytest 9.1.1.

Results with these exact heads, no module skipped:

```
pytest tests/integration -q   ->  30 passed
pytest tests/backend    -q    -> 308 passed
pytest tests/processing -q    ->  63 passed
pytest                  -q    -> 401 passed
```

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
| Previews | the API **declares** its own preview URLs and the tests consume them: each must be same-origin (no scheme/netloc), address exactly `/api/imagery/{key}` for its own key, declare `path == "<key>.png"`, and carry exactly `analysis_id=<the served analysis_id>`; served bytes are the produced PNGs byte for byte, `image/png`, real RGBA images >1 px, bounds equal `analysis.bbox`; all three PNGs share one grid size; unknown imagery key `404`; before/after declare one shared pooled stretch |
| Corruption / repair | invalid `analysis.json`, `region_count` mismatch, `total_changed_area_ha` that does not reconcile, a non-PNG preview, a half-published bundle (missing `regions.geojson`) and a symlinked bundle directory all become `error` with `503` on data endpoints, then `ready` again after repair, no restart |
| Generation safety | a second producer run through the same pointer publishes a new generation (new `analysis_id`, new declared URLs), the **superseded** URLs answer `409 Conflict` and never return the new bytes, the current URLs return exactly the current bytes, and the superseded generation directory is retained (no auto-prune) |
| Immutability / root loss | republishing into an existing non-empty generation directory is refused with `ChangeError` and leaves every byte untouched; once the pinned generation directory is renamed away, `/api/status` reports `error` and the pinned preview URLs never return image bytes |
| Containment | absolute (`/etc/passwd`), escaping (`../outside.png`), nested (`previews/before.png`) and directory-like (`before.png/`) declared paths are refused with `error` + `503`; a symlinked preview inside the bundle is refused; every status message is checked for path sanitisation (no bundle path, no outside path, no temp root) without asserting the exact wording |
| UTC dates | served timestamps are `Z`-suffixed UTC; a `+00:00` manifest input is normalised (not echoed); the two acquisitions are distinct UTC dates and strictly ascending; `baseline_at` < `detected_at`, `observation_interval` brackets them, `time_series` is ascending; a `+01:00` acquisition is refused by the producer and nothing is published |
| Unavailable stays unavailable | `persistence.status == "not_evaluable"`, `rate` serialised as `null` (never `0`), `observations_after_detection == 0`, `historical_anomaly` `null` |
| No invented content | no `confidence`/`probability`/`likelihood`/`deforestation`/`forest_loss`/`cause`/`severity_score`/`risk` key anywhere in the served documents; every served explanation disclaims causation; no "confidence"/"confirmed deforestation" phrasing |
| Areas in hectares | `total_changed_area_ha == sum(served region area_ha)`, `valid + not_evaluable == analysis_area`, changed `<= valid`, `scene_count == len(scenes) == 2`, each `area_ha == geodesic area of its own geometry` - for all five scenarios |
| Read-only surface | OpenAPI exposes no verb other than GET/HEAD/OPTIONS; POST/PUT/DELETE rejected; serving the bundle does not mutate a single file in it |

## Resolved mismatch (was: blocked the slice)

`relative_orbit` was written by the producer as a JSON float and validated by the
API as an int, so every produced bundle was refused. This is reported fixed in the
current producer/API heads; the tests below still use `relative_orbit: 12345` in the
fixture manifest so a regression is caught.

## Observed mismatch at the published reference heads

**One defect, found against the published reference heads.** Reference
composition used (scratch only, never committed): integration setup
`237d45ea506dc17d3b348f93b7cc13dfa5801ff3`, API
`87cfacb58d5c4896389eb80749e6222f687db4cb`, producer
`b8d9059dc23efaf122ee85bd4403bcb1df350ce1`, environment from the integration
`pyproject.toml` / `uv.lock`.

**Status: this is historical.** The lead verified the composite producer
`50f4b48379700c85a10f18f78db397ccf98bacac` + API
`b109bfc739642140bad2db297a3332fd61445a93` with this suite: 378 passed, 3 failed,
7.70 s, and the real bundle now reaches `/api/analysis` and `/api/regions`. The
three failures were contract evolution, not product defects, and this file has
been updated accordingly (see the two sections below). The following was observed
against the earlier pair and is kept only to document what changed:

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

## Contract evolution this suite now enforces (was the 3 failures)

1. **Generation-scoped preview URLs.** The API no longer hardcodes
   `/api/imagery/{key}`; it declares a URL carrying the current
   `analysis_id` (`?analysis_id=chg-*`) so a client cannot be handed a preview
   from a superseded run. The tests now consume the *declared* URL and assert its
   shape (same origin, own key path, `<key>.png` name, exactly one
   `analysis_id` query parameter equal to the served `analysis_id`) instead of
   comparing against a hardcoded string.
2. **Stale generation is refused.** After a second producer run into the same
   bundle directory, the previous URLs must answer `409 Conflict` and must never
   return the new bytes; the current URLs must return exactly the current bytes.
   The reverse interleaving is covered too: previews written before their
   `analysis.json` must not be served under the old analysis id.
3. **Sanitised status messages.** The message is now a relative contract pointer.
   The tests assert the invariant instead of the wording: the message is non-empty
   and leaks no filesystem path (bundle dir, symlink target, temp root, or the
   rejected path candidate itself). Exact wording is deliberately not asserted.

### Closed by the latest heads

The per-generation root is now implemented on both sides, so the earlier gap is
closed rather than worked around: the producer publishes a fresh immutable
generation per run (refusing a non-empty plain directory) and the API resolves
the configured root through the producer's pointer, pins the generation for the
snapshot, and answers `409` for a superseded `analysis_id` and `503` when the
pinned root is gone. `test_published_generation_is_immutable_and_root_loss_is_not_served`
now asserts exactly that, and no test mutates a published generation in place.

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

* **No real SAR data is exercised.** Everything is synthetic and small (40x40
  cells, UTM 33N, 30 m). No real RADARSAT-2 product is available locally, so
  real radiometry, terrain correction, incidence-angle behaviour, real
  speckle statistics and a real multi-scene series remain untested end to end.
* Registration is a manifest attestation in these fixtures, never a measurement.
* The suite pins the heads listed above. If the producer's generation directory
  naming or the pointer rules change again, `_publish_into` and the root-pointer
  fixtures must be revisited deliberately.
* Only a two-date pair is covered. Persistence and historical anomaly are
  asserted to be *unavailable*; no temporal claim is tested, because none can be
  supported yet.
* The suite asserts the published heads. If the API authors change wording
  (`"pooled stretch"` in preview labels, the causal disclaimer phrases), those
  assertions must be revisited deliberately rather than silently relaxed.