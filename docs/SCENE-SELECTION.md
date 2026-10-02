# Scene selection from a public STAC catalog

`catalog/select.py` chooses 2 or 3 compatible RADARSAT-2 scenes from a STAC
FeatureCollection that has already been fetched, and writes a JSON report
containing the full public records of the chosen scenes.

It is an offline, credential-free step. It performs no network request, reads no
credential, and downloads nothing. The only input is a local JSON file.

## Usage

```bash
python -m catalog.select data/catalogs/eodms-tropical-forest.json \
    --count 3 \
    --max-scene-mb 700 \
    --min-gap-days 90 \
    --min-overlap 99.0 \
    --out data/reports/selection.json
```

Every threshold is a required argument. There is no built-in default, no weight
and no hidden score: if a threshold matters to the caller, the caller states it.

| Argument | Meaning |
| --- | --- |
| `catalog` | local STAC `FeatureCollection` JSON file (positional) |
| `--count` | 2 or 3 scenes; any other value is refused with exit code 2 |
| `--max-scene-mb` | client-side per-scene size cap, in megabytes |
| `--min-gap-days` | minimum separation between any two selected acquisitions, in UTC calendar days |
| `--min-overlap` | minimum pairwise intersection, in percent of the smaller of the two footprints |
| `--product-type` | optional, repeatable; restrict to product types client-side |
| `--out` | path of the JSON report to write |

Exit codes: `0` selection written, `1` the catalog is unusable or no candidate
set satisfies the criteria (nothing is written), `2` usage error.

## What the tool does, in order

1. **Client-side size and product-type filtering.** Size is read from
   `megabytes`, or from `file:size` / `raster:bytes` converted from bytes. An
   item whose size is unknown is not selectable, because `--max-scene-mb` cannot
   be checked against it; it is listed in `items_with_unknown_size` and its
   rejection reason says so.
2. **Metadata compatibility grouping.** Scenes are compared on polarization
   (`polarization` or `sar:polarizations`), beam (`beam_mnemonic` or
   `sar:beam_ids`), orbit (`sat:orbit_state` together with `sat:relative_orbit`)
   and `applied_lut`. Two scenes are compatible when every one of these
   attributes that *both* of them state agrees. An attribute that is unknown
   imposes no constraint and is never copied from a sibling item: it is listed in
   `unknown_metadata` and in `selection.unknown_fields_by_item`.
3. **Candidate enumeration.** Within each compatibility cluster, every
   combination of `--count` scenes is enumerated. A cluster needing more than
   `MAX_CANDIDATE_SETS` (200000) combinations is refused with an explicit
   message rather than silently truncated.
4. **Hard requirements**, all of which must hold: pairwise compatibility, every
   pairwise overlap at least `--min-overlap`, every pairwise calendar-day
   separation at least `--min-gap-days`, and every size at most
   `--max-scene-mb`.
5. **Ranking**, applied lexicographically; the first term that differs decides.
   The same list is echoed in the report as `selection_order`.
   1. largest minimum pairwise overlap, in percent of the smaller footprint
   2. largest common intersection area of all selected scenes, in hectares
   3. smallest total calendar-day span between the first and last acquisition
   4. smallest total size in megabytes
   5. ascending sorted tuple of item ids, so an exact tie is still reproducible

Because the search is exhaustive over a cluster and the ordering is total, the
result depends only on the input file and the thresholds, not on the order of
the features in the file.

## Dates

Separation is a difference between **UTC calendar days** of the acquisition
instants. The exact elapsed interval is reported separately as
`gap_elapsed_seconds` and is never floored into a day count. A pair acquired
23:30 on one day and 00:30 the next is one calendar day apart and 3600 elapsed
seconds apart; flooring elapsed seconds would call it zero days and would
understate a real 96-day RADARSAT-2 repeat interval by a day whenever the two
scenes were acquired a few seconds apart across midnight, which is the normal
case for a repeat orbit.

## Areas

Overlap is computed from the **full STAC item polygons**, never from bounding
boxes. Hectares come from `pyproj.Geod(ellps="WGS84")`, which is an ellipsoidal
area and therefore correct for a geographic CRS, and each figure is cross-checked
against a spherical Lambert azimuthal equal-area projection centred on the
first candidate's footprint centroid. The cross-check result is reported as
`equal_area_cross_check_max_relative_difference` together with a 0.5 %
`equal_area_cross_check_passed` flag. The two measures differ by roughly 0.4 %
on the published Sabah frames, which is the expected difference between a
spherical and an ellipsoidal area, so hectares from this tool are not
interchangeable with a spherical figure quoted elsewhere.

The overlap fraction is always stated explicitly:

```
percent_of_smaller_footprint = 100 * intersection hectares / hectares of the smaller footprint
```

The denominator is the smaller of the two footprints, so the value means "the
part of the narrower scene that the wider one covers". The report also carries
`intersection_ha_geodesic`, `intersection_ha_equal_area`, `smaller_footprint_ha`
and `covers_entire_smaller_footprint`, so the fraction can be recomputed by a
reader rather than taken on trust.

Because the footprints are frame outlines from metadata, these areas are
upper bounds on the true common footprint of a prepared pair.

## Refusals

