# Product Inventory (`processing/inventory.py`)

Local, **credential-free** reconnaissance of a RADARSAT-2 dataset that has already been
placed on this machine. The tool answers one question: *what is actually in this data
directory?* It reads product metadata and raster headers only.

```bash
python -m processing.inventory data/raw --out data/reports/inventory.json
```

| Flag | Meaning |
| --- | --- |
| `data_dir` | Directory to inspect. **Read-only**: nothing inside it is created, modified or extracted. |
| `--out PATH` | Write the JSON report to `PATH` (atomic temp-file + `os.replace`). Parent directories are created. Omit to print to stdout. |
| `--indent N` | JSON indentation (default `2`). |
| `--quiet` | Suppress the stdout echo when `--out` is used. |
| `--version` | Print the tool version. |

Exit codes: `0` success (including "0 scenes found"), `2` the given path is missing or is
not a directory (message on stderr).

## What it does

1. Walks `data_dir` (never following symlinks out of the tree) looking for
   `product.xml`, `*.zip` archives, and `*.tif`/`*.tiff` rasters.
2. Parses `product.xml` **namespace-independently**: elements are indexed by their
   local name (`{http://…}productType` == `productType`), so prefixed, default-namespaced
   and no-namespace documents all produce the same result.
   Recognised names cover the GDAL **RS2** driver schema (`frmts/rs2/rs2dataset.cpp`:
   `product.sourceAttributes.{satellite,sensor,beamModeMnemonic,rawDataStartTime}`,
   `product.imageGenerationParameters.generalProcessingInformation.productType`,
   `product.imageAttributes.rasterAttributes.{dataType,bitsPerSample,
   numberOfSamplesPerLine,numberOfLines,sampledPixelSpacing,sampledLineSpacing}`,
   `geographicInformation.geolocationGrid.imageTiePoint`,
   `imageAttributes.lookupTable[@incidenceAngleCorrection]` whose *text* is the LUT
   filename, and `imageAttributes.fullResolutionImageData[@pole]`, which is where RS2
   polarizations live) plus the Safe/`productIdentifier`/`imageTiePoints` style names.
   Unknown names are reported as missing rather than guessed.
3. Derives per-scene: scene ID, acquisition start/end/duration, polarizations,
   product type, sample type, bits per sample, beam mode, sensor mode, orbit
   number/range/type/direction, calibration-LUT / noise / orbit-file presence,
   geolocation tiepoint and GCP counts, and product footprint SRS.
4. Opens raster **headers** (never pixels) for CRS, EPSG, CRS units, affine
   transform, shape, band count, dtype, nodata and native pixel spacing. Rasters
   inside archives are read through the GDAL virtual filesystem
   (`zip://…!/member`, falling back to `/vsizip/…!member`) — no extraction.
5. Emits scenes plus `missing_data`, `errors`, `warnings`, and a one-line `summary`.
   **Zero scenes is reported explicitly** (`scene_count: 0`, `zero_scenes: true`, and a
   summary that says so) rather than as an empty success.

## Safety and scope guarantees

* **No credentials.** No `.env`, `.aws`, `.codex`, `.eodms`, `credentials`, `*secret*`,
  `*token*`, `*.pem`, `*.key` … path is ever opened. Skips are recorded in `warnings`.
  No EODMS (or other) authentication and no network access is attempted;
  `credentials_used` and `network_used` are hard-coded `false` in the report.
* **No writes to the data directory.** Archives are inspected with `zipfile` and never
  extracted. The only file written is `--out`, and only after the walk has finished.
* **No path traversal.** Zip entries are rejected when absolute, drive-lettered,
  containing `..` (POSIX or Windows style), hidden, or credential-like.
* **Resource limits** (`limits` in the report): 32 MiB per XML, 50 000 archive members,
  8 GiB per archive.

## Rasterio is optional

`rasterio` is an optional dependency. If it is absent the scan still completes; every
raster record carries an explanatory `error`, the report contains

```json
"rasterio": {"available": false, "version": null}
```

plus a warning, and the corresponding fields land in `missing_data`. **A CRS is never
invented**: a raster with no CRS reports `crs: null` / `epsg: null` and is listed as
missing (`raster[<name>].crs`).

Tie points: rasterio does not expose GDAL tie points, so `raster.tiepoint_count` is
`null` with `tiepoint_source` explaining why. GCPs are read from the raster header.
Tiepoint counts parsed from `product.xml` are reported per scene under
`scene.geolocation.tiepoint_count`.

