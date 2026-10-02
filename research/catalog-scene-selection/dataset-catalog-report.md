# EODMS STAC catalog report — Radarsat-2_Tropical_Forest_Products scene selection

**Task:** `catalog-scene-selection` (research-only; no source changes in the repository)
**Collection endpoint:** `https://www.eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products`
**Items endpoint:** `https://www.eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products/items`
**Access used:** public STAC metadata only. No EODMS login, no bearer token, no product download, no pixel data read.
**Query budget used:** 15 HTTP requests (13 returning data), within the <=15 / 12-minute bound.

---

## 1. Headline result

The challenge collection holds **two different product families**, not one universal format. A practical
same-attribute, genuinely overlapping 3-acquisition series exists in **both**:

| | Primary (preferred) | Comparison |
|---|---|---|
| Product type | `SGF` (ScanSAR Georeferenced) | `SLC` (Single Look Complex) |
| Beam | `W3` / Wide3 | `XF0W2` / Extra Fine0 |
| Polarization | `VV VH` | `HH` |
| Orbit direction | ascending | ascending |
| Relative orbit | 98 | 104 |
| Location | interior Sabah, Malaysian Borneo (118.13–119.51 E, 4.81–6.15 N) | central Amazon, Brazil (-57.73 – -56.38, -2.61 – -1.18) |
| Selected dates | 2009-08-12, 2009-11-16, 2010-02-20 | 2014-05-06, 2015-04-07, 2016-04-01 |
| Epoch gaps | 96 d / 95 d (192 d span) | 335 d / 359 d (695 d span) |
| Scene size (zip, from `megabytes`) | 355 / 395 / 395 MB | 5497 MB each |
| Delivered grid | 12.5 m x 12.5 m | 2.662357 m x 2.498 m |
| Minimum pairwise polygon overlap | 1 442 704 ha (99.6 % of the smaller footprint) | 1 666 598 ha (99.9 %) |
| 3-way AOI | 1 442 704 ha | 1 666 365 ha |

SGF is selected as primary: it is a legitimate member of this same challenge collection, is **~14x smaller per
scene** (355–395 MB vs 5 497 MB), gives a tighter 24-day repeat series (9 acquisitions available in the sampled
window), and the W3 swath footprint is large enough that the whole AOI survives into every epoch.

## 2. What the live collection says (facts retrieved, not assumed)

From the collection document (query 1):

- `stac_version` 1.0.0, `type` Collection, `license` "various", `crs`/`storageCrs` `CRS84` (OGC 1.3).
- Spatial extent is the **whole globe** (`-180,-90,180,90`) — it carries no forest-specific footprint.
- Temporal extent `2008-02-14T00:00:00Z` / open-ended. No end date is published.
- `summaries`: `platform: ["Radarsat-2"]`, `instruments: ["SAR"]`. Frequency band C, centre 5.405 GHz (item level).
- Assets: `product` (`application/zip`, roles data/metadata/archive, `auth:refs: ["bearer"]`) and
  `thumbnail` (`image/png`, no auth reference).
- `auth:schemes` declares OAuth2 password flow with `tokenUrl .../aaa/v1/login` and `refreshUrl .../aaa/v1/refresh`.
  Not exercised.

From `/queryables` (query 11) — the **only** filterable/sortable properties advertised are:

```
applied_lut, datetime, end_datetime, geometry, order_key, polarization, sat:relative_orbit
```

`product_type` and `beam_mnemonic` exist on items but are **not** queryable, and the server rejected both
CQL2 `POST` attempts with `HTTP 405` (queries 12 and 13). Consequence: product-type-conditional searches are not
possible server-side; discovery has to be done client-side over paged results. That is the main practical
constraint on catalog exploration of this collection.

Item-level properties observed (full set): `absolute_orbit`, `applied_lut`, `datetime`, `end_datetime`,
`megabytes`, `order_key`, `polarization`, `product_format`, `product_type`, `proj:code`, `sampled_line_spacing`,
`sampled_pixel_spacing`, `sar:beam_ids`, `sar:center_frequency`, `sar:frequency_band`, `sar:polarizations`,
`sat:orbit_state`, `sat:relative_orbit`, plus `auth:schemes`.

