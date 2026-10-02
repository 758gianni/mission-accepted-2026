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
    "rasters": [{"name": "IMAGEDATA/imagery_b1.tif", "inside_archive": true,
                 "epsg": 32633, "crs": "…", "crs_units": "metre",
                 "transform": [c, a, b, f, d, e], "transform_order": "gdal (c, a, b, f, d, e)",
                 "pixel_spacing": {"x": 12.5, "y": 12.5},
                 "shape": {"width": 64, "height": 64}, "count": 2,
                 "dtypes": ["float32", "float32"], "nodata": -9999.0,
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

Coverage includes: namespaced / prefixed / namespace-free XML, absent metadata,
malformed XML, corrupt zip, zip path traversal (POSIX, Windows and absolute) with a
no-files-written assertion, rasters generated on the fly (CRS, transform, shape, dtype,
nodata, pixel spacing, GCPs), rasters inside zips, graceful `rasterio` absence,
credential-like path skipping, symlink escape, zero-scene reporting, footprint caveat,
preprocessing decisions, and the CLI (`--out`, stdout, exit code 2).

Tests that need real rasters are skipped when `rasterio` is unavailable
(`test_rasterio_absence_is_graceful_and_noted` covers the degraded path).