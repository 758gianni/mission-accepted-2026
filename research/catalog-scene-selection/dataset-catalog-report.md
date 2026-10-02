# EODMS catalog report — Radarsat-2_Tropical_Forest_Products scene selection

Revision 3. Task `catalog-scene-selection` (research only, no source changes).
**Access:** public STAC metadata only — no EODMS login, no bearer token, no download, no pixel read. 15 HTTP requests,
13 returning data. Every number below is re-derived by `verify_artifacts.py` from the delivered items.

## 1. Result

| | Primary series (download this) | Comparison series (reference only) |
|---|---|---|
| Product / beam | `SGF` W3 / Wide3, `VV VH`, ascending, relative orbit 98 | `SLC` XF0W2 / Extra Fine0, `HH`, ascending, relative orbit 104 |
| Dates | 2009-08-12, 2009-11-16, 2010-02-20 | 2014-05-06, 2015-04-07, 2016-04-01 |
| Calendar-day gaps / span | **96 / 96, 192** | **336 / 360, 696** |
| Scene size (`megabytes`) | 355, 395, 395 MB (1145 MB total) | 5497 MB each (16 491 MB total) |
| Grid pitch (pixel x line) | 12.5 m x 12.5 m | 2.662357 m x 2.498430–2.498449 m |
| Min pairwise polygon overlap | 1 442 704 ha = **99.64 %** of the smaller footprint | 1 666 598 ha = **99.86 %** |
| 3-way AOI area | 1 442 704 ha | 1 666 365 ha |
| 3-way AOI bbox (intersection bounds) | 118.133–119.490 E, 4.868–6.089 N | -57.725 - -56.379, -2.606 - -1.176 |

Gaps are calendar-day differences between acquisition dates. Elapsed intervals fall a few seconds short of them
(acquisition timestamps differ by 4–5 s within the SGF series and 24 s within the SLC series), so a floored
elapsed-day count would read 95 instead of 96, and 335/359 instead of 336/360.

Primary is preferred because it is the smaller legitimate product family in this same collection: 1145 MB against
16 491 MB for the comparison trio (14.4x), with a 24-calendar-day repeat cadence in the group.

Regions are identified from coordinates only (state of Sabah on the island of Borneo; Brazilian Amazon basin).
**Land cover at either AOI is not verified by this catalog** — it exposes only frame outlines and a browse thumbnail.

## 2. Primary series detail

| # | acquisition (UTC) | `order_key` | MB | `absolute_orbit` | grid (m) | `applied_lut` |
|---|---|---|---|---|---|---|
| 1 | 2009-08-12T10:24:43 | `RS2_OK133876_PK1168492_DK1128203_W3_20090812_102443_VV_VH_SGF` | 355 | 8673 | 12.5 x 12.5 | Constant-beta |
| 2 | 2009-11-16T10:24:48 | `RS2_OK133876_PK1168508_DK1128219_W3_20091116_102448_VV_VH_SGF` | 395 | 10045 | 12.5 x 12.5 | Constant-beta |
| 3 | 2010-02-20T10:24:47 | `RS2_OK133876_PK1168526_DK1128237_W3_20100220_102447_VV_VH_SGF` | 395 | 11417 | 12.5 x 12.5 | Constant-beta |

| pair | calendar days | elapsed | overlap ha | fraction of smaller footprint |
|---|---|---|---|---|
| 2009-08-12 x 2009-11-16 | 96 | 96 d 00:00:05 | 1 442 704 | 0.9964 |
| 2009-08-12 x 2010-02-20 | 192 | 192 d 00:00:04 | 1 443 812 | 0.9972 |
| 2009-11-16 x 2010-02-20 | 96 | 95 d 23:59:59 | 1 608 927 | 0.9992 |

Group: 9 acquisitions, 2009-08-12 → 2010-02-20, exact 24-calendar-day cadence, identical beam / polarization /
direction / relative orbit / grid / `applied_lut`, and **exactly one frame per date** — no adjacent-frame choice.
The 99.64 % minimum is set by scene 1, whose 1 447 874 ha footprint bounds the 3-way AOI; scenes 2 and 3 overlap
each other at 99.92 %. A rejected alternative in the same group (2009-11-16, 2010-01-03, 2010-02-20; gaps 48 and
48) has a higher minimum overlap of 1 608 927 ha on a 96-calendar-day span; the 192-day baseline was preferred.

## 3. Comparison series detail

