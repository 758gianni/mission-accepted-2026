# ForestWatch dashboard handoff

**Audience:** the frontend team (who own `frontend/`), and the lead.
**Owner of this file and of `contracts/forestwatch.ts`:** backend contract author.
**Frontend source is not edited here.** No UI components, no presentation mocks,
no fixtures for display.

## Status: not a certified contract, and not auto-certifying

The **reference API head is now `b109bfc739642140bad2db297a3332fd61445a93`**,
which follows `a3f94b345434cfc76bddefb4a3e0d6692957ccff`, which follows
`87cfacb58d5c4896389eb80749e6222f687db4cb`. The producer contract is unchanged at
`b8d9059dc23efaf122ee85bd4403bcb1df350ce1`.

| Concern | Head | Locally inspected? |
| --- | --- | --- |
| HTTP API (latest reviewed reference) | `b109bfc739642140bad2db297a3332fd61445a93` | **No** |
| HTTP API (previous reviewed reference) | `a3f94b345434cfc76bddefb4a3e0d6692957ccff` | **No** |
| HTTP API — newest head actually verifiable here | `87cfacb58d5c4896389eb80749e6222f687db4cb` | Yes |
| Producer (change → bundle) | `b8d9059dc23efaf122ee85bd4403bcb1df350ce1` | Yes |
| Integration decisions | `237d45ea506dc17d3b348f93b7cc13dfa5801ff3` | Yes |
| Catalog scene selection | `dd44fa12d15893c5474f382f0e1fa9839f231308` | Yes |

**Neither `b109bfc7` nor `a3f94b3` could be inspected.** Both objects are absent
from this clone's object store, no ref points to either (`backend-api` still
resolves to `87cfacb`), and no fetch or authentication was attempted. So:

* Every **field-level** statement and the TypeScript parity check below are
  verified against `87cfacb`, the newest API head actually available locally.
* Every **behavioural** statement attributed to `b109bfc7` or `a3f94b3` is marked
  *reported by review* and is **unverified against source**. Treat it as the
  reviewer's statement of that head's behaviour, not as something re-read from
  code.
* This document **does not auto-certify against a head it cannot see.** A further
  head is already **queued**: a final **hot-generation path guard**. When it
  lands, do not treat this as current — re-run the parity check against it and
  re-confirm every *reported by review* claim first.

There is also an **open producer blocker** — see section 12. Producer/API
agreement is **not complete**, so this contract is **not final** regardless of
the reference head above.

Anything that shows up as missing or renamed at integration time is a contract
question for the backend owner — resolve it upstream, do not paper over it in the
dashboard. A running service also publishes the same contract machine-readably
at `/openapi.json`.

No real prepared RADARSAT-2 inputs exist in the repository yet, so nothing here
has been exercised against a real bundle. Every number in this document was read
from source or attributed to review, never invented.

`contracts/forestwatch.ts` contains the TypeScript types and one short fetch
example for these endpoints. It type-checks under `tsc --strict`.

## API evolution note — `b109bfc7` (reported by review, unverified locally)

The API has moved on twice since the newest head present in this clone. All three
items below are **reported by review for
`b109bfc739642140bad2db297a3332fd61445a93`** and are **not verified against
source**, because that object is absent here. They change how the frontend must
call the API.

**1. Use the API-declared imagery URL. Do not construct one.**
The declared `imagery.<key>.url` is authoritative and must be consumed **verbatim,
resolved against the backend base URL**:

```ts
// correct: use exactly what the API declared
const src = resolveImageryUrl(analysis.imagery.before);

// wrong in all four cases:
"/api/imagery/before"                 // hardcoded path
`${import.meta.env.VITE_API}/api/imagery/before`  // rebuilt from scratch
`/api/imagery/before?analysis_id=${analysis.analysis_id}` // hand-built query
src.split("?")[0]                     // stripped query
```

* **Never hardcode `/api/imagery/<key>`.** The path is API-owned and versioned.
* **Never request it relative to the frontend/Vite origin.** It must go to the
  backend base URL; a same-origin request from the dev server will not reach the
  API.
* **Never strip, rebuild or add to the query string.** Reported by review, the
  declared URLs now carry a generation parameter of the shape
  `?analysis_id=chg-<sha256-16>`. That parameter is how the API names which
  generation of the bundle the image belongs to, so it is not decoration.
