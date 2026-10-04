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
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DERIVED = Path("/home/overlord/hackathon/rs2-analysis/data/derived/exploration_v2")
DEFAULT_OUT = REPO / "public" / "data"

# Fallback metadata used only if a source-scene catalogue is unavailable.
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
    """Load T4 results from either the contract format or evaluation catalogue.

    Expected shape (all optional):
      {"regions": {"2": {"t4_status": "confirmed", "t4_mean_power": 0.04,
                         "t4_mean_signed_db": -6.9, "t4_first_seen": "..."}},
       "acquisition": {"id": "T4", "date": "2024-12-21", ...}}
    """
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text())
    if "regions" in raw:
        return raw

    regions = {}
    status_by_verdict = {
        "SURVIVED_AND_CONFIRMED": "confirmed",
        "PERSISTENT_MODERATE": "moderate_support",
        "WEAKENED_TO_TRANSIENT": "weakened",
        "OUTSIDE_T4_SWATH": "outside_coverage",
    }
    for candidate in raw.get("candidates", []):
        verdict = candidate.get("t4_verdict")
        powers = candidate.get("t4_powers") or {}
        deltas = candidate.get("t4_deltas_db") or {}
        regions[str(candidate["id"])] = {
            "t4_status": status_by_verdict.get(verdict, "pending"),
            "t4_mean_power": next(iter(powers.values()), None),
            "t4_mean_signed_db": next(iter(deltas.values()), None),
            "t4_observation": {
                "source_ids": list(powers),
                "verdict": verdict,
            } if verdict else None,
        }
    return {"regions": regions, "summary": {key: raw.get(key, 0) for key in (
        "total_tested", "covered_in_t4", "survived_confirmed", "weakened_transient", "outside_t4_swath"
    )}}


def acquisitions_from_metadata(path: Path, images: dict | None = None) -> list[dict]:
    """Normalize N source-scene records into chronologically ordered observations."""
    if not path.exists():
        return [dict(item) for item in BASE_ACQUISITIONS]
    metadata = json.loads(path.read_text())
    if isinstance(metadata, dict):
        metadata = metadata.get("scenes", metadata.get("acquisitions", []))
    by_date: dict[str, list[dict]] = {}
    for source in metadata:
        stamp = source.get("acquisition") or source.get("iso") or source.get("date")
        if not stamp:
            continue
        day = stamp[:10]
        by_date.setdefault(day, []).append(source)

    images = images or {}
    observations = []
    for index, (day, sources) in enumerate(sorted(by_date.items())):
        labels = [str(source.get("label") or source.get("id") or f"source-{index + 1}") for source in sources]
        grouped_id = labels[0].split("_")[0] if all(label.startswith("T4_") for label in labels) else labels[0]
        if all(label.startswith("T4_") for label in labels):
            role = "Validation observation"
        elif labels[0] == "T1":
            role = "Baseline"
        elif labels[0] == "T2":
            role = "Seasonal observation"
        elif labels[0] == "T3":
            role = "Later dry-season observation"
        else:
            role = "Observation"
        polygons = [source["gcp_hull_wgs84"] for source in sources if source.get("gcp_hull_wgs84")]
        footprint = None
        if polygons:
            footprint = {"type": "MultiPolygon", "coordinates": [p["coordinates"] for p in polygons]}
        image = images.get(grouped_id)
        if image is None:
            image = images.get(labels[0])
        observations.append({
            "id": grouped_id,
            "date": day,
            "iso": min(source.get("acquisition", day) for source in sources),
            "sensor": sources[0].get("sensor", "RADARSAT-2"),
            "source_ids": labels,
            "source_count": len(sources),
            "role": role,
            "primary": all(source.get("primary", True) for source in sources),
            "beam": sources[0].get("beam"),
            "polarization": sources[0].get("polarization"),
            "orbit": sources[0].get("pass_direction", sources[0].get("orbit")),
            "footprint": footprint,
            "image": image,
        })
    return observations


def union_geographic_bounds(*bounds: dict | None) -> dict | None:
    present = [value for value in bounds if value and all(key in value for key in ("west", "south", "east", "north"))]
    if not present:
        return None
    return {
        "west": min(value["west"] for value in present),
        "south": min(value["south"] for value in present),
        "east": max(value["east"] for value in present),
        "north": max(value["north"] for value in present),
    }


