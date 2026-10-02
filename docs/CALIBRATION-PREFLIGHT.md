# Calibration preflight (RADARSAT-2)

Read-only gate that answers one question about a product directory that already
exists on disk:

> Is this directory internally complete and self-consistent enough for a
> radiometric calibration step to be *attempted*?

Implemented in `processing/calibration_preflight.py`, tested in
`tests/processing/test_calibration_preflight.py`.

```
python -m processing.calibration_preflight /path/to/RS2_PRODUCT_DIR
python -m processing.calibration_preflight /path/to/RS2_PRODUCT_DIR --out preflight.json
python -m processing.calibration_preflight /path/to/RS2_PRODUCT_DIR --require-gdal-driver
```

## What it is not

* It is **not** calibration. No gains are applied, no offset is subtracted, no
  backscatter is produced.
* It is **not** geocoding, orthorectification, reprojection, terrain correction,
  coregistration or speckle filtering.
* It is **not** change detection, and it makes no persistence, historical
  anomaly, deforestation, fire or causal claim of any kind.
* It does **not** read a single pixel. Imagery is checked for *existence only*.
* It does **not** return `"verified"`, `"processed"` or `"calibrated"`. The
  strongest status it can return is `ready_for_calibration`.

`status` is one of:

| status | meaning |
| --- | --- |
| `ready_for_calibration` | Calibration *inputs* (metadata, lookup table, imagery references) were found present, contained and numerically sane. A calibration step may be attempted. |
| `blocked` | At least one required input is missing, unsupported or corrupt. Every reason is listed individually in `blocking`, each with a stable `code`. |

Anything the tool does not know stays unknown. There are no confidence
percentages and no inferred values.

## Checks performed

| Area | Findings |
| --- | --- |
| `product.xml` | `PRODUCT_XML_MISSING`, `PRODUCT_XML_UNREADABLE`, `PRODUCT_XML_UNPARSEABLE`, `PRODUCT_XML_TOO_LARGE` |
| Sample type | `SAMPLE_TYPE_MISSING`, `SAMPLE_TYPE_UNSUPPORTED` |
| Bit depth | `BITS_MISSING`, `BITS_UNSUPPORTED` |
| Dimensions | `DIMENSIONS_MISSING` |
| Polarizations | `POLARIZATIONS_MISSING` |
| Incidence span | `INCIDENCE_RANGE_MISSING` (advisory, non-blocking) |
| LUT reference | `SIGMA_LUT_REFERENCE_MISSING` |
| LUT containment | `SIGMA_LUT_PATH_ESCAPE` (blocking; nothing is read) |
| LUT presence | `SIGMA_LUT_MISSING` |
| LUT readability | `SIGMA_LUT_CORRUPT`, `SIGMA_LUT_TOO_LARGE`, `SIGMA_LUT_NO_ENTRIES` |
| LUT numerics | `SIGMA_LUT_GAIN_NOT_FINITE`, `SIGMA_LUT_GAIN_NOT_POSITIVE`, `SIGMA_LUT_OFFSET_NOT_FINITE`, `SIGMA_LUT_NO_GAIN_FOR_POL`, `SIGMA_LUT_WIDTH_COVERAGE_GAP` |
| Imagery | `IMAGERY_MISSING_FOR_POL`, `IMAGERY_INCOMPLETE_FOR_POL` |
| GDAL driver | `GDAL_DRIVER_UNAVAILABLE` (advisory by default, blocking with `--require-gdal-driver`) |

### Numeric gates

* **Gains** must parse as `float`, be finite (no `NaN`, no `INF`) and be
  strictly positive. A zero or negative gain is refused rather than used, since
  it would silently zero or invert calibrated backscatter.
* **Offsets** must be finite. Zero is legitimate and accepted; only `NaN`/`INF`
  or unparseable values are refused.
