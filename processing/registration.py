"""Residual coregistration diagnostic for two prepared, calibrated, overlapping rasters.

Scope
-----
This module measures **how far apart two already-prepared rasters actually are**, and
reports the evidence. It is a diagnostic, not a correction:

    two prepared rasters on a real CRS (linear power, sigma0/gamma0)
      -> common reference grid over the overlapping footprint (reproject + crop)
      -> tiled candidate patches, jointly valid and textured enough to correlate
      -> phase correlation on each selected patch -> one sub-pixel shift per patch
      -> robust centre shift, per-patch deviations, spatial spread of the estimates
      -> a status decided **only** by the accept/reject criterion the caller supplied

What this module deliberately does not do
-----------------------------------------
* It is **not** a raw-product adapter. It never opens, calibrates, geocodes or
  terrain-corrects a RADARSAT-2 product, and it never authenticates to EODMS.
* It is **not** an automatic warper. It never resamples the *delivered* rasters to
  fix what it measured, and it never applies a correction to either input.
* It never sets, infers or asserts ``manifest.registration.status``, never writes a
  manifest, and never publishes an analysis bundle. Its only output is one JSON
  diagnostic at a path the caller names. The sentence offered under
  ``manifest_registration_diagnostic_text`` is a *candidate* for a human to consider;
  accepting it stays a manual, out-of-band decision.
* It never reports a probability, confidence, accuracy or quality score. Every number
  it emits is a directly measured quantity (a shift, a peak height, a spread) or a
  threshold the caller supplied.
* It never attributes a cause to any observed difference, and it never expands a
  two-image comparison into a time series.

Alignment of grids is not registration evidence
------------------------------------------------
Reprojecting two rasters onto a common grid is *necessary* to compare them and is
*not* evidence that they are aligned. The status below is computed only from the
per-patch phase-correlation measurements. Two rasters that already share an identical
transform, CRS and shape are still measured patch by patch, and content misregistered
by more than the caller's tolerance is reported as exceeding it.

Usage
-----
    python -m processing.registration \\
        --reference data/prepared/2026-03-04/before.tif \\
        --target   data/prepared/2026-09-19/after.tif  \\
        --out      data/processed/registration-diagnostic.json \\
        --max-shift-pixels 1.0 --max-patch-deviation-pixels 0.5

No threshold has a default that hides a decision: the CLI defaults are the values
recorded in :class:`Criteria`, and they are echoed into the output under ``criterion``
so a reviewer can see exactly which numbers produced the status.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
import scipy.fft
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds
from scipy import ndimage

__all__ = [
    "RegistrationError",
    "Criteria",
    "DIAGNOSTIC_SCHEMA_VERSION",
    "STATUS_ALIGNED",
    "STATUS_EXCEEDS_TOLERANCE",
    "STATUS_INCONSISTENT",
    "STATUS_NOT_EVALUABLE",
    "diagnose_arrays",
    "estimate_patch_shift",
    "run_registration_diagnostic",
    "main",
]

DIAGNOSTIC_SCHEMA_VERSION = 1
REFERENCE_CRS_EPSG = 4326

#: Side length in pixels of one correlation patch. Must stay even (separable Hann window).
PATCH_SIZE = 32
#: Step between candidate patch origins when tiling the common grid.
PATCH_STRIDE = 16
#: Minimum Euclidean distance, in pixels, between the centres of two selected patches.
MIN_PATCH_SEPARATION = 2 * PATCH_SIZE
#: Upper bound on selected patches, to bound cost on large overlaps.
MAX_PATCHES = 12
#: Upper bound on candidate patch windows examined, to bound cost on large overlaps.
MAX_CANDIDATES = 400
#: Radius, in pixels, around the winning peak that cannot hold a rival peak. Rivals are found
#: as local maxima, so this only has to exclude the winner's own immediate neighbourhood; a
#: broad but single peak is then not mistaken for an ambiguous one.
PEAK_EXCLUSION_RADIUS = 2
#: Maximum reference-grid size, to fail loudly instead of exhausting memory.
MAX_COMMON_CELLS = 40_000_000
#: Smallest accepted pixel size; anything smaller is a degenerate/identity geotransform.
MIN_PIXEL_SIZE = 1e-6
#: Relative dispersion below which a patch counts as having no texture at all.
DISPERSION_FLOOR = 1e-12

#: Reasons a candidate patch is not used, in the order they are tested.
REJECTION_REASONS = (
    "insufficient_valid",
    "flat",
    "ambiguous",
    "low_peak",
    "out_of_search_range",
    "degenerate_correlation",
)

STATUS_ALIGNED = "within_caller_tolerance"
STATUS_EXCEEDS_TOLERANCE = "exceeds_caller_tolerance"
STATUS_INCONSISTENT = "inconsistent_local_shifts"
STATUS_NOT_EVALUABLE = "not_evaluable"

ALGORITHM = (
    "Per patch: standardise both patches to zero mean and unit variance over their jointly "
    "valid samples, set samples that are not jointly valid to exactly zero so they contribute "
    "nothing, taper with a separable Hann window, then take the inverse real FFT of "
    "conj(FFT(reference)) * FFT(target) with the patch zero-padded to twice its size. The lag of "
    "the correlation peak is the translation that maps the reference onto the target, and the "
    "peak lag is refined to sub-pixel precision by a 3-point parabolic fit along each axis. No "
    "speckle filter is applied inside the patch: on the synthetic textured scenes used to develop "
    "this module a 3x3 box mean degraded sub-pixel accuracy from about 0.15 px to 0.5-2 px, so "
    "speckle suppression is left to the caller upstream."
)

#: Fixed structural requirements, reported so a reviewer sees they are not caller knobs.
STRUCTURAL_REQUIREMENTS = (
    "at least 2 accepted patches, covering at least 2 distinct patch-centre rows and 2 distinct "
    "patch-centre columns, so that one textured corner cannot stand in for a global shift; the "
    "caller's min_patches may raise the patch count further",
    f"selected patch centres are at least {MIN_PATCH_SEPARATION} px apart, so their shifts are "
    "measured from largely independent samples rather than overlapping windows",
    f"a patch whose shift exceeds {PATCH_SIZE // 2 - 1} px is reported as out_of_search_range "
    "rather than clamped, because that is the range the zero-padded correlation surface "
    "represents without wrapping onto a spurious shift",
)

LIMITATIONS = [
    "The fitted model is a global translation only. Rotation, scale, shear, terrain-driven "
    "relief displacement and any non-translational geolocation difference between the two dates "
    "are neither corrected nor separable from a translation here. When they are present the "
    "per-patch shifts disagree and the status is inconsistent_local_shifts; the rotation, "
    "dilation and shear read-outs are evidence of that, not a correction.",
    "Phase correlation reports the displacement of the dominant structure in the patch. It is "
    "not point matching or feature detection, and it does not identify what moved.",
    "Residual speckle is not filtered. Speckle lowers the correlation peak and can bias the "
    "sub-pixel refinement. The peak-dominance test and the agreement between independent patches "
    "are defences against a spurious peak, not a guarantee: a speckle-driven peak that happened to "
    "agree across several patches would not be detected by anything here.",
    "texture_cv is a dimensionless dispersion measure (std/|mean|). It cannot distinguish real "
    "structure from speckle and must not be read as an image-quality score.",
    "normalized_peak is a correlation coefficient of the measured patch pair, not a probability, "
    "confidence or accuracy. It says how strongly the two patches agree, nothing more.",
    "The measurement is of the rasters **as delivered**. Any resampling, warping, mosaicking or "
    "projection change already applied upstream is inside the measured shift and cannot be "
    "attributed to either acquisition.",
    "When the two grids differ, both rasters are interpolated onto a common grid before being "
    "measured. That interpolation smooths high-frequency content and can attenuate a real "
    "structural shift, so the reported shift belongs to the common grid, not to either source "
    "pixel grid.",
    "Two acquisitions constrain geometry only. They cannot establish whether any observed radar "
    "difference is forest loss, fire, flooding, agriculture or a processing artifact. No cause, "
    "probability or confidence is reported here.",
    "The status compares a measurement against the caller's own thresholds. It is not a verdict "
    "on whether the pair is scientifically usable, and it does not verify calibration, geocoding "
    "or terrain correction, which remain upstream attestations.",
]


class RegistrationError(RuntimeError):
    """Raised for any input, parameter, or processing condition that must abort."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RegistrationError(message)


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _jsonable(value: Any) -> Any:
    """Replace non-finite floats with None so the payload survives strict JSON."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


# --------------------------------------------------------------------------- #
# caller-supplied criterion
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Criteria:
    """Accept/reject thresholds. Every one of them is supplied by the caller.

    The defaults exist so the CLI has flags; they do not decide the scientific question on
    the caller's behalf, and all of them are echoed into the output.
    """

    #: Largest accepted magnitude of the fitted shift, in pixels.
    max_shift_pixels: float = 1.0
    #: Largest accepted distance of any accepted patch estimate from the centre shift.
    max_patch_deviation_pixels: float = 0.5
    #: Smallest accepted ratio of best correlation peak to best rival peak.
    min_patch_dominance: float = 1.5
    #: Smallest accepted normalized correlation peak.
    min_patch_peak: float = 0.15
    #: Smallest accepted dimensionless patch dispersion (std/|mean|).
    min_texture_cv: float = 0.05
    #: Smallest accepted fraction of a patch where both rasters are valid.
    min_valid_fraction: float = 0.6
    #: Smallest accepted number of usable patches.
    min_patches: int = 4
    #: Largest shift, in pixels, a patch may report before it is out of search range.
    max_shift_search_pixels: float = 8.0

    def validate(self) -> "Criteria":
        for name in (
            "max_shift_pixels",
            "max_patch_deviation_pixels",
            "min_patch_dominance",
            "min_patch_peak",
            "min_texture_cv",
            "min_valid_fraction",
            "max_shift_search_pixels",
        ):
            value = _finite(getattr(self, name))
            flag = "--" + name.replace("_", "-")
            _require(value is not None, f"{flag} must be a finite number, got {getattr(self, name)!r}")
            _require(value > 0.0, f"{flag} must be strictly positive, got {value!r}")
        _require(
            isinstance(self.min_patches, int) and not isinstance(self.min_patches, bool) and self.min_patches >= 2,
            f"--min-patches must be an integer >= 2, got {self.min_patches!r}",
        )
        _require(
            self.min_valid_fraction <= 1.0,
            f"--min-valid-fraction must be <= 1.0, got {self.min_valid_fraction!r}",
        )
        limit = PATCH_SIZE / 2.0 - 1.0
        _require(
            self.max_shift_search_pixels <= limit,
            f"--max-shift-search-pixels must be <= {limit:g} px for a {PATCH_SIZE} px patch, got "
            f"{self.max_shift_search_pixels!r}; a larger bound would let the zero-padded "
            "correlation surface wrap around and report a spurious shift",
        )
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {key: _jsonable(value) for key, value in asdict(self).items()}


# --------------------------------------------------------------------------- #
# per-patch phase correlation
# --------------------------------------------------------------------------- #
def _parabolic_offset(surface: np.ndarray, row: int, column: int, axis: int) -> float:
    """Sub-pixel peak offset from a 3-point parabolic fit along one axis, clipped to +/-0.5."""
    if axis == 0:
        if not 0 < row < surface.shape[0] - 1:
            return 0.0
        before, centre, after = surface[row - 1, column], surface[row, column], surface[row + 1, column]
    else:
        if not 0 < column < surface.shape[1] - 1:
            return 0.0
        before, centre, after = surface[row, column - 1], surface[row, column], surface[row, column + 1]
    denominator = before - 2.0 * centre + after
    if denominator == 0.0:
        return 0.0
    offset = 0.5 * (before - after) / denominator
    return float(min(0.5, max(-0.5, offset)))


def _standardise(patch: np.ndarray, valid: np.ndarray) -> Optional[np.ndarray]:
    """Zero-mean, unit-variance patch over `valid` samples, invalid samples set to exactly 0.

    Standardising rather than dividing by the mean keeps the correlation invariant to any
    radiometric offset or scale between the dates, so a globally brighter or darker
    acquisition cannot by itself create an apparent shift. Invalid samples become exactly 0,
    so they contribute nothing to the correlation instead of being interpolated.
    """
    if not bool(valid.any()):
        return None
    values = patch[valid]
    deviation = float(values.std())
    # A constant array has a standard deviation of order 1e-17 rather than exactly 0, so a
    # relative floor is needed for "no dispersion at all" to mean what it says.
    floor = abs(float(values.mean())) * DISPERSION_FLOOR
    if not math.isfinite(deviation) or deviation <= floor:
        return None
    return np.where(valid, (patch - float(values.mean())) / deviation, 0.0)


def estimate_patch_shift(
    reference: np.ndarray,
    target: np.ndarray,
    jointly_valid: np.ndarray,
    *,
    exclusion_radius: int = PEAK_EXCLUSION_RADIUS,
) -> Dict[str, Any]:
    """Sub-pixel translation of one patch pair, by phase correlation.

    Parameters
    ----------
    reference, target:
        Equal-shaped 2-D float arrays; NaN marks a missing sample.
    jointly_valid:
        Boolean array, True where both patches have a usable sample.
    exclusion_radius:
        Pixels around the winning peak that cannot hold a rival peak. Rivals are local maxima
        of the correlation surface, so this need only exclude the winner's neighbourhood.

    Returns
    -------
    dict
        ``shift_x``/``shift_y`` in pixels, where ``np.roll(reference, (shift_y, shift_x))``
        matches ``target``; ``normalized_peak`` and ``rival_peak`` correlation
        coefficients; their ratio ``dominance``; the integer peak lag; and the fraction of
        the patch that is jointly valid.

    Raises
    ------
    RegistrationError
        If the patch pair has no jointly valid samples, or one patch has no dispersion at all.
    """
    _require(
        reference.shape == target.shape == jointly_valid.shape,
        f"patch shapes disagree: reference {reference.shape}, target {target.shape}, "
        f"jointly_valid {jointly_valid.shape}",
    )
    _require(reference.ndim == 2, f"patches must be 2-D, got shape {reference.shape}")
    reference = np.asarray(reference, dtype="float64")
    target = np.asarray(target, dtype="float64")
    prepared = _standardise(np.where(jointly_valid, reference, np.nan), jointly_valid)
    other = _standardise(np.where(jointly_valid, target, np.nan), jointly_valid)
    _require(
        prepared is not None and other is not None,
        "patch pair has no jointly valid samples, or one patch has zero dispersion; a "
        "translation cannot be measured from it",
    )
    height, width = reference.shape
    window = np.hanning(height)[:, None] * np.hanning(width)[None, :]
    tapered_reference = prepared * window
    tapered_target = other * window
    padded_height = int(scipy.fft.next_fast_len(2 * height))
    padded_width = int(scipy.fft.next_fast_len(2 * width))
    spectrum_reference = scipy.fft.rfft2(tapered_reference, s=(padded_height, padded_width))
    spectrum_target = scipy.fft.rfft2(tapered_target, s=(padded_height, padded_width))
    correlation = scipy.fft.irfft2(
        np.conj(spectrum_reference) * spectrum_target, s=(padded_height, padded_width)
    )
    # Lags in [-(size/2), size/2) occupy this window of the shifted surface; taking the
    # centred window is what makes lag 0 and both signs representable without wraparound.
    start_row = height - height // 2
    start_column = width - width // 2
    surface = scipy.fft.fftshift(correlation)[
        start_row : start_row + height, start_column : start_column + width
    ]
    norm = float(np.sqrt((tapered_reference**2).sum() * (tapered_target**2).sum()))
    _require(norm > 0.0, "patch pair has no correlation energy after tapering")
    surface = surface / norm
    peak_row, peak_column = np.unravel_index(int(np.argmax(surface)), surface.shape)
    normalized_peak = float(surface[peak_row, peak_column])
    row_offsets = np.arange(height)[:, None] - peak_row
    column_offsets = np.arange(width)[None, :] - peak_column
    # A rival must be a competing *candidate shift*, i.e. a local maximum of the correlation
    # surface, not merely a high value on the flank of the winning peak. Comparing against
    # flank values would report every broad but unambiguous peak as ambiguous.
    local_maximum = surface >= ndimage.maximum_filter(surface, size=3, mode="nearest")
    rival = np.where(np.hypot(row_offsets, column_offsets) > exclusion_radius, local_maximum, False)
    rival_peak = float(np.max(surface[rival])) if bool(rival.any()) else None
    shift_y = (peak_row - height // 2) + _parabolic_offset(surface, peak_row, peak_column, 0)
    shift_x = (peak_column - width // 2) + _parabolic_offset(surface, peak_row, peak_column, 1)
    return {
        "shift_x": float(shift_x),
        "shift_y": float(shift_y),
        "peak_row": int(peak_row - height // 2),
        "peak_column": int(peak_column - width // 2),
        "normalized_peak": normalized_peak,
        "rival_peak": rival_peak,
        "dominance": float(normalized_peak / rival_peak) if rival_peak is not None and rival_peak > 0.0 else None,
        "valid_fraction": float(np.count_nonzero(jointly_valid)) / float(jointly_valid.size),
    }


# --------------------------------------------------------------------------- #
# patch selection
# --------------------------------------------------------------------------- #
def _candidate_origins(height: int, width: int) -> List[Tuple[int, int]]:
    """Deterministic tiling of the overlap into candidate patch origins, cost-bounded."""
    if height < PATCH_SIZE or width < PATCH_SIZE:
        return []
    rows = list(range(0, height - PATCH_SIZE + 1, PATCH_STRIDE))
    columns = list(range(0, width - PATCH_SIZE + 1, PATCH_STRIDE))
    windows = len(rows) * len(columns)
    if windows > MAX_CANDIDATES:
        thinning = math.ceil(math.sqrt(windows / MAX_CANDIDATES))
        rows = rows[::thinning]
        columns = columns[::thinning]
    return [(row, column) for row in rows for column in columns]


def _patch_dispersion(
    reference: np.ndarray, target: np.ndarray, jointly_valid: np.ndarray, row: int, column: int
) -> Tuple[float, float, float]:
    """(valid_fraction, dispersion std, dimensionless dispersion std/|mean|) for one patch.

    The dispersion is the *smaller* of the two rasters' dispersions over the jointly valid
    samples, not a figure pooled across both. Pooling would let a purely radiometric
    difference between the dates look like structure and would carry a flat patch through
    the texture test. A patch pair is only as correlatable as its less textured member.
    """
    window = (slice(row, row + PATCH_SIZE), slice(column, column + PATCH_SIZE))
    patch_valid = jointly_valid[window]
    valid_fraction = float(np.count_nonzero(patch_valid)) / float(patch_valid.size)
    if valid_fraction == 0.0:
        return valid_fraction, 0.0, 0.0
    relative_values = []
    dispersions = []
    for array in (reference, target):
        values = array[window][patch_valid]
        dispersion = float(values.std())
        mean_value = float(values.mean())
        dispersions.append(dispersion)
        relative_values.append(dispersion / abs(mean_value) if mean_value != 0.0 else math.inf)
    return valid_fraction, float(min(dispersions)), float(min(relative_values))


def _select_patches(
    reference: np.ndarray,
    target: np.ndarray,
    jointly_valid: np.ndarray,
    criteria: Criteria,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Score candidates by dispersion, then keep the most textured and the most spread out.

    A correlation peak only exists where there is dispersion, so dispersion is the
    candidate score. Selection is deterministic (score descending, then row, then column)
    and enforces a minimum centre separation, so the accepted patches cover the overlap
    instead of clustering on whichever corner happens to be busiest.
    """
    height, width = reference.shape
    rejections = {reason: 0 for reason in REJECTION_REASONS}
    scored: List[Tuple[float, int, int]] = []
    for row, column in _candidate_origins(height, width):
        valid_fraction, dispersion, _relative = _patch_dispersion(
            reference, target, jointly_valid, row, column
        )
        if valid_fraction < criteria.min_valid_fraction:
            rejections["insufficient_valid"] += 1
            continue
        scored.append((dispersion, row, column))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected: List[Dict[str, Any]] = []
    for dispersion, row, column in scored:
        if len(selected) >= MAX_PATCHES:
            break
        centre_row = row + PATCH_SIZE / 2.0
        centre_column = column + PATCH_SIZE / 2.0
        if any(
            math.hypot(centre_row - other["centre_row"], centre_column - other["centre_column"])
            < MIN_PATCH_SEPARATION
            for other in selected
        ):
            continue
        selected.append(
            {
                "row": row,
                "column": column,
                "centre_row": centre_row,
                "centre_column": centre_column,
                "dispersion": dispersion,
            }
        )
    return selected, rejections