* `analysis_id` is available as a field on the analysis document if a caller
  needs it for its own bookkeeping, but the query string belongs to the API.

**2. Declared imagery URLs are generation-scoped.**
Because the URL names a generation, a cached or in-flight request for a bundle
the API has already replaced is stale. Reported by review, a stale request
answers **`409`**. Treat 409 as "the bundle you were holding is no longer
current": re-read `/api/status` and reload, rather than displaying the older
bundle as though it were current, and rather than retrying the same stale URL.
Do not map 409 onto the `404` (nothing produced yet) or `503` (bundle broken)
cases — they mean different things.

**3. Strict producer-shaped relative generation sibling pointer.**
Reported by review, the API now supports a strict, producer-shaped *relative*
generation sibling pointer. I could not inspect this head, so the exact shape,
name and validation rules are unknown to me and are deliberately **not**
documented as fact here. Treat it as a producer/API concern to confirm with the
API owner before the frontend depends on it in any way. It does not change any
field described in this document, and no example of it is given because I cannot
verify one.

**Not yet in effect.** A final **hot-generation path guard** is queued, so the
latest head may advance again. When it does, re-check all three items above; do
not treat this note as stable.

## 1. Endpoint summary

Every route is a `GET`. There is no static mount and no directory listing.

| Route | Success | Failure |
| --- | --- | --- |
| `/health` | `200` — liveness only, independent of the bundle | — |
| `/api/status` | always `200` | — |
| `/api/analysis` | `200` | `404` absent, `503` invalid |
| `/api/regions` | `200` | `404` absent, `503` invalid |
| `/api/regions/{region_id}` | `200` | `404` absent **or** unknown id, `503` invalid |
| `/api/imagery/{key}` | `200` `image/png` | `404` absent **or** unknown key, `503` invalid, `409` stale generation (reported by review, head `b109bfc7`) |

`key` must be `before`, `after` or `change`. Prefer reading the declared
`imagery.<key>.url` from `/api/analysis` and resolving it against the backend
base URL rather than building this path yourself — see the API evolution note.
Error bodies are always
`{"detail": "<message>"}`; messages name contract fields only and never contain
filesystem paths or raw tool output.

CORS allows exactly the configured origins (defaults `http://localhost:3000`
and `http://localhost:5173`), methods `GET, HEAD, OPTIONS`, headers
`Accept, Content-Type`. A request from any other origin gets no
`Access-Control-Allow-Origin` header, so a browser call will simply fail.

## 2. `/api/status`: absent / invalid / ready

Always `200`. Branch on `state` and never assume data exists.

| `state` | Meaning | Data endpoints | `analysis_id` / `scene_count` |
| --- | --- | --- | --- |
| `awaiting_analysis` | **absent** — no bundle produced yet (directory missing or empty) | `404` | `null` / `0` |
| `error` | **invalid** — bundle present but fails validation | `503` | `null` / `0` |
| `ready` | **valid** bundle | `200` | populated |

`404` means "not produced yet"; `503` means "produced but broken". They are
different problems and must not share one fallback screen without saying which
one it is. The service substitutes no defaults, zeros or estimates for missing
data, and neither should the dashboard.

```json
{
  "state": "ready",
  "schema_version": 1,
  "analysis_id": "chg-<sha256-16>",
  "scene_count": 2,
  "message": "Result bundle is available."
}
```

For the other two states, `message` is one of:

* `awaiting_analysis` — "No analysis bundle has been produced yet. Waiting for
  the first analysis run."
* `error` — "The analysis bundle is present but does not satisfy the result
  bundle contract, so no values are served." (possibly with a trailing
  `(<field label>)`).

Recommended sequence, matching the backend's own guidance: fetch `/api/status`
on load; on `awaiting_analysis` stop and do not call data endpoints; on `error`
show `message` as a problem to report; on `ready` fetch `/api/analysis` and
`/api/regions` in parallel. A status fetch that fails outright should be treated
as "unknown", not as `ready`.

The bundle is revalidated when the bundle directory changes, so a new bundle is
picked up without restarting the service — polling `/api/status` is a legitimate
refresh strategy.

## 3. Region selection from GeoJSON