def geojson_bounds(geometry: dict | None) -> dict | None:
    coordinates: list[tuple[float, float]] = []

    def visit(value):
        if not isinstance(value, (list, tuple)):
            return
        if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
            coordinates.append((value[0], value[1]))
            return
        for child in value:
            visit(child)

    if geometry:
        visit(geometry.get("coordinates"))
    if not coordinates:
        return None
    return {
        "west": min(point[0] for point in coordinates),
        "south": min(point[1] for point in coordinates),
        "east": max(point[0] for point in coordinates),
        "north": max(point[1] for point in coordinates),
    }


def annotate_tile_support(features: list[dict], manifest: dict | None, tile_root: Path | None) -> dict:
    """Record whether each discovery candidate has support in the displayed tile stack.

    The support sample is the candidate geometry's geographic bounding-box center.
    It is a display-coordination aid, not an additional scientific validation result.
    """
    if not manifest or not tile_root:
        return {"candidates": len(features), "in_tile_grid": 0, "observed": 0, "temporally_classifiable": 0}
    import numpy as np
    import rasterio
    from pyproj import Transformer

    def points(value, output):
        if not isinstance(value, (list, tuple)):
            return
        if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
            output.append((value[0], value[1]))
            return
        for child in value:
            points(child, output)

    sources = []
    for tile in manifest.get("tiles", []):
        raster_path = tile_root / tile["temporal_asset"]
        with rasterio.open(raster_path) as dataset:
            sources.append({
                "tile_id": str(tile["tile_id"]),
                "transformer": Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True),
                "affine": dataset.transform,
                "width": dataset.width,
                "height": dataset.height,
                "crs": dataset.crs,
                "path": raster_path,
            })

    totals = {"candidates": len(features), "in_tile_grid": 0, "observed": 0, "temporally_classifiable": 0}
    for feature in features:
        coordinates = []
        points(feature.get("geometry", {}).get("coordinates"), coordinates)
        if coordinates:
            center_x = (min(point[0] for point in coordinates) + max(point[0] for point in coordinates)) / 2
            center_y = (min(point[1] for point in coordinates) + max(point[1] for point in coordinates)) / 2
        else:
            center_x = center_y = None
        support = {
            "tile_id": None,
            "observation_count": 0,
            "temporal_class_id": None,
            "status": "outside_tile_grid",
        }
        if center_x is not None:
            for source in sources:
                x, y = source["transformer"].transform(center_x, center_y)
                col, row = (~source["affine"]) @ (x, y)
                row, col = int(math.floor(row)), int(math.floor(col))
                if not (0 <= row < source["height"] and 0 <= col < source["width"]):
                    continue
                with rasterio.open(source["path"]) as dataset:
                    observed = dataset.read(3, window=((row, row + 1), (col, col + 1)))[0, 0]
                    temporal_class = dataset.read(1, window=((row, row + 1), (col, col + 1)))[0, 0]
                observation_count = int(observed) if np.isfinite(observed) and observed > 0 else 0
                class_id = int(temporal_class) if observation_count > 0 and np.isfinite(temporal_class) and 0 <= temporal_class <= 5 else None
                support = {
                    "tile_id": source["tile_id"],
                    "observation_count": observation_count,
                    "temporal_class_id": class_id,
                    "status": "temporal_class" if class_id is not None else ("observation_only" if observation_count else "tile_no_observation"),
                }
                break
        feature.setdefault("properties", {})["tile_support"] = support
        totals["in_tile_grid"] += int(support["tile_id"] is not None)
        totals["observed"] += int(support["observation_count"] > 0)
        totals["temporally_classifiable"] += int(support["temporal_class_id"] is not None)
    return totals


def summarize_registration_qa(registration_path: Path | None, screening_path: Path | None) -> dict:
    """Summarize local registration and candidate gates without conflating them."""
    pairs = {}
    candidates = {}
    if registration_path and registration_path.exists():
        raw = json.loads(registration_path.read_text())
        statuses = [pair.get("status", "insufficient_registration_evidence")
                    for tile in raw.values() for pair in tile.get("dates", {}).values()]
        pairs = {
            "comparisons": len(statuses),
            "passing": statuses.count("passes_local_residual_checks"),
            "insufficient": statuses.count("insufficient_registration_evidence"),
            "review_required": statuses.count("registration_review_required"),
        }
    if screening_path and screening_path.exists():
        raw = json.loads(screening_path.read_text())
        candidates = {
            "screened": raw.get("candidates_screened", 0),
            "registration_passing": raw.get("local_registration_pass", 0),
            "registration_insufficient": raw.get("local_registration_status_counts", {}).get("insufficient_registration_evidence", 0),
            "initial_screen_passing": raw.get("passes_initial_screen", 0),
        }
    return {"pair_checks": pairs, "candidate_subset": candidates,
            "interpretation": "Insufficient means evidence is unavailable; it is not a measured registration failure."}


