# EODMS STAC catalog report — Radarsat-2_Tropical_Forest_Products scene selection

**Task:** `catalog-scene-selection` (research-only; no source changes in the repository)
**Revision:** 2 — rationale text, gap units and metadata wording corrected after review (see §15).
**Collection endpoint:** `https://www.eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products`
**Items endpoint:** `https://www.eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products/items`
**Access used:** public STAC metadata only. No EODMS login, no bearer token, no product download, no pixel data read.
**Query budget used:** 15 HTTP requests (13 returning data), within the <=15 / 12-minute bound.

Every figure in this report is recomputed by `verify_artifacts.py` from the delivered items; the machine-readable
version of the same numbers is the `verified_claims` block in `candidate-pairs.json`.

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
| 3-way AOI bounds | 118.133–119.490 E, 4.868–6.089 N (state of Sabah, island of Borneo, as read from the item coordinates) | −57.725 – −56.379, −2.606 – −1.176 (Brazilian Amazon basin, as read from the item coordinates) |
| Selected dates | 2009-08-12, 2009-11-16, 2010-02-20 | 2014-05-06, 2015-04-07, 2016-04-01 |
| Calendar-day gaps / total span | 96 / 96, 192 total | 336 / 360, 696 total |
| Scene zip size (`megabytes`) | 355, 395, 395 MB | 5497 MB each |
| Delivered grid spacing | 12.5 m pixel x 12.5 m line | 2.662357 m pixel x 2.498430–2.498449 m line |
| Minimum pairwise polygon overlap | 1 442 704 ha = **99.64 %** of the smaller footprint | 1 666 598 ha = **99.86 %** of the smaller footprint |
| 3-way AOI | 1 442 704 ha | 1 666 365 ha |
| Ratio of downloaded bytes | primary trio is 14.4x smaller than the comparison trio | — |

SGF is selected as primary: it is a legitimate member of this same challenge collection, is **~14x smaller per
trio** (1145 MB versus 16 491 MB), and the W3 swath footprint is large enough that the whole AOI survives into
every epoch.

Land cover at either AOI is **not** verified here. This catalog exposes only frame outlines and a browse
thumbnail, and no land-cover dataset was consulted within the query budget. Region names above are geographic
identifications read from the coordinates, nothing more.

## 2. What the live collection says (facts retrieved, not assumed)

From the collection document (query 1):

- `stac_version` 1.0.0, `type` Collection, `license` "various", `crs`/`storageCrs` `CRS84` (OGC 1.3).
- Spatial extent is the **whole globe** (`-180,-90,180,90`) — it carries no forest-specific footprint.
- Temporal extent `2008-02-14T00:00:00Z` / open-ended. No end date is published.
- `summaries`: `platform: ["Radarsat-2"]`, `instruments: ["SAR"]`. Frequency band C, centre 5.405 GHz (item level).
- Assets: `product` (`application/zip`, roles data/metadata/archive, `auth:refs: ["bearer"]`) and
  `thumbnail` (`image/png`, no auth reference).
- `auth:schemes` declares an OAuth2 password flow with `tokenUrl .../aaa/v1/login` and `refreshUrl .../aaa/v1/refresh`.
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

Unfiltered paging (queries 14–15, oldest-first) returned 200 items covering 2009-02-17 → 2010-12-02: **81 `SGF`
and 119 `SLC`**, every one `product_format: GeoTIFF`, every one `applied_lut: Constant-beta`. Polarizations in that
window include `HH VV HV VH`.

A separate bbox search over the central Amazon (queries 2–10, 800 unique items, 2013-08-29 → 2016-06-02) returned
**exclusively** `SLC` / GeoTIFF / `HH` / `applied_lut: Land` — 441 items with beam mnemonic `XF0W2` and 359 with
`XF0W3`, and zero `SGF` items.

