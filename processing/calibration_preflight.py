"""Read-only calibration preflight for a delivered RADARSAT-2 product directory.

This module answers one narrow question about a product directory that already
exists on disk:

    *Is this directory internally complete and self-consistent enough for a
     radiometric calibration step to be attempted?*

It is a **gate**, not a processing step. It never calibrates, never
geocodes, never reprojects, never reads a single pixel, and never reports that
a product is "verified", "processed" or "calibrated". The strongest status it
can return is ``ready_for_calibration``; every other outcome is an explicit
list of what is missing or unsupported.

What is checked
---------------

* ``product.xml`` presence and parseability.
* Sample representation and bit depth (``sampleType`` / ``bitsPerSample``).
* Image dimensions (``numberOfLines`` / ``numberOfSamplesPerLine``).
* Declared polarizations.
* The sigma0 lookup-table **reference** taken from ``product.xml``, whether
  that reference stays inside the product directory, and whether the file
  exists and parses.
* Lookup-table numerics: gains finite and strictly positive, offsets finite,
  and per-polarization incidence-angle **width coverage** of the span the
  product declares.
* Existence of the imagery files for every declared polarization.

Detected versus complex power semantics
---------------------------------------

The two supported sample representations are *not* interchangeable, and the
preflight refuses to blur them:

* A **detected magnitude** (``sampleType`` = ``MAG``/``MAGNITUDE``) product, once
  a detector driver has applied the lookup table, yields a band that is
  already **linear sigma0 power**. Downstream code must **not** square it.
* A **complex I/Q** (``sampleType`` = ``COMPLEX_IQ``) product yields in-phase
  and quadrature components. Calibrated backscatter is
  ``|I + jQ|**2 = I**2 + Q**2``: a **magnitude-squared** step is required.

Which rule applies is derived from the declared ``sampleType`` in the delivered
``product.xml``; it is never assumed from the product name.

Safety
------

* Read-only. Nothing inside the product directory is created, modified or
  removed; the only optional write is the report path given to the CLI.
* Every path reached from ``product.xml`` (lookup tables, and any file
  discovery) must resolve to a regular file *inside* the product directory.
  ``..`` segments, absolute paths, drive letters, and symlinks that resolve
  outside the product are refused **before** any read is attempted.
* XML and lookup-table reads are bounded by a byte limit and an entry limit.
* No credentials, no network, no EODMS (or any other) authentication.

Honest limitations
------------------

* ``width`` in the CSA lookup table is interpreted as the full width of a bin
  centred on that entry's incidence angle (``width_semantics="bin-width"``).
  That convention is **not verified against a real delivered lookup table**
  here; the report records it as ``convention_verified_against_real_product:
  false``. Use ``width_semantics="half-width"`` if the delivered product turns
  out to define ``width`` as a half-width.
* Driver availability is **measured** from the installed GDAL/rasterio driver
  registry, never assumed. This preflight does not assert that any driver is
  ScanSAR, ground-range-detected or geocoded; those are product-format claims
  that require a real delivered product to establish. An absent driver is
  reported as absent and no substitute route is forced.
* Nothing here has been run against a real RADARSAT-2 product, because no real
  product is present in the development environment.

Usage::

    python -m processing.calibration_preflight /path/to/RS2_PRODUCT_DIR
    python -m processing.calibration_preflight /path/to/RS2_PRODUCT_DIR --out preflight.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "preflight",
    "gdal_driver_capabilities",
    "SEMANTICS",
    "LIMITS",
    "SUPPORTED_BIT_DEPTHS",
    "main",
]

__version__ = "1.0.0"

#: The strongest statement this tool is allowed to make about a product.
SEMANTICS = (
    "The product directory was inspected read-only. A 'ready_for_calibration' "
    "status means the calibration *inputs* (metadata, lookup table and imagery "
    "references) were found to be present, contained and numerically sane. It is "
    "not a statement that the product has been calibrated, verified, processed, "
    "geocoded or quality-checked. No pixel was read and no calibration was "
    "performed."
)

LIMITS: Dict[str, int] = {
    "max_xml_bytes": 32 * 1024 * 1024,
    "max_lut_bytes": 32 * 1024 * 1024,
    "max_lut_entries": 200_000,
    "max_imagery_files": 20_000,
}

RASTER_SUFFIXES = (".tif", ".tiff", ".tiff.gz")

#: Bit depths accepted per sample representation. Anything else is reported as
#: unsupported rather than rounded or coerced.
SUPPORTED_BIT_DEPTHS: Dict[str, Tuple[int, ...]] = {
    "complex_iq": (16, 32),
    "magnitude": (8, 16),
}

COMPLEX_SAMPLE_TYPES = {"COMPLEX_IQ", "COMPLEX", "I_Q", "IQ", "SLC_COMPLEX"}
MAGNITUDE_SAMPLE_TYPES = {"MAG", "MAGNITUDE", "DETECTED", "AMPLITUDE"}

#: Drivers probed in the installed GDAL/rasterio registry. Presence is measured,
#: not assumed; see :func:`gdal_driver_capabilities`.
PROBED_DRIVERS: Tuple[str, ...] = ("SGF", "CGX", "RS2", "ISCE", "ENVI", "GTiff")

#: Product-type tokens that a dedicated GDAL driver would be used to open. SLC /
#: complex products are plain GeoTIFF I/Q pairs and need no special driver, so
#: they are deliberately absent here.
DRIVER_ROUTE_BY_PRODUCT_TYPE: Dict[str, str] = {
    "SGF": "SGF",
    "SGX": "SGF",
    "SCN": "SGF",
    "SCC": "SGF",
    "SCX": "SGF",
    "SPG": "SGF",
    "SPX": "SGF",
    "SGC": "SGF",
}

#: Imagery component tokens for complex (I/Q) products.
COMPLEX_COMPONENTS = ("I", "Q")

# Element local names searched in ``product.xml`` (namespace independent).
_PRODUCT_TYPE_NAMES = ("producttype",)
_SAMPLE_TYPE_NAMES = ("sampletype", "samplemode", "samplemodeid", "imagetype")
_BITS_NAMES = ("bitspersample", "bitdepth", "datatype")
_LINES_NAMES = ("numberoflines", "imagelines")
_SAMPLES_NAMES = ("numberofsamplesperline", "linepixels", "imagesamples")
_POL_NAMES = (
    "transmitterreceiverpolarisation",
    "transmitterreceiverpolarization",
    "polarisation",
    "polarization",
    "pols",
    "pol",
)
_NEAR_ANGLE_NAMES = ("nearerangeincidenceangle", "nearrangeincidenceangle")
_FAR_ANGLE_NAMES = ("farrangeincidenceangle", "farincidenceangle")
_LUT_REF_NAMES = ("calibrationlookuptable", "sigma0lookuptable", "sigma0lut", "calibrationlut")

_POL_SPLIT = ("+", "/", ",", ";", " ", "\t", "\n", "|", "-")
_VALID_POL = {"HH", "HV", "VV", "VH"}


# ---------------------------------------------------------------------------
# XML helpers (namespace independent)
# ---------------------------------------------------------------------------


def _localname(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        tag = tag.split("}", 1)[1]
    return tag.strip().lower()


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


class _Index:
    """Local-name indexed, namespace-independent view of an ElementTree."""

    def __init__(self, root: ET.Element) -> None:
        self.texts: Dict[str, List[str]] = {}
        self.elements: Dict[str, List[ET.Element]] = {}
        self.paths: Dict[str, List[str]] = {}
        self.namespaces: List[str] = []
        self._root = root
        self._walk(root, "")

    def _walk(self, element: ET.Element, prefix: str) -> None:
        for child in list(element):
            if not isinstance(child.tag, str):
                continue
            if child.tag.startswith("{"):
                namespace = child.tag[1:].partition("}")[0]
                if namespace not in self.namespaces:
                    self.namespaces.append(namespace)
            local = _localname(child.tag)
            path = f"{prefix}/{local}" if prefix else local
            self.paths.setdefault(local, []).append(path)
            self.elements.setdefault(local, []).append(child)
            text = _clean(child.text)
            if text is not None:
                self.texts.setdefault(local, []).append(text)
            self._walk(child, path)

    def first(self, *names: str) -> Optional[str]:
        for name in names:
            values = self.texts.get(name.lower())
            if values:
                return values[0]
        return None

    def find_element(self, *names: str) -> Optional[ET.Element]:
        for name in names:
            found = self.elements.get(name.lower())
            if found:
                return found[0]
        return None

    def path_of(self, element: ET.Element) -> Optional[str]:
        for local, group in self.elements.items():
            for candidate in group:
                if candidate is element:
                    paths = self.paths.get(local) or []
                    try:
                        return paths[group.index(candidate)]
                    except (ValueError, IndexError):  # pragma: no cover
                        return local
        return None


def _child_text(element: ET.Element, *names: str) -> Optional[str]:
    wanted = {name.lower() for name in names}
    for child in list(element):
        if _localname(child.tag) in wanted:
            text = _clean(child.text)
            if text is not None:
                return text
    return None


def _as_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(str(value).strip().rstrip("\x00").strip())
    except (TypeError, ValueError):
        return None
    return parsed


def _as_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        parsed = _as_float(text)
        if parsed is None or not float(parsed).is_integer():
            return None
        return int(parsed)


# ---------------------------------------------------------------------------
# Path containment
# ---------------------------------------------------------------------------


class _PathRefusal(Exception):
    """Raised when a referenced path is not a safe in-product regular file."""


class _ReferenceAbsent(Exception):
    """Raised when a safe in-product reference points at a non-existent file."""


def _product_root(product_dir: Path) -> str:
    return os.path.realpath(str(product_dir))


def _refuse(reason: str) -> None:
    raise _PathRefusal(reason)


def _check_reference_shape(href: str) -> PurePosixPath:
    """Reject traversal/absolute/drive-letter references before touching disk."""
    if not href or not href.strip():
        _refuse("empty reference")
    normalized = href.replace("\\", "/").strip()
    if normalized.startswith("/"):
        _refuse("absolute path reference")
    if len(normalized) > 1 and normalized[1] == ":":
        _refuse("drive-letter path reference")
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if not parts:
        _refuse("empty reference")
    if any(part == ".." for part in parts):
        _refuse("path traversal ('..') in reference")
    return PurePosixPath(*parts)


def _resolve_in_product(product_real: str, href: str) -> Tuple[Path, str]:
    """Return ``(resolved_path, relative_posix)`` for a safe in-product file."""
    parts = _check_reference_shape(href)
    candidate = Path(product_real).joinpath(*parts.parts)
    resolved = os.path.realpath(str(candidate))
    try:
        inside = os.path.commonpath([resolved, product_real]) == product_real
    except ValueError:  # pragma: no cover - different drives on Windows
        inside = False
    if not inside:
        _refuse("reference resolves outside the product directory (symlink or mount)")
    if not os.path.exists(resolved):
        raise _ReferenceAbsent("referenced file does not exist inside the product directory")
    if not os.path.isfile(resolved):
        _refuse("reference is not a regular file")
    relative = os.path.relpath(resolved, product_real).replace(os.sep, "/")
    return resolved, relative


def _iter_files(product_real: str) -> List[str]:
    """List regular files inside the product, skipping escaping symlinks."""
    collected: List[str] = []
    for dirpath, dirnames, filenames in os.walk(product_real, followlinks=False):
        kept: List[str] = []
        for name in sorted(dirnames):
            child = os.path.join(dirpath, name)
            if os.path.islink(child):
                real = os.path.realpath(child)
                try:
                    if os.path.commonpath([real, product_real]) != product_real:
                        continue
                except ValueError:  # pragma: no cover
                    continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                real = os.path.realpath(path)
                try:
                    if os.path.commonpath([real, product_real]) != product_real:
                        continue
                except ValueError:  # pragma: no cover
                    continue
            if not os.path.isfile(path):
                continue
            collected.append(path)
            if len(collected) > LIMITS["max_imagery_files"]:
                return collected
    return collected


# ---------------------------------------------------------------------------
# GDAL driver capabilities: measured, never assumed
# ---------------------------------------------------------------------------


def gdal_driver_capabilities(driver_names: Sequence[str] = PROBED_DRIVERS) -> Dict[str, Any]:
    """Measure which GDAL drivers the *installed* stack actually exposes.

    Uses the public ``rasterio.drivers.raster_driver_extensions()`` registry,
    which is the set rasterio can actually open. Absence is reported as
    absence. No product-format claim (ScanSAR, ground-range detected,
    geocoded, ...) is attached to any driver, because such a claim needs a real
    delivered product to establish.
    """
    probes = [name for name in driver_names if name]
    try:
        import rasterio  # type: ignore
        from rasterio.drivers import raster_driver_extensions

        registry = {str(name).upper() for name in raster_driver_extensions().values()}
        rasterio_version = getattr(rasterio, "__version__", None)
        gdal_version = None
        try:
            from rasterio import _env  # type: ignore

            gdal_version = _env.gdal_version()
        except Exception:  # pragma: no cover - depends on build
            gdal_version = None
        probe_source = (
            "measured at runtime from rasterio.drivers.raster_driver_extensions() "
            f"({len(registry)} drivers visible to rasterio, GDAL {gdal_version})"
        )
    except Exception as exc:  # pragma: no cover - optional dependency
        registry = set()
        rasterio_version = None
        gdal_version = None
        probe_source = f"rasterio driver registry unavailable: {type(exc).__name__}: {exc}"

    drivers: Dict[str, Dict[str, Any]] = {}
    for name in probes:
        present = name.upper() in registry
        if present:
            note = (
                f"{name} driver is present in the installed registry; no open route is "
                "claimed and no product-format semantics are asserted because no real "
                "delivered product is available to check them against"
            )
        else:
            note = (
                f"{name} driver is absent from the installed GDAL/rasterio registry in "
                "this environment; no substitute route is claimed and no product-format "
                "semantics are asserted"
            )
        drivers[name] = {
            "present": present,
            "note": note,
            "route_claimed": False,
        }

    return {
        "probe_source": probe_source,
        "probe_is_measurement": True,
        "rasterio_version": rasterio_version,
        "gdal_version": gdal_version,
        "driver_count": len(registry),
        "drivers": drivers,
        "caveat": (
            "Driver presence is a property of the installed software only. It says "
            "nothing about whether the delivered product is ScanSAR, ground-range "
            "detected, geocoded or orthorectified; that must be established from the "
            "real product, not from the driver list."
        ),
    }


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class _Report:
    def __init__(self) -> None:
        self.findings: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        self.errors: List[Dict[str, str]] = []

    def add(
        self,
        code: str,
        severity: str,
        subject: str,
        message: str,
        *,
        blocking: bool = True,
    ) -> None:
        self.findings.append(
            {
                "code": code,
                "severity": severity,
                "subject": subject,
                "message": message,
                "blocking": bool(blocking),
            }
        )
        if not blocking:
            if message not in self.warnings:
                self.warnings.append(message)

    def blocking(self) -> List[Dict[str, Any]]:
        return [finding for finding in self.findings if finding["blocking"]]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# product.xml
# ---------------------------------------------------------------------------


def _extract_pols(index: _Index) -> List[str]:
    found: List[str] = []
    for raw in index.texts.get("transmitterreceiverpolarisation", []) + index.texts.get(
        "transmitterreceiverpolarization", []
    ):
        for token in _split_pols(raw):
            if token not in found:
                found.append(token)
    for local in ("pol", "polarisation", "polarization"):
        for element in index.elements.get(local, []):
            raw = _clean(element.text)
            for token in _split_pols(raw or ""):
                if token not in found:
                    found.append(token)
    return found


def _split_pols(raw: str) -> List[str]:
    tokens = [raw]
    for separator in _POL_SPLIT:
        expanded: List[str] = []
        for token in tokens:
            expanded.extend(token.split(separator))
        tokens = expanded
    return [token.strip().upper() for token in tokens if token.strip().upper() in _VALID_POL]


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        _refuse(f"file exceeds the {limit} byte read limit")
    return payload


# ---------------------------------------------------------------------------
# Sigma0 lookup table
# ---------------------------------------------------------------------------


def _lut_entries(index: _Index, kind: str) -> List[Dict[str, Any]]:
    """Extract ``gain``/``offset`` entries by local name, namespace independent.

    An entry is an element whose own local name equals its value child's local
    name (``<gain><gain>``/``<offset><offset>``) and which also carries a
    polarization or incidence-angle child. This tolerates both the nested
    ``sigmaZeroLookupTable/lut/gainList/gain`` and flat ``gainList/gain``
    layouts, and the resolved element paths are reported.
    """
    entries: List[Dict[str, Any]] = []
    for element in index.elements.get(kind.lower(), []):
        if _child_text(element, kind) is None:
            continue
        if _child_text(element, "pol", "polarisation", "polarization") is None and _child_text(
            element, "incidenceangle"
        ) is None:
            continue
        entries.append(
            {
                "pol": (_child_text(element, "pol", "polarisation", "polarization") or "").upper()
                or None,
                "step": _child_text(element, "step"),
                "incidence_angle": _as_float(_child_text(element, "incidenceangle")),
                "value": _as_float(_child_text(element, kind)),
                "width": _as_float(_child_text(element, "width")),
                "element_path": index.path_of(element),
            }
        )
        if len(entries) > LIMITS["max_lut_entries"]:
            _refuse(f"lookup table has more than {LIMITS['max_lut_entries']} entries")
    return entries


def _merge_intervals(intervals: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    ordered = sorted((lo, hi) for lo, hi in intervals if hi >= lo)
    merged: List[Tuple[float, float]] = []
    for lo, hi in ordered:
        if merged and lo <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _uncovered(
    merged: Sequence[Tuple[float, float]], near: float, far: float
) -> List[Dict[str, float]]:
    gaps: List[Dict[str, float]] = []
    cursor = near
    for lo, hi in merged:
        if lo > cursor + 1e-9:
            gaps.append({"start": round(cursor, 6), "end": round(min(lo, far), 6)})
        cursor = max(cursor, hi)
        if cursor >= far:
            break
    if cursor < far - 1e-9:
        gaps.append({"start": round(cursor, 6), "end": round(far, 6)})
    return [gap for gap in gaps if gap["end"] > gap["start"] + 1e-9]


def _width_coverage(
    gains_by_pol: Dict[str, List[Dict[str, Any]]],
    near: Optional[float],
    far: Optional[float],
    width_semantics: str,
) -> Dict[str, Any]:
    per_pol: Dict[str, Any] = {}
    if near is None or far is None:
        return {
            "convention": width_semantics,
            "convention_verified_against_real_product": False,
            "evaluated": False,
            "required_range": None,
            "covered": None,
            "uncovered_ranges": [],
            "per_polarization": per_pol,
            "reason": (
                "product.xml does not declare nearRangeIncidenceAngle/farRangeIncidenceAngle, "
                "so the span the lookup table must cover is unknown; coverage not evaluated"
            ),
        }
    if far < near:
        near, far = far, near
    divisor = 2.0 if width_semantics == "bin-width" else 1.0
    for pol, entries in sorted(gains_by_pol.items()):
        intervals = [
            (
                entry["incidence_angle"] - entry["width"] / divisor,
                entry["incidence_angle"] + entry["width"] / divisor,
            )
            for entry in entries
            if entry["incidence_angle"] is not None
            and entry["width"] is not None
            and math.isfinite(entry["incidence_angle"])
            and math.isfinite(entry["width"])
        ]
        merged = _merge_intervals(intervals)
        gaps = _uncovered(merged, near, far)
        per_pol[pol] = {
            "usable_entries": len(intervals),
            "covered": not gaps,
            "covered_span": [[round(lo, 6), round(hi, 6)] for lo, hi in merged],
            "uncovered_ranges": gaps,
        }
    covered = all(entry["covered"] for entry in per_pol.values()) if per_pol else False
    gaps: List[Dict[str, Any]] = []
    for pol, entry in per_pol.items():
        for gap in entry["uncovered_ranges"]:
            gaps.append({"pol": pol, **gap})
    return {
        "convention": width_semantics,
        "convention_verified_against_real_product": False,
        "evaluated": True,
        "required_range": {"near": round(near, 6), "far": round(far, 6)},
        "covered": covered,
        "uncovered_ranges": gaps,
        "per_polarization": per_pol,
        "reason": None,
    }


def _inspect_sigma_lut(
    product_real: str,
    href: str,
    pols: Sequence[str],
    near: Optional[float],
    far: Optional[float],
    width_semantics: str,
    report: _Report,
    max_lut_bytes: int,
) -> Dict[str, Any]:
    section: Dict[str, Any] = {
        "reference": href,
        "resolved_path": None,
        "relative_path": None,
        "contained": False,
        "size_bytes": None,
        "parse_status": "not_attempted",
        "element_paths": {"gain": [], "offset": []},
        "gains": [],
        "offsets": [],
        "gain_count": 0,
        "offset_count": 0,
        "gain_finite": None,
        "gain_positive": None,
        "offset_finite": None,
        "poles_with_gains": [],
        "poles_without_gains": [],
        "incidence_grid": [],
        "width_coverage": _width_coverage({}, near, far, width_semantics),
    }

    try:
        resolved, relative = _resolve_in_product(product_real, href)
    except _ReferenceAbsent as exc:
        section["parse_status"] = "missing"
        section["refusal_reason"] = str(exc)
        report.add(
            "SIGMA_LUT_MISSING",
            "missing",
            "sigma_lookup_table",
            f"sigma0 lookup-table reference {href!r} points to a file that is not present "
            f"inside the product directory: {exc}",
        )
        return section
    except _PathRefusal as exc:
        section["refusal_reason"] = str(exc)
        report.add(
            "SIGMA_LUT_PATH_ESCAPE",
            "unsupported",
            "sigma_lookup_table",
            f"sigma0 lookup-table reference {href!r} refused: {exc}. Nothing was read.",
        )
        return section

    section["contained"] = True
    section["relative_path"] = relative
    section["resolved_path"] = str(resolved)
    resolved_path = Path(resolved)

    try:
        section["size_bytes"] = resolved_path.stat().st_size
    except OSError as exc:  # pragma: no cover - race
        section["parse_status"] = "missing"
        report.add(
            "SIGMA_LUT_MISSING",
            "missing",
            relative,
            f"sigma0 lookup table is not readable: {type(exc).__name__}: {exc}",
        )
        return section

    try:
        payload = _read_bounded(resolved_path, max_lut_bytes)
    except _PathRefusal as exc:
        section["parse_status"] = "too_large"
        report.add("SIGMA_LUT_TOO_LARGE", "unsupported", relative, str(exc))
        return section
    except OSError as exc:
        section["parse_status"] = "corrupt"
        report.add(
            "SIGMA_LUT_CORRUPT",
            "corrupt",
            relative,
            f"sigma0 lookup table could not be read: {type(exc).__name__}: {exc}",
        )
        return section

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        section["parse_status"] = "corrupt"
        report.add(
            "SIGMA_LUT_CORRUPT",
            "corrupt",
            relative,
            f"sigma0 lookup table is not well-formed XML: {exc}",
        )
        return section

    index = _Index(root)
    gains = _lut_entries(index, "gain")
    offsets = _lut_entries(index, "offset")
    section["parse_status"] = "parsed"
    section["gains"] = gains
    section["offsets"] = offsets
    section["gain_count"] = len(gains)
    section["offset_count"] = len(offsets)
    section["element_paths"] = {
        "gain": sorted({entry["element_path"] for entry in gains if entry["element_path"]}),
        "offset": sorted({entry["element_path"] for entry in offsets if entry["element_path"]}),
    }

    if not gains and not offsets:
        report.add(
            "SIGMA_LUT_NO_ENTRIES",
            "missing",
            relative,
            "sigma0 lookup table parsed but contains no gain or offset entries; the "
            "element paths found were " + (", ".join(index.paths) or "none"),
        )
        return section

    # Gains must be finite and strictly positive.
    bad_finite = [
        entry
        for entry in gains
        if entry["value"] is None or not math.isfinite(entry["value"])
    ]
    bad_positive = [entry for entry in gains if entry["value"] is not None and entry["value"] <= 0.0]
    section["gain_finite"] = not bad_finite
    section["gain_positive"] = not bad_positive
    if bad_finite:
        report.add(
            "SIGMA_LUT_GAIN_NOT_FINITE",
            "unsupported",
            relative,
            f"{len(bad_finite)} gain entr(y/ies) are missing or non-finite "
            f"(e.g. {bad_finite[0]['value']!r}); calibration is undefined for them",
        )
    if bad_positive:
        report.add(
            "SIGMA_LUT_GAIN_NOT_POSITIVE",
            "unsupported",
            relative,
            f"{len(bad_positive)} gain entr(y/ies) are not strictly positive "
            f"(e.g. {bad_positive[0]['value']!r})",
        )

    # Offsets must be finite. Zero is legitimate.
    bad_offsets = [
        entry
        for entry in offsets
        if entry["value"] is None or not math.isfinite(entry["value"])
    ]
    section["offset_finite"] = not bad_offsets
    if bad_offsets:
        report.add(
            "SIGMA_LUT_OFFSET_NOT_FINITE",
            "unsupported",
            relative,
            f"{len(bad_offsets)} offset entr(y/ies) are missing or non-finite "
            f"(e.g. {bad_offsets[0]['value']!r})",
        )

    gains_by_pol: Dict[str, List[Dict[str, Any]]] = {}
    for entry in gains:
        gains_by_pol.setdefault(entry["pol"] or "?", []).append(entry)
    section["poles_with_gains"] = sorted(pol for pol in gains_by_pol if pol != "?")
    if pols:
        section["poles_without_gains"] = [pol for pol in pols if pol not in gains_by_pol]
        if section["poles_without_gains"]:
            report.add(
                "SIGMA_LUT_NO_GAIN_FOR_POL",
                "missing",
                relative,
                "sigma0 lookup table has no gain entry for declared polarization(s): "
                + ", ".join(section["poles_without_gains"]),
            )
    grid = sorted(
        {
            round(entry["incidence_angle"], 6)
            for entry in gains
            if entry["incidence_angle"] is not None
            and math.isfinite(entry["incidence_angle"])
        }
    )
    section["incidence_grid"] = grid

    coverage = _width_coverage(gains_by_pol, near, far, width_semantics)
    if coverage["evaluated"] and coverage["covered"] is False:
        detail = "; ".join(
            f"{gap['pol']} {gap['start']}-{gap['end']}" for gap in coverage["uncovered_ranges"]
        )
        report.add(
            "SIGMA_LUT_WIDTH_COVERAGE_GAP",
            "missing",
            relative,
            "sigma0 lookup-table width coverage does not span the incidence range "
            f"declared by product.xml; uncovered: {detail}",
        )
    section["width_coverage"] = coverage
    return section


# ---------------------------------------------------------------------------
# Imagery discovery
# ---------------------------------------------------------------------------


def _tokens(stem: str) -> List[str]:
    current = ""
    out: List[str] = []
    for char in stem:
        if char.isalnum():
            current += char.upper()
        else:
            if current:
                out.append(current)
            current = ""
    if current:
        out.append(current)
    return out


def _is_raster(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in RASTER_SUFFIXES)


def _inspect_imagery(
    product_real: str,
    pols: Sequence[str],
    representation: Optional[str],
    report: _Report,
) -> Dict[str, Any]:
    files: List[str] = []
    for path in _iter_files(product_real):
        if _is_raster(os.path.basename(path)):
            files.append(os.path.relpath(path, product_real).replace(os.sep, "/"))
    files.sort()

    by_pol: Dict[str, Dict[str, Any]] = {}
    for pol in pols:
        matches = [name for name in files if pol in _tokens(os.path.basename(name))]
        components = sorted(
            {
                component
                for name in matches
                for component in COMPLEX_COMPONENTS
                if component in _tokens(os.path.basename(name))
            }
        )
        by_pol[pol] = {
            "files": matches,
            "count": len(matches),
            "components": components,
        }

    missing = [pol for pol in pols if not by_pol[pol]["files"]]
    incomplete = [
        pol
        for pol in pols
        if representation == "complex_iq"
        and by_pol[pol]["files"]
        and not all(component in by_pol[pol]["components"] for component in COMPLEX_COMPONENTS)
    ]

    if missing:
        report.add(
            "IMAGERY_MISSING_FOR_POL",
            "missing",
            "imagery",
            "no imagery file found inside the product directory for declared "
            f"polarization(s): {', '.join(missing)}; discovered rasters: "
            + (", ".join(files) if files else "none"),
        )
    if incomplete:
        report.add(
            "IMAGERY_INCOMPLETE_FOR_POL",
            "missing",
            "imagery",
            "complex (I/Q) product requires both I and Q imagery for declared "
            f"polarization(s): {', '.join(incomplete)}; "
            f"required components are {', '.join(COMPLEX_COMPONENTS)}",
        )

    return {
        "search_suffixes": list(RASTER_SUFFIXES),
        "discovered_files": files,
        "discovered_count": len(files),
        "by_polarization": by_pol,
        "missing_polarizations": missing,
        "incomplete_polarizations": incomplete,
        "pixels_read": False,
        "note": (
            "Imagery is checked for existence only. No pixel was read, no band was "
            "opened and no radiometric value was inspected."
        ),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def preflight(
    product_dir: os.PathLike | str,
    *,
    require_gdal_driver: bool = False,
    width_semantics: str = "bin-width",
    max_lut_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """Inspect *product_dir* read-only and return a preflight report.

    ``status`` is ``"ready_for_calibration"`` when nothing blocking was found,
    otherwise ``"blocked"`` with the reasons in ``blocking``. It is never
    ``"verified"``, ``"processed"`` or ``"calibrated"``.

    Args:
        product_dir: directory containing the delivered ``product.xml``.
        require_gdal_driver: when true, an absent GDAL driver for the declared
            product type becomes a blocking finding instead of an advisory one.
        width_semantics: ``"bin-width"`` (default) treats the lookup table's
            ``width`` as the full width of a bin centred on that entry's
            incidence angle; ``"half-width"`` treats it as a half-width. Neither
            convention has been verified against a real delivered lookup table.
        max_lut_bytes: optional override of the lookup-table read limit.
    """
    if width_semantics not in ("bin-width", "half-width"):
        raise ValueError("width_semantics must be 'bin-width' or 'half-width'")
    lut_limit = int(max_lut_bytes) if max_lut_bytes is not None else LIMITS["max_lut_bytes"]
    if lut_limit <= 0:
        raise ValueError("max_lut_bytes must be positive")

    root = Path(product_dir)
    if not root.exists():
        raise FileNotFoundError(f"product directory not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    product_real = _product_root(root)
    report = _Report()
    capabilities = gdal_driver_capabilities()

    product_section: Dict[str, Any] = {
        "product_xml": None,
        "product_type": None,
        "sample_type": None,
        "sample_representation": None,
        "bits_per_sample": None,
        "dimensions": {"number_of_lines": None, "number_of_samples_per_line": None},
        "polarizations": [],
        "incidence_angle_range": {"near": None, "far": None},
        "namespaces": [],
    }

    xml_path = os.path.join(product_real, "product.xml")
    product_section["product_xml"] = xml_path
    if not os.path.isfile(xml_path):
        report.add(
            "PRODUCT_XML_MISSING",
            "missing",
            "product.xml",
            f"no product.xml at {xml_path}; this directory is not a delivered RADARSAT-2 "
            "product root",
        )
        return _assemble(
            root,
            product_real,
            product_section,
            {"reference": None},
            {"discovered_files": [], "by_polarization": {}, "missing_polarizations": []},
            _calibration_output(None),
            capabilities,
            None,
            report,
        )

    try:
        payload = _read_bounded(Path(xml_path), LIMITS["max_xml_bytes"])
    except _PathRefusal as exc:
        report.add("PRODUCT_XML_TOO_LARGE", "unsupported", "product.xml", str(exc))
        return _assemble(
            root,
            product_real,
            product_section,
            {"reference": None},
            {"discovered_files": [], "by_polarization": {}, "missing_polarizations": []},
            _calibration_output(None),
            capabilities,
            None,
            report,
        )
    except OSError as exc:
        report.add(
            "PRODUCT_XML_UNREADABLE",
            "corrupt",
            "product.xml",
            f"product.xml could not be read: {type(exc).__name__}: {exc}",
        )
        return _assemble(
            root,
            product_real,
            product_section,
            {"reference": None},
            {"discovered_files": [], "by_polarization": {}, "missing_polarizations": []},
            _calibration_output(None),
            capabilities,
            None,
            report,
        )

    try:
        xml_root = ET.fromstring(payload)
    except ET.ParseError as exc:
        report.add(
            "PRODUCT_XML_UNPARSEABLE",
            "corrupt",
            "product.xml",
            f"product.xml is not well-formed XML: {exc}",
        )
        return _assemble(
            root,
            product_real,
            product_section,
            {"reference": None},
            {"discovered_files": [], "by_polarization": {}, "missing_polarizations": []},
            _calibration_output(None),
            capabilities,
            None,
            report,
        )

    index = _Index(xml_root)
    product_section["namespaces"] = index.namespaces
    product_section["product_type"] = (_clean(index.first(*_PRODUCT_TYPE_NAMES)) or "").upper() or None
    raw_sample = (_clean(index.first(*_SAMPLE_TYPE_NAMES)) or "").upper().replace("-", "_") or None
    product_section["sample_type"] = raw_sample

    representation: Optional[str] = None
    if raw_sample is None:
        report.add(
            "SAMPLE_TYPE_MISSING",
            "missing",
            "product.xml/imageAttributes/sampleType",
            "product.xml does not declare a sample type, so it is unknown whether the "
            "delivered samples are detected magnitude or complex I/Q",
        )
    elif raw_sample in COMPLEX_SAMPLE_TYPES:
        representation = "complex_iq"
    elif raw_sample in MAGNITUDE_SAMPLE_TYPES:
        representation = "magnitude"
    else:
        report.add(
            "SAMPLE_TYPE_UNSUPPORTED",
            "unsupported",
            "product.xml/imageAttributes/sampleType",
            f"sampleType={raw_sample!r} is not a representation this preflight can gate; "
            f"supported: complex {sorted(COMPLEX_SAMPLE_TYPES)}, magnitude "
            f"{sorted(MAGNITUDE_SAMPLE_TYPES)}",
        )
    product_section["sample_representation"] = representation

    bits = _as_int(index.first(*_BITS_NAMES))
    product_section["bits_per_sample"] = bits
    if bits is None:
        report.add(
            "BITS_MISSING",
            "missing",
            "product.xml/imageAttributes/bitsPerSample",
            "product.xml does not declare a bit depth",
        )
    elif representation is not None:
        allowed = SUPPORTED_BIT_DEPTHS[representation]
        if bits not in allowed:
            report.add(
                "BITS_UNSUPPORTED",
                "unsupported",
                "product.xml/imageAttributes/bitsPerSample",
                f"bitsPerSample={bits} is not supported for {representation} samples "
                f"(supported: {', '.join(str(value) for value in allowed)})",
            )

    lines = _as_int(index.first(*_LINES_NAMES))
    samples = _as_int(index.first(*_SAMPLES_NAMES))
    product_section["dimensions"] = {
        "number_of_lines": lines,
        "number_of_samples_per_line": samples,
    }
    if lines is None or samples is None or lines <= 0 or samples <= 0:
        absent = [
            name
            for name, value in (("numberOfLines", lines), ("numberOfSamplesPerLine", samples))
            if value is None or value <= 0
        ]
        report.add(
            "DIMENSIONS_MISSING",
            "missing",
            "product.xml/imageAttributes",
            "product.xml does not declare a usable positive image size; missing or "
            "non-positive: " + ", ".join(absent),
        )

    pols = _extract_pols(index)
    product_section["polarizations"] = pols
    if not pols:
        report.add(
            "POLARIZATIONS_MISSING",
            "missing",
            "product.xml/imageAttributes/transmitterReceiverPolarisation",
            "product.xml does not declare any usable polarization (HH/HV/VV/VH)",
        )

    near = _as_float(index.first(*_NEAR_ANGLE_NAMES))
    far = _as_float(index.first(*_FAR_ANGLE_NAMES))
    if near is not None and not math.isfinite(near):
        near = None
    if far is not None and not math.isfinite(far):
        far = None
    product_section["incidence_angle_range"] = {"near": near, "far": far}
    if near is None or far is None:
        report.add(
            "INCIDENCE_RANGE_MISSING",
            "missing",
            "product.xml/imageAttributes",
            "product.xml does not declare both nearRangeIncidenceAngle and "
            "farRangeIncidenceAngle, so the incidence span the sigma0 lookup table "
            "must cover cannot be determined; width coverage is reported as unknown",
            blocking=False,
        )

    # Sigma0 lookup-table reference.
    lut_href: Optional[str] = None
    for name in _LUT_REF_NAMES:
        element = index.find_element(name)
        if element is None:
            continue
        for attribute in ("href", "uri", "url", "fileName", "name", "xlink:href"):
            value = _clean(element.get(attribute))
            if value:
                lut_href = value
                break
        if lut_href is None:
            lut_href = _clean(element.text)
        if lut_href:
            break

    if not lut_href:
        report.add(
            "SIGMA_LUT_REFERENCE_MISSING",
            "missing",
            "product.xml/calibration",
            "product.xml declares no sigma0 calibration lookup-table reference; without "
            "it no radiometric gain can be applied",
        )
        lut_section = {
            "reference": None,
            "contained": False,
            "parse_status": "not_attempted",
            "gains": [],
            "offsets": [],
            "gain_count": 0,
            "offset_count": 0,
            "gain_finite": None,
            "gain_positive": None,
            "offset_finite": None,
            "poles_with_gains": [],
            "poles_without_gains": pols,
            "incidence_grid": [],
            "width_coverage": _width_coverage({}, near, far, width_semantics),
        }
    else:
        lut_section = _inspect_sigma_lut(
            product_real, lut_href, pols, near, far, width_semantics, report, lut_limit
        )

    imagery_section = _inspect_imagery(product_real, pols, representation, report)

    expected_driver = None
    if product_section["product_type"]:
        expected_driver = DRIVER_ROUTE_BY_PRODUCT_TYPE.get(product_section["product_type"])
    driver_present = None
    if expected_driver is not None:
        driver_present = bool(capabilities["drivers"].get(expected_driver, {}).get("present"))
        if not driver_present:
            report.add(
                "GDAL_DRIVER_UNAVAILABLE",
                "unsupported",
                "gdal",
                f"productType={product_section['product_type']} would normally be read with "
                f"the {expected_driver} driver, but that driver is absent from the "
                "installed GDAL/rasterio registry; no substitute route is claimed and no "
                "product-format semantics (ScanSAR / ground-range detected / geocoded) "
                "are asserted",
                blocking=bool(require_gdal_driver),
            )
    if capabilities["drivers"].get("SGF", {}).get("present") is None:  # pragma: no cover
        report.add(
            "GDAL_DRIVER_PROBE_INCONCLUSIVE",
            "unsupported",
            "gdal",
            "driver registry probe returned no SGF entry",
            blocking=False,
        )

    return _assemble(
        root,
        product_real,
        product_section,
        lut_section,
        imagery_section,
        _calibration_output(representation),
        capabilities,
        expected_driver if expected_driver is None else expected_driver,
        report,
        driver_present=driver_present,
        require_gdal_driver=require_gdal_driver,
    )


def _calibration_output(representation: Optional[str]) -> Dict[str, Any]:
    if representation == "magnitude":
        return {
            "sample_representation": representation,
            "expected_band_semantics": "linear_power_sigma0",
            "apply_magnitude_squared": False,
            "rule": (
                "Detected magnitude samples: once the detector applies the sigma0 lookup "
                "table the band is already linear sigma0 power, so do not square it. "
                "Treat the stored value as linear power and convert to dB with "
                "10*log10(value)."
            ),
            "source": "derived from the sampleType declared in the delivered product.xml",
            "not_verified": (
                "no pixels were read, so the stored radiometric convention of the actual "
                "delivered band has not been verified against a real product"
            ),
        }
    if representation == "complex_iq":
        return {
            "sample_representation": representation,
            "expected_band_semantics": "complex_iq_pairs",
            "apply_magnitude_squared": True,
            "rule": (
                "Complex I/Q samples: the calibrated backscatter is the magnitude squared, "
                "sigma0 = |I + jQ|**2 = I**2 + Q**2, because the band stores components "
                "rather than power. A magnitude-squared step is required before any dB "
                "conversion (10*log10). Do not treat I or Q alone as power."
            ),
            "source": "derived from the sampleType declared in the delivered product.xml",
            "not_verified": (
                "no pixels were read, so the stored component layout of the actual "
                "delivered band has not been verified against a real product"
            ),
        }
    return {
        "sample_representation": None,
        "expected_band_semantics": "undetermined",
        "apply_magnitude_squared": None,
        "rule": (
            "Undetermined: product.xml does not declare a supported sampleType, so it is "
            "unknown whether the band is already linear power or a complex I/Q pair. No "
            "power rule is asserted."
        ),
        "source": "product.xml sampleType absent or unsupported",
        "not_verified": "no pixels were read",
    }


def _assemble(
    root: Path,
    product_real: str,
    product: Dict[str, Any],
    lut: Dict[str, Any],
    imagery: Dict[str, Any],
    calibration_output: Dict[str, Any],
    capabilities: Dict[str, Any],
    expected_driver: Optional[str],
    report: _Report,
    *,
    driver_present: Optional[bool] = None,
    require_gdal_driver: bool = False,
) -> Dict[str, Any]:
    blocking = report.blocking()
    status = "ready_for_calibration" if not blocking else "blocked"
    payload: Dict[str, Any] = {
        "schema_version": "1.0",
        "tool": "processing.calibration_preflight",
        "tool_version": __version__,
        "generated_utc": _now(),
        "product_dir": str(root),
        "product_dir_resolved": product_real,
        "status": status,
        "ready_for_calibration": not blocking,
        "semantics": {
            "statement": SEMANTICS,
            "verdict_meaning": {
                "ready_for_calibration": (
                    "calibration inputs were found present, contained and numerically "
                    "sane; a calibration step may be attempted"
                ),
                "blocked": (
                    "at least one required input is missing, unsupported or corrupt; see "
                    "'blocking' for the specific reason"
                ),
            },
            "explicitly_not_claimed": [
                "not verified",
                "not processed",
                "not calibrated",
                "not geocoded",
                "not quality-checked",
                "no change detection",
            ],
        },
        "read_only": True,
        "network_used": False,
        "credentials_used": False,
        "pixels_read": False,
        "calibration_performed": False,
        "geocoding_performed": False,
        "change_detection_performed": False,
        "limits": dict(LIMITS),
        "product": product,
        "sigma_lut": lut,
        "imagery": imagery,
        "calibration_output": calibration_output,
        "gdal_capabilities": {
            **capabilities,
            "expected_driver_for_product_type": expected_driver,
            "expected_driver_present": driver_present,
            "require_gdal_driver": require_gdal_driver,
        },
        "findings": report.findings,
        "blocking": blocking,
        "blocking_count": len(blocking),
        "warnings": report.warnings,
        "errors": report.errors,
        "summary": "",
    }
    payload["summary"] = _summary(payload)
    return payload


def _summary(payload: Dict[str, Any]) -> str:
    product = payload["product"]
    representation = product.get("sample_representation") or "undetermined"
    if payload["status"] == "ready_for_calibration":
        return (
            f"ready for calibration: product.xml parsed, samples={representation}, "
            f"bits={product.get('bits_per_sample')}, "
            f"polarizations={'/'.join(product.get('polarizations') or []) or 'none'}, "
            f"sigma0 LUT contained and parsed "
            f"({payload['sigma_lut'].get('gain_count')} gains, "
            f"{payload['sigma_lut'].get('offset_count')} offsets), imagery present for every "
            f"declared polarization. Calibration inputs only; nothing was calibrated, "
            f"geocoded or verified."
        )
    codes = sorted({finding["code"] for finding in payload["blocking"]})
    return (
        f"blocked ({len(payload['blocking'])} blocking finding(s)): "
        + ", ".join(codes)
        + ". Calibration inputs are missing, unsupported or corrupt; see 'blocking'."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m processing.calibration_preflight",
        description=(
            "Read-only calibration preflight for a delivered RADARSAT-2 product "
            "directory: reports whether the calibration inputs (product.xml, sigma0 "
            "lookup table, imagery references) are present, contained and sane. "
            "No credentials, no network, no calibration, no geocoding, no pixel reads."
        ),
    )
    parser.add_argument("product_dir", help="delivered product directory (read-only)")
    parser.add_argument("--out", default=None, help="write the JSON report to this path")
    parser.add_argument("--indent", type=int, default=2, help="JSON indentation (default: 2)")
    parser.add_argument(
        "--require-gdal-driver",
        action="store_true",
        help="treat an absent GDAL driver for the declared product type as blocking",
    )
    parser.add_argument(
        "--width-semantics",
        choices=("bin-width", "half-width"),
        default="bin-width",
        help=(
            "how to interpret the lookup table's 'width' field (default: bin-width). "
            "Neither convention is verified against a real delivered lookup table."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = preflight(
            args.product_dir,
            require_gdal_driver=args.require_gdal_driver,
            width_semantics=args.width_semantics,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        temporary = out.with_name(f".{out.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=args.indent, default=str)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, out)
        finally:
            if temporary.exists():  # pragma: no cover - only on write failure
                temporary.unlink()
    else:
        json.dump(payload, sys.stdout, indent=args.indent, default=str)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
