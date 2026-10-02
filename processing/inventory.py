"""Credential-free local reconnaissance of a RADARSAT-2 style dataset.

Walks a data directory, finds RADARSAT-2 product metadata (``product.xml``) and
raster products, and emits a structured JSON inventory describing what is
actually present on disk.

Design constraints honoured here:

* No credentials, no network access, no EODMS (or any other) authentication.
* Archives are inspected with :mod:`zipfile` and are never extracted; entries with
  absolute or traversing names are refused.
* Only files underneath the directory passed on the command line are inspected,
  and credential-like paths (``.env``, ``.aws``, ``.codex``, ``.eodms``, ...) are
  never opened.
* XML is parsed namespace-independently (local-name matching), so metadata is
  recognised regardless of namespace prefixes or the schema in use.
* ``rasterio`` is optional. When it is missing the tool still runs and reports
  that raster metadata could not be determined. A CRS is never invented.
* This is reconnaissance only: no radiometric calibration, no speckle
  filtering, no terrain correction and no change detection are performed.
  A product ``EPSG:4326`` footprint is a metadata footprint; it does not imply
  that the raster inside the product is georeferenced.

Usage::

    python -m processing.inventory data/raw --out data/reports/inventory.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "scan",
    "recommend_preprocessing",
    "main",
    "LIMITS",
    "SKIPPED_DIRECTORIES",
]

__version__ = "1.0.0"

try:  # optional dependency, installed by the project lead
    import rasterio  # type: ignore
except Exception:  # pragma: no cover - depends on environment
    rasterio = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Safety limits and path rules
# ---------------------------------------------------------------------------

#: Hard limits so a hostile or simply enormous dataset cannot exhaust resources.
LIMITS: Dict[str, int] = {
    "max_xml_bytes": 32 * 1024 * 1024,
    "max_zip_archive_bytes": 8 * 1024 * 1024 * 1024,
    "max_zip_members": 50_000,
}

#: Directories that are never walked, whatever the caller asks for.
SKIPPED_DIRECTORIES = frozenset(
    {
        "aws",
        "codex",
        "eodms",
        "ssh",
        "gnupg",
        "gpg",
        "docker",
        "credentials",
        "secrets",
    }
)

#: Filename fragments that mark a path as credential-like.
_CREDENTIAL_TOKENS = (
    "credential",
    "secret",
    "password",
    "passwd",
    "token",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    "apikey",
    "api_key",
    "access_key",
)

RASTER_SUFFIXES = (".tif", ".tiff")
ARCHIVE_SUFFIXES = (".zip",)

FOOTPRINT_CAVEAT = (
    "A footprint SRS (for example EPSG:4326) describes the product footprint in "
    "metadata only; it does not imply that the raster(s) inside the product are "
    "georeferenced, rectified or resampled."
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _localname(tag: Any) -> str:
    """Return the lowercase local-name of an ElementTree tag (namespace-free)."""
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        tag = tag.split("}", 1)[1]
    return tag.strip().lower()


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _classify_component(name: str) -> Optional[str]:
    """Return a skip reason for a directory/file name, or ``None`` to keep it."""
    lowered = name.lower()
    if lowered == ".env" or lowered.startswith(".env."):
        return "environment/credential file"
    if lowered in SKIPPED_DIRECTORIES:
        return "credential or tooling directory"
    if lowered.startswith("."):
        return "hidden directory or file"
    if any(token in lowered for token in _CREDENTIAL_TOKENS):
        return "credential-like name"
    return None


def is_credential_like(name: str) -> bool:
    """True for paths that must never be opened (secrets / local tooling dirs)."""
    return _classify_component(name) is not None


def _safe_zip_member(name: str) -> Tuple[bool, Optional[str]]:
    """Validate a zip member name. Returns ``(safe, reason_if_rejected)``.

    Rejects absolute paths, drive letters, ``..`` traversal (POSIX or Windows
    style), hidden components and credential-like names. Nothing is ever
    extracted, so this guards the metadata reader rather than the filesystem.
    """
    if not name or name.endswith("/"):
        return False, "directory entry"
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return False, "absolute path"
    if len(normalized) > 1 and normalized[1] == ":":
        return False, "drive-letter path"
    parts = [p for p in PurePosixPath(normalized).parts if p not in ("", ".")]
    if not parts:
        return False, "empty path"
    if any(part == ".." for part in parts):
        return False, "path traversal ('..')"
    for part in parts:
        reason = _classify_component(part)
        if reason is not None:
            return False, reason
    return True, None


def _normalise_timestamp(value: Optional[str]) -> Optional[str]:
    """Best-effort ISO-8601 normalisation. Returns ``None`` when unparseable."""
    text = _clean(value)
    if text is None:
        return None
    candidate = text
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    candidate = candidate.replace(" UTC", "+00:00").replace(" utc", "+00:00")
    candidate = candidate.replace("/", "-")
    for attempt in (candidate, candidate.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(attempt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def _duration_seconds(start: Optional[str], end: Optional[str]) -> Optional[float]:
    first, last = _normalise_timestamp(start), _normalise_timestamp(end)
    if first is None or last is None:
        return None
    try:
        a = datetime.fromisoformat(first.replace("Z", "+00:00"))
        b = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((b - a).total_seconds(), 6)


# ---------------------------------------------------------------------------
# Namespace-independent XML index
# ---------------------------------------------------------------------------


class _XmlIndex:
    """Local-name indexed view of an ElementTree, ignoring namespaces."""

    def __init__(self, root: ET.Element) -> None:
        self.namespaces: List[str] = []
        self.texts: Dict[str, List[str]] = {}
        self.paths: Dict[str, List[str]] = {}
        self.elements: Dict[str, List[ET.Element]] = {}
        seen_ns: set = set()
        self._walk(root, "", seen_ns)

    def _walk(self, element: ET.Element, prefix: str, seen_ns: set) -> None:
        for child in list(element):
            if not isinstance(child.tag, str):
                continue  # comments and processing instructions
            if child.tag.startswith("{"):
                namespace = child.tag[1:].partition("}")[0]
                if namespace not in seen_ns:
                    seen_ns.add(namespace)
                    self.namespaces.append(namespace)
            local = _localname(child.tag)
            path = f"{prefix}/{local}" if prefix else local
            self.paths.setdefault(local, []).append(path)
            self.elements.setdefault(local, []).append(child)
            text = _clean(child.text)
            if text is not None:
                self.texts.setdefault(local, []).append(text)
            self._walk(child, path, seen_ns)

    def first(self, *names: str) -> Optional[str]:
        for name in names:
            values = self.texts.get(name.lower())
            if values:
                return values[0]
        return None

    def all_text(self, *names: str) -> List[str]:
        out: List[str] = []
        for name in names:
            out.extend(self.texts.get(name.lower(), []))
        return out

    def child_text(self, parent_names: Sequence[str], child_names: Sequence[str]) -> Optional[str]:
        """Return the text of a direct ``<parent><child>..</child></parent>``."""
        wanted = {name.lower() for name in child_names}
        for parent_name in parent_names:
            for parent in self.elements.get(parent_name.lower(), []):
                for child in list(parent):
                    if isinstance(child.tag, str) and _localname(child.tag) in wanted:
                        text = _clean(child.text)
                        if text is not None:
                            return text
        return None

    def count(self, *names: str) -> int:
        return sum(len(self.elements.get(name.lower(), [])) for name in names)


# ---------------------------------------------------------------------------
# Field extraction from RADARSAT-2 style product metadata
# ---------------------------------------------------------------------------

_SCENE_ID_NAMES = (
    "sceneid",
    "sceneidentifier",
    "productidentifier",
    "productname",
    "product_name",
    "imagename",
    "datasetidentifier",
    "granulename",
    "name",
)
_ACQ_START_NAMES = (
    "productfirstlineutctime",
    "starttime",
    "acquisitiontime",
    "acquisitionstart",
    "acquisitionstarttime",
    "sensingstart",
    "imagetime",
    "firststarttime",
    "collectionstart",
    "productfirstlinetimeutc",
    "missionstarttime",
    "startdate",
)
_ACQ_END_NAMES = (
    "productlastlineutctime",
    "endtime",
    "acquisitionend",
    "acquisitionendtime",
    "sensingend",
    "laststarttime",
    "collectionend",
    "productlastlinetimeutc",
    "missionendtime",
    "enddate",
)
_PRODUCT_TYPE_NAMES = (
    "producttype",
    "producttypeid",
    "productcategory",
    "collectioncategory",
    "productformat",
)
_PROCESSING_LEVEL_NAMES = (
    "processinglevel",
    "processinglvl",
    "processinglevelid",
    "productlevel",
    "level",
)
_SAMPLE_TYPE_NAMES = (
    "sampletype",
    "samplemode",
    "samplemodeid",
    "quantizationtype",
    "imagetype",
)
_BITS_NAMES = ("bitspersample", "bitdepth", "datatype", "sampletypeid")
_POL_NAMES = (
    "polarization",
    "polarisation",
    "polarizations",
    "polarisations",
    "transmitterreceiverpolarisation",
    "transmitterreceiverpolarization",
    "pol",
    "rxpol",
    "txpol",
)
_BEAM_NAMES = ("beamname", "beam", "beammode", "beamtype", "beamsequence", "beamseq")
_MODE_NAMES = (
    "sensormode",
    "modename",
    "acquisitionmode",
    "mode",
    "imagingmode",
    "processingmode",
    "productmode",
    "swathmode",
)
_ORBIT_NUMBER_NAMES = (
    "absoluteorbitnumber",
    "orbitnumber",
    "orbit",
    "relorbitnumber",
    "osculatingorbitnumber",
)
_ORBIT_FIRST_NAMES = ("firstorbit", "firstorbitnumber", "orbitstart", "firstorbitcycle")
_ORBIT_LAST_NAMES = ("lastorbit", "lastorbitnumber", "orbitstop", "lastorbitcycle")
_ORBIT_TYPE_NAMES = ("orbittype", "orbitaccuracy", "orbitprecisetype", "precisetype")
_ORBIT_DIRECTION_NAMES = ("orbitdirection", "pass", "ascdescdirection", "ascdesc", "direction")

_TIEPOINT_NAMES = (
    "imagetiepoint",
    "tiepoint",
    "tiepoints",
    "imagetiepoints",
    "polynomialtiepoint",
)
_GCP_NAMES = ("gcp", "gcps", "gcplist", "groundcontrolpoint", "groundcontrolpoints")

_POL_SPLIT = ("+", "/", ",", ";", " ", "\t", "\n", "|")
_VALID_POL = {"HH", "HV", "VV", "VH", "HH+HV", "HH-HV", "VV+VH"}


def _extract_polarizations(index: _XmlIndex) -> List[str]:
    found: List[str] = []
    raw_values = index.all_text(*_POL_NAMES)
    for element in index.elements.get("pol", []) + index.elements.get("polarization", []):
        raw_values.append(_clean(element.text) or "")
        for attribute in ("pol", "polarization", "polarisation"):
            value = _clean(element.get(attribute))
            if value:
                raw_values.append(value)
    for raw in raw_values:
        if raw is None:
            continue
        for token in _replace_separators(raw):
            token = token.strip().upper()
            if token in _VALID_POL and token not in found:
                found.append(token)
    return sorted(found)


def _replace_separators(raw: str) -> List[str]:
    tokens = [raw]
    for separator in _POL_SPLIT:
        expanded: List[str] = []
        for token in tokens:
            expanded.extend(token.split(separator))
        tokens = expanded
    return [token for token in tokens if token.strip()]


def _as_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    text = value.strip()
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return None


def _collect_references(index: _XmlIndex, names: Sequence[str], tokens: Sequence[str]) -> List[str]:
    """Collect file-ish strings (href/URI/name attributes) related to *names*."""
    references: List[str] = []
    for name in names:
        for element in index.elements.get(name.lower(), []):
            for attribute in ("href", "uri", "url", "name", "fileName", "path"):
                value = _clean(element.get(attribute))
                if value and value not in references:
                    references.append(value)
    for element in index.elements.values():
        for element_item in element:
            for attribute in ("href", "uri", "url", "fileName", "path"):
                value = _clean(element_item.get(attribute))
                if value and any(token in value.lower() for token in tokens):
                    if value not in references:
                        references.append(value)
    return references


def _references_from_container(names: Iterable[str], tokens: Sequence[str]) -> List[str]:
    """Match sibling file names inside the same container against *tokens*."""
    hits: List[str] = []
    lowered = [name.lower() for name in names]
    for name in lowered:
        if any(token in name for token in tokens):
            hits.append(name)
    return sorted(set(hits))


def _footprint(index: _XmlIndex) -> Dict[str, Any]:
    srs_name = index.first("srsname", "srs", "footprintsrs", "coordinatesystem")
    if srs_name is None:
        for name in ("polygon", "envelope", "linearring", "point", "position", "coordinates"):
            for element in index.elements.get(name, []):
                for attribute, value in element.attrib.items():
                    if attribute.lower() in ("srsname", "srs", "crs", "epsg"):
                        srs_name = _clean(value)
                        break
                if srs_name:
                    break
            if srs_name:
                break
    footprint_elements = index.count("footprint", "gmlpolygon", "polygon", "envelope", "bbox")
    is_geographic = False
    if srs_name:
        upper = srs_name.upper().replace(" ", "")
        is_geographic = any(code in upper for code in ("4326", "CRS84", "WGS84", "4258"))
    return {
        "srs_name": srs_name,
        "element_count": footprint_elements,
        "present": bool(srs_name) or footprint_elements > 0,
        "is_geographic": is_geographic,
        "caveat": FOOTPRINT_CAVEAT,
    }


def _acquisition(index: _XmlIndex, warnings: List[str]) -> Dict[str, Any]:
    raw_start = index.first(*_ACQ_START_NAMES)
    raw_end = index.first(*_ACQ_END_NAMES)
    start = _normalise_timestamp(raw_start)
    end = _normalise_timestamp(raw_end)
    if raw_start and start is None:
        warnings.append(
            f"acquisition start time {raw_start!r} in product.xml could not be parsed as ISO-8601"
        )
    if raw_end and end is None:
        warnings.append(
            f"acquisition end time {raw_end!r} in product.xml could not be parsed as ISO-8601"
        )
    return {
        "start": start,
        "end": end,
        "duration_seconds": _duration_seconds(raw_start, raw_end),
        "pass_time_utc": index.first("ascendingpasstime", "passTimeUtc", "passesover"),
        "sensor": index.first("sensor", "sensorid", "sensoridentifier", "platform"),
    }


def _orbits(index: _XmlIndex) -> Dict[str, Any]:
    first = index.first(*_ORBIT_FIRST_NAMES)
    last = index.first(*_ORBIT_LAST_NAMES)
    return {
        "number": _as_int(index.first(*_ORBIT_NUMBER_NAMES)),
        "first": _as_int(first),
        "last": _as_int(last),
        "type": index.first(*_ORBIT_TYPE_NAMES),
        "direction": index.first(*_ORBIT_DIRECTION_NAMES),
        "reference_time": index.first("firststarttime", "orbitreferenceepoch", "epoch"),
    }


def _calibration(index: _XmlIndex, container_files: Sequence[str]) -> Dict[str, Any]:
    lut_elements = index.all_text("calibrationlookuptable", "calibrationlut", "betalut", "lutfile")
    noise_elements = index.all_text("noiselookuptable", "noiselut", "noiseimage", "noisefilename")
    orbit_elements = index.all_text("orbitfile", "orbitstatevector", "statevector", "orbitvec")

    lut_refs = _collect_references(
        index,
        ("calibrationlookuptable", "calibrationlut", "calibration", "lutfile"),
        ("cal", "lut", "beta"),
    )
    noise_refs = _collect_references(
        index, ("noise", "noiselookuptable", "noiselut", "noiseimage"), ("noise",)
    )
    orbit_refs = _collect_references(
        index, ("orbit", "orbitfile", "statevector"), ("orbit", ".epr", "statevector")
    )

    lut_files = _references_from_container(container_files, ("cal", "lut", "beta"))
    noise_files = _references_from_container(container_files, ("noise",))
    orbit_files = _references_from_container(container_files, ("orbit", "statevector"))

    return {
        "lut_present": bool(lut_elements or lut_refs or lut_files),
        "lut_references": sorted(set(lut_refs + lut_files)),
        "noise_present": bool(noise_elements or noise_refs or noise_files),
        "noise_references": sorted(set(noise_refs + noise_files)),
        "orbit_files_present": bool(orbit_elements or orbit_refs or orbit_files),
        "orbit_references": sorted(set(orbit_refs + orbit_files)),
    }


def _geolocation(index: _XmlIndex) -> Dict[str, Any]:
    tiepoint_count = index.count("imagetiepoint", "tiepoint", "polynomialtiepoint")
    gcp_count = index.count("gcp", "groundcontrolpoint")
    return {
        "tiepoint_count": tiepoint_count,
        "gcp_count": gcp_count,
        "has_tiepoints": tiepoint_count > 0,
        "has_gcps": gcp_count > 0,
    }


# ---------------------------------------------------------------------------
# Raster inspection (optional rasterio, never invents a CRS)
# ---------------------------------------------------------------------------


def _empty_raster(name: str, **extra: Any) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "name": name,
        "inside_archive": False,
        "archive": None,
        "driver": None,
        "crs": None,
        "epsg": None,
        "crs_units": None,
        "transform": None,
        "transform_order": "gdal (c, a, b, f, d, e)",
        "pixel_spacing": None,
        "shape": None,
        "count": None,
        "dtype": None,
        "dtypes": None,
        "nodata": None,
        "gcp_count": None,
        "gcps": None,
        "tiepoint_count": None,
        "tiepoint_source": None,
        "open_location": None,
        "file_size_bytes": None,
        "error": None,
    }
    record.update(extra)
    return record


def _inspect_raster(
    name: str,
    locations: Sequence[str] | str,
    *,
    archive: Optional[str],
    size_bytes: Optional[int],
) -> Dict[str, Any]:
    """Read header-only metadata for one raster. Never reads pixel values.

    ``locations`` is one open string, or several candidates (used for the GDAL
    virtual-filesystem syntaxes for archives).
    """
    candidates = [locations] if isinstance(locations, str) else list(locations)
    inside_archive = archive is not None
    record = _empty_raster(
        name,
        inside_archive=inside_archive,
        archive=archive,
        file_size_bytes=size_bytes,
        open_location=candidates[0] if candidates else None,
    )
    if rasterio is None:
        record["error"] = (
            "rasterio is not installed; raster CRS/transform/shape/dtype/count/nodata "
            "could not be determined"
        )
        return record
    dataset = None
    failures: List[str] = []
    for candidate in candidates:
        try:
            dataset = rasterio.open(candidate)
            break
        except Exception as exc:
            failures.append(f"{candidate}: {type(exc).__name__}: {exc}")
    if dataset is None:
        record["error"] = "could not open raster: " + " | ".join(failures)
        return record
    try:
        with dataset:
            crs = dataset.crs
            record["driver"] = dataset.driver
            record["crs"] = crs.to_string() if crs is not None else None
            record["epsg"] = crs.to_epsg() if crs is not None else None
            record["crs_units"] = _axis_units(crs)
            record["count"] = dataset.count
            record["shape"] = {"width": dataset.width, "height": dataset.height}
            record["dtypes"] = list(dataset.dtypes)
            record["dtype"] = dataset.dtypes[0] if dataset.dtypes else None
            record["nodata"] = dataset.nodata
            try:
                transform = dataset.transform
                # GDAL / GeoTIFF geotransform ordering: (c, a, b, f, d, e)
                record["transform"] = [
                    transform.c,
                    transform.a,
                    transform.b,
                    transform.f,
                    transform.d,
                    transform.e,
                ]
                record["transform_order"] = "gdal (c, a, b, f, d, e)"
                record["pixel_spacing"] = {
                    "x": abs(transform.a),
                    "y": abs(transform.e),
                }
            except Exception:  # pragma: no cover - transform is usually present
                record["transform"] = None
                record["transform_order"] = "gdal (c, a, b, f, d, e)"
                record["pixel_spacing"] = None
            # rasterio does not expose GDAL tie points; XML-level tiepoint counts
            # are reported per scene under scene["geolocation"]["tiepoint_count"].
            record["tiepoint_count"] = None
            record["tiepoint_source"] = (
                "not exposed by rasterio; see scene.geolocation.tiepoint_count for "
                "product.xml tiepoint metadata"
            )
            gcps = []
            for gcp in getattr(dataset, "gcps", [])[0]:
                gcps.append(
                    {
                        "row": gcp.row,
                        "col": gcp.col,
                        "x": gcp.x,
                        "y": gcp.y,
                        "z": gcp.z,
                    }
                )
            record["gcps"] = gcps
            record["gcp_count"] = len(gcps)
    except Exception as exc:  # unreadable/corrupt raster must not abort the scan
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            dataset.close()
        except Exception:  # pragma: no cover - already closed
            pass
    return record


def _axis_units(crs: Any) -> Optional[str]:
    if crs is None:
        return None
    try:
        crs_info = crs.to_dict()
    except Exception:
        return None
    crs_info = crs_info or {}
    crs_info = crs_info.get("init", crs_info)
    if not isinstance(crs_info, dict):
        return None
    units = crs_info.get("units")
    if isinstance(units, (list, tuple)):
        units = ",".join(str(unit) for unit in units)
    if not units:
        return None
    return {"m": "metre", "metre": "metre", "meter": "metre"}.get(str(units).lower(), str(units))


# ---------------------------------------------------------------------------
# Preprocessing guidance (minimum decision, unknowns always explicit)
# ---------------------------------------------------------------------------

_NOT_PERFORMED = (
    "no radiometric calibration performed in this step",
    "no speckle filtering performed in this step",
    "no terrain/geometric correction performed in this step",
    "no change detection performed in this step",
    "no pixels were read for analysis (header metadata only)",
)


def recommend_preprocessing(scene: Dict[str, Any]) -> Dict[str, Any]:
    """Minimum preprocessing decision for an observed product.

    Returns a *recommendation with explicit unknowns*, never a guarantee. The
    wording is deliberately conditional: nothing here has been validated against
    the actual data, and no calibration or change detection has been performed.
    """
    product_type = (scene.get("product_type") or "").upper()
    processing_level = (scene.get("processing_level") or "").upper()
    sample_type = (scene.get("sample_type") or "").upper()
    polarizations = scene.get("polarizations") or []
    has_rasters = bool(scene.get("rasters"))
    raster_crs = next(
        (raster.get("crs") for raster in scene.get("rasters", []) if raster.get("crs")),
        None,
    )

    unknowns: List[str] = ["DEM source not decided for this project"]
    if scene.get("product_type") is None:
        unknowns.append("product type absent from metadata")
    if scene.get("processing_level") is None:
        unknowns.append("processing level absent from metadata")
    if not polarizations:
        unknowns.append("polarization set not recorded in metadata")
    if not scene.get("orbits", {}).get("type"):
        unknowns.append("orbit accuracy class (POE/ODE/ODP) unknown")
    if not has_rasters:
        unknowns.append("no raster found in this product; raster georeferencing unknown")
    elif raster_crs is None:
        unknowns.append("raster CRS unknown (rasterio reported no CRS); do not assume EPSG:4326")
    if not scene.get("geolocation", {}).get("has_tiepoints") and not scene.get(
        "geolocation", {}
    ).get("has_gcps"):
        unknowns.append("no tiepoint/GCP metadata found; geolocation quality unknown")
    if "COMPLEX" in sample_type and "SLC" not in product_type:
        unknowns.append(
            "complex (phase-preserving) samples present but product type is not SLC; "
            "coregistration/inSAR feasibility unverified"
        )

    if not product_type:
        recommended = (
            "No decision: product type is absent from product.xml. Collect product "
            "metadata first; preprocessing cannot be chosen from the current evidence."
        )
        rationale = ["product_type missing", "avoid assuming GRD or SLC"]
    elif any(token in product_type for token in ("SLC", "SCN", "SCC", "COMPLEX", "RAW")):
        recommended = (
            "SLC-grade product: verify thermal-noise removal and calibration lookup "
            "tables are present before radiometric calibration; interpolate precise "
            "orbits (POE vs ODE not verified). Multilooking/speckle filtering is a "
            "later, separate decision."
        )
        rationale = [
            f"product_type={product_type}",
            "phase is preserved, so speckle filtering and simple GRD assumptions "
            "would discard information",
            f"polarizations={polarizations or 'unknown'}",
        ]
    elif any(token in product_type for token in ("GRD", "SGD", "GROUND", "GEO", "IMAGE")):
        recommended = (
            "Ground-range detected product: minimum chain is speckle filtering for "
            "radiometric texture, then terrain correction against a DEM once the DEM "
            "is chosen. Both steps still need validation on a real sample."
        )
        rationale = [
            f"product_type={product_type}",
            "amplitude/detected product, so speckle statistics matter before differencing",
        ]
    else:
        recommended = (
            f"No decision: unrecognised product_type={product_type!r}. Confirm the "
            "product specification with the provider metadata before choosing a chain."
        )
        rationale = [f"product_type={product_type}"]

    if len(polarizations) >= 2:
        rationale.append(
            f"multi-polarisation acquisition ({', '.join(polarizations)}) observed; "
            "polarimetric analysis is possible in principle but not performed here"
        )
    elif len(polarizations) == 1:
        rationale.append(
            "single-polarisation acquisition observed; polarimetric decomposition is "
            "not applicable"
        )
    if processing_level:
        rationale.append(f"processing_level={processing_level}")

    return {
        "recommended": recommended,
        "rationale": rationale,
        "unknowns": unknowns,
        "not_performed": list(_NOT_PERFORMED),
        "confidence": "conditional on the metadata observed; not validated against pixels",
        "footprint_caveat": FOOTPRINT_CAVEAT,
    }


# ---------------------------------------------------------------------------
# Scene assembly
# ---------------------------------------------------------------------------


def _parse_product_xml(
    xml_bytes: bytes,
    source: Dict[str, Any],
    container_files: Sequence[str],
) -> Tuple[Optional[Dict[str, Any]], List[str], List[Dict[str, str]]]:
    """Build a scene record.

    Returns ``(scene, warnings, errors)``. ``scene`` is ``None`` when the XML
    cannot be parsed at all; the parse failure is then reported in ``errors``.
    """
    warnings: List[str] = []
    errors: List[Dict[str, str]] = []
    scene: Dict[str, Any] = {
        "scene_id": None,
        "source": source,
        "processing_level": None,
        "product_type": None,
        "sample_type": None,
        "bits_per_sample": None,
        "polarizations": [],
        "beam_mode": None,
        "mode": None,
        "acquisition": {},
        "orbits": {},
        "calibration": {},
        "geolocation": {},
        "footprint": {},
        "xml": {},
        "rasters": [],
        "missing": [],
        "warnings": warnings,
        "errors": [],
    }
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        errors.append(
            {
                "stage": "xml",
                "path": source["product_xml"],
                "message": f"XML parse failed: {exc}",
            }
        )
        return None, warnings, errors

    index = _XmlIndex(root)
    scene["xml"] = {
        "status": "parsed",
        "namespaces": index.namespaces,
        "element_count": sum(len(v) for v in index.elements.values()),
        "root": _localname(root.tag),
    }

    scene_id = index.child_text(("productidentifier",), ("name",)) or index.first(*_SCENE_ID_NAMES)
    scene["scene_id"] = _clean(scene_id)
    scene["processing_level"] = _clean(index.first(*_PROCESSING_LEVEL_NAMES))
    scene["product_type"] = _clean(index.first(*_PRODUCT_TYPE_NAMES))
    scene["sample_type"] = _clean(index.first(*_SAMPLE_TYPE_NAMES))
    scene["bits_per_sample"] = _as_int(index.first(*_BITS_NAMES))
    scene["polarizations"] = _extract_polarizations(index)
    scene["beam_mode"] = _clean(index.first(*_BEAM_NAMES))
    scene["mode"] = _clean(index.first(*_MODE_NAMES))
    scene["acquisition"] = _acquisition(index, warnings)
    scene["orbits"] = _orbits(index)
    scene["calibration"] = _calibration(index, container_files)
    scene["geolocation"] = _geolocation(index)
    scene["footprint"] = _footprint(index)

    scene["missing"] = _missing_fields(scene)
    scene["preprocessing"] = recommend_preprocessing(scene)
    return scene, warnings, errors


def _missing_fields(scene: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    if not scene.get("scene_id"):
        missing.append("scene_id")
    acquisition = scene.get("acquisition") or {}
    if not acquisition.get("start"):
        missing.append("acquisition.start")
    if not acquisition.get("end"):
        missing.append("acquisition.end")
    if not scene.get("polarizations"):
        missing.append("polarizations")
    if not scene.get("product_type"):
        missing.append("product_type")
    if not scene.get("processing_level"):
        missing.append("processing_level")
    if not scene.get("sample_type"):
        missing.append("sample_type")
    if not scene.get("beam_mode"):
        missing.append("beam_mode")
    if not scene.get("mode"):
        missing.append("mode")
    orbits = scene.get("orbits") or {}
    if not orbits.get("number"):
        missing.append("orbits.number")
    calibration = scene.get("calibration") or {}
    if not calibration.get("lut_present"):
        missing.append("calibration.lut")
    if not calibration.get("noise_present"):
        missing.append("calibration.noise")
    if not calibration.get("orbit_files_present"):
        missing.append("calibration.orbit_files")
    geolocation = scene.get("geolocation") or {}
    if not geolocation.get("has_tiepoints"):
        missing.append("geolocation.tiepoints")
    if not geolocation.get("has_gcps"):
        missing.append("geolocation.gcps")
    rasters = scene.get("rasters") or []
    if not rasters:
        missing.append("raster")
    for raster in rasters:
        prefix = f"raster[{raster['name']}]"
        if not raster.get("crs"):
            missing.append(f"{prefix}.crs")
        if not raster.get("transform"):
            missing.append(f"{prefix}.transform")
        if raster.get("error"):
            missing.append(f"{prefix}.readable")
    return missing


# ---------------------------------------------------------------------------
# Container walking
# ---------------------------------------------------------------------------


class _Collector:
    def __init__(self) -> None:
        self.scenes: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, str]] = []
        self.warnings: List[str] = []
        self.orphan_rasters: List[Dict[str, Any]] = []

    def error(self, stage: str, path: str, message: str) -> None:
        self.errors.append({"stage": stage, "path": path, "message": message})

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


def _walk_directory(root: Path, collector: _Collector) -> Tuple[List[Path], List[Path]]:
    """Collect candidate archives/product files, pruning unsafe paths."""
    zips: List[Path] = []
    product_xmls: List[Path] = []
    rasters: List[Path] = []
    root_real = os.path.realpath(root)

    def on_error(exc: OSError) -> None:
        collector.error("walk", str(getattr(exc, "filename", root)), f"{type(exc).__name__}: {exc}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error, followlinks=False):
        current = Path(dirpath)
        kept: List[str] = []
        for name in sorted(dirnames):
            child = current / name
            if os.path.islink(child):
                collector.warn(f"skipped symlinked directory {child} (not followed)")
                continue
            reason = _classify_component(name)
            if reason is not None:
                collector.warn(f"skipped directory {child}: {reason}")
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in sorted(filenames):
            path = current / name
            reason = _classify_component(name)
            if reason is not None:
                collector.warn(f"skipped file {path}: {reason}")
                continue
            if os.path.islink(path):
                real = os.path.realpath(path)
                if not _within(real, root_real):
                    collector.warn(f"skipped symlink {path} escaping the data directory")
                    continue
            if name.lower().endswith(ARCHIVE_SUFFIXES):
                zips.append(path)
            elif name.lower() == "product.xml":
                product_xmls.append(path)
            elif name.lower().endswith(RASTER_SUFFIXES):
                rasters.append(path)
    return zips, product_xmls + rasters


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), root]) == root
    except ValueError:
        return False


def _read_xml_file(path: Path, collector: _Collector) -> Optional[bytes]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        collector.error("stat", str(path), f"{type(exc).__name__}: {exc}")
        return None
    if size > LIMITS["max_xml_bytes"]:
        collector.warn(
            f"skipped {path}: product.xml is {size} bytes, above the "
            f"{LIMITS['max_xml_bytes']} byte limit"
        )
        return None
    try:
        with path.open("rb") as handle:
            return handle.read(LIMITS["max_xml_bytes"] + 1)
    except OSError as exc:
        collector.error("read", str(path), f"{type(exc).__name__}: {exc}")
        return None


def _inspect_zip_archive(
    zip_path: Path,
    collector: _Collector,
    *,
    data_root: Path,
    relative: str,
) -> None:
    try:
        size = zip_path.stat().st_size
    except OSError as exc:
        collector.error("stat", str(zip_path), f"{type(exc).__name__}: {exc}")
        return
    if size > LIMITS["max_zip_archive_bytes"]:
        collector.warn(
            f"skipped archive {zip_path}: {size} bytes exceeds the "
            f"{LIMITS['max_zip_archive_bytes']} byte limit"
        )
        return

    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            if len(infos) > LIMITS["max_zip_members"]:
                collector.warn(
                    f"truncated inspection of {zip_path}: more than "
                    f"{LIMITS['max_zip_members']} members"
                )
                infos = infos[: LIMITS["max_zip_members"]]

            safe_members: List[zipfile.ZipInfo] = []
            product_infos: List[zipfile.ZipInfo] = []
            raster_infos: List[zipfile.ZipInfo] = []
            for info in infos:
                safe, reason = _safe_zip_member(info.filename)
                if not safe:
                    collector.warn(
                        f"skipped unsafe zip member {info.filename!r} in {zip_path}: {reason}"
                    )
                    continue
                safe_members.append(info)
                lowered = info.filename.lower()
                if lowered.endswith("product.xml"):
                    product_infos.append(info)
                elif lowered.endswith(RASTER_SUFFIXES):
                    raster_infos.append(info)

            container_files = [info.filename for info in safe_members]
            archive_abs = os.path.realpath(zip_path)

            if not product_infos:
                collector.warn(
                    f"archive {zip_path} contains no product.xml; "
                    "not treated as a RADARSAT-2 product"
                )

            for info in product_infos:
                if info.file_size > LIMITS["max_xml_bytes"]:
                    collector.warn(
                        f"skipped member {info.filename!r} in {zip_path}: XML larger than "
                        f"{LIMITS['max_xml_bytes']} bytes"
                    )
                    continue
                try:
                    with archive.open(info) as handle:
                        payload = handle.read(LIMITS["max_xml_bytes"] + 1)
                except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                    collector.error(
                        "zip", f"{zip_path}!{info.filename}", f"{type(exc).__name__}: {exc}"
                    )
                    continue

                prefix = _container_prefix(info.filename, product_infos)
                member_rasters = [
                    raster for raster in raster_infos if _same_container(prefix, raster.filename)
                ]
                source = {
                    "container": "zip",
                    "path": str(zip_path),
                    "archive": zip_path.name,
                    "product_xml": f"{zip_path}!{info.filename}",
                    "container_path": prefix or None,
                    "member_count": len(safe_members),
                    "relative_to_data_dir": os.path.relpath(zip_path, data_root),
                }
                scene, _, parse_errors = _parse_product_xml(payload, source, container_files)
                if scene is None:
                    collector.errors.extend(parse_errors)
                    continue
                for raster_info in member_rasters:
                    scene["rasters"].append(
                        _inspect_raster(
                            raster_info.filename,
                            _archive_locations(archive_abs, raster_info.filename),
                            archive=zip_path.name,
                            size_bytes=raster_info.file_size,
                        )
                    )
                scene["missing"] = _missing_fields(scene)
                scene["preprocessing"] = recommend_preprocessing(scene)
                collector.scenes.append(scene)
    except (zipfile.BadZipFile, OSError) as exc:
        collector.error(
            "zip", str(zip_path), f"could not read archive: {type(exc).__name__}: {exc}"
        )


def _archive_locations(archive_path: str, member: str) -> List[str]:
    """GDAL open candidates for a member inside a zip, none of which extract it."""
    return [
        f"zip://{archive_path}!/{member}",
        f"/vsizip/{archive_path}!{member}",
    ]


def _container_prefix(product_xml_name: str, product_infos: Sequence[zipfile.ZipInfo]) -> str:
    """Directory prefix of the folder containing product.xml (e.g. ``IMAGEDATA/``)."""
    own = PurePosixPath(product_xml_name.replace("\\", "/")).parent
    own_prefix = "" if str(own) in (".", "") else f"{own}/"
    for info in product_infos:
        if info.filename == product_xml_name:
            continue
        candidate = str(PurePosixPath(info.filename.replace("\\", "/")).parent)
        candidate_prefix = "" if candidate in (".", "") else f"{candidate}/"
        if candidate_prefix and (
            candidate_prefix.startswith(own_prefix) or own_prefix.startswith(candidate_prefix)
        ):
            if len(candidate_prefix) > len(own_prefix):
                own_prefix = candidate_prefix
    return own_prefix


def _same_container(prefix: str, member_name: str) -> bool:
    if not prefix:
        return "/" not in member_name.replace("\\", "/")
    return member_name.replace("\\", "/").startswith(prefix)


def _inspect_loose_directory(
    product_xml: Path,
    container_files: Sequence[Path],
    collector: _Collector,
    *,
    data_root: Path,
) -> None:
    container_dir = product_xml.parent
    siblings = [path.name for path in container_dir.rglob("*") if path.is_file()]
    source = {
        "container": "directory",
        "path": str(container_dir),
        "archive": None,
        "product_xml": str(product_xml),
        "container_path": None,
        "member_count": len(siblings),
        "relative_to_data_dir": os.path.relpath(container_dir, data_root),
    }
    payload = _read_xml_file(product_xml, collector)
    if payload is None:
        return
    scene, _, parse_errors = _parse_product_xml(payload, source, siblings)
    if scene is None:
        collector.errors.extend(parse_errors)
        return
    for raster_path in container_files:
        try:
            size = raster_path.stat().st_size
        except OSError:
            size = None
        scene["rasters"].append(
            _inspect_raster(
                raster_path.name,
                str(raster_path),
                archive=None,
                size_bytes=size,
            )
        )
    scene["missing"] = _missing_fields(scene)
    scene["preprocessing"] = recommend_preprocessing(scene)
    collector.scenes.append(scene)


def _attach_orphan_rasters(rasters: Sequence[Path], collector: _Collector) -> None:
    """Rasters not inside any product folder are reported, not silently dropped."""
    scene_dirs = [Path(scene["source"]["path"]) for scene in collector.scenes]
    for raster_path in rasters:
        owned = any(_is_within(raster_path, directory) for directory in scene_dirs)
        if owned:
            continue
        record = _inspect_raster(
            raster_path.name, str(raster_path), archive=None, size_bytes=_safe_size(raster_path)
        )
        collector.orphan_rasters.append(record)
        if record["error"] is None:
            collector.warn(
                f"raster {raster_path} is not inside a folder containing product.xml; "
                "reported as an orphan raster"
            )


def _safe_size(path: Path) -> Optional[int]:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _is_within(path: Path, directory: Path) -> bool:
    try:
        return os.path.commonpath([str(path.resolve()), str(directory.resolve())]) == str(
            directory.resolve()
        )
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan(
    data_dir: os.PathLike | str, out_path: Optional[os.PathLike | str] = None
) -> Dict[str, Any]:
    """Inspect *data_dir* and return (optionally write) an inventory report.

    Read-only with respect to *data_dir*: nothing inside it is created, modified
    or extracted. The only file written is *out_path* (written atomically), and
    only after the walk has finished.
    """
    root = Path(data_dir)
    if not root.exists():
        raise FileNotFoundError(f"data directory not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    collector = _Collector()
    zips, candidates = _walk_directory(root, collector)
    product_xmls = [path for path in candidates if path.name.lower() == "product.xml"]
    loose_rasters = [path for path in candidates if path.name.lower().endswith(RASTER_SUFFIXES)]

    if not zips and not product_xmls:
        collector.warn(
            "no product.xml found under the data directory (looked for product.xml and "
            "zip archives containing one)"
        )

    for zip_path in zips:
        _inspect_zip_archive(
            zip_path, collector, data_root=root, relative=os.path.relpath(zip_path, root)
        )

    for product_xml in product_xmls:
        container_rasters = [path for path in loose_rasters if _is_within(path, product_xml.parent)]
        _inspect_loose_directory(product_xml, container_rasters, collector, data_root=root)
        # Consume the container's rasters only when product.xml yielded a scene;
        # otherwise they are reported as orphans rather than silently dropped.
        produced_scene = any(
            scene["source"]["product_xml"] == str(product_xml) for scene in collector.scenes
        )
        if produced_scene and container_rasters:
            owned = set(container_rasters)
            loose_rasters = [path for path in loose_rasters if path not in owned]

    _attach_orphan_rasters(loose_rasters, collector)

    scenes = sorted(
        collector.scenes, key=lambda scene: (scene["scene_id"] or "", scene["source"]["path"])
    )
    missing_data = [
        {"scene_id": scene["scene_id"], "path": scene["source"]["product_xml"], "field": field}
        for scene in scenes
        for field in scene["missing"]
    ]
    raster_error_count = sum(
        1 for scene in scenes for raster in scene["rasters"] if raster["error"]
    )
    if rasterio is None:
        collector.warn(
            "rasterio is not installed: raster CRS/transform/shape/dtype/count/nodata and "
            "tiepoint/GCP metadata could not be determined for any raster"
        )
    for scene in scenes:
        if scene["footprint"].get("is_geographic"):
            collector.warn(
                f"scene {scene['scene_id']}: product footprint declares "
                f"{scene['footprint']['srs_name']}; {FOOTPRINT_CAVEAT}"
            )

    report: Dict[str, Any] = {
        "schema_version": "1.0",
        "tool": "processing.inventory",
        "tool_version": __version__,
        "generated_utc": _now(),
        "data_dir": str(root),
        "data_dir_resolved": os.path.realpath(root),
        "output_path": str(out_path) if out_path is not None else None,
        "credentials_used": False,
        "network_used": False,
        "archives_extracted": False,
        "rasterio": {
            "available": rasterio is not None,
            "version": getattr(rasterio, "__version__", None),
        },
        "limits": dict(LIMITS),
        "scene_count": len(scenes),
        "zero_scenes": not scenes,
        "scenes": scenes,
        "orphan_rasters": collector.orphan_rasters,
        "missing_data": missing_data,
        "missing_data_count": len(missing_data),
        "error_count": len(collector.errors),
        "raster_error_count": raster_error_count,
        "errors": collector.errors,
        "warnings": collector.warnings,
        "summary": _summary(scenes, collector),
    }

    if out_path is not None:
        _write_report(report, Path(out_path))
    return report


def _summary(scenes: Sequence[Dict[str, Any]], collector: _Collector) -> str:
    if not scenes:
        detail = ""
        if collector.errors:
            detail = f" {len(collector.errors)} error(s) were recorded; see 'errors'."
        return (
            f"no RADARSAT-2 scenes found: no usable product.xml was discovered under this "
            f"directory.{detail}"
        )
    pols: Dict[str, int] = {}
    types: Dict[str, int] = {}
    for scene in scenes:
        for pol in scene["polarizations"]:
            pols[pol] = pols.get(pol, 0) + 1
        key = scene["product_type"] or "unknown"
        types[key] = types.get(key, 0) + 1
    pol_text = ", ".join(f"{key}x{value}" for key, value in sorted(pols.items())) or "none recorded"
    type_text = ", ".join(f"{key}x{value}" for key, value in sorted(types.items()))
    all_rasters = [raster for scene in scenes for raster in scene["rasters"]]
    readable = [raster for raster in all_rasters if raster["error"] is None]
    return (
        f"{len(scenes)} RADARSAT-2 scene(s); {len(all_rasters)} raster(s) found, "
        f"{len(readable)} readable via rasterio (header metadata only, no pixels read); "
        f"product types: {type_text}; polarizations: {pol_text}; "
        f"{len(collector.errors)} error(s), {len(collector.warnings)} warning(s)."
    )


def _write_report(report: Dict[str, Any], out_path: Path) -> None:
    """Write JSON atomically (temp file + ``os.replace``)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(f".{out_path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=False, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, out_path)
    finally:
        if temporary.exists():  # pragma: no cover - only on write failure
            temporary.unlink()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m processing.inventory",
        description=(
            "Local, credential-free reconnaissance of a RADARSAT-2 dataset: reads "
            "product.xml metadata and raster headers without extracting archives, "
            "authenticating anywhere or inventing metadata."
        ),
    )
    parser.add_argument("data_dir", help="directory to inspect (read-only)")
    parser.add_argument(
        "--out",
        default=None,
        help="write the JSON inventory to this path (atomic write, created if needed)",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation used for stdout and --out output (default: 2)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not echo the report to stdout when --out is used",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = scan(args.data_dir, args.out)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not (args.out and args.quiet):
        json.dump(report, sys.stdout, indent=args.indent, default=str)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