So: the CSA-style overview of this mission family describes SLC, but the *public catalog as served* also contains
SGF, and it also contains non-Extra-Fine SLC variants (F3N, F6F, FQ3/FQ6/FQ9/FQ13/FQ16/FQ19/FQ23/FQ26/FQ29,
MF5W, MF6W, MF22W, MF23W), including quad-pol. **Any statement of the form "the collection is SLC" or "the
collection is SGF" is false.** Selection logic must filter on `product_type` client-side.

Observed grids and sizes, by (product_type, beam_mnemonic), across the 1000 items retrieved:

| product_type | beam | n | polarization | pixel x line grid (m) | `applied_lut` | zip size range (MB) |
|---|---|---|---|---|---|---|
| SGF | W3 | 43 | VV VH | 12.5 x 12.5 | Constant-beta | 355–529 |
| SGF | S6 | 2 | HH HV | 12.5 x 12.5 | Constant-beta | 291–303 |
| SGF | MF5W / MF6W / MF22W / MF23W | 36 | HH | 6.25 x 6.25 | Constant-beta | 288–301 |
| SLC | XF0W2 | 441 | HH | 2.662357 x 2.497627–2.499716 | Land | 4196–5803 |
| SLC | XF0W3 | 359 | HH | 2.662357 x 3.038302–3.041725 | Land | 2651–5065 |
| SLC | F3N / F6F / F6N (legacy) | 6 | HH HV | 4.733079 x 4.7126–5.2865 | Constant-beta | 688–773 |
| SLC | FQ3 / FQ6 / FQ9 / FQ13 / FQ16 / FQ19 / FQ23 / FQ26 / FQ29 (legacy) | 32 | HH VV HV VH | 4.733079 x 4.7119–5.3339 | Constant-beta | 194–409 |
| SLC | MF5W / MF6W / MF22W / MF23W (legacy) | 81 | HH | 2.662357 x 2.3908–2.9153 | Constant-beta | 1649–2040 |

Older vs newer offerings inside this one collection:

| Aspect | Legacy window (2009-02 → 2010-12) | Modern window (2013–2016, as sampled) |
|---|---|---|
| Representative product | `SGF` W3 / Wide3, VV VH | `SLC` XF0W2 / Extra Fine0, HH |
| `applied_lut` | `Constant-beta` on all 200 sampled items | `Land` on all 800 sampled items |
| Grid pitch | 6.25 m, 12.5 m (SGF); 2.66 m, 4.73 m (SLC) | 2.662357 m pixel, 2.50 m or 3.04 m line (XF0W2 / XF0W3) |
| Scene zip size | 194–2040 MB (SLC), 288–529 MB (SGF) | 2651–5803 MB |
| Beam ids present | S6, W3, F3N, F6F, F6N, FQ*, MF5W, MF6W, MF22W, MF23W | XF0W2, XF0W3 |
| Polarization | includes quad-pol (HH VV HV VH) | HH only in the sampled Amazon window |

## 4. Spacing is not resolution

This is the easiest mistake to make with this catalog.

- `sampled_pixel_spacing` and `sampled_line_spacing` are the **grid spacing of the delivered GeoTIFF** — the pixel
  pitch of the resampled map-geometry product. That is all they say.
- They are **not** sensor resolution, not independent-sample resolution, and not a detectability figure. Native
  resolution, processing-window limits and looks per pixel are not published in the item metadata, so **no
  resolution figure can be derived from these fields at all** — not for the 2.662357 m SLC grid and not for the
  12.5 m SGF grid.
- Practical consequence for scene selection: grid spacing determines **file size and disk/IO cost**, and nothing
  else that this catalog can tell you. Resolution determines **what is detectable**, but it must be established
  from product documentation and calibration metadata, not from the STAC item.
- A beam-dependent difference in line spacing is real and reproducible: across the 800 Amazon items, `XF0W2` spans
  2.497627–2.499716 m and `XF0W3` spans 3.038302–3.041725 m, with pixel spacing 2.662357 m for both. Since a beam
  change also changes the acquisition geometry, XF0W2 and XF0W3 are never mixed inside one series here.