* **Width coverage** is evaluated per polarization. Each gain entry is treated
  as covering an interval around its `incidenceAngle`, and the union of those
  intervals must span the incidence range that the *product itself* declares via
  `nearRangeIncidenceAngle` / `farRangeIncidenceAngle`. Gaps are reported
  explicitly, per polarization, in degrees.

The `width` interpretation is stated in the report and is **not verified
against a real delivered lookup table** (see Limitations):

* `width_semantics="bin-width"` (default): `width` is the full width of a bin
  centred on `incidenceAngle`, i.e. half-width is `width / 2`.
* `width_semantics="half-width"`: `width` is the half-width, i.e.
  `|theta - incidenceAngle| <= width`.

Switch with `--width-semantics` once a real lookup table is available.

## Detected magnitude versus complex I/Q

This distinction is the reason the preflight refuses to be vague, and it is
derived from the `sampleType` declared in the delivered `product.xml` — never
from the product name or folder.

| declared `sampleType` | representation | calibrated band | power rule |
| --- | --- | --- | --- |
| `MAG`, `MAGNITUDE`, `DETECTED`, `AMPLITUDE` | `magnitude` | already **linear sigma0 power** once the detector applies the lookup table | **Do not square it.** Convert to dB with `10*log10(value)`. |
| `COMPLEX_IQ`, `COMPLEX`, `I_Q`, `IQ`, `SLC_COMPLEX` | `complex_iq` | in-phase and quadrature components | `sigma0 = abs(I + jQ)**2 = I**2 + Q**2`. A **magnitude-squared** step is required before `10*log10`. Neither `I` nor `Q` alone is power. |

Squaring an already-linear detected band would corrupt the radiometry, and
treating a complex component as power would corrupt it differently. The two are
reported in `calibration_output`, with `apply_magnitude_squared` set explicitly
to `true` or `false` — and to `null` when the sample type is unknown, in which
case **no** power rule is asserted.

## Path safety

Every path reached from `product.xml` is checked before any read is attempted:

* References containing `..` segments, absolute paths or drive letters are
  refused.
* The resolved real path must be a regular file **inside** the product
  directory, so a symlink pointing outside is refused
  (`SIGMA_LUT_PATH_ESCAPE`, `parse_status: "not_attempted"`). A symlink whose
  target is inside the product is accepted.
* Directory symlinks that escape the product are pruned during imagery
  discovery, so a hostile link cannot smuggle files into the inventory.
* XML and lookup-table reads are bounded (`LIMITS["max_xml_bytes"]`,
  `LIMITS["max_lut_bytes"]`, `LIMITS["max_lut_entries"]`,
  `LIMITS["max_imagery_files"]`).
* A corrupt or truncated lookup table yields `SIGMA_LUT_CORRUPT` with the parser
  message, never a partial gain list.

Nothing inside the product directory is created, modified or removed. The only
optional write is the `--out` report path, written atomically (temp file plus
`os.replace`). No credentials, no network, no EODMS or other authentication.

## GDAL driver capabilities are measured, not assumed

`gdal_driver_capabilities()` probes the **installed** registry at runtime via
`rasterio.drivers.raster_driver_extensions()` and reports, per driver,
`present: true|false`, a `note`, and `route_claimed: false`.

Measured in this development environment:

* rasterio 1.5.2, **GDAL 3.12.2**, 44 driver names visible to rasterio.
* **`SGF` is absent.** `CGX` is absent. `RS2`, `ISCE` and `ENVI` are also absent
  from rasterio's registry. A direct probe of the bundled `libgdal` via `ctypes`
  found 147 registered drivers including `RS2`, `ISCE`, `SAR_CEOS`, `JAXAPALSAR`
  and `AirSAR` — but still **no `SGF` and no `CGX`**. So the absence of `SGF` is
  a real property of this GDAL build, not an artifact of the probe method.

Consequences, stated explicitly:

* The preflight does **not** assert that SGF is ScanSAR, ground-range detected,
  geocoded or orthorectified. Those are product-format claims that require a real
  delivered product to establish. `gdal_capabilities.drivers.SGF` carries no
  `scansar` or `geocoded` key at all, and a test asserts that the report never
  contains such a claim.