## 3. Format reality check: SGF *and* SLC, both GeoTIFF

Unfiltered paging (queries 14–15, oldest-first) returned 200 items covering 2009-02-17 → 2010-12-02:

| product_type | n | product_format | beam mnemonics | applied_lut | polarizations |
|---|---|---|---|---|---|
| `SGF` | 71 | GeoTIFF | F3N, F6F, F6N, MF22W, MF5W, MF6W, S6, W3 | Constant-beta | HH, HH HV, VV VH |
| `SLC` | 29 | GeoTIFF | F3N, F6F, F6N, MF22W, MF5W, MF6W, S6, W3 | Land / Constant-beta | HH, HH HV, VV VH |

A separate bbox search over the central Amazon (queries 2–10, 800 unique items, 2013-08-29 → 2016-06-02) returned
**exclusively** `SLC` / GeoTIFF / `HH` / `applied_lut: Land`, beam mnemonics `XF0W2` (441) and `XF0W3` (359).

So: the CSA-style overview of this mission family describes SLC, but the *public catalog as served* also contains
SGF, and it also contains non-Extra-Fine SLC variants (F3N, F6F, MF5W …, including quad-pol). **Any statement of
the form "the collection is SLC" or "the collection is SGF" is false.** Selection logic must filter on
`product_type` client-side.

Older vs newer offerings in this one collection:

| Aspect | Legacy (2009–2010) | Modern (2013–2016, as sampled) |
|---|---|---|
| Representative product | `SGF` W3 / Wide3, VV VH | `SLC` XF0W2 / Extra Fine0, HH |
| `applied_lut` | `Constant-beta` | `Land` |
| Delivered grid | 12.5 m x 12.5 m | 2.662357 m x 2.498 m (XF0W2) / 2.662357 m x 3.040 m (XF0W3) |
| Scene zip size | 288–529 MB | 4196–5803 MB |
| Beam ids present | S6, W3, F3N, F6F, F6N, MF5W, MF6W, MF22W | XF0W2, XF0W3 |
| Polarization | includes quad-pol (HH VV HV VH) | HH only in the sampled Amazon window |

## 4. Spacing is not resolution

This is the single easiest mistake to make with this catalog.

- `sampled_pixel_spacing` and `sampled_line_spacing` are the **grid spacing of the delivered GeoTIFF**, i.e. the
  pixel pitch of the resampled map-geometry product. They are not sensor resolution and not independent-sample
  resolution.
- RS2 Extra Fine SLC products are gridded at ~2.66 m x 2.50 m here. That is roughly 4x finer in linear dimension
  than the native Extra Fine SLC information content allows; adjacent pixels in an SLC GeoTIFF are strongly
  correlated, and the real effective resolution depends on the processing (multilooking, adaptive filtering) that
  was applied — none of which is stated in the item metadata.
- SGF W3 products are gridded at 12.5 m. SGF is a multi-look, terrain-corrected, georeferenced product class; its
  native information content is coarser than or comparable to its 12.5 m grid, so the grid must not be read as
  "12.5 m resolution" either.
- Practical consequence for scene selection: pixel spacing determines **file size and disk/IO cost**; resolution
  determines **what is detectable**. The two must be tracked separately in any design document. The number of
  looks per pixel is unknown from the metadata.
- Beam-dependent grid difference, verified on 800 items: `XF0W2` -> line spacing 2.4976–2.4996 m (441 items),
  `XF0W3` -> 3.0385–3.0417 m (359 items); pixel spacing is 2.662357 m for both. Mixing XF0W2 and XF0W3 in one
  series changes both the grid and the incidence geometry, so the two series above never mix beams.

## 5. Selected primary series — SGF, W3, VV VH, ascending, relative orbit 98

Interior Sabah, Malaysian Borneo — tropical forest region. Centroid of the 3-way AOI: **118.812 E, 5.479 N**.

