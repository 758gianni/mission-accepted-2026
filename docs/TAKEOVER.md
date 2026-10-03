# Takeover guide and goal ledger

Updated: 2026-10-03. Read this first, then [PROGRESS.md](PROGRESS.md) and [STATUS-2026-10-03.md](STATUS-2026-10-03.md). These documents must be updated after meaningful evidence, fixes, reviews, integration, downloads and validation. They are snapshots; query live workers and exact remote heads before acting.

## Mission and authority

Deliver a reliable five-minute dashboard demonstration using real RADARSAT-2 Tropical Forest acquisitions: calibrated/aligned scenes -> radiometric change -> spatial regions -> hectares/magnitude/dates/transparent priority -> read-only API -> interactive visual VAP. At least two real temporal acquisitions, quantified regions and a validated preselected real example are required. Avoid cause classifications and invented confidence percentages. Start with defensible simple methods; no ML without actual-data evidence.

The user explicitly authorizes SwarmForge delegation, reviews, useful integration, worker recovery and isolated branch publication. They request active worker management and durable goal/progress documentation. Human teammates own frontend: do not edit it until authorized. They will provide needed frontend work and perform required EODMS credential actions themselves. Do not send communications to other people. No EODMS password/token in chat, git, prompts, logs, artifacts or saved config. Do not authenticate as an agent. Do not repurpose HOME, USERPROFILE or CODEX_HOME.

## Ordered goal ledger

- [x] Inspect correct repository and SwarmForge capabilities; repair repository/App mismatch.
- [x] Add/push original gitignore and establish isolated backend ownership/interfaces.
- [x] Public catalog reconnaissance and primary temporal-series selection.
- [x] Implement, independently approve and integrate read-only product inventory.
- [x] Verify a synthetic prepared-pair -> producer bundle -> API checkpoint: 262 tests passed at producer1873426b/APIa3f94b3/integration49ac9ff9.
- [ ] Review/fix latest producer/API publication compatibility and scientific correctness; earlier green checkpoint is not latest-head approval.
- [ ] Finish safe reproducible EODMS acquisition wrapper and independent security review.
- [ ] Give user exact safe interactive download command; acquire actual scene files.
- [ ] Parallel actual dataset reconnaissance: file/product structure, dates, footprints/overlap, CRS, polarization, mode, dimensions/spacing, calibration and minimum preprocessing.
- [ ] Implement the minimum evidence-supported raw-to-prepared route and establish the real-data vertical slice.
- [ ] Validate registration/radiometry/speckle/water/terrain/seasonal artifacts; assess threshold usefulness.
- [ ] Supply reviewed API/imagery contract and real result bundle to human frontend owners.
- [ ] Validate/select a particularly clear real region; complete deterministic dashboard presentation path.
- [ ] Independently review science, challenge compliance, backend, UX, performance and demo; rehearse five-minute live presentation.

No percentage completion is asserted: component tests, independent approval, integration and real-data demonstration are separate gates.

## Repository and durable branches

Correct private remote: https://github.com/758gianni/mission-accepted-2026.git.

User checkout: /home/overlord/hackathon/mission-accepted-2026, main. Preserve untracked .agents/ and data/ and teammates' changes. Latest fetched main b0217815e6adcdc0c4e411e1a8a4eb58d2a8232e contains human React/Tailwind/TypeScript work; main can advance.

Lead integration worktree: /tmp/forestwatch-recovery, branch forestwatch/offline-recovery-20261003. Initial setup/design branch forestwatch/vertical-slice-20261002 is published at237d45ea506dc17d3b348f93b7cc13dfa5801ff3. Recovery docs and approved inventory are on the newer recovery branch. Inventory merge59869bc2b95c9c11962ba2361f564fe83ba67578 is followed by this handoff's publication. Fetch remote recovery branch for its actual current tip.

