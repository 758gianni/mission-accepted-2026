# Demo runbook — five-minute presentation checklist and recovery path

Owner: documentation. Scope: how to get from a **locally prepared real result
bundle** through the read-only API to the teammates' dashboard, on one machine,
without touching EODMS or running processing during the talk. Frontend, PPTX and
README are owned elsewhere and are not edited by this document.

## 0. Current status of this runbook (read first)

**No real result bundle exists yet.** Nothing in this repository is a real
RADARSAT-2 result: there are no local RADARSAT-2 product bytes, no
`data/processed/current/` bundle, and no inspected change result. Consequently
the following are **PENDING** and must not be presented as done:

| Item | State | Who/what unblocks it |
| --- | --- | --- |
| Real prepared pair (two calibrated, verified acquisitions) | PENDING | acquisition + inventory, from real product bytes |
| Real result bundle in `data/processed/current/` | PENDING | `processing.change` run over the real pair |
| **Example/demo region selection** | **PENDING — no region is nominated** | independent review of the real result sets `demo_region_id` |
| Real-data verification (dates, magnitudes, areas read off a served bundle) | PENDING | once the first real bundle is served |
| Dashboard consuming `/api/*` | UNVERIFIED — frontend has no API client or map dependency at the time of writing | frontend owners |

`demo_region_id` is `null` in the contract and stays `null` until a review of a
real result nominates one. No region, area, magnitude or date is written into
this runbook, and none may be filled in from a test fixture. Every value below is
either a command to run, or a placeholder to fill from the served API at
preflight time.

The scene selection below (catalog metadata only) is *not* a change event. The
catalog report states it explicitly: it establishes only that the same ground was
imaged on the listed dates by comparable acquisitions. It does not establish that
any change occurred.

## 1. The five-minute story arc

Five beats, in order. Total speaking time 5:00. Every beat reads values from the
served API or from a screenshot captured at preflight — never from memory, never
from a slide number.

| # | Beat | Time | What is shown | What is said (one sentence, no more) |
| --- | --- | --- | --- | --- |
| 1 | Overview | 0:00–0:45 | Slides / problem framing | RADARSAT-2 C-band change intelligence: two comparable acquisitions of the same ground, differenced and ranked for review. |
| 2 | **Selected real region** | 0:45–1:45 | Dashboard overview + one highlighted region | "This region was selected by an independent review of the real result, not chosen to look good." If no region is nominated yet, say so — see §7. |
| 3 | Before / after | 1:45–2:30 | `before` then `after` preview, same extent | "Same footprint, two dates, identical rendering scale." |
| 4 | Change + magnitude | 2:30–3:30 | `change` preview, then the region's `change_db` / `magnitude_db` / `area_ha` | "Thresholded radiometric difference. Magnitude and area, with cause undetermined." |
| 5 | Dates and the temporal limitation | 3:30–4:30 | `baseline_at`, `detected_at`, `observation_interval`, persistence panel | "Two dates bound an observation window. They cannot date an event, and they cannot show persistence." |
| 6 | Reproducibility + limits | 4:30–5:00 | `limitations`, method line, the commands in §8 | "Every number is reproducible from the committed scripts and the served bundle." |

Beat 3 is where the two previews must be visually comparable: `before.png` and
`after.png` share one pooled 2nd–98th percentile dB stretch, so a global
brightening stays visible rather than being normalised away. Say that if a judge
asks why the two panels differ in brightness.

## 2. Preflight health/status/imagery/region checks (T−24 h and again T−10 min)

Run these against the running API. They are read-only `GET`s. Record the literal
output; that recording is the reproducibility evidence for §8.

```bash
# 0. API process is up. Deliberately independent of the bundle.
curl -fsS http://127.0.0.1:8000/health
# expect: {"status":"ok","service":"forestwatch-api"}

# 1. Branch on state. Do not skip this and do not assume it is "ready".
curl -fsS http://127.0.0.1:8000/api/status
# expect one of:
#   ready            -> data endpoints answer 200
#   awaiting_analysis-> data endpoints answer 404  (NOT a bug to demo around silently)
#   error            -> data endpoints answer 503  (bundle present but off-contract)
```

