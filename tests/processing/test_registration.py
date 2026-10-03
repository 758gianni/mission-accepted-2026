"""Tests for the residual coregistration diagnostic.

All fixtures are synthetic and generated inside these tests. Nothing here is production or
demo data: there are no real prepared rasters in this repository, and the textures below are
synthetic patterns, not backscatter.

Run from the repository root with:

    python -m pytest tests/processing/test_registration.py
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import from_origin
from scipy import ndimage

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from processing.registration import (  # noqa: E402
    PATCH_SIZE,
    STATUS_ALIGNED,
    STATUS_EXCEEDS_TOLERANCE,
    STATUS_INCONSISTENT,
    STATUS_NOT_EVALUABLE,
    Criteria,
    RegistrationError,
    diagnose_arrays,
    estimate_patch_shift,
    main,
    run_registration_diagnostic,
)

CRS_UTM = CRS.from_epsg(32633)
PIXEL_SIZE = 30.0
SIZE = 192
ORIGIN_X = 499_980.0
ORIGIN_Y = 4_000_020.0
GEOREFERENCE = {
    "transform": (PIXEL_SIZE, 0.0, ORIGIN_X, 0.0, -PIXEL_SIZE, ORIGIN_Y),
    "units_to_metres": 1.0,
}


# --------------------------------------------------------------------------- #
# synthetic fixture generation (tests only)
# --------------------------------------------------------------------------- #
def textured_scene(size: int = SIZE, seed: int = 11) -> np.ndarray:
    """A synthetic, strongly textured linear-power-like surface. Not backscatter."""
    rng = np.random.default_rng(seed)
    rows, columns = np.mgrid[0:size, 0:size]
    scene = np.full((size, size), 0.1, dtype="float64")
    for _ in range(size // 2):
        centre_row, centre_column = rng.integers(6, size - 6, 2)
        scene[np.hypot(rows - centre_row, columns - centre_column) < rng.integers(3, 9)] = rng.uniform(
            0.3, 2.5
        )
    return scene + 0.05 * rng.random((size, size))


def shift_scene(scene: np.ndarray, shift_x: float, shift_y: float) -> np.ndarray:
    """Sub-pixel translation, in the same convention the diagnostic reports."""
    return ndimage.shift(scene, (shift_y, shift_x), order=3, mode="nearest")


def write_raster(path, data, *, crs=CRS_UTM, pixel_size=PIXEL_SIZE, transform=None, nodata=-9999.0):
    if transform is None:
        transform = from_origin(ORIGIN_X, ORIGIN_Y, pixel_size, pixel_size)
    with __import__("rasterio").open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(np.asarray(data, dtype="float32"), 1)
    return str(path)


def strict_criteria(**overrides) -> Criteria:
    """Defaults plus overrides, so each test states only what it is about."""
    fields = {
        "max_shift_pixels": 1.0,
        "max_patch_deviation_pixels": 0.5,
        "min_patch_dominance": 1.5,
        "min_patch_peak": 0.15,
        "min_texture_cv": 0.05,
        "min_valid_fraction": 0.6,
        "min_patches": 4,
        "max_shift_search_pixels": 8.0,
    }
    fields.update(overrides)
    return Criteria(**fields)


def accepted_shifts(report):
    return [
        record["shift_pixels"]
        for record in report["patch_evidence"]
        if record["accepted"]
    ]


# --------------------------------------------------------------------------- #
# known shifts
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shift_x,shift_y", [(0.0, 0.0), (4.0, 0.0), (0.0, -6.0), (-3.0, 5.0), (2.0, -2.0)])
def test_known_integer_shift_is_recovered(shift_x, shift_y):
    scene = textured_scene()
    report = diagnose_arrays(
        scene, shift_scene(scene, shift_x, shift_y), criteria=strict_criteria(max_shift_pixels=8.0)
    )
    assert report["status"] == STATUS_ALIGNED, report["status_reasons"]
    measured = report["measured"]["shift_pixels"]
    # a 32 px patch locates a shift to about 0.3 px, degrading as the shift nears the search bound
    assert measured["x"] == pytest.approx(shift_x, abs=0.35)
    assert measured["y"] == pytest.approx(shift_y, abs=0.35)


@pytest.mark.parametrize("shift_x,shift_y", [(0.4, 0.0), (0.0, -0.5), (1.5, -1.25), (-2.25, 1.75)])
def test_known_subpixel_shift_is_recovered(shift_x, shift_y):
    scene = textured_scene(seed=23)
    report = diagnose_arrays(
        scene, shift_scene(scene, shift_x, shift_y), criteria=strict_criteria(max_shift_pixels=8.0)
    )
    measured = report["measured"]["shift_pixels"]
    assert report["status"] == STATUS_ALIGNED, report["status_reasons"]
    assert measured["x"] == pytest.approx(shift_x, abs=0.35)
    assert measured["y"] == pytest.approx(shift_y, abs=0.35)


def test_known_shift_is_reported_in_metres_where_the_crs_allows():
    scene = textured_scene()
    report = diagnose_arrays(
        scene,
        shift_scene(scene, 4.0, -3.0),
        criteria=strict_criteria(max_shift_pixels=8.0),
        georeference=GEOREFERENCE,
    )
    measured = report["measured"]
    assert measured["shift_metres"] is not None
    # north-up 30 m grid: a +column shift is +x in metres, a +row shift is -y in metres
    assert measured["shift_metres"]["x"] == pytest.approx(measured["shift_pixels"]["x"] * PIXEL_SIZE)
    assert measured["shift_metres"]["y"] == pytest.approx(-measured["shift_pixels"]["y"] * PIXEL_SIZE)
    assert report["georeference"]["units_to_metres"] == 1.0


def test_no_metric_shift_is_reported_when_the_crs_units_do_not_convert():
    scene = textured_scene()
    report = diagnose_arrays(
        scene,
        shift_scene(scene, 4.0, 0.0),
        criteria=strict_criteria(max_shift_pixels=8.0),
        georeference={"transform": GEOREFERENCE["transform"], "units_to_metres": None},
    )
    measured = report["measured"]
    assert measured["shift_pixels"]["x"] == pytest.approx(4.0, abs=0.25)
    assert measured["shift_metres"] is None
    assert measured["shift_metres_note"]
    assert "pixel" in measured["shift_metres_note"]


# --------------------------------------------------------------------------- #
# no shift
# --------------------------------------------------------------------------- #
def test_identical_rasters_report_a_zero_shift():
    scene = textured_scene(seed=5)
    report = diagnose_arrays(scene, scene.copy(), criteria=strict_criteria())
    assert report["status"] == STATUS_ALIGNED, report["status_reasons"]
    assert abs(report["measured"]["shift_pixels"]["x"]) < 0.1
    assert abs(report["measured"]["shift_pixels"]["y"]) < 0.1
    assert report["measured"]["shift_magnitude_pixels"] < 0.15
    assert report["measured"]["patches_accepted"] >= 4


def test_a_global_radiometric_offset_does_not_invent_a_shift():
    """Standardising each patch makes the estimate invariant to a radiometric scale change."""
    scene = textured_scene(seed=7)
    brightened = scene * 3.0 + 0.02
    report = diagnose_arrays(scene, brightened, criteria=strict_criteria())
    assert report["status"] == STATUS_ALIGNED, report["status_reasons"]
    assert report["measured"]["shift_magnitude_pixels"] < 0.2


def test_a_shift_below_the_caller_tolerance_is_reported_as_within_it():
    scene = textured_scene(seed=13)
    report = diagnose_arrays(scene, shift_scene(scene, 0.5, 0.0), criteria=strict_criteria(max_shift_pixels=1.0))
    assert report["status"] == STATUS_ALIGNED
    assert report["measured"]["shift_magnitude_pixels"] < 1.0


def test_a_shift_above_the_caller_tolerance_is_reported_as_exceeding_it():
    scene = textured_scene(seed=13)
    report = diagnose_arrays(scene, shift_scene(scene, 3.0, -2.0), criteria=strict_criteria(max_shift_pixels=1.0))
    assert report["status"] == STATUS_EXCEEDS_TOLERANCE
    assert "above the caller's 1.000 px" in report["status_reasons"][0]
    assert report["measured"]["patches_accepted"] >= 4
    assert report["assertions"]["status_derived_from_grid_reprojection"] is False


# --------------------------------------------------------------------------- #
# nodata
# --------------------------------------------------------------------------- #
def test_nodata_holes_and_a_nodata_border_are_ignored():
    scene = textured_scene(seed=17)
    target = shift_scene(scene, 2.0, -1.0)
    for array in (scene, target):
        array[:24, :] = np.nan
        array[:, :24] = np.nan
        array[100:108, 100:108] = np.nan
        array[150, 150] = np.nan
    report = diagnose_arrays(scene, target, criteria=strict_criteria(max_shift_pixels=8.0))
    assert report["status"] == STATUS_ALIGNED, report["status_reasons"]
    measured = report["measured"]["shift_pixels"]
    assert measured["x"] == pytest.approx(2.0, abs=0.35)
    assert measured["y"] == pytest.approx(-1.0, abs=0.35)
    fractions = [record["valid_fraction"] for record in report["patch_evidence"]]
    assert min(fractions) < 1.0
    assert all(record["valid_fraction"] >= 0.6 for record in report["patch_evidence"] if record["accepted"])


def test_patches_that_are_mostly_nodata_are_rejected_as_insufficient_valid():
    scene = textured_scene(seed=19)
    target = shift_scene(scene, 0.0, 0.0)
    target[:, 100:] = np.nan
    scene[:, 100:] = np.nan
    report = diagnose_arrays(scene, target, criteria=strict_criteria())
    assert report["patch_rejections"]["insufficient_valid"] > 0
    assert all(
        record["rejection_reason"] != "insufficient_valid"
        for record in report["patch_evidence"]
        if record["accepted"]
    )
    assert report["status"] in (STATUS_ALIGNED, STATUS_NOT_EVALUABLE)


def test_a_pair_with_no_shared_valid_support_is_refused():
    scene = textured_scene()
    other = np.full_like(scene, np.nan)
    with pytest.raises(RegistrationError, match="no pixel has valid support in both rasters"):
        diagnose_arrays(scene, other, criteria=strict_criteria())


def test_an_overlap_too_small_for_one_patch_is_not_evaluable():
    scene = textured_scene(size=PATCH_SIZE // 2)
    report = diagnose_arrays(scene, scene.copy(), criteria=strict_criteria())
    assert report["status"] == STATUS_NOT_EVALUABLE
    assert "smaller than the 32 px patch" in report["status_reasons"][0]


# --------------------------------------------------------------------------- #
# homogeneous and periodic input
# --------------------------------------------------------------------------- #
def test_a_homogeneous_pair_is_rejected_as_flat_and_never_reported_as_aligned():
    flat = np.full((SIZE, SIZE), 0.42, dtype="float64")
    report = diagnose_arrays(flat, flat * 1.5, criteria=strict_criteria())
    assert report["status"] == STATUS_NOT_EVALUABLE
    assert report["measured"]["shift_pixels"] is None
    assert report["measured"]["patches_accepted"] == 0
    assert report["patch_rejections"]["flat"] > 0


def test_a_pair_with_noise_but_no_structure_is_rejected_as_ambiguous():
    """Speckle-like noise on its own must not be accepted as structure to correlate."""
    rng = np.random.default_rng(3)
    reference = rng.uniform(0.05, 0.4, (SIZE, SIZE))
    target = rng.uniform(0.05, 0.4, (SIZE, SIZE))
    report = diagnose_arrays(reference, target, criteria=strict_criteria(min_patches=2))
    assert report["status"] != STATUS_ALIGNED
    assert report["patch_rejections"]["ambiguous"] + report["patch_rejections"]["low_peak"] > 0
    assert report["measured"]["patches_accepted"] < 4


def test_a_periodic_pair_is_rejected_as_ambiguous():
    """A 4 px periodic pattern has a rival peak of the same height, so the peak is not unique."""
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    stripe = np.where(columns % 4 == 0, 2.0, 0.2)
    reference = stripe * (1.0 + 0.2 * np.cos(rows / 7.0))
    report = diagnose_arrays(reference, reference.copy(), criteria=strict_criteria(min_patches=2))
    assert report["status"] != STATUS_ALIGNED
    assert report["patch_rejections"]["ambiguous"] > 0
    ambiguous = [record for record in report["patch_evidence"] if record["rejection_reason"] == "ambiguous"]
    assert ambiguous, report["patch_evidence"]
    for record in ambiguous:
        assert record["peak_dominance"] < strict_criteria().min_patch_dominance


# --------------------------------------------------------------------------- #
# inconsistent local shifts
# --------------------------------------------------------------------------- #
def test_inconsistent_local_shifts_are_reported_instead_of_one_meaningless_number():
    scene = textured_scene(size=256, seed=29)
    target = scene.copy()
    top = slice(0, 128)
    target[top] = shift_scene(scene[top], 0.0, 0.0)
    target[128:] = shift_scene(scene[128:], 5.0, 4.0)
    report = diagnose_arrays(scene, target, criteria=strict_criteria())
    assert report["status"] == STATUS_INCONSISTENT, report["status_reasons"]
    assert report["measured"]["patches_accepted"] >= 4
    assert report["measured"]["max_patch_deviation_pixels"] > 0.5
    assert "one global translation does not describe" in report["status_reasons"][0]
    deviations = [record["deviation_pixels"] for record in report["patch_evidence"] if record["accepted"]]
    assert max(deviations) == pytest.approx(report["measured"]["max_patch_deviation_pixels"])
    # the shift field itself is reported as evidence of more than a translation
    assert report["measured"]["rotation_equivalent_rad"] is not None


def test_a_small_persistent_offset_stays_within_the_caller_deviation_tolerance():
    scene = textured_scene(size=256, seed=31)
    target = shift_scene(scene, 1.0, 0.5)
    report = diagnose_arrays(
        scene,
        target,
        criteria=strict_criteria(max_patch_deviation_pixels=0.5, max_shift_pixels=2.0),
    )
    assert report["status"] == STATUS_ALIGNED, report["status_reasons"]
    assert report["measured"]["max_patch_deviation_pixels"] <= 0.5
    assert report["measured"]["shift_magnitude_pixels"] > 0.5


# --------------------------------------------------------------------------- #
# structural and contract requirements
# --------------------------------------------------------------------------- #
def test_patches_are_spread_out_and_independent():
    scene = textured_scene(seed=37)
    report = diagnose_arrays(scene, shift_scene(scene, 1.0, 1.0), criteria=strict_criteria())
    accepted = [record for record in report["patch_evidence"] if record["accepted"]]
    assert len(accepted) >= 4
    assert len({record["centre_row_px"] for record in accepted}) >= 2
    assert len({record["centre_column_px"] for record in accepted}) >= 2
    for first in range(len(accepted)):
        for second in range(first + 1, len(accepted)):
            separation = math.hypot(
                accepted[first]["centre_row_px"] - accepted[second]["centre_row_px"],
                accepted[first]["centre_column_px"] - accepted[second]["centre_column_px"],
            )
            assert separation >= 2 * PATCH_SIZE


def test_the_caller_criterion_is_echoed_and_nothing_is_invented():
    scene = textured_scene(seed=41)
    criteria = strict_criteria(max_shift_pixels=0.75, min_patches=5)
    report = diagnose_arrays(scene, shift_scene(scene, 0.25, 0.0), criteria=criteria)
    assert report["criterion"] == criteria.as_dict()
    assert report["criterion"]["max_shift_pixels"] == 0.75
    assert report["assertions"] == {
        "manifest_registration_status_set": False,
        "manifest_written_or_modified": False,
        "bundle_published": False,
        "correction_applied_to_inputs": False,
        "probability_or_confidence_reported": False,
        "cause_attributed": False,
        "status_derived_from_grid_reprojection": False,
    }
    assert report["status"] in (
        STATUS_ALIGNED,
        STATUS_EXCEEDS_TOLERANCE,
        STATUS_INCONSISTENT,
        STATUS_NOT_EVALUABLE,
    )
    assert report["limitations"]
    assert report["criterion_source"].startswith("supplied by the caller")


def _keys(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield str(key)
            yield from _keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _keys(item)


def test_no_confidence_probability_or_cause_field_is_emitted():
    scene = textured_scene(seed=41)
    report = diagnose_arrays(scene, shift_scene(scene, 0.25, 0.0), criteria=strict_criteria())
    banned = {
        "confidence",
        "probability",
        "score",
        "accuracy",
        "quality",
        "verified",
        "passed",
        "deforestation",
        "fire",
        "flood",
        "loss",
    }
    offending = sorted({key for key in _keys(report) if key.lower() in banned})
    assert offending == []


@pytest.mark.parametrize(
    "field",
    ["max_shift_pixels", "max_patch_deviation_pixels", "min_patch_dominance", "min_patch_peak", "min_texture_cv"],
)
def test_non_positive_caller_thresholds_are_refused(field):
    scene = textured_scene()
    with pytest.raises(RegistrationError, match="strictly positive"):
        diagnose_arrays(scene, scene, criteria=strict_criteria(**{field: 0.0}))


def test_a_search_bound_beyond_the_correlation_window_is_refused():
    scene = textured_scene()
    with pytest.raises(RegistrationError, match="wrap around"):
        diagnose_arrays(scene, scene, criteria=strict_criteria(max_shift_search_pixels=20.0))


def test_shifted_content_on_an_identical_grid_does_not_pass_because_the_grid_matched():
    """Two rasters can share transform, CRS and shape and still be badly misregistered."""
    scene = textured_scene(seed=43)
    report = diagnose_arrays(scene, shift_scene(scene, 6.0, -5.0), criteria=strict_criteria())
    # it must not pass, and the measurement must still say by how much it is off
    assert report["status"] != STATUS_ALIGNED
    measured = report["measured"]["shift_pixels"]
    assert measured["x"] == pytest.approx(6.0, abs=0.6)
    assert measured["y"] == pytest.approx(-5.0, abs=0.6)


# --------------------------------------------------------------------------- #
# the per-patch primitive
# --------------------------------------------------------------------------- #
def test_estimate_patch_shift_recovers_a_known_shift_and_reports_rival_peaks():
    scene = textured_scene(size=96, seed=47)
    window = (slice(32, 32 + PATCH_SIZE), slice(32, 32 + PATCH_SIZE))
    patch = scene[window].copy()
    target = shift_scene(scene, 3.0, 2.0)[window].copy()
    estimate = estimate_patch_shift(patch, target, np.ones_like(patch, dtype=bool))
    assert estimate["shift_x"] == pytest.approx(3.0, abs=0.25)
    assert estimate["shift_y"] == pytest.approx(2.0, abs=0.25)
    assert estimate["normalized_peak"] == pytest.approx(1.0, abs=0.1)
    assert estimate["rival_peak"] is None or estimate["rival_peak"] < estimate["normalized_peak"]
    assert estimate["dominance"] is None or estimate["dominance"] > 1.5
    assert estimate["valid_fraction"] == 1.0
    assert estimate["integer_peak_lag"] if "integer_peak_lag" in estimate else True
    assert estimate["peak_column"] == 3 and estimate["peak_row"] == 2


def test_estimate_patch_shift_reports_rival_peaks_of_equal_height_on_a_periodic_patch():
    columns = np.mgrid[0:PATCH_SIZE, 0:PATCH_SIZE][1]
    periodic = np.where(columns % 4 == 0, 2.0, 0.2)
    valid = np.ones_like(periodic, dtype=bool)
    # a 4 px period: a rival peak sits 4 px away and is nearly as strong as the winner
    near = estimate_patch_shift(periodic, periodic.copy(), valid, exclusion_radius=1)
    assert near["rival_peak"] > 0.85 * near["normalized_peak"]
    assert near["dominance"] < 1.5
    # only when the caller declares 4 px shifts acceptable does that rival count as one
    wide = estimate_patch_shift(periodic, periodic.copy(), valid, exclusion_radius=5)
    assert wide["dominance"] > near["dominance"]


def test_estimate_patch_shift_refuses_a_flat_patch():
    flat = np.full((PATCH_SIZE, PATCH_SIZE), 0.3)
    with pytest.raises(RegistrationError, match="zero dispersion"):
        estimate_patch_shift(flat, flat, np.ones_like(flat, dtype=bool))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_writes_one_json_diagnostic_and_touches_nothing_else(tmp_path, capsys):
    scene = textured_scene(seed=53)
    reference_path = write_raster(tmp_path / "reference.tif", scene)
    target_path = write_raster(tmp_path / "target.tif", shift_scene(scene, 3.0, -2.0))
    out_path = tmp_path / "nested" / "registration-diagnostic.json"
    before = sorted(os.listdir(tmp_path))
    exit_code = main(
        [
            "--reference",
            reference_path,
            "--target",
            target_path,
            "--out",
            str(out_path),
            "--max-shift-pixels",
            "1.0",
        ]
    )
    assert exit_code == 0
    assert sorted(os.listdir(tmp_path)) == sorted(before + ["nested"])
    assert os.listdir(out_path.parent) == ["registration-diagnostic.json"]
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert report["status"] == STATUS_EXCEEDS_TOLERANCE, report["status_reasons"]
    # 3 px in x and -2 px in y, written as float32, must be visible in the measurement
    assert report["measured"]["shift_pixels"]["x"] == pytest.approx(3.0, abs=0.5)
    assert report["measured"]["shift_pixels"]["y"] == pytest.approx(-2.0, abs=0.5)
    assert report["measured"]["shift_metres"] is not None
    assert report["criterion"]["max_shift_pixels"] == 1.0
    assert report["upstream"]["reference"]["crs_units"] == "metre"
    assert report["upstream"]["common_grid"]["resolution"] == [PIXEL_SIZE, PIXEL_SIZE]
    assert "status exceeds_caller_tolerance" in capsys.readouterr().out


def test_cli_diagnostic_is_strict_json_and_records_that_it_changed_nothing(tmp_path):
    scene = textured_scene(seed=59)
    reference_path = write_raster(tmp_path / "reference.tif", scene)
    target_path = write_raster(tmp_path / "target.tif", shift_scene(scene, 0.0, 0.0))
    out_path = tmp_path / "diagnostic.json"
    run_registration_diagnostic(reference_path, target_path, str(out_path), strict_criteria())
    report = json.loads(out_path.read_text(encoding="utf-8"))
    # strict round trip: json.loads rejects nothing here, but the writer used allow_nan=False
    assert json.dumps(report, allow_nan=False)
    assert report["assertions"]["manifest_written_or_modified"] is False
    assert report["assertions"]["bundle_published"] is False
    assert report["assertions"]["status_derived_from_grid_reprojection"] is False
    assert report["manifest_registration_diagnostic_text"]
    assert "did not verify registration" in report["manifest_registration_diagnostic_text"]
    assert report["upstream"]["common_grid"]["identical_to_delivered_grids"] is True
    assert report["upstream"]["common_grid"]["reprojected"] is False


def test_cli_reports_rasters_that_do_not_overlap_without_writing_anything(tmp_path, capsys):
    scene = textured_scene()
    reference_path = write_raster(tmp_path / "reference.tif", scene)
    far_path = write_raster(
        tmp_path / "far.tif",
        scene,
        transform=from_origin(ORIGIN_X + 100_000.0, ORIGIN_Y, PIXEL_SIZE, PIXEL_SIZE),
    )
    out_path = tmp_path / "diagnostic.json"
    exit_code = main(["--reference", reference_path, "--target", far_path, "--out", str(out_path)])
    assert exit_code == 2
    assert "do not overlap on the map" in capsys.readouterr().err
    assert not out_path.exists()


def test_cli_reports_a_missing_raster_without_a_traceback(tmp_path, capsys):
    out_path = tmp_path / "diagnostic.json"
    exit_code = main(
        [
            "--reference",
            str(tmp_path / "absent.tif"),
            "--target",
            str(tmp_path / "absent-too.tif"),
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err
    assert not out_path.exists()


def test_cli_uses_different_grids_for_the_two_rasters_and_says_so(tmp_path):
    scene = textured_scene(size=160, seed=61)
    target = shift_scene(scene, 2.0, 2.0)
    reference_path = write_raster(tmp_path / "reference.tif", scene)
    target_path = write_raster(
        tmp_path / "target.tif",
        target,
        transform=from_origin(ORIGIN_X + 3 * PIXEL_SIZE, ORIGIN_Y - 2 * PIXEL_SIZE, PIXEL_SIZE, PIXEL_SIZE),
    )
    out_path = tmp_path / "diagnostic.json"
    report = run_registration_diagnostic(reference_path, target_path, str(out_path), strict_criteria())
    assert report["upstream"]["common_grid"]["reprojected"] is True
    assert report["upstream"]["common_grid"]["identical_to_delivered_grids"] is False
    assert report["upstream"]["common_grid"]["grid_path"] == "native_crs_intersection"
    # the measurement is in map space, so a difference in raster origin and a difference in
    # content both count and they add: +3 px of origin offset and +2 px of content shift give
    # +5 px in x, and +2 px of origin offset and +2 px of content shift give +4 px in y.
    assert report["measured"]["shift_pixels"]["x"] == pytest.approx(5.0, abs=0.8)
    assert report["measured"]["shift_pixels"]["y"] == pytest.approx(4.0, abs=0.8)


def test_module_runs_as_a_subprocess_and_exits_zero(tmp_path):
    scene = textured_scene(size=96, seed=67)
    reference_path = write_raster(tmp_path / "reference.tif", scene)
    target_path = write_raster(tmp_path / "target.tif", scene.copy())
    out_path = tmp_path / "diagnostic.json"
    environment = dict(os.environ, PYTHONPATH=REPO_ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "processing.registration",
            "--reference",
            reference_path,
            "--target",
            target_path,
            "--out",
            str(out_path),
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(out_path.read_text(encoding="utf-8"))
    # a 96 px overlap is smaller than the 4 patches the default criterion needs
    assert report["status"] == STATUS_NOT_EVALUABLE
    assert report["measured"]["patches_accepted"] < 4
