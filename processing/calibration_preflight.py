"""Read-only calibration preflight for a delivered RADARSAT-2 product directory.

One question only: *are the inputs for a radiometric calibration step present,
contained and sane?* This tool performs **no** calibration, no geocoding, no
reprojection, no pixel read and no change detection. The strongest status it can
return is ``ready_for_calibration``; every other outcome is an explicit,
individually-coded list of what is missing, unsupported, corrupt or truncated.

Schema actually implemented
----------------------------

``product.xml`` (namespace-independent local-name matching):

* ``imageAttributes/rasterAttributes/dataType`` -> ``Mag`` or ``Complex``
* ``imageAttributes/rasterAttributes/bitsPerSample``
* ``imageAttributes/rasterAttributes/numberOfLines``
* ``imageAttributes/rasterAttributes/numberOfSamplesPerLine``
* ``imageAttributes/transmitterReceiverPolarisation``
* ``calibration/.../lookupTable`` -> the referenced **filename is the element
  text**; the lookup dimension is carried by the ``selected`` attribute, whose
  values include ``incidenceAngleCorrection`` / ``incidenceAngleRange``.

The sigma0 lookup-table file:

.. code-block:: xml

    <lut>
      <offset>SCALAR</offset>
      <gains>G0 G1 G2 ... GN</gains>
    </lut>

``<gains>`` is a **column list**, one gain per image column. There is no
``gainList``, no ``pol`` element, no ``incidenceAngle``/``width`` per-gain pair
and no per-angle interpolation in this schema. ``<offset>`` is a single scalar.

That schema is corroborated by the installed GDAL 3.12.2 binary itself, which
contains the XPath expression string ``=lut.gains`` and the literal
``incidenceAngleCorrection``.

Raw source versus calibrated representation
-------------------------------------------

These are different things and are reported separately. See
:func:`_representations`.

Drivers
-------

The driver that reads delivered RADARSAT-2 GeoTIFF products is ``RS2``. Driver
availability is measured from the installed libgdal at runtime. ``RCM`` is a
**separate** GDAL driver for RCM products and must not be mixed with the RS2
delivered-product route. ``SGF`` and ``CGX`` are **not** the RADARSAT-2 driver
and are not alternatives to it. ``productType`` is a product attribute; it is
never used to infer a driver name.

Usage::

    python -m processing.calibration_preflight /path/to/RS2_PRODUCT_DIR
    python -m processing.calibration_preflight /path/to/RS2_PRODUCT_DIR --out report.json
"""

from __future__ import annotations

import argparse
import ctypes
import glob
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
    "representations",
    "SEMANTICS",
    "LIMITS",
    "SUPPORTED_DATA_TYPES",
    "SUPPORTED_BIT_DEPTHS",
    "main",
]

__version__ = "2.0.0"

SEMANTICS = (
    "The product directory was inspected read-only. 'ready_for_calibration' means the "
    "calibration INPUTS (product.xml fields, the sigma0 lookup table and its scalar "
    "offset, its finite positive per-column gain list, and the imagery references) were "
    "found present, contained in the product directory and numerically sane. It is NOT a "
    "statement that the product has been calibrated, verified, processed, geocoded or "
    "quality-checked. No pixel was read and no calibration was performed."
)

LIMITS: Dict[str, int] = {
    "max_product_xml_bytes": 8 * 1024 * 1024,
    "max_lut_bytes": 32 * 1024 * 1024,
    "max_gain_entries": 1_000_000,
}

#: ``dataType`` values this preflight understands, mapped to a representation.
#: Anything else is reported unsupported rather than guessed at.
SUPPORTED_DATA_TYPES: Dict[str, str] = {"MAG": "magnitude", "COMPLEX": "complex"}

#: Bit depths accepted per representation.
SUPPORTED_BIT_DEPTHS: Dict[str, Tuple[int, ...]] = {"magnitude": (8, 16), "complex": (16, 32)}

#: The driver that reads delivered RADARSAT-2 GeoTIFF products.
DELIVERED_PRODUCT_DRIVER = "RS2"

#: A different GDAL driver for RCM products. Recorded separately and never mixed
#: with the delivered-product route.
SEPARATE_DRIVER = "RCM"

#: Not the RADARSAT-2 driver; recorded only so their absence is explicit.
NOT_THE_RS2_DRIVER = ("SGF", "CGX")

PROBED_DRIVERS: Tuple[str, ...] = (DELIVERED_PRODUCT_DRIVER, SEPARATE_DRIVER, "GTiff") + NOT_THE_RS2_DRIVER