Temporary directories disappeared once when the lead environment restarted. Remote branches are durable; /tmp paths are convenience only. Rebuild missing worktrees from the published recovery branch. Do not rely on functions.store surviving a restart. Preserve useful worker artifacts and verify source publication before VM destruction; no retained VM cleanup is currently requested.

## Current priorities and evidence

1. **Acquisition is the real-data blocker.** Published ad01dabf063df22491d002db091a8ed4ff0a22bf has72 tests reported but independent review requested changes: dirty executable trees pass HEAD-only pins, selection limit is not enforced locally. Lead recovery additionally reproduced real Click SystemExit ending a multi-UUID loop after its first success and found signed-URL sanitization absent. Reviewer disagreements must be resolved by actual real-Click probes, not mocks. Owner is actively fixing these in run8a4e6680-5491-46f2-9029-a8923edd8ada; integrity/redaction/new regression files were dirty at last check. Do not issue credentials yet.
2. **Latest producer/API combined gate is red.** Producer50f4b48379700c85a10f18f78db397ccf98bacac has59 tests independently re-run, but its atomic-generation symlink is rejected by API66cb7af51cf0037e6c752ca96d56a4197d7b6f12. Lead combined run stopped after two failures, APIerror/503. Owners are coordinating supported sibling pointers, generation-pinned reads, unique immutable generations and no automatic pruning. API stale imagery requests now have a proposed analysis_id query/409 behavior; document/review actual final implementation before giving frontend instructions.
3. **API correctness fixes progressed.** Published66cb7af5 has268 worker tests; recovery probes verify overflow1e400 rejects globally, CORS preflight works, malformed/filter/zlib-truncated PNGs reject, excessive-pixel decoder bombs are controlled. Current compatibility run de71fe6c-64ee-4aab-b50b-04510ffbec4d is executing; one queued hot-read containment followup964e887e-2224-4333-bb84-2b0b3d13b8fa covers a pinned generation replaced with an outside symlink returning outside PNG bytes. No final approval yet.
4. **Inventory is approved and merged.** Exact b3fb1ecdcce909b686a1b2badeaa8eb122d00a73 approved by reviewerw-8038e552-88a7-4685-89f6-5c7f425b77ff, including116 independent probe assertions and mutation tests. Lead merged only its three owned files; integration re-run46 passed, six expected unreferenced-fixture warnings,1.24seconds.
5. **Calibration preflight is scientifically rejected.** 2aba9b2dd8cabf24edbd12ce4d51eb7396299e96 passes50 tests that encode wrong assumptions. Reviewer proved fabricated LUT schema, wrong driver names and raw/calibrated DN confusion. Do not integrate it. Its owner has corrective run111d259f-7ac3-46d1-bca6-b5bf84e95788. Required: real RS2 dataType/lookupTable element-text/positional gains schema; proper raw vs calibrated and complex gain semantics; path containment and honest unknowns.
6. **Catalog/contract auxiliary changes need review.** Catalog report05da39bf has validated selection but needs revision/origin metadata and non-vacuous verifier fixes (run8b88026b-64af-4ac4-ae3c-4ac93d4a8347). Selector16076a68 had42 tests but touching intersections, nonfinite size guards and sanitizer bypasses were reproduced; owner continuing. Dashboard handoff was corrected to10d5ae0 after earlier baseline/threshold errors but must be re-reviewed against final API.

## Data state and next user action

No actual RADARSAT-2 product bytes, real result bundle or validated change event exists locally.

Primary selected public product UUIDs:

- 4ff7a4b5-57b0-5cde-bd8b-300a6903c0e7 — 2009-08-12,355 MB.
- b8a8461b-0f00-5996-922e-76721012d178 — 2009-11-16,395 MB.
- 8178671e-0090-5be2-ad28-a176e4358b34 — 2010-02-20,395 MB.

Catalog SGF/W3, VV/VH, ascending, relative orbit98; dates96/96 calendar days apart. Three-way outline roughly118.133–119.490E,4.868–6.089N. Catalog pitch12.5m is not resolution. Frame overlap is not pixel-valid overlap or registration proof. LUT label Constant-beta is opaque.