`GET /api/regions` returns a WGS84 `FeatureCollection`. Feature order follows
`regions.geojson` and is stable across requests.

* `feature.id` is always identical to `feature.properties.region_id`.
* `feature.geometry.type` is `Polygon` or `MultiPolygon`; positions are
  `[longitude, latitude]` pairs. No `crs` member (RFC 7946).
* Ids are assigned by the producer in the order `R001`, `R002`, … — by
  descending `priority_score`, then descending `area_ha`, then centroid — so the
  leading region is the highest-priority one. Do not re-sort client-side and
  assume the original order survives.
* `GET /api/regions/{region_id}` returns one `Feature` for on-demand fetch. The
  id is used as a dictionary key only; it is never interpreted as a path.
* `analysis.demo_region_id` preselects a region when it is not `null`. **Today
  the producer always emits `null`** and keeps it `null` until an independent
  review of real results nominates one, so the dashboard must work with no
  preselection. Never set it from a synthetic or test result.

Region polygons are thresholded radiometric-change polygons, **not** classified
forest-loss polygons, and polygon rings preserve holes. A region can be
interrupted by unmeasured ground, so a region outline is not a claim of
contiguity.

## 4. Declared imagery, URLs and bounds

`analysis.imagery` declares exactly `before`, `after` and `change`. Each entry
has exactly `path`, `bounds`, `label` in the bundle; the API **adds** `url`.

```json
"imagery": {
  "before": { "path": "before.png", "bounds": [w, s, e, n],
              "label": "... pooled stretch -18.40 to -6.15 dB",
              "url": "/api/imagery/before" },
  "after":  { "path": "after.png",  "bounds": [w, s, e, n], "label": "...", "url": "/api/imagery/after" },
  "change": { "path": "change.png", "bounds": [w, s, e, n], "label": "...", "url": "/api/imagery/change" }
}
```

* `url` is **relative** and API-owned. Resolve it against the **backend base
  URL** and use it directly as the `<img>` source — never hardcode
  `/api/imagery/<key>`, never fetch it from the frontend/Vite origin, and never
  strip or rebuild its query string (reported by review it carries
  `?analysis_id=chg-*`, which names the generation; see the API evolution note).
  The dashboard never needs a bundle file path, and `path` is a bundle-relative
  file name (`<key>.png`) that is not a URL — do not concatenate it onto
  anything.
* `bounds` and `analysis.bbox` are both `[west, south, east, north]` in WGS84
  (EPSG:4326), with `west < east`, `south < north`. Some map libraries want
  `[south, west, north, east]` — convert explicitly, do not assume.
* The three previews are identically warped onto the analysis grid, so **each
  `imagery.<key>.bounds` equals `analysis.bbox` exactly**. The bundle is rejected
  (state `error`, `503`) if they differ, so a mismatch is not something the
  dashboard has to tolerate. Register the `<img>` extent and the map extent from
  the same numbers.
* `change.png` is a diverging red/blue rendering around 0 dB, clipped at the
  98th percentile of detected |dB| and never below the applied threshold. It is
  a signed rendering, not a magnitude map.
* Nodata is fully transparent in all three previews. Transparent areas are
  unmeasured, not zero backscatter.

## 5. Before/after share one stretch

`before.png` and `after.png` are stretched with **one pooled range** shared by
both dates: the 2nd–98th percentile of the pooled valid backscatter samples of
both scenes. Both entries say so in `label`, and `analysis.limitations` repeats
it.

Consequences the dashboard must not get wrong:

* The two previews are **comparable to each other**: a real global brightness
  shift between the dates stays visible as a brightness difference, and before
  maps toward the dark end while after maps toward the bright end for such a
  shift.
* They are **not** comparable to any external radiometric scale. The stretch is a
  relative rendering range, not an absolute radiometric scale.
* A per-image stretch would silently hide exactly that difference, so do not
  "improve" the previews by re-normalising each one independently in the UI.
* The numeric range is disclosed only as text inside `label` /
  `limitations`; there is no separate structured stretch field in the current
  contract. Render the label; do not parse numbers out of it for a colour bar.

## 6. Times: `baseline_at`, `detected_at`, `observation_interval`

