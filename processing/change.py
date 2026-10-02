"""Prepared-pair radiometric change detection -> change raster -> regions -> result bundle.

Scope
-----
This module implements only the calibrated prepared-pair -> change raster ->
polygons -> API bundle portion of the vertical slice:

    two verified prepared scenes (linear power, sigma0/gamma0)
      -> common reference grid (reproject + crop)
      -> nodata-aware 3x3 linear-power mean filter
      -> signed 10*log10(after/before) in dB
      -> absolute threshold -> connected components -> minimum area filter
      -> vectorised regions (holes preserved) with geodesic area
      -> data/processed/current/{analysis.json,regions.geojson,previews}

What this module deliberately does not do
-----------------------------------------
* It does not calibrate, geocode, terrain-correct, or prepare anything. It
  consumes prepared rasters whose provenance flags are asserted upstream.
* It does not measure or correct geometric registration. Reprojection here is
  grid alignment only; registration evidence comes from
  ``manifest.registration.diagnostic``.
* It does not evaluate persistence or historical anomaly from two dates. Those
  fields are reported as not evaluable / null, never as zero.
* It does not attribute cause (deforestation, fire, flooding, agriculture,
  processing artifact) and never emits probabilities or confidence scores.

Usage
-----
    python -m processing.change \
        --manifest data/manifests/pair.json \
        --out data/processed/current \
        --threshold-db 2.0 \
        --min-area-ha 1.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
from PIL import Image
from pyproj import Geod, Transformer
from rasterio.crs import CRS
from rasterio.features import shapes as rio_shapes
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds
from scipy import ndimage
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shapely_transform, unary_union
from shapely.validation import make_valid

__all__ = ["ChangeError", "run_change_detection", "main", "region_change_statistics"]

MANIFEST_SCHEMA_VERSION = 1
ANALYSIS_SCHEMA_VERSION = 1
REQUIRED_SOURCE_COLLECTION = "Radarsat-2_Tropical_Forest_Products"
ALLOWED_QUANTITIES = ("sigma0", "gamma0")
ALLOWED_TERRAIN_CORRECTION = ("verified", "not_required")
REFERENCE_CRS_EPSG = 4326

#: Minimum number of valid neighbours (of 9) required for a filtered sample.
MIN_VALID_NEIGHBOURS = 5
#: Connectivity used for connected-component labelling.
LABEL_CONNECTIVITY = 8
#: Maximum reference-grid size, to fail loudly instead of exhausting memory.
MAX_GRID_CELLS = 40_000_000
#: Smallest accepted pixel size; anything smaller is a degenerate/identity geotransform.
MIN_PIXEL_SIZE = 1e-6
#: Upper bound on preview raster edge length.
PREVIEW_MAX_EDGE = 1024

CHANGE_DEFINITION = "10*log10(after/before)"
UNITS = "dB"
GEOD = Geod(ellps="WGS84")

SPECKLE_FILTER = (
    "3x3 box mean filter applied in linear power with nodata-aware support: "
    "each output sample is the mean of the valid (finite, positive) neighbours "
    f"in its 3x3 window, and is undefined unless at least {MIN_VALID_NEIGHBOURS} "
    "of 9 window samples are valid"
)


class ChangeError(RuntimeError):
    """Raised for any input, parameter, or processing condition that must abort."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ChangeError(message)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value: Any) -> Optional[float]:
    if _is_number(value):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def parse_utc(value: Any, field: str) -> datetime:
    """Parse a strict ISO-8601 UTC timestamp (``Z`` or ``+00:00``)."""
    _require(_nonempty_str(value), f"{field} must be a non-empty ISO-8601 UTC string")
    text = str(value).strip()
    normalised = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:  # pragma: no cover - message path
        raise ChangeError(f"{field} is not a valid ISO-8601 timestamp: {value!r}") from exc
    _require(parsed.tzinfo is not None, f"{field} must carry an explicit UTC offset: {value!r}")
    offset = parsed.utcoffset()
    _require(offset == timedelta_zero(), f"{field} must be UTC (offset +00:00): {value!r}")
    return parsed.astimezone(timezone.utc)


def timedelta_zero():
    from datetime import timedelta

    return timedelta(0)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# manifest validation
# --------------------------------------------------------------------------- #
SCENE_KEYS = (
    "id",
    "acquired_at",
    "polarization",
    "beam_mode",
    "orbit_direction",
    "relative_orbit",
    "product_type",
    "source_collection",
    "catalog_url",
    "path",
    "radiometry",
)
RADIOMETRY_KEYS = (
    "quantity",
    "representation",
    "calibration",
    "geocoding",
    "terrain_correction",
    "processing_steps",
)


def load_manifest(manifest_path: str) -> Dict[str, Any]:
    if not os.path.isfile(manifest_path):
        raise ChangeError(f"manifest not found: {manifest_path}")
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ChangeError(f"manifest is not valid JSON: {exc}") from exc
    _require(isinstance(manifest, dict), "manifest must be a JSON object")
    return manifest