Public collection: https://www.eodms-sgdot.nrcan-rncan.gc.ca/search/collections/Radarsat-2_Tropical_Forest_Products . Search is credential-free; downloads require user authentication. Research catalog branch contains research/catalog-scene-selection/selected-scenes-primary.geojson (three products only) and selected-scenes.geojson (six including huge alternative SLC scenes). Never download the whole six-item file accidentally. Primary-only file is restored under /tmp/forestwatch-recovery/data/reports/acquisition/selected-scenes.primary.geojson; regenerate from catalog commit if absent.

A candidate~30km crop at118.245–118.515E,5.445–5.715N lies inside overlap. Ancillary land/DEM screening does not establish forest cover or exclude estuaries/low coastal water; prefer a smaller initial window if actual processing memory/terrain evidence warrants it. AOI artifacts remain on workerw-9ef29711-caba-4425-b0b9-34c2f73b3d27.

The planned user-only command is NOT READY until final acquisition source is reviewed, integrated and bootstrap verified: .tools/eodms-cli/.venv/bin/python -m acquisition download --collection Radarsat-2_Tropical_Forest_Products --scenes data/reports/acquisition/selected-scenes.primary.geojson --output-dir data/raw . Give the user the actual exact command and working directory once ready, with interactive password entry and no persistent credential storage. Automate everything unrelated to their credentials. User permits downloads anywhere.

Pinned CLI eodms-sgdot/eodms-cli464b94920e7faf28c84a6d31229ef0b2828a1479; eodms-pyec373705727dd50a3e80d092356f43e5c5f0e075; py-eodms-rapi22aa6348a120da98c1122057a245a1ba3100b759. Do not use upstream configure, which saves a base64 password. Bootstrap is credential-free and independently pinned; safe wrapper still requires review.

## Scientific rulings to preserve

Raw detected Mag DN is amplitude: GDAL detected calibration computes(DN²+offset)/gain. A GDAL RADARSAT_2_CALIB:SIGMA0 detected output is already linear power; do not square it again. Complex output requires exact driver representation/gain inspection, then magnitude-squared where appropriate; do not omit gain or assume the detected offset rule applies. Real LUTs have positional gains, not fabricated polarization/gain-width records.

Driver RS2 is RADARSAT-2. RCM is separate RADARSAT Constellation. SGF is a product label, not a GDAL driver, ScanSAR assumption, CRS or proof of geocoding. SLC is already focused; deburst is not universally required. GDAL GCP half-pixel adjustment must not be added again. Rasterio reproject supports GCP/RPC and documented transformer options. Scientific reviewers have also made errors: verify their claims against installed code/primary evidence.

Grid alignment is not measured co-registration. Producer manifest calibration/geocoding/registration flags are upstream attestations, not measurements performed by change.py. Median absolute region change is distinct from signed median: mixed-sign regions may have signed median near zero despite valid thresholded magnitude. Priority currently magnitude*sqrt(hectares), a disclosed index, not confidence or cause. baseline-relative delta0 is mathematically valid, not invented historical stability. With two dates, later persistence and historical anomaly are unavailable; event onset is an interval, not exactly detected_at.

## Interfaces and verification

Producer bundle schema1: analysis.json, regions.geojson, before.png, after.png, change.png with common WGS84 bounds, plus change.tif/mask.tif. Quantified metrics include changed/valid/not-evaluable/analysis hectares. Region fields include area, signed median change_db, median-absolute magnitude_db, baseline_at, detected_at, observation_interval, priority formula/units, persistence unavailable values, historical anomaly null, explanation and time series. demo_region_id remains null until a real region is validated.