These are reported, never guessed around:

* anything other than a `Polygon` footprint (missing geometry, `Point`,
  `LineString`, `MultiPolygon`, a `GeometryCollection` produced by repairing an
  invalid polygon) is rejected with its type in the reason;
* a ring whose consecutive longitudes jump by more than 180 degrees, or a
  footprint whose longitude extent exceeds 180 degrees, is rejected as
  antimeridian-spanning;
* an invalid polygon is repaired with `shapely.make_valid` and listed in
  `geometry_repairs`; if the repair does not yield a polygon the item is
  rejected;
* an item declaring a `proj:code` that is not WGS84 geographic is rejected; an
  item with no declared CRS is measured in WGS84 (the STAC GeoJSON default) and
  `proj:code` is listed as unknown;
* an acquisition timestamp that is missing, unparseable, or has no UTC offset is
  rejected;
* if no set satisfies the criteria, the run fails with the per-cluster counts of
  enumerated and feasible sets plus the refusal reasons, and writes nothing.

## Credential handling

The report keeps the selected public items in full: `id`, `geometry`, `bbox`,
all `properties`, `assets` and `links`. Before writing, three things are removed
and recorded in `sanitization.findings`:

* an asset whose href carries a credential-like query parameter, or embedded
  URL userinfo;
* an asset guarded by a non-public `auth:refs` scheme such as `bearer`;
* a property whose name is credential-like, or whose value is a URL carrying
  credential-like parameters.

The credential detector is intentionally over-broad: it also matches short
Azure/AWS-style signature parameter names, so it may refuse a benign link. Every
refusal is reported, so nothing disappears silently. Public item links (`self`,
`collection`, `parent`, `root`) and the thumbnail asset are kept, which is what
lets a person follow the item to the official catalogue using their own
credentials. The product asset href of the published EODMS items is a
catalogue endpoint guarded by `auth:refs: ["bearer"]`; it is dropped, and no
credential is present in these records to begin with.

## Worked example: the six published research items

Input: the six public items of `research/catalog-scene-selection/selected-scenes.geojson`
at commit `dd44fa12d15893c5474f382f0e1fa9839f231308` (three Sabah `Wide3` SGF
scenes from 2009, three `XF0W2` SLC scenes from the Amazon comparison series),
with `--max-scene-mb 700`:

| Field | Value |
| --- | --- |
| selected | the three Sabah SGF scenes, 2009-08-12 / 2009-11-16 / 2010-02-20 |
| rejected | all three SLC items, `size 5497 MB (megabytes) > --max-scene-mb 700` |
| group | `polarization=VV+VH, beam=W3, orbit=ascending:98, applied_lut=Constant-beta` |
| gaps | 96 and 96 calendar days (8294405 s and 8294399 s elapsed) |
| sizes | 355, 395, 395 MB; 1145 MB total |
| min pairwise overlap | 99.6449 % of the smaller footprint |
| common intersection | 1436530.2265 ha (geodesic), 1442733.7318 ha (equal-area) |

The 5497 MB SLC frames are never candidates under that cap; nothing about their
polarization, beam, orbit or LUT influences the outcome. The three size
rejections are listed in `rejected_items` with the measured size and the field
the size came from.

The same catalog with `--max-scene-mb 6000 --min-gap-days 300` instead selects
the SLC trio (2014-05-06 / 2015-04-07 / 2016-04-01, gaps 336 and 360 calendar
days, `applied_lut=Land`, `beam=XF0W2`, `polarization=HH`).

`--count 2` on the same catalog with `--max-scene-mb 700` selects
2009-11-16 / 2010-02-20 rather than 2009-08-12 / 2009-11-16: the two later scenes
share 99.92 % of the smaller footprint against 99.64 % for the pair that involves
the narrower first frame, and ranking term 1 is the larger minimum pairwise
overlap. This is a consequence of the stated ordering, not a preference for
later dates; term 3 prefers the shorter span and would decide the opposite way
if term 1 were tied.

## What this module does not do

* No network access, authentication, token reading, or product download, and no
  other acquisition path.
* No cause is attributed to anything. There is no deforestation, fire, logging
  or other class output, and no confidence, probability or risk score; the
  overlap percent is a measured geometric fraction and nothing more.
* No radiometric calibration, geocoding, terrain correction, speckle filtering
  or change detection.
* No real RADARSAT-2 product is needed, present or opened by this module.

## Tests

```bash
python -m pytest tests/catalog/test_select.py
```

`tests/catalog/test_select.py` embeds the six published public items verbatim
(geometry, properties, assets and public links, pinned by SHA-256) and uses them
for the size-filter, calendar-gap, overlap and sanitization regressions, checking
the measured overlap against the figures published in
`research/catalog-scene-selection/candidate-pairs.json` at `dd44fa1`. Every other
fixture in that file is generated inside the test, carries a
`SYNTHETIC-TEST-FIXTURE` marker and a `synthetic-` id prefix, and exists only in
the test process: antimeridian and unsupported geometry, signed hrefs and
credential-named properties, straddling-midnight dates, partial overlaps,
unknown LUT and unknown size, mixed polarization, deterministic tie-breaks, and
the guarantee that the module imports no network or credential client.