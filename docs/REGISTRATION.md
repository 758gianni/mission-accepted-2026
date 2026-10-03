# Residual coregistration diagnostic

`processing/registration.py` measures how far apart two **prepared, calibrated,
overlapping** rasters actually are, and writes one JSON diagnostic. It is a
measurement, not a correction.

```bash
python -m processing.registration \
    --reference data/prepared/2026-03-04/before.tif \
    --target   data/prepared/2026-09-19/after.tif \
    --out      data/processed/registration-diagnostic.json \
    --max-shift-pixels 1.0 \
    --max-patch-deviation-pixels 0.5
```

* `--reference`, `--target` — prepared rasters (linear power, `sigma0`/`gamma0`), any
  CRS rasterio can open.
* `--out` — path of the JSON diagnostic. Parent directories are created; the file is
  written atomically. **Nothing else is written, anywhere.**
* Every threshold has a CLI flag with the default recorded in `Criteria`. No threshold
  is read from a manifest and none is chosen on the caller's behalf. Invalid or
  unusable inputs exit `2` with a single-line `error: ...` on stderr, no traceback, and
  **no output file**.

Requirements, all already in the declared stack: `numpy`, `scipy`, `rasterio`.
Nothing new was added to `pyproject.toml` or any shared file.

## What this is not

* **Not a raw-product adapter.** It never opens a RADARSAT-2 product, never calibrates,
  geocodes or terrain-corrects, and never authenticates to EODMS.
* **Not an automatic warper.** It never corrects either input. Its own reprojection onto
  a common grid is a measurement convenience, not a fix.
* **Not a gate.** It never sets or infers `manifest.registration.status`, never writes a
  manifest, and never publishes an analysis bundle. `status` is a comparison of a
  measurement against the caller's own thresholds; nothing downstream should read it as
  a verification.
* **Not a source of probabilities.** No confidence, probability, accuracy, quality score
  or cause is emitted, for any field. Every number is a measured quantity (a shift, a
  peak height, a spread) or a threshold the caller supplied.

## Alignment of grids is not registration evidence

Reprojecting two rasters onto a common grid is *necessary* to compare them and is not
evidence that they are aligned. The status is computed only from the per-patch
phase-correlation measurements. Two rasters that already share transform, CRS and shape
are still measured patch by patch, and misregistered content is reported as exceeding the
tolerance — covered by
`test_shifted_content_on_an_identical_grid_does_not_pass_because_the_grid_matched`.

## Method

1. **Common grid.** If both rasters share a CRS and both are axis-aligned, the overlap
   is intersected in their own projected coordinates (`grid_path:
   "native_crs_intersection"`), which is exact. Otherwise the overlap comes from
   WGS84 densified bounding boxes, which over-estimate the footprint by a fraction of a
   pixel per side (`grid_path: "wgs84_densified_bounds"`). The grid uses the reference
   pixel size. Values are resampled bilinearly; the source validity mask is resampled
   nearest-neighbour and reapplied, so interpolation cannot invent an observation at a
   nodata pixel.
2. **Patch tiling.** `PATCH_SIZE = 32` px windows on a `PATCH_STRIDE = 16` px lattice,
   at most `MAX_CANDIDATES = 400` windows. A window whose jointly valid fraction is below
   `min_valid_fraction` is rejected as `insufficient_valid`.
3. **Scoring and selection.** Candidates are scored by the *smaller* of the two rasters'
   dispersions in the window — pooling the pair would let a purely radiometric
   difference look like structure. The most textured windows are kept, subject to a
   `MIN_PATCH_SEPARATION = 64` px minimum between centres, so the accepted patches cover
   the overlap instead of clustering in one busy corner. At most `MAX_PATCHES = 12` are
   correlated. Selection is deterministic (score descending, then row, then column).
4. **Per patch.** Both patches are standardised to zero mean and unit variance over
   their jointly valid samples, non-jointly-valid samples are set to exactly zero so they
   contribute nothing, the result is tapered with a separable Hann window, and the inverse
   real FFT of `conj(FFT(reference)) * FFT(target)` is taken with the patch zero-padded
   to twice its size. The lag of the correlation peak is the translation that maps the
   reference onto the target; a 3-point parabolic fit along each axis gives the
   sub-pixel value.
