# TerraSignal implementation plan — evidence-based revision

**Goal:** Establish a reusable, multi-source metadata model and demonstrate
RS2-first planning → compact tiled observations → interpretable N-date candidates.

**Spec:** `terrasignal/ARCHITECTURE.md` and the user's large-area/multi-source brief.
Inline execution; isolated branch `terrasignal/planner-proof`. UI remains frozen.
New imagery downloads: `/mnt/d/TerraSignal/raw/`. No raw scenes in workers.

## Corrections established during live verification

- Use `terrasignal` with one `s`.
- Real prototype relative orbit is **256**, not 98.
- RAPI search returns metadata pairs (`Date`, `Type`, `Position`) and full records
  use different labels (`Start Date`, `Product Type`). Normalize actual variants.
- Search is 1-based, has a 2,000-record window limit, and exposes a hit count.
  Partition by date and compare counts before declaring a complete inventory.
- Multiple product size values may be identical or conflicting. Deduplicate
  identical values; preserve unknown estimates when values conflict.
- Do not optimize national acquisition cadence to mimic one Region-2 sequence.
  Prioritize useful unique-date spatial support with explicit rejection reasons.
- No fixture-only transpose test: orientation tests must call real temporal code.

## Phase 1 — model and planner

- [x] `terrasignal/planner/models.py`: observation metadata and conservative geometry strata.
- [x] `rs2_adapter.py`: parse actual RAPI schema, preserve unknowns and provenance.
- [x] `eodms.py`: read-only paginated, date-partitioned search; normalize safe fields only.
- [x] `coverage.py`: budgeted spatial/temporal selection and footprint coverage report.
- [x] `cli.py`: AOI/country input, catalogue replay, scope filters, local CRC-journal reuse.
- [x] Regression tests: sensor/orbit separation, duplicate date, missing geometry,
  deduplicated IDs, budget, one-based pagination, repeated catalogue sizes.
- [ ] Run opt-in live pair test and complete Myanmar national availability query.
- [ ] Publish planner checkpoint and coverage evidence; do not order the entire catalogue.

Commands:

```bash
python -m pytest terrasignal/tests/test_planner -q
TERRASIGNAL_LIVE=1 python -m pytest terrasignal/tests/test_planner/test_live_rapi.py -q
python -m terrasignal.planner.cli --country Myanmar --beam XF0W2 \
  --env-file /path/to/local/.env --out results/myanmar --limit 5000 --budget 12
```

## Phase 2 — profile and reduced representation

- [ ] Preserve prototype code; reuse its power/calibration/validity arithmetic.
- [ ] `terrasignal/numerics.py`: array-backend reductions matching prototype outputs.
- [ ] `terrasignal/benchmark.py`: real raw strips at 512/1024/2048, warmups,
  CPU/device/transfer-inclusive medians, throughput, GPU memory and read time.
- [ ] Numeric tests include invalid/saturated pixels, block borders and GPU parity.
- [ ] `terrasignal/tiles.py`: deterministic CRS/resolution/chunk/X/Y spatial keys,
  float32 power + validity, dated manifest, source and recipe signatures.
- [ ] Reuse existing native/GCP products, central raw references and DEM products;
  publish atomically and verify idempotency. No destructive intermediate cleanup.
- [ ] Measure RAW → ARD → temporal → candidate bytes and actual reduction ratios.

## Phase 3 — N dates and geographic catalogue

- [ ] Chronological unique-date stacks; same-date fragments are not independent validation.
- [ ] Robust history, observation count, variance, signed magnitude, persistence,
  return-to-baseline, late/progressive/stable classification with explicit thresholds.
- [ ] Missing data is not stable. Sparse cadence and unregistered expansion are QA flags.
- [ ] Actual code test: asymmetric seasonal band + persistent block catches transpose errors.
- [ ] Tile-wise spatial extraction, geographic areas, timestamps, source links,
  magnitude, terrain risk, priority and explicit validation evidence.
- [ ] Identify tile-edge fragments instead of inventing reconciled national region counts.
- [ ] Process the larger five-scene footprint; report actual tile/observation coverage.
- [ ] Full checks, independent derived-data/source review if useful, and GitHub push.

## Completion gate

Report national availability and selected plan separately from processed footprint.
Include observed bytes, CPU and CUDA times, actual speedup (including transfer),
tile count, temporal classes, remaining scientific limits and next-phase recommendation.
Never label a complete catalogue search as a complete country analysis.