All three are ISO 8601 **UTC** (`Z` or `+00:00`); other offsets and date-only
strings are rejected by the validator. Each must name the `acquired_at` time of
a scene in `analysis.scenes`, so every displayed time is tied to a real
acquisition rather than an inferred date. Reported by review for the reference
head `a3f94b3`, **actual dates are validated**; that is consistent with what is
verifiable at `87cfacb` but could not be re-read at `a3f94b3` (see section 11).

| Field | Meaning | Must satisfy |
| --- | --- | --- |
| `baseline_at` | the acquisition used as the baseline reference | strictly before `detected_at` |
| `detected_at` | the acquisition at which a radar difference was **first observed** | — |
| `observation_interval` | `{start, end}`, the acquisitions bracketing the observation | `start <= baseline_at`, `detected_at <= end` |

`observation_interval` is **not** an onset interval. Two scenes cannot establish
when anything physically began: a change could have started at any time inside
the window, including long before `detected_at`. Label these as observations, not
events — do not call them "date of change" or "onset", and do not infer or
average a change date between them.

## 7. Units

* **Areas are hectares (`ha`)** everywhere: `analysis.metrics.*_area_ha`,
  `properties.area_ha`, `method.minimum_area_ha`. Areas are geodesic, computed
  on the WGS84 ellipsoid with holes subtracted. `not_evaluable_area_ha` is
  unmeasured ground, not a change area.
* **Backscatter and change are dB**: `method.units` is always `"dB"`,
  `method.change_definition` is always `"10*log10(after/before)"`,
  `change_db` and `magnitude_db` are dB, and `time_series[].mean_backscatter_db` /
  `change_from_baseline_db` are dB.
* `method.quantity` is `sigma0` or `gamma0` and `method.units` is always dB —
  the quantity is the *linear-power* basis; never render it as a dB unit.
* `valid_fraction` and `persistence.rate` are fractions in `[0, 1]`.
* `analysis.metrics` partition the extent: `analysis_area_ha` is the total, and
  `valid_area_ha + not_evaluable_area_ha == analysis_area_ha` (validated within
  0.01 ha = 100 m²). Report the two as a breakdown of the total, not as separate
  totals. `total_changed_area_ha` equals the sum of the served regions'
  `area_ha` and never exceeds `valid_area_ha`.

* **`method.threshold_db` is a strictly positive ABSOLUTE per-pixel threshold.**
  A pixel is retained when `abs(change_db) >= threshold_db`, so the threshold
  selects on **magnitude and never on sign**. It is always `> 0` and always
  caller-supplied; the producer has no default. Never render it as a signed
  quantity, and never compare `change_db` against it without taking the absolute
  value first.

**`change_db` and `magnitude_db` are two different statistics.**
`change_db` is the **signed median** of per-pixel dB change; `magnitude_db` is the
**median of the absolute** per-pixel dB change. `median(|x|)` is not
`|median(x)|`, so a region containing both brightening and darkening pixels keeps
its dominant sign in `change_db` while `magnitude_db` stays at the median
absolute value. The two need not agree. Display both, labelled; never derive one
from the other and never assume `magnitude_db === Math.abs(change_db)`.

Because the threshold is applied **per pixel to the absolute value** while
`change_db` is a **region-level signed median**, the reported `change_db`:

* can be **0.0** — when a region's pixels balance brightening against darkening
  the median lands at zero; and
* can be **mixed in sign across regions** — one region's median is negative,
  another's positive.

`change_db === 0.0` does **not** mean "no change". Every pixel in a retained
region satisfied `|change_db| >= threshold_db > 0`, so a zero median means the
region *contains both directions of change*, not that it contains none. Render
`0.0` as the signed median it is; never collapse it to "unchanged", and never use
it to suppress or drop a region.

`magnitude_db` is always `>= 0` and, for a retained region, at least
`threshold_db`.

## 8. Priority score

```
priority_score = magnitude_db * sqrt(area_ha)
priority_units = "dB sqrt(ha)"
```

Both `priority_units` and `priority_formula` are carried on every region and are
validated as exactly those strings, so the dashboard can display them instead of
hard-coding them. The score is built from **`magnitude_db`**, never from
`change_db`: a region whose signed change is near zero can still have a
substantial magnitude and therefore a substantial priority score. `area_ha` is
in hectares, so the square root is of hectares — the resulting units are
`dB sqrt(ha)`, not a probability, percentage or confidence. Do not label it a
percentage or rescale it into one.

