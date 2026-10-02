# Backend decisions

## 2026-10-02: first acquisition target

Prefer the public catalog's three-date SGF series over Sabah, Malaysian Borneo:

| Acquisition (UTC) | Public item ID | Catalog size |
| --- | --- | --- |
| 2009-08-12 10:24:43 | `4ff7a4b5-57b0-5cde-bd8b-300a6903c0e7` | 355 MB |
| 2009-11-16 10:24:48 | `b8a8461b-0f00-5996-922e-76721012d178` | 395 MB |
| 2010-02-20 10:24:47 | `8178671e-0090-5be2-ad28-a176e4358b34` | 395 MB |

Catalog attributes agree: W3 / Wide3, VV VH, ascending, relative orbit 98, SGF, and 12.5 m sampled spacing. Independent review of the selection is pending. Footprint intersections cover at least 99.6% of the smaller outline in each pair. These are catalog outlines, not pixel-validity masks. Neither native spatial resolution nor radiometric comparability has been established from downloaded files.

The smaller products reduce acquisition and processing time compared with the selected Amazon SLC alternative, approximately 5.5 GB per scene. Start with two dates, retaining the third for subsequent persistence checks. A 20–40 km overlap window can bound initial computation after inspecting geolocation and terrain. No specific real-world change event has been identified yet.

Source: [EODMS Tropical Forest collection](https://www.eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products). Preserve selected public STAC records and selection evidence with the acquisition workflow.

## 2026-10-02: result semantics

- Two acquisitions measure an observed radar difference; they cannot establish persistence or exact physical onset.
- `baseline_at` names the reference acquisition without claiming that it was historically unchanged. `observation_interval` names the compared acquisition interval.
- `detected_at` names the acquisition where the radar difference is observed.
- Magnitude is median absolute pixel change in dB. Signed change is the median signed pixel change in dB. Priority is `magnitude_db * sqrt(area_ha)` and orders candidates for review.
- Persistence and historical anomaly remain unavailable until supported by subsequent and historical observations.
- Area totals must reconcile. Preview rasters share declared WGS84 bounds. Registration evidence remains separate from grid reprojection.
- SLC is already focused; preprocessing requirements depend on the actual product and mode. Inspect calibration output representation before calculating power.

The lead adjudicates independent findings against observed behavior and primary documentation. Unsupported generic preprocessing requirements and invented challenge requirements do not become product gates.