| # | acquisition (UTC) | `order_key` | MB | `absolute_orbit` | grid (m) | `applied_lut` |
|---|---|---|---|---|---|---|
| 1 | 2014-05-06T22:08:17 | `RS2_OK151247_PK1385617_DK1350411_XF0W2_20140506_220817_HH_SLC` | 5497 | 33375 | 2.662357 x 2.498430 | Land |
| 2 | 2015-04-07T22:08:08 | `RS2_OK151584_PK1387509_DK1352132_XF0W2_20150407_220808_HH_SLC` | 5497 | 38177 | 2.662357 x 2.498448 | Land |
| 3 | 2016-04-01T22:07:53 | `RS2_OK151671_PK1389651_DK1354430_XF0W2_20160401_220753_HH_SLC` | 5497 | 43322 | 2.662357 x 2.498449 | Land |

| pair | calendar days | elapsed | overlap ha | fraction of smaller footprint |
|---|---|---|---|---|
| 2014-05-06 x 2015-04-07 | 336 | 335 d 23:59:51 | 1 667 489 | 0.9992 |
| 2014-05-06 x 2016-04-01 | 696 | 695 d 23:59:36 | 1 666 598 | 0.9986 |
| 2015-04-07 x 2016-04-01 | 360 | 359 d 23:59:45 | 1 667 587 | 0.9993 |

Group: 32 acquisitions, 2013-09-08 → 2016-04-25, actual date gaps 24, 48, 72 and 96 days; 1–8 frames per pass.
Frames were clustered into 31 ground swaths by polygon centroid (20 swaths appear on three or more dates; 1112
date-triples enumerated), and one swath present on all three dates was required. The naive first/middle/last date
pick was rejected on measurement: its minimum pairwise overlap is **0 ha**, because the middle date's
nearest-centroid frame lies on a different swath. The two series are alternatives, not a mergeable stack —
different product class, beam, polarization, region, epoch and grid.

Pixel spacing is exactly uniform across the comparison trio. Line spacing is not bit-identical: 2.498430,
2.498448 and 2.498449 m, a spread of 1.9e-05 m. That spread is within the 1e-03 m tolerance this deliverable
accepts, and no series-wide exemption is used to hide it.

## 4. Query findings (what the live API actually returned)

Endpoints: `/search/collections/Radarsat-2_Tropical_Forest_Products` and `.../items`.

| # | Request | HTTP | Note |
|---|---|---|---|
| 1 | GET collection | 200 | stac 1.0.0, CRS84, license "various", global spatial extent, temporal extent 2008-02-14 / open-ended, `product` zip asset with `auth:refs: ["bearer"]`, `thumbnail` png |
| 2 | GET items, `bbox=-62,-5,-55,0&limit=100` | 200 | scouting page |
| 3–10 | GET items, same bbox, `limit=200` + 7 `next` links | 200 | 800 unique items, 2013-08-29 → 2016-06-02 |
| 11 | GET `.../queryables` | 200 | only `applied_lut`, `datetime`, `end_datetime`, `geometry`, `order_key`, `polarization`, `sat:relative_orbit` |
| 12 | POST `.../items` with a CQL2 `product_type` filter | **405** | body: `{"code":405,"message":"405 Method Not Allowed","error":"Unknown",...}` |
| 13 | POST `/search` with a CQL2 `product_type` filter | **405** (this worker) / **400 unknown property** (independent reviewer) | see below |
| 14 | GET items, `limit=200`, no bbox | 200 | 200 items, 2009-02-17 → 2010-12-02 |
| 15 | GET items, `limit=200`, page 2 | 200 | (14–15 together form the 200-item legacy sample) |

**POST /search status is unresolved.** This worker's probe returned 405; an independent reviewer's probe of the
same endpoint returned 400 with an unknown-property error, which indicates the request reached property
resolution rather than being rejected as a method. Either way no server-side `product_type` filter was achieved,
and `product_type` is not among the advertised queryables — so client-side paging is the only method that
demonstrably works. Re-probing with correct CQL2 property syntax is an open follow-up item.

Catalog content, computed from the retrieved items (`catalog_sample_inventory` in `candidate-pairs.json`):

| sample | SGF | SLC |
|---|---|---|
| Unfiltered legacy page, 200 items, 2009-02-17 → 2010-12-02 | 81 items, all `Constant-beta`, beams MF22W/MF23W/MF5W/MF6W/S6/W3, polarizations HH, HH HV, VV VH, grid pitch 6.25 m and 12.5 m, 288–529 MB | 119 items, all `Constant-beta`, beams F3N/F6F/F6N/FQ3/FQ6/FQ9/FQ13/FQ16/FQ19/FQ23/FQ26/FQ29/MF22W/MF23W/MF5W/MF6W, polarizations HH, HH HV, HH VV HV VH, pixel pitch 2.662357 m and 4.733079 m, 194–2040 MB |
| Bbox −62,−5,−55,0 central Amazon, 800 unique items, 2013-08-29 → 2016-06-02 | 0 items | 800 items, all `Land`, all `HH`, all beam mode `Extra Fine0` with mnemonics XF0W2 (441) and XF0W3 (359), pixel pitch 2.662357 m, line pitch 2.497627–3.041725 m, 2651–5803 MB |

