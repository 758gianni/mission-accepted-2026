# Goal and progress

Updated: 2026-10-03, after offline result recovery. Lead-owned living status; update after meaningful worker results, reviews, integration, dataset acquisition and validation. Record evidence and blockers rather than speculative completion percentages.

## Goal

Deliver a scientifically defensible, locally runnable RADARSAT-2 Tropical Forest land-change demo: real temporally separated acquisitions -> calibrated/aligned rasters -> detected polygons with hectares, magnitude and temporal context -> API -> polished interactive presentation dashboard. Human teammates own frontend; this lead owns acquisition, geospatial analysis, backend, coordination and validation. The presentation must reliably show a preselected validated real region without terminal navigation.

## Verified progress

| Milestone | State | Evidence / remaining work |
| --- | --- | --- |
| Repository and worker setup | Established | Correct private repo configured; isolated worker branches; original gitignore pushed. |
| Public dataset reconnaissance | Initial selection established | Three overlapping Sabah SGF catalog scenes identified, 2009-08-12 / 2009-11-16 / 2010-02-20; actual delivered product properties unverified. |
| Reproducible acquisition | Implemented branch, security review pending | CLI wrapper checkpoint babc4bd8; signed-URL handling, multi-scene selection, limits and credential-boundary fixes remain outstanding. No agent authentication. |
| Product inventory | Implemented branch, fixes/review pending | Checkpoint 7c0faf33; placeholder georeferencing, JSON nodata, rotated spacing and real XML fields under review. |
| Prepared-pair change detection | Implemented branch, final review pending | Checkpoint b8d9059d; 47 lead-run tests passed on synthetic prepared rasters. This does not demonstrate raw-data preprocessing or real changes. |
| Read-only API | Updated branch, independent review pending | Recovered published a3f94b3; worker retained evidence of 184 passing tests after consistency fixes. Lead re-verification and PNG integrity fix pending. |
| Integrated real-data vertical slice | Not achieved | No real pixels/result bundle locally; no implementation branches merged yet. |
| Dashboard integration and preselected event | Pending | Frontend owned by teammates; backend contract and demo-runbook workers assigned. No validated real example region. |
| Live-demo readiness | Not achieved | Requires real-data vertical slice, scientific checks, reviewed integration and deterministic dashboard rehearsal. |

## Current worker management

Twelve additional offline work packages cover end-to-end tests, registration diagnostics, calibration preflight, catalog selection, dashboard contract, demo runbook, performance, resilience, empirical GDAL behavior, AOI context, reproducibility and compliance. Their VMs/artifacts survived a lead environment restart. Eighteen timed-out workers received bounded recovery instructions; latest check showed 17 running, seven completed, one failed and four previously destroyed. These are snapshots; consult SwarmForge for live states. No retained VM is being destroyed before artifact/source verification.

See [OFFLINE-HANDOFF.md](OFFLINE-HANDOFF.md) for worker IDs, checkpoints, review gates and recovery context. Recovery handoff is published on forestwatch/offline-recovery-20261003; main and teammates' frontend are unchanged by that branch.

## Next actions and blockers

Recovered commits are published for integration tests (49ac9ff9), registration diagnostics (93250adf), catalog selector (16076a68), dashboard contract (62952f75) and demo runbook (8755df1f). Six independent review workers are assigned these changes and the final API checkpoint. No approval or integration is implied by publication.

The actual producer-to-API integration tests exposed a blocker: relative_orbit is serialized as float by the producer but must be an integer in the API. The producer owner has a regression/fix task. Additional bounded follow-ups cover exact minimum-area filtering, raster-content identity, rotated-grid rejection and interrupted publication preserving the previous bundle. The API owner is checking truncated PNG acceptance. Synthetic fixture failures are useful evidence, not a demonstrated real land-change result.

Latest recovery inspection found clean unpublished VM commits: acquisition ad01dabf (72 tests reported), inventory b3fb1ecd (46 tests reported), catalog 05da39bf (422 checks reported), API PNG verification 679b7be6 (219 tests reported) and producer orbit fix 1873426b (52 tests reported). These are actual VM checkpoints, not approved or remotely verified deliverables yet. Recovery agents are preserving source and checking publication; final-head reviewers follow. The API PNG checkpoint still has reproduced decode gaps when optional Pillow is absent, so its larger passing suite is not sufficient evidence of correctness. See [STATUS-2026-10-03.md](STATUS-2026-10-03.md) for the detailed milestone assessment.

1. Recover current-run reports and exact published heads; preserve artifacts, independently review new implementations and remaining fixes.
2. Make acquisition credential handling reviewable and safe, then give the user the exact interactive authentication/download command. Authentication requires the user; unrelated work continues.
3. Download only the primary selected scenes, inspect actual products, and determine minimum calibration/geocoding/alignment from evidence.
4. Integrate approved backend branches and establish a bounded real-data vertical slice, checking speckle, geometry, registration, water/terrain and radiometric differences.
5. Supply actual results to teammates' dashboard, select a validated real region and rehearse the five-minute presentation.

## Scientific limits

No cause classifications or confidence percentages. Priority is a disclosed magnitude/area index. Two acquisitions cannot establish subsequent persistence or historical anomaly. Catalog metadata and synthetic tests do not prove real-data scientific validity. Every future status update must distinguish implementation, independent review, integration and real-data validation.
