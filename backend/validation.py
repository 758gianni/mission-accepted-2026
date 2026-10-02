"""Structural validation for a ForestWatch result bundle.

The bundle contract is versioned. This module checks a candidate bundle
(``analysis.json`` + ``regions.geojson`` + declared PNG previews) against
the contract and refuses to serve anything that is malformed, incomplete
or internally inconsistent.

Two design rules drive everything here:

* **Never fabricate.** A value that the contract declares nullable stays
  ``null`` (for example two-date persistence and historical anomaly, which
  are *unavailable*, not zero). No derived or invented value is ever
  substituted for a missing one.
* **Never leak.** Error messages are short labels about the contract, and
  never contain filesystem paths, raw parser output or secrets.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

ANALYSIS_FILENAME = "analysis.json"
REGIONS_FILENAME = "regions.geojson"

SCHEMA_VERSION = 1
IMAGERY_KEYS: tuple[str, ...] = ("before", "after", "change")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

QUANTITIES = frozenset({"sigma0", "gamma0"})
UNITS = "dB"
CHANGE_DEFINITION = "10*log10(after/before)"
PRIORITY_UNITS = "dB sqrt(ha)"
PRIORITY_FORMULA = "magnitude_db * sqrt(area_ha)"
PERSISTENCE_STATUSES = frozenset({"not_evaluable", "observed"})

GEOMETRY_TYPES = frozenset({"Polygon", "MultiPolygon"})
MINIMUM_SEPARATE_DATES = 2

_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$"
)
_SIMPLE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class BundleValidationError(ValueError):
    """A bundle is malformed, incomplete or inconsistent with the contract.

    ``label`` is a sanitized pointer such as ``analysis.method.units``; it
    never embeds a filesystem path or raw tool output.
    """

    def __init__(self, label: str, detail: str) -> None:
        super().__init__(f"{label}: {detail}")
        self.label = label
        self.detail = detail


@dataclass(frozen=True)
class ImageryEntry:
    """A validated, bundle-contained PNG preview."""

    key: str
    filename: str
    path: str
    bounds: tuple[float, float, float, float]
    label: str
    size_bytes: int


@dataclass(frozen=True)
class ValidatedBundle:
    """A bundle that satisfies the contract and is safe to serve verbatim."""

    analysis: dict[str, Any]
    regions: dict[str, Any]
    imagery: Mapping[str, ImageryEntry]
    region_index: Mapping[str, dict[str, Any]]
    demo_region_id: str | None
    scene_count: int
    warnings: tuple[str, ...] = field(default=())


# --------------------------------------------------------------------------
# primitive checks
# --------------------------------------------------------------------------


def _fail(label: str, detail: str) -> None:
    raise BundleValidationError(label, detail)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail(label, "expected an object")
    return value


def _list(value: Any, label: str, *, allow_empty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        _fail(label, "expected an array")
    if not value and not allow_empty:
        _fail(label, "must not be empty")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(label, "expected a non-empty string")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(label, "expected a number")
    number = float(value)
    if not math.isfinite(number):
        _fail(label, "must be a finite number")
    return number


def _non_negative(value: Any, label: str) -> float:
    number = _number(value, label)
    if number < 0:
        _fail(label, "must not be negative")
    return number


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(label, "expected an integer")
    return value


def _present(mapping: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        _fail(f"{label}.{key}", "is required")
    return mapping[key]


def _nullable(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return _number(value, label)


def _bounded_fraction(value: Any, label: str) -> float:
    number = _number(value, label)
    if not 0.0 <= number <= 1.0:
        _fail(label, "must be between 0 and 1")
    return number


def parse_utc_timestamp(value: Any, label: str) -> datetime:
    """Parse an ISO 8601 timestamp that must be expressed in UTC."""
    if not isinstance(value, str):
        _fail(label, "expected an ISO 8601 UTC timestamp string")
    if not _UTC_TIMESTAMP.match(value):
        _fail(label, "must be ISO 8601 in UTC (for example 2026-01-15T10:00:00Z)")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(label, "is not a valid ISO 8601 timestamp")
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        _fail(label, "must be UTC")
    return parsed


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def _position(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        _fail(label, "expected a [longitude, latitude] position")
    longitude = _number(value[0], f"{label}[0]")
    latitude = _number(value[1], f"{label}[1]")
    if not -180.0 <= longitude <= 180.0:
        _fail(f"{label}[0]", "longitude must be within [-180, 180]")
    if not -90.0 <= latitude <= 90.0:
        _fail(f"{label}[1]", "latitude must be within [-90, 90]")
    return longitude, latitude


def _ring(value: Any, label: str) -> None:
    ring = _list(value, label)
    if len(ring) < 4:
        _fail(label, "a linear ring needs at least four positions")
    positions = [_position(item, f"{label}[{index}]") for index, item in enumerate(ring)]
    if positions[0] != positions[-1]:
        _fail(label, "a linear ring must be closed (first position equals last)")


def _polygon(value: Any, label: str) -> None:
    polygon = _list(value, label)
    for index, ring in enumerate(polygon):
        _ring(ring, f"{label}[{index}]")


def _bbox(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        _fail(label, "expected [west, south, east, north]")
    west = _number(value[0], f"{label}[0]")
    south = _number(value[1], f"{label}[1]")
    east = _number(value[2], f"{label}[2]")
    north = _number(value[3], f"{label}[3]")
    if not -180.0 <= west <= 180.0:
        _fail(f"{label}[0]", "longitude must be within [-180, 180]")
    if not -180.0 <= east <= 180.0:
        _fail(f"{label}[2]", "longitude must be within [-180, 180]")
    if not -90.0 <= south <= 90.0:
        _fail(f"{label}[1]", "latitude must be within [-90, 90]")
    if not -90.0 <= north <= 90.0:
        _fail(f"{label}[3]", "latitude must be within [-90, 90]")
    if west >= east:
        _fail(label, "west must be strictly less than east")
    if south >= north:
        _fail(label, "south must be strictly less than north")
    return west, south, east, north


# --------------------------------------------------------------------------
# analysis.json
# --------------------------------------------------------------------------


def _scene(value: Any, label: str) -> datetime:
    scene = _mapping(value, label)
    _text(_present(scene, "id", label), f"{label}.id")
    if not _SIMPLE_NAME.match(str(scene["id"])):
        _fail(f"{label}.id", "must be a simple identifier")
    acquired_at = parse_utc_timestamp(
        _present(scene, "acquired_at", label), f"{label}.acquired_at"
    )
    _text(_present(scene, "polarization", label), f"{label}.polarization")
    _text(_present(scene, "beam_mode", label), f"{label}.beam_mode")
    _text(_present(scene, "orbit_direction", label), f"{label}.orbit_direction")
    relative_orbit = _present(scene, "relative_orbit", label)
    if relative_orbit is not None:
        _integer(relative_orbit, f"{label}.relative_orbit")
    _text(_present(scene, "product_type", label), f"{label}.product_type")
    _text(_present(scene, "source_collection", label), f"{label}.source_collection")
    _text(_present(scene, "catalog_url", label), f"{label}.catalog_url")
    return acquired_at


def _registration(value: Any, label: str) -> None:
    registration = _mapping(value, label)
    _text(_present(registration, "status", label), f"{label}.status")
    _nullable(_present(registration, "residual_pixels", label), f"{label}.residual_pixels")


def _method(value: Any) -> list[Any]:
    label = "analysis.method"
    method = _mapping(value, label)
    quantity = _text(_present(method, "quantity", label), f"{label}.quantity")
    if quantity not in QUANTITIES:
        _fail(f"{label}.quantity", "must be 'sigma0' or 'gamma0'")
    units = _text(_present(method, "units", label), f"{label}.units")
    if units != UNITS:
        _fail(f"{label}.units", f"must be '{UNITS}'")
    change_definition = _text(
        _present(method, "change_definition", label), f"{label}.change_definition"
    )
    if change_definition != CHANGE_DEFINITION:
        _fail(f"{label}.change_definition", f"must be '{CHANGE_DEFINITION}'")
    _number(_present(method, "threshold_db", label), f"{label}.threshold_db")
    _non_negative(_present(method, "minimum_area_ha", label), f"{label}.minimum_area_ha")
    _text(_present(method, "speckle_filter", label), f"{label}.speckle_filter")
    _registration(_present(method, "registration", label), f"{label}.registration")
    preprocessing = _list(
        _present(method, "preprocessing", label),
        f"{label}.preprocessing",
        allow_empty=True,
    )
    for index, step in enumerate(preprocessing):
        _text(step, f"{label}.preprocessing[{index}]")
    return preprocessing


def _metrics(value: Any, scene_count: int) -> dict[str, Any]:
    label = "analysis.metrics"
    metrics = _mapping(value, label)
    region_count = _integer(_present(metrics, "region_count", label), f"{label}.region_count")
    if region_count < 0:
        _fail(f"{label}.region_count", "must not be negative")
    _non_negative(_present(metrics, "total_changed_area_ha", label), f"{label}.total_changed_area_ha")
    _non_negative(_present(metrics, "valid_area_ha", label), f"{label}.valid_area_ha")
    _non_negative(
        _present(metrics, "not_evaluable_area_ha", label), f"{label}.not_evaluable_area_ha"
    )
    declared_scene_count = _integer(
        _present(metrics, "scene_count", label), f"{label}.scene_count"
    )
    if declared_scene_count != scene_count:
        _fail(
            f"{label}.scene_count",
            "does not match the number of entries in analysis.scenes",
        )
    return metrics


def _imagery_path(key: str, value: Any, label: str) -> str:
    path = _text(value, label)
    if not _SIMPLE_NAME.match(path):
        _fail(label, "must be a plain file name inside the bundle")
    if path in {".", ".."} or path.startswith("."):
        _fail(label, "must be a plain file name inside the bundle")
    expected = f"{key}.png"
    if path != expected:
        _fail(label, f"must be '{expected}'")
    return path


def _imagery_entry(key: str, value: Any) -> tuple[str, tuple[float, float, float, float], str]:
    label = f"analysis.imagery.{key}"
    entry = _mapping(value, label)
    if set(entry) != {"path", "bounds", "label"}:
        _fail(label, "must declare exactly 'path', 'bounds' and 'label'")
    path = _imagery_path(key, _present(entry, "path", label), f"{label}.path")
    bounds = _bbox(_present(entry, "bounds", label), f"{label}.bounds")
    text = _text(_present(entry, "label", label), f"{label}.label")
    return path, bounds, text


def _validate_analysis(document: Any) -> tuple[dict[str, Any], int, str | None, list[str]]:
    label = "analysis"
    analysis = _mapping(document, label)
    schema_version = _integer(_present(analysis, "schema_version", label), f"{label}.schema_version")
    if schema_version != SCHEMA_VERSION:
        _fail(f"{label}.schema_version", f"unsupported schema version; expected {SCHEMA_VERSION}")
    _text(_present(analysis, "analysis_id", label), f"{label}.analysis_id")
    _text(_present(analysis, "title", label), f"{label}.title")
    _bbox(_present(analysis, "bbox", label), f"{label}.bbox")

    scenes = _list(_present(analysis, "scenes", label), f"{label}.scenes")
    dates = [
        _scene(scene, f"{label}.scenes[{index}]") for index, scene in enumerate(scenes)
    ]
    distinct = {date.isoformat() for date in dates}
    if len(distinct) < MINIMUM_SEPARATE_DATES:
        _fail(
            f"{label}.scenes",
            f"requires at least {MINIMUM_SEPARATE_DATES} separate acquisition dates",
        )

    _method(_present(analysis, "method", label))
    _metrics(_present(analysis, "metrics", label), scene_count=len(scenes))

    imagery = _mapping(_present(analysis, "imagery", label), f"{label}.imagery")
    if set(imagery) != set(IMAGERY_KEYS):
        _fail(f"{label}.imagery", f"must declare exactly {', '.join(IMAGERY_KEYS)}")
    for key in IMAGERY_KEYS:
        _imagery_entry(key, imagery[key])

    demo_region_id = _present(analysis, "demo_region_id", label)
    if demo_region_id is not None:
        demo_region_id = _text(demo_region_id, f"{label}.demo_region_id")

    limitations = _list(
        _present(analysis, "limitations", label), f"{label}.limitations", allow_empty=True
    )
    for index, item in enumerate(limitations):
        _text(item, f"{label}.limitations[{index}]")

    return dict(analysis), len(scenes), demo_region_id, list(limitations)


# --------------------------------------------------------------------------
# regions.geojson
# --------------------------------------------------------------------------


def _persistence(value: Any, label: str) -> None:
    persistence = _mapping(value, label)
    status = _text(_present(persistence, "status", label), f"{label}.status")
    if status not in PERSISTENCE_STATUSES:
        _fail(f"{label}.status", "must be 'not_evaluable' or 'observed'")
    observations = _integer(
        _present(persistence, "observations_after_detection", label),
        f"{label}.observations_after_detection",
    )
    changed = _integer(
        _present(persistence, "changed_observations", label),
        f"{label}.changed_observations",
    )
    if observations < 0 or changed < 0:
        _fail(label, "observation counts must not be negative")
    if changed > observations:
        _fail(f"{label}.changed_observations", "must not exceed observations_after_detection")
    rate = _present(persistence, "rate", label)
    if rate is None:
        if status == "observed":
            _fail(f"{label}.rate", "is required when status is 'observed'")
    else:
        rate_value = _number(rate, f"{label}.rate")
        if not 0.0 <= rate_value <= 1.0:
            _fail(f"{label}.rate", "must be between 0 and 1")
        if status == "not_evaluable":
            _fail(f"{label}.rate", "must be null when status is 'not_evaluable'")


def _time_series(value: Any, label: str) -> None:
    series = _list(value, label, allow_empty=True)
    previous: datetime | None = None
    for index, item in enumerate(series):
        item_label = f"{label}[{index}]"
        entry = _mapping(item, item_label)
        acquired_at = parse_utc_timestamp(
            _present(entry, "acquired_at", item_label), f"{item_label}.acquired_at"
        )
        if previous is not None and acquired_at <= previous:
            _fail(f"{item_label}.acquired_at", "must be strictly ascending")
        previous = acquired_at
        _nullable(
            _present(entry, "mean_backscatter_db", item_label),
            f"{item_label}.mean_backscatter_db",
        )
        _nullable(
            _present(entry, "change_from_baseline_db", item_label),
            f"{item_label}.change_from_baseline_db",
        )
        _bounded_fraction(
            _present(entry, "valid_fraction", item_label), f"{item_label}.valid_fraction"
        )


def _region_properties(value: Any, label: str) -> None:
    properties = _mapping(value, label)
    _text(_present(properties, "region_id", label), f"{label}.region_id")
    _non_negative(_present(properties, "area_ha", label), f"{label}.area_ha")
    _number(_present(properties, "change_db", label), f"{label}.change_db")
    _non_negative(_present(properties, "magnitude_db", label), f"{label}.magnitude_db")
    parse_utc_timestamp(
        _present(properties, "detected_at", label), f"{label}.detected_at"
    )
    parse_utc_timestamp(
        _present(properties, "last_observed_unchanged_at", label),
        f"{label}.last_observed_unchanged_at",
    )
    onset = _mapping(
        _present(properties, "onset_interval", label), f"{label}.onset_interval"
    )
    start = parse_utc_timestamp(
        _present(onset, "start", f"{label}.onset_interval"), f"{label}.onset_interval.start"
    )
    end = parse_utc_timestamp(
        _present(onset, "end", f"{label}.onset_interval"), f"{label}.onset_interval.end"
    )
    if end < start:
        _fail(f"{label}.onset_interval", "end must not precede start")
    _non_negative(_present(properties, "priority_score", label), f"{label}.priority_score")
    units = _text(_present(properties, "priority_units", label), f"{label}.priority_units")
    if units != PRIORITY_UNITS:
        _fail(f"{label}.priority_units", f"must be '{PRIORITY_UNITS}'")
    formula = _text(
        _present(properties, "priority_formula", label), f"{label}.priority_formula"
    )
    if formula != PRIORITY_FORMULA:
        _fail(f"{label}.priority_formula", f"must be '{PRIORITY_FORMULA}'")
    _persistence(
        _present(properties, "persistence", label), f"{label}.persistence"
    )
    _nullable(
        _present(properties, "historical_anomaly", label), f"{label}.historical_anomaly"
    )
    _text(_present(properties, "explanation", label), f"{label}.explanation")
    _time_series(_present(properties, "time_series", label), f"{label}.time_series")


def _geometry(value: Any, label: str) -> None:
    geometry = _mapping(value, label)
    geometry_type = _text(_present(geometry, "type", label), f"{label}.type")
    coordinates = _present(geometry, "coordinates", label)
    if geometry_type == "Polygon":
        _polygon(coordinates, f"{label}.coordinates")
        return
    if geometry_type == "MultiPolygon":
        multi = _list(coordinates, f"{label}.coordinates")
        for index, polygon in enumerate(multi):
            _polygon(polygon, f"{label}.coordinates[{index}]")
        return
    _fail(f"{label}.type", "must be 'Polygon' or 'MultiPolygon'")


def _validate_regions(document: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    label = "regions"
    regions = _mapping(document, label)
    if _present(regions, "type", label) != "FeatureCollection":
        _fail(f"{label}.type", "must be 'FeatureCollection'")
    features = _list(
        _present(regions, "features", label), f"{label}.features", allow_empty=True
    )
    index: dict[str, dict[str, Any]] = {}
    for position, feature in enumerate(features):
        feature_label = f"{label}.features[{position}]"
        entry = _mapping(feature, feature_label)
        if _present(entry, "type", feature_label) != "Feature":
            _fail(f"{feature_label}.type", "must be 'Feature'")
        feature_id = _text(_present(entry, "id", feature_label), f"{feature_label}.id")
        if feature_id in index:
            _fail(f"{feature_label}.id", "duplicates an earlier feature id")
        _geometry(_present(entry, "geometry", feature_label), f"{feature_label}.geometry")
        properties = _mapping(
            _present(entry, "properties", feature_label), f"{feature_label}.properties"
        )
        _region_properties(properties, f"{feature_label}.properties")
        if properties["region_id"] != feature_id:
            _fail(
                f"{feature_label}.properties.region_id",
                "must match the feature id",
            )
        index[feature_id] = dict(entry)
    return dict(regions), index


# --------------------------------------------------------------------------
# imagery on disk
# --------------------------------------------------------------------------


def resolve_imagery(
    bundle_dir, imagery: Mapping[str, Any]
) -> dict[str, ImageryEntry]:
    """Resolve declared imagery to real, contained, non-symlink PNG files.

    ``bundle_dir`` must already be a real directory (checked by the loader).
    Only the three declared keys are considered, and a path is accepted only
    when it is a plain file name, resolves inside the bundle directory,
    carries no symlink component and starts with the PNG signature.
    """
    root = bundle_dir.resolve()
    resolved: dict[str, ImageryEntry] = {}
    for key in IMAGERY_KEYS:
        entry = _mapping(imagery[key], f"analysis.imagery.{key}")
        path = _imagery_path(key, _present(entry, "path", f"analysis.imagery.{key}"),
                             f"analysis.imagery.{key}.path")
        bounds = _bbox(
            _present(entry, "bounds", f"analysis.imagery.{key}"),
            f"analysis.imagery.{key}.bounds",
        )
        text = _text(
            _present(entry, "label", f"analysis.imagery.{key}"),
            f"analysis.imagery.{key}.label",
        )
        candidate = root / path
        label = f"analysis.imagery.{key}.path"
        if candidate.parent != root:
            _fail(label, "must resolve inside the bundle directory")
        if candidate.is_symlink():
            _fail(label, "must not be a symbolic link")
        try:
            stat = candidate.lstat()
        except OSError:
            _fail(label, "declared image file is missing")
        import stat as stat_module

        if not stat_module.S_ISREG(stat.st_mode):
            _fail(label, "declared image must be a regular file")
        try:
            with candidate.open("rb") as handle:
                if handle.read(len(PNG_MAGIC)) != PNG_MAGIC:
                    _fail(label, "declared image must be a PNG file")
        except BundleValidationError:
            raise
        except OSError:
            _fail(label, "declared image file could not be read")
        resolved[key] = ImageryEntry(
            key=key,
            filename=path,
            path=path,
            bounds=bounds,
            label=text,
            size_bytes=stat.st_size,
        )
    return resolved


def cross_check(analysis: dict[str, Any], regions: dict[str, Any],
                index: Mapping[str, dict[str, Any]], imagery: Mapping[str, ImageryEntry],
                demo_region_id: str | None) -> tuple[str, ...]:
    """Validate consistency *across* the bundle files. Returns warnings."""
    metrics = _mapping(analysis["metrics"], "analysis.metrics")
    if metrics["region_count"] != len(index):
        _fail("analysis.metrics.region_count", "does not match the number of regions.geojson features")
    if demo_region_id is not None and demo_region_id not in index:
        _fail("analysis.demo_region_id", "does not match any region id in regions.geojson")
    if len(regions["features"]) != len(index):
        _fail("regions.features", "contains duplicate region ids")

    warnings: list[str] = []
    for key in IMAGERY_KEYS:
        declared = _mapping(analysis["imagery"][key], f"analysis.imagery.{key}")
        bounds = _bbox(declared["bounds"], f"analysis.imagery.{key}.bounds")
        if bounds != imagery[key].bounds:
            _fail(f"analysis.imagery.{key}.bounds", "does not match the validated imagery entry")
    bbox = _bbox(analysis["bbox"], "analysis.bbox")
    for key in IMAGERY_KEYS:
        west, south, east, north = imagery[key].bounds
        if not (
            west >= bbox[0] and south >= bbox[1] and east <= bbox[2] and north <= bbox[3]
        ):
            warnings.append(
                f"imagery.{key}.bounds extends outside analysis.bbox; serving values as declared"
            )
    return tuple(warnings)


def build_validated_bundle(
    analysis_document: Any,
    regions_document: Any,
    bundle_dir,
) -> ValidatedBundle:
    """Validate both JSON documents plus the declared imagery on disk."""
    analysis, scene_count, demo_region_id, _ = _validate_analysis(analysis_document)
    regions, index = _validate_regions(regions_document)
    imagery = resolve_imagery(bundle_dir, _mapping(analysis["imagery"], "analysis.imagery"))
    warnings = cross_check(analysis, regions, index, imagery, demo_region_id)
    return ValidatedBundle(
        analysis=analysis,
        regions=regions,
        imagery=imagery,
        region_index=index,
        demo_region_id=demo_region_id,
        scene_count=scene_count,
        warnings=warnings,
    )


__all__ = [
    "ANALYSIS_FILENAME",
    "BundleValidationError",
    "IMAGERY_KEYS",
    "ImageryEntry",
    "REGIONS_FILENAME",
    "SCHEMA_VERSION",
    "ValidatedBundle",
    "build_validated_bundle",
    "parse_utc_timestamp",
    "resolve_imagery",
]