Both product families are therefore genuinely in this collection, and `product_format` is GeoTIFF for both.
"The collection is SLC" and "the collection is SGF" are both false.

## 5. Spacing is not resolution

- `sampled_pixel_spacing` and `sampled_line_spacing` are the **grid pitch of the delivered GeoTIFF**, nothing more.
- They are not sensor resolution and not independent-sample resolution. Native resolution, processing-window limits
  and looks per pixel are not published, so **no resolution figure can be derived from these fields**.
- Grid pitch determines file size and IO cost only. Resolution determines detectability, and must come from
  product documentation and calibration metadata.
- `XF0W2` line pitch spans 2.497627–2.499716 m and `XF0W3` spans 3.038302–3.041725 m in the Amazon sample, so
  beams are never mixed inside one series here.

## 6. Unknowns

- No calibrated radiometry, incidence angle, look direction, SNR, orbit phase or calibration-LUT identifier.
- `applied_lut` is an opaque label (`Land` on modern SLC, `Constant-beta` on legacy items). The catalog defines
  neither term nor the coefficients, speckle filtering or multilooking actually applied; unknown until the
  in-zip product metadata is read.
- Grid pitch is published; native resolution and looks per pixel are not.
- Only a browse `thumbnail` PNG is exposed — no quicklook, no backscatter statistics, so radiometric
  comparability between dates is unverified.
- GeoTIFF zip layout, band naming (amplitude vs real/imag), nodata conventions and calibration file names are
  undocumented.
- Ordering feasibility (cost, delivery, availability) is unverified: authentication was out of scope and the
  `product` href still requires a bearer token.
- Land cover, forest extent and habitat at either AOI are not verifiable from this catalog.
- Both samples are partial, not censuses: paging stopped at 800 Amazon items (2016-06-02) against an open-ended
  temporal extent, and every SGF group found sits in Borneo. An SGF series over the Amazon is neither confirmed
  nor excluded.
- The accepted POST `/search` payload syntax is unknown (§4).

## 7. Limitations and non-assertion

Overlap areas come from STAC frame outlines, not data masks, so they are upper bounds on the common footprint. No
authentication, ordering, download, calibration or pixel inspection was performed.

**No real-world change event is claimed.** Polygon overlap shows only that the same ground was imaged on the listed
dates by comparable acquisitions. It does not establish that forest loss, degradation, regrowth, flooding or fire
occurred, and it does not establish that either AOI is forested.

## 8. Artifacts

| file | contents |
|---|---|
| `selected-scenes-primary.geojson` | **download from this one**: the 3 primary SGF scenes (1145 MB total) and the primary 3-way AOI. The comparison SLC trio is deliberately absent so it cannot be pulled in by accident. |
| `selected-scenes.geojson` | all 6 selected public STAC items (primary + comparison) plus both 3-way AOI polygons |
| `candidate-pairs.json` | per-series attributes, every pair with hectares and fractions, selection rationale, `catalog_sample_inventory`, `verified_claims`, query log, unknowns |
| `verify_artifacts.py` | re-derives every quoted figure and fails on contradictions, unsupported wording, or missing primary-only separation |

Item properties are verbatim except `properties.auth:schemes`, which is removed because it advertises the EODMS
login endpoint. No token, signature or query credential appears in any href; `product` hrefs are the
unauthenticated public paths and still require a bearer token to download.

## 9. Revision log

- **r1** initial artifacts.
- **r2** corrected stale/invented wording: primary overlap claim 99.9 % → 99.64 %; floored elapsed-day gaps →
  calendar days (96/96/192 and 336/360/696); the comparison rationale's 480+480=960 search replaced by the
  selected trio; the unsupported `Constant-beta`/HINT description, resolution-from-grid wording and
  land-cover claims removed; secondary figures recomputed (legacy sample 81 SGF / 119 SLC, Amazon SLC sizes
  2651–5803 MB, comparison group 1–8 frames per date); rationale text now generated from computed values.
- **r3** report shortened to verified primary series, query findings and unknowns; AOI bbox fields made explicit
  (`aoi_intersection_bbox_lonlat` vs labelled `selected_scene_union_bbox_lonlat`); `selected-scenes-primary.geojson`
  added for primary-only download; POST `/search` status recorded as unresolved with both observed codes instead
  of a blanket 405; beam and polarization inventories now derived from the retrieved items; line-spacing
  uniformity checked with an explicit 1e-03 m tolerance rather than a series-name exemption.