## 5. Selected primary series — SGF, W3, VV VH, ascending, relative orbit 98

State of Sabah, island of Borneo, as read from the item coordinates. Centroid of the 3-way AOI:
**118.812 E, 5.479 N**.

| # | acquisition (UTC) | order_key | `megabytes` | `absolute_orbit` | grid (m) | `applied_lut` |
|---|---|---|---|---|---|---|
| 1 | 2009-08-12T10:24:43 | `RS2_OK133876_PK1168492_DK1128203_W3_20090812_102443_VV_VH_SGF` | 355 | 8673 | 12.5 x 12.5 | Constant-beta |
| 2 | 2009-11-16T10:24:48 | `RS2_OK133876_PK1168508_DK1128219_W3_20091116_102448_VV_VH_SGF` | 395 | 10045 | 12.5 x 12.5 | Constant-beta |
| 3 | 2010-02-20T10:24:47 | `RS2_OK133876_PK1168526_DK1128237_W3_20100220_102447_VV_VH_SGF` | 395 | 11417 | 12.5 x 12.5 | Constant-beta |

Pairwise real-polygon overlap. Calendar days are date differences; the elapsed interval between the acquisition
timestamps is a few seconds short of the calendar figure (the three timestamps differ by 4–5 s), so a naive
elapsed-days floor would report 95 instead of 96 for the second pair:

| pair | calendar days | elapsed interval | overlap ha | fraction of smaller footprint |
|---|---|---|---|---|
| 2009-08-12 x 2009-11-16 | 96 | 96 d 00:00:05 | 1 442 704 | 0.9964 |
| 2009-08-12 x 2010-02-20 | 192 | 192 d 00:00:04 | 1 443 812 | 0.9972 |
| 2009-11-16 x 2010-02-20 | 96 | 95 d 23:59:59 | 1 608 927 | 0.9992 |

3-way AOI = **1 442 704 ha** (99.64 % of the smaller footprint — the minimum, set by the 2009-08-12 scene, whose
1 447 874 ha footprint bounds the AOI; the two later scenes overlap each other at 99.92 %).

Group context: 9 acquisitions, 2009-08-12 → 2010-02-20, an exact 24-calendar-day cadence, all with the identical
beam / polarization / direction / relative orbit tuple and the identical grid, and **exactly one frame per date** —
so there is no adjacent-frame ambiguity. First, middle and last dates were taken to maximise the temporal baseline
(96 + 96 calendar days).

The rejected alternative in the same group is the trio a maximum-minimum-overlap search returns —
2009-11-16, 2010-01-03, 2010-02-20, gaps 48 and 48 calendar days: minimum pairwise overlap 1 608 927 ha (99.90 %)
but only a 96-calendar-day span. The selected trio trades 166 223 ha of minimum overlap for a 192-day baseline,
which is the right trade for change detection. Both figures are computed, not asserted.

A second, equally valid SGF group exists at relative orbit 240: 8 acquisitions, 2009-07-29 → 2010-03-02
(216 calendar days), 521–529 MB, centroid 116.95 E / 4.37 N, with **two** adjacent frames per pass, so a frame must
be chosen deliberately. It was not selected because it is larger than the chosen group and the extra frame
judgement is avoidable.

## 6. Comparison series — SLC, XF0W2, HH, ascending, relative orbit 104

Brazilian Amazon basin, as read from the item coordinates. Centroid of the 3-way AOI: **−57.053, −1.890**.

| # | acquisition (UTC) | order_key | `megabytes` | `absolute_orbit` | grid (m) | `applied_lut` |
|---|---|---|---|---|---|---|
| 1 | 2014-05-06T22:08:17 | `RS2_OK151247_PK1385617_DK1350411_XF0W2_20140506_220817_HH_SLC` | 5497 | 33375 | 2.662357 x 2.498430 | Land |
| 2 | 2015-04-07T22:08:08 | `RS2_OK151584_PK1387509_DK1352132_XF0W2_20150407_220808_HH_SLC` | 5497 | 38177 | 2.662357 x 2.498448 | Land |
| 3 | 2016-04-01T22:07:53 | `RS2_OK151671_PK1389651_DK1354430_XF0W2_20160401_220753_HH_SLC` | 5497 | 43322 | 2.662357 x 2.498449 | Land |