# --------------------------------------------------------------------------- #
# geometry summaries
# --------------------------------------------------------------------------- #
def _centre_shift(accepted: Sequence[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    if not accepted:
        return None, None
    return (
        float(np.median([record["shift_pixels"]["x"] for record in accepted])),
        float(np.median([record["shift_pixels"]["y"] for record in accepted])),
    )


def _summarise_shift_field(accepted: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Centre shift, per-patch spread, and a rotation/scale read-out of the shift field.

    A least-squares plane is fitted to the per-patch shifts. A pure translation leaves
    zero derivative terms; non-zero ones are reported as evidence that the two rasters
    differ by more than a translation. They are never applied as a correction.
    """
    summary: Dict[str, Any] = {
        "shift_x": None,
        "shift_y": None,
        "shift_magnitude_pixels": None,
        "max_patch_deviation_pixels": None,
        "median_absolute_deviation_pixels": None,
        "rotation_equivalent_rad": None,
        "dilation_equivalent": None,
        "shear_equivalent": None,
    }
    shift_x, shift_y = _centre_shift(accepted)
    if shift_x is None:
        return summary
    deviations = [
        math.hypot(record["shift_pixels"]["x"] - shift_x, record["shift_pixels"]["y"] - shift_y)
        for record in accepted
    ]
    summary.update(
        {
            "shift_x": shift_x,
            "shift_y": shift_y,
            "shift_magnitude_pixels": math.hypot(shift_x, shift_y),
            "max_patch_deviation_pixels": float(max(deviations)),
            "median_absolute_deviation_pixels": float(np.median(deviations)),
        }
    )
    if len(accepted) >= 3:
        design = np.column_stack(
            [
                np.ones(len(accepted)),
                np.array([record["centre_row_px"] for record in accepted]),
                np.array([record["centre_column_px"] for record in accepted]),
            ]
        )
        observed = np.column_stack(
            [
                np.array([record["shift_pixels"]["x"] for record in accepted]),
                np.array([record["shift_pixels"]["y"] for record in accepted]),
            ]
        )
        try:
            coefficients = np.linalg.lstsq(design, observed, rcond=None)[0]
        except np.linalg.LinAlgError:  # pragma: no cover - numerically degenerate input
            return summary
        d_row_x, d_column_x = float(coefficients[1, 0]), float(coefficients[2, 0])
        d_row_y, d_column_y = float(coefficients[1, 1]), float(coefficients[2, 1])
        summary["rotation_equivalent_rad"] = 0.5 * (d_column_x - d_row_y)
        summary["dilation_equivalent"] = 1.0 + 0.5 * (d_column_x + d_row_y)
        summary["shear_equivalent"] = 0.5 * (d_column_x + d_row_y)
    return summary


def _shift_in_metres(
    shift_x: Optional[float], shift_y: Optional[float], georeference: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Optional[float]]]:
    """Map the pixel shift through the affine transform, then convert CRS units to metres.

    A positive row shift is a displacement toward larger map y only when the transform says
    so, so the transform coefficients are used rather than a north-up assumption.
    """
    if shift_x is None or shift_y is None or not georeference:
        return None
    transform = georeference.get("transform")
    units_to_metres = _finite(georeference.get("units_to_metres"))
    if transform is None or units_to_metres is None:
        return None
    a, b, _c, d, e, _f = (float(value) for value in transform)
    return {
        "x": (a * shift_x + b * shift_y) * units_to_metres,
        "y": (d * shift_x + e * shift_y) * units_to_metres,
    }


# --------------------------------------------------------------------------- #
# array-level diagnostic
# --------------------------------------------------------------------------- #
def diagnose_arrays(
    reference: np.ndarray,
    target: np.ndarray,
    *,
    criteria: Criteria,
    georeference: Optional[Dict[str, Any]] = None,
    upstream: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Measure the residual translation between two rasters that already share one grid.

    The returned status is derived only from the per-patch phase-correlation measurements
    and the caller's ``criteria``. Nothing about the input grid, the caller's metadata or the
    file names can produce a passing status.

    Parameters
    ----------
    reference, target:
        Equal-shaped 2-D float arrays on one common grid; NaN marks a missing sample.
    criteria:
        The caller's accept/reject thresholds.
    georeference:
        Optional ``{"transform": (a, b, c, d, e, f), "units_to_metres": float}`` for the
        common grid. Without a convertible CRS unit, the shift is reported in pixels only.
    upstream:
        Optional record of the delivered grids, copied into the output verbatim so the
        geometry this measurement rests on stays visible in the artifact.
    """
    _require(
        reference.shape == target.shape,
        f"reference and target must share one grid, got {reference.shape} and {target.shape}",
    )
    _require(reference.ndim == 2, f"rasters must be 2-D, got shape {reference.shape}")
    criteria.validate()
    reference = np.asarray(reference, dtype="float64")
    target = np.asarray(target, dtype="float64")
    jointly_valid = np.isfinite(reference) & np.isfinite(target)
    _require(
        bool(jointly_valid.any()),
        "no pixel has valid support in both rasters; nothing can be compared",
    )

    height, width = reference.shape
    selected, rejections = _select_patches(reference, target, jointly_valid, criteria)
    evidence: List[Dict[str, Any]] = []
    accepted: List[Dict[str, Any]] = []
    for candidate in selected:
        row, column = candidate["row"], candidate["column"]
        window = (slice(row, row + PATCH_SIZE), slice(column, column + PATCH_SIZE))
        patch_valid = jointly_valid[window]
        valid_fraction, dispersion, relative = _patch_dispersion(
            reference, target, jointly_valid, row, column
        )
        record: Dict[str, Any] = {
            "patch_index": len(evidence),
            "row": row,
            "column": column,
            "centre_row_px": candidate["centre_row"],
            "centre_column_px": candidate["centre_column"],
            "valid_fraction": valid_fraction,
            "texture_cv": relative,
            "texture_std": dispersion,
        }
        if georeference is not None and georeference.get("transform") is not None:
            a, b, _c, d, e, _f = (float(value) for value in georeference["transform"])
            record["centre_map_x"] = a * candidate["centre_column"] + b * candidate["centre_row"]
            record["centre_map_y"] = d * candidate["centre_column"] + e * candidate["centre_row"]
        estimate: Optional[Dict[str, Any]] = None
        reason: Optional[str] = None
        if valid_fraction < criteria.min_valid_fraction:
            reason = "insufficient_valid"
        elif not math.isfinite(relative) or relative < criteria.min_texture_cv:
            reason = "flat"
        else:
            try:
                estimate = estimate_patch_shift(
                    reference[window], target[window], patch_valid, exclusion_radius=PEAK_EXCLUSION_RADIUS
                )
            except RegistrationError:
                reason = "degenerate_correlation"
            if estimate is not None:
                dominance = estimate["dominance"]
                if dominance is not None and dominance < criteria.min_patch_dominance:
                    reason = "ambiguous"
                elif estimate["normalized_peak"] < criteria.min_patch_peak:
                    reason = "low_peak"
                elif max(abs(estimate["shift_x"]), abs(estimate["shift_y"])) > criteria.max_shift_search_pixels:
                    reason = "out_of_search_range"
        if estimate is not None:
            record.update(
                {
                    "shift_pixels": {"x": estimate["shift_x"], "y": estimate["shift_y"]},
                    "normalized_peak": estimate["normalized_peak"],
                    "rival_peak": estimate["rival_peak"],
                    "peak_dominance": estimate["dominance"],
                    "integer_peak_lag": {"x": estimate["peak_column"], "y": estimate["peak_row"]},
                }
            )
        record.update({"accepted": reason is None, "rejection_reason": reason})
        if reason is None:
            accepted.append(record)
        else:
            rejections[reason] += 1
        evidence.append(record)

    summary = _summarise_shift_field(accepted)
    for record in accepted:
        record["deviation_pixels"] = math.hypot(
            record["shift_pixels"]["x"] - summary["shift_x"],
            record["shift_pixels"]["y"] - summary["shift_y"],
        )

    distinct_rows = len({int(record["centre_row_px"]) for record in accepted})
    distinct_columns = len({int(record["centre_column_px"]) for record in accepted})
    status_reasons: List[str] = []
    if not _candidate_origins(height, width):
        status = STATUS_NOT_EVALUABLE
        status_reasons.append(
            f"the common grid is {width}x{height} px, smaller than the {PATCH_SIZE} px patch the "
            "correlator needs; widen the overlap window upstream"
        )
    elif len(accepted) < criteria.min_patches:
        status = STATUS_NOT_EVALUABLE
        status_reasons.append(
            f"only {len(accepted)} of {len(evidence)} selected patches yielded a usable "
            f"translation, and the caller's criterion requires at least {criteria.min_patches}; "
            f"rejections: {json.dumps(_jsonable(rejections), sort_keys=True)}"
        )
    elif distinct_rows < 2 or distinct_columns < 2:
        status = STATUS_NOT_EVALUABLE
        status_reasons.append(
            f"the {len(accepted)} accepted patches cover {distinct_rows} distinct centre row(s) "
            f"and {distinct_columns} distinct centre column(s); one textured area cannot support "
            "a claim about global alignment"
        )
    elif summary["max_patch_deviation_pixels"] > criteria.max_patch_deviation_pixels:
        status = STATUS_INCONSISTENT
        status_reasons.append(
            f"per-patch shifts disagree by up to {summary['max_patch_deviation_pixels']:.3f} px, "
            f"above the caller's {criteria.max_patch_deviation_pixels:.3f} px; one global "
            "translation does not describe these two rasters"
        )
    elif summary["shift_magnitude_pixels"] > criteria.max_shift_pixels:
        status = STATUS_EXCEEDS_TOLERANCE
        status_reasons.append(
            f"fitted shift is {summary['shift_magnitude_pixels']:.3f} px "
            f"({summary['shift_x']:+.3f} px in x, {summary['shift_y']:+.3f} px in y), above the "
            f"caller's {criteria.max_shift_pixels:.3f} px"
        )
    else:
        status = STATUS_ALIGNED
        status_reasons.append(
            f"fitted shift is {summary['shift_magnitude_pixels']:.3f} px and the {len(accepted)} "
            f"accepted patches agree to within {summary['max_patch_deviation_pixels']:.3f} px, "
            f"inside the caller's {criteria.max_shift_pixels:.3f} px and "
            f"{criteria.max_patch_deviation_pixels:.3f} px limits"
        )

    measured = {
        "model": "global_translation",
        "patches_selected": len(evidence),
        "patches_accepted": len(accepted),
        "accepted_patch_rows": sorted({int(record["centre_row_px"]) for record in accepted}),
        "accepted_patch_columns": sorted({int(record["centre_column_px"]) for record in accepted}),
        "shift_pixels": None
        if summary["shift_x"] is None
        else {"x": summary["shift_x"], "y": summary["shift_y"]},
        "shift_magnitude_pixels": summary["shift_magnitude_pixels"],
        "shift_metres": _shift_in_metres(summary["shift_x"], summary["shift_y"], georeference),
        "shift_metres_note": (
            None
            if _shift_in_metres(summary["shift_x"], summary["shift_y"], georeference) is not None
            else "no metric shift is reported: the grid has no affine transform with convertible "
            "CRS units, so the pixel shift must not be converted with an assumed pixel size"
        ),
        "max_patch_deviation_pixels": summary["max_patch_deviation_pixels"],
        "median_absolute_deviation_pixels": summary["median_absolute_deviation_pixels"],
        "rotation_equivalent_rad": summary["rotation_equivalent_rad"],
        "dilation_equivalent": summary["dilation_equivalent"],
        "shear_equivalent": summary["shear_equivalent"],
        "shift_field_note": (
            "rotation_equivalent_rad, dilation_equivalent and shear_equivalent describe a least-"
            "squares plane through the per-patch shifts. They are evidence that the two rasters "
            "differ by more than a translation; they are not a correction and are not applied."
        ),
    }

    payload: Dict[str, Any] = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "diagnostic": "residual_coregistration_diagnostic",
        "status": status,
        "status_reasons": status_reasons,
        "measured": measured,
        "patch_evidence": evidence,
        "patch_rejections": rejections,
        "patch_rejections_note": (
            "Candidate patch windows that were not used, by the first reason they failed. A "
            "window dropped because a better-scoring window was already selected nearby is not "
            "counted here."
        ),
        "criterion": criteria.as_dict(),
        "criterion_source": (
            "supplied by the caller; this diagnostic does not choose an accept/reject threshold "
            "and does not read one from a manifest"
        ),
        "structural_requirements": list(STRUCTURAL_REQUIREMENTS),
        "method": {
            "model": "global_translation",
            "algorithm": ALGORITHM,
            "patch_size_pixels": PATCH_SIZE,
            "patch_stride_pixels": PATCH_STRIDE,
            "min_patch_separation_pixels": MIN_PATCH_SEPARATION,
            "max_patches": MAX_PATCHES,
            "max_candidate_windows": MAX_CANDIDATES,
            "peak_exclusion_radius_pixels": PEAK_EXCLUSION_RADIUS,
            "rival_peak_meaning": (
                "height of the highest other local maximum of the correlation surface, i.e. the "
                "best competing candidate shift. peak_dominance is the winning peak divided by "
                "it, and is null when the winning peak is the only local maximum. A broad but "
                "single peak is therefore not reported as ambiguous."
            ),
            "centre_estimator": "componentwise median of the accepted per-patch shifts",
            "speckle_filter": "none inside the patch; applied upstream by the caller if at all",
            "shift_convention": (
                "shift_pixels.x is the number of pixels the target must be moved in +column to "
                "sit on the reference, so np.roll(reference, (shift_y, shift_x)) matches the "
                "target. The metric shift is derived from the grid's affine transform, not from a "
                "north-up assumption."
            ),
            "normalized_peak_meaning": (
                "correlation coefficient of the measured patch pair after standardisation; not a "
                "probability, a confidence, or an accuracy"
            ),
            "rejection_reason_order": list(REJECTION_REASONS),
        },
        "assertions": {
            "manifest_registration_status_set": False,
            "manifest_written_or_modified": False,
            "bundle_published": False,
            "correction_applied_to_inputs": False,
            "probability_or_confidence_reported": False,
            "cause_attributed": False,
            "status_derived_from_grid_reprojection": False,
        },
        "limitations": list(LIMITATIONS),
    }
    if upstream is not None:
        payload["upstream"] = upstream
    if georeference is not None:
        payload["georeference"] = {
            "transform": [_jsonable(value) for value in georeference.get("transform", ())],
            "units_to_metres": _jsonable(georeference.get("units_to_metres")),
        }
    payload = _jsonable(payload)
    payload["manifest_registration_diagnostic_text"] = _diagnostic_text(payload)
    return payload


def _diagnostic_text(payload: Dict[str, Any]) -> str:
    """One sentence a human may consider for a manifest field. Never written to one."""
    measured = payload["measured"]
    magnitude = measured["shift_magnitude_pixels"]
    spread = measured["max_patch_deviation_pixels"]
    accepted = measured["patches_accepted"]
    if magnitude is None or spread is None:
        return (
            f"Residual coregistration diagnostic: status {payload['status']}. No global "
            "translation could be fitted from the jointly valid, textured patches of the overlap, "
            "so registration is not evaluable by this method. This is a measurement tool only: it "
            "did not verify registration, correct it, or set any manifest field."
        )
    shift = measured["shift_pixels"]
    return (
        f"Residual coregistration diagnostic: status {payload['status']}. Phase correlation over "
        f"{accepted} independent jointly valid textured patches of the overlapping footprint gives "
        f"a median global translation of {shift['x']:+.3f} px in x and {shift['y']:+.3f} px in y "
        f"({magnitude:.3f} px magnitude), with the accepted patches agreeing to within "
        f"{spread:.3f} px. The estimate covers translation only; rotation, scale and relief "
        "displacement are not modelled or corrected. This diagnostic measured the rasters as "
        "delivered; it did not verify registration, correct it, or alter any manifest."
    )


# --------------------------------------------------------------------------- #
# prepared-raster input
# --------------------------------------------------------------------------- #
def _units_to_metres(crs) -> Optional[float]:
    """Metres per CRS axis unit, or None when the units are not a length that converts."""
    try:
        name, factor = crs.linear_units_factor
    except Exception:  # pragma: no cover - rasterio version dependent
        return None
    if not isinstance(name, str):
        return None
    if "metre" not in name.lower() and "meter" not in name.lower():
        return None
    value = _finite(factor)
    return None if value is None or value <= 0.0 else value


def read_prepared_raster(path: str, label: str) -> Dict[str, Any]:
    """Open one prepared raster and record exactly the geometry it ships with."""
    _require(os.path.isfile(path), f"{label}: raster does not exist: {path}")
    try:
        dataset = rasterio.open(path)
    except Exception as exc:
        raise RegistrationError(f"{label}: cannot open prepared raster {path}: {exc}") from exc
    with dataset as src:
        _require(src.count >= 1, f"{label}: prepared raster has no bands: {path}")
        _require(
            src.width > 0 and src.height > 0,
            f"{label}: prepared raster has zero extent ({src.width}x{src.height}): {path}",
        )
        _require(
            src.crs is not None,
            f"{label}: prepared raster has no CRS; a real projected or geographic CRS is required "
            f"(identity or assumed CRSs are rejected): {path}",
        )
        transform = src.transform
        _require(
            math.isfinite(transform.a)
            and math.isfinite(transform.e)
            and abs(transform.a) > MIN_PIXEL_SIZE
            and abs(transform.e) > MIN_PIXEL_SIZE,
            f"{label}: prepared raster has a missing or degenerate transform "
            f"(identity/undefined geotransform is rejected): {path}",
        )
        _require(
            "complex" not in (src.dtypes[0] or "").lower(),
            f"{label}: complex rasters are rejected; this diagnostic correlates real "
            f"backscatter, got dtype {src.dtypes[0]}: {path}",
        )
        data = np.asarray(src.read(1, masked=True).filled(np.nan), dtype="float64")
        nodata = src.nodata
        _require(
            bool(np.isfinite(data).any()),
            f"{label}: prepared raster has no valid samples (all nodata/NaN): {path}",
        )
        return {
            "label": label,
            "path": path,
            "data": data,
            "crs": src.crs,
            "transform": transform,
            "width": src.width,
            "height": src.height,
            "nodata": None if nodata is None or not math.isfinite(float(nodata)) else float(nodata),
            "units_to_metres": _units_to_metres(src.crs),
            "crs_units": src.crs.linear_units,
        }


def _wgs84_bounds(info: Dict[str, Any]) -> Tuple[float, float, float, float]:
    return transform_bounds(
        info["crs"],
        REFERENCE_CRS_EPSG,
        *rasterio.transform.array_bounds(info["height"], info["width"], info["transform"]),
    )


def _is_axis_aligned(transform) -> bool:
    return math.isfinite(transform.b) and math.isfinite(transform.d) and transform.b == 0.0 and transform.d == 0.0


def build_common_grid(reference: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    """Overlap of both footprints, on the reference pixel size, aligned to the reference grid.

    When both rasters share a CRS and both are axis-aligned, the overlap is intersected in
    their own projected coordinates, which is exact. Otherwise the overlap is computed from
    the WGS84 bounding boxes, whose densified edges slightly over-estimate the footprint; the
    grid is then larger than the true overlap by at most a fraction of a pixel on each side
    and `grid_path` in the output says which route was taken.
    """
    res_x = abs(reference["transform"].a)
    res_y = abs(reference["transform"].e)
    shared_crs = reference["crs"] == target["crs"]
    if shared_crs and _is_axis_aligned(reference["transform"]) and _is_axis_aligned(target["transform"]):
        west_reference, south_reference, east_reference, north_reference = _native_bounds(reference)
        west_target, south_target, east_target, north_target = _native_bounds(target)
        crs = reference["crs"]
        grid_path = "native_crs_intersection"
    else:
        west_reference, south_reference, east_reference, north_reference = _wgs84_bounds(reference)
        west_target, south_target, east_target, north_target = _wgs84_bounds(target)
        crs = reference["crs"]
        west, south = max(west_reference, west_target), max(south_reference, south_target)
        east, north = min(east_reference, east_target), min(north_reference, north_target)
        west, south, east, north = transform_bounds(
            REFERENCE_CRS_EPSG, crs, west, south, east, north, densify_pts=21
        )
        grid_path = "wgs84_densified_bounds"
    if grid_path == "native_crs_intersection":
        west = max(west_reference, west_target)
        south = max(south_reference, south_target)
        east = min(east_reference, east_target)
        north = min(north_reference, north_target)
    _require(
        west < east and south < north,
        "prepared rasters do not overlap on the map; no common reference grid exists "
        f"(reference bbox={[west_reference, south_reference, east_reference, north_reference]}, "
        f"target bbox={[west_target, south_target, east_target, north_target]})",
    )
    left = math.floor(west / res_x) * res_x
    right = math.ceil(east / res_x) * res_x
    bottom = math.floor(south / res_y) * res_y
    top = math.ceil(north / res_y) * res_y
    width = int(round((right - left) / res_x))
    height = int(round((top - bottom) / res_y))
    _require(
        width > 0 and height > 0,
        "common reference grid collapsed to zero size; the footprints share no usable pixels",
    )
    _require(
        width * height <= MAX_COMMON_CELLS,
        f"common reference grid is too large ({width}x{height}); narrow the overlap window "
        "upstream rather than exhausting memory here",
    )
    return {
        "crs": crs,
        "transform": transform_from_bounds(left, bottom, right, top, width, height),
        "width": width,
        "height": height,
        "resolution": (res_x, res_y),
        "bounds_wgs84": (west, south, east, north),
        "grid_path": grid_path,
    }


def _native_bounds(info: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """(west, south, east, north) of an axis-aligned raster, in its own CRS."""
    left, top = info["transform"].c, info["transform"].f
    right = left + info["width"] * info["transform"].a
    bottom = top + info["height"] * info["transform"].e
    return (min(left, right), min(bottom, top), max(left, right), max(bottom, top))


#: Slack, in metres, allowed when comparing a delivered transform with the derived one. The
#: common grid is built by round-tripping the overlap through WGS84, so exact float equality
#: would report a re-projection of an already co-gridded pair as a difference.
GRID_TOLERANCE_M = 1e-6


def _on_common_grid(info: Dict[str, Any], grid: Dict[str, Any]) -> bool:
    if info["crs"] != grid["crs"] or info["width"] != grid["width"] or info["height"] != grid["height"]:
        return False
    delivered = tuple(float(value) for value in tuple(info["transform"])[:6])
    derived = tuple(float(value) for value in tuple(grid["transform"])[:6])
    return all(
        math.isclose(first, second, rel_tol=0.0, abs_tol=GRID_TOLERANCE_M)
        for first, second in zip(delivered, derived)
    )


def warp_to_grid(info: Dict[str, Any], grid: Dict[str, Any]) -> np.ndarray:
    """Reproject + crop one prepared raster onto the common grid (grid alignment only).

    The warp is a measurement convenience, not evidence of alignment. Values are resampled
    bilinearly and the source validity mask with nearest-neighbour resampling, and the mask is
    reapplied, so bilinear interpolation cannot invent an observation at a nodata pixel.
    """
    destination = np.full((grid["height"], grid["width"]), np.nan, dtype="float64")
    validity = np.full((grid["height"], grid["width"]), np.nan, dtype="float64")
    with rasterio.open(info["path"]) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=info["nodata"],
            dst_transform=grid["transform"],
            dst_crs=grid["crs"],
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
            num_threads=1,
        )
        source_valid = (src.read_masks(1) > 0).astype("float64")
        reproject(
            source=source_valid,
            destination=validity,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=0.0,
            dst_transform=grid["transform"],
            dst_crs=grid["crs"],
            dst_nodata=np.nan,
            resampling=Resampling.nearest,
            num_threads=1,
        )
    if np.isfinite(validity).any():
        destination = np.where(validity > 0.5, destination, np.nan)
    return destination


# --------------------------------------------------------------------------- #
# file-level diagnostic
# --------------------------------------------------------------------------- #
def run_registration_diagnostic(
    reference_path: str,
    target_path: str,
    out_path: str,
    criteria: Criteria,
) -> Dict[str, Any]:
    """Measure two prepared rasters, then write exactly one JSON diagnostic at `out_path`."""
    criteria.validate()
    reference = read_prepared_raster(reference_path, "reference raster")
    target = read_prepared_raster(target_path, "target raster")
    grid = build_common_grid(reference, target)
    already_common = _on_common_grid(reference, grid) and _on_common_grid(target, grid)
    reference_grid = reference["data"] if already_common else warp_to_grid(reference, grid)
    target_grid = target["data"] if already_common else warp_to_grid(target, grid)
    # A non-metric CRS is not fatal: the pixel shift is still measured, and the payload then
    # says explicitly that no metric shift could be derived.
    units_to_metres = reference["units_to_metres"]
    georeference: Optional[Dict[str, Any]] = {
        "transform": tuple(float(value) for value in tuple(grid["transform"])[:6]),
        "units_to_metres": units_to_metres,
    }
    payload = diagnose_arrays(
        reference_grid,
        target_grid,
        criteria=criteria,
        georeference=georeference,
        upstream={
            "note": (
                "Geometry exactly as delivered by the prepared rasters. This diagnostic measured "
                "it; it did not correct it, and having reprojected onto a common grid is not "
                "evidence of alignment."
            ),
            "reference": _upstream_record(reference),
            "target": _upstream_record(target),
            "crs_units_comparable": reference["crs_units"] == target["crs_units"],
            "crs_identical": reference["crs"] == target["crs"],
            "common_grid": {
                "crs": str(grid["crs"]),
                "transform": [float(value) for value in tuple(grid["transform"])[:6]],
                "width": grid["width"],
                "height": grid["height"],
                "resolution": [float(value) for value in grid["resolution"]],
                "bounds_wgs84": [float(value) for value in grid["bounds_wgs84"]],
                "identical_to_delivered_grids": bool(already_common),
                "reprojected": not already_common,
                "grid_path": grid["grid_path"],
                "axis_aligned_inputs": bool(
                    _is_axis_aligned(reference["transform"]) and _is_axis_aligned(target["transform"])
                ),
                "interpolation": "bilinear values, nearest-neighbour validity remask",
                "resolution_note": (
                    "The common grid uses the reference pixel size. If the two rasters have "
                    "different pixel sizes, a shift measured on the common grid is not directly "
                    "comparable to a shift measured on either source grid."
                ),
            },
        },
    )
    _write_json(out_path, payload)
    payload["output_path"] = os.path.abspath(out_path)
    return payload


def _upstream_record(info: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "path": info["path"],
        "crs": str(info["crs"]),
        "crs_units": info["crs_units"],
        "units_to_metres": info["units_to_metres"],
        "transform": [float(value) for value in tuple(info["transform"])[:6]],
        "width": info["width"],
        "height": info["height"],
        "nodata": info["nodata"],
        "valid_samples": int(np.count_nonzero(np.isfinite(info["data"]))),
    }


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    """Write the diagnostic atomically, so a reader never sees a partial file."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    try:
        text = json.dumps(payload, indent=2, allow_nan=False)
    except ValueError as exc:
        raise RegistrationError(f"refusing to write non-finite JSON: {exc}") from exc
    staging = os.path.join(parent, f".{os.path.basename(path)}.staging-{os.getpid()}")
    try:
        with open(staging, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m processing.registration",
        description=(
            "Measure the residual translation between two prepared, calibrated, overlapping "
            "rasters and write one JSON diagnostic. Does not correct geometry, does not write a "
            "manifest, and does not publish a bundle."
        ),
    )
    parser.add_argument("--reference", required=True, help="path to the reference prepared raster")
    parser.add_argument("--target", required=True, help="path to the target prepared raster to be measured")
    parser.add_argument("--out", required=True, help="path of the JSON diagnostic to write (parent created if needed)")
    defaults = Criteria()
    for name, help_text in (
        ("max_shift_pixels", "largest accepted shift magnitude, in pixels"),
        ("max_patch_deviation_pixels", "largest accepted distance of a patch estimate from the centre shift"),
        ("min_patch_dominance", "smallest accepted ratio of best to rival correlation peak"),
        ("min_patch_peak", "smallest accepted normalized correlation peak (not a probability)"),
        ("min_texture_cv", "smallest accepted patch dispersion std/|mean| (not an image-quality score)"),
        ("min_valid_fraction", "smallest accepted fraction of a patch valid in both rasters"),
        ("max_shift_search_pixels", "largest shift a patch may report before it is out of search range"),
    ):
        parser.add_argument(
            f"--{name.replace('_', '-')}",
            type=float,
            default=getattr(defaults, name),
            help=help_text,
        )
    parser.add_argument(
        "--min-patches", type=int, default=defaults.min_patches, help="smallest accepted number of usable patches"
    )
    return parser


def _criteria_from_args(args: argparse.Namespace) -> Criteria:
    return Criteria(
        max_shift_pixels=args.max_shift_pixels,
        max_patch_deviation_pixels=args.max_patch_deviation_pixels,
        min_patch_dominance=args.min_patch_dominance,
        min_patch_peak=args.min_patch_peak,
        min_texture_cv=args.min_texture_cv,
        min_valid_fraction=args.min_valid_fraction,
        min_patches=args.min_patches,
        max_shift_search_pixels=args.max_shift_search_pixels,
    ).validate()


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        criteria = _criteria_from_args(args)
        payload = run_registration_diagnostic(args.reference, args.target, args.out, criteria)
    except RegistrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    measured = payload["measured"]
    shift = measured["shift_pixels"] or {"x": None, "y": None}
    magnitude = measured["shift_magnitude_pixels"]
    print(
        f"wrote {payload['output_path']}: status {payload['status']}, "
        f"shift x={_format(shift['x'])} px y={_format(shift['y'])} px, "
        f"magnitude {'not measurable' if magnitude is None else f'{magnitude:.3f} px'} from "
        f"{measured['patches_accepted']} accepted patch(es); measurement only, no manifest or "
        f"bundle written"
    )
    return 0


def _format(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