API health/status, analysis, GeoJSON regions, region selection and declared imagery URLs. Missing inputs must yield truthful waiting state; invalid bundles error; no fabricated presentation defaults. Frontend should use declared imagery URLs, not construct or guess new routes. Generation-pointer/epoch changes are in progress; recheck final contract and add exact frontend requests after review.

Lead commands: uv sync --frozen; uv run --frozen pytest tests/processing/test_inventory.py -q (merged inventory). Composite temporary tree /tmp/forestwatch-composed was assembled by git archive of exact source heads, not branch merges. Earlier262 green and latesttwo red must remain separately attributed. Component tests alone do not approve a merge. All changes need exact-head independent review; re-run meaningful combined tests after interface changes.

## Worker operations and recovery

Team forest-change-20261002. Use SwarmForge list_workers pagination/get_worker/get_worker_result/artifacts and current using-swarmforge skill at .agents/skills/using-swarmforge/SKILL.md. Worker branch convention swarmforge/forest-change-20261002/TASK/WORKER_ID. One owner per file scope; do not duplicate active coders. Read-only reviewers never fix source.

Provider-error strings, stale token counters and pending_messages1 are sometimes misleading. Actual current execution was confirmed by readonly OpenCode SQLite assistant parent IDs matching the current dispatch message. Inspect metadata, never dump prompts, infrastructure config or worker records containing server passwords.

Follow-up result.json sometimes keeps an initial run ID or null. After actual final assistant completion, independently verify clean exact commit, final report and test evidence, then repair ONLY run_id to the actual current dispatch UUID returned by send_worker_message. Never manufacture a result or reuse an older dispatch. Publication is separate: create/verify git bundle, push exact owned branch non-force with existing authorized host GitHub access and verify ls-remote. Established VMs lack private runtime fetch credentials; use initial refs or a supplied bundle, never give credentials to workers.

Remote source is durable; artifacts disappear on VM destruction. Get artifact ranges <=32KiB and explicitly read resource bytes without printing base64. Keep useful reports before cleanup. Several failed reviewers have retained reports despite invalid structured result records. No cleanup is authorized as the current priority.

Native lead recovery coordinators: /root/recover_swarm_state (acquisition), /root/api_corrections (API), /root/recover_producer (producer; final recovery completed, later continuation managed by lead). Their messages contain current evidence; if unavailable, inspect SwarmForge/VM state directly. Existing infrastructure credentials may be loaded only for authorized provider operations without disclosure, never sent to EODMS workers or artifacts.

## Worker snapshot

States below are snapshots and may lag real execution. Re-query before dispatch or cleanup. Four destroyed workers are earlier reconnaissance, already preserved; all others remain retained.

