# Calibration preflight (RADARSAT-2)

Read-only gate answering one question about a product directory already on disk:

> Are the inputs for a radiometric calibration step present, contained and sane?

`processing/calibration_preflight.py`, tests in
`tests/processing/test_calibration_preflight.py`. Version 2.0.0 corrects the
schema and the radiometry of version 1.x (see
[Corrections in this revision](#corrections-in-this-revision)).

```
python -m processing.calibration_preflight /path/to/RS2_PRODUCT_DIR
python -m processing.calibration_preflight /path/to/RS2_PRODUCT_DIR --out report.json
```

## What it is not

* **No calibration is performed.** No gain is applied, no offset subtracted, no
  backscatter produced, no pixel read.
* No geocoding, orthorectification, reprojection, terrain correction or speckle
  filtering. No change detection, and no persistence, historical, deforestation,
  fire or causal claim of any kind.
* It never returns `"verified"`, `"processed"` or `"calibrated"`. The strongest
  status is `ready_for_calibration`.

| status | meaning |
| --- | --- |
| `ready_for_calibration` | Calibration **inputs** were found present, contained and numerically sane. A calibration step *may be attempted*. |
| `blocked` | Something is missing, unsupported, corrupt or truncated. Every reason is listed individually in `blocking`. |

## Schema actually parsed

`product.xml`, matched by namespace-independent local name:

| path | use |
| --- | --- |
| `imageAttributes/rasterAttributes/dataType` | `Mag` or `Complex` |
| `imageAttributes/rasterAttributes/bitsPerSample` | 8/16 for Mag, 16/32 for Complex |
| `imageAttributes/rasterAttributes/numberOfLines` | > 0 |
| `imageAttributes/rasterAttributes/numberOfSamplesPerLine` | > 0, and the gain list must cover it |
| `imageAttributes/transmitterReceiverPolarisation` | `HH`/`HV`/`VV`/`VH` |
| `imageAttributes/productType`, `.../acquisitionType` | recorded as product attributes only |
| `calibration/.../lookupTable` | **filename is the element text**; `selected` attribute carries the lookup dimension (`incidenceAngleCorrection`, `incidenceAngleRange`) |

The sigma0 lookup table:

```xml
<lut>
  <offset>SCALAR</offset>
  <gains>G0 G1 G2 ... GN</gains>   <!-- one gain per image column -->
</lut>
```

`<gains>` is a **column list**. There is **no** `gainList`, **no** `pol` element
and **no** per-gain `incidenceAngle`/`width` pair — version 1.x parsed that
invented schema and it has been removed. There is no per-angle interpolation and
no width-coverage check. A document containing `gainList`/`pol`/`incidenceAngle`
/`width` is correctly reported as carrying no usable `<gains>` column list
(`LUT_GAINS_UNPARSEABLE`), and a regression test pins that.

This schema is corroborated by the installed GDAL 3.12.2 binary, which contains
the XPath expression string **`=lut.gains`** and the literal
**`incidenceAngleCorrection`**.

## Radiometry: raw source versus calibrated output

The two are reported separately and never conflated. **This tool performs no
calibration**; it only states what a later step would have to do.

| | raw source (as delivered) | future calibrated (not produced here) |
| --- | --- | --- |
| `Mag` | quantised **amplitude DN**. `is_power: false` | `sigma0 = (DN**2 + offset) / gain[column]` |
| `Complex` | **I and Q components**. `is_power: false`. Neither I nor Q is power | `sigma0 = (I**2 + Q**2 + offset) / gain[column]` |

**The gain divides in both cases.** `abs(I+jQ)**2` *alone* is not sigma0; the
gain and the offset are still required. Version 1.x wrongly described a raw
magnitude band as "already linear sigma0 power" and wrongly stated the complex
rule as bare `|I+jQ|**2`; both are fixed.

**A band opened with GDAL metadata item `RADARSAT_2_CALIB:SIGMA0` is already
linear sigma0 POWER. Do not square it again.** Squaring is a step in *producing*
that band from raw DN, not a step to apply to it afterwards. (The
corresponding `RADARSAT_2_CALIB:BETA0` and `:GAMMA0` items exist too.)

### What is and is not verified

Verified against the installed libgdal by direct inspection:

* the metadata domain `RADARSAT_2_CALIB` and item names `RADARSAT_2_CALIB:SIGMA0`,
  `:BETA0`, `:GAMMA0`, `:GAMMA`, `:UNCALIB` exist;
* the LUT XPath `=lut.gains` and the literal `incidenceAngleCorrection` exist;
* the `Complex` data-type literal exists.

**Not** verified, and reported as such in
`representations.formula_verified_against_gdal_source: false`: the exact
arithmetic of the calibration formula. The formulas above are the CSA/project
convention specified for this work. They were **not** confirmed against GDAL
3.12.2 source or an empirical lab artifact, because **no real RADARSAT-2 product
is available in this environment** and building one GDAL would accept was out of
scope. No division factor is guessed and no coefficient is invented. Independent
review should confirm this against `frmts/rs2` before any calibration code is
written.

## Drivers

| driver | status on this stack | role |
| --- | --- | --- |
| `RS2` | **present** | reads delivered RADARSAT-2 GeoTIFF products |
| `RCM` | present | **separate** driver for RCM products; never a fallback for RS2 |
| `SGF` | **absent** | not the RADARSAT-2 driver, not an alternative |
| `CGX` | **absent** | not the RADARSAT-2 driver, not an alternative |

Driver presence is measured at runtime from the **full GDAL driver registry**,
reached through the bundled libgdal. Version 1.x used
`rasterio.drivers.raster_driver_extensions()`, a *filtered* listing that **hid
the `RS2` driver on this very stack** and wrongly led to an SGF-first design.
`test_rs2_driver_is_measured_from_the_real_gdal_registry` pins the corrected
probe.

`productType` is a product attribute. It is **never** used to infer a driver
name and never matched against the driver list. Driver absence is blocking by
default (`GDAL_DRIVER_UNAVAILABLE`); `--allow-absent-driver` makes it advisory.

## Checks

| area | findings |
| --- | --- |
| `product.xml` | `PRODUCT_XML_MISSING`, `PRODUCT_XML_UNREADABLE`, `PRODUCT_XML_UNPARSEABLE`, `PRODUCT_XML_SCAN_TRUNCATED` |
| `product.xml` containment | `PRODUCT_XML_PATH_ESCAPE` |
| `dataType` | `DATA_TYPE_MISSING`, `DATA_TYPE_UNSUPPORTED` |
| bit depth | `BITS_MISSING`, `BITS_UNSUPPORTED` |
| dimensions | `DIMENSIONS_MISSING` |
| polarizations | `POLARIZATIONS_MISSING` |
| LUT reference | `LUT_REFERENCE_MISSING`, `LUT_PATH_ESCAPE`, `LUT_MISSING` |
| LUT readability | `LUT_SCAN_TRUNCATED`, `LUT_UNREADABLE`, `LUT_CORRUPT`, `LUT_NO_LUT_ELEMENT`, `LUT_GAINS_UNPARSEABLE` |
| LUT numerics | `LUT_OFFSET_NOT_FINITE`, `LUT_GAINS_NOT_FINITE`, `LUT_GAINS_NOT_POSITIVE`, `LUT_GAINS_DO_NOT_COVER_WIDTH` |
| width coverage | `WIDTH_UNKNOWN` (advisory, when raster width is unknown) |
| imagery | `IMAGERY_MISSING_FOR_POL`, `IMAGERY_INCOMPLETE_FOR_POL` |
| driver | `GDAL_DRIVER_UNAVAILABLE` |

### Numeric gates

* **Gains**: every entry must parse as a finite float and be strictly positive.
* **Offset**: a single finite scalar. Zero is legitimate and accepted.
* **Gain list length must cover the raster width.** Fewer gains than
  `numberOfSamplesPerLine` is blocking, and the report names the uncovered column
  range (e.g. `columns 3..5 would have no gain`). A longer list is fine.

### Bounded scans and truncation

Every XML and lookup-table read is bounded. Exceeding a limit produces an
**explicit truncation finding** and asserts nothing about the contents:

* `PRODUCT_XML_SCAN_TRUNCATED` — above `LIMITS["max_product_xml_bytes"]`
* `LUT_SCAN_TRUNCATED` — above `LIMITS["max_lut_bytes"]`

A truncated lookup table reports `gain_count: 0` rather than a partial list.

### Path safety

* References containing `..`, absolute paths or drive letters are refused.
* The resolved real path must be a **regular file inside the product
  directory**, so a symlink pointing outside is refused. A symlink whose target
  *is* inside the product is accepted.
* **`product.xml` itself is subject to the same containment rule.** Version 1.x
  read `product.xml` directly and would follow a symlink out of the product;
  that is fixed and pinned by
  `test_product_xml_symlink_escaping_the_product_is_refused`.
* Directory symlinks escaping the product are pruned during imagery discovery.
* **The CLI refuses to write the report inside the product directory**
  (exit code 2), so a read-only product cannot be written into.

## Corrections in this revision

| v1.x | v2.0 |
| --- | --- |
| Parsed an invented `gainList`/`gain`/`pol`/`incidenceAngle`/`width` schema | Parses the real `<lut><offset/><gains/></lut>` column list |
| Raw `Mag` band described as "already linear sigma0 power" | Raw `Mag` DN is amplitude; `sigma0 = (DN**2 + offset) / gain` |
| Complex rule stated as bare `\|I+jQ\|**2` | `sigma0 = (I**2 + Q**2 + offset) / gain[column]`; gain included |
| LUT reference read from an `href` attribute | Read from `<lookupTable>` **element text**, with `selected` |
| `dataType` read from `imageAttributes/sampleType` | Read from `imageAttributes/rasterAttributes/dataType` (`Mag`/`Complex`) |
| SGF/CGX treated as the RADARSAT-2 drivers | `RS2` is the driver; SGF/CGX absent and not alternatives; `RCM` separate |
| Driver probe used rasterio's filtered list, hiding `RS2` | Full libgdal registry via the bundled library |
| `productType` mapped to a driver name | `productType` is a product attribute, never a driver |
| `product.xml` symlink escape accepted | Contained like every other reference |
| Silent read-limit handling | Explicit `*_SCAN_TRUNCATED` findings |
| Report could be written inside the product | Refused, exit 2 |
| ~1490 lines of generic machinery | 1227 lines, one concrete parser |

Version 1.x tests that asserted the wrong science (raw Mag is power, the complex
rule without gain, SGF as the driver) were removed, not adjusted.

## Limitations

1. **Never run against a real product.** No RADARSAT-2 product exists here, so
   every fixture is synthetic. `ready_for_calibration` is a claim about
   XML/LUT/path consistency only.
2. **The calibration formula is unverified against GDAL source** (see above).
3. **Imagery layout is discovered, not hardcoded** — files are matched to a
   polarization by filename token, so both `imagery/imagery_HH_I.tif` and
   `imagery/image_HH.tif` styles are found. The real delivered layout has not
   been observed.
4. **No calibration route, geocoding or product selection** is implemented or
   recommended.
5. `rasterio` is optional; if it cannot locate libgdal, the driver probe reports
   `present: null` (**unknown**, not absent) and every other check still runs.
   No CRS, transform or radiometric value is ever invented.