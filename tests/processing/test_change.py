"""Tests for the prepared-pair -> change raster -> regions -> result bundle slice.

All fixtures are synthetic and generated in temporary directories inside these
tests. Nothing here is production or demo data: there are no real prepared
rasters in this repository, and these fixtures must never be published as such.

Run from the repository root with:

    python -m pytest tests/processing/test_change.py
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from pyproj import Geod
from shapely.geometry import LinearRing, shape

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from processing.change import (  # noqa: E402
    ChangeError,
    MIN_VALID_NEIGHBOURS,
    nodata_mean_filter,
    region_change_statistics,
    run_change_detection,
    signed_change_db,
)

CRS_UTM = CRS.from_epsg(32633)
BASE_PRICE = 0.2
PATCH_RATIO = 2.0
PATCH_COLUMNS = slice(12, 24)
PATCH_ROWS = slice(14, 26)
HOLE_ROWS = slice(18, 22)
HOLE_COLUMNS = slice(16, 20)
GRID_SIZE = 40
PIXEL_SIZE = 30.0
ORIGIN_X = 499_980.0  # multiples of PIXEL_SIZE so the reference grid coincides with the fixture grid
ORIGIN_Y = 4_000_020.0
# Detected-pixel counts below follow from the 3x3 linear-power kernel: inside a
# 12x12 patch the 10x10 core keeps the full 3.0103 dB, the 1-cell ring averages
# 6-of-9 changed samples (2.2185 dB, still above the 2 dB threshold), and the
# corners (1.60/1.25/0.87/0.46 dB) fall below it. Detected 12x12 patch = 140 cells.
PATCH_CELLS = 140
PATCH_AREA_HA = PATCH_CELLS * PIXEL_SIZE * PIXEL_SIZE / 10_000.0

BASELINE_ACQUIRED = "2026-03-04T10:15:00Z"
FOLLOWUP_ACQUIRED = "2026-09-19T10:15:00Z"
EXPECTED_DB = 10.0 * math.log10(PATCH_RATIO)


# --------------------------------------------------------------------------- #
# synthetic fixture generation (tests only)
# --------------------------------------------------------------------------- #
def write_raster(path, data, *, crs=CRS_UTM, transform=None, nodata=-9999.0, dtype="float32", tags=None):
    if transform is None:
        transform = from_origin(ORIGIN_X, ORIGIN_Y, PIXEL_SIZE, PIXEL_SIZE)
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.asarray(data, dtype=dtype), 1)
        if tags:
            dst.update_tags(**tags)
    return str(path)


def stable_grid():
    data = np.full((GRID_SIZE, GRID_SIZE), BASE_PRICE, dtype="float64")
    return data


def change_grid(*, with_hole=False, extra_small_patch=False):
    data = stable_grid()
    data[PATCH_ROWS, PATCH_COLUMNS] = BASE_PRICE * PATCH_RATIO
    if with_hole:
        data[HOLE_ROWS, HOLE_COLUMNS] = BASE_PRICE
    if extra_small_patch:
        data[30:36, 30:36] = BASE_PRICE * PATCH_RATIO
    return data


def scene(
    path,
    *,
    scene_id="SCENE-B",
    acquired_at=FOLLOWUP_ACQUIRED,
    quantity="sigma0",
    polarization="C",
    beam_mode="S",
    orbit_direction="DESCENDING",
    relative_orbit=12345,
    terrain_correction="verified",
    calibration="verified",
    geocoding="verified",
    representation="linear_power",
    product_type="GRD",
):
    return {
        "id": scene_id,
        "acquired_at": acquired_at,
        "polarization": polarization,
        "beam_mode": beam_mode,
        "orbit_direction": orbit_direction,
        "relative_orbit": relative_orbit,
        "product_type": product_type,
        "source_collection": "Radarsat-2_Tropical_Forest_Products",
        "catalog_url": f"https://catalogue.example.invalid/{scene_id}",
        "path": str(path),
        "radiometry": {
            "quantity": quantity,
            "representation": representation,
            "calibration": calibration,
            "geocoding": geocoding,
            "terrain_correction": terrain_correction,
            "processing_steps": ["synthetic test fixture: not a real prepared product"],
        },
    }


def registration(status="passed", residual=0.8, diagnostic="synthetic fixture diagnostic, not real evidence"):
    return {"status": status, "residual_pixels": residual, "diagnostic": diagnostic}


def write_manifest(path, baseline_scene, followup_scene, *, reg=None, schema_version=1, extra_scenes=()):
    payload = {
        "schema_version": schema_version,
        "scenes": [baseline_scene, followup_scene, *extra_scenes],
        "registration": registration() if reg is None else reg,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return str(path)


@pytest.fixture
def pair(tmp_path):
    """Synthetic prepared pair: stable background plus a 2x-power patch."""
    before_path = write_raster(tmp_path / "before.tif", stable_grid())
    after_path = write_raster(tmp_path / "after.tif", change_grid())
    baseline = scene(before_path, scene_id="SCENE-A", acquired_at=BASELINE_ACQUIRED, product_type="SLC")
    followup = scene(after_path, scene_id="SCENE-B", acquired_at=FOLLOWUP_ACQUIRED)
    manifest = write_manifest(tmp_path / "manifest.json", baseline, followup)
    return {"manifest": manifest, "before": before_path, "after": after_path, "tmp_path": tmp_path}


def geodesic_area_ha(geometry):
    """Geodesic area of a WGS84 geometry in hectares, holes subtracted."""
    return abs(Geod(ellps="WGS84").geometry_area_perimeter(geometry)[0]) / 10_000.0


def json_features(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)["features"]


def run_pair(pair, out_name="bundle", **kwargs):
    params = {"threshold_db": 2.0, "min_area_ha": 0.0}
    params.update(kwargs)
    out_dir = os.path.join(str(pair["tmp_path"]), out_name)
    analysis = run_change_detection(pair["manifest"], out_dir, params["threshold_db"], params["min_area_ha"])
    with open(os.path.join(out_dir, "analysis.json"), "r", encoding="utf-8") as handle:
        analysis_on_disk = json.load(handle)
    with open(os.path.join(out_dir, "regions.geojson"), "r", encoding="utf-8") as handle:
        geojson = json.load(handle)
    return analysis, analysis_on_disk, geojson, out_dir


# --------------------------------------------------------------------------- #
# unit-level: filtering and change math
# --------------------------------------------------------------------------- #
def test_speckle_filter_uses_only_valid_neighbours():
    raster = np.full((5, 5), 4.0)
    raster[0, 0] = np.nan
    raster[0, 1] = np.nan
    raster[1, 0] = np.nan
    filtered = nodata_mean_filter(raster)
    # every sample is the same value, so any adequately supported window means 4.0
    assert np.allclose(filtered[1:4, 1:4], 4.0)
    # the array corner has only one valid neighbour in its 3x3 window -> undefined
    assert np.isnan(filtered[0, 0])


def test_speckle_filter_never_fills_a_missing_centre():
    # regression: at 9d27d506 this array produced filtered[2,2] == 1.0, inventing an
    # observation at a nodata pixel from its neighbours
    raster = np.ones((5, 5))
    raster[2, 2] = np.nan
    filtered = nodata_mean_filter(raster)
    assert np.isnan(filtered[2, 2]), "a missing original centre must stay unevaluable"
    # the eight valid centres around the hole keep their 8-of-9 window means
    assert np.isnan(filtered[1:4, 1:4]).sum() == 1
    assert np.allclose(filtered[1:4, 1:4][np.isfinite(filtered[1:4, 1:4])], 1.0)

    # a hole of nodata: every cell inside it has a missing centre, so none is evaluable
    hole = np.ones((5, 5))
    hole[1:4, 1:4] = np.nan
    assert np.isnan(nodata_mean_filter(hole)[1:4, 1:4]).all()

    # the rule is not over-strict: a valid centre with nodata neighbours is still defined and
    # the mean uses only the valid samples
    partial = np.ones((5, 5))
    partial[2, 1] = np.nan
    partial[1, 2] = np.nan
    assert nodata_mean_filter(partial)[2, 2] == pytest.approx(1.0)

    # and a valid centre whose window keeps exactly the documented minimum support is defined
    support = np.ones((5, 5))
    support[0, 0] = np.nan
    support[0, 1] = np.nan
    support[1, 0] = np.nan
    support[1, 1] = np.nan
    support[0, 2] = np.nan
    assert np.isfinite(nodata_mean_filter(support)[2, 2])


def test_speckle_filter_ignores_nodata_magnitude():
    raster = np.full((5, 5), 4.0)
    raster[2, 2] = 1000.0
    filtered = nodata_mean_filter(raster)
    window = filtered[2, 2]
    assert window == pytest.approx((8 * 4.0 + 1000.0) / 9.0)
    neighbour = filtered[2, 3]
    assert neighbour == pytest.approx((8 * 4.0 + 1000.0) / 9.0)


def test_speckle_filter_is_undefined_without_adequate_support():
    raster = np.full((3, 3), np.nan)
    raster[1, 1] = 1.0
    filtered = nodata_mean_filter(raster)
    assert np.isnan(filtered).all()

    mostly = np.full((3, 3), np.nan)
    mostly[0, 0] = 1.0
    mostly[0, 1] = 1.0
    filtered = nodata_mean_filter(mostly)
    # only 2 of 9 valid in the shared window -> undefined, per the documented rule
    assert np.isnan(filtered[0, 0])
    assert np.isnan(filtered[0, 1])

    supported = np.full((3, 3), np.nan)
    supported[0, 0] = 1.0
    supported[0, 1] = 1.0
    supported[0, 2] = 1.0
    supported[1, 0] = 1.0
    supported[1, 1] = 1.0
    filtered = nodata_mean_filter(supported)
    # the centre window holds exactly 5 of 9 valid samples -> the documented minimum is met
    assert np.isfinite(filtered[1, 1])
    assert filtered[1, 1] == pytest.approx(1.0)
    # array-edge cells see fewer samples, so they stay undefined
    assert np.isnan(filtered[0, 0])
    assert MIN_VALID_NEIGHBOURS == 5


def test_speckle_filter_preserves_uniform_values_exactly():
    raster = np.full((5, 5), 0.37)
    assert np.allclose(nodata_mean_filter(raster)[1:4, 1:4], 0.37)


def test_signed_change_is_ten_log10_ratio():
    before = np.array([[0.5, 0.5], [0.5, 0.5]])
    after = np.array([[1.0, 0.25], [np.nan, 0.5]])
    change, evaluable = signed_change_db(before, after)
    assert evaluable.tolist() == [[True, True], [False, True]]
    assert change[0, 0] == pytest.approx(10.0 * math.log10(2.0))
    assert change[0, 1] == pytest.approx(-10.0 * math.log10(2.0))
    assert change[1, 0] != change[1, 0]  # NaN
    assert change[0, 0] == pytest.approx(3.010299956639812)


# --------------------------------------------------------------------------- #
# known 2x power patch, stable background, finite output
# --------------------------------------------------------------------------- #
def test_two_times_power_patch_is_detected_at_3_0103_db(pair):
    analysis, analysis_disk, geojson, out_dir = run_pair(pair)
    assert analysis == analysis_disk
    assert analysis["metrics"]["region_count"] == 1
    assert len(geojson["features"]) == 1
    feature = geojson["features"][0]
    properties = feature["properties"]
    assert properties["change_db"] == pytest.approx(3.010299956639812, abs=1e-6)
    assert properties["magnitude_db"] == pytest.approx(3.010299956639812, abs=1e-6)
    assert feature["geometry"]["type"] in ("Polygon", "MultiPolygon")
    with rasterio.open(os.path.join(out_dir, "change.tif")) as src, rasterio.open(
        os.path.join(out_dir, "mask.tif")
    ) as mask_src:
        change = src.read(1).astype("float64")
        mask = mask_src.read(1)
        assert src.crs == CRS_UTM
        stored = change[~np.isnan(change)]
        assert stored.size > 0
        assert np.isfinite(stored).all(), "no NaN or infinite value may be stored as data"
        # the published mask is exactly the threshold of the published change raster
        assert np.array_equal(np.nan_to_num(np.abs(change), nan=0.0) >= 2.0, mask == 1)
        assert int(np.count_nonzero(mask == 1)) == PATCH_CELLS
        # the 10x10 core keeps the full 2x power change (3.0103 dB), the 1-cell ring averages
        # 6 of 9 changed samples (2.2185 dB, still above the 2 dB threshold) and the corners
        # (1.60/1.25/0.87/0.46 dB) fall below it, so 140 of the 144 patch cells are detected
        assert np.nanmax(change) == pytest.approx(3.010299956639812, abs=1e-4)
        assert int(np.isclose(change, 3.010299956639812, atol=1e-4).sum()) == 100
        assert int(np.isclose(change, 2.2184875, atol=1e-4).sum()) == 40
        detected_db = np.abs(change[np.abs(change) >= 2.0])
        assert detected_db.min() == pytest.approx(2.2184875, abs=1e-4)
        assert np.allclose(change[2:10, 2:10], 0.0, atol=1e-6)


def test_stable_background_produces_no_regions(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", stable_grid() * 1.000001)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    analysis = run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)
    assert analysis["metrics"]["region_count"] == 0
    assert analysis["metrics"]["total_changed_area_ha"] == 0.0
    with open(os.path.join(str(tmp_path / "out"), "regions.geojson"), "r", encoding="utf-8") as handle:
        assert json.load(handle)["features"] == []


def test_brightening_and_darkening_both_detected_with_signed_median(tmp_path):
    after = stable_grid()
    after[10:20, 10:20] = BASE_PRICE / PATCH_RATIO
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)
    with open(os.path.join(str(tmp_path / "out"), "regions.geojson"), "r", encoding="utf-8") as handle:
        feature = json.load(handle)["features"][0]
    assert feature["properties"]["change_db"] == pytest.approx(-3.010299956639812, abs=1e-6)
    assert feature["properties"]["magnitude_db"] == pytest.approx(3.010299956639812, abs=1e-6)


def test_all_numeric_outputs_are_finite(pair):
    _, _, geojson, out_dir = run_pair(pair)
    with open(os.path.join(out_dir, "analysis.json"), "r", encoding="utf-8") as handle:
        text = handle.read()
    for token in ("NaN", "Infinity", "-Infinity"):
        assert token not in text
    with open(os.path.join(out_dir, "regions.geojson"), "r", encoding="utf-8") as handle:
        assert "NaN" not in handle.read()
    for feature in geojson["features"]:
        for key in ("area_ha", "change_db", "magnitude_db", "priority_score"):
            assert math.isfinite(float(feature["properties"][key]))
        for point in feature["time_series"] if "time_series" in feature else feature["properties"]["time_series"]:
            value = point["mean_backscatter_db"]
            assert value is None or math.isfinite(value)


# --------------------------------------------------------------------------- #
# nodata handling
# --------------------------------------------------------------------------- #
def test_nodata_hole_is_excluded_and_preserved_as_polygon_hole(tmp_path):
    after = change_grid(with_hole=True)
    after[34:39, 2:6] = np.nan  # a nodata block in the followup scene
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 0.0)
    with open(os.path.join(out_dir, "regions.geojson"), "r", encoding="utf-8") as handle:
        feature = json.load(handle)["features"][0]
    geometry = shape(feature["geometry"])
    assert geometry.geom_type == "Polygon"
    assert len(geometry.interiors) == 1, "the unchanged hole inside the patch must be preserved"
    hole = LinearRing(geometry.interiors[0].coords)
    # the 4x4 unchanged hole is 16 cells of 900 m2
    assert geodesic_area_ha(hole) == pytest.approx(16 * PIXEL_SIZE * PIXEL_SIZE / 10_000.0, rel=0.02)
    # the unchanged hole removes its 16 cells plus the kernel-diluted cells around them
    assert feature["properties"]["area_ha"] == pytest.approx(11.169, rel=0.02)
    assert feature["properties"]["area_ha"] < PATCH_AREA_HA
    # area_ha is the geodesic area of the thresholded pixels, so it matches the polygon
    assert feature["properties"]["area_ha"] == pytest.approx(geodesic_area_ha(geometry), rel=1e-9)
    # the 124 detected cells (140 minus the 16 hole cells) must match the reported area
    assert feature["properties"]["area_ha"] == pytest.approx(124 * PIXEL_SIZE * PIXEL_SIZE / 10_000.0, rel=0.02)
    assert analysis["metrics"]["not_evaluable_area_ha"] > 0.0
    assert analysis["metrics"]["valid_area_ha"] > 0.0
    with rasterio.open(os.path.join(out_dir, "change.tif")) as src:
        change = src.read(1).astype("float64")
        # the unchanged hole is evaluable but not changed: its core is 0 dB and the cells
        # diluted by it stay below the threshold, so none of them is detected
        assert np.allclose(change[20:22, 18:20], 0.0, atol=1e-6)
        assert np.nanmax(np.abs(change[19:23, 17:21])) < 2.0
        # the nodata block has no valid support in the followup scene, so it is not evaluable
        assert np.isnan(change[36:39, 3:6]).all()
        assert np.isfinite(change[16:26, 14:24]).all()
        assert np.nanmax(change[16:26, 14:24]) == pytest.approx(3.010299956639812, abs=1e-4)
        # the hole dilutes the cells around it, so fewer cells keep the untouched 3.0103 dB
        assert int(np.isclose(change, 3.010299956639812, atol=1e-4).sum()) < 100


def test_filter_edge_support_keeps_evaluable_near_scene_border(tmp_path):
    before = np.full((GRID_SIZE, GRID_SIZE), BASE_PRICE)
    after = np.full((GRID_SIZE, GRID_SIZE), BASE_PRICE)
    after[0:8, 0:8] = BASE_PRICE * PATCH_RATIO  # corner patch, partially outside the filter kernel
    before_path = write_raster(tmp_path / "b.tif", before)
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 0.0)
    assert analysis["metrics"]["region_count"] == 1
    with open(os.path.join(out_dir, "regions.geojson"), "r", encoding="utf-8") as handle:
        feature = json.load(handle)["features"][0]
    properties = feature["properties"]
    # 62 thresholded cells: the corner patch loses only the diluted outer edge, because the
    # interior-side 1-cell ring is still at 2.2185 dB
    assert properties["area_ha"] == pytest.approx(62 * PIXEL_SIZE * PIXEL_SIZE / 10_000.0, rel=0.02)
    assert properties["time_series"][1]["valid_fraction"] > 0.95
    assert properties["time_series"][1]["valid_fraction"] == pytest.approx(1.0)


def test_isolated_nodata_hole_stays_unevaluable_end_to_end(tmp_path):
    # a single missing observation inside a changed patch: it must not be reconstructed from
    # its neighbours, and it must leave the change raster and the area metrics as nodata
    plain_after = change_grid()
    plain_manifest = write_manifest(
        tmp_path / "plain.json",
        scene(write_raster(tmp_path / "pb.tif", stable_grid()), scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(write_raster(tmp_path / "pa.tif", plain_after), scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    plain = run_change_detection(plain_manifest, str(tmp_path / "plain"), 2.0, 0.0)

    holed_after = change_grid()
    holed_after[20, 18] = np.nan  # scene coordinates: inside the patch core
    holed_manifest = write_manifest(
        tmp_path / "holed.json",
        scene(write_raster(tmp_path / "hb.tif", stable_grid()), scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(write_raster(tmp_path / "ha.tif", holed_after), scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "holed")
    holed = run_change_detection(holed_manifest, out_dir, 2.0, 0.0)

    one_cell_ha = PIXEL_SIZE * PIXEL_SIZE / 10_000.0
    # exactly one cell leaves the detected region, the valid area and the changed area
    assert holed["metrics"]["total_changed_area_ha"] == pytest.approx(
        plain["metrics"]["total_changed_area_ha"] - one_cell_ha, rel=1e-3
    )
    assert holed["metrics"]["valid_area_ha"] == pytest.approx(
        plain["metrics"]["valid_area_ha"] - one_cell_ha, rel=1e-3
    )
    assert holed["metrics"]["not_evaluable_area_ha"] == pytest.approx(
        plain["metrics"]["not_evaluable_area_ha"] + one_cell_ha, rel=1e-3
    )
    assert holed["metrics"]["valid_area_ha"] + holed["metrics"]["not_evaluable_area_ha"] == pytest.approx(
        holed["metrics"]["analysis_area_ha"], rel=1e-9
    )
    assert holed["metrics"]["region_count"] == 1

    with rasterio.open(os.path.join(out_dir, "change.tif")) as src:
        change = src.read(1)
        # the missing observation is nodata in the published change raster ...
        assert np.isnan(change[21, 19]), "the missing original centre must not be reconstructed"
        # ... and its neighbours keep the untouched 3.0103 dB, because the nodata neighbour is
        # excluded from the kernel mean rather than treated as a zero backscatter
        assert change[21, 18] == pytest.approx(3.010299956639812, abs=1e-4)
        assert change[20, 19] == pytest.approx(3.010299956639812, abs=1e-4)
    with rasterio.open(os.path.join(out_dir, "mask.tif")) as src:
        assert int((src.read(1) == 1).sum()) == PATCH_CELLS - 1


def test_nodata_block_hole_is_excluded_from_change_and_area(tmp_path):
    after = change_grid()
    after[19:22, 17:20] = np.nan  # 3x3 missing block in the patch core
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(write_raster(tmp_path / "b.tif", stable_grid()), scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(write_raster(tmp_path / "a.tif", after), scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 0.0)
    one_cell_ha = PIXEL_SIZE * PIXEL_SIZE / 10_000.0
    assert analysis["metrics"]["total_changed_area_ha"] == pytest.approx(
        PATCH_AREA_HA - 9 * one_cell_ha, rel=1e-3
    )
    with open(os.path.join(out_dir, "regions.geojson"), "r", encoding="utf-8") as handle:
        feature = json.load(handle)["features"][0]
    geometry = shape(feature["geometry"])
    assert len(geometry.interiors) == 1, "the missing block is preserved as a hole"
    assert geodesic_area_ha(LinearRing(geometry.interiors[0].coords)) == pytest.approx(
        9 * one_cell_ha, rel=0.02
    )
    with rasterio.open(os.path.join(out_dir, "change.tif")) as src:
        change = src.read(1)
        assert np.isnan(change[20:23, 18:21]).all()
        # the cells around the block are still evaluable: their centres exist and their windows
        # keep 8 of 9 valid samples
        assert np.isfinite(change[19, 18:21]).all()
        assert np.isfinite(change[23, 18:21]).all()


def test_scene_mask_edge_is_never_interpolated_into(tmp_path):
    # a nodata stripe along the left edge of the followup scene: bilinear resampling would pull
    # neighbour values into those cells, so the post-warp remask must keep them unevaluable
    plain_manifest = write_manifest(
        tmp_path / "plain.json",
        scene(write_raster(tmp_path / "pb.tif", stable_grid()), scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(write_raster(tmp_path / "pa.tif", change_grid()), scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    plain = run_change_detection(plain_manifest, str(tmp_path / "plain"), 2.0, 0.0)
    after = change_grid()
    after[:, :3] = np.nan
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(write_raster(tmp_path / "b.tif", stable_grid()), scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(write_raster(tmp_path / "a.tif", after), scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 0.0)
    with rasterio.open(os.path.join(out_dir, "change.tif")) as src:
        change = src.read(1)
        # the grid is offset by one snapped cell, so the three nodata scene columns land on
        # reference-grid columns 1..3
        assert np.isnan(change[:, 1:4]).all(), "the valid-mask edge must stay unevaluable"
        assert np.isfinite(change[:, 4:]).any()
    with open(os.path.join(out_dir, "regions.geojson"), "r", encoding="utf-8") as handle:
        features = json.load(handle)["features"]
    assert analysis["metrics"]["region_count"] == len(features) == 1
    # the patch is away from the stripe, so its detected area is unchanged
    assert analysis["metrics"]["total_changed_area_ha"] == pytest.approx(PATCH_AREA_HA, rel=0.02)
    assert analysis["metrics"]["valid_area_ha"] + analysis["metrics"]["not_evaluable_area_ha"] == pytest.approx(
        analysis["metrics"]["analysis_area_ha"], rel=1e-9
    )
    # the three masked columns leave the evaluable area even though their windows have ample
    # valid support, because their centres are missing
    stripe_cells_ha = 3 * GRID_SIZE * PIXEL_SIZE * PIXEL_SIZE / 10_000.0
    assert analysis["metrics"]["valid_area_ha"] == pytest.approx(
        plain["metrics"]["valid_area_ha"] - stripe_cells_ha, rel=2e-3
    )
    assert analysis["metrics"]["not_evaluable_area_ha"] > plain["metrics"]["not_evaluable_area_ha"]
    # the outside-of-footprint ring of the snapped grid is never evaluable either
    with rasterio.open(os.path.join(out_dir, "change.tif")) as src:
        change = src.read(1)
        assert np.isnan(change[0, :]).all()
        assert np.isnan(change[-1, :]).all()
        assert np.isnan(change[:, 0]).all()
        assert np.isnan(change[:, -1]).all()


def test_nodata_only_scene_is_rejected(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after = np.full((GRID_SIZE, GRID_SIZE), np.nan)
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    with pytest.raises(ChangeError, match="no valid samples"):
        run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)


# --------------------------------------------------------------------------- #
# area, minimum area filter, priority
# --------------------------------------------------------------------------- #
def test_minimum_area_filter_removes_small_component(tmp_path):
    after = change_grid(extra_small_patch=True)
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    kept = run_change_detection(manifest, out_dir, 2.0, 0.0)
    assert kept["metrics"]["region_count"] == 2
    areas = sorted(f["properties"]["area_ha"] for f in json_features(os.path.join(out_dir, "regions.geojson")))
    assert areas == pytest.approx([2.882, PATCH_AREA_HA], rel=0.02)
    # a 6 ha minimum drops only the 2.88 ha patch and keeps the 12.6 ha one
    filtered = run_change_detection(manifest, out_dir + "-filtered", 2.0, 6.0)
    assert filtered["metrics"]["region_count"] == 1
    assert filtered["metrics"]["total_changed_area_ha"] == pytest.approx(PATCH_AREA_HA, rel=0.02)
    dropped = run_change_detection(manifest, out_dir + "-dropped", 2.0, 100.0)
    assert dropped["metrics"]["region_count"] == 0
    assert dropped["metrics"]["total_changed_area_ha"] == 0.0


def test_area_ha_is_geodesic_and_matches_pixel_count(pair):
    _, _, geojson, _ = run_pair(pair)
    properties = geojson["features"][0]["properties"]
    # 140 thresholded cells of 900 m2: the 10x10 core plus the diluted-but-detected 1-cell ring
    assert properties["area_ha"] == pytest.approx(PATCH_AREA_HA, rel=0.02)
    assert properties["area_ha"] == pytest.approx(12.6, abs=0.05)
    assert properties["priority_score"] == pytest.approx(
        properties["magnitude_db"] * math.sqrt(properties["area_ha"]), rel=1e-9
    )
    assert properties["priority_units"] == "dB sqrt(ha)"
    assert properties["priority_formula"] == "magnitude_db * sqrt(area_ha)"


def test_zero_minimum_area_is_allowed_and_threshold_still_applies(tmp_path):
    after = stable_grid()
    after[5, 5] = BASE_PRICE * PATCH_RATIO
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    # a single changed pixel surrounded by unchanged pixels is filtered away by the kernel mean
    analysis = run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)
    assert analysis["metrics"]["region_count"] == 0


def test_higher_threshold_removes_regions(tmp_path):
    after = change_grid()
    after[10:20, 10:20] = BASE_PRICE * 1.4
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    assert run_change_detection(manifest, out_dir, 2.0, 0.0)["metrics"]["region_count"] == 1
    assert run_change_detection(manifest, out_dir + "-hi", 5.0, 0.0)["metrics"]["region_count"] == 0


# --------------------------------------------------------------------------- #
# rejections: provenance, radiometry, geometry, parameters
# --------------------------------------------------------------------------- #
def _rejected(pair_builder, pattern, **kwargs):
    with pytest.raises(ChangeError, match=pattern):
        pair_builder(**kwargs)


def test_rejects_unverified_calibration_and_geocoding(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    for key, value in (("calibration", "unverified"), ("geocoding", "assumed"), ("terrain_correction", "none")):
        after = scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, **{key: value})
        manifest = write_manifest(
            tmp_path / "m.json", scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED), after
        )
        with pytest.raises(ChangeError, match="must be"):
            run_change_detection(manifest, str(tmp_path / f"out-{key}"), 2.0, 0.0)


def test_rejects_db_and_amplitude_representations(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    for representation in ("db", "amplitude", "amplitude_db"):
        after = scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, representation=representation)
        manifest = write_manifest(
            tmp_path / "m.json", scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED), after
        )
        with pytest.raises(ChangeError, match="linear_power|linear power"):
            run_change_detection(manifest, str(tmp_path / f"out-{representation}"), 2.0, 0.0)


def test_rejects_raster_tagged_in_db_or_amplitude(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid(), tags={"representation": "dB"})
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    with pytest.raises(ChangeError, match="dB data"):
        run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)
    amplitude_path = write_raster(tmp_path / "amp.tif", np.abs(change_grid()), tags={"units": "amplitude"})
    manifest2 = write_manifest(
        tmp_path / "m2.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(amplitude_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    with pytest.raises(ChangeError, match="amplitude"):
        run_change_detection(manifest2, str(tmp_path / "out2"), 2.0, 0.0)


def test_rejects_negative_valued_raster_that_looks_like_db(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    db_like = np.full((GRID_SIZE, GRID_SIZE), -8.0)
    db_like[PATCH_ROWS, PATCH_COLUMNS] = -4.0
    after_path = write_raster(tmp_path / "a.tif", db_like)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    with pytest.raises(ChangeError, match="negative"):
        run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)


def test_rejects_complex_raster(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    complex_data = (change_grid() + 1j * 0.01).astype("complex64")
    after_path = write_raster(tmp_path / "a.tif", complex_data, dtype="complex64")
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    with pytest.raises(ChangeError, match="complex"):
        run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)


def test_rejects_missing_crs_and_degenerate_transform(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    no_crs = write_raster(tmp_path / "nocrs.tif", change_grid(), crs=None)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(no_crs, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    with pytest.raises(ChangeError, match="no CRS"):
        run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)

    # a near-zero pixel size stands in for an undefined/identity geotransform
    identity = write_raster(
        tmp_path / "identity.tif", change_grid(), transform=rasterio.Affine(1e-9, 0.0, 500_000.0, 0.0, -1e-9, 4_000_000.0)
    )
    manifest2 = write_manifest(
        tmp_path / "m2.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(identity, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    with pytest.raises(ChangeError, match="degenerate transform"):
        run_change_detection(manifest2, str(tmp_path / "out2"), 2.0, 0.0)


def test_rejects_incompatible_pairs(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    baseline = scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED)
    cases = {
        "polarization": {"polarization": "X"},
        "beam_mode": {"beam_mode": "W"},
        "orbit_direction": {"orbit_direction": "ASCENDING"},
        "relative_orbit": {"relative_orbit": 99999},
        "quantity": {"quantity": "gamma0"},
    }
    for label, override in cases.items():
        after = scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, **override)
        manifest = write_manifest(tmp_path / f"m-{label}.json", baseline, after)
        with pytest.raises(ChangeError, match="incompatible"):
            run_change_detection(manifest, str(tmp_path / f"out-{label}"), 2.0, 0.0)

    unknown_orbit = scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, relative_orbit=None)
    manifest = write_manifest(tmp_path / "m-unknown.json", baseline, unknown_orbit)
    unknown_result = run_change_detection(manifest, str(tmp_path / "out-unknown"), 2.0, 0.0)
    assert unknown_result["metrics"]["region_count"] == 1
    assert unknown_result["scenes"][1]["relative_orbit"] is None


def test_rejects_non_overlapping_scenes(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    shifted = from_origin(ORIGIN_X + 100_000.0, ORIGIN_Y, PIXEL_SIZE, PIXEL_SIZE)
    after_path = write_raster(tmp_path / "a.tif", change_grid(), transform=shifted)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    with pytest.raises(ChangeError, match="do not overlap"):
        run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)


def test_rejects_wrong_scene_count_dates_and_registration(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    baseline = scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED)
    after = scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED)

    single = write_manifest(tmp_path / "single.json", baseline, after) if False else None
    assert single is None
    with open(tmp_path / "one.json", "w", encoding="utf-8") as handle:
        json.dump({"schema_version": 1, "scenes": [baseline], "registration": registration()}, handle)
    with pytest.raises(ChangeError, match="exactly 2 scenes"):
        run_change_detection(str(tmp_path / "one.json"), str(tmp_path / "out1"), 2.0, 0.0)

    third = scene(after_path, scene_id="C", acquired_at="2026-12-01T10:15:00Z")
    manifest3 = write_manifest(tmp_path / "three.json", baseline, after, extra_scenes=[third])
    with pytest.raises(ChangeError, match="exactly 2 scenes"):
        run_change_detection(manifest3, str(tmp_path / "out3"), 2.0, 0.0)

    same_date = scene(after_path, scene_id="B", acquired_at="2026-03-04T18:15:00Z")
    manifest_dup = write_manifest(tmp_path / "dup.json", baseline, same_date)
    with pytest.raises(ChangeError, match="distinct UTC dates"):
        run_change_detection(manifest_dup, str(tmp_path / "out4"), 2.0, 0.0)

    unsorted_manifest = write_manifest(
        tmp_path / "unsorted.json", after, scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED)
    )
    with pytest.raises(ChangeError, match="sorted ascending"):
        run_change_detection(unsorted_manifest, str(tmp_path / "out5"), 2.0, 0.0)

    for reg, pattern in (
        (registration(status="unchecked"), "status must be 'passed'"),
        (registration(residual=-1.0), "residual_pixels"),
        (registration(diagnostic=""), "diagnostic"),
    ):
        manifest = write_manifest(tmp_path / f"reg-{pattern[:4]}.json", baseline, after, reg=reg)
        with pytest.raises(ChangeError, match=pattern):
            run_change_detection(manifest, str(tmp_path / "out6"), 2.0, 0.0)

    bad_version = write_manifest(tmp_path / "ver.json", baseline, after, schema_version=2)
    with pytest.raises(ChangeError, match="schema_version"):
        run_change_detection(bad_version, str(tmp_path / "out7"), 2.0, 0.0)

    wrong_source = scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED)
    wrong_source["source_collection"] = "SomeOtherCollection"
    manifest_source = write_manifest(tmp_path / "src.json", baseline, wrong_source)
    with pytest.raises(ChangeError, match="source_collection"):
        run_change_detection(manifest_source, str(tmp_path / "out8"), 2.0, 0.0)

    with pytest.raises(ChangeError, match="manifest not found"):
        run_change_detection(str(tmp_path / "missing.json"), str(tmp_path / "out9"), 2.0, 0.0)


def test_relative_orbit_integer_stays_an_integer_in_the_bundle(tmp_path):
    # integration blocker at b8d9059d: a manifest integer orbit was serialised as a float
    # (98.0), which the API rejects with error/503
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED, relative_orbit=98),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, relative_orbit=98),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 0.0)
    for entry in analysis["scenes"]:
        assert entry["relative_orbit"] == 98
        assert isinstance(entry["relative_orbit"], int)
        assert not isinstance(entry["relative_orbit"], bool)
    with open(os.path.join(out_dir, "analysis.json"), "r", encoding="utf-8") as handle:
        text = handle.read()
    # the serialised form must be a JSON integer, not 98.0
    assert '"relative_orbit": 98' in text
    assert '"relative_orbit": 98.0' not in text
    reparsed = json.loads(text)
    for entry in reparsed["scenes"]:
        assert isinstance(entry["relative_orbit"], int)


def test_relative_orbit_null_stays_null(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED, relative_orbit=None),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, relative_orbit=None),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 0.0)
    for entry in analysis["scenes"]:
        assert entry["relative_orbit"] is None
    with open(os.path.join(out_dir, "analysis.json"), "r", encoding="utf-8") as handle:
        text = handle.read()
    assert '"relative_orbit": null' in text
    assert "NaN" not in text


def test_relative_orbit_fractional_values_are_rejected_not_rounded(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    baseline = scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED, relative_orbit=98)
    for value in (98.5, 98.1, -98.25):
        after = scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, relative_orbit=value)
        manifest = write_manifest(tmp_path / f"m-{value}.json", baseline, after)
        with pytest.raises(ChangeError, match="relative_orbit must be a whole number"):
            run_change_detection(manifest, str(tmp_path / f"out-{value}"), 2.0, 0.0)


def test_relative_orbit_integral_float_is_normalised_to_an_integer(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED, relative_orbit=98.0),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, relative_orbit=98.0),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 0.0)
    for entry in analysis["scenes"]:
        assert entry["relative_orbit"] == 98
        assert isinstance(entry["relative_orbit"], int)
    # 98 and 98.0 describe the same orbit, so the pair stays compatible
    mixed = write_manifest(
        tmp_path / "mixed.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED, relative_orbit=98),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, relative_orbit=98.0),
    )
    assert run_change_detection(mixed, str(tmp_path / "mixed-out"), 2.0, 0.0)["metrics"]["region_count"] == 1


def test_relative_orbit_non_finite_and_boolean_are_rejected(tmp_path):
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    baseline = scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED, relative_orbit=98)
    for value in ("98", True, float("nan"), float("inf"), [98]):
        after = scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED, relative_orbit=value)
        manifest = write_manifest(tmp_path / "m.json", baseline, after)
        with pytest.raises(ChangeError, match="relative_orbit must be a whole number or null"):
            run_change_detection(manifest, str(tmp_path / "out-bad"), 2.0, 0.0)


def test_rejects_nonpositive_or_nonfinite_parameters(pair):
    for threshold, pattern in ((0.0, "strictly positive"), (-1.0, "strictly positive"), (float("nan"), "finite"), (float("inf"), "finite")):
        with pytest.raises(ChangeError, match=pattern):
            run_pair(pair, out_name=f"out-t{threshold}", threshold_db=threshold)
    for area, pattern in ((-0.1, "non-negative"), (float("nan"), "finite"), (float("-inf"), "finite")):
        with pytest.raises(ChangeError, match=pattern):
            run_pair(pair, out_name=f"out-a{area}", min_area_ha=area)


# --------------------------------------------------------------------------- #
# previews and bounds
# --------------------------------------------------------------------------- #
def test_preview_bounds_are_real_wgs84_pixel_bounds(pair):
    analysis, _, geojson, out_dir = run_pair(pair)
    from PIL import Image

    bounds = analysis["imagery"]["before"]["bounds"]
    assert analysis["bbox"] == bounds
    for key in ("before", "after", "change"):
        assert analysis["imagery"][key]["bounds"] == bounds
    west, south, east, north = bounds
    assert -180 <= west < east <= 180
    assert -90 <= south < north <= 90
    # the synthetic grid is 1.2 km across in UTM 33N; a degree-wide bbox would betray a
    # projected rectangle being passed off as image pixels
    assert east - west < 0.05 and north - south < 0.05
    with rasterio.open(os.path.join(out_dir, "change.tif")) as src:
        wgs84 = rasterio.warp.transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    assert bounds[0] == pytest.approx(wgs84[0], abs=0.02)
    assert bounds[1] == pytest.approx(wgs84[1], abs=0.02)
    sizes = set()
    for key in ("before", "after", "change"):
        with Image.open(os.path.join(out_dir, analysis["imagery"][key]["path"])) as image:
            assert image.size[0] > 0 and image.size[1] > 0
            sizes.add(image.size)
    assert len(sizes) == 1, "all previews must share one WGS84 grid"
    with open(os.path.join(out_dir, "regions.geojson"), "r", encoding="utf-8") as handle:
        geometry = shape(json.load(handle)["features"][0]["geometry"])
    minx, miny, maxx, maxy = geometry.bounds
    assert west <= minx <= maxx <= east
    assert south <= miny <= maxy <= north
    assert minx > west and maxx < east, "the region must not fill the whole preview extent"


def test_previews_share_one_pooled_stretch_so_global_shift_stays_visible(tmp_path):
    # a known global shift of +3.0103 dB over the whole scene (linear power doubled everywhere)
    before = stable_grid()
    after = before * PATCH_RATIO
    before_path = write_raster(tmp_path / "b.tif", before)
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 5.0, 0.0)
    assert analysis["metrics"]["region_count"] == 0, "a global shift is not a localized change"

    from PIL import Image

    with Image.open(os.path.join(out_dir, "before.png")) as image:
        before_pixels = np.asarray(image.convert("RGBA"), dtype="float64")
    with Image.open(os.path.join(out_dir, "after.png")) as image:
        after_pixels = np.asarray(image.convert("RGBA"), dtype="float64")

    def mean_level(pixels):
        valid = pixels[..., 3] == 255
        assert valid.any()
        return float(pixels[..., 0][valid].mean())

    # one pooled stretch: the before scene sits at the dark end and the after scene at the bright
    # end, so the +3.0103 dB global shift is plainly visible instead of being normalised away
    before_level = mean_level(before_pixels)
    after_level = mean_level(after_pixels)
    assert before_level < 40.0, f"before preview should map to the dark end, got {before_level:.1f}"
    assert after_level > 215.0, f"after preview should map to the bright end, got {after_level:.1f}"
    assert after_level - before_level > 150.0

    # the common scale is recorded, and both previews cite the same one
    limitations = " ".join(analysis["limitations"])
    assert "pooled stretch" in limitations
    assert "2nd-98th percentile" in limitations
    import re

    pattern = r"pooled stretch (-?[0-9.]+) to (-?[0-9.]+) dB"
    before_scale = re.search(pattern, analysis["imagery"]["before"]["label"])
    after_scale = re.search(pattern, analysis["imagery"]["after"]["label"])
    assert before_scale and after_scale
    assert before_scale.group(0) == after_scale.group(0), "both previews must cite one common scale"
    low, high = float(before_scale.group(1)), float(before_scale.group(2))
    assert low == pytest.approx(10.0 * math.log10(BASE_PRICE), abs=0.02)
    assert high == pytest.approx(10.0 * math.log10(BASE_PRICE * PATCH_RATIO), abs=0.02)
    assert low < high


def test_previews_are_really_resampled_not_placeholder(tmp_path):
    before = stable_grid()
    after = change_grid()
    before_path = write_raster(tmp_path / "b.tif", before)
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    run_change_detection(manifest, out_dir, 2.0, 0.0)
    from PIL import Image

    with Image.open(os.path.join(out_dir, "before.png")) as image:
        before_pixels = np.asarray(image.convert("RGBA"))
    with Image.open(os.path.join(out_dir, "after.png")) as image:
        after_pixels = np.asarray(image.convert("RGBA"))
    with Image.open(os.path.join(out_dir, "change.png")) as image:
        change_pixels = np.asarray(image.convert("RGBA"))
    assert not np.array_equal(before_pixels[..., :3], after_pixels[..., :3])
    # the change preview must be coloured, not a flat greyscale copy
    assert change_pixels[..., 0].std() > 0
    assert (change_pixels[..., 3] == 255).any(), "valid pixels must be opaque in the change preview"


# --------------------------------------------------------------------------- #
# API bundle contract v1
# --------------------------------------------------------------------------- #
def test_analysis_json_matches_contract_v1(pair):
    analysis, _, geojson, out_dir = run_pair(pair)
    assert analysis["schema_version"] == 1
    for key in (
        "schema_version",
        "analysis_id",
        "title",
        "bbox",
        "scenes",
        "method",
        "metrics",
        "imagery",
        "demo_region_id",
        "limitations",
    ):
        assert key in analysis
    assert isinstance(analysis["analysis_id"], str) and analysis["analysis_id"]
    assert len(analysis["bbox"]) == 4
    assert len(analysis["scenes"]) == 2
    for scene_entry in analysis["scenes"]:
        assert set(scene_entry) == {
            "id",
            "acquired_at",
            "polarization",
            "beam_mode",
            "orbit_direction",
            "relative_orbit",
            "product_type",
            "source_collection",
            "catalog_url",
        }
        assert scene_entry["acquired_at"].endswith("Z")
        assert scene_entry["source_collection"] == "Radarsat-2_Tropical_Forest_Products"
    method = analysis["method"]
    assert method["quantity"] == "sigma0"
    assert method["units"] == "dB"
    assert method["change_definition"] == "10*log10(after/before)"
    assert method["threshold_db"] == 2.0
    assert method["minimum_area_ha"] == 0.0
    assert isinstance(method["speckle_filter"], str) and "3x3" in method["speckle_filter"]
    assert method["registration"]["status"] == "passed"
    assert method["registration"]["residual_pixels"] == 0.8
    assert isinstance(method["preprocessing"], list) and method["preprocessing"]
    assert any("not measure or correct" in step for step in method["preprocessing"])
    metrics = analysis["metrics"]
    assert set(metrics) == {
        "region_count",
        "total_changed_area_ha",
        "analysis_area_ha",
        "valid_area_ha",
        "not_evaluable_area_ha",
        "scene_count",
    }
    assert metrics["scene_count"] == 2
    assert isinstance(metrics["region_count"], int)
    assert metrics["total_changed_area_ha"] == pytest.approx(
        geojson["features"][0]["properties"]["area_ha"], rel=1e-9
    )
    assert metrics["analysis_area_ha"] > 0.0
    assert metrics["valid_area_ha"] > 0.0
    assert metrics["not_evaluable_area_ha"] >= 0.0
    for key in ("before", "after", "change"):
        entry = analysis["imagery"][key]
        assert entry["path"] == f"{key}.png"
        assert len(entry["bounds"]) == 4
        assert entry["bounds"] == analysis["bbox"], "every preview shares the analysis bbox"
        assert isinstance(entry["label"], str) and entry["label"]
        assert os.path.isfile(os.path.join(out_dir, entry["path"]))
    assert analysis["demo_region_id"] is None
    assert isinstance(analysis["limitations"], list) and len(analysis["limitations"]) >= 2
    joined = " ".join(analysis["limitations"]).lower()
    assert "registration" in joined
    assert "cause" in joined or "deforest" in joined
    assert "not evaluable" in joined
    assert not any(key in analysis for key in ("confidence", "probability", "cause", "validation"))


def test_geojson_feature_properties_match_contract_v1(pair):
    _, analysis, geojson, _ = run_pair(pair)
    assert geojson["type"] == "FeatureCollection"
    for feature in geojson["features"]:
        assert feature["type"] == "Feature"
        assert isinstance(feature["id"], str)
        assert feature["geometry"]["type"] in ("Polygon", "MultiPolygon")
        properties = feature["properties"]
        for key in (
            "region_id",
            "area_ha",
            "change_db",
            "magnitude_db",
            "detected_at",
            "baseline_at",
            "observation_interval",
            "priority_score",
            "priority_units",
            "priority_formula",
            "persistence",
            "historical_anomaly",
            "explanation",
            "time_series",
        ):
            assert key in properties, key
        assert properties["region_id"] == feature["id"]
        assert properties["change_db"] == pytest.approx(properties["magnitude_db"], abs=1e-9)
        assert properties["area_ha"] > 0
        assert properties["magnitude_db"] == pytest.approx(abs(properties["change_db"]), rel=1e-6)
        assert properties["detected_at"] == analysis["scenes"][1]["acquired_at"]
        assert properties["baseline_at"] == analysis["scenes"][0]["acquired_at"]
        assert properties["observation_interval"] == {
            "start": analysis["scenes"][0]["acquired_at"],
            "end": analysis["scenes"][1]["acquired_at"],
        }
        # v1 renamed these fields; the old names must not reappear
        assert "last_observed_unchanged_at" not in properties
        assert "onset_interval" not in properties
        persistence = properties["persistence"]
        assert persistence["status"] == "not_evaluable"
        assert persistence["rate"] is None
        assert persistence["observations_after_detection"] == 0
        assert persistence["changed_observations"] == 0
        assert properties["historical_anomaly"] is None
        assert isinstance(properties["explanation"], str) and properties["explanation"]
        assert "cause" in properties["explanation"].lower()
        # detected_at is an observation time, and the wording must not claim an onset
        assert "onset" not in properties["explanation"].lower()
        assert "not when any event began" in properties["explanation"]
        assert len(properties["time_series"]) == 2
        baseline_point, followup_point = properties["time_series"]
        assert baseline_point["acquired_at"] == analysis["scenes"][0]["acquired_at"]
        assert followup_point["acquired_at"] == analysis["scenes"][1]["acquired_at"]
        assert baseline_point["change_from_baseline_db"] == 0.0
        assert followup_point["change_from_baseline_db"] == pytest.approx(
            followup_point["mean_backscatter_db"] - baseline_point["mean_backscatter_db"], rel=1e-9
        )
        assert 0.0 < baseline_point["valid_fraction"] <= 1.0
        # the ROI is the whole detected region, so its mean sits between the kernel-diluted
        # ring (2.2185 dB) and the untouched core (3.0103 dB)
        delta = followup_point["mean_backscatter_db"] - baseline_point["mean_backscatter_db"]
        assert delta == pytest.approx(2.798, abs=0.01)
        assert 2.2185 < delta < 3.0103
        assert not any(key in properties for key in ("confidence", "probability", "cause"))
    text = json.dumps(geojson).lower()
    for forbidden in ("deforestation detected", "confidence", "probability"):
        assert forbidden not in text


def test_region_ids_and_order_are_stable_across_runs(pair):
    first = run_pair(pair, out_name="run-a")[2]["features"]
    second = run_pair(pair, out_name="run-b")[2]["features"]
    assert [f["id"] for f in first] == [f["id"] for f in second]
    assert [f["properties"]["region_id"] for f in first] == [f["properties"]["region_id"] for f in second]
    scores = [f["properties"]["priority_score"] for f in first]
    assert scores == sorted(scores, reverse=True)
    assert all(f["id"] == f"R{index:03d}" for index, f in enumerate(first, start=1))


def test_change_raster_has_real_nodata_metadata(tmp_path):
    after = change_grid()
    after[34:39, 2:6] = np.nan
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(write_raster(tmp_path / "b.tif", stable_grid()), scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(write_raster(tmp_path / "a.tif", after), scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 0.0)
    with rasterio.open(os.path.join(out_dir, "change.tif")) as src:
        assert math.isnan(src.nodata), "change.tif must declare NaN nodata metadata"
        raw = src.read(1)
        masked = src.read(1, masked=True)
        assert np.isnan(raw).any()
        # rasterio masked reads must mask exactly the not-evaluable pixels and nothing else
        assert np.array_equal(masked.mask, np.isnan(raw))
        assert np.isfinite(masked.data[~masked.mask]).all()
        assert not masked.mask[15:25, 14:23].any()
        assert src.tags()["quantity"] == "change_db"
        assert src.tags()["change_definition"] == "10*log10(after/before)"
    # the masked count is exactly the unevaluable area, reported in the metrics
    unevaluable_cells = int(masked.mask.sum())
    assert unevaluable_cells * PIXEL_SIZE * PIXEL_SIZE / 10_000.0 == pytest.approx(
        analysis["metrics"]["not_evaluable_area_ha"], rel=0.02
    )


def test_mask_raster_is_the_retained_region_mask_with_invalid_nodata(tmp_path):
    # a large patch and a small one; the small patch is below the minimum area
    after = change_grid(extra_small_patch=True)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(write_raster(tmp_path / "b.tif", stable_grid()), scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(write_raster(tmp_path / "a.tif", after), scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 6.0)
    assert analysis["metrics"]["region_count"] == 1

    with rasterio.open(os.path.join(out_dir, "mask.tif")) as src:
        assert src.nodata == -1.0, "invalid must be a distinct nodata sentinel"
        raw = src.read(1)
        masked = src.read(1, masked=True)
        semantics = src.tags()["mask_semantics"]
        assert "retained region" in semantics
        assert set(np.unique(raw).tolist()) == {-1.0, 0.0, 1.0}
        # 0 is a valid class: unchanged evaluable ground must never be masked as nodata
        assert int((raw == 0).sum()) > 0
        assert not masked.mask[raw == 0].any()
        assert masked.mask[raw == 1].sum() == 0
        assert np.array_equal(masked.mask, raw == -1.0)
        retained = raw == 1.0

    # the mask is consistent with the published regions and the reported area: retained cells only
    small_patch = np.zeros_like(retained)
    small_patch[31:35, 31:35] = True  # grid offset of the small scene patch at rows/cols 30..35
    assert not retained[small_patch].any(), "a region dropped by the minimum area filter is not retained"
    one_cell_ha = PIXEL_SIZE * PIXEL_SIZE / 10_000.0
    assert int(retained.sum()) * one_cell_ha == pytest.approx(
        analysis["metrics"]["total_changed_area_ha"], rel=0.01
    )
    with open(os.path.join(out_dir, "regions.geojson"), "r", encoding="utf-8") as handle:
        features = json.load(handle)["features"]
    assert len(features) == 1
    with rasterio.open(os.path.join(out_dir, "change.tif")) as src:
        change = src.read(1)
    # every retained cell is above the threshold, and no non-retained evaluable cell is
    evaluable = np.isfinite(change)
    assert (np.abs(change[retained]) >= analysis["method"]["threshold_db"]).all()
    assert not np.isfinite(change[~evaluable]).any()
    assert analysis["metrics"]["total_changed_area_ha"] <= analysis["metrics"]["valid_area_ha"]


def test_published_bundle_is_complete_and_staging_leaves_nothing(tmp_path):
    out_dir = str(tmp_path / "current")
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    run_change_detection(manifest, out_dir, 2.0, 0.0)
    for name in ("analysis.json", "regions.geojson", "change.tif", "mask.tif", "before.png", "after.png", "change.png"):
        assert os.path.getsize(os.path.join(out_dir, name)) > 0
    leftovers = [name for name in os.listdir(str(tmp_path)) if "staging" in name]
    assert leftovers == []


def test_failed_run_publishes_nothing(tmp_path):
    out_dir = str(tmp_path / "current")
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", change_grid())
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    with pytest.raises(ChangeError):
        run_change_detection(manifest, out_dir, 0.0, 0.0)
    assert not os.path.exists(out_dir) or os.listdir(out_dir) == []


def test_metrics_partition_the_analysis_area(pair):
    analysis, _, geojson, _ = run_pair(pair)
    metrics = analysis["metrics"]
    assert metrics["valid_area_ha"] + metrics["not_evaluable_area_ha"] == pytest.approx(
        metrics["analysis_area_ha"], rel=1e-9
    )
    assert metrics["analysis_area_ha"] == pytest.approx(42 * 42 * PIXEL_SIZE * PIXEL_SIZE / 10_000.0, rel=0.01)
    assert metrics["valid_area_ha"] < metrics["analysis_area_ha"]
    assert metrics["total_changed_area_ha"] <= metrics["valid_area_ha"]
    assert metrics["total_changed_area_ha"] == pytest.approx(
        sum(feature["properties"]["area_ha"] for feature in geojson["features"]), rel=1e-9
    )
    assert any("detected_at is the acquisition" in item for item in analysis["limitations"])


def test_area_partition_holds_when_everything_is_evaluable(tmp_path):
    before = stable_grid()
    after = stable_grid()
    after[5:9, 5:9] = BASE_PRICE * PATCH_RATIO
    before_path = write_raster(tmp_path / "b.tif", before)
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    analysis = run_change_detection(manifest, str(tmp_path / "out"), 2.0, 0.0)
    metrics = analysis["metrics"]
    # the small patch sits inside the valid area, so the identity still holds exactly
    assert metrics["valid_area_ha"] + metrics["not_evaluable_area_ha"] == pytest.approx(
        metrics["analysis_area_ha"], rel=1e-9
    )
    assert metrics["total_changed_area_ha"] <= metrics["valid_area_ha"]


def _mixed_sign_change(positive, negative):
    """One row of change values: `positive` cells at +3.0103 dB, `negative` cells at -3.0103 dB."""
    change = np.full((3, positive + negative + 1), np.nan)
    change[0, :positive] = EXPECTED_DB
    change[0, positive : positive + negative] = -EXPECTED_DB
    component = np.zeros(change.shape, dtype=bool)
    component[0, : positive + negative] = True
    return change, component


def test_signed_median_and_absolute_median_are_distinct_metrics():
    # a balanced region: the signed median is ~0 dB while the median absolute dB stays at the
    # 2x power value, so the two metrics are genuinely different quantities
    change, component = _mixed_sign_change(5, 5)
    signed, magnitude = region_change_statistics(change, component)
    assert signed == pytest.approx(0.0, abs=1e-9)
    assert magnitude == pytest.approx(EXPECTED_DB)
    assert magnitude != signed

    # an unbalanced region keeps its dominant sign in the signed median while the absolute
    # median is unchanged
    change, component = _mixed_sign_change(9, 6)
    signed_unbalanced, magnitude_unbalanced = region_change_statistics(change, component)
    assert signed_unbalanced == pytest.approx(EXPECTED_DB)
    assert magnitude_unbalanced == pytest.approx(EXPECTED_DB)

    # a single-sign region has change_db == magnitude_db, and neither is the mean
    change, component = _mixed_sign_change(15, 0)
    signed_uniform, magnitude_uniform = region_change_statistics(change, component)
    assert signed_uniform == pytest.approx(magnitude_uniform)
    assert signed_uniform == pytest.approx(EXPECTED_DB)

    # nodata cells inside the region geometry are ignored, not counted as 0 dB
    change, component = _mixed_sign_change(9, 6)
    change[0, :2] = np.nan  # leaves 7 positive and 6 negative cells
    signed_nodata, magnitude_nodata = region_change_statistics(change, component)
    assert signed_nodata == pytest.approx(EXPECTED_DB)
    assert magnitude_nodata == pytest.approx(EXPECTED_DB)

    with pytest.raises(ChangeError, match="no finite change values"):
        region_change_statistics(change, np.zeros(change.shape, dtype=bool))


def test_mixed_sign_region_keeps_signs_in_separate_components(tmp_path):
    # brightening and darkening halves are separated by the kernel's ~0 dB seam, so they stay
    # two components, each reporting its own signed median and a shared absolute median
    after = stable_grid()
    after[14:26, 10:19] = BASE_PRICE * PATCH_RATIO
    after[14:26, 19:28] = BASE_PRICE / PATCH_RATIO
    before_path = write_raster(tmp_path / "b.tif", stable_grid())
    after_path = write_raster(tmp_path / "a.tif", after)
    manifest = write_manifest(
        tmp_path / "m.json",
        scene(before_path, scene_id="A", acquired_at=BASELINE_ACQUIRED),
        scene(after_path, scene_id="B", acquired_at=FOLLOWUP_ACQUIRED),
    )
    out_dir = str(tmp_path / "out")
    analysis = run_change_detection(manifest, out_dir, 2.0, 0.0)
    with open(os.path.join(out_dir, "regions.geojson"), "r", encoding="utf-8") as handle:
        features = json.load(handle)["features"]
    assert len(features) == 2
    by_id = {feature["id"]: feature["properties"] for feature in features}
    assert by_id["R001"]["change_db"] == pytest.approx(EXPECTED_DB, abs=1e-6)
    assert by_id["R002"]["change_db"] == pytest.approx(-EXPECTED_DB, abs=1e-6)
    for properties in by_id.values():
        assert properties["magnitude_db"] == pytest.approx(EXPECTED_DB, abs=1e-6)
        assert properties["magnitude_db"] == pytest.approx(abs(properties["change_db"]), abs=1e-6)
        assert properties["priority_score"] == pytest.approx(
            properties["magnitude_db"] * math.sqrt(properties["area_ha"]), rel=1e-9
        )
    assert analysis["metrics"]["total_changed_area_ha"] == pytest.approx(
        sum(properties["area_ha"] for properties in by_id.values()), rel=1e-9
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "processing.change", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_cli_publishes_bundle_and_reports_metrics(pair):
    out_dir = str(pair["tmp_path"] / "cli")
    result = _cli(
        ["--manifest", pair["manifest"], "--out", out_dir, "--threshold-db", "2.0", "--min-area-ha", "0.5"],
        REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "region(s)" in result.stdout
    with open(os.path.join(out_dir, "analysis.json"), "r", encoding="utf-8") as handle:
        analysis = json.load(handle)
    assert analysis["method"]["threshold_db"] == 2.0
    assert analysis["method"]["minimum_area_ha"] == 0.5


def test_cli_reports_invalid_parameters_without_traceback(pair):
    out_dir = str(pair["tmp_path"] / "cli-bad")
    result = _cli(
        ["--manifest", pair["manifest"], "--out", out_dir, "--threshold-db", "-1", "--min-area-ha", "0"],
        REPO_ROOT,
    )
    assert result.returncode == 2
    assert "strictly positive" in result.stderr
    assert "Traceback" not in result.stderr
    assert not os.path.exists(out_dir)


def test_cli_requires_all_arguments(pair):
    result = _cli(["--manifest", pair["manifest"]], REPO_ROOT)
    assert result.returncode != 0