def build_tile_previews(manifest: dict, tile_root: Path, output_dir: Path) -> tuple[list[dict], dict]:
    """Create compact, independently georeferenced PNG overlays for every temporal tile."""
    import numpy as np
    import rasterio
    from PIL import Image
    from pyproj import Transformer
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject, transform_bounds

    output_dir.mkdir(parents=True, exist_ok=True)
    palette = {
        0: (77, 100, 88, 105),       # stable
        1: (238, 113, 142, 220),     # persistent
        2: (103, 211, 206, 220),     # temporary / seasonal
        3: (202, 211, 203, 210),     # late
        4: (246, 173, 115, 225),     # progressive
        5: (240, 208, 138, 145),     # ambiguous
    }
    tile_products = []
    all_dates: set[str] = set()
    all_source_record_ids: set[str] = set()
    total_observed_pixels = 0
    total_analyzed_pixels = 0
    total_insufficient_pixels = 0
    meters_per_pixel = float(manifest.get("recipe", {}).get("resolution", 60))
    pixel_area_km2 = meters_per_pixel * meters_per_pixel / 1_000_000
    overall_bounds = {"west": math.inf, "south": math.inf, "east": -math.inf, "north": -math.inf}

    for tile in manifest.get("tiles", []):
        tile_id = str(tile["tile_id"])
        source_path = tile_root / tile["temporal_asset"]
        if not source_path.is_file():
            raise FileNotFoundError(f"Temporal tile in manifest is missing: {source_path}")
        with rasterio.open(source_path) as source:
            if source.count < 3:
                raise ValueError(f"Expected 3 temporal bands (class, change, observation count): {source_path}")
            classes = source.read(1)
            observations = source.read(3)
            data_mask = np.isfinite(classes) & (classes != 255)
            analysis_mask = data_mask & (classes >= 0) & (classes <= 5)
            coverage_mask = np.isfinite(observations) & (observations > 0)

            west, south, east, north = transform_bounds(
                source.crs, "EPSG:4326", *source.bounds, densify_pts=21
            )
            preview_size = 512
            preview_transform = from_bounds(west, south, east, north, preview_size, preview_size)
            warped_classes = np.full((preview_size, preview_size), 255, dtype=np.uint8)
            reproject(
                source=classes.astype(np.uint8), destination=warped_classes,
                src_transform=source.transform, src_crs=source.crs, src_nodata=255,
                dst_transform=preview_transform, dst_crs="EPSG:4326", dst_nodata=255,
                resampling=Resampling.nearest,
            )
            warped_observations = np.zeros((preview_size, preview_size), dtype=np.uint8)
            reproject(
                source=np.nan_to_num(observations, nan=0).astype(np.uint8), destination=warped_observations,
                src_transform=source.transform, src_crs=source.crs,
                dst_transform=preview_transform, dst_crs="EPSG:4326", dst_nodata=0,
                resampling=Resampling.nearest,
            )

            def categorical_rgba(selected_classes: set[int]) -> np.ndarray:
                output = np.zeros((preview_size, preview_size, 4), dtype=np.uint8)
                for class_id in selected_classes:
                    if class_id in palette:
                        output[warped_classes == class_id] = palette[class_id]
                return output

            coverage_colors = {
                1: (111, 194, 174, 90), 2: (111, 194, 174, 125),
                3: (103, 211, 206, 155), 4: (103, 211, 206, 185),
                5: (244, 206, 120, 210), 6: (244, 206, 120, 235),
            }
            coverage_rgba = np.zeros((preview_size, preview_size, 4), dtype=np.uint8)
            for count, color in coverage_colors.items():
                coverage_rgba[warped_observations >= count] = color

            def save_rgba(image_data: np.ndarray, filename: str) -> None:
                Image.fromarray(image_data).save(output_dir / filename, optimize=True)

            base_name = f"{tile_id}.png"
            save_rgba(categorical_rgba(set(palette)), base_name)
            save_rgba(categorical_rgba({2}), f"{tile_id}-seasonal.png")
            save_rgba(categorical_rgba({1, 4}), f"{tile_id}-persistent.png")
            save_rgba(coverage_rgba, f"{tile_id}-coverage.png")

            transformer = Transformer.from_crs(source.crs, "EPSG:4326", always_xy=True)
            grid_corners = [
                source.transform @ (0, 0),
                source.transform @ (source.width, 0),
                source.transform @ (source.width, source.height),
                source.transform @ (0, source.height),
            ]
            corners = [list(transformer.transform(x, y)) for x, y in grid_corners]
            overall_bounds["west"] = min(overall_bounds["west"], west)
            overall_bounds["east"] = max(overall_bounds["east"], east)
            overall_bounds["south"] = min(overall_bounds["south"], south)
            overall_bounds["north"] = max(overall_bounds["north"], north)

            analyzed_pixels = int(analysis_mask.sum())
            observed_pixels = int(coverage_mask.sum())
            insufficient_pixels = int((~analysis_mask & coverage_mask).sum())
            total_analyzed_pixels += analyzed_pixels
            total_observed_pixels += observed_pixels
            total_insufficient_pixels += insufficient_pixels
            all_dates.update(tile.get("dates", []))
            all_source_record_ids.update(str(value) for value in tile.get("source_record_ids", []))
            tile_products.append({
                "tile_id": tile_id,
                "crs": source.crs.to_string(),
                "corners": corners,
                "display_bounds": {"west": west, "south": south, "east": east, "north": north},
                "footprint": {"type": "Polygon", "coordinates": [corners + [corners[0]]]},
                "width": source.width,
                "height": source.height,
                "preview_width": preview_size,
                "preview_height": preview_size,
                "dates": tile.get("dates", []),
                "source_record_ids": tile.get("source_record_ids", []),
                "analysis_valid_pixels": analyzed_pixels,
                "satellite_coverage_pixels": observed_pixels,
                "insufficient_evidence_pixels": insufficient_pixels,
                "layers": {
                    "temporal_rgb": f"tiles/{base_name}",
                    "persistent_candidates": f"tiles/{tile_id}-persistent.png",
                    "seasonal_transient": f"tiles/{tile_id}-seasonal.png",
                    "coverage": f"tiles/{tile_id}-coverage.png",
                },
            })

    processing = {
        "tile_count": len(tile_products),
        "acquisition_count": len(all_source_record_ids) or len(all_dates),
        "acquisition_dates": sorted(all_dates),
        "analysis_valid_pixels": total_analyzed_pixels,
        "satellite_coverage_pixels": total_observed_pixels,
        "insufficient_evidence_pixels": total_insufficient_pixels,
        "analysis_valid_area_km2": round(total_analyzed_pixels * pixel_area_km2, 4),
        "satellite_coverage_area_km2": round(total_observed_pixels * pixel_area_km2, 4),
        "pixel_area_km2": pixel_area_km2,
        "requested_aoi_area_km2": None,
        "requested_aoi_geometry_available": False,
        "area_note": "Areas sum valid pixels from non-overlapping analysis tiles; no requested AOI geometry was included in the run manifest.",
    }
    return tile_products, {**processing, "map_bounds": overall_bounds}


