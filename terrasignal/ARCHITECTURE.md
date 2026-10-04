# TerraSignal: RS2-first, multi-source intelligence

The frontend is frozen at `b1feadd`. The Myanmar experiment is the numerical
reference, not the extent of the product. No raw archives are copied to workers.
New imagery downloads belong under `/mnt/d/TerraSignal/raw/`; existing verified
archives remain referenced in place.

## Data boundary

`AOI → observations → sensor/geometry strata → spatial tiles → dated stacks →
anomaly products → ranked geographic candidates → investigation evidence`.

`Observation` normalizes sensor, timestamp, footprint, CRS, resolution,
polarization/bands, processing level, quality flags, provenance and local assets.
Measurements remain sensor-specific. An RS2 sigma0 power stack is never silently
mixed with S1 radar or optical reflectance. RS2 Tropical Forests generates the
hackathon candidates; independent SAR/optical observations are attached as
supporting evidence, not blended into invented confidence percentages.

## Reuse audit

- `pipeline.py`: reuse Sigma Nought gain semantics `(real² + imag²)/gain²`,
  validity checks, 90% valid multilook support, dB transforms, three-date
  return/persistence rules, connected regions and geographic area metrics.
- `pipeline_4date.py`: reuse the per-region validation results and distinguish
  **9 strong** (`>=2.5 dB`) from **2 moderate** (`>=1.5 dB`) corroborations.
  T4 (December 21) is chronologically before T3 (January 7); do not sort by label.
- `terrain_analysis.py`: reuse DEM cache, slope and geometric risk calculations,
  and disclosed ranking `magnitude × sqrt(area) × (1 − risk_fraction)`.
- `eodms_orders.py`: reuse stable order/record tracking, CRC verification,
  streamed downloads and uncertain-submission refusal. Planner never submits
  orders. Its selection is a bounded proposal with explicit rejection reasons.
- Existing GCP-geocoded rasters are **not** DEM terrain-corrected. Terrain QA
  reduces one risk; it is not proof of land-cover change or registration accuracy.

## Storage and incremental processing

| Tier | Retained product | Retention/provenance |
|---|---|---|
| RAW | Original SLC ZIP, size, hash/CRC journal, source record ID | Immutable, central archive; never frontend assets |
| ANALYSIS-READY | Float32 calibrated power tiles, validity, spatial transform, processing recipe | One acquisition per tile; deterministic content/recipe key |
| TEMPORAL | N-date summaries, class masks, anomaly magnitude, observation counts | Recompute only tiles touched by new observations |
| CANDIDATE | GeoJSON, metrics, date trajectories, selected crops | Tiny investigation contract; links to originating observations |
| SUPPORTING | Geographic DEM/slope once per grid; look-dependent risk per geometry | Reuse slope/elevation; do not reuse look-dependent masks across unlike passes |

Lossless tiled GeoTIFFs are the first implementation: existing tools already
read/write them. Grid IDs include CRS, pixel size, chunk size and integer X/Y;
UTM zones have separate grids. Chunk dimensions are benchmarked at 512/1024/2048.
Within each grid, matching source/date fragments are mosaicked deterministically
and counted as **one temporal observation**, not independent validation scenes.
Missing data stays missing; timestamps and observation counts survive reduction.

Keep raw and the minimal calibrated tile representation. Native/GCP intermediates
and repeated presentation PNGs are regenerable; report their bytes separately,
never delete the prototype outputs as part of this phase. Tiles and manifests are
published atomically. A cache hit requires matching recipe/source signatures;
file existence alone is insufficient.

## Planner

Catalogue query is read-only, filtered, paginated and date-partitioned. RAPI
`firstResult` is **1-based**; a search exposes at most 2,000 records even when
`hitCount` is larger. Per-window returned count must agree with hit count.
Metadata is allowlisted; credentials, URLs and signed download links are never
serialized. Missing orbit/look/beam metadata cannot establish comparability.

Geometry strata include sensor, measurement, beam, polarization, orbit direction,
look direction, relative orbit, product type and incidence range. Scene selection
scores marginal distinct-date spatial support on 0.25° sample cells, constrained
by new-scene budget and orderability. Reported coverage is polygon-intersection
area, not valid-pixel coverage. Spatial selection is heuristic, not optimal.

Natural Earth 1:110m country boundaries provide reproducible coarse national
AOIs. A user-supplied WGS84 GeoJSON replaces this boundary for precise work.

## Numerical and scientific gates

- Match prototype arithmetic on real strips before enabling CUDA. Report
  resident-device kernel time, host-transfer-inclusive time, disk read time and
  end-to-end time independently. CUDA may lose on small transfers.
- Generalize the interpretable baseline to dated N-observation stacks with
  per-pixel missingness, magnitude, robust history, variance, persistence,
  return-to-baseline and late/progressive behavior. Three dates cannot establish
  recurring annual seasonality; label seasonal/transient as a behavior candidate.
- Registration quality is a measurement to carry into candidate provenance;
  equal grids are not registration. Unregistered expanded areas are exploratory.
- Candidate objects carry geometry, area, first/latest timestamps, magnitude,
  class, supporting observation count, terrain risk, priority and explicit
  validation evidence. Cross-tile candidates need boundary reconciliation before
  country-wide region counts are claimed.
- Country availability does not imply country processing completion. Report
  queried/selected/processed footprints and bytes separately.

## Phase order

1. Metadata/model and bounded national acquisition plan.
2. Real-strip CPU/CUDA profiling; retain CPU where measured end-to-end is faster.
3. Minimal tiled representation from existing five scenes, with source manifests.
4. N-date temporal/candidate prototype and stage-by-stage measured bytes.
5. Expand a selected compatible regional subset only after reporting this evidence.

No frontend redesign, giant swarm, production scheduler or opaque ML is required.
