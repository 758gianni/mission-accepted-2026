"""Credential-free selection of compatible RADARSAT-2 scenes from a public STAC catalog.

Scope
-----
This module implements only the offline scene-selection step of the vertical
slice:

    a supplied public STAC FeatureCollection (local JSON file, already fetched)
      -> client-side size / product-type filtering
      -> grouping by matching polarization, beam, orbit and applied LUT
      -> real polygon intersection with an explicit overlap fraction
      -> a deterministic choice of 2 or 3 compatible scenes

Usage::

    python -m catalog.select CATALOG.json \\
        --count 3 --max-scene-mb 700 --min-gap-days 90 \\
        --min-overlap 99.0 --out data/selection.json

Design constraints honoured here
--------------------------------
* No network access, no authentication and no product download. The catalog
  file is read as-is; the module imports no HTTP client.
* Every threshold is supplied by the caller. There is no built-in risk score,
  no hidden weighting and no implicit default: the ranking order is the fixed
  lexicographic order reported in ``selection_order`` and documented in
  ``docs/SCENE-SELECTION.md``.
* Geometry work is real polygon intersection in geographic coordinates, with
  hectares computed on the WGS84 ellipsoid and cross-checked against a spherical
  Lambert azimuthal equal-area projection. Unsupported geometry and
  antimeridian-spanning footprints are rejected with an explicit reason.
* Dates are compared as UTC calendar days. The elapsed interval between two
  acquisition instants is reported alongside, because flooring elapsed days
  understates a calendar-day separation by one whenever the two scenes were
  acquired seconds apart across midnight.
* Unknown metadata is reported, never guessed. An item whose size cannot be
  checked against ``--max-scene-mb`` is not selectable; a missing LUT or beam
  imposes no constraint but is listed in ``unknown_metadata``.
* Selected public items are preserved in full, with credential-bearing asset
  hrefs stripped. Signed or bearer-guarded assets are removed and the public
  item links are kept, so nothing in the output can carry a secret.
* No causal attribution, no deforestation, fire or logging claim, and no
  confidence or probability value is produced anywhere in this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pyproj import CRS, Geod, Transformer
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from shapely.validation import make_valid

__all__ = [
    "SelectionError",
    "Criteria",
    "Candidate",
    "load_catalog",
    "select_scenes",
    "build_parser",
    "main",
    "TOOL_NAME",
    "SELECTION_SCHEMA_VERSION",
    "SELECTION_ORDER",
]

TOOL_NAME = "catalog.select"
__version__ = "1.0.0"
SELECTION_SCHEMA_VERSION = 1

#: Only these counts are meaningful for a baseline/follow-up comparison.
ALLOWED_COUNTS = (2, 3)

GEOD = Geod(ellps="WGS84")
#: Sphere radius used for the independent equal-area cross-check (metres).
EQUAL_AREA_SPHERE_RADIUS_M = 6370997.0
#: WGS84 geographic CRS identifiers accepted for a footprint.
ACCEPTED_GEOGRAPHIC_CRS = frozenset(
    {
        "EPSG:4326",
        "EPSG:4269",
        "CRS84",
        "urn:ogc:def:crs:OGC:1.3:CRS84",
        "urn:ogc:def:crs:EPSG::4326",
    }
)
#: STAC ``geometry`` types this module can intersect and measure.
SUPPORTED_GEOMETRY_TYPES = ("Polygon",)
#: Explicit ceiling on the number of candidate sets enumerated in one cluster.
#: Exceeding it is an error, never a silent truncation of the search.
MAX_CANDIDATE_SETS = 200_000
#: Attributes that make two scenes comparable. ``None`` means "unknown", which
#: imposes no constraint instead of being filled in from another item.
GROUPING_ATTRIBUTES = ("polarization", "beam", "orbit", "applied_lut")

#: The published lexicographic ranking. Every entry is "larger is better", so
#: the candidate sets are ordered ascending on the negation of each term.
SELECTION_ORDER = (
    "1. every pair is compatible on polarization, beam, orbit and applied_lut where both are known",
    "2. every pair's overlap percent of the smaller footprint is >= --min-overlap",
    "3. every pair's calendar-day separation is >= --min-gap-days",
    "4. every item's size is <= --max-scene-mb",
    "rank by minimum pairwise overlap percent of the smaller footprint (largest first)",
    "rank by the common intersection area of all selected scenes in hectares (largest first)",
    "rank by the total calendar-day span between the first and last acquisition (smallest first)",
    "rank by total size in megabytes (smallest first)",
    "tie-break on the ascending sorted tuple of item ids, so the result is reproducible",
)

#: Intentional over-broad query-parameter and property-name detectors. They may
#: refuse a benign link, but they never let a secret through, and every refusal
#: is reported in the ``sanitization`` block of the output.
_CREDENTIAL_QUERY_KEYS = frozenset(
    {
        "access_token",
        "accesstoken",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "awsaccesskeyid",
        "bearer",
        "credential",
        "credentials",
        "expires",
        "expiry",
        "jwt",
        "key",
        "passwd",
        "password",
        "pwd",
        "sas",
        "secret",
        "session",
        "sessiontoken",
        "se",
        "sig",
        "signature",
        "signed",
        "sp",
        "sv",
        "token",
        "x-amz-credential",
        "x-amz-security-token",
        "x-amz-signature",
    }
)
_CREDENTIAL_NAME_RE = re.compile(
    r"(?i)(pass(word|wd)?|secret|token|api[_-]?key|credential|authorization|bearer|session[_-]?id)"
)
_USERINFO_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://[^/@]*@", re.IGNORECASE)
_PUBLIC_AUTH_SCHEMES = frozenset({"public", "none", "basic-public"})
_HA_PER_M2 = 1.0 / 10_000.0


class SelectionError(Exception):
    """A caller-visible failure: bad arguments, unusable catalog, or no candidate."""


@dataclass(frozen=True)
class Criteria:
    """Caller-supplied, fully explicit selection thresholds."""

    count: int
    max_scene_mb: float
    min_gap_days: int
    min_overlap_percent: float
    product_types: Tuple[str, ...] = ()

    def validate(self) -> None:
        if self.count not in ALLOWED_COUNTS:
            raise SelectionError(
                f"--count must be one of {ALLOWED_COUNTS}, got {self.count}"
            )
        if self.max_scene_mb <= 0:
            raise SelectionError("--max-scene-mb must be greater than zero")
        if self.min_gap_days < 0:
            raise SelectionError("--min-gap-days must not be negative")
        if not (0.0 <= self.min_overlap_percent <= 100.0):
            raise SelectionError("--min-overlap must be between 0 and 100 percent")

    def as_json(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "max_scene_mb": self.max_scene_mb,
            "min_gap_days": self.min_gap_days,
            "min_overlap_percent": self.min_overlap_percent,
            "product_types": list(self.product_types) if self.product_types else "any",
        }


@dataclass(frozen=True)
class Candidate:
    """One usable STAC item, with its footprint measured and its unknowns listed."""

    index: int
    item_id: str
    acquired: datetime
    acquired_date: date_cls
    geometry: BaseGeometry
    footprint_ha_geodesic: float
    footprint_ha_equal_area: float
    equal_area_origin: Tuple[float, float]
    group_key: Tuple[Any, Any, Any, Any]
    unknown_fields: Tuple[str, ...] = ()
    feature: Dict[str, Any] = field(default_factory=dict)
    order_key: str = ""
    product_type: Optional[str] = None
    megabytes: Optional[float] = None
    megabytes_source: Optional[str] = None
    polarization: Optional[Tuple[str, ...]] = None
    polarization_text: Optional[str] = None
    beam: Optional[str] = None
    orbit: Optional[Any] = None
    applied_lut: Optional[str] = None

    @property
    def date_text(self) -> str:
        return self.acquired_date.isoformat()

    def unknown_report(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "datetime": self.acquired.isoformat(),
            "unknown_fields": list(self.unknown_fields),
            "product_type": self.product_type,
            "megabytes": self.megabytes,
            "megabytes_source": self.megabytes_source,
            "polarization": self.polarization_text,
            "beam": self.beam,
            "orbit": _orbit_json(self.orbit),
            "applied_lut": self.applied_lut,
        }


# --------------------------------------------------------------------------- #
# geometry: real polygons, geodesic and equal-area hectares, explicit refusals
# --------------------------------------------------------------------------- #
def _round6(value: float) -> float:
    return round(float(value), 6)


def _geodesic_area_ha(geometry: BaseGeometry) -> float:
    area_m2, _ = GEOD.geometry_area_perimeter(geometry)
    return abs(area_m2) * _HA_PER_M2


def _equal_area_crs(lon0: float, lat0: float) -> CRS:
    return CRS.from_proj4(
        f"+proj=laea +lat_0={lat0:.6f} +lon_0={lon0:.6f} "
        f"+R={EQUAL_AREA_SPHERE_RADIUS_M:.0f} +units=m +no_defs"
    )


def _equal_area_area_ha(geometry: BaseGeometry, origin: Tuple[float, float]) -> float:
    transformer = Transformer.from_crs("EPSG:4326", _equal_area_crs(*origin), always_xy=True)
    projected = shapely_transform(transformer.transform, geometry)
    return abs(projected.area) * _HA_PER_M2


def _antimeridian_reason(geometry: BaseGeometry) -> Optional[str]:
    lons: List[float] = []
    rings = [geometry.exterior, *geometry.interiors] if geometry.geom_type == "Polygon" else []
    for ring in rings:
        ring_coords = list(ring.coords)
        for a, b in zip(ring_coords, ring_coords[1:]):
            if abs(b[0] - a[0]) > 180.0:
                return (
                    "antimeridian-spanning ring: consecutive longitudes jump "
                    f"{b[0] - a[0]:.6f} degrees"
                )
        lons.extend(point[0] for point in ring_coords)
    if lons and (max(lons) - min(lons)) > 180.0:
        return (
            "antimeridian-spanning footprint: longitude extent "
            f"{max(lons) - min(lons):.6f} degrees exceeds 180"
        )
    return None


def _polygon_only(geometry: BaseGeometry) -> Optional[str]:
    if geometry.is_empty:
        return "empty geometry"
    if geometry.geom_type not in SUPPORTED_GEOMETRY_TYPES:
        return (
            f"unsupported geometry type {geometry.geom_type!r}; only "
            f"{', '.join(SUPPORTED_GEOMETRY_TYPES)} footprints are supported"
        )
    if not geometry.is_valid:
        repaired = make_valid(geometry)
        if repaired.geom_type not in SUPPORTED_GEOMETRY_TYPES:
            return (
                f"unsupported repaired geometry type {repaired.geom_type!r} after make_valid"
            )
        return None
    return None


def _normalise_geometry(raw: Any) -> Tuple[Optional[BaseGeometry], Optional[str], bool]:
    """Return ``(geometry, refusal_reason, repaired)`` for a STAC item geometry."""
    if raw is None:
        return None, "missing geometry", False
    try:
        geometry = shapely_shape(raw)
    except Exception as exc:  # noqa: BLE001 - the reason is reported to the caller
        return None, f"unparseable geometry ({exc.__class__.__name__})", False
    reason = _polygon_only(geometry)
    if reason is not None:
        return None, reason, False
    repaired = False
    if not geometry.is_valid:
        geometry = make_valid(geometry)
        reason = _polygon_only(geometry)
        if reason is not None:
            return None, reason, True
        repaired = True
    antimeridian = _antimeridian_reason(geometry)
    if antimeridian is not None:
        return None, antimeridian, repaired
    if geometry.area <= 0.0:
        return None, "degenerate polygon with zero area in degrees", repaired
    return geometry, None, repaired


# --------------------------------------------------------------------------- #
# metadata extraction (unknown is None, never inferred)
# --------------------------------------------------------------------------- #
def _first_property(properties: Dict[str, Any], *names: str) -> Tuple[Any, Optional[str]]:
    for name in names:
        if name in properties and properties[name] not in (None, ""):
            return properties[name], name
    return None, None


def _parse_instant(properties: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[str]]:
    value, field_name = _first_property(properties, "datetime", "start_datetime")
    if value is None:
        return None, "missing datetime and start_datetime"
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, f"unparseable {field_name}={text!r}"
    if parsed.tzinfo is None:
        return None, f"{field_name}={text!r} has no UTC offset"
    return parsed.astimezone(timezone.utc), None


def _polarization_key(value: Any) -> Tuple[Optional[Tuple[str, ...]], Optional[str]]:
    """Return ``(grouping_key, display)`` for a polarization string.

    The grouping key is order-insensitive (``VV VH`` and ``VH VV`` are the same
    scene) while the display keeps the published token order.
    """
    if value is None:
        return None, None
    tokens = [token for token in re.split(r"[\s,+]+", str(value).strip().upper()) if token]
    if not tokens:
        return None, None
    return tuple(sorted(tokens)), "+".join(tokens)


def _orbit_key(value: Any, state: Any) -> Optional[Tuple[Any, Any]]:
    """Orbit identity as a hashable ``(relative_orbit, orbit_state)`` pair."""
    if value is None:
        return None
    try:
        orbit: Any = int(str(value).strip())
    except ValueError:
        orbit = str(value).strip()
    return (orbit, None if state is None else str(state).strip().lower())


def _orbit_json(orbit: Optional[Tuple[Any, Any]]) -> Optional[Dict[str, Any]]:
    if orbit is None:
        return None
    return {"relative_orbit": orbit[0], "orbit_state": orbit[1]}


def _size_megabytes(properties: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    value, field_name = _first_property(properties, "megabytes")
    if value is not None:
        try:
            return float(value), field_name
        except (TypeError, ValueError):
            return None, None
    value, field_name = _first_property(properties, "file:size", "raster:bytes")
    if value is not None:
        try:
            return float(value) / (1024.0 * 1024.0), f"{field_name} (converted from bytes)"
        except (TypeError, ValueError):
            return None, None
    return None, None


def _declared_crs(properties: Dict[str, Any]) -> Optional[str]:
    value, _ = _first_property(properties, "proj:code", "proj:epsg")
    return None if value is None else str(value).strip()


def _build_candidate(feature: Dict[str, Any], index: int) -> Tuple[Optional[Candidate], Optional[str], bool]:
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return None, "feature has no properties object", False
    item_id = str(feature.get("id") or f"feature-index-{index}")

    acquired, reason = _parse_instant(properties)
    if acquired is None:
        return None, f"{item_id}: {reason}", False

    geometry, reason, repaired = _normalise_geometry(feature.get("geometry"))
    if geometry is None:
        return None, f"{item_id}: {reason}", repaired

    declared_crs = _declared_crs(properties)
    if declared_crs is not None and declared_crs not in ACCEPTED_GEOGRAPHIC_CRS:
        return None, f"{item_id}: declared proj:code {declared_crs!r} is not WGS84 geographic", repaired

    unknown: List[str] = []
    if declared_crs is None:
        unknown.append("proj:code")

    product_type, _ = _first_property(properties, "product_type", "processing:level")
    megabytes, megabytes_source = _size_megabytes(properties)

    raw_polarization, _ = _first_property(properties, "polarization", "sar:polarizations")
    polarization, polarization_text = _polarization_key(raw_polarization)
    if polarization is None:
        unknown.append("polarization")

    raw_beam, _ = _first_property(properties, "beam_mnemonic")
    if raw_beam is None:
        raw_beam, _ = _first_property(properties, "sar:beam_ids")
        if isinstance(raw_beam, (list, tuple)) and raw_beam:
            raw_beam = raw_beam[0]
    beam = None if raw_beam is None else str(raw_beam).strip().upper() or None
    if beam is None:
        unknown.append("beam")

    raw_orbit, _ = _first_property(properties, "sat:relative_orbit")
    raw_state, _ = _first_property(properties, "sat:orbit_state")
    orbit = _orbit_key(raw_orbit, raw_state)
    if orbit is None:
        unknown.append("orbit")

    raw_lut, _ = _first_property(properties, "applied_lut")
    applied_lut = None if raw_lut is None else str(raw_lut).strip() or None
    if applied_lut is None:
        unknown.append("applied_lut")

    if megabytes is None:
        unknown.append("megabytes")

    origin = (_round6(geometry.centroid.x), _round6(geometry.centroid.y))
    candidate = Candidate(
        index=index,
        item_id=item_id,
        acquired=acquired,
        acquired_date=acquired.date(),
        geometry=geometry,
        footprint_ha_geodesic=round(_geodesic_area_ha(geometry), 4),
        footprint_ha_equal_area=round(_equal_area_area_ha(geometry, origin), 4),
        equal_area_origin=origin,
        group_key=(polarization, beam, orbit, applied_lut),
        unknown_fields=tuple(unknown),
        feature=feature,
        order_key=str(properties.get("order_key") or item_id),
        product_type=None if product_type is None else str(product_type).strip() or None,
        megabytes=megabytes,
        megabytes_source=megabytes_source,
        polarization=polarization,
        polarization_text=polarization_text,
        beam=beam,
        orbit=orbit,
        applied_lut=applied_lut,
    )
    return candidate, None, repaired


# --------------------------------------------------------------------------- #
# compatibility, calendar-day gaps, overlap
# --------------------------------------------------------------------------- #
def _compatible(left: Candidate, right: Candidate) -> bool:
    return all(
        lv is None or rv is None or lv == rv
        for lv, rv in zip(left.group_key, right.group_key)
    )


def _calendar_day_gap_days(left: Candidate, right: Candidate) -> int:
    return (right.acquired_date - left.acquired_date).days


def _elapsed_seconds(left: Candidate, right: Candidate) -> int:
    return int((right.acquired - left.acquired).total_seconds())


def _intersection_geometry(left: Candidate, right: Candidate) -> Optional[BaseGeometry]:
    try:
        inter = left.geometry.intersection(right.geometry)
    except Exception as exc:  # noqa: BLE001 - reported instead of guessed
        raise SelectionError(
            f"intersection failed for {left.item_id} and {right.item_id}: {exc}"
        ) from exc
    if inter.is_empty:
        return None
    if inter.geom_type not in SUPPORTED_GEOMETRY_TYPES:
        raise SelectionError(
            f"unsupported intersection geometry {inter.geom_type!r} between "
            f"{left.item_id} and {right.item_id}"
        )
    return inter


def _overlap_report(left: Candidate, right: Candidate) -> Dict[str, Any]:
    earlier, later = sorted((left, right), key=lambda c: (c.acquired, c.item_id))
    days = _calendar_day_gap_days(earlier, later)
    inter = _intersection_geometry(earlier, later)
    if inter is None:
        overlap_ha_geodesic = 0.0
        overlap_ha_equal_area = 0.0
    else:
        overlap_ha_geodesic = _geodesic_area_ha(inter)
        overlap_ha_equal_area = _equal_area_area_ha(inter, earlier.equal_area_origin)
    smaller = min(earlier.footprint_ha_geodesic, later.footprint_ha_geodesic)
    percent = (100.0 * overlap_ha_geodesic / smaller) if smaller > 0 else 0.0
    return {
        "earlier_item_id": earlier.item_id,
        "later_item_id": later.item_id,
        "gap_calendar_days": days,
        "gap_elapsed_seconds": _elapsed_seconds(earlier, later),
        "intersection_ha_geodesic": round(overlap_ha_geodesic, 4),
        "intersection_ha_equal_area": round(overlap_ha_equal_area, 4),
        "percent_of_smaller_footprint": round(percent, 4),
        "smaller_footprint_ha": round(smaller, 4),
        "covers_entire_smaller_footprint": bool(percent >= 99.9999),
    }


def _pairwise_overlap(left: Candidate, right: Candidate) -> Tuple[float, float]:
    inter = _intersection_geometry(left, right)
    if inter is None:
        return 0.0, 0.0
    overlap_ha = _geodesic_area_ha(inter)
    smaller = min(left.footprint_ha_geodesic, right.footprint_ha_geodesic)
    percent = (100.0 * overlap_ha / smaller) if smaller > 0 else 0.0
    return overlap_ha, percent


def _common_area_ha(candidates: Sequence[Candidate]) -> Tuple[float, float]:
    """Area of the n-way common footprint, geodesic and equal-area hectares."""
    origin = candidates[0].equal_area_origin
    common = candidates[0].geometry
    for other in candidates[1:]:
        if common.is_empty:
            break
        try:
            common = common.intersection(other.geometry)
        except Exception as exc:  # noqa: BLE001 - reported instead of guessed
            raise SelectionError(
                f"common intersection failed between {candidates[0].item_id} and {other.item_id}: {exc}"
            ) from exc
    if common.is_empty or common.geom_type not in SUPPORTED_GEOMETRY_TYPES:
        return 0.0, 0.0
    return round(_geodesic_area_ha(common), 4), round(_equal_area_area_ha(common, origin), 4)


# --------------------------------------------------------------------------- #
# credential stripping
# --------------------------------------------------------------------------- #
def _credential_url_reason(href: Any) -> Optional[str]:
    if not isinstance(href, str) or not href:
        return None
    if _USERINFO_URL_RE.match(href):
        return "URL carries embedded userinfo"
    if "?" not in href:
        return None
    query = href.split("?", 1)[1]
    for chunk in re.split(r"[&;]", query):
        key = chunk.split("=", 1)[0].strip().lower().replace("%20", " ")
        if key in _CREDENTIAL_QUERY_KEYS:
            return f"query parameter {key!r} may carry a credential or signature"
    return None


def _asset_auth_reason(asset: Any) -> Optional[str]:
    if not isinstance(asset, dict):
        return None
    href_reason = _credential_url_reason(asset.get("href"))
    if href_reason is not None:
        return href_reason
    refs = asset.get("auth:refs")
    if isinstance(refs, (list, tuple)) and refs:
        extra = sorted(str(ref) for ref in refs if str(ref).lower() not in _PUBLIC_AUTH_SCHEMES)
        if extra:
            return f"asset is guarded by non-public auth:refs {extra}"
    return None


def _sanitize_properties(properties: Any, findings: List[Dict[str, str]]) -> Dict[str, Any]:
    if not isinstance(properties, dict):
        return {}
    clean: Dict[str, Any] = {}
    for key, value in properties.items():
        if _CREDENTIAL_NAME_RE.search(str(key)):
            findings.append({"kind": "property", "name": str(key), "reason": "credential-like property name"})
            continue
        if isinstance(value, str):
            reason = _credential_url_reason(value)
            if reason is not None:
                findings.append({"kind": "property_value", "name": str(key), "reason": reason})
                continue
        clean[key] = value
    return clean


def _sanitize_feature(feature: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    findings: List[Dict[str, str]] = []
    clean = {key: value for key, value in feature.items() if key not in ("assets", "links", "properties")}

    assets = feature.get("assets")
    if isinstance(assets, dict):
        kept: Dict[str, Any] = {}
        for key, asset in assets.items():
            reason = _asset_auth_reason(asset)
            if reason is not None:
                findings.append({"kind": "asset", "name": str(key), "reason": reason})
                continue
            kept[key] = asset
        clean["assets"] = kept

    links = feature.get("links")
    if isinstance(links, list):
        kept_links = []
        for link in links:
            if not isinstance(link, dict):
                continue
            reason = _credential_url_reason(link.get("href"))
            if reason is not None:
                findings.append(
                    {"kind": "link", "name": str(link.get("rel") or "unnamed"), "reason": reason}
                )
                continue
            kept_links.append(link)
        clean["links"] = kept_links

    clean["properties"] = _sanitize_properties(feature.get("properties"), findings)
    return clean, findings


# --------------------------------------------------------------------------- #
# catalog loading
# --------------------------------------------------------------------------- #
def load_catalog(path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Read a local STAC FeatureCollection and return ``(document, provenance)``."""
    if not os.path.isfile(path):
        raise SelectionError(f"catalog file not found: {path}")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise SelectionError(f"cannot read catalog file {path}: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelectionError(f"catalog file {path} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise SelectionError(f"catalog file {path} must contain a JSON object")
    doc_type = document.get("type")
    if doc_type != "FeatureCollection":
        raise SelectionError(
            f"catalog file {path} has type {doc_type!r}; a STAC FeatureCollection is required"
        )
    if not isinstance(document.get("features"), list):
        raise SelectionError(f"catalog file {path} has no features array")
    provenance = {
        "path": os.path.abspath(path),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "features_total": len(document["features"]),
        "collection": document.get("collection"),
        "network_access": "none: the file was read from disk and never fetched",
        "authentication": "none: no token is read, requested or required",
    }
    return document, provenance


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #
def _combinations(candidates: Sequence[Candidate], count: int) -> Iterable[Tuple[Candidate, ...]]:
    from itertools import combinations

    return combinations(candidates, count)


def _compatibility_clusters(candidates: Sequence[Candidate]) -> List[List[Candidate]]:
    """Connected components of the "compatible" relation.

    Two scenes are compatible when every grouping attribute that both of them
    state agrees. Unknown values join the relation instead of blocking it, so the
    components are a superset of the feasible sets: every feasible set lies
    wholly inside one component, and :func:`_feasible` still checks each pair.
    """
    parent = list(range(len(candidates)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for i, j in _combinations(list(range(len(candidates))), 2):
        if _compatible(candidates[i], candidates[j]):
            union(i, j)

    grouped: Dict[int, List[Candidate]] = {}
    for index, candidate in enumerate(candidates):
        grouped.setdefault(find(index), []).append(candidate)
    return [grouped[key] for key in sorted(grouped, key=lambda root: grouped[root][0].acquired)]


def _common_group_json(members: Sequence[Candidate]) -> Dict[str, Any]:
    first = members[0]
    common: Dict[str, Any] = {}
    for position, name in enumerate(GROUPING_ATTRIBUTES):
        values = {member.group_key[position] for member in members}
        shared = next(iter(values)) if len(values) == 1 else None
        if name == "polarization":
            texts = {member.polarization_text for member in members}
            common[name] = first.polarization_text if texts == {first.polarization_text} else None
        elif name == "orbit":
            common[name] = _orbit_json(shared) if isinstance(shared, tuple) else None
        else:
            common[name] = shared
    return common


def _cluster_label(members: Sequence[Candidate]) -> str:
    common = _common_group_json(members)
    parts = []
    for name in GROUPING_ATTRIBUTES:
        value = common[name]
        if isinstance(value, dict):
            parts.append(f"{name}={value['orbit_state'] or '?'}:{value['relative_orbit']}")
        else:
            parts.append(f"{name}={'?' if value is None else value}")
    label = "|".join(parts)
    if any(member.unknown_fields for member in members):
        label += f"|+{sum(1 for m in members if m.unknown_fields)} item(s) with unknown fields"
    return label


def _feasible(
    subset: Sequence[Candidate], criteria: Criteria
) -> Tuple[bool, List[str], float, float]:
    reasons: List[str] = []
    for left, right in _combinations(subset, 2):
        earlier, later = sorted((left, right), key=lambda c: (c.acquired, c.item_id))
        if not _compatible(earlier, later):
            reasons.append(
                f"{earlier.item_id} vs {later.item_id}: incompatible polarization, beam, "
                "orbit or applied_lut where both values are known"
            )
        gap_days = _calendar_day_gap_days(earlier, later)
        if gap_days < criteria.min_gap_days:
            reasons.append(
                f"{earlier.item_id} vs {later.item_id}: calendar-day separation is "
                f"{gap_days} < --min-gap-days {criteria.min_gap_days}"
            )
        _, percent = _pairwise_overlap(earlier, later)
        if percent < criteria.min_overlap_percent:
            reasons.append(
                f"{earlier.item_id} vs {later.item_id}: overlap {percent:.4f}% of the smaller "
                f"footprint < --min-overlap {criteria.min_overlap_percent}"
            )
    if reasons:
        return False, reasons, 0.0, 0.0
    percents = [_pairwise_overlap(a, b)[1] for a, b in _combinations(subset, 2)]
    common_geodesic, _ = _common_area_ha(subset)
    return True, [], min(percents) if percents else 100.0, common_geodesic


def select_scenes(document: Dict[str, Any], criteria: Criteria, provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Select ``criteria.count`` compatible scenes and return the full report."""
    criteria.validate()
    features = document.get("features") or []
    rejected: List[Dict[str, str]] = []
    geometry_repairs: List[Dict[str, str]] = []
    unknown_sizes: List[str] = []
    candidates: List[Candidate] = []
    requested_types = {value.strip().upper() for value in criteria.product_types if value.strip()}

    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            rejected.append({"item": f"feature-index-{index}", "reason": "not a JSON object"})
            continue
        candidate, reason, repaired = _build_candidate(feature, index)
        if repaired:
            geometry_repairs.append(
                {"item": str(feature.get("id") or f"feature-index-{index}"), "reason": "invalid polygon repaired with shapely.make_valid"}
            )
        if candidate is None:
            rejected.append({"item": str(feature.get("id") or f"feature-index-{index}"), "reason": reason or "unusable item"})
            continue
        if requested_types and (candidate.product_type or "").strip().upper() not in requested_types:
            rejected.append(
                {
                    "item": candidate.item_id,
                    "reason": f"product_type {candidate.product_type!r} is not one of {sorted(requested_types)}",
                }
            )
            continue
        if candidate.megabytes is None:
            unknown_sizes.append(candidate.item_id)
            rejected.append(
                {
                    "item": candidate.item_id,
                    "reason": "size is unknown, so --max-scene-mb cannot be checked; the item is not selectable",
                }
            )
            continue
        if candidate.megabytes > criteria.max_scene_mb:
            rejected.append(
                {
                    "item": candidate.item_id,
                    "reason": f"size {candidate.megabytes:g} MB ({candidate.megabytes_source}) > --max-scene-mb {criteria.max_scene_mb:g}",
                }
            )
            continue
        candidates.append(candidate)

    candidates.sort(key=lambda c: (c.acquired, c.item_id))
    clusters = _compatibility_clusters(candidates)

    group_report = []
    feasible_sets: List[Tuple[Tuple[Any, ...], Tuple[Candidate, ...], float, float]] = []
    rejections_by_group: List[Dict[str, Any]] = []
    for cluster_index, members in enumerate(clusters):
        label = _cluster_label(members)
        common_key = _common_group_json(members)
        group_report.append(
            {
                "cluster": label,
                "cluster_index": cluster_index,
                "group_key": common_key,
                "member_item_ids": [c.item_id for c in members],
                "acquisition_dates": [c.date_text for c in members],
                "unknown_fields_by_item": {c.item_id: list(c.unknown_fields) for c in members},
                "candidate_sets_enumerated": 0,
                "candidate_sets_feasible": 0,
            }
        )
        entry = group_report[-1]
        if len(members) < criteria.count:
            entry["note"] = f"only {len(members)} member(s); --count {criteria.count} cannot be satisfied"
            continue
        total_sets = math.comb(len(members), criteria.count)
        if total_sets > MAX_CANDIDATE_SETS:
            raise SelectionError(
                f"cluster {label} would need {total_sets} candidate sets of {criteria.count} scenes, "
                f"above the explicit enumeration limit of {MAX_CANDIDATE_SETS}; narrow the catalog "
                "(for example with --product-type) instead of silently truncating the search"
            )
        entry["candidate_sets_enumerated"] = total_sets
        group_reasons: Dict[str, int] = {}
        for subset in _combinations(members, criteria.count):
            ok, reasons, min_percent, common_ha = _feasible(subset, criteria)
            if ok:
                entry["candidate_sets_feasible"] += 1
                span_days = (subset[-1].acquired_date - subset[0].acquired_date).days
                total_mb = sum(c.megabytes or 0.0 for c in subset)
                tiebreak = tuple(sorted(c.item_id for c in subset))
                feasible_sets.append(
                    (
                        (-round(min_percent, 6), -round(common_ha, 4), span_days, total_mb, tiebreak),
                        subset,
                        min_percent,
                        common_ha,
                    )
                )
            else:
                for reason in reasons:
                    key = reason.split(":", 1)[-1].strip()
                    group_reasons[key] = group_reasons.get(key, 0) + 1
        if group_reasons:
            rejections_by_group.append(
                {"cluster": label, "refused_candidate_set_reasons": group_reasons}
            )

    report: Dict[str, Any] = {
        "tool": TOOL_NAME,
        "tool_version": __version__,
        "schema_version": SELECTION_SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "input": provenance or {"path": None, "features_total": len(features)},
        "criteria": criteria.as_json(),
        "criteria_definitions": {
            "size_filter": "megabytes (or file:size converted from bytes) <= --max-scene-mb, applied client-side",
            "product_type_filter": "client-side match against --product-type; no product type is assumed when it is absent",
            "grouping": "polarization, beam (beam_mnemonic / sar:beam_ids), orbit (sat:orbit_state + sat:relative_orbit) and applied_lut; two scenes are compatible when every one of these that both of them state agrees, so an unknown value imposes no constraint and is reported",
            "date_separation": "difference between UTC acquisition calendar days; gap_elapsed_seconds is reported separately and is never floored into the day count",
            "overlap": "full polygon intersection in EPSG:4326, not bounding boxes; percent_of_smaller_footprint = intersection hectares / hectares of the smaller of the two footprints",
            "areas": "hectares on the WGS84 ellipsoid via pyproj.Geod, cross-checked against a spherical Lambert azimuthal equal-area projection centred on the first candidate's footprint centroid",
            "geometry": "Polygon footprints only; invalid polygons repaired with shapely.make_valid and reported; antimeridian-spanning footprints and all other geometry types are refused with a reason",
            "claims": "this module attributes no cause to any observed difference; the overlap percent is a measured geometric fraction only, not a likelihood or a score",
        },
        "selection_order": list(SELECTION_ORDER),
        "rejected_items": rejected,
        "geometry_repairs": geometry_repairs,
        "items_with_unknown_size": unknown_sizes,
        "unknown_metadata": {
            "items": [c.unknown_report() for c in candidates],
            "policy": "unknown metadata is listed and left unset; no value is copied from a sibling item",
        },
        "groups": group_report,
    }
    if rejections_by_group:
        report["refused_candidate_sets"] = rejections_by_group

    if not feasible_sets:
        blockers = []
        for entry in group_report:
            entry_block = {
                "cluster": entry["cluster"],
                "candidate_sets_enumerated": entry["candidate_sets_enumerated"],
                "candidate_sets_feasible": entry["candidate_sets_feasible"],
            }
            if "note" in entry:
                entry_block["note"] = entry["note"]
            for refused in rejections_by_group:
                if refused["cluster"] == entry["cluster"]:
                    entry_block["refused_candidate_set_reasons"] = refused["refused_candidate_set_reasons"]
            blockers.append(entry_block)
        if not blockers:
            raise SelectionError(
                f"no usable item survived filtering for --count {criteria.count}; "
                f"rejected: {json.dumps(rejected[:5], sort_keys=True)}"
            )
        raise SelectionError(
            f"no set of {criteria.count} scenes satisfies the criteria; groups considered: "
            + json.dumps(blockers, sort_keys=True)
        )

    feasible_sets.sort(key=lambda entry: entry[0])
    _, selected, min_percent, common_ha = feasible_sets[0]

    sanitized_items: List[Dict[str, Any]] = []
    sanitization_findings: List[Dict[str, str]] = []
    for candidate in selected:
        clean, findings = _sanitize_feature(candidate.feature)
        for finding in findings:
            finding = dict(finding)
            finding["item"] = candidate.item_id
        sanitization_findings.extend(findings)
        sanitized_items.append(clean)

    spans = []
    for left, right in zip(selected, selected[1:]):
        spans.append(
            {
                "from_item_id": left.item_id,
                "to_item_id": right.item_id,
                "from_date": left.date_text,
                "to_date": right.date_text,
                "gap_calendar_days": _calendar_day_gap_days(left, right),
                "gap_elapsed_seconds": _elapsed_seconds(left, right),
            }
        )
    pairwise = [_overlap_report(a, b) for a, b in _combinations(selected, 2)]
    equal_area_checks = []
    for pair in pairwise:
        if pair["intersection_ha_geodesic"] <= 0:
            continue
        delta = abs(pair["intersection_ha_equal_area"] - pair["intersection_ha_geodesic"])
        relative = delta / pair["intersection_ha_geodesic"]
        equal_area_checks.append(relative)
        pair["equal_area_cross_check_relative_difference"] = round(relative, 6)
    max_difference = max(equal_area_checks) if equal_area_checks else 0.0

    report["selection"] = {
        "count": len(selected),
        "cluster": _cluster_label(selected),
        "group_key": _common_group_json(selected),
        "acquisition_dates": [c.date_text for c in selected],
        "acquisition_instants": [c.acquired.isoformat().replace("+00:00", "Z") for c in selected],
        "item_ids": [c.item_id for c in selected],
        "gaps_calendar_days": [span["gap_calendar_days"] for span in spans],
        "gap_elapsed_seconds": [span["gap_elapsed_seconds"] for span in spans],
        "consecutive_pairs": spans,
        "total_span_calendar_days": (selected[-1].acquired_date - selected[0].acquired_date).days,
        "scene_megabytes": [c.megabytes for c in selected],
        "total_megabytes": round(sum(c.megabytes or 0.0 for c in selected), 4),
        "footprint_ha_geodesic": [c.footprint_ha_geodesic for c in selected],
        "footprint_ha_equal_area": [c.footprint_ha_equal_area for c in selected],
        "pairwise_overlap": pairwise,
        "min_pairwise_overlap_percent_of_smaller_footprint": round(min_percent, 4),
        "common_intersection_ha_geodesic": common_ha,
        "common_intersection_ha_equal_area": _common_area_ha(selected)[1],
        "equal_area_cross_check_max_relative_difference": round(max_difference, 6),
        "equal_area_cross_check_passed": bool(max_difference <= 0.005),
        "unknown_fields_by_item": {c.item_id: list(c.unknown_fields) for c in selected},
        "considered_candidate_sets": len(feasible_sets),
        "note": "selection is a geometric and metadata compatibility result only; no cause is attributed to any difference and no likelihood is produced",
    }
    report["sanitization"] = {
        "policy": "credential-bearing or bearer-guarded assets are removed; public item links and every other public field are preserved",
        "findings": sanitization_findings,
    }
    report["selected"] = {
        "type": "FeatureCollection",
        "features": sanitized_items,
    }
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m catalog.select",
        description=(
            "Select 2 or 3 compatible scenes from a supplied public STAC FeatureCollection. "
            "Offline and credential-free: the catalog file is read from disk, no request is "
            "made and no product is downloaded."
        ),
    )
    parser.add_argument("catalog", help="local STAC FeatureCollection JSON file (public metadata)")
    parser.add_argument(
        "--count",
        type=int,
        required=True,
        help="number of scenes to select (2 or 3)",
    )
    parser.add_argument(
        "--max-scene-mb",
        type=float,
        required=True,
        help="client-side size cap per scene in megabytes",
    )
    parser.add_argument(
        "--min-gap-days",
        type=int,
        required=True,
        help="minimum separation between any two selected acquisitions, in UTC calendar days",
    )
    parser.add_argument(
        "--min-overlap",
        type=float,
        required=True,
        dest="min_overlap",
        help="minimum pairwise intersection, in percent of the smaller of the two footprints",
    )
    parser.add_argument(
        "--product-type",
        action="append",
        default=[],
        help="restrict to this product type; repeat for several (default: any)",
    )
    parser.add_argument("--out", required=True, help="path of the JSON selection report to write")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def _summarise(report: Dict[str, Any]) -> str:
    selection = report["selection"]
    lines = [
        f"selected {selection['count']} scenes from {report['input'].get('path')}",
        f"  cluster: {selection['cluster']}",
        f"  dates (UTC calendar days): {', '.join(selection['acquisition_dates'])}",
        f"  gaps (calendar days): {selection['gaps_calendar_days']}  elapsed seconds: {selection['gap_elapsed_seconds']}",
        f"  sizes (MB): {selection['scene_megabytes']}  total {selection['total_megabytes']:g}",
        f"  min pairwise overlap: {selection['min_pairwise_overlap_percent_of_smaller_footprint']:.4f}% of the smaller footprint",
        f"  common intersection: {selection['common_intersection_ha_geodesic']} ha",
    ]
    if report["rejected_items"]:
        lines.append(f"  rejected items: {len(report['rejected_items'])}")
    if report["sanitization"]["findings"]:
        lines.append(f"  sanitized asset/link/property entries: {len(report['sanitization']['findings'])}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    criteria = Criteria(
        count=args.count,
        max_scene_mb=args.max_scene_mb,
        min_gap_days=args.min_gap_days,
        min_overlap_percent=args.min_overlap,
        product_types=tuple(args.product_type),
    )
    try:
        criteria.validate()
    except SelectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        document, provenance = load_catalog(args.catalog)
        report = select_scenes(document, criteria, provenance)
    except SelectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=False)
        handle.write("\n")
    print(_summarise(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())