* No substitute route is forced. When the declared `productType` would normally
  be read with a dedicated driver and that driver is absent, the preflight
  reports `GDAL_DRIVER_UNAVAILABLE` and refuses to guess an alternative.
* By default that finding is **advisory**, so the metadata/LUT/imagery verdict
  stays independent of local software. Pass `--require-gdal-driver` to make it
  blocking.
* SLC/complex products need no special driver (they are plain GeoTIFF I/Q
  pairs), so no driver is required for them.

## Report shape

Top level: `schema_version`, `tool`, `tool_version`, `generated_utc`,
`product_dir`, `product_dir_resolved`, `status`, `ready_for_calibration`,
`semantics`, `read_only`, `network_used`, `credentials_used`, `pixels_read`,
`calibration_performed`, `geocoding_performed`, `change_detection_performed`,
`limits`, `product`, `sigma_lut`, `imagery`, `calibration_output`,
`gdal_capabilities`, `findings`, `blocking`, `blocking_count`, `warnings`,
`errors`, `summary`.

`blocking` is the filtered list of `findings` where `blocking: true`; each entry
carries a stable `code`, a `severity` of `missing` / `unsupported` / `corrupt`,
a `subject` and a `message`. `sigma_lut.element_paths` records the element paths
the gain and offset entries were actually found at inside the delivered file, so
the layout assumption is auditable rather than implicit.

## Limitations (recorded honestly)

1. **No real product has been checked.** No RADARSAT-2 product exists in the
   development environment, so this gate has never run against delivered data.
   Every fixture in the test file is synthetic.
2. **The public reference `product.xml` could not be fetched.** The reference
   named in the task,
   `https://donnees-data.asc-csa.gc.ca/users/OpenData_DonneesOuvertes/pub/RADARSAT-2/RS2_OK103540_PK929658_DK864570_SLA12_20190317_110012_HH_SLC/product.xml`
   (a *different* acquisition, not challenge input) is unreachable from this
   environment: TLS verification fails with `unable to get local issuer
   certificate` via `curl` (default CA store and certifi's bundle) and the fetch
   tool reports a transport error. TLS verification was deliberately **not**
   disabled, so the element paths below are **not** confirmed against that file.
3. **Element paths are namespace-independent and tolerant by design.** Matching
   is by local name and descendant search, not by a strict schema, so both the
   nested `sigmaZeroLookupTable/lut/gainList/gain` layout and a flat
   `gainList/gain` layout are handled. The resolved paths are reported in
   `sigma_lut.element_paths` so a mismatch with the real product is visible
   rather than silent. Paths currently matched:
   * `product/imageAttributes/{productType, sampleType, bitsPerSample, numberOfLines, numberOfSamplesPerLine, nearRangeIncidenceAngle, farRangeIncidenceAngle, transmitterReceiverPolarisation}`
   * `product/calibration/calibrationLookupTable/@href`
   * `sigmaZeroLookupTable/lut/gainList/gain/{pol, step, incidenceAngle, gain, width}`
   * `sigmaZeroLookupTable/lut/offsetList/offset/{pol, step, incidenceAngle, offset}`
4. **The `width` convention is unverified** (see above). It is reported, not
   hidden.
5. **Imagery layout is discovered, not hardcoded.** Files are matched to a
   polarization by token, so both `imagery/imagery_HH_I.tif`-style and
   `imagery/image_HH.tif`-style layouts work. The real delivered layout has not
   been observed.
6. **No calibration route, geocoding, terrain correction or product type
   selection is implemented or recommended.** Per the recorded backend
   decision, preprocessing requirements depend on the actual product and mode
   and must not become gates before the real files arrive.
7. **`rasterio` is optional.** If it is missing, the driver probe reports that it
   could not measure the registry; every other check still runs. No CRS, no
   geotransform and no radiometric value is ever invented.
