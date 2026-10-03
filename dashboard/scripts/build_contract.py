#!/usr/bin/env python3
"""Build the frontend data contract from derived SAR analysis outputs.

Reads only derived artifacts (GeoJSON, dashboard metrics, PNG renders).
Never touches raw SAR archives and never modifies the scientific pipeline.

The contract is T4-ready: temporal_class and t4_* fields are derived here so
that a later T4 validation run only has to rewrite these JSON/GeoJSON files.

Usage:
    python build_contract.py [--derived DIR] [--out DIR] [--t4-results FILE]
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DERIVED = Path("/home/overlord/hackathon/rs2-analysis/data/derived/exploration_v2")
DEFAULT_OUT = REPO / "public" / "data"

# Baseline acquisition timeline. T4 is appended when it lands.
BASE_ACQUISITIONS = [
    {
        "id": "T1",
        "date": "2024-04-18",
        "iso": "2024-04-18T11:46:55Z",
        "beam": "XF0W2",
        "polarization": "HH",
        "orbit": "Ascending",
        "role": "pre-change baseline",
    },
    {
        "id": "T2",
        "date": "2024-11-20",
        "iso": "2024-11-20T11:46:56Z",
        "beam": "XF0W2",
        "polarization": "HH",
        "orbit": "Ascending",
        "role": "first change observation",
    },
    {
        "id": "T3",
        "date": "2025-01-07",
        "iso": "2025-01-07T11:46:53Z",
        "beam": "XF0W2",
        "polarization": "HH",
        "orbit": "Ascending",
        "role": "persistence check",
    },
]

# Map extent: UTM 46N analysis grid expressed in WGS84 for Leaflet.
MAP_BOUNDS = {
    "west": 93.25067860517936,
    "south": 22.5110622429485,
    "east": 94.69129975432692,
    "north": 23.858827848355713,
}

# Imagery overlay bounds (identical grid for every render).
IMAGE_BOUNDS = MAP_BOUNDS

TEMPORAL_CLASSES = {
    "seasonal_transient": {
        "id": "seasonal_transient",
        "label": "Seasonal / transient",
        "class_id": 2,
        "color": "#22b8cf",
        "description": "Radar change that returns toward the T1 level by T3. Consistent with seasonal moisture or river-level effects in the valley floor.",
    },
    "persistent": {
        "id": "persistent",
        "label": "Persistent",
        "class_id": 1,
        "color": "#e14c8c",
        "description": "Radar change that does not return toward the T1 level by T3. Candidate persistent surface change, not yet validated by T4.",
    },
    "t4_validated_persistent": {
        "id": "t4_validated_persistent",
        "label": "T4-validated persistent",
        "class_id": 4,
        "color": "#b0306a",
        "description": "Persistent change confirmed to hold through the T4 acquisition.",
    },
    "late_unclassified": {
        "id": "late_unclassified",
        "label": "Late change (unclassified)",
        "class_id": 3,
        "color": "#9a9e9b",
        "description": "Change appearing only in the T2-T3 interval; persistence is unknown.",
    },
}

CLASS_ID_TO_TEMPORAL = {
    2: "seasonal_transient",
    1: "persistent",
    3: "late_unclassified",
}


def load_t4_results(path: Path | None) -> dict:
    """Optional T4 validation results keyed by region id.

    Expected shape (all optional):
      {"regions": {"2": {"t4_status": "confirmed", "t4_mean_power": 0.04,
                         "t4_mean_signed_db": -6.9, "t4_first_seen": "..."}},
       "acquisition": {"id": "T4", "date": "2024-12-21", ...}}
    """
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text())


def classify(feature_props: dict, t4: dict) -> dict:
    """Return temporal_class / t4_* fields for one region."""
    region_id = feature_props.get("id")
    base = CLASS_ID_TO_TEMPORAL.get(feature_props.get("class_id"), "late_unclassified")
    t4_region = (t4.get("regions") or {}).get(str(region_id), {})
    t4_status = t4_region.get("t4_status", "pending")

    if base == "persistent" and t4_status == "confirmed":
        temporal_class = "t4_validated_persistent"
    else:
        temporal_class = base

    return {
        "temporal_class": temporal_class,
        "t4_status": t4_status,
        "t4_mean_power": t4_region.get("t4_mean_power"),
        "t4_mean_signed_db": t4_region.get("t4_mean_signed_db"),
        "t4_observation": t4_region.get("t4_observation"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--derived", type=Path, default=DEFAULT_DERIVED)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--t4-results", type=Path, default=None)
    args = ap.parse_args()

    derived = args.derived
    out = args.out
    (out / "derived").mkdir(parents=True, exist_ok=True)
    (out / "imagery").mkdir(parents=True, exist_ok=True)

    t4 = load_t4_results(args.t4_results)

    # --- candidate regions: extend with T4-ready fields -----------------
    src_geo = derived / "terrain" / "candidate_regions_terrain_qa.geojson"
    geo = json.loads(src_geo.read_text())
    for feat in geo["features"]:
        feat["properties"].update(classify(feat["properties"], t4))
    geo["properties"] = {
        "contract_version": 2,
        "t4_status_default": "pending",
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    (out / "derived" / "candidate_regions.geojson").write_text(json.dumps(geo))

    # --- summary metrics -----------------------------------------------
    src_metrics = derived / "terrain" / "dashboard_metrics.json"
    metrics = json.loads(src_metrics.read_text())

    counts = {"seasonal_transient": 0, "persistent": 0, "t4_validated_persistent": 0, "late_unclassified": 0}
    for feat in geo["features"]:
        counts[feat["properties"]["temporal_class"]] += 1

    t4_region_count = len((t4.get("regions") or {}))
    contract = {
        "contract_version": 2,
        "generated": datetime.now(timezone.utc).isoformat(),
        "scene": {
            "footprint": MAP_BOUNDS,
            "crs_analysis": "EPSG:32646",
            "crs_display": "EPSG:4326",
            "product": "RADARSAT-2 SLC",
            "beam": "XF0W2",
            "polarization": "HH",
            "orbit": "Ascending",
            "resolution_m": 60,
        },
        "acquisitions": BASE_ACQUISITIONS
        + ([t4["acquisition"]] if t4.get("acquisition") else []),
        "temporal_classes": TEMPORAL_CLASSES,
        "counts": counts,
        "metrics": metrics,
        "t4": {
            "available": bool(t4),
            "regions_tested": t4_region_count,
            "summary": t4.get("summary"),
            "note": (
                "T4 validation applied. Confirmed persistent candidates are "
                "reclassified as t4_validated_persistent."
                if t4
                else "T4 validation pending. Re-run build_contract.py with --t4-results "
                "to flip persistent candidates into t4_validated_persistent."
            ),
        },
        "presentation_path": [
            {"step": 1, "id": "overview", "title": "Overview", "detail": "525 candidate regions across three real RADARSAT-2 acquisitions over Myanmar."},
            {"step": 2, "id": "seasonal", "title": "Seasonal river signal", "detail": "440 regions track the valley-floor moisture cycle and return toward baseline by T3."},
            {"step": 3, "id": "persistent", "title": "Persistent anomalies", "detail": "80 regions do not return toward baseline. Filter to this class."},
            {"step": 4, "id": "region-2", "title": "Region 2", "detail": "148.7 ha, -6.7 dB mean signed change, flat lowland, priority score 82.1."},
            {"step": 5, "id": "terrain-qa", "title": "Terrain QA", "detail": "Copernicus GLO-30 DEM: 0% terrain risk, 0.15 deg mean slope. Not a terrain artifact."},
            {
                "step": 6,
                "id": "t4",
                "title": "T4 validation",
                "detail": (
                    f"{t4.get('summary', {}).get('survived_confirmed', 0)} of "
                    f"{t4.get('summary', {}).get('covered_in_t4', 0)} candidates inside the T4 swath "
                    "held through the 2024-12-21 acquisition. Region 2 remains a "
                    "-6.7 dB drop on T2 and -4.8 dB on T4."
                    if t4
                    else "Hold the anomaly against the 2024-12-21 acquisition when it lands."
                ),
            },
        ],
    }
    (out / "derived" / "contract.json").write_text(json.dumps(contract, indent=2))

    # --- metrics alias (stable filename for the frontend) ---------------
    shutil.copyfile(src_metrics, out / "derived" / "dashboard_metrics.json")

    # --- imagery --------------------------------------------------------
    imagery = {
        "temporal_rgb": "data/imagery/temporal_rgb.png",
        "terrain_qa_mask": "data/imagery/terrain_qa_mask.png",
        "persistent_candidates": "data/imagery/persistent_candidates_map.png",
        "seasonal_transient": "data/imagery/seasonal_transient_candidates_map.png",
        "change_t1_t3": "data/imagery/T1_to_T3_signed_db.png",
        "change_t1_t2": "data/imagery/T1_to_T2_signed_db.png",
        "change_t2_t3": "data/imagery/T2_to_T3_signed_db.png",
        "quicklook_t1": "data/imagery/T1_quicklook.png",
        "quicklook_t2": "data/imagery/T2_quicklook.png",
        "quicklook_t3": "data/imagery/T3_quicklook.png",
        "crop_t1": "data/imagery/top_candidate_T1.png",
        "crop_t2": "data/imagery/top_candidate_T2.png",
        "crop_t3": "data/imagery/top_candidate_T3.png",
    }
    copies = {
        "imagery/temporal_rgb.png": derived / "change" / "temporal_rgb.png",
        "imagery/terrain_qa_mask.png": derived / "terrain" / "terrain_qa_mask.png",
        "imagery/persistent_candidates_map.png": derived / "terrain" / "persistent_candidates_map.png",
        "imagery/seasonal_transient_candidates_map.png": derived / "terrain" / "seasonal_transient_candidates_map.png",
        "imagery/T1_to_T3_signed_db.png": derived / "change" / "T1_to_T3_signed_db.png",
        "imagery/T1_to_T2_signed_db.png": derived / "change" / "T1_to_T2_signed_db.png",
        "imagery/T2_to_T3_signed_db.png": derived / "change" / "T2_to_T3_signed_db.png",
        "imagery/T1_quicklook.png": derived / "quicklooks" / "T1_quicklook.png",
        "imagery/T2_quicklook.png": derived / "quicklooks" / "T2_quicklook.png",
        "imagery/T3_quicklook.png": derived / "quicklooks" / "T3_quicklook.png",
        "imagery/top_candidate_T1.png": derived / "quicklooks" / "top_candidate_T1.png",
        "imagery/top_candidate_T2.png": derived / "quicklooks" / "top_candidate_T2.png",
        "imagery/top_candidate_T3.png": derived / "quicklooks" / "top_candidate_T3.png",
    }
    missing = []
    for dest, src in copies.items():
        if src.exists():
            shutil.copyfile(src, out / dest)
        else:
            missing.append(str(src))

    # Comparison strip: T1/T2/T3 quicklooks plus a T4 placeholder slot.
    t4_crop_available = (out / "imagery" / "T4_quicklook.png").exists()
    t4_slot_image = "data/imagery/T4_quicklook.png" if t4_crop_available else None
    comparisons = [
        {"id": "T1", "date": "2024-04-18", "image": imagery["quicklook_t1"], "available": True},
        {"id": "T2", "date": "2024-11-20", "image": imagery["quicklook_t2"], "available": True},
        {"id": "T3", "date": "2025-01-07", "image": imagery["quicklook_t3"], "available": True},
        {"id": "T4", "date": "2024-12-21", "image": t4_slot_image, "available": t4_crop_available},
    ]
    contract["imagery"] = imagery
    contract["comparisons"] = comparisons
    contract["image_bounds"] = IMAGE_BOUNDS
    (out / "derived" / "contract.json").write_text(json.dumps(contract, indent=2))

    print(f"contract written to {out / 'derived' / 'contract.json'}")
    print(f"regions: {counts}")
    print(f"t4 available: {bool(t4)} regions_tested={t4_region_count}")
    if missing:
        print(f"missing imagery (skipped): {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