| `state` | What it means | Preflight action |
| --- | --- | --- |
| `ready` | Bundle present and satisfies contract v1. | Continue with checks 2–5. |
| `awaiting_analysis` | No bundle produced yet (directory missing or empty). | **Gate fails.** Use the §7 fallback. Do not fabricate data. |
| `error` | Bundle present but malformed/incomplete/inconsistent. | **Gate fails.** Read the `message`, report it as a problem to report. Do not present the numbers as trustworthy. |

`404` means "not produced yet"; `503` means "produced but broken". The API never
substitutes defaults, zero values or estimates for missing data — and neither may
the presentation.

```bash
# 2. Analysis document. analysis_id, bbox, scenes, method and metrics all come from here.
curl -fsS http://127.0.0.1:8000/api/analysis > /tmp/demo-analysis.json
python -c "import json;d=json.load(open('/tmp/demo-analysis.json'));print(d['analysis_id'],d['bbox'],d['demo_region_id'],d['metrics'])"

# 3. Regions. Feature order is stable across requests; ids equal feature.id.
curl -fsS http://127.0.0.1:8000/api/regions > /tmp/demo-regions.json
python -c "import json;d=json.load(open('/tmp/demo-regions.json'));print(len(d['features']),[f['id'] for f in d['features']])"

# 4. Imagery. All three must return 200 with image/png.
for k in before after change; do
  printf '%s ' "$k"
  curl -s -o /dev/null -w '%{http_code} %{content_type} %{size_download}\n' \
    "http://127.0.0.1:8000/api/imagery/$k"
done

# 5. One region by id. Use the id from analysis.demo_region_id if one is set;
#    otherwise pick from the list in check 3 for the demo only if a review has
#    nominated it — otherwise use the §7 fallback.
curl -fsS "http://127.0.0.1:8000/api/regions/<REGION_ID>" > /tmp/demo-region.json
python -c "import json;p=json.load(open('/tmp/demo-region.json'))['properties'];print(p['region_id'],p['area_ha'],p['change_db'],p['magnitude_db'],p['priority_score'],p['baseline_at'],p['detected_at'],p['observation_interval'],p['persistence'],p['historical_anomaly'])"
```

Also verify at preflight, because they are the claims most likely to be wrong:

- `analysis.imagery.<key>.bounds` equals `analysis.bbox` **exactly** for all
  three keys. The API already rejects a mismatch (`503`), so a `ready` state
  proves it; a visual check confirms the map and the preview show the same place.
- `valid_area_ha + not_evaluable_area_ha == analysis_area_ha` and
  `total_changed_area_ha == sum(region.area_ha)`. The API enforces both within
  0.01 ha; repeat them out loud as a reconciliation rather than as a claim of
  accuracy.
- `method.threshold_db` and `method.minimum_area_ha` are the values the operator
  actually passed. They have no defaults — if they are not stated, the result is
  not reproducible.

### Preflight decision record (fill this in, keep it)

| Field | Value |
| --- | --- |
| Bundle directory served | |
| `analysis_id` | |
| `scene_count` / acquisition dates | |
| `demo_region_id` (or `null`) | |
| `threshold_db` / `minimum_area_ha` | |
| `valid_area_ha` / `not_evaluable_area_ha` / `total_changed_area_ha` | |
| Frontend origin serving the dashboard | |
| Preflight run at (UTC) | |

## 3. Cold local startup (T−10 min, before the walk-on)

Two terminals. Nothing here contacts EODMS and nothing here processes rasters.

```bash
# Terminal A — API. uv is the shared project manager (pyproject.toml, uv.lock).
# Python 3.12+.
uv sync --frozen
uv run uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Without `uv`, the equivalent is documented in `docs/API.md`:

```bash
pip install -r requirements-backend.txt
FORESTWATCH_BUNDLE_DIR=data/processed/current \
  python -m uvicorn backend.app:app --port 8000
```

Bundle location precedence: `--` argument > `FORESTWATCH_BUNDLE_DIR` >
`data/processed/current`. Cross-origin browser calls are allowed **only** for
`http://localhost:3000` and `http://localhost:5173` by default; any other origin
gets no `Access-Control-Allow-Origin` header. If the dashboard is served from a
different host or port, set `FORESTWATCH_CORS_ORIGINS` before starting, or the
browser blocks the calls and the dashboard shows the awaiting state for reasons
unrelated to the bundle.

