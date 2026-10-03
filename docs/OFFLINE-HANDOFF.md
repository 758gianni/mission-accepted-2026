# Offline worker handoff and recovery

Snapshot: 2026-10-03. Team: forest-change-20261002.

The lead environment restarted and temporary files disappeared. SwarmForge VMs survived. Twelve additional offline workers produced retained artifacts, but their runs reached task deadlines before valid results/source publication were recorded. Eighteen failed or recovery-required workers have now received bounded recovery instructions: inspect retained work, preserve it, finish necessary reporting/publication, do not repeat completed research or expand scope. Keep all retained VMs until source and artifacts are independently verified.

Integration setup/design is published at 237d45ea506dc17d3b348f93b7cc13dfa5801ff3 on forestwatch/vertical-slice-20261002. Recovery lead worktree: /tmp/forestwatch-recovery, branch forestwatch/offline-recovery-20261003. No implementation branches have been merged. Human teammates own frontend; leave it untouched. Main may advance concurrently.

No real RADARSAT-2 pixels/result bundle or validated demonstration event are available yet. Catalog selection identified three overlapping Sabah SGF VV/VH W3 ascending relative-orbit-98 acquisitions (2009-08-12, 2009-11-16, 2010-02-20), approximately 1.145 GB total. Inspect actual metadata before choosing calibration/geocoding. User alone performs credential actions after exact safe CLI command is reviewed; workers must not authenticate or handle EODMS credentials.

## Resume gates

1. Collect current-run structured results and artifacts, verify exact remote branch/head and dirty state. Completed does not imply approved. Retained artifacts exist for integration tests, registration, calibration, selector, demo, performance, resilience, driver experiment, AOI, reproducibility and compliance.
2. Finish acquisition security review: signed-URL redaction, proper multi-UUID selection, limit semantics, runtime pins, restored defaults and real credential-boundary sentinel tests. Do not present the wrapper as download-ready yet.
3. Independently review final inventory/API/producer changes, then integrate exact approved heads and run meaningful produced-bundle/API tests. Producer b8d9059d had 47 passing lead tests, API87cfacb had139; final review remains pending.
4. Rebuild primary-only STAC selection from catalog branch; avoid downloading its large alternative SLC series. Obtain real files via reviewed CLI, inspect/calibrate/align a bounded crop, validate false positives, then select a real demo region.
5. Share stable actual API/imagery contract with human frontend owners; rehearse five-minute map -> region -> imagery -> quantified change -> timeline path.

## Retained task inventory

State is the pre-recovery snapshot. Failed/recovery-required rows subsequently received recovery dispatches. Four earlier reconnaissance VMs were already destroyed after evidence recovery.

| Task | Worker ID | Role | Snapshot state |
| --- | --- | --- | --- |
| eodms-acquisition-recon | w-852c055f-caa6-4beb-815c-4f572750f79e | researcher | destroyed |
| sar-science-recon | w-654d852d-05ed-419f-b364-069713220323 | researcher | destroyed |
| dashboard-science-review | w-8093558c-8b91-4c1b-a2d8-f43419432e14 | reviewer | destroyed |
| repository-checkout-smoke | w-5e9b09b9-9222-4292-b7a8-e6fe9070a268 | reviewer | destroyed |
| acquisition-cli | w-544f8b0e-1b02-45a0-a6dd-4aa76c2bf1f2 | coder | recovery_required |
| catalog-scene-selection | w-52812be0-5278-4df1-98d8-affa52949791 | researcher | recovery_required |
| raw-product-inventory | w-afc4b51c-3712-4881-a287-074041a72204 | coder | recovery_required |
| backend-api | w-637aa6d9-c6aa-4745-aca8-375d93277e9f | coder | recovery_required |
| scientific-gates | w-9c0c6601-04c7-480f-8585-437239e143af | reviewer | completed |
| prepared-pair-change | w-2fe278d1-50ee-4dad-903f-524abbe93fa3 | coder | completed |
| integration-review | w-29423311-eb9e-4396-8f6f-dba7365b7e63 | reviewer | completed |
| review-raw-product-inventory | w-f0c2bebe-ff3a-47fc-bc2d-59ef328f6e0a | reviewer | completed |
| review-catalog-scene-selection | w-5e2051bf-59c0-4207-a559-42f7d5a5232a | reviewer | completed |
| review-backend-api | w-35c7f944-65f1-4af2-bffa-41c45f95c106 | reviewer | failed |
| review-acquisition-security | w-ed15e6dd-61cd-4ce1-a439-5ed249791cd7 | reviewer | completed |
| sgf-preprocessing-route | w-69e73c70-ee18-4473-8b83-986b8b762275 | researcher | completed |
| review-prepared-pair-change | w-7797c47f-9903-45b0-bbb7-70e3b15b2ae0 | reviewer | failed |
| offline-bundle-api-tests | w-aa748747-0c64-4955-96b5-f443faab8929 | coder | recovery_required |
| offline-registration-diagnostics | w-47286095-410b-4a35-bb21-ac93030eebdc | coder | recovery_required |
| offline-calibration-preflight | w-e638ceed-e7d9-40e5-8c55-34ebdb5a146f | coder | recovery_required |
| offline-catalog-selector | w-c929c0c3-f4a3-4350-a1fb-0ea93bc5b793 | coder | recovery_required |
| offline-dashboard-contract | w-d369b818-8114-4354-9a4c-d1541c52cae5 | coder | recovery_required |
| offline-demo-runbook | w-f424f4c0-d0a7-4edb-a697-6d82775a662b | coder | recovery_required |
| offline-pipeline-performance | w-d21af2d9-df20-4bce-9f26-3eb99c03a2c1 | reviewer | failed |
| offline-bundle-demo-resilience | w-1938ff4f-8448-45e9-a5d1-122eac2cd4cd | reviewer | failed |
| offline-rs2-driver-experiment | w-54ae8156-d99a-4635-8369-cd28fe7d7e12 | researcher | failed |
| offline-aoi-context | w-9ef29711-caba-4425-b0b9-34c2f73b3d27 | researcher | failed |
| offline-reproducibility-review | w-19d3763b-108c-4ace-bec5-b0429b72f6af | reviewer | failed |
| offline-challenge-compliance | w-9d6161a5-5923-4263-8224-054009dbc5ec | reviewer | failed |

## Last known published implementation heads

- Acquisition: babc4bd80634d2aeaa04d53cdd172f6b3306edbc; security fixes pending.
- Catalog: dd44fa12d15893c5474f382f0e1fa9839f231308; final wording corrections pending.
- Inventory: 7c0faf33d24011f20c273d96937c07cc39793b9d; georeferencing/JSON/spacing/XML fixes pending.
- API: 87cfacb58d5c4896389eb80749e6222f687db4cb; metric/temporal consistency fixes pending.
- Prepared-pair producer: b8d9059dc23efaf122ee85bd4403bcb1df350ce1; final exact-head review pending.

These are checkpoints, not automatically the latest VM heads. Query final publication before merging. Branches follow swarmforge/forest-change-20261002/TASK/WORKER_ID.

Detected GDAL calibrated SIGMA0 is linear power, not amplitude. SGF does not establish geocoding or ScanSAR processing. Matching grids do not establish registration. Catalog spacing is not resolution. Priority is a transparent magnitude/area index, not confidence. Two-date persistence/history remain unavailable. Never infer cause or publish fixtures as real change evidence.