| # | acquisition (UTC) | order_key | `megabytes` | `absolute_orbit` | px / ln spacing (m) | `applied_lut` |
|---|---|---|---|---|---|---|
| 1 | 2009-08-12T10:24:43 | `RS2_OK133876_PK1168492_DK1128203_W3_20090812_102443_VV_VH_SGF` | 355 | 8673 | 12.5 / 12.5 | Constant-beta |
| 2 | 2009-11-16T10:24:48 | `RS2_OK133876_PK1168508_DK1128219_W3_20091116_102448_VV_VH_SGF` | 395 | 10045 | 12.5 / 12.5 | Constant-beta |
| 3 | 2010-02-20T10:24:47 | `RS2_OK133876_PK1168526_DK1128237_W3_20100220_102447_VV_VH_SGF` | 395 | 11417 | 12.5 / 12.5 | Constant-beta |

Pairwise real-polygon overlap (hectares, equal-area projection):

| pair | gap | overlap ha | fraction of smaller footprint |
|---|---|---|---|
| 2009-08-12 x 2009-11-16 | 96 d | 1 442 704 | 0.9964 |
| 2009-08-12 x 2010-02-20 | 192 d | 1 443 812 | 0.9972 |
| 2009-11-16 x 2010-02-20 | 95 d | 1 608 927 | 0.9992 |

3-way AOI = **1 442 704 ha** (bounded by the 2009-08-12 scene, which is a smaller/partial frame at 355 MB).

Group context: 9 acquisitions available in the sampled window, 2009-08-12 → 2010-02-20 (192 d, exact 24-day
cadence), all with the identical beam / polarization / direction / relative orbit tuple, and **exactly one frame
per date** in this group — so there is no adjacent-frame ambiguity. A second equally valid SGF group exists at
relative orbit 240 (8 dates, 2009-07-29 → 2010-03-02, 521–529 MB, centroid 116.95 E / 4.37 N); it was not selected
because it carries two adjacent frames per pass, so frame choice must be made deliberately, and ro98 is smaller.

## 6. Comparison series — SLC, XF0W2, HH, ascending, relative orbit 104

Central Amazon, Brazil. Centroid of the 3-way AOI: **-57.053, -1.890** (west of the Amazon arc, Pará/Amapá side of
the basin). Chosen to be the modern-SLC counterpart with the same rigour rules.

| # | acquisition (UTC) | order_key | `megabytes` | `absolute_orbit` | px / ln spacing (m) | `applied_lut` |
|---|---|---|---|---|---|---|
| 1 | 2014-05-06T22:08:17 | `RS2_OK151247_PK1385617_DK1350411_XF0W2_20140506_220817_HH_SLC` | 5497 | 33375 | 2.662357 / 2.498430 | Land |
| 2 | 2015-04-07T22:08:08 | `RS2_OK151584_PK1387509_DK1352132_XF0W2_20150407_220808_HH_SLC` | 5497 | 38177 | 2.662357 / 2.498448 | Land |
| 3 | 2016-04-01T22:07:53 | `RS2_OK151671_PK1389651_DK1354430_XF0W2_20160401_220753_HH_SLC` | 5497 | 43322 | 2.662357 / 2.498449 | Land |

Pairwise real-polygon overlap:

| pair | gap | overlap ha | fraction of smaller footprint |
|---|---|---|---|
| 2014-05-06 x 2015-04-07 | 335 d | 1 667 489 | 0.9992 |
| 2014-05-06 x 2016-04-01 | 695 d | 1 666 598 | 0.9986 |
| 2015-04-07 x 2016-04-01 | 359 d | 1 667 587 | 0.9993 |

3-way AOI = **1 666 365 ha**.

Group context: 32 acquisitions, 2013-09-08 → 2016-04-25, 24-day cadence with a 3-month hole
(2014-01-06 → 2014-04-12). 2–8 frames exist per pass, and frames within a pass are adjacent along-track swaths.
Frames were clustered into ground swaths by polygon centroid, and one swath was required to exist on all three
chosen dates. The naive "earliest / middle / latest date" choice failed: the middle date's nearest-centroid frame
belonged to a different swath and produced **0 ha** overlap — a concrete demonstration of why bbox or
centroid-proximity matching is unsafe and real polygon intersection is required.