| Task | Worker | Role | Snapshot state |
| --- | --- | --- | --- |
| eodms-acquisition-recon | w-852c055f-caa6-4beb-815c-4f572750f79e | researcher | destroyed |
| sar-science-recon | w-654d852d-05ed-419f-b364-069713220323 | researcher | destroyed |
| dashboard-science-review | w-8093558c-8b91-4c1b-a2d8-f43419432e14 | reviewer | destroyed |
| repository-checkout-smoke | w-5e9b09b9-9222-4292-b7a8-e6fe9070a268 | reviewer | destroyed |
| acquisition-cli | w-544f8b0e-1b02-45a0-a6dd-4aa76c2bf1f2 | coder | running |
| catalog-scene-selection | w-52812be0-5278-4df1-98d8-affa52949791 | researcher | running |
| raw-product-inventory | w-afc4b51c-3712-4881-a287-074041a72204 | coder | recovery_required |
| backend-api | w-637aa6d9-c6aa-4745-aca8-375d93277e9f | coder | running |
| scientific-gates | w-9c0c6601-04c7-480f-8585-437239e143af | reviewer | completed |
| prepared-pair-change | w-2fe278d1-50ee-4dad-903f-524abbe93fa3 | coder | running |
| integration-review | w-29423311-eb9e-4396-8f6f-dba7365b7e63 | reviewer | completed |
| review-raw-product-inventory | w-f0c2bebe-ff3a-47fc-bc2d-59ef328f6e0a | reviewer | completed |
| review-catalog-scene-selection | w-5e2051bf-59c0-4207-a559-42f7d5a5232a | reviewer | completed |
| review-backend-api | w-35c7f944-65f1-4af2-bffa-41c45f95c106 | reviewer | failed |
| review-acquisition-security | w-ed15e6dd-61cd-4ce1-a439-5ed249791cd7 | reviewer | completed |
| sgf-preprocessing-route | w-69e73c70-ee18-4473-8b83-986b8b762275 | researcher | completed |
| review-prepared-pair-change | w-7797c47f-9903-45b0-bbb7-70e3b15b2ae0 | reviewer | completed |
| offline-bundle-api-tests | w-aa748747-0c64-4955-96b5-f443faab8929 | coder | completed |
| offline-registration-diagnostics | w-47286095-410b-4a35-bb21-ac93030eebdc | coder | completed |
| offline-calibration-preflight | w-e638ceed-e7d9-40e5-8c55-34ebdb5a146f | coder | running |
| offline-catalog-selector | w-c929c0c3-f4a3-4350-a1fb-0ea93bc5b793 | coder | running |
| offline-dashboard-contract | w-d369b818-8114-4354-9a4c-d1541c52cae5 | coder | completed |
| offline-demo-runbook | w-f424f4c0-d0a7-4edb-a697-6d82775a662b | coder | completed |
| offline-pipeline-performance | w-d21af2d9-df20-4bce-9f26-3eb99c03a2c1 | reviewer | completed |
| offline-bundle-demo-resilience | w-1938ff4f-8448-45e9-a5d1-122eac2cd4cd | reviewer | failed |
| offline-rs2-driver-experiment | w-54ae8156-d99a-4635-8369-cd28fe7d7e12 | researcher | completed |
| offline-aoi-context | w-9ef29711-caba-4425-b0b9-34c2f73b3d27 | researcher | completed |
| offline-reproducibility-review | w-19d3763b-108c-4ace-bec5-b0429b72f6af | reviewer | completed |
| offline-challenge-compliance | w-9d6161a5-5923-4263-8224-054009dbc5ec | reviewer | completed |
| review-api-final-recovery | w-57357621-0246-476a-9f8d-0040d87d0a94 | reviewer | completed |
| review-registration-offline | w-54bf1c45-5514-4f1b-b2fd-f65fdf677866 | reviewer | completed |
| review-selector-offline | w-4574e0d6-398c-43b6-bf65-bb74e18b2a2b | reviewer | completed |
| review-integration-suite-offline | w-3b91da88-05e9-4558-a670-a9a60137e55a | reviewer | failed |
| review-dashboard-handoff-offline | w-359ba2f9-93e1-4136-bc8f-3ed0139fdda7 | reviewer | completed |
| review-demo-runbook-offline | w-1489e4f2-838a-4205-9f7f-328eeb1186c0 | reviewer | failed |
| review-acquisition-final | w-0362d214-3350-4404-b698-999ff78beb5b | reviewer | completed |
| review-inventory-final | w-8038e552-88a7-4685-89f6-5c7f425b77ff | reviewer | completed |
| review-calibration-preflight-science | w-ba6ffc8d-37e3-4de6-aa6a-0fd4481a2dba | reviewer | completed |
| review-catalog-report-final | w-cf66d38d-ad88-4d0d-84c4-49cd1a6cbf9c | reviewer | completed |

## Next takeover actions

Collect current acquisition/API/producer final heads and reports first. Give independent reviewers the new exact heads, including real Click credentials-boundary probes and producer/API generation compatibility tests. Correct current source before merging. Finish catalog/selector/registration/contract reviews only as needed for the vertical slice. Keep calibration-preflight rejected until actual schema/science passes. Request the user-only EODMS action as soon as safe, then prioritize real scenes over further generic framework features.