_VALID_POL = {"HH", "HV", "VV", "VH"}


# ---------------------------------------------------------------------------
# XML helpers (namespace independent, local-name matching)
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
    text = " ".join(str(value).split())
    return text or None


def _as_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        parsed = _as_float(value)
        if parsed is None or not parsed.is_integer():
            return None
        return int(parsed)


def _find(root: ET.Element, *names: str) -> Optional[ET.Element]:
    """First descendant whose local name matches, in document order."""
    wanted = {name.lower() for name in names}
    for element in root.iter():
        if isinstance(element.tag, str) and _localname(element.tag) in wanted:
            return element
    return None


def _text(root: ET.Element, *names: str) -> Optional[str]:
    element = _find(root, *names)
    return _clean(element.text) if element is not None else None


def _path_of(root: ET.Element, target: ET.Element) -> Optional[str]:
    """Lowercase local-name path of *target* inside *root*."""
    if target is root:
        return _localname(root.tag)
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        stack = [(parent, "")]
        while stack:
            element, path = stack.pop()
            for child in element:
                if not isinstance(child.tag, str):
                    continue
                local = _localname(child.tag)
                child_path = f"{path}/{local}" if path else local
                if child is target:
                    return child_path
                stack.append((child, child_path))
    return None


# ---------------------------------------------------------------------------
# Bounded reads
# ---------------------------------------------------------------------------


class _Scan:
    """Result of a bounded, containment-checked file read."""

    def __init__(self) -> None:
        self.payload: Optional[bytes] = None
        self.size_bytes: Optional[int] = None
        self.truncated = False
        self.refusal: Optional[str] = None
        self.error: Optional[str] = None


def _scan_file(path: Path, limit: int) -> _Scan:
    result = _Scan()
    try:
        result.size_bytes = path.stat().st_size
        with path.open("rb") as handle:
            result.payload = handle.read(limit + 1)
    except OSError as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    if len(result.payload) > limit:
        result.truncated = True
        result.payload = None
    return result


# ---------------------------------------------------------------------------
# Path containment
# ---------------------------------------------------------------------------


class _Escape(Exception):
    """A reference escapes the product directory or is otherwise unsafe."""


class _Absent(Exception):
    """A safe reference points at something that is not there."""


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), root]) == root
    except ValueError:  # pragma: no cover - different drives
        return False


def _resolve_inside(product_real: str, href: str) -> str:
    """Resolve *href* to a real regular file inside the product, or raise."""
    normalized = str(href).replace("\\", "/").strip()
    if not normalized:
        raise _Escape("empty reference")
    if normalized.startswith("/"):
        raise _Escape("absolute path reference")
    if len(normalized) > 1 and normalized[1] == ":":
        raise _Escape("drive-letter path reference")
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if not parts:
        raise _Escape("empty reference")
    if any(part == ".." for part in parts):
        raise _Escape("path traversal ('..') in reference")
    candidate = Path(product_real).joinpath(*parts)
    resolved = os.path.realpath(str(candidate))
    if not _within(resolved, product_real):
        raise _Escape("reference resolves outside the product directory (symlink or mount)")
    if not os.path.exists(resolved):
        raise _Absent("referenced file does not exist inside the product directory")
    if not os.path.isfile(resolved):
        raise _Escape("reference is not a regular file")
    return resolved


def _iter_files(product_real: str) -> List[str]:
    """Regular files inside the product; symlinks escaping it are skipped."""
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(product_real, followlinks=False):
        kept = []
        for name in sorted(dirnames):
            child = os.path.join(dirpath, name)
            if os.path.islink(child) and not _within(child, product_real):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path) and not _within(path, product_real):
                continue
            if os.path.isfile(path):
                found.append(os.path.relpath(path, product_real).replace(os.sep, "/"))
    return sorted(found)


# ---------------------------------------------------------------------------
# GDAL driver capabilities: measured from the installed libgdal
# ---------------------------------------------------------------------------