```bash
# Terminal B — dashboard (teammates' repo, their command; Vite default is 5173).
cd frontend && npm install && npm run dev
```

Cold-start notes that cost real time if unknown:

- The bundle is validated once and cached, then revalidated when the bundle
  directory changes. Dropping a new bundle into place is picked up **without a
  restart** — so a bundle swap mid-session does not require restarting the API,
  but the browser does need a refresh.
- `npm install` on a cold machine is the slowest step. Pre-warm it earlier
  (T−24 h) and leave `node_modules` in place.
- `uv sync --frozen` is offline-safe only if the uv cache is already populated.
  Do this at T−24 h as well; do not rely on network at T−10 min.
- Run the API and the dashboard on the **same machine** as the bundle. Do not
  copy the bundle between machines during the talk.

## 4. During the presentation: what must not happen

- **No EODMS.** No login, no token, no catalog search, no product download. The
  talk needs none of it, and any network call to EODMS during the demo is a
  failure mode with no upside.
- **No processing.** Do not run `processing.change` for the first time on stage.
  It is a heavyweight raster job with no progress output worth watching, and a
  failure there leaves no bundle at all (publishing is atomic). If a rebuild is
  genuinely needed, it happens at T−24 h, and the previous bundle is kept.
- **No credentials on screen.** No terminal window showing a prompt, no `.env`.
  The account owner performs any credential action, outside the talk.
- **No synthetic data.** Test fixtures live in `tests/` and are generated at test
  time. A fixture that reaches a dashboard is a defect, not a fallback.
- **No invented numbers.** Every figure spoken in beats 2–6 is read from the
  bundle the API served.

## 5. Preserve validated result backups

Processing publishes atomically: the bundle is staged, checked, then moved into
place with `analysis.json` written **last** as the completion sentinel. So a
bundle directory containing `analysis.json` is complete. Protect the good one.

```bash
# Copy a served-good bundle to a timestamped backup before any rebuild attempt.
# Keep at least the last two, and keep the one that passed §2 verbatim.
cp -a data/processed/current "data/processed/bundle-${analysis_id}"
```

Rules:

- Back up **after** §2 passes, never before.
- Never edit a bundle in place to "fix" a number. Fix the pipeline, re-run, and
  re-verify. A hand-edited bundle is indistinguishable from fabricated data at
  demo time.
- If the API reports `error`, do not repair the JSON on the serving path. Move
  the broken bundle aside, restore the last verified backup, re-run §2.
- Backups live under `data/`, which Git ignores. Do not commit bundles, previews
  or rasters; commit scripts, tests, schemas and reports.
- Prefer serving a backup explicitly:
  `FORESTWATCH_BUNDLE_DIR=data/processed/bundle-<analysis_id> uv run uvicorn backend.app:app --port 8000`.

## 6. Honest readiness gate

The demo is **GO** only when every line below is true, checked on the machine
that will present:

| # | Gate condition | Check |
| --- | --- | --- |
| G1 | `/health` returns `{"status":"ok","service":"forestwatch-api"}` | §2 check 0 |
| G2 | `/api/status` reports `state == "ready"` | §2 check 1 |
| G3 | `/api/analysis` returns 200 and `analysis_id` is non-null | §2 check 2 |
| G4 | `/api/regions` returns 200 with at least one feature | §2 check 3 |
| G5 | All three imagery URLs return `200 image/png` with non-zero size | §2 check 4 |
| G6 | `/api/regions/{id}` returns 200 for the id actually shown | §2 check 5 |
| G7 | The dashboard renders that same `analysis_id` and region id | browser check |
| G8 | A region has been nominated by review of the **real** result (`demo_region_id` non-null, or a signed-off id) | §0 |
| G9 | Real data verified: acquisition dates, `area_ha`, `change_db`, `magnitude_db` read from the served bundle and recorded | §2 decision record |
| G10 | §2 was re-run within the last 10 minutes and the recorded values match what the dashboard shows | §2 |
| G11 | No `awaiting_analysis`, no `error`, no console CORS error | browser + terminal |