Pairwise real-polygon overlap:

| pair | calendar days | elapsed interval | overlap ha | fraction of smaller footprint |
|---|---|---|---|---|
| 2014-05-06 x 2015-04-07 | 336 | 335 d 23:59:51 | 1 667 489 | 0.9992 |
| 2014-05-06 x 2016-04-01 | 696 | 695 d 23:59:36 | 1 666 598 | 0.9986 |
| 2015-04-07 x 2016-04-01 | 360 | 359 d 23:59:45 | 1 667 587 | 0.9993 |

3-way AOI = **1 666 365 ha**, minimum pairwise overlap 1 666 598 ha = **99.86 %** of the smaller footprint.

Group context: 32 acquisitions, 2013-09-08 → 2016-04-25, a nominal 24-day repeat whose actual date gaps are
24, 48, 72 and 96 calendar days. 1 to 8 frames exist per pass, and frames within a pass are adjacent along-track
swaths. Frames were clustered into ground swaths by polygon centroid (0.02° tolerance): 31 swaths were
clustered, 20 of them exist on three or more dates, and 1112 date-triples were enumerated per swath keeping only
triples with >=300 calendar-day gaps and non-empty overlap.

The naive first/middle/last date pick (2013-09-08, 2015-01-25, 2016-04-25) was **rejected on measurement**: taking
each date's frame with the centroid nearest the first date's frame gives a minimum pairwise overlap of **0 ha**,
because the middle date's nearest-centroid frame lies on a different ground swath. That is direct evidence that
centroid proximity or bbox matching is unsafe for this catalog.

## 7. Overlap method (real polygons, not bboxes)

- Library: `shapely 2.1.2` (GEOS), installed into the sandbox for this analysis only.
- Operation: `Polygon.intersection` on the STAC item geometries, not on `bbox`.
- Areas: geometries projected to a **Lambert azimuthal equal-area** projection on the WGS84 sphere
  (R = 6 370 997 m) centred on the mean centroid of the series, then intersected in projected metres. This keeps
  the projection origin within about a degree of the data, so hectares are equal-area and the projection is not
  stretched across the globe. An earlier attempt with a single Amazon-centred projection produced GEOS
  `TopologyException` for the Borneo scenes — recorded because it is the failure mode to expect.
- Invalid geometries: `shapely.validation.make_valid` applied where `is_valid` was false.
- Caveat that bounds every number above: the STAC geometry is a **frame outline**, not a data mask. Overlap areas
  are therefore outline-derived estimates of the common footprint — an upper bound, because no data-validity mask,
  incidence-angle limit or nodata region is subtracted.

## 8. Candidate AOI

| | primary (SGF ro98) | comparison (SLC ro104) |
|---|---|---|
| bbox of the 3-way overlap | 118.133–119.490 E, 4.868–6.089 N | −57.725 – −56.379, −2.606 – −1.176 |
| area | 1 442 704 ha | 1 666 365 ha |
| polygon | `swarmforge_selection.primary_series.aoi_all_scenes_geojson` in `selected-scenes.geojson` | `swarmforge_selection.comparison_series.aoi_all_scenes_geojson` |

Both AOIs are large. For a first change-detection run it is sensible to cut a sub-AOI (for example a 20–40 km
window around the AOI centroid) before ordering, since the catalog only exposes whole-scene footprints.

Coordinates identify the administrative area and the broad region (Sabah on Borneo; the Brazilian Amazon basin).
Nothing in this catalog establishes what is on the ground inside either AOI: no land-cover class, no forest
extent, no habitat type. Treat the AOI as "a place a satellite pointed", not as "a forested place".