def _full_driver_registry() -> Tuple[Optional[set], Optional[str], Optional[str]]:
    """Measure the real libgdal driver registry (not a filtered subset)."""
    try:
        import rasterio  # noqa: F401  (locates the bundled libgdal)
    except Exception:  # pragma: no cover - optional dependency
        return None, None, None
    patterns = [
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(rasterio.__file__))),
            "rasterio.libs",
            "libgdal*.so*",
        ),
        os.path.join(os.path.dirname(os.path.abspath(rasterio.__file__)), "..", "libgdal*.so*"),
    ]
    library = None
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            library = matches[0]
            break
    if library is None:
        return None, None, None
    try:
        lib = ctypes.CDLL(library)
        lib.GDALAllRegister()
        lib.GDALGetDriverCount.restype = ctypes.c_int
        lib.GDALGetDriver.restype = ctypes.c_void_p
        lib.GDALGetDriver.argtypes = [ctypes.c_int]
        lib.GDALGetDriverShortName.restype = ctypes.c_char_p
        lib.GDALGetDriverShortName.argtypes = [ctypes.c_void_p]
        names = {
            lib.GDALGetDriverShortName(lib.GDALGetDriver(i)).decode().upper()
            for i in range(lib.GDALGetDriverCount())
        }
    except Exception:  # pragma: no cover - depends on build
        return None, library, None
    return names, library, os.path.basename(library)


def gdal_driver_capabilities(driver_names: Sequence[str] = PROBED_DRIVERS) -> Dict[str, Any]:
    """Measure driver presence in the installed libgdal registry.

    Uses the full GDAL registry reached through the bundled libgdal. A filtered
    listing such as ``rasterio.drivers.raster_driver_extensions()`` is NOT used,
    because it omits drivers that libgdal genuinely has -- it hid the ``RS2``
    driver on this very stack.
    """
    names, library, library_name = _full_driver_registry()
    gdal_version = None
    try:
        from rasterio import _env  # type: ignore

        gdal_version = _env.gdal_version()
    except Exception:  # pragma: no cover
        gdal_version = None
    rasterio_version = None
    try:
        import rasterio  # type: ignore

        rasterio_version = getattr(rasterio, "__version__", None)
    except Exception:  # pragma: no cover
        pass

    if names is None:
        probe_source = (
            "libgdal registry could not be measured in this environment"
            f"{f' (library: {library_name})' if library_name else ''}; driver presence UNKNOWN"
        )
    else:
        probe_source = (
            f"measured at runtime from the full GDAL driver registry in {library_name} "
            f"({len(names)} drivers registered, GDAL {gdal_version})"
        )

    drivers: Dict[str, Dict[str, Any]] = {}
    for name in driver_names:
        present = None if names is None else (name.upper() in names)
        if present:
            note = "present in the installed GDAL registry"
        elif present is False:
            note = "absent from the installed GDAL registry"
        else:
            note = "registry not measurable here; presence unknown"
        drivers[name] = {
            "present": present,
            "note": note,
            "route_claimed": False,
        }

    return {
        "probe_source": probe_source,
        "probe_is_measurement": names is not None,
        "gdal_version": gdal_version,
        "rasterio_version": rasterio_version,
        "driver_count": None if names is None else len(names),
        "drivers": drivers,
        "delivered_product_driver": DELIVERED_PRODUCT_DRIVER,
        "separate_driver": {
            "name": SEPARATE_DRIVER,
            "relationship": "separate GDAL driver for RCM products; not mixed with the "
            f"{DELIVERED_PRODUCT_DRIVER} delivered-product route",
        },
        "not_the_rs2_driver": list(NOT_THE_RS2_DRIVER),
        "product_type_is_not_a_driver": (
            "productType in product.xml is a product attribute. It is never used to infer a "
            "GDAL driver name, and it must not be matched against the driver list."
        ),
    }


# ---------------------------------------------------------------------------
# Representations: raw source versus future calibrated
# ---------------------------------------------------------------------------