### Georeferencing honesty (no placeholder identity transforms)

GDAL returns an **identity matrix** when a raster has no affine georeferencing, which
naively reads as a real 1x1 pixel spacing. The tool therefore reports
`raster.georeferencing` and only publishes a transform it can justify:

| `georeferencing.source` | When | `transform` / `pixel_spacing` |
| --- | --- | --- |
| `none` | GDAL emitted `NotGeoreferencedWarning` ("the identity matrix will be returned"), **or** identity with no CRS, no GCPs and no RPCs | `null` |
| `gcp` | identity while ground control points define the georeferencing | `null`, `gcps` kept |
| `rpc` | identity while rational polynomial coefficients define the georeferencing | `null` |
| `affine` | GDAL reported a transform with none of the above signals — **including a legitimately declared identity geotransform** | reported, with `identity_transform: true` when it is identity |

A null transform is listed in `missing_data` as `raster[<name>].transform`,
`raster[<name>].pixel_spacing` and `raster[<name>].georeferencing`. Real GCPs are
always preserved and reported per raster.

### Pixel spacing for rotated and sheared rasters

For `Affine(a, b, c, d, e, f)`, `x = a*col + b*row + c` and `y = d*col + e*row + f`, so
one pixel column advances by `(a, d)` and one pixel row by `(b, e)`:

```jsonc
"pixel_step_vectors": {"column": [a, d], "row": [b, e]},
"pixel_spacing": {"column": hypot(a, d), "row": hypot(b, e), "x": …column…, "y": …row…},
"rotation_degrees": atan2(d, a) in degrees
```

`x` is the step along one pixel **column** and `y` the step along one pixel **row**.
Hypotenuses are required: for a 30-degree rotation the true spacing is larger than the
matrix diagonal, and `abs()` of the diagonals is wrong.

### Strict JSON and nonfinite values

JSON has no `NaN`/`Infinity` literals, so every report is passed through `json_safe()`
(nonfinite floats become `null`) and serialised with `allow_nan=False`; a regression
raises instead of writing invalid JSON. Nonfinite nodata is encoded explicitly:

| `nodata` | `nodata_kind` |
| --- | --- |
| `-9999.0` | `value` |
| `null` | `nan` |
| `null` | `posinf` |
| `null` | `neginf` |
| `null` | `none` (no nodata declared) |

## Footprint caveat

A footprint SRS in `product.xml` (commonly `EPSG:4326`) describes the **product
footprint in metadata only**. It does **not** imply that the raster inside the product is
georeferenced, rectified or resampled. When a footprint declares a geographic CRS the
report sets `scene.footprint.is_geographic` and emits a warning carrying the caveat.
Check `scene.rasters[].crs` for actual raster georeferencing.

## Report shape (abridged)

```jsonc
{
  "schema_version": "1.0",
  "tool": "processing.inventory",
  "data_dir": "data/raw",
  "credentials_used": false, "network_used": false, "archives_extracted": false,
  "rasterio": {"available": true, "version": "1.5.2"},
  "scene_count": 2,
  "zero_scenes": false,
  "scenes": [{
    "scene_id": "RS2_…",
    "source": {"container": "zip|directory", "path": "…", "product_xml": "…"},
    "product_type": "SLC", "processing_level": "L1", "sample_type": "COMPLEX_IQ",
    "polarizations": ["HH", "HV"], "beam_mode": "UW3", "mode": "IW",
    "acquisition": {"start": "…Z", "end": "…Z", "duration_seconds": 6.0},
    "orbits": {"number": 77777, "type": "POE", "direction": "descending"},
    "calibration": {"lut_present": true, "lut_references": […],
                    "noise_present": true, "noise_references": […],
                    "orbit_files_present": false, "orbit_references": […]},
    "geolocation": {"tiepoint_count": 2, "gcp_count": 0,
                    "has_tiepoints": true, "has_gcps": false},
    "footprint": {"srs_name": "EPSG:4326", "is_geographic": true, "caveat": "…"},
    "sampled_pixel_spacing": 12.5, "sampled_line_spacing": 12.5,
    "raster_attributes": {"number_of_samples_per_line": …, "number_of_lines": …},
    "radar_geometry": {"incidence_angle_near_range": …, "incidence_angle_far_range": …,
                       "slant_range_near_edge": …, "ascending_pass_time": "…"},
    "rasters": [{"name": "IMAGEDATA/imagery_b1.tif", "inside_archive": true,
                 "epsg": 32633, "crs": "…", "crs_units": "metre",
                 "transform": [c, a, b, f, d, e], "transform_order": "gdal (c, a, b, f, d, e)",
                 "pixel_step_vectors": {"column": [12.5, 0.0], "row": [0.0, -12.5]},
                 "pixel_spacing": {"column": 12.5, "row": 12.5, "x": 12.5, "y": 12.5},
                 "rotation_degrees": 0.0,
                 "georeferencing": {"source": "affine", "affine": true, "gcp_count": 0,
                                    "rpc": false, "identity_transform": false,
                                    "gdal_placeholder_warning": false, "note": "…"},
                 "shape": {"width": 64, "height": 64}, "count": 2,
                 "dtypes": ["float32", "float32"], "nodata": -9999.0, "nodata_kind": "value",
                 "gcp_count": 0, "gcps": [], "error": null}],
    "missing": ["calibration.orbit_files", "geolocation.gcps"],
    "preprocessing": {"recommended": "…", "rationale": […], "unknowns": […],
                      "not_performed": […], "confidence": "…", "footprint_caveat": "…"}
  }],
  "orphan_rasters": [],
  "missing_data": [{"scene_id": "…", "path": "…", "field": "calibration.orbit_files"}],
  "errors": [{"stage": "xml", "path": "…", "message": "XML parse failed: …"}],
  "warnings": ["skipped file …/.env: environment/credential file", "…"],
  "summary": "2 RADARSAT-2 scene(s); …"
}
```