## 9. Two-date runs: `null` is not zero

With exactly two acquisitions, which is what the producer emits today:

* `persistence.status` is always `"not_evaluable"`, with
  `observations_after_detection: 0`, `changed_observations: 0` and
  `rate: null`.
* `historical_anomaly` is always `null`.
* `time_series[].change_from_baseline_db` is **`0.0` at the baseline acquisition**
  itself whenever the baseline value is valid. See below.

### `change_from_baseline_db` at the baseline date is `0.0`, not `null`

**Corrected.** Earlier text in this document said the baseline point is `null`.
That was wrong.

`change_from_baseline_db` is `mean_backscatter_db` for that acquisition minus the
**baseline scene's** `mean_backscatter_db` for the same region. At the baseline
acquisition the two are the same measurement, so the value is **`0.0`** whenever
the baseline value is valid.

That `0.0` is a **real computed difference against the baseline reference**. It
is:

* **not** an invented value substituted for a missing observation, and
* **not** a claim of historical stability — it says nothing about what happened
  before the baseline date. Two scenes cannot support that claim.

`change_from_baseline_db` remains **`null`** when the baseline value is genuinely
unavailable — for example when the region had no valid ROI support in the baseline
scene. So at the baseline date:

| Value | Meaning | Render as |
| --- | --- | --- |
| `0.0` | computed; no difference from the baseline reference | `0.0 dB` |
| `null` | not computable, no valid baseline value for this region | "not assessed" |

Keep those two apart. Do not default one to the other, and do not treat the
`0.0` as evidence that the region was stable before the baseline.

These two-date values are otherwise **unavailable, not zero**. A `0` persistence
rate would assert that nothing changed after detection; a `0` anomaly would assert
that nothing unusual ever happened before it. Neither is supportable from two
dates.

How to render:

* Render `null` as "not assessed" / "not evaluable" — never `0`, never `0.0%`,
  never a dash that reads as zero.
* Never present these fields as probabilities or causal statements. The API
  emits **no** probabilities, **no** confidence scores and **no** causal labels,
  so any percentage the dashboard invents — including a "confidence" figure —
  would be fabricated. There is no confidence field to display, so display none.
* `persistence.rate` is null exactly when `status === "not_evaluable"`; an
  `"observed"` status requires a rate. Branch on `status` before rendering.
* The causes are undetermined: nothing in this data distinguishes deforestation
  from flooding, fire, agriculture, or a processing artifact, and there is no
  ground validation. Do not label a region as deforestation, fire loss or any
  other cause, and do not add such labels to `explanation`.

## 10. Safe text and safe links

The dashboard renders text that arrives from a bundle file on disk. All of it
must be treated as untrusted plain text.

* Render `analysis.title`, `analysis.limitations[]`,
  `properties.explanation`, every `label`, and every `detail` as **text nodes or
  escaped strings only**. Do not use `innerHTML`, `dangerouslySetInnerHTML`,
  `v-html`, `{...}` spread-into-markup, or Markdown/`dangerously`-style raw-HTML
  rendering for any of these fields. There is no HTML in the contract.
* Do not concatenate server text into a URL, a CSS selector, or an inline style.
* **Catalogue links.** `scene.catalog_url` is public EODMS/STAC metadata. Every
  href observed in the catalogue selection artefacts is `https://`, but the value
  is still untrusted input. Render it as an anchor only after confirming the
  scheme is `http:` or `https:` (see `safeExternalHref` in
  `contracts/forestwatch.ts`); drop the link otherwise. Reject `javascript:`,
  `data:`, `vbscript:` and relative values rather than rewriting them.
  These are metadata links to a public catalogue page — following one does not
  download product pixels, and no token, signature or query credential exists in
  any of them. Opening one must not require or attach credentials. The
  `properties.auth:schemes` entry was deliberately removed from the delivered
  catalogue artefact because it advertised the EODMS login endpoint.
* Imagery URLs come from `imagery.<key>.url`, which the API generates itself as
  `/api/imagery/<key>`. Those are the only image sources; do not build an image
  path from `path`.