## 7. Overlap method (real polygons, not bboxes)

- Library: `shapely 2.1.2` (GEOS), installed into the sandbox for this analysis only.
- Operation: `Polygon.intersection` on the STAC item geometries, not on `bbox`.
- Areas: geometries projected to a **Lambert azimuthal equal-area** projection on the WGS84 authalic-ish sphere
  (R = 6 370 997 m) centred on the mean centroid of the series, then intersected in projected metres. This keeps
  the projection origin within ~1 degree of the data, so hectares are equal-area and the projection is not
  stretched across the globe. An earlier attempt with a single Amazon-centred projection produced GEOS
  `TopologyException` for the Borneo scenes — recorded because it is the failure mode to expect.
- Invalid geometries: `shapely.validation.make_valid` applied where `is_valid` was false.
- Caveat that bounds every number above: the STAC geometry is a **frame outline**, not a data mask. Overlap areas
  are therefore outline-derived estimates of the common footprint — an upper bound in the sense that no data
  validity mask, incidence-angle limit or nodata region is subtracted.

## 8. Candidate AOI

| | primary (SGF ro98) | comparison (SLC ro104) |
|---|---|---|
| bbox of the 3-way overlap | 118.120–119.506 E, 4.815–6.146 N | -57.726 – -56.378, -2.607 – -1.175 |
| area | 1 442 704 ha | 1 666 365 ha |
| polygon | `swarmforge_selection.primary_series.aoi_all_scenes_geojson` in `selected-scenes.geojson` | `swarmforge_selection.comparison_series.aoi_all_scenes_geojson` |

Both AOIs are large. For a first change-detection run it is sensible to cut a sub-AOI (for example a 20–40 km
window around the AOI centroid, or around a known clearing) before ordering, since the catalog only exposes
whole-scene footprints.

Region notes, from coordinates and general knowledge only: the primary AOI sits in the interior of Sabah,
Malaysian Borneo, a lowland dipterocarp rainforest / oil-palm mosaic; the comparison AOI sits in the Brazilian
Amazon basin, in a well-documented frontier zone. These are characterisations of the area, not findings.

## 9. Known comparability

Established from public metadata alone:

- Within the primary series: identical `product_type`, `product_format`, `beam_mode`, `beam_mnemonic`, `sar:beam_ids`,
  `polarization`, `sat:orbit_state`, `sat:relative_orbit`, `applied_lut` and identical grid spacing across all three
  dates. Absolute orbits advance 8673 → 10045 → 11417 (+1372, +1372), i.e. a stable repeat cycle. Acquisition
  times within the series agree to ~5 seconds (10:24:43–10:24:48 UTC), so sun-synchronous local time of day is
  effectively constant.
- Within the comparison series: identical tuple as above; absolute orbits 33375 → 38177 → 43322 (+4802, +5145);
  acquisition times agree to ~24 s (22:07:53–22:08:17 UTC). The differing increments are a sampling artefact of
  which repeat was chosen, not evidence of an orbit change, but they do mean the repeat interval is not exactly
  constant.
- Polygon overlap >= 99.6 % of the smaller footprint for every pair in both series, so incidence-angle and
  terrain-processing differences are the remaining geometric comparability risk, not footprint mismatch.
- Cross-series comparison between the SGF and SLC series is **not** supported: different product class, beam,
  polarization, region, epoch and grid. The two series are alternatives, not a merged stack.
- Within-series local solar geometry is consistent (SGF ~10:24 UTC ≈ 18:24 local in Sabah, SLC ~22:08 UTC ≈ 19:08
  local in Brazil), which is favourable for radiometric comparability inside each series.

## 10. Metadata unknowns (precise)

- No calibrated radiometry, incidence angle, look direction, SNR, orbit phase, or calibration-LUT identifier in
  the STAC properties. Terrain-corrected change detection cannot be *planned* from metadata alone.
- `applied_lut` is recorded (`Land` for the modern SLC, `Constant-beta` for the legacy items) but the actual
  calibration coefficients, speckle filtering and multilooking inside the zip are not described. `Constant-beta`
  is a dose-rate LUT for gamma-0, i.e. raw intensity, not sigma-nought.