## 9. Known comparability

Established from public metadata alone:

- Within the primary series: identical `product_type`, `product_format`, `beam_mode`, `beam_mnemonic`,
  `sar:beam_ids`, `polarization`, `sat:orbit_state`, `sat:relative_orbit`, `applied_lut` and identical grid
  spacing across all three dates. Absolute orbits advance 8673 → 10045 → 11417 (+1372, +1372), a stable repeat
  cycle. Acquisition times agree to 5 s (10:24:43–10:24:48 UTC), so the time of day is effectively constant.
- Within the comparison series: identical tuple as above; absolute orbits 33375 → 38177 → 43322 (+4802, +5145);
  acquisition times agree to 24 s (22:07:53–22:08:17 UTC). The differing increments come from which repeats were
  selected, not from an orbit change.
- Polygon overlap is 99.64 % (primary) and 99.86 % (comparison) of the smaller footprint for every pair, so the
  remaining geometric comparability risk is incidence-angle and terrain-processing behaviour, not footprint
  mismatch.
- Cross-series comparison between the SGF and SLC series is **not** supported: different product class, beam,
  polarization, region, epoch and grid. The two series are alternatives, not a merged stack.
- Local time of day is consistent inside each series (SGF 10:24 UTC, SLC 22:08 UTC, both sun-synchronous passes),
  which removes one common cause of radiometric drift, but radiometric comparability itself is unverified because
  no pixel data was retrieved.

## 10. Metadata unknowns (precise)

- No calibrated radiometry, incidence angle, look direction, SNR, orbit phase, or calibration-LUT identifier in
  the STAC properties. Terrain-corrected change detection cannot be *planned* from metadata alone.
- `applied_lut` is recorded as a **label only** — `Land` on the modern SLC items, `Constant-beta` on the legacy
  items. The catalog does not define the vocabulary and does not state which coefficients, speckle filtering or
  multilooking were applied inside the delivered zip. What those coefficients are, and what they mean, stays
  unknown until the product metadata inside the zip is read.
- Grid spacing is given; native resolution, processing-window limits and looks per pixel are not, so no resolution
  can be inferred from the item metadata.
- The only visual asset is a browse `thumbnail` PNG. No quicklook product, no backscatter statistics, no per-date
  radiometric summary — so radiometric comparability between dates is unverified from the catalog.
- `product_format` is GeoTIFF for both families, but zip layout, band naming (amplitude vs real/imag), nodata
  conventions and calibration file names are undocumented and only visible after an authenticated download.
- Ordering feasibility (cost, delivery time, product availability on the server) is unverified: authentication was
  explicitly out of scope, and the `product` href is a public path that still requires a bearer token.
- Land cover, forest extent and habitat at either AOI are not verifiable from this catalog; no land-cover dataset
  was consulted within the query budget.
- Coverage completeness: the central-Amazon bbox result was paged to 800 items and stopped at 2016-06-02, and the
  collection temporal extent is open-ended from 2008-02-14, so the 2013–2016 window and the legacy 2009–2010
  window are **samples, not censuses**. Later years and other regions were not enumerated.
- Because `product_type` is not queryable and `POST` search is disabled, there is no efficient way to enumerate
  every SGF vs SLC group in this collection within a small query budget.
- An SGF series over the Amazon basin is neither confirmed nor excluded: every SGF group found in the 200-item
  legacy sample was in Borneo, and the budget did not allow a wider search.

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
  and resolution statement above is about metadata, or is explicitly absent.
- Overlap areas are outline-derived (see §7).
- To confirm real change, the next step is: authenticate → order the 3 primary SGF scenes (or the 3 SLC scenes) →
  read the product metadata inside the zip (incidence angles, calibration, multilooking) → orthorectify/calibrate to
  a common grid → difference/Coherence analysis. Not attempted here.

## 13. Explicit non-assertion