* Error `detail` strings are short contract labels by construction; show them
  verbatim rather than paraphrasing them into a stronger claim.

## 11. Fields still under review

Treat these as open, and confirm with the API owner before building UI that
depends on them:

1. **Whole-contract certification, and no auto-certification.** The reference head
   is `a3f94b3`, which could **not** be inspected locally (absent object, no ref,
   no fetch attempted). Field-level statements are verified against `87cfacb`
   only. If any further API head appears, re-run the parity check against it and
   re-confirm every *reported by review* claim before treating this as current.
2. **New behaviours attributed to review, unverified against source.** Reported
   by review for `a3f94b3`, and not observable at `87cfacb`:
   * **Actual dates are validated.** Timestamps continue to have to name the
     `acquired_at` of a real scene in `analysis.scenes`, so every displayed time
     is tied to a real acquisition. Nothing in the contract infers a date.
   * **Persistence behaviour** is unchanged: a two-date run reports
     `not_evaluable` with a `null` rate.
   * **The source URL is validated** (`scene.catalog_url` must be a valid
     source URL), so a malformed link fails the bundle rather than reaching the
     dashboard. This is why scheme checking is documented as a defensive runtime
     guard rather than as the primary protection.
3. **Acquisition and inventory provenance fields.** Not part of this contract and
   not included here.
4. **No structured preview-stretch field.** The shared before/after range is only
   available as text in `imagery.*.label` and `limitations`. If the UI needs it as
   data (a colour bar, a shared legend), that is a contract addition request, not
   a frontend workaround.
5. **Imagery `url` is API-added, not bundle-declared.** A client reading
   `analysis.json` from disk would not see it. Anything else the API injects is
   possible; treat `/api/analysis` as the schema of record.
6. **`demo_region_id` is always `null` today.** Preselection behaviour is
   unexercised.
7. **Persistence/anomaly are structurally specified but never exercised.** Their
   non-null `"observed"` branch has no producer today, so it is untested against
   a real bundle.
8. **Untested against real data.** No real prepared RADARSAT-2 products exist
   locally yet; `regions.geojson` and `analysis.json` have only been validated
   against synthetic fixtures.

## 12. Open blocker: producer `relative_orbit` float vs integer

**Producer/API agreement is not complete. Do not treat the contract as final.**

At `87cfacb` the API validates `scene.relative_orbit` as an **integer** or
`null`, while the producer's manifest schema permits a **finite number** or
`null`. A JSON float such as `98.0` is not an `int` in Python terms, so such a
bundle fails validation and surfaces as `state: "error"` with `503` on the data
endpoints — for this field alone, with nothing else wrong.

The producer fix is **under way**. Until it lands (emit an integer, or `null`),
assume a bundle built today may fail to serve. This is recorded here rather than
worked around: the dashboard cannot detect it, because the failure appears as a
generic `error` state. If the dashboard sees `state: "error"`, report the message
verbatim rather than assuming the bundle is merely mid-write.

## 13. What not to build

* No frontend edits, no presentation mocks, no UI features from this work
  package.
* No real-product or demo content: no synthetic results presented as findings,
  and no fabricated region ids, dates or percentages.
* No causal labels (deforestation, fire, flood) and no confidence values, because
  nothing in the pipeline supports either.
* No third-party or additional dependency request is made by this handoff; the
  existing stack covers typing these structures. Any dependency the frontend
  needs should go through the lead.

## Verification performed

* Every field name and constant in this document and in
  `contracts/forestwatch.ts` was compared against
  `backend/models.py`, `backend/validation.py`, `backend/app.py`,
  `backend/bundle.py` at `87cfacb`, and `processing/change.py` plus
  `docs/CHANGE.md` at `b8d9059`, by reading those trees locally.
* The reviewed head `a3f94b3` **could not be checked**: the object is absent from
  the local store and no ref points to it. No fetch or authentication was
  attempted, and no claim above is presented as verified against it.
* `contracts/forestwatch.ts` type-checks with
  `tsc --noEmit --strict --target es2022 --module esnext --moduleResolution bundler --lib es2022,dom`
  (TypeScript 7.0.2) with no diagnostics.
* No test suite was run: the backend tests live on the API author's branch and
  the frontend is owned by another team, so neither is in scope for this change.