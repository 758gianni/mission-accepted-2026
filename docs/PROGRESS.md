# Goal and progress

Updated: 2026-10-03 12:43 UTC. This is a living milestone ledger. Start a takeover with [TAKEOVER.md](TAKEOVER.md); re-query workers and remote heads before acting. Update these documents after meaningful results, reviews, fixes, integration, downloads and scientific validation.

## Goal

Deliver a reliable five-minute presentation dashboard using real RADARSAT-2 Tropical Forest acquisitions: calibrated and aligned scenes → quantified change polygons → read-only API → interactive map, imagery, dates and explanation. At least two real acquisitions and a validated preselected real region are required. Human teammates own frontend; the lead owns acquisition, processing, backend, coordination and validation.

## Current goal ledger

| Goal | Verified progress | Gate remaining |
| --- | --- | --- |
| Repository, isolation and worker orchestration | Correct private repository; original gitignore pushed; isolated worker branches; retained source recovery | Keep checking teammates' advancing commits and worker state |
| Public dataset selection | Three overlapping Sabah scenes dated 2009-08-12, 2009-11-16, 2010-02-20; approximately 1.145 GB | Actual product properties and useful changes are unverified |
| Safe reproducible acquisition | eb692bc independently approved: 97 tests passed, one setup-only skip, 14 reviewer probes; local bootstrap and doctor pass | Lead rejected interruption-time signed-URL persistence; owner correction and re-review required. Local full-suite scan stalled and needs bounded diagnosis |
| Product inventory | b3fb1ecd independently approved and merged at59869bc2; 46 lead tests pass | Run on actual products when downloaded |
| Prepared-pair producer | Published8027d7bd; 63 independent component tests pass; publication concurrency probes pass | Reviewer requests correction of stale scientific method description; exact new-head approval |
| Read-only API | Publishedb88dd5b7; 308 independently rerun tests pass; epoch URLs and generation containment implemented | Final exact-head review |
| Prepared-data producer/API integration | Latest b88dd5b7/8027d7bd/eda4c6fb composition: 401 lead tests passed, one dependency warning, 9.37 seconds | Exact-head independent reviews and integration merge still required |
| Calibration and registration | Corrective calibration4fb2b713 found on VM; registration2dcd157c published | Verify source/report durability and obtain final scientific approval; no actual product validation |
| Frontend handoff | Contract7351140a published; real URLs, bounds and epoch409 semantics documented | Final review; give concrete real bundle to human frontend owners |
| Real-data vertical slice | Not achieved | No real product bytes locally; raw-to-prepared route must follow reconnaissance |
| Presentation dashboard and example | Not achieved | Teammates' dashboard, validated real region and deterministic rehearsal |

Do not describe component tests as a completed real-data demo. No percentage completion is claimed.

## Current work and ownership

SwarmForge team: forest-change-20261002. The latest inventory has 43 workers: four previously destroyed, others retained. Snapshots may become stale. Preserve exact source and artifacts before cleanup; no retained VM is currently being destroyed.

- /root/recover_swarm_state coordinates the existing acquisition owner, local test diagnosis and interruption-safe redaction.
- /root/api_corrections supplies verified exact source bundles and supervises the integration owner's composition run468f02bd-f207-4e83-bd0b-b7d726e145b6.
- /root/recover_producer coordinates producer/API final reviews and calibration source/report recovery.
- Existing SwarmForge specialists continue calibration, catalog, contract, registration and ancillary validation work without competing file ownership.
- Main/frontend remain human-owned. The lead publishes to forestwatch/offline-recovery-20261003.

Exact worker IDs, heads, report recovery rules and acceptance criteria are in [TAKEOVER.md](TAKEOVER.md). Earlier detailed recovery history remains in [OFFLINE-HANDOFF.md](OFFLINE-HANDOFF.md).

## Next actions

1. Resolve actual final-review findings with existing owners, preserve source, and review new exact heads.
2. Verify current producer/API/integration composition and integrate approved changes.
3. Finish interruption-safe acquisition, verify bootstrap and give the user the exact interactive download command. Only the user handles real credentials.
4. Inspect actual XML/LUTs/rasters in parallel; determine minimum evidence-supported calibration/geocoding/registration.
5. Establish and validate a real-data bundle before advanced features.
6. Supply concrete API/data needs to frontend teammates, validate a clear example region and rehearse the five-minute demo.

## Scientific and credential limits

No unsupported cause classifications or confidence percentages. Priority is a disclosed magnitude/area index. Two acquisitions do not establish later persistence or historical anomaly. Matching grids do not establish measured registration. Catalog metadata, ancillary forest-loss proxies and synthetic tests do not establish real SAR change validity.

Do not authenticate as an agent. Do not put credentials or tokens in chat, prompts, logs, files or configuration. The signed-URL interruption finding is being corrected because it conflicts with this requirement.

## Latest evidence checkpoint

Lead independently exported exact APIb88, producer8027 and integrationeda4 with frozen setup237 into /tmp/forestwatch-checkpoint-401. Command: uv run --frozen pytest tests/integration tests/backend tests/processing/test_change.py -q. Result: 401 passed, one Starlette/httpx dependency warning, 9.37 seconds. This validates prepared synthetic rasters -> producer bundle -> API on these exact heads, not real RADARSAT-2 preprocessing or a validated change event.

The acquisition coordinator reproduced transient raw-output and manifest-token persistence with static sentinels and no authentication. Owner run0290d1f6-e309-40cc-9a80-dc6b21ff2593 corrects persistence before redaction and real-home test reads. Reviewer amendment9dd99998-0757-40b3-b922-4c3a461af22f accounts for the stricter user requirement.

Producer metadata correction is now published at4ada2a357f7e55b05973043bebbd7c3b52684734; 64 tests passed, existing reviewer re-reviewa013dd63 pending. This new head has not yet been used in the lead's 401-test checkpoint, which belongs to8027d7bd. Calibration4fb2b713 is remotely verified and independently passed57tests; existing scientific re-review758b92f1 remains pending. No approval or additional implementation merge is implied.

API exact-head reviewer now requests narrow documentation corrections (mandatory Pillow, supported versioned bundle pointers);308 owned tests and179 independent probes pass, one platform-specific probe skipped. Implementation and new-head re-review remain with existing owner/reviewer. Integrationeda4 owner report is complete and durable; existing reviewer continuation28e83247 is queued. Live team snapshot:43 workers,8running,25completed,3recovery_required,3failed,4previouslydestroyed. Controller states can lag actual execution.