**No real-world change event is claimed.** This deliverable establishes only that the same ground was imaged on the
listed dates by comparable acquisitions, with computed polygon overlap. It does **not** establish that forest loss,
degradation, regrowth, flooding, fire or any other change occurred between those dates, and it does not establish
that either AOI is forested. Detecting change requires ordered, calibrated pixels and a difference analysis, which
was out of scope. Any downstream document must not infer a change event from this scene selection.

## 14. Artifacts

- `selected-scenes.geojson` — the 6 selected public STAC items verbatim (properties intact; `properties.auth:schemes`
  removed because it advertises the EODMS login endpoint). No token, signature or query credential exists in any
  href. The `product` hrefs are the unauthenticated public paths returned by the API and still require a bearer
  token to download. The two 3-way AOIs are attached as a `swarmforge_selection` foreign member.
- `candidate-pairs.json` — per-series attributes, every computed pair (hectares + fractions), selection rationale,
  a `verified_claims` block that the verifier re-derives, rejected candidates, metadata unknowns, query log,
  overlap method.
- `verify_artifacts.py` — re-derives the overlaps from the delivered geojson, checks attribute uniformity, checks
  that no token or signed URL is present, and fails on any contradiction between the numbers quoted in this report
  and the numbers actually computed from the items.

## 15. What changed in revision 2

Corrections made after review, so a reader diffing against revision 1 knows what moved:

1. **Primary overlap claim corrected.** Revision 1's rationale said ">=99.9 %". The computed minimum is
   1 442 704 ha = 99.64 % of the smaller footprint; 99.92 % applies only to the two later scenes. The rationale now
   reports the minimum and names which scene sets it.
2. **Gap units corrected.** Epoch gaps are now calendar-day differences (96 and 96 for the primary series; 336 and
   360 for the comparison series), with the elapsed interval shown alongside. Revision 1 quoted the floored elapsed
   values 95/95 and 335/359, which understate the true interval because the acquisition timestamps differ by
   4–24 s within a series.
3. **Comparison series numbers replaced.** Revision 1's comparison rationale described a rejected first/middle/last
   search (480 + 480 = 960 calendar days) that is not the series that was selected. The selected trio's real gaps
   (336 and 360 calendar days, 696 total) and its measured minimum overlap are now used throughout, and the naive
   pick is described only as a *rejected* alternative with its measured 0 ha overlap.
4. **Unsupported LUT wording removed.** Revision 1 described `Constant-beta` as a dose-rate LUT for gamma-0 and
   mentioned an undefined "HINT" term. Neither is supported by the catalog. `applied_lut` is now treated as an
   opaque label whose coefficients and meaning are unknown pending product metadata.
5. **No resolution inferred from grid pitch.** Revision 1's spacing discussion implied that the 2.66 m grid was
   oversampled relative to the sensor and characterised SGF's information content. Both were removed: grid pitch is
   reported as grid pitch, and resolution is listed as unknown and not derivable from these fields.
6. **No land-cover or habitat claims.** Revision 1 described the AOIs as tropical forest / oil-palm mosaic and
   frontier zone. Those descriptions are removed; regions are now identified from coordinates only, with an
   explicit statement that land cover is unverified.
7. **Several secondary figures recomputed** after checking them against the raw items: the legacy 200-item
   sample is 81 SGF + 119 SLC (revision 1 quoted the first page's 71 + 29); SLC zip sizes span 2651–5803 MB (revision 1 said
   4196–5803); the 2009–2010 SGF grid is not only 12.5 m — the MF beams are 6.25 m; the comparison group has 1–8
   frames per date, not 2–8, and its repeat gaps are 24/48/72/96 days, not a uniform 24; and the alternative
   SGF triple's overlap is 1 608 927 ha (revision 1 quoted an unverified 1 604 178 ha).
8. **Rationale text is now generated from computed values** in `build_artifacts.py`, so a number cannot drift out of
   agreement with the data again, and `verify_artifacts.py` fails on any contradiction between quoted and computed
   figures.