G8 and G9 are the two gates that are **currently unmet**, and they are unmet for
a substantive reason: there is no real result to verify. Failing G2/G5/G6/G7
means the demo path itself is broken and §5 recovery applies. Failing G8/G9
means the presentation may describe the method and the architecture but may not
present a region as a reviewed real finding.

## 7. Fallback when there is no real result

The fallback is an honest description of the system, **not** a simulated demo.
There is no second bundle, no test fixture on stage, and no substitute numbers.

Suggested 60–90 seconds, roughly:

> "We don't have a real result to show you yet, so I'm not going to show you a
> synthetic one. Here's what exists and what's verified. The pipeline is built
> and tested: two prepared RADARSAT-2 acquisitions go in, a thresholded
> radiometric difference comes out, regions are ranked, and a read-only API
> serves the bundle. The API distinguishes three states and it will not invent
> data: if no result has been produced it reports `awaiting_analysis` and returns
> 404, and if a result exists but violates the contract it reports `error` and
> returns 503 rather than serving numbers it cannot stand behind. What is missing
> is the real product bytes and the review that nominates a demonstration region.
> When those land, the same command serves them with no code change."

Then, if useful and only if true, show `GET /api/status` reporting
`awaiting_analysis`. That is a real observation about the real system, and it is
a stronger answer than a fabricated region would be.

If asked "so what would it show?": describe the *shape* of the output from the
committed contract — before/after/change previews, GeoJSON regions, and per-region
area, signed median dB, median absolute dB, priority score, and the three
observation timestamps — and be explicit that no value has been measured yet.

Do not: invent an event, name a real-world fire or clearing date, present a test
fixture, or estimate what the numbers "would look like".

## 8. Reproducibility evidence to have on screen

One terminal, this only — read-only, no processing:

```bash
git rev-parse HEAD                                     # exact source under demo
curl -s http://127.0.0.1:8000/api/status               # analysis_id + state
python -c "import json;d=json.load(open('/tmp/demo-analysis.json'));print(d['method'])"
python -c "import json;d=json.load(open('/tmp/demo-analysis.json'));print(*d['limitations'],sep='\n- ')"
cat /tmp/demo-region.json | python -m json.tool | head -40
```

And the two commands that produced the result, quoted from the run that produced
it (not re-run on stage):

```bash
uv run python -m processing.inventory data/raw --out data/reports/inventory.json
uv run python -m processing.change \
    --manifest data/manifests/pair.json \
    --out data/processed/current \
    --threshold-db <value actually used> \
    --min-area-ha <value actually used>
```

`--threshold-db` must be strictly positive and `--min-area-ha` non-negative;
there are no defaults, and invalid parameters exit `2` with a single-line
`error: ...` and publish nothing. The manifest's `radiometry.calibration`,
`radiometry.geocoding` and `registration.status` are **upstream attestations**,
not findings of the CLI. Quote them as claims with their inspected evidence, and
name the evidence — do not present a flag as proof.

Evidence that should exist on disk for the nominated region, if a real result
exists: the manifest, the inventory report, the calibration/validation note, the
registration diagnostic (threshold, residual in pixels, rejection decision), the
threshold choice with its stable-reference diagnostic, and the commit SHA of the
pipeline that produced the bundle.

## 9. Judge-facing wording

Use these formulations. They are the honest ones.

**Cause.** "Regions are thresholded radiometric-change polygons, not classified
forest-loss polygons. Radar backscatter changed; the cause is undetermined and
we have no ground validation." Water, terrain, moisture, seasonality, incidence
angle and registration error can all produce a difference of this kind. Never say
deforestation, clearing, fire or flood as an established cause; if a specific
hypothesis is discussed, mark it as a hypothesis requiring evidence that does not
yet exist.

**Priority.** "Priority is `magnitude_db * sqrt(area_ha)`, in `dB sqrt(ha)`. It
orders review work by size of radiometric signal relative to how much area is
involved. It is not a probability and not a severity score."