- Grid spacing is given; native resolution and looks-per-pixel are not.
- The only visual asset is a browse `thumbnail` PNG. No quicklook product, no backscatter statistics, no
  per-date radiometric summary — so radiometric comparability between dates is unverified from the catalog.
- `product_format` is GeoTIFF for both families, but zip layout, band naming (amplitude vs real/imag), nodata
  conventions and calibration file names are undocumented and only visible after an authenticated download.
- Ordering feasibility (cost, delivery time, product availability on the server) is unverified: authentication
  was explicitly out of scope, and the `product` href is a public path that still requires a bearer token.
- The term "HINT" is not defined by CSA in this metadata; the API's `applied_lut` vocabulary is not documented
  in the collection document.
- Coverage completeness: the central-Amazon bbox result was paged to 800 items and stopped at 2016-06-02; the
  collection temporal extent is open-ended from 2008-02-14, so the 2013–2016 window and the legacy 2009–2010
  window are **samples, not censuses**. Later years and other regions were not enumerated.
- Because `product_type` is not queryable and `POST` search is disabled, no efficient way exists to enumerate
  every SGF vs SLC group in this collection within a small query budget.

## 11. Query log (15 HTTP requests, budget exhausted)

| # | Method | Request | HTTP | Purpose |
|---|---|---|---|---|
| 1 | GET | `/collections/Radarsat-2_Tropical_Forest_Products` | 200 | collection metadata, assets, auth schemes |
| 2 | GET | `/items?bbox=-62,-5,-55,0&limit=100` | 200 | scout central-Amazon items |
| 3–10 | GET | `/items?bbox=-62,-5,-55,0&limit=200` + 7 `next` links | 200 | 800 unique items, 2013-08-29 → 2016-06-02 |
| 11 | GET | `/collections/.../queryables` | 200 | discover filterable properties |
| 12 | POST | `/collections/.../items` + CQL2 `product_type='SGF'` | 405 | probe server-side filter; unsupported |
| 13 | POST | `/search` + CQL2 | 405 | probe server-side filter; unsupported |
| 14 | GET | `/items?limit=200` (no bbox) | 200 | oldest items — revealed the SGF era |
| 15 | GET | `/items?limit=200` page 2 | 200 | extend legacy sample to 2010-12-02 |

## 12. Limitations and what a follow-up needs

- Bounded to 15 requests, so the 2013–2016 Amazon census (800 items) and the 2009–2010 legacy sample (200 items)
  are the only evidence base. The SGF groups found are all in Borneo; an equivalent SGF series over the Amazon is
  **not ruled out** but was not demonstrated.
- No authentication, no ordering, no download, no radiometric processing, no pixel inspection. Every radiometric
  statement above is about metadata, not data.
- Overlap areas are outline-derived (see §7).
- To confirm real change, the next step is: authenticate → order the 3 primary SGF scenes (or the 3 SLC scenes) →
  read the product metadata inside the zip (incidence angles, calibration, multilooking) → orthorectify/calibrate to
  a common grid → difference/Coherence analysis. Not attempted here.

## 13. Explicit non-assertion

**No real-world change event is claimed.** This deliverable establishes only that the same ground was imaged on the
listed dates by comparable acquisitions, with computed polygon overlap. It does **not** establish that forest loss,
degradation, regrowth, flooding, fire or any other change occurred between those dates. That claim requires
ordered, calibrated pixels and a difference analysis, which was out of scope. Any downstream document must not
infer a change event from this scene selection.

## 14. Artifacts

- `selected-scenes.geojson` — the 6 selected public STAC items verbatim (properties intact; `properties.auth:schemes`
  removed because it advertises the EODMS login endpoint). No token, signature or query credential exists in any
  href. The `product` hrefs are the unauthenticated public paths returned by the API and still require a bearer
  token to download. The two 3-way AOIs are attached as a `swarmforge_selection` foreign member.
- `candidate-pairs.json` — per-series attributes, every computed pair (hectares + fractions), selection rationale,
  rejected candidates, metadata unknowns, query log, overlap method.