def validate_manifest(manifest: Dict[str, Any], manifest_path: str) -> List[Dict[str, Any]]:
    _require(
        manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION,
        f"manifest schema_version must be {MANIFEST_SCHEMA_VERSION}, got {manifest.get('schema_version')!r}",
    )
    scenes = manifest.get("scenes")
    _require(isinstance(scenes, list), "manifest.scenes must be a list")
    _require(
        len(scenes) == 2,
        f"manifest must contain exactly 2 scenes for a pair analysis, got {len(scenes)}",
    )
    base_dir = os.path.dirname(os.path.abspath(manifest_path))
    validated: List[Dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        _require(isinstance(scene, dict), f"manifest.scenes[{index}] must be an object")
        for key in SCENE_KEYS:
            _require(key in scene, f"manifest.scenes[{index}] is missing required key {key!r}")
        for key in ("id", "polarization", "beam_mode", "orbit_direction", "product_type", "catalog_url"):
            _require(
                _nonempty_str(scene[key]),
                f"manifest.scenes[{index}].{key} must be a non-empty string",
            )
        acquired_at = parse_utc(scene["acquired_at"], f"manifest.scenes[{index}].acquired_at")
        relative_orbit = scene["relative_orbit"]
        _require(
            relative_orbit is None or _finite(relative_orbit) is not None,
            f"manifest.scenes[{index}].relative_orbit must be a finite number or null",
        )
        _require(
            scene["source_collection"] == REQUIRED_SOURCE_COLLECTION,
            f"manifest.scenes[{index}].source_collection must be "
            f"{REQUIRED_SOURCE_COLLECTION!r}, got {scene['source_collection']!r}",
        )
        _require(_nonempty_str(scene["path"]), f"manifest.scenes[{index}].path must be non-empty")
        scene_path = scene["path"]
        if not os.path.isabs(scene_path):
            candidate = os.path.normpath(os.path.join(base_dir, scene_path))
            scene_path = candidate if os.path.exists(candidate) else scene_path
        _require(
            os.path.isfile(scene_path),
            f"manifest.scenes[{index}].path does not exist: {scene['path']}",
        )
        radiometry = scene["radiometry"]
        _require(isinstance(radiometry, dict), f"manifest.scenes[{index}].radiometry must be an object")
        for key in RADIOMETRY_KEYS:
            _require(
                key in radiometry,
                f"manifest.scenes[{index}].radiometry is missing required key {key!r}",
            )
        _require(
            radiometry["quantity"] in ALLOWED_QUANTITIES,
            f"manifest.scenes[{index}].radiometry.quantity must be one of {ALLOWED_QUANTITIES}, "
            f"got {radiometry['quantity']!r}",
        )
        _require(
            radiometry["representation"] == "linear_power",
            f"manifest.scenes[{index}].radiometry.representation must be 'linear_power'; "
            f"amplitude and dB inputs are rejected, got {radiometry['representation']!r}",
        )
        _require(
            radiometry["calibration"] == "verified",
            f"manifest.scenes[{index}].radiometry.calibration must be 'verified'; "
            "this CLI cannot verify calibration",
        )
        _require(
            radiometry["geocoding"] == "verified",
            f"manifest.scenes[{index}].radiometry.geocoding must be 'verified'; "
            "this CLI cannot verify geocoding",
        )
        _require(
            radiometry["terrain_correction"] in ALLOWED_TERRAIN_CORRECTION,
            f"manifest.scenes[{index}].radiometry.terrain_correction must be one of "
            f"{ALLOWED_TERRAIN_CORRECTION}, got {radiometry['terrain_correction']!r}",
        )
        steps = radiometry["processing_steps"]
        _require(
            isinstance(steps, list) and steps and all(_nonempty_str(step) for step in steps),
            f"manifest.scenes[{index}].radiometry.processing_steps must be a non-empty list of strings",
        )
        validated.append(
            {
                "id": scene["id"],
                "acquired_at": acquired_at,
                "acquired_at_iso": iso_utc(acquired_at),
                "date": acquired_at.date().isoformat(),
                "polarization": scene["polarization"],
                "beam_mode": scene["beam_mode"],
                "orbit_direction": scene["orbit_direction"],
                "relative_orbit": None if relative_orbit is None else float(relative_orbit),
                "product_type": scene["product_type"],
                "source_collection": scene["source_collection"],
                "catalog_url": scene["catalog_url"],
                "path": scene_path,
                "quantity": radiometry["quantity"],
                "processing_steps": list(steps),
            }
        )

    first, second = validated
    _require(
        first["date"] != second["date"],
        f"the two scenes must fall on distinct UTC dates, got {first['date']} twice",
    )
    _require(
        first["acquired_at"] < second["acquired_at"],
        "manifest.scenes must be sorted ascending by acquired_at "
        f"({first['acquired_at_iso']} then {second['acquired_at_iso']})",
    )
    _require(first["id"] != second["id"], "scene ids must be unique")

    for key, label in (
        ("quantity", "radiometry.quantity"),
        ("polarization", "polarization"),
        ("beam_mode", "beam_mode"),
        ("orbit_direction", "orbit_direction"),
    ):
        _require(
            first[key] == second[key],
            f"prepared pair is incompatible: {label} differs ({first[key]!r} vs {second[key]!r})",
        )
    if first["relative_orbit"] is not None and second["relative_orbit"] is not None:
        _require(
            first["relative_orbit"] == second["relative_orbit"],
            "prepared pair is incompatible: relative_orbit differs "
            f"({first['relative_orbit']} vs {second['relative_orbit']})",
        )

    registration = manifest.get("registration")
    _require(isinstance(registration, dict), "manifest.registration must be an object")
    _require(
        registration.get("status") == "passed",
        f"manifest.registration.status must be 'passed', got {registration.get('status')!r}; "
        "registration is not measured by this CLI",
    )
    residual = _finite(registration.get("residual_pixels"))
    _require(
        residual is not None and residual >= 0,
        "manifest.registration.residual_pixels must be a finite number >= 0",
    )
    _require(
        _nonempty_str(registration.get("diagnostic")),
        "manifest.registration.diagnostic must be a non-empty string naming the inspected evidence",
    )
    return validated


# --------------------------------------------------------------------------- #
# raster inspection
# --------------------------------------------------------------------------- #
def inspect_raster(scene: Dict[str, Any]) -> Dict[str, Any]:
    """Open a prepared raster and reject anything that is not usable linear power."""
    label = f"scene {scene['id']!r}"
    try:
        dataset = rasterio.open(scene["path"])
    except Exception as exc:  # pragma: no cover - message path
        raise ChangeError(f"{label}: cannot open prepared raster {scene['path']}: {exc}") from exc
    with dataset as src:
        _require(src.count >= 1, f"{label}: prepared raster has no bands")
        _require(
            src.width > 0 and src.height > 0,
            f"{label}: prepared raster has zero extent ({src.width}x{src.height})",
        )
        _require(
            src.crs is not None,
            f"{label}: prepared raster has no CRS; a real projected/geographic CRS is required "
            "(identity or assumed CRSs are rejected)",
        )
        _require(
            src.transform is not None
            and math.isfinite(src.transform.a)
            and math.isfinite(src.transform.e)
            and abs(src.transform.a) > MIN_PIXEL_SIZE
            and abs(src.transform.e) > MIN_PIXEL_SIZE,
            f"{label}: prepared raster has a missing or degenerate transform "
            "(identity/undefined geotransform is rejected)",
        )
        dtype = (src.dtypes[0] or "").lower()
        _require(
            "complex" not in dtype,
            f"{label}: complex rasters are rejected; expected real linear power, got dtype {dtype}",
        )
        tags = {str(key).lower(): str(value) for key, value in src.tags().items()}
        for key in ("representation", "units", "unit", "quantity", "calibration", "data_type"):
            text = tags.get(key, "").lower()
            _require(
                "amplitude" not in text,
                f"{label}: raster tag {key}={tags.get(key)!r} indicates amplitude data; "
                "this CLI requires linear power",
            )
            _require(
                not any(token in text for token in ("db", "decibel")),
                f"{label}: raster tag {key}={tags.get(key)!r} indicates dB data; "
                "this CLI requires linear power",
            )
        band_tags = {str(key).lower(): str(value) for key, value in src.tags(1).items()}
        for key, value in band_tags.items():
            text = value.lower()
            _require(
                "amplitude" not in text,
                f"{label}: band tag {key}={value!r} indicates amplitude data; linear power required",
            )
            _require(
                not any(token in text for token in ("db", "decibel")),
                f"{label}: band tag {key}={value!r} indicates dB data; linear power required",
            )
        stats = src.read(1, masked=True)
        data = np.asarray(stats.filled(np.nan), dtype="float64")
        valid = np.isfinite(data)
        _require(
            bool(valid.any()),
            f"{label}: prepared raster contains no valid samples (all nodata/NaN)",
        )
        negative_fraction = float(np.count_nonzero(valid & (data < 0)) / int(valid.sum()))
        _require(
            negative_fraction <= 0.001,
            f"{label}: {negative_fraction:.1%} of valid samples are negative, which is impossible for "
            "linear power; the raster looks like dB/amplitude data and is rejected",
        )
        return {
            "scene": scene,
            "crs": src.crs,
            "transform": src.transform,
            "width": src.width,
            "height": src.height,
            "nodata": src.nodata,
            "path": scene["path"],
        }


# --------------------------------------------------------------------------- #
# reference grid
# --------------------------------------------------------------------------- #
def _wgs84_bounds(info: Dict[str, Any]) -> Tuple[float, float, float, float]:
    return transform_bounds(
        info["crs"], REFERENCE_CRS_EPSG, *rasterio.transform.array_bounds(info["height"], info["width"], info["transform"])
    )


def build_reference_grid(baseline: Dict[str, Any], followup: Dict[str, Any]) -> Dict[str, Any]:
    """Common reference grid: overlap of both footprints, aligned to the baseline pixel size."""
    west_b, south_b, east_b, north_b = _wgs84_bounds(baseline)
    west_f, south_f, east_f, north_f = _wgs84_bounds(followup)
    west, south = max(west_b, west_f), max(south_b, south_f)
    east, north = min(east_b, east_f), min(north_b, north_f)
    _require(
        west < east and south < north,
        "prepared scenes do not overlap on the map; no common reference grid exists "
        f"(baseline bbox={[west_b, south_b, east_b, north_b]}, followup bbox={[west_f, south_f, east_f, north_f]})",
    )
    crs = baseline["crs"]
    if followup["crs"] != crs:
        crs = baseline["crs"]
    left, bottom, right, top = transform_bounds(REFERENCE_CRS_EPSG, crs, west, south, east, north, densify_pts=21)
    res_x = abs(baseline["transform"].a)
    res_y = abs(baseline["transform"].e)
    _require(
        math.isfinite(res_x) and math.isfinite(res_y) and res_x > 0 and res_y > 0,
        "cannot derive a finite reference pixel size from the baseline transform",
    )
    left = math.floor(left / res_x) * res_x
    right = math.ceil(right / res_x) * res_x
    bottom = math.floor(bottom / res_y) * res_y
    top = math.ceil(top / res_y) * res_y
    width = int(round((right - left) / res_x))
    height = int(round((top - bottom) / res_y))
    _require(
        width > 0 and height > 0,
        "common reference grid collapsed to zero size; footprints do not share usable pixels",
    )
    _require(
        width * height <= MAX_GRID_CELLS,
        f"common reference grid is too large ({width}x{height}); narrow the pair or pre-crop upstream",
    )
    transform = transform_from_bounds(left, bottom, right, top, width, height)
    return {
        "crs": crs,
        "transform": transform,
        "width": width,
        "height": height,
        "bounds": (left, bottom, right, top),
        "resolution": (res_x, res_y),
    }


def warp_to_grid(info: Dict[str, Any], grid: Dict[str, Any]) -> np.ndarray:
    """Reproject + crop one prepared scene onto the reference grid (grid alignment only)."""
    destination = np.full((grid["height"], grid["width"]), np.nan, dtype="float64")
    src_nodata = info["nodata"]
    if src_nodata is None or not math.isfinite(float(src_nodata)):
        src_nodata = None
    with rasterio.open(info["path"]) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src_nodata,
            dst_transform=grid["transform"],
            dst_crs=grid["crs"],
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
            num_threads=1,
        )
    return destination