5. **Rejections**, in this order: `insufficient_valid`, `flat`, `ambiguous`,
   `low_peak`, `out_of_search_range`, `degenerate_correlation`. A patch is *ambiguous*
   when the correlation surface has another **local maximum** outside the winning peak's
   2 px neighbourhood that reaches `min_patch_dominance` of the winner. Rivals are local
   maxima rather than arbitrary surface values on purpose: a broad but single peak is an
   imprecise measurement, not an ambiguous one, and comparing against flank values would
   reject every such patch.
6. **Centre.** Componentwise median of the accepted per-patch shifts, plus the maximum
   and median distance of an accepted patch from it.

### Minimum footprint and patch layout

| Overlap (32 px patches) | Accepted patches | Default criterion |
| --- | --- | --- |
| 64 x 64 px | 1 | `not_evaluable` |
| 80 x 80 px | 2 | `not_evaluable` |
| **96 x 96 px** | **4** | usable |
| 128 x 128 px | 5 | usable |
| 192 x 192 px | 8 | usable |

**96 x 96 px is the minimum overlap** that can satisfy the default four-patch criterion, and
that is a floor rather than a working margin: two separated patch centres per direction
(`MIN_PATCH_SEPARATION = 2 x PATCH_SIZE = 64` px) leaves no room for texture variation. At
12.5 m sampling 96 px is a 1.2 km overlap; at 30 m it is 2.9 km. A wider overlap gives the
selection more freedom, so it can place patches away from the overlap edges — which matters
because patches placed on an edge carry the largest bias (see below). Anything narrower than
96 x 96 px needs a smaller `min_patches` and an explicit acknowledgement that a
two-patch measurement is weak.

### Measured bias: the shift is systematically too small

Across four synthetic scenes and shifts of 1-5 px, the fitted magnitude is an
**under**estimate of the injected shift by **3.3 % to 5.9 %, median 4.0 %**, and the
per-patch spread grows with the shift:

| Shift | Recovered | Error | Error % | Max per-patch spread |
| --- | --- | --- | --- | --- |
| 1.0 px | 0.96 px | 0.04 px | 3.3-5.8 % | 0.05 px |
| 3.0 px | 2.83 px | 0.11-0.18 px | 3.4-5.8 % | 0.13-0.16 px |
| 5.0 px | 4.79 px | 0.17-0.29 px | 3.4-5.9 % | 0.11-0.47 px |

At shifts approaching the search bound the effect is much larger than 6 %: a 6 px shift
measured on 32 px patches gave per-patch estimates spread over about 2.5 px, because patches
near the overlap edge carry content that the Hann taper and the finite window handle poorly.

**Tolerance-boundary implication.** The bias is one-sided: the measurement never runs large,
only small. A result that lands *just inside* `max_shift_pixels` may be a shift just outside
it — with the default 1.0 px tolerance the boundary is fuzzy by roughly 0.04 px, and with a
0.25 px tolerance by roughly 0.01 px. Consequences:

* Treat a magnitude within about 6 % of the tolerance as **unresolved**, not as passing.
* Do not tune `max_shift_pixels` to sit close to the observed misregistration of a pair; that
  inverts the sign of the error.
* `max_patch_deviation_pixels` must be loosened as the expected shift grows, or the run will
  report `inconsistent_local_shifts` for a pair that is merely badly aligned. The default
  0.5 px is appropriate only for shifts of roughly 1-3 px on 32 px patches.

Every figure here is from synthetic textures. No real prepared RADARSAT-2 pair was available,
so none of these numbers has been confirmed on real backscatter.

### No speckle filter, on measured evidence

A 3×3 box mean was tried and rejected. On the synthetic textured scenes used to develop
this module, sub-pixel accuracy degraded from about **0.15 px to 0.5–2 px** once a 3×3
mean was applied inside the patch (per patch and whole-scene variants alike), because the
filter's border behaviour dominates a 32 px window. Speckle suppression is therefore
left to the caller upstream, and the interaction of any such filter with this estimate
has **not** been validated here.

## Observed accuracy

See *Measured bias* above for the systematic underestimate and the minimum footprint. The
per-test expectations are `abs=0.35` px for integer and sub-pixel shifts, which the
measured 3.3-5.9 % bias satisfies up to a 6 px shift.

## The caller's criterion, and the statuses

All eight thresholds are echoed into the output under `criterion`.