def classify(feature_props: dict, t4: dict) -> dict:
    """Return temporal_class / t4_* fields for one region."""
    region_id = feature_props.get("id")
    base = CLASS_ID_TO_TEMPORAL.get(feature_props.get("class_id"), "late_unclassified")
    t4_region = (t4.get("regions") or {}).get(str(region_id), {})
    t4_status = t4_region.get("t4_status", "pending")

    if base == "persistent" and t4_status in {"confirmed", "moderate_support"}:
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
    ap.add_argument("--scene-metadata", type=Path, default=None)
    ap.add_argument("--t4-quicklook", type=Path, default=None)
    ap.add_argument("--storage-metrics", type=Path, default=None)
    ap.add_argument("--registration-qa", type=Path, default=None)
    ap.add_argument("--screening-summary", type=Path, default=None)
    ap.add_argument("--comparability-report", type=Path, default=None)
    ap.add_argument("--tile-manifest", type=Path, default=None)
    ap.add_argument("--tile-root", type=Path, default=None)
    ap.add_argument("--imagery-layout", type=Path, default=None, help="Explicit pixel extents of source preview content, excluding report annotations")
    ap.add_argument("--candidate-notes", type=Path, default=None, help="Human-authored candidate investigation notes; separate from scientific classifications")
    ap.add_argument("--region-label", default="Myanmar · RADARSAT-2 forest monitoring")
    args = ap.parse_args()

    derived = args.derived
    out = args.out
    (out / "derived").mkdir(parents=True, exist_ok=True)
    (out / "imagery").mkdir(parents=True, exist_ok=True)

    tile_products = []
    tile_processing = None
    tile_manifest = None
    tile_root = None
    if args.tile_manifest and args.tile_manifest.exists():
        tile_manifest = json.loads(args.tile_manifest.read_text())
        tile_root = args.tile_root or args.tile_manifest.parent
        tile_products, tile_processing = build_tile_previews(
            tile_manifest, tile_root, out / "tiles"
        )

    t4 = load_t4_results(args.t4_results)
    scene_metadata = args.scene_metadata or (derived / "metadata" / "scene_metadata.json")
    extra_imagery = {}
    if args.t4_quicklook and args.t4_quicklook.exists():
        extra_imagery["T4"] = "imagery/T4_quicklook.png"

    # --- candidate regions: extend with T4-ready fields -----------------
    src_geo = derived / "terrain" / "candidate_regions_terrain_qa.geojson"
    geo = json.loads(src_geo.read_text())
    for feat in geo["features"]:
        feat["properties"].update(classify(feat["properties"], t4))
    tile_candidate_support = annotate_tile_support(
        geo["features"], tile_manifest, tile_root
    )
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

    t4_summary = t4.get("summary") or {}
    t4_region_count = t4_summary.get("covered_in_t4", sum(
        region.get("t4_status") == "confirmed" for region in (t4.get("regions") or {}).values()
    ))
    acquisitions = acquisitions_from_metadata(scene_metadata, extra_imagery)
    map_bounds = union_geographic_bounds(
        MAP_BOUNDS,
        IMAGE_BOUNDS,
        tile_processing["map_bounds"] if tile_processing else None,
        *(geojson_bounds(item.get("footprint")) for item in acquisitions),
    )
    contract = {
        "contract_version": 2,
        "generated": datetime.now(timezone.utc).isoformat(),
        "scene": {
            "footprint": MAP_BOUNDS,
            "crs_analysis": "EPSG:32646",
            "crs_display": "EPSG:4326",
            "name": args.region_label,
            "product": "RADARSAT-2 SLC",
            "beam": "XF0W2",
            "polarization": "HH",
            "orbit": "Ascending",
            "resolution_m": 60,
        },
        "acquisitions": acquisitions,
        "temporal_classes": TEMPORAL_CLASSES,
        "counts": counts,
        "metrics": metrics,
        "tiles": tile_products,
        "tile_candidate_support": tile_candidate_support,
        "t4": {
            "available": bool(t4),
            "regions_tested": t4_region_count,
            "covered_candidates": t4_summary.get("covered_in_t4", t4_region_count),
            "independently_supported_candidates": t4_summary.get("survived_confirmed", sum(
                region.get("t4_status") in {"confirmed", "moderate_support"}
                for region in (t4.get("regions") or {}).values()
            )),
            "strong_support_candidates": sum(region.get("t4_status") == "confirmed" for region in (t4.get("regions") or {}).values()),
            "moderate_support_candidates": sum(region.get("t4_status") == "moderate_support" for region in (t4.get("regions") or {}).values()),
            "weakened_candidates": t4_summary.get("weakened_transient", 0),
            "outside_swath": t4_summary.get("outside_t4_swath", 0),
            "note": "Independent temporal observation support applies only to covered candidates; it does not establish land-cover cause.",
        },
        "region_label": args.region_label,
        "map_bounds": map_bounds,
        "presentation_path": [],
    }

    if tile_processing:
        contract["tile_processing"] = {
            key: value for key, value in tile_processing.items() if key != "map_bounds"
        }
    if args.storage_metrics and args.storage_metrics.exists():
        contract["processing"] = json.loads(args.storage_metrics.read_text())
    if args.comparability_report and args.comparability_report.exists():
        report = json.loads(args.comparability_report.read_text())
        contract["baseline_comparability"] = {
            "common_scene_footprint_km2": report.get("common_gcp_hull_area_km2"),
            "common_valid_grid_area_km2": (
                report.get("common_valid_grid_area_ha", 0) / 100 if report.get("common_valid_grid_area_ha") else None
            ),
            "registration_gate_passed": report.get("registration_gate_passed"),
            "note": "This is the original four-date discovery subset, separate from the expanded geographic tile run.",
        }
    if args.registration_qa or args.screening_summary:
        contract["registration_qa"] = summarize_registration_qa(args.registration_qa, args.screening_summary)
    top_candidates = metrics.get("top_persistent_candidates") or []
    top_id = top_candidates[0].get("id") if top_candidates else None
    contract["presentation_path"] = [
        {"step": 1, "id": "scale", "title": "Scale", "detail": f"{(tile_processing or {}).get('acquisition_count', len(contract['acquisitions']))} source acquisitions across {len((tile_processing or {}).get('acquisition_dates', [])) or len(contract['acquisitions'])} dates in {args.region_label}."},
        {"step": 2, "id": "detection", "title": "Detection", "detail": f"{metrics.get('total_candidates_analyzed', len(geo['features']))} detected regions."},
        {"step": 3, "id": "seasonal", "title": "Seasonal signals", "detail": f"{counts['seasonal_transient']} seasonal or transient regions."},
        {"step": 4, "id": "persistent", "title": "Persistent signals", "detail": f"{counts['persistent'] + counts['t4_validated_persistent']} persistent regions."},
        {"step": 5, "id": "validated", "title": "Independent observation", "detail": f"{counts['t4_validated_persistent']} persistent candidates supported by the later observation."},
        {"step": 6, "id": "investigate", "title": "Investigate", "candidate_id": top_id},
        {"step": 7, "id": "evidence", "title": "Evidence", "candidate_id": top_id},
    ]
    (out / "derived" / "contract.json").write_text(json.dumps(contract, indent=2))

    # --- metrics alias (stable filename for the frontend) ---------------
    shutil.copyfile(src_metrics, out / "derived" / "dashboard_metrics.json")

    # --- imagery --------------------------------------------------------
    imagery = {
        "temporal_rgb": "imagery/temporal_rgb.png",
        "terrain_qa_mask": "imagery/terrain_qa_mask.png",
        "persistent_candidates": "imagery/persistent_candidates_map.png",
        "seasonal_transient": "imagery/seasonal_transient_candidates_map.png",
        "change_t1_t3": "imagery/T1_to_T3_signed_db.png",
        "change_t1_t2": "imagery/T1_to_T2_signed_db.png",
        "change_t2_t3": "imagery/T2_to_T3_signed_db.png",
        "quicklook_t1": "imagery/T1_quicklook.png",
        "quicklook_t2": "imagery/T2_quicklook.png",
        "quicklook_t3": "imagery/T3_quicklook.png",
        "crop_t1": "imagery/top_candidate_T1.png",
        "crop_t2": "imagery/top_candidate_T2.png",
        "crop_t3": "imagery/top_candidate_T3.png",
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

    if args.t4_quicklook and args.t4_quicklook.exists():
        destination = out / "imagery" / "T4_quicklook.png"
        if args.t4_quicklook.resolve() != destination.resolve():
            shutil.copyfile(args.t4_quicklook, destination)

    # Comparison strip: T1/T2/T3 quicklooks plus a T4 placeholder slot.
    quicklook_by_id = {"T1": imagery["quicklook_t1"], "T2": imagery["quicklook_t2"], "T3": imagery["quicklook_t3"]}
    if args.t4_quicklook and args.t4_quicklook.exists():
        imagery["quicklook_t4"] = "imagery/T4_quicklook.png"
    for observation in contract["acquisitions"]:
        if observation.get("image") is None:
            observation["image"] = quicklook_by_id.get(observation["id"], imagery.get(f"quicklook_{observation['id'].lower()}"))
    comparisons = [{"id": acquisition["id"], "date": acquisition["date"],
                    "image": acquisition.get("image"), "available": bool(acquisition.get("image")),
                    "role": acquisition.get("role"), "sensor": acquisition.get("sensor")}
                   for acquisition in contract["acquisitions"]]
    contract["imagery"] = imagery
    contract["comparisons"] = comparisons
    contract["imagery_layout"] = json.loads(args.imagery_layout.read_text()) if args.imagery_layout else {}
    contract["candidate_notes"] = json.loads(args.candidate_notes.read_text()) if args.candidate_notes else {}
    if (out / "context" / "geography.geojson").exists():
        contract["geographic_context"] = {"asset": "context/geography.geojson", "source": "Natural Earth", "source_url": "https://www.naturalearthdata.com/"}
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