def representations(data_type: Optional[str], representation: Optional[str]) -> Dict[str, Any]:
    """Describe the raw delivered samples and the future calibrated band.

    These are deliberately separate. This tool performs no calibration, so it
    only states what a later calibration step would have to do and what a
    later consumer of an already-calibrated band would see.
    """
    if representation == "magnitude":
        raw = {
            "data_type": data_type,
            "representation": "magnitude",
            "stored_values": "quantised amplitude digital numbers (DN)",
            "is_power": False,
            "note": "raw source samples are amplitude DN, NOT backscatter power",
        }
        future = {
            "calibrated": False,
            "formula": "sigma0 = (DN**2 + offset) / gain[column]",
            "gain_is_applied": True,
            "requires_magnitude_squared": True,
            "output_units": "linear sigma0 power",
            "output_is_power": True,
            "square_output_again": False,
            "gdal_band_metadata_item": "RADARSAT_2_CALIB:SIGMA0",
            "note": (
                "A band opened with GDAL metadata item RADARSAT_2_CALIB:SIGMA0 is already "
                "linear sigma0 POWER. Do not square it again. Squaring is a step in producing "
                "that band from raw DN, not a step to apply to it afterwards."
            ),
        }
    elif representation == "complex":
        raw = {
            "data_type": data_type,
            "representation": "complex",
            "stored_values": "in-phase and quadrature components (I, Q)",
            "is_power": False,
            "note": "raw source samples are components; neither I nor Q is power",
        }
        future = {
            "calibrated": False,
            "formula": "sigma0 = (I**2 + Q**2 + offset) / gain[column]",
            "gain_is_applied": True,
            "requires_magnitude_squared": True,
            "output_units": "linear sigma0 power",
            "output_is_power": True,
            "square_output_again": False,
            "gdal_band_metadata_item": "RADARSAT_2_CALIB:SIGMA0",
            "note": (
                "The magnitude-squared is the power of the complex sample and the gain still "
                "divides; abs(I+jQ)**2 ALONE is not sigma0. A band opened with GDAL metadata "
                "item RADARSAT_2_CALIB:SIGMA0 is already linear power; do not square it again."
            ),
        }
    else:
        raw = {
            "data_type": data_type,
            "representation": None,
            "stored_values": "unknown",
            "is_power": None,
            "note": "product.xml does not declare a supported rasterAttributes/dataType",
        }
        future = {
            "calibrated": False,
            "formula": None,
            "gain_is_applied": None,
            "requires_magnitude_squared": None,
            "output_units": None,
            "output_is_power": None,
            "square_output_again": None,
            "gdal_band_metadata_item": None,
            "note": "no calibration rule asserted: dataType is absent or unsupported",
        }

    return {
        "calibration_performed_by_this_tool": False,
        "raw_source": raw,
        "future_calibrated": future,
        "formula_verified_against_gdal_source": False,
        "formula_verification_note": (
            "The formula above is the CSA/project calibration convention specified for this "
            "work. It has NOT been verified against GDAL 3.12.2 source or an empirical lab "
            "artifact in this session, because no real RADARSAT-2 product is available here. "
            "What WAS verified against the installed libgdal is only that the metadata domain "
            "'RADARSAT_2_CALIB' and item name 'RADARSAT_2_CALIB:SIGMA0' exist, together with "
            "the LUT XPath '=lut.gains' and the literal 'incidenceAngleCorrection'."
        ),
    }


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def _finding(code: str, severity: str, subject: str, message: str, blocking: bool = True) -> Dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "subject": subject,
        "message": message,
        "blocking": bool(blocking),
    }


# ---------------------------------------------------------------------------
# Sigma0 lookup table
# ---------------------------------------------------------------------------


def _parse_gains(text: Optional[str], limit: int) -> Tuple[Optional[List[float]], Optional[str]]:
    """Parse the ``<gains>`` column list. Returns ``(values, error)``."""
    if text is None:
        return None, "gains element is absent or empty"
    tokens = text.split()
    if not tokens:
        return None, "gains element contains no values"
    if len(tokens) > limit:
        return None, f"gains list has more than {limit} entries"
    values: List[float] = []
    for position, token in enumerate(tokens):
        try:
            values.append(float(token))
        except ValueError:
            return None, f"gains entry {position} ({token!r}) is not a number"
    return values, None