Transform values use GDAL/GeoTIFF ordering `(c, a, b, f, d, e)`.

## Minimum preprocessing decision (metadata only)

`scene.preprocessing` is a *conditional* recommendation derived from what the metadata
actually shows, never a guarantee. It always carries `rationale`, `unknowns` (never
empty) and `not_performed`.

| Observed | Decision | Always-unknown items |
| --- | --- | --- |
| `productType` contains SLC / SCN / SCC / COMPLEX | Verify thermal-noise removal and calibration LUTs before radiometric calibration; interpolate precise orbits (POE vs ODE not verified). Multilooking/speckle filtering stays a separate, later decision. | DEM source undecided; orbit class unknown if `orbitType` absent; phase handling unverified if sample type is complex but product type is not SLC. |
| `productType` contains GRD / SGD / GROUND / GEO / IMAGE | Speckle filtering for radiometric texture, then terrain correction once the DEM is chosen — both still need validation on a real sample. | DEM source undecided; raster CRS unknown when absent; geolocation quality unknown without tiepoints/GCPs. |
| `productType` absent or unrecognised | **No decision**: collect metadata first; preprocessing cannot be chosen from current evidence. | As above plus the missing product type / processing level. |

Multi-polarisation acquisitions get a rationale note that polarimetric analysis is
possible in principle (not performed); single-pol acquisitions note that polarimetric
decomposition does not apply.

## Not in scope for this step

No radiometric calibration, no speckle filtering, no terrain/geometric correction and
no change detection. No pixels are read for analysis. No DEM is selected and no
preprocessing output is written.

## Tests

```bash
python -m pytest tests/processing/test_inventory.py -q
```

Coverage includes: the **official GDAL RS2 autotest `product.xml`** (vendored verbatim
in the test module from `https://github.com/OSGeo/gdal/blob/master/autotest/gdrivers/data/rs2/product.xml`,
blob sha256 `892b1ea2bfdd12e46549e336dad6f08f5ced1ca6393c26daa3196084afe6028a`
(fetched 2026-10-02, asserted by a test so drift is detectable); that file states it is
"completely artificially (and certainly not
spec complying) RS2 product.xml", i.e. GDAL's synthetic driver test data, never a real
acquisition — its `PRODUCT_TYPE` placeholder deliberately produces a "No decision"
preprocessing recommendation), namespaced / prefixed / namespace-free XML, absent metadata,
malformed XML, corrupt zip, zip path traversal (POSIX, Windows and absolute) with a
no-files-written assertion, rasters generated on the fly (CRS, transform, shape, dtype,
nodata, pixel spacing, GCPs), GDAL placeholder identity transforms, declared identity
geotransforms, rotated and sheared spacing, NaN/±inf nodata with strict JSON output,
rasters inside zips, graceful `rasterio` absence,
credential-like path skipping, symlink escape, zero-scene reporting, footprint caveat,
preprocessing decisions, and the CLI (`--out`, stdout, exit code 2).

Tests that need real rasters are skipped when `rasterio` is unavailable
(`test_rasterio_absence_is_graceful_and_noted` covers the degraded path).