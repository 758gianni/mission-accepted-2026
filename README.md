# ForestWatch backend

RADARSAT-2 Tropical Forests acquisition, reproducible change analysis, and a local API for the hackathon dashboard. The frontend is developed separately by teammates.

## Run locally

Install [uv](https://docs.astral.sh/uv/) and use Python 3.12 or newer:

```bash
uv sync --frozen
uv run uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

The API serves inspected results from `data/processed/current/`. Before processing is complete, `/api/status` reports `awaiting_analysis`; it does not generate demonstration data.

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/status
```

Set `FORESTWATCH_BUNDLE_DIR` to serve a different local result directory. See [API documentation](docs/API.md) for the dashboard contract and local CORS configuration.

## Data and processing

1. Select comparable acquisitions from `Radarsat-2_Tropical_Forest_Products`.
2. Use the official EODMS CLI through the safe acquisition workflow in [ACQUISITION.md](docs/ACQUISITION.md). The account owner performs credential prompts; credentials must stay outside Git and worker prompts.
3. Place original products in `data/raw/` and inspect them:

   ```bash
   uv run python -m processing.inventory data/raw --out data/reports/inventory.json
   ```

4. Use the inventory to choose product-specific calibration, geocoding, terrain handling, and alignment checks. Raw amplitude, complex samples, and calibrated linear power are different inputs.
5. Run the reviewed prepared-pair workflow documented in [CHANGE.md](docs/CHANGE.md), with an explicitly selected change threshold. It publishes `analysis.json`, `regions.geojson`, and georeferenced image previews.
6. Inspect the real result and scientific diagnostics before selecting a demonstration region.

Original imagery, derived products, local tools, and reports are ignored by Git. Commit processing scripts, tests, provenance definitions, and technical assumptions.

## Metric definitions

- Change: `10 * log10(after / before)` in dB, using comparable calibrated linear radar power.
- Area: affected region area in hectares, with invalid/masked observations excluded.
- Priority: `magnitude_db * sqrt(area_ha)`, in `dB sqrt(ha)`, to order candidates for review.
- Dates: acquisition dates and the interval in which a difference was observed.
- Two-date persistence and historical anomaly: unavailable until enough observations support evaluating them.

Radar differences alone do not identify the cause of a change. Acquisition geometry, water, terrain, moisture, seasonality, and registration errors can affect the result.

## Verify

```bash
uv run pytest
```

Generated raster and metadata fixtures are restricted to tests. Real analysis needs actual acquired products and recorded preprocessing evidence.

The [backend design](docs/BACKEND-DESIGN.md) and [execution plan](docs/superpowers/plans/2026-10-02-backend.md) record the current decisions, risks, ownership, and milestones.