def _inspect_lut(
    product_real: str,
    reference: str,
    expected_width: Optional[int],
    findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    section: Dict[str, Any] = {
        "reference": reference,
        "reference_is_element_text": True,
        "relative_path": None,
        "contained": False,
        "size_bytes": None,
        "status": "not_attempted",
        "lut_element_path": None,
        "offset": None,
        "offset_finite": None,
        "gain_count": 0,
        "gains_finite_positive": None,
        "min_gain": None,
        "max_gain": None,
        "covers_raster_width": None,
        "required_width": expected_width,
        "non_finite_gain_count": 0,
        "non_positive_gain_count": 0,
    }

    try:
        resolved = _resolve_inside(product_real, reference)
    except _Absent as exc:
        section["status"] = "missing"
        findings.append(
            _finding("LUT_MISSING", "missing", "sigma0_lookup_table", f"{exc}: {reference!r}")
        )
        return section
    except _Escape as exc:
        section["status"] = "refused"
        findings.append(
            _finding(
                "LUT_PATH_ESCAPE",
                "unsupported",
                "sigma0_lookup_table",
                f"reference {reference!r} refused: {exc}. Nothing was read.",
            )
        )
        return section

    section["contained"] = True
    section["relative_path"] = os.path.relpath(resolved, product_real).replace(os.sep, "/")
    resolved_path = Path(resolved)

    scan = _scan_file(resolved_path, LIMITS["max_lut_bytes"])
    section["size_bytes"] = scan.size_bytes
    if scan.error:
        section["status"] = "unreadable"
        findings.append(
            _finding("LUT_UNREADABLE", "corrupt", section["relative_path"], scan.error)
        )
        return section
    if scan.truncated:
        section["status"] = "truncated"
        findings.append(
            _finding(
                "LUT_SCAN_TRUNCATED",
                "unsupported",
                section["relative_path"],
                f"lookup table is {scan.size_bytes} bytes, above the bounded scan limit of "
                f"{LIMITS['max_lut_bytes']} bytes; it was not parsed and nothing is asserted "
                "about its contents",
            )
        )
        return section

    assert scan.payload is not None
    try:
        root = ET.fromstring(scan.payload)
    except ET.ParseError as exc:
        section["status"] = "corrupt"
        findings.append(
            _finding(
                "LUT_CORRUPT",
                "corrupt",
                section["relative_path"],
                f"lookup table is not well-formed XML: {exc}",
            )
        )
        return section

    lut = _find(root, "lut")
    if lut is None:
        section["status"] = "corrupt"
        findings.append(
            _finding(
                "LUT_NO_LUT_ELEMENT",
                "corrupt",
                section["relative_path"],
                "lookup table parsed but contains no <lut> element",
            )
        )
        return section

    section["lut_element_path"] = _path_of(root, lut)
    section["status"] = "parsed"

    offset = _as_float(_text(lut, "offset"))
    section["offset"] = offset
    section["offset_finite"] = offset is not None and math.isfinite(offset)
    if not section["offset_finite"]:
        findings.append(
            _finding(
                "LUT_OFFSET_NOT_FINITE",
                "unsupported",
                section["relative_path"],
                f"<offset> must be a finite scalar; got {offset!r}",
            )
        )

    values, error = _parse_gains(_text(lut, "gains"), LIMITS["max_gain_entries"])
    if values is None:
        section["gains_finite_positive"] = False
        findings.append(
            _finding(
                "LUT_GAINS_UNPARSEABLE",
                "corrupt",
                section["relative_path"],
                f"<gains> column list could not be parsed: {error}",
            )
        )
        return section

    section["gain_count"] = len(values)
    non_finite = [value for value in values if not math.isfinite(value)]
    non_positive = [value for value in values if math.isfinite(value) and value <= 0.0]
    section["non_finite_gain_count"] = len(non_finite)
    section["non_positive_gain_count"] = len(non_positive)
    finite = [value for value in values if math.isfinite(value)]
    section["min_gain"] = min(finite) if finite else None
    section["max_gain"] = max(finite) if finite else None
    section["gains_finite_positive"] = not non_finite and not non_positive

    if non_finite:
        findings.append(
            _finding(
                "LUT_GAINS_NOT_FINITE",
                "unsupported",
                section["relative_path"],
                f"{len(non_finite)} of {len(values)} gains are not finite (NaN or infinity)",
            )
        )
    if non_positive:
        findings.append(
            _finding(
                "LUT_GAINS_NOT_POSITIVE",
                "unsupported",
                section["relative_path"],
                f"{len(non_positive)} of {len(values)} gains are not strictly positive",
            )
        )

    if expected_width is None:
        section["covers_raster_width"] = None
        findings.append(
            _finding(
                "WIDTH_UNKNOWN",
                "missing",
                section["relative_path"],
                "raster width is unknown, so it cannot be checked that the gain list covers "
                "every image column",
                blocking=False,
            )
        )
    else:
        covered = len(values) >= expected_width
        section["covers_raster_width"] = covered
        if not covered:
            findings.append(
                _finding(
                    "LUT_GAINS_DO_NOT_COVER_WIDTH",
                    "missing",
                    section["relative_path"],
                    f"gain list has {len(values)} entries but the raster is "
                    f"{expected_width} samples wide; columns "
                    f"{len(values)}..{expected_width - 1} would have no gain",
                )
            )
    return section


# ---------------------------------------------------------------------------
# Imagery
# ---------------------------------------------------------------------------

_RASTER_SUFFIXES = (".tif", ".tiff")


def _tokens(stem: str) -> List[str]:
    out: List[str] = []
    current = ""
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


def _inspect_imagery(
    product_real: str, pols: Sequence[str], representation: Optional[str], findings: List[Dict[str, Any]]
) -> Dict[str, Any]:
    files = [
        name
        for name in _iter_files(product_real)
        if name.lower().endswith(_RASTER_SUFFIXES)
    ]
    by_pol: Dict[str, Dict[str, Any]] = {}
    for pol in pols:
        matches = [name for name in files if pol in _tokens(os.path.basename(name))]
        components = sorted(
            {
                component
                for name in matches
                for component in ("I", "Q")
                if component in _tokens(os.path.basename(name))
            }
        )
        by_pol[pol] = {"files": matches, "count": len(matches), "components": components}

    missing = [pol for pol in pols if not by_pol[pol]["files"]]
    incomplete = [
        pol
        for pol in pols
        if representation == "complex"
        and by_pol[pol]["files"]
        and not all(component in by_pol[pol]["components"] for component in ("I", "Q"))
    ]
    if missing:
        findings.append(
            _finding(
                "IMAGERY_MISSING_FOR_POL",
                "missing",
                "imagery",
                f"no imagery file for declared polarization(s): {', '.join(missing)}; "
                f"rasters discovered: {', '.join(files) if files else 'none'}",
            )
        )
    if incomplete:
        findings.append(
            _finding(
                "IMAGERY_INCOMPLETE_FOR_POL",
                "missing",
                "imagery",
                f"complex dataType requires both I and Q imagery for: {', '.join(incomplete)}",
            )
        )
    return {
        "search_suffixes": list(_RASTER_SUFFIXES),
        "discovered_files": files,
        "by_polarization": by_pol,
        "missing_polarizations": missing,
        "incomplete_polarizations": incomplete,
        "pixels_read": False,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _lut_reference(root: ET.Element) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return ``(filename, selected, element_path)`` for the sigma0 lookup table.

    In this schema the lookup-table **filename is the element text** of
    ``<lookupTable>``; the ``selected`` attribute names the lookup dimension.
    """
    element = _find(root, "lookupTable")
    if element is None:
        return None, None, None
    filename = _clean(element.text)
    selected = None
    for attribute in ("selected", "xlink:href"):
        selected = _clean(element.get(attribute))
        if selected:
            break
    return filename, selected, _path_of(root, element)


def preflight(
    product_dir: os.PathLike | str,
    *,
    require_gdal_driver: bool = True,
) -> Dict[str, Any]:
    """Inspect *product_dir* read-only and return a preflight report.

    Read-only with respect to *product_dir*: nothing inside it is created,
    modified or removed. This function does not write any file at all; the CLI
    owns output writing and refuses to write inside the product directory.
    """
    root = Path(product_dir)
    if not root.exists():
        raise FileNotFoundError(f"product directory not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    product_real = os.path.realpath(str(root))
    capabilities = gdal_driver_capabilities()
    findings: List[Dict[str, Any]] = []

    product: Dict[str, Any] = {
        "product_xml_relative": "product.xml",
        "product_xml_contained": False,
        "product_type": None,
        "acquisition_type": None,
        "data_type": None,
        "representation": None,
        "bits_per_sample": None,
        "number_of_lines": None,
        "number_of_samples_per_line": None,
        "polarizations": [],
        "data_type_element_path": None,
        "lut_reference_element_path": None,
        "lut_selected": None,
    }

    # --- product.xml, with symlink containment enforced ------------------------
    xml_candidate = Path(product_real) / "product.xml"
    xml_path = None
    if os.path.islink(str(xml_candidate)) and not _within(str(xml_candidate), product_real):
        findings.append(
            _finding(
                "PRODUCT_XML_PATH_ESCAPE",
                "unsupported",
                "product.xml",
                "product.xml is a symlink resolving outside the product directory; refused and "
                "not read",
            )
        )
    else:
        try:
            xml_path = _resolve_inside(product_real, "product.xml")
            product["product_xml_contained"] = True
        except _Absent:
            findings.append(
                _finding(
                    "PRODUCT_XML_MISSING",
                    "missing",
                    "product.xml",
                    f"no product.xml inside {product_real}",
                )
            )
        except _Escape as exc:
            findings.append(
                _finding(
                    "PRODUCT_XML_PATH_ESCAPE",
                    "unsupported",
                    "product.xml",
                    f"product.xml refused: {exc}",
                )
            )

    lut_section: Dict[str, Any] = {
        "reference": None,
        "status": "not_attempted",
        "contained": False,
    }
    imagery_section: Dict[str, Any] = {
        "discovered_files": [],
        "by_polarization": {},
        "missing_polarizations": [],
        "pixels_read": False,
    }
    representation = None

    if xml_path is not None:
        scan = _scan_file(Path(xml_path), LIMITS["max_product_xml_bytes"])
        product["product_xml_size_bytes"] = scan.size_bytes
        if scan.error:
            findings.append(
                _finding("PRODUCT_XML_UNREADABLE", "corrupt", "product.xml", scan.error)
            )
        elif scan.truncated:
            findings.append(
                _finding(
                    "PRODUCT_XML_SCAN_TRUNCATED",
                    "unsupported",
                    "product.xml",
                    f"product.xml is {scan.size_bytes} bytes, above the bounded scan limit of "
                    f"{LIMITS['max_product_xml_bytes']} bytes; it was not parsed",
                )
            )
        else:
            assert scan.payload is not None
            try:
                xml_root = ET.fromstring(scan.payload)
            except ET.ParseError as exc:
                findings.append(
                    _finding(
                        "PRODUCT_XML_UNPARSEABLE",
                        "corrupt",
                        "product.xml",
                        f"product.xml is not well-formed XML: {exc}",
                    )
                )
            else:
                representation = _read_product_xml(
                    xml_root, product, product_real, lut_section, findings
                )

    representation = product.get("representation")
    expected_width = product.get("number_of_samples_per_line")
    reference = lut_section.get("reference")
    if reference:
        lut_section = _inspect_lut(product_real, reference, expected_width, findings)

    imagery_section = _inspect_imagery(
        product_real, product.get("polarizations") or [], representation, findings
    )

    driver_present = capabilities["drivers"][DELIVERED_PRODUCT_DRIVER]["present"]
    if driver_present is False:
        findings.append(
            _finding(
                "GDAL_DRIVER_UNAVAILABLE",
                "unsupported",
                "gdal",
                f"the {DELIVERED_PRODUCT_DRIVER} driver, which reads delivered RADARSAT-2 "
                "GeoTIFF products, is absent from the installed GDAL registry; no substitute "
                f"route is claimed and {SEPARATE_DRIVER} is a different driver for RCM "
                "products and is not a fallback",
                blocking=bool(require_gdal_driver),
            )
        )

    blocking = [item for item in findings if item["blocking"]]
    payload: Dict[str, Any] = {
        "schema_version": "2.0",
        "tool": "processing.calibration_preflight",
        "tool_version": __version__,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "product_dir": str(root),
        "product_dir_resolved": product_real,
        "status": "ready_for_calibration" if not blocking else "blocked",
        "ready_for_calibration": not blocking,
        "semantics": {
            "statement": SEMANTICS,
            "explicitly_not_claimed": [
                "not verified",
                "not processed",
                "not calibrated",
                "not geocoded",
                "not change detected",
            ],
        },
        "read_only": True,
        "network_used": False,
        "credentials_used": False,
        "pixels_read": False,
        "calibration_performed": False,
        "geocoding_performed": False,
        "limits": dict(LIMITS),
        "product": product,
        "sigma_lut": lut_section,
        "imagery": imagery_section,
        "representations": representations(product.get("data_type"), representation),
        "gdal_capabilities": capabilities,
        "findings": findings,
        "blocking": blocking,
        "blocking_count": len(blocking),
        "warnings": [item["message"] for item in findings if not item["blocking"]],
        "errors": [
            item for item in findings if item["severity"] == "corrupt"
        ],
    }
    payload["summary"] = _summary(payload)
    return payload


def _read_product_xml(
    root: ET.Element,
    product: Dict[str, Any],
    product_real: str,
    lut_section: Dict[str, Any],
    findings: List[Dict[str, Any]],
) -> Optional[str]:
    """Extract and check the product.xml fields this preflight depends on."""
    product["product_type"] = _text(root, "productType")
    product["acquisition_type"] = _text(root, "acquisitionType")

    data_type_element = _find(root, "dataType")
    data_type_raw = _clean(data_type_element.text) if data_type_element is not None else None
    product["data_type"] = data_type_raw
    if data_type_element is not None:
        product["data_type_element_path"] = _path_of(root, data_type_element)

    representation = None
    if data_type_raw is None:
        findings.append(
            _finding(
                "DATA_TYPE_MISSING",
                "missing",
                "imageAttributes/rasterAttributes/dataType",
                "product.xml does not declare rasterAttributes/dataType, so it is unknown "
                "whether the samples are Mag or Complex",
            )
        )
    else:
        key = data_type_raw.strip().upper()
        if key in SUPPORTED_DATA_TYPES:
            representation = SUPPORTED_DATA_TYPES[key]
        else:
            findings.append(
                _finding(
                    "DATA_TYPE_UNSUPPORTED",
                    "unsupported",
                    "imageAttributes/rasterAttributes/dataType",
                    f"dataType={data_type_raw!r} is not supported; supported: "
                    + ", ".join(sorted(SUPPORTED_DATA_TYPES)),
                )
            )
    product["representation"] = representation

    bits = _as_int(_text(root, "bitsPerSample"))
    product["bits_per_sample"] = bits
    if bits is None:
        findings.append(
            _finding(
                "BITS_MISSING",
                "missing",
                "imageAttributes/rasterAttributes/bitsPerSample",
                "product.xml does not declare a usable bitsPerSample",
            )
        )
    elif representation is not None and bits not in SUPPORTED_BIT_DEPTHS[representation]:
        findings.append(
            _finding(
                "BITS_UNSUPPORTED",
                "unsupported",
                "imageAttributes/rasterAttributes/bitsPerSample",
                f"bitsPerSample={bits} is not supported for {representation} data (supported: "
                + ", ".join(str(value) for value in SUPPORTED_BIT_DEPTHS[representation])
                + ")",
            )
        )

    lines = _as_int(_text(root, "numberOfLines"))
    samples = _as_int(_text(root, "numberOfSamplesPerLine"))
    product["number_of_lines"] = lines
    product["number_of_samples_per_line"] = samples
    for label, value in (("numberOfLines", lines), ("numberOfSamplesPerLine", samples)):
        if value is None or value <= 0:
            findings.append(
                _finding(
                    "DIMENSIONS_MISSING",
                    "missing",
                    f"imageAttributes/rasterAttributes/{label}",
                    f"{label} is missing or not a positive integer (got {value!r})",
                )
            )

    pols: List[str] = []
    raw_pols = _text(root, "transmitterReceiverPolarisation") or ""
    for token in raw_pols.replace("+", " ").replace("/", " ").replace(",", " ").split():
        upper = token.strip().upper()
        if upper in _VALID_POL and upper not in pols:
            pols.append(upper)
    product["polarizations"] = pols
    if not pols:
        findings.append(
            _finding(
                "POLARIZATIONS_MISSING",
                "missing",
                "imageAttributes/transmitterReceiverPolarisation",
                "product.xml declares no usable polarization (HH/HV/VV/VH)",
            )
        )

    reference, selected, element_path = _lut_reference(root)
    lut_section["reference"] = reference
    lut_section["reference_selected"] = selected
    product["lut_selected"] = selected
    product["lut_reference_element_path"] = element_path
    if reference is None:
        findings.append(
            _finding(
                "LUT_REFERENCE_MISSING",
                "missing",
                "calibration/lookupTable",
                "product.xml declares no sigma0 lookupTable element text, so no gain can be "
                "applied",
            )
        )
    return representation


def _summary(payload: Dict[str, Any]) -> str:
    product = payload["product"]
    lut = payload["sigma_lut"]
    if payload["status"] == "ready_for_calibration":
        return (
            "ready for calibration: product.xml contained and parsed, dataType="
            f"{product.get('data_type')}, bitsPerSample={product.get('bits_per_sample')}, "
            f"{product.get('number_of_samples_per_line')}x{product.get('number_of_lines')} "
            f"samples, polarizations={'/'.join(product.get('polarizations') or []) or 'none'}, "
            f"sigma0 LUT {lut.get('relative_path')} parsed with offset={lut.get('offset')} and "
            f"{lut.get('gain_count')} finite positive gains covering the raster width, imagery "
            "present for every declared polarization. Calibration INPUTS only; nothing was "
            "calibrated, geocoded or verified."
        )
    codes = sorted({item["code"] for item in payload["blocking"]})
    return (
        f"blocked ({len(payload['blocking'])} blocking finding(s)): "
        + ", ".join(codes)
        + ". Calibration inputs are missing, unsupported, corrupt or truncated."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m processing.calibration_preflight",
        description=(
            "Read-only calibration preflight for a delivered RADARSAT-2 product directory. "
            "Reports whether the calibration inputs are present, contained and sane. Performs "
            "no calibration, no geocoding, no pixel read. No credentials, no network."
        ),
    )
    parser.add_argument("product_dir", help="delivered product directory (read-only)")
    parser.add_argument("--out", default=None, help="write the JSON report here (never inside the product)")
    parser.add_argument("--indent", type=int, default=2)
    parser.add_argument(
        "--allow-absent-driver",
        action="store_true",
        help="make an absent GDAL RS2 driver advisory instead of blocking",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = preflight(
            args.product_dir, require_gdal_driver=not args.allow_absent_driver
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.out:
        out = os.path.realpath(args.out)
        if _within(out, os.path.realpath(args.product_dir)):
            print(
                "error: refusing to write the report inside the read-only product directory",
                file=sys.stderr,
            )
            return 2
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=args.indent, default=str)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():  # pragma: no cover - only on write failure
                temporary.unlink()
    else:
        json.dump(payload, sys.stdout, indent=args.indent, default=str)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())