**Persistence.** "With two acquisitions there is no post-detection observation,
so `persistence.status` is `not_evaluable` and `rate` is `null`. That is
unavailable, not zero." Do not display it as `0`, and do not describe a rate of
zero as "no change after detection".

**Dates.** "`baseline_at` is the reference acquisition, `detected_at` is the
acquisition in which a radar difference was first observed, and
`observation_interval` is the acquisition window the observation is bracketed by.
The date any physical change began is unknown inside that interval. Two
acquisitions cannot narrow it."

**Statistics.** "`change_db` is the signed median pixel change; `magnitude_db` is
the median of the absolute pixel change. `median(|x|)` is not `|median(x)|`, so
these two numbers can disagree, and both are served as produced."

**Metrics vocabulary.** There are no confidence percentages, no likelihoods and
no accuracy figures anywhere in this system. Do not invent them, and do not
quote them even as placeholders. If asked for accuracy, the answer is that no
reference or validation dataset has been used to measure it.

**Area.** "`area_ha` is a geodesic area on the WGS84 ellipsoid of the thresholded
pixels with holes subtracted. The analysis extent partitions into evaluable and
not-evaluable area; pixels whose observation is missing are excluded, never
interpolated, and never counted as unchanged."

**Preview scale.** "`before` and `after` share one pooled 2nd–98th percentile dB
stretch, so the pair is comparable but the brightness is not an absolute
radiometric scale."

## 10. Recovery paths, in order of preference

| Symptom | Diagnosis | Action | Time cost |
| --- | --- | --- | --- |
| `/health` fails | API not running, or wrong port/host | Restart §3 Terminal A; confirm `--host 127.0.0.1 --port 8000` | ~40 s |
| `state == "awaiting_analysis"` | Bundle dir missing/empty, or `FORESTWATCH_BUNDLE_DIR` points elsewhere | Check the path; point at the last verified backup (§5); re-run §2 | ~1 min |
| `state == "error"` | Bundle present but off-contract | Read `message`; move it aside; restore backup; re-run §2. Report the failure if unrecoverable. | ~2 min |
| Dashboard shows awaiting state while API says `ready` | CORS or wrong origin | Compare browser origin against `FORESTWATCH_CORS_ORIGINS` defaults (`localhost:3000`, `localhost:5173`); restart API with the correct value | ~1 min |
| Dashboard shows data but no map/previews | Frontend not yet wired to `/api/imagery/*`, or a map dependency is missing | Show the three preview PNGs from §2 check 4 (`curl .../api/imagery/before` etc.) and the GeoJSON; describe the overlay as pending on the frontend | ~30 s |
| Imagery `503` after a `ready` | A declared preview was swapped for a symlink or became unreadable | Restore the verified backup; re-run §2 | ~2 min |
| Region `404` | Id not in the current bundle (bundle changed since selection) | Re-read ids from §2 check 3 and update the deck's region id | ~30 s |
| Anything still broken at T−2 min | — | Abandon the demo path; run the §7 fallback. Do not process on stage. | — |

The rule underneath all of these: **the last verified bundle and this fallback
text are the recovery plan.** There is no third option that invents data.

## 11. What this document does not establish

- That any real change has been detected. No change event is claimed anywhere,
  and the catalog scene selection is metadata about comparable acquisitions, not
  evidence of change.
- That any region is a good demonstration region. No region has been reviewed.
- That the frontend currently consumes the API. At the time of writing the
  frontend has a Vite/React scaffold with no map or API dependency, so G7 is
  unverified and the dashboard-side steps are stated as the contract requires,
  not as observed behaviour.
- That the reference heads used to write this runbook are approved. Commands and
  field names here were read from the API head
  `87cfacb58d5c4896389eb80749e6222f687db4cb`, the integration head
  `237d45ea506dc17d3b348f93b7cc13dfa5801ff3`, the prepared-pair head
  `b8d9059dc23efaf122ee85bd4403bcb1df350ce1` and the catalog head
  `dd44fa12d15893c5474f382f0e1fa9839f231308`, which are under active review.
  Re-check against `docs/API.md` and `docs/CHANGE.md` after those reviews land.
- That `uv sync --frozen` or `npm install` will succeed offline on the demo
  machine. Both are pre-warmed at T−24 h for that reason.