# --------------------------------------------------------------------------- #
# filtering and change
# --------------------------------------------------------------------------- #
def nodata_mean_filter(raster: np.ndarray, min_valid: int = MIN_VALID_NEIGHBOURS) -> np.ndarray:
    """3x3 mean of valid linear-power samples; undefined where support is too small."""
    valid = np.isfinite(raster) & (raster > 0)
    weights = np.ones((3, 3), dtype="float64")
    counts = ndimage.convolve(valid.astype("float64"), weights, mode="constant", cval=0.0)
    totals = ndimage.convolve(np.where(valid, raster, 0.0), weights, mode="constant", cval=0.0)
    filtered = np.full(raster.shape, np.nan, dtype="float64")
    supported = counts >= float(min_valid)
    np.divide(totals, counts, out=filtered, where=supported)
    filtered[~(np.isfinite(filtered) & (filtered > 0))] = np.nan
    return filtered


def region_change_statistics(change_db: np.ndarray, component: np.ndarray) -> Tuple[float, float]:
    """Signed median dB and median absolute dB over one region's pixels, as two distinct metrics."""
    values = change_db[component]
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ChangeError("region contains no finite change values; cannot report change_db/magnitude_db")
    return float(np.median(values)), float(np.median(np.abs(values)))


def signed_change_db(before: np.ndarray, after: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Signed 10*log10(after/before) plus the evaluable (both scenes valid) mask."""
    evaluable = np.isfinite(before) & np.isfinite(after) & (before > 0) & (after > 0)
    change = np.full(before.shape, np.nan, dtype="float64")
    np.divide(after, before, out=change, where=evaluable)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(change)
    change = np.where(evaluable, db, np.nan)
    return change, evaluable


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _to_wgs84_geometry(geometry, from_crs):
    transformer = Transformer.from_crs(from_crs, REFERENCE_CRS_EPSG, always_xy=True)
    return shapely_transform(lambda x, y, z=None: transformer.transform(x, y), geometry)


def _geodesic_area_ha(geometry, from_crs) -> float:
    """Geodesic area in hectares on the WGS84 ellipsoid, holes subtracted."""
    wgs84_geometry = _to_wgs84_geometry(geometry, from_crs)
    area_m2, _ = GEOD.geometry_area_perimeter(wgs84_geometry)
    return abs(float(area_m2)) / 10_000.0


def _cell_area_ha(transform, width: int, height: int, crs) -> float:
    """Mean geodesic area of one reference cell (approximation, documented)."""

    def cell_at(column: int, row: int):
        x0, y0 = transform @ (column, row)
        x1, y1 = transform @ (column + 1, row + 1)
        return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    samples = [cell_at(0, 0), cell_at(width - 1, height - 1), cell_at(width // 2, height // 2)]
    areas = [_geodesic_area_ha(cell, crs) for cell in samples]
    return float(sum(areas) / len(areas))


def polygonise_region(component_mask: np.ndarray, transform, crs):
    """Vectorise one component; holes preserved, part boundaries dissolved."""
    parts = []
    for geometry, _ in rio_shapes(
        component_mask.astype("uint8"), mask=component_mask, transform=transform, connectivity=4
    ):
        part = shape(geometry)
        if not part.is_valid:
            part = make_valid(part)
        parts.append(part)
    if not parts:
        return None
    geometry = unary_union(parts) if len(parts) > 1 else parts[0]
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    _ = crs  # geometry is kept in the reference CRS; WGS84 conversion happens for output
    return geometry


def _rasterize(geometry, grid: Dict[str, Any]) -> np.ndarray:
    from rasterio.features import rasterize as rio_rasterize

    return rio_rasterize(
        [(mapping(geometry), 1)],
        out_shape=(grid["height"], grid["width"]),
        transform=grid["transform"],
        fill=0,
        dtype="uint8",
        all_touched=False,
    ).astype(bool)


def _roi_statistics(grid: Dict[str, Any], scene_grid: np.ndarray, geometry) -> Dict[str, Any]:
    """ROI mean calibrated backscatter (dB) and valid fraction for one scene."""
    roi = _rasterize(geometry, grid)
    roi_count = int(np.count_nonzero(roi))
    if roi_count == 0:
        return {"mean_backscatter_db": None, "valid_fraction": 0.0, "valid_pixels": 0}
    values = scene_grid[roi]
    valid = np.isfinite(values) & (values > 0)
    valid_count = int(np.count_nonzero(valid))
    if valid_count == 0:
        return {"mean_backscatter_db": None, "valid_fraction": 0.0, "valid_pixels": 0}
    mean_db = float(10.0 * math.log10(float(values[valid].mean())))
    return {
        "mean_backscatter_db": mean_db,
        "valid_fraction": valid_count / roi_count,
        "valid_pixels": valid_count,
    }


# --------------------------------------------------------------------------- #
# previews
# --------------------------------------------------------------------------- #
def _preview_grid(grid: Dict[str, Any], bounds_wgs84: Tuple[float, float, float, float]) -> Dict[str, Any]:
    west, south, east, north = bounds_wgs84
    longest = max(grid["width"], grid["height"])
    scale = min(1.0, PREVIEW_MAX_EDGE / float(longest)) if longest else 1.0
    width = max(1, int(round(grid["width"] * scale)))
    height = max(1, int(round(grid["height"] * scale)))
    preview_transform = transform_from_bounds(west, south, east, north, width, height)
    return {
        "transform": preview_transform,
        "width": width,
        "height": height,
        "crs": CRS.from_epsg(REFERENCE_CRS_EPSG),
        "bounds": [west, south, east, north],
    }


def _warp_preview(values: np.ndarray, grid: Dict[str, Any], preview: Dict[str, Any]) -> np.ndarray:
    warped = np.full((preview["height"], preview["width"]), np.nan, dtype="float64")
    reproject(
        source=values,
        destination=warped,
        src_transform=grid["transform"],
        src_crs=grid["crs"],
        src_nodata=np.nan,
        dst_transform=preview["transform"],
        dst_crs=preview["crs"],
        resampling=Resampling.bilinear,
        num_threads=1,
    )
    return warped


def _stretch(warped: np.ndarray) -> Tuple[np.ndarray, float, float]:
    valid = warped[np.isfinite(warped)]
    if valid.size == 0:
        return np.zeros(warped.shape, dtype="float64"), 0.0, 1.0
    low, high = np.percentile(valid, [2.0, 98.0])
    if not math.isfinite(float(low)) or not math.isfinite(float(high)) or high <= low:
        low, high = float(valid.min()), float(valid.max())
    if high <= low:
        high = low + 1.0
    scaled = (warped - low) / (high - low)
    return np.clip(scaled, 0.0, 1.0), float(low), float(high)


def _gray_rgba(scaled: np.ndarray) -> np.ndarray:
    valid = np.isfinite(scaled)
    rgba = np.zeros(scaled.shape + (4,), dtype="uint8")
    level = np.nan_to_num(scaled, nan=0.0)
    grey = np.clip(np.round(level * 255.0), 0, 255).astype("uint8")
    rgba[..., 0] = grey
    rgba[..., 1] = grey
    rgba[..., 2] = grey
    rgba[..., 3] = np.where(valid, 255, 0).astype("uint8")
    return rgba


def _diverging_rgba(warped: np.ndarray, clip_db: float) -> np.ndarray:
    valid = np.isfinite(warped)
    clip_db = clip_db if clip_db > 0 else 1.0
    normalised = np.clip(np.nan_to_num(warped, nan=0.0) / clip_db, -1.0, 1.0)
    magnitude = np.abs(normalised)
    red = np.round(np.where(normalised >= 0, 255 * magnitude, 255 * (0.35 + 0.65 * (1.0 - magnitude))))
    green = np.round(np.where(normalised >= 0, 255 * (0.35 + 0.65 * (1.0 - magnitude)), 255 * (0.35 + 0.65 * (1.0 - magnitude))))
    blue = np.round(np.where(normalised >= 0, 255 * (0.35 + 0.65 * (1.0 - magnitude)), 255 * magnitude))
    rgba = np.zeros(warped.shape + (4,), dtype="uint8")
    rgba[..., 0] = np.clip(red, 0, 255).astype("uint8")
    rgba[..., 1] = np.clip(green, 0, 255).astype("uint8")
    rgba[..., 2] = np.clip(blue, 0, 255).astype("uint8")
    rgba[..., 3] = np.where(valid, 255, 0).astype("uint8")
    return rgba


def _write_png(path: str, rgba: np.ndarray) -> None:
    image = Image.fromarray(rgba, mode="RGBA")
    image.save(path, format="PNG", optimize=True)


# --------------------------------------------------------------------------- #
# main pipeline
# --------------------------------------------------------------------------- #
def _partition_areas(analysis_area_ha: float, valid_area_ha: float) -> Tuple[float, float]:
    """Return (not_evaluable_area_ha, valid_area_ha) so the two sum to analysis_area_ha."""
    _require(
        math.isfinite(analysis_area_ha) and analysis_area_ha > 0,
        "the analysis extent has zero or non-finite area; cannot report area metrics",
    )
    _require(
        math.isfinite(valid_area_ha) and valid_area_ha >= 0,
        "the evaluable area is not finite; cannot report area metrics",
    )
    tolerance = 1e-6 * analysis_area_ha
    _require(
        valid_area_ha <= analysis_area_ha + tolerance,
        f"evaluable area ({valid_area_ha:.6f} ha) exceeds the analysis area "
        f"({analysis_area_ha:.6f} ha); the area partition would be inconsistent",
    )
    valid = min(valid_area_ha, analysis_area_ha)
    return analysis_area_ha - valid, valid


def _clamp_changed_area(total_changed_area_ha: float, valid_area_ha: float) -> float:
    """Retained regions cannot cover more than the evaluable area they were measured on."""
    tolerance = 1e-6 * max(valid_area_ha, 1.0)
    _require(
        total_changed_area_ha <= valid_area_ha + tolerance,
        f"retained region area ({total_changed_area_ha:.6f} ha) exceeds the evaluable area "
        f"({valid_area_ha:.6f} ha); the metrics would be inconsistent",
    )
    return min(total_changed_area_ha, valid_area_ha)


def _analysis_id(scenes: Sequence[Dict[str, Any]], threshold_db: float, min_area_ha: float) -> str:
    payload = json.dumps(
        {
            "scenes": [[scene["id"], scene["acquired_at_iso"]] for scene in scenes],
            "threshold_db": round(float(threshold_db), 9),
            "min_area_ha": round(float(min_area_ha), 9),
        },
        sort_keys=True,
    )
    return "chg-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _write_raster(path: str, grid: Dict[str, Any], values: np.ndarray, nodata: float) -> None:
    profile = {
        "driver": "GTiff",
        "height": grid["height"],
        "width": grid["width"],
        "count": 1,
        "dtype": "float32",
        "crs": grid["crs"],
        "transform": grid["transform"],
        "tiled": False,
        "compress": "deflate",
        "predictor": 3,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.update_tags(quantity="change_db", units=UNITS, change_definition=CHANGE_DEFINITION)
        dst.write(np.where(np.isfinite(values), values, nodata).astype("float32"), 1)
        dst.update_tags(1, nodata=str(nodata))


def run_change_detection(
    manifest_path: str,
    out_dir: str,
    threshold_db: float,
    min_area_ha: float,
) -> Dict[str, Any]:
    threshold = _finite(threshold_db)
    _require(threshold is not None, f"--threshold-db must be a finite number, got {threshold_db!r}")
    _require(threshold > 0, f"--threshold-db must be strictly positive, got {threshold_db!r}")
    area = _finite(min_area_ha)
    _require(area is not None, f"--min-area-ha must be a finite number, got {min_area_ha!r}")
    _require(area >= 0, f"--min-area-ha must be non-negative, got {min_area_ha!r}")

    manifest = load_manifest(manifest_path)
    scenes = validate_manifest(manifest, manifest_path)
    baseline, followup = scenes

    baseline_info = inspect_raster(baseline)
    followup_info = inspect_raster(followup)
    grid = build_reference_grid(baseline_info, followup_info)

    baseline_grid = warp_to_grid(baseline_info, grid)
    followup_grid = warp_to_grid(followup_info, grid)
    _require(
        bool(np.isfinite(baseline_grid).any()),
        f"scene {baseline['id']!r} has no valid samples inside the common reference grid "
        "(all-invalid grid)",
    )
    _require(
        bool(np.isfinite(followup_grid).any()),
        f"scene {followup['id']!r} has no valid samples inside the common reference grid "
        "(all-invalid grid)",
    )

    baseline_filtered = nodata_mean_filter(baseline_grid)
    followup_filtered = nodata_mean_filter(followup_grid)
    change_db, evaluable = signed_change_db(baseline_filtered, followup_filtered)
    evaluable_fraction = float(np.count_nonzero(evaluable)) / float(evaluable.size)
    _require(
        evaluable_fraction > 0.0,
        "no pixel has valid support in both scenes after filtering; the common grid is all-invalid",
    )

    thresholded = evaluable & np.isfinite(change_db) & (np.abs(change_db) >= threshold)
    labels, count = ndimage.label(
        thresholded, structure=np.ones((3, 3), dtype="uint8") if LABEL_CONNECTIVITY == 8 else None
    )
    cell_area_ha = _cell_area_ha(grid["transform"], grid["width"], grid["height"], grid["crs"])
    _require(
        cell_area_ha > 0,
        "reference grid cells have zero geodesic area; cannot apply the minimum area filter",
    )
    min_pixels = 0 if area == 0 else int(math.ceil(area / cell_area_ha))

    candidates = []
    for label_value in range(1, count + 1):
        component = labels == label_value
        pixel_count = int(np.count_nonzero(component))
        if pixel_count < max(min_pixels, 1):
            continue
        geometry = polygonise_region(component, grid["transform"], grid["crs"])
        if geometry is None:
            continue
        area_ha = _geodesic_area_ha(geometry, grid["crs"])
        if area_ha < area and area > 0:
            continue
        change_median, magnitude_median = region_change_statistics(change_db, component)
        candidates.append(
            {
                "geometry": geometry,
                "area_ha": area_ha,
                "pixel_count": pixel_count,
                "quantity": baseline["quantity"],
                "change_db": change_median,
                "magnitude_db": magnitude_median,
                "priority_score": magnitude_median * math.sqrt(area_ha),
            }
        )

    def sort_key(item: Dict[str, Any]):
        wgs84_geometry = _to_wgs84_geometry(item["geometry"], grid["crs"])
        centroid = wgs84_geometry.centroid
        return (-round(item["priority_score"], 6), -round(item["area_ha"], 6), centroid.y, centroid.x)

    candidates.sort(key=sort_key)
    for index, candidate in enumerate(candidates, start=1):
        candidate["region_id"] = f"R{index:03d}"

    time_series_cache = [
        _roi_statistics(grid, baseline_filtered, candidate["geometry"]) for candidate in candidates
    ]
    followup_cache = [
        _roi_statistics(grid, followup_filtered, candidate["geometry"]) for candidate in candidates
    ]

    baseline_iso = baseline["acquired_at_iso"]
    followup_iso = followup["acquired_at_iso"]
    # detected_at is the acquisition in which the radar difference was observed, not an event onset
    detected_at = followup_iso
    features = []
    for candidate, base_stats, after_stats in zip(candidates, time_series_cache, followup_cache):
        base_db = base_stats["mean_backscatter_db"]
        after_db = after_stats["mean_backscatter_db"]
        change_from_baseline = None
        if base_db is not None and after_db is not None:
            change_from_baseline = float(after_db - base_db)
        explanation = (
            f"Radiometric backscatter change of {candidate['change_db']:+.2f} dB (median signed, "
            f"median absolute {candidate['magnitude_db']:.2f} dB) over {candidate['area_ha']:.2f} ha "
            f"between {baseline_iso} and {followup_iso}, from {candidate['quantity']} linear-power "
            "difference thresholded at "
            f"{threshold:.2f} dB. The cause of the change is not determined: two acquisitions "
            "cannot separate deforestation, flood, fire, agriculture, or processing artifacts, and "
            "no ground validation is included. The change was observed at the later acquisition "
            f"({followup_iso}); that is when the radar difference is measured, not when any event "
            "began. The event, if any, occurred at an unknown time inside the observation interval."
        )
        features.append(
            {
                "type": "Feature",
                "id": candidate["region_id"],
                "geometry": mapping(_to_wgs84_geometry(candidate["geometry"], grid["crs"])),
                "properties": {
                    "region_id": candidate["region_id"],
                    "area_ha": candidate["area_ha"],
                    "change_db": candidate["change_db"],
                    "magnitude_db": candidate["magnitude_db"],
                    "detected_at": detected_at,
                    "baseline_at": baseline_iso,
                    "observation_interval": {"start": baseline_iso, "end": followup_iso},
                    "priority_score": candidate["priority_score"],
                    "priority_units": "dB sqrt(ha)",
                    "priority_formula": "magnitude_db * sqrt(area_ha)",
                    "persistence": {
                        "status": "not_evaluable",
                        "observations_after_detection": 0,
                        "changed_observations": 0,
                        "rate": None,
                    },
                    "historical_anomaly": None,
                    "explanation": explanation,
                    "time_series": [
                        {
                            "acquired_at": baseline_iso,
                            "mean_backscatter_db": base_db,
                            "change_from_baseline_db": 0.0 if base_db is not None else None,
                            "valid_fraction": float(base_stats["valid_fraction"]),
                        },
                        {
                            "acquired_at": followup_iso,
                            "mean_backscatter_db": after_db,
                            "change_from_baseline_db": change_from_baseline,
                            "valid_fraction": float(after_stats["valid_fraction"]),
                        },
                    ],
                },
            }
        )

    grid_wgs84 = transform_bounds(
        grid["crs"],
        REFERENCE_CRS_EPSG,
        grid["bounds"][0],
        grid["bounds"][1],
        grid["bounds"][2],
        grid["bounds"][3],
        densify_pts=21,
    )
    analysis_area_ha = _geodesic_area_ha(
        unary_union(
            [
                polygonise_region(
                    np.ones((grid["height"], grid["width"]), dtype=bool), grid["transform"], grid["crs"]
                )
            ]
        ),
        grid["crs"],
    )
    valid_area_ha = _geodesic_area_ha(
        unary_union(
            [
                polygonise_region(evaluable, grid["transform"], grid["crs"])
                or polygonise_region(np.ones_like(evaluable), grid["transform"], grid["crs"])
            ]
        ),
        grid["crs"],
    )
    # contract invariant: valid_area_ha + not_evaluable_area_ha == analysis_area_ha
    not_evaluable_area_ha, valid_area_ha = _partition_areas(analysis_area_ha, valid_area_ha)
    total_changed_area_ha = float(sum(candidate["area_ha"] for candidate in candidates))
    # contract invariant: retained regions cannot cover more than the evaluable area
    total_changed_area_ha = _clamp_changed_area(total_changed_area_ha, valid_area_ha)

    preview = _preview_grid(grid, grid_wgs84)
    before_db_preview = _warp_preview(np.where(np.isfinite(baseline_filtered), np.log10(baseline_filtered) * 10.0, np.nan), grid, preview)
    after_db_preview = _warp_preview(np.where(np.isfinite(followup_filtered), np.log10(followup_filtered) * 10.0, np.nan), grid, preview)
    change_preview = _warp_preview(change_db, grid, preview)
    before_scaled, before_low, before_high = _stretch(before_db_preview)
    after_scaled, after_low, after_high = _stretch(after_db_preview)
    change_clip = threshold
    valid_change = change_preview[np.isfinite(change_preview)]
    if valid_change.size:
        change_clip = max(threshold, float(np.percentile(np.abs(valid_change), 98.0)))

    labels_for_preview = np.where(thresholded, 1, 0).astype("uint8")

    analysis = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_id": _analysis_id(scenes, threshold, area),
        "title": (
            f"Radiometric change {baseline['date']} to {followup['date']} "
            f"({baseline['quantity']}, {baseline['polarization']}, {baseline['beam_mode']})"
        ),
        "bbox": [float(value) for value in grid_wgs84],
        "scenes": [
            {
                "id": scene["id"],
                "acquired_at": scene["acquired_at_iso"],
                "polarization": scene["polarization"],
                "beam_mode": scene["beam_mode"],
                "orbit_direction": scene["orbit_direction"],
                "relative_orbit": scene["relative_orbit"],
                "product_type": scene["product_type"],
                "source_collection": scene["source_collection"],
                "catalog_url": scene["catalog_url"],
            }
            for scene in scenes
        ],
        "method": {
            "quantity": baseline["quantity"],
            "units": UNITS,
            "change_definition": CHANGE_DEFINITION,
            "threshold_db": float(threshold),
            "minimum_area_ha": float(area),
            "speckle_filter": SPECKLE_FILTER,
            "registration": {
                "status": manifest["registration"]["status"],
                "residual_pixels": float(manifest["registration"]["residual_pixels"]),
            },
            "preprocessing": [
                "Reprojected and cropped both prepared scenes onto a common reference grid defined by "
                "the overlapping footprints at the baseline pixel size; this is grid alignment only and "
                "does not measure or correct geometric registration.",
                f"Speckle reduction: {SPECKLE_FILTER}.",
                "Linear-power means converted to backscatter dB (10*log10) only where both scenes have "
                "valid support; all other pixels are nodata and excluded from evaluation.",
                "Thresholded absolute dB change, labelled components with 8-neighbour connectivity, "
                "filtered by minimum area using mean geodesic cell area, and vectorised with holes "
                "preserved.",
                "Region area_ha is the geodesic area of the thresholded pixels including subtracted "
                "holes, computed on the WGS84 ellipsoid.",
                "Previews are separately resampled onto a WGS84 pixel grid; projected rectangular "
                "extents are not used as if they were image pixels in degrees.",
            ],
        },
        "metrics": {
            "region_count": len(features),
            "total_changed_area_ha": total_changed_area_ha,
            "analysis_area_ha": analysis_area_ha,
            "valid_area_ha": valid_area_ha,
            "not_evaluable_area_ha": not_evaluable_area_ha,
            "scene_count": len(scenes),
        },
        "imagery": {
            "before": {
                "path": "before.png",
                "bounds": [float(value) for value in grid_wgs84],
                "label": f"{baseline['id']} {baseline['date']} {baseline['quantity']} before (dB)",
            },
            "after": {
                "path": "after.png",
                "bounds": [float(value) for value in grid_wgs84],
                "label": f"{followup['id']} {followup['date']} {followup['quantity']} after (dB)",
            },
            "change": {
                "path": "change.png",
                "bounds": [float(value) for value in grid_wgs84],
                "label": f"Signed change 10*log10(after/before) dB, clipped at +/-{change_clip:.2f} dB",
            },
        },
        "demo_region_id": None,
        "limitations": [
            "Registration status is asserted by the upstream manifest diagnostic; this CLI only aligns "
            "grids and does not measure or correct geometric misregistration, so residual coregistration "
            "error is carried into the change rasters.",
            "verified calibration, geocoding, and terrain-correction flags in the manifest are "
            "upstream attestations. This CLI cannot verify them and has not inspected the underlying "
            "processing or diagnostics.",
            "Regions are thresholded radiometric-change polygons, not classified forest-loss polygons: "
            "the cause of change is undetermined and no ground validation is available.",
            "Only two acquisitions are available, so temporal persistence after detection and "
            "historical anomaly are not evaluable and are reported as not_evaluable / null.",
            "detected_at is the acquisition in which the radar difference was observed, not the onset "
            "of any event; the event time, if any, is unknown inside observation_interval.",
            "Radiometric change can arise from geometry, terrain, incidence angle, processing, and "
            "speckle effects as well as surface change; magnitudes are not calibrated as loss severity.",
            f"Scene backscatter previews are stretched to the 2nd-98th percentile of valid dB samples "
            f"(before {before_low:.2f} to {before_high:.2f} dB, after {after_low:.2f} to "
            f"{after_high:.2f} dB) and are not radiometrically comparable to any external scale.",
            f"Minimum-area filtering uses mean geodesic cell area ({cell_area_ha:.6f} ha/cell), so the "
            "retained region set can differ marginally from an exact per-cell geodesic area filter.",
        ],
    }
    # RFC 7946 GeoJSON: WGS84 lon/lat, no crs member
    geojson = {"type": "FeatureCollection", "name": analysis["analysis_id"], "features": features}

    staging = _staging_dir(out_dir)
    try:
        _write_raster(os.path.join(staging, "change.tif"), grid, change_db, float("nan"))
        _write_raster(
            os.path.join(staging, "mask.tif"), grid, labels_for_preview.astype("float64"), 0.0
        )
        _write_png(os.path.join(staging, "before.png"), _gray_rgba(before_scaled))
        _write_png(os.path.join(staging, "after.png"), _gray_rgba(after_scaled))
        _write_png(os.path.join(staging, "change.png"), _diverging_rgba(change_preview, change_clip))
        _write_json(os.path.join(staging, "regions.geojson"), geojson)
        _write_json(os.path.join(staging, "analysis.json"), analysis)
        _validate_bundle(staging)
        _publish(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return analysis


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    try:
        text = json.dumps(payload, indent=2, sort_keys=False, allow_nan=False)
    except ValueError as exc:
        raise ChangeError(f"refusing to write non-finite JSON ({os.path.basename(path)}): {exc}") from exc
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


REQUIRED_BUNDLE_FILES = ("analysis.json", "regions.geojson", "change.tif", "mask.tif", "before.png", "after.png", "change.png")


def _staging_dir(out_dir: str) -> str:
    parent = os.path.dirname(os.path.abspath(out_dir)) or "."
    os.makedirs(parent, exist_ok=True)
    staging = os.path.join(parent, f".{os.path.basename(os.path.abspath(out_dir))}.staging-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    return staging


def _validate_bundle(staging: str) -> None:
    for name in REQUIRED_BUNDLE_FILES:
        path = os.path.join(staging, name)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise ChangeError(f"staged bundle is incomplete, refusing to publish: {name} missing or empty")
    with open(os.path.join(staging, "analysis.json"), "r", encoding="utf-8") as handle:
        analysis = json.load(handle)
    with open(os.path.join(staging, "regions.geojson"), "r", encoding="utf-8") as handle:
        geojson = json.load(handle)
    metrics = analysis.get("metrics", {})
    if metrics.get("region_count") != len(geojson.get("features", [])):
        raise ChangeError("staged bundle is inconsistent: region_count does not match regions.geojson")
    bbox = analysis.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ChangeError("staged bundle is inconsistent: bbox must be [west, south, east, north]")
    for key in ("before", "after", "change"):
        entry = analysis.get("imagery", {}).get(key, {})
        if entry.get("bounds") != bbox:
            raise ChangeError(
                f"staged bundle is inconsistent: imagery.{key}.bounds must equal analysis bbox"
            )
        if entry.get("path") != f"{key}.png":
            raise ChangeError(f"staged bundle is inconsistent: imagery.{key}.path is not {key}.png")
    required_metrics = (
        "region_count",
        "total_changed_area_ha",
        "analysis_area_ha",
        "valid_area_ha",
        "not_evaluable_area_ha",
        "scene_count",
    )
    for key in required_metrics:
        if key not in metrics:
            raise ChangeError(f"staged bundle is inconsistent: metrics.{key} is missing")
    _partition_areas(float(metrics["analysis_area_ha"]), float(metrics["valid_area_ha"]))
    _clamp_changed_area(float(metrics["total_changed_area_ha"]), float(metrics["valid_area_ha"]))
    for feature in geojson.get("features", []):
        if feature.get("id") != feature.get("properties", {}).get("region_id"):
            raise ChangeError("staged bundle is inconsistent: feature id and region_id disagree")


def _fsync_path(path: str) -> None:
    try:
        handle = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.fsync(handle)
    except OSError:  # pragma: no cover - platform dependent
        pass
    finally:
        os.close(handle)


def _publish(staging: str, out_dir: str) -> None:
    """Move the staged bundle into place; analysis.json lands last as the completion sentinel."""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    ordered = [name for name in REQUIRED_BUNDLE_FILES if name != "analysis.json"]
    for name in ordered:
        os.replace(os.path.join(staging, name), os.path.join(out_dir, name))
    for name in ordered:
        target = os.path.join(out_dir, name)
        if not os.path.isfile(target):
            raise ChangeError(f"publish failed, bundle incomplete: {name} not present in {out_dir}")
    os.replace(os.path.join(staging, "analysis.json"), os.path.join(out_dir, "analysis.json"))
    for name in REQUIRED_BUNDLE_FILES:
        _fsync_path(os.path.join(out_dir, name))
    directory = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(directory)
    except OSError:  # pragma: no cover - platform dependent
        pass
    finally:
        os.close(directory)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m processing.change",
        description=(
            "Detect radiometric change between two verified prepared SAR scenes and publish the "
            "result bundle (analysis.json, regions.geojson, previews, derived rasters)."
        ),
    )
    parser.add_argument("--manifest", required=True, help="path to the prepared pair manifest (schema_version 1)")
    parser.add_argument("--out", required=True, help="output bundle directory (published atomically)")
    parser.add_argument(
        "--threshold-db",
        required=True,
        type=float,
        help="absolute signed dB change threshold; must be finite and strictly positive",
    )
    parser.add_argument(
        "--min-area-ha",
        required=True,
        type=float,
        help="minimum region area in hectares; must be finite and non-negative",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        analysis = run_change_detection(args.manifest, args.out, args.threshold_db, args.min_area_ha)
    except ChangeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    metrics = analysis["metrics"]
    print(
        f"wrote {analysis['analysis_id']} to {os.path.abspath(args.out)}: "
        f"{metrics['region_count']} region(s), "
        f"{metrics['total_changed_area_ha']:.2f} ha changed, "
        f"{metrics['valid_area_ha']:.2f} ha evaluable of {metrics['analysis_area_ha']:.2f} ha analysis area, "
        f"{metrics['not_evaluable_area_ha']:.2f} ha not evaluable"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