| Field | Meaning |
| --- | --- |
| `max_shift_pixels` | largest accepted magnitude of the fitted shift |
| `max_patch_deviation_pixels` | largest accepted distance of any patch estimate from the centre |
| `min_patch_dominance` | smallest accepted best-peak / rival-peak ratio |
| `min_patch_peak` | smallest accepted normalized correlation peak (not a probability) |
| `min_texture_cv` | smallest accepted `std/|mean|` (not an image-quality score) |
| `min_valid_fraction` | smallest accepted fraction of a patch valid in both rasters |
| `min_patches` | smallest accepted number of usable patches |
| `max_shift_search_pixels` | largest shift a patch may report before it is `out_of_search_range`; must be ≤ 15 px, since beyond that the zero-padded surface wraps onto a spurious shift |

`min_patches` must not exceed `MAX_PATCHES` (12); the CLI refuses a larger value, because a
criterion that can never be met would silently turn every run into `not_evaluable`.

Statuses, decided in this order:

1. `not_evaluable` — the overlap is smaller than one patch, fewer than `min_patches`
   patches were usable, the accepted patches do not span at least 2 distinct centre rows and
   2 distinct centre columns, or **a selected patch reported a shift beyond the caller's
   search bound**. One textured corner cannot stand in for a global shift, and a shift that
   ran past the search bound has not been measured at all — the residual is then unknown,
   not small.

2. `inconsistent_local_shifts` — accepted patches disagree by more than
   `max_patch_deviation_pixels`. A single global translation does not describe these two
   rasters, so no single number is reported as the answer.
3. `exceeds_caller_tolerance` — the fitted shift is larger than `max_shift_pixels`.
4. `within_caller_tolerance` — the measurement is inside both limits. This is **not** a
   verification of registration.

Consistency is checked before magnitude on purpose: when the patches disagree, the
centre shift is not a meaningful number to compare against a tolerance.

`measured.usable_measurement` is `true` only for `within_caller_tolerance` with no patch
outside the search bound, and `usable_measurement_reason` states why when it is `false`.
Only a usable measurement produces a `manifest_registration_diagnostic_text` that quotes a
translation; otherwise the text says the residual is **UNKNOWN** and offers nothing for a
manifest field. A `not_evaluable` or `inconsistent_local_shifts` run never prints a median
translation that a reader could mistake for a validated registration.

`rotation_equivalent_rad`, `dilation_equivalent` and `shear_equivalent` are `null` when the
accepted patch centres are collinear or otherwise rank deficient (fewer than 3 non-collinear
centres): `lstsq` would return a least-norm solution that looks like a measurement but is
determined by nothing in the data.

## Metric shift

`shift_pixels.y` is positive for a displacement toward larger map y. The metric shift is
derived from the common grid's affine transform
(`x = a·dx + b·dy`, `y = d·dx + e·dy`, scaled by the CRS units-to-metres factor), not
from a north-up assumption, so a rotated or south-up grid is handled correctly. When the
CRS units do not convert to metres, `shift_metres` is `null`, `shift_metres_note`
explains why, and the pixel shift is still reported. Do not convert it with an assumed
pixel size.

`rotation_equivalent_rad`, `dilation_equivalent` and `shear_equivalent` describe a
least-squares plane through the per-patch shifts. They are **evidence** that the pair
differs by more than a translation. They are never applied.

## Output

```jsonc
{
  "schema_version": 1,
  "diagnostic": "residual_coregistration_diagnostic",
  "status": "within_caller_tolerance",
  "status_reasons": ["..."],
  "measured": {
    "shift_pixels": {"x": 2.849, "y": -1.908},
    "shift_magnitude_pixels": 3.429,
    "shift_metres": {"x": 85.47, "y": 57.24},
    "patches_selected": 7, "patches_accepted": 7,
    "max_patch_deviation_pixels": 0.31,
    "rotation_equivalent_rad": 0.002, "dilation_equivalent": 1.001, "shear_equivalent": 0.0
  },
  "patch_evidence": [ { "row": 48, "column": 96, "shift_pixels": {...},
                        "normalized_peak": 0.83, "rival_peak": null,
                        "peak_dominance": null, "valid_fraction": 1.0,
                        "texture_cv": 1.21, "accepted": true, "rejection_reason": null } ],
  "patch_rejections": {"insufficient_valid": 0, "flat": 0, "ambiguous": 0, "...": 0},
  "criterion": { "...": "the caller's thresholds, echoed" },
  "upstream": { "reference": {...}, "target": {...}, "common_grid": {...} },
  "georeference": { "transform": [...], "units_to_metres": 1.0 },
  "assertions": { "manifest_registration_status_set": false, "bundle_published": false,
                  "status_derived_from_grid_reprojection": false, "...": false },
  "limitations": ["..."],
  "manifest_registration_diagnostic_text": "..."
}
```

`peak_dominance` and `rival_peak` are `null` when the winning peak is the only local
maximum — an unambiguous patch, not a missing measurement.

`manifest_registration_diagnostic_text` is one sentence a human may *consider* for the
`manifest.registration.diagnostic` field required by `processing/change.py`. It is a
candidate string inside this diagnostic. This tool does not write it anywhere, and
deciding whether to accept it stays a manual, out-of-band step. The text states plainly
that nothing was verified or corrected.

## Limitations

The full list is in the module and is copied into every output under `limitations`. The
ones that decide whether a result may be trusted:

* **Translation only.** Rotation, scale, shear, terrain-driven relief displacement and
  any non-translational geolocation difference are neither corrected nor separable from a
  translation here. When present, the patches disagree and the status is
  `inconsistent_local_shifts`.
* **Dominant structure only.** The reported shift is the displacement of whatever
  structure dominates each patch. It does not identify what moved.
* **Speckle is not filtered** and cannot be distinguished from structure by
  `texture_cv`. Peak dominance and inter-patch agreement are defences, not guarantees: a
  speckle-driven peak that agreed across several patches would not be caught.
* **Of the rasters as delivered.** Any upstream resampling, warping, mosaicking or
  projection change is inside the measured shift and cannot be attributed to either date.
* **Interpolation when the grids differ.** Bilinear resampling onto the common grid
  smooths high-frequency content and can attenuate a real structural shift. The reported
  shift belongs to the common grid, and the common grid is the intersection of both
  footprints at the reference pixel size — if the two rasters have different pixel sizes,
  a shift measured on it is not directly comparable to one measured on a source grid.
* **`normalized_peak` is a correlation coefficient**, not a probability, a confidence or
  an accuracy.
* **Geometry only.** Two acquisitions say nothing about *why* the radar differs. No
  cause, probability or confidence is reported, and none can be derived here.
* **Calibration, geocoding and terrain correction remain upstream attestations.** This
  tool does not inspect the processing that produced the rasters it measures.

## Clarifications

* **Zero mean.** The diagnostic reports a translation only. It does not remove, correct or
  even characterise any radiometric offset between the two dates: patches are standardised to
  zero mean solely so the correlation is invariant to a difference in brightness, and a
  genuine radiometric change is invisible to it by construction.
* **Exit codes.** `0` means the diagnostic was written and the measurement ran; it does not
  mean the pair is aligned, and it does not mean anything was verified. Read `status` and
  `measured.usable_measurement` from the file. `2` means no output file was written.
* **Patch shape.** `PATCH_SIZE` is 32, but `estimate_patch_shift` accepts any patch size and
  the reported lag is read against the padded FFT geometry, not against `size/2`; a regression
  test covers the sizes whose fast FFT length is not `2 * size` (19, 23, 41, 47).

## Tests

```bash
PYTHONPATH=. python -m pytest tests/processing/test_registration.py
```

62 tests, ~5 s. Every fixture is synthetic and generated inside the test module. There
are **no real prepared rasters in this repository**, and these textures are synthetic
patterns, not backscatter; they must never be published as results.

Covered: the padded-FFT lag regression across seven patch sizes; `min_patches` bounded by
the patch budget; withholding of the candidate manifest sentence unless the measurement is
usable; gating when a patch falls outside the search bound; collinear centres publishing no
rotation; the minimum-overlap boundary at 96 px; known integer and sub-pixel shifts; a zero shift; a purely radiometric
difference not inventing a shift; metric conversion, and its refusal for non-metric CRS;
nodata borders, holes and sparse valid support; a pair with no shared valid support; a
homogeneous pair; speckle-like noise with no structure; a 4 px periodic pattern; patches
being spread out and independent; inconsistent local shifts; and the CLI (writes exactly
one file, refuses non-overlapping or missing rasters without a traceback, handles
differing source grids, and runs as a subprocess).

Note: this branch has no `pyproject.toml`, so `PYTHONPATH=.` is required. The
`[tool.pytest.ini_options] pythonpath = ["."]` setting lives in the integration setup
branch (`237d45e`), which this branch must not merge.