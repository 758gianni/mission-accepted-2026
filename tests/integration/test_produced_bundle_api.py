"""End-to-end integration: ``processing.change`` output -> ``backend.create_app`` -> HTTP.

These tests never hand-write a bundle. Every response checked here comes from a
bundle that the real producer produced from *generated* prepared rasters inside
pytest ``tmp_path`` directories, served by the real FastAPI application over
``TestClient``. There are no real RADARSAT-2 products in this repository, and
nothing generated here may be published as if it were real data: every fixture
is synthetic, marked as such in its scene metadata, and deleted with its
temporary directory.

They also assert the negative space the contract promises: unavailable
persistence stays ``null`` instead of becoming ``0.0``, no confidence or causal
attribution is ever invented, area totals reconcile, the three previews share
one grid, and a corrupted bundle is reported as an error and reloaded after
repair without restarting the service.

Skip policy
-----------
The producer (``processing.change``) and the API (``backend.app``) are merged
independently by the lead. Until both are present in the checkout this module
skips explicitly with a message naming the missing implementation; a skip is
reported as a skip, never as a pass.
"""

from __future__ import annotations

import copy
import io
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:  # dependencies owned by the shared integration environment
    import rasterio
    from fastapi.testclient import TestClient
    from PIL import Image
    from pyproj import Geod
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds
    from scipy import ndimage
    from shapely.geometry import Polygon
    from shapely.geometry import shape as shapely_shape
except ImportError as error:  # pragma: no cover - environment dependent
    pytest.skip(
        "integration dependencies (rasterio, fastapi, pillow, pyproj, scipy, shapely) "
        f"are unavailable: {error}",
        allow_module_level=True,
    )

_IMPLEMENTATION_SKIP = (
    "{name} is not present in this checkout ({error}). These integration tests run "
    "after the lead merges the producer and the API; the skip is a missing "
    "implementation, not a passing test."
)
try:
    from backend.app import create_app
    from backend.validation import AREA_TOLERANCE_HA
except ImportError as error:  # pragma: no cover - depends on merge state
    pytest.skip(_IMPLEMENTATION_SKIP.format(name="backend.app (API)", error=error), allow_module_level=True)

try:
    from processing.change import ChangeError, run_change_detection
except ImportError as error:  # pragma: no cover - depends on merge state
    pytest.skip(
        _IMPLEMENTATION_SKIP.format(name="processing.change (producer)", error=error),
        allow_module_level=True,
    )


# --------------------------------------------------------------------------- #
# synthetic prepared-scene fixtures (generated in tests only, never real data)
# --------------------------------------------------------------------------- #
CRS_UTM = CRS.from_epsg(32633)
PIXEL_SIZE = 30.0
ORIGIN_X = 499_980.0  # a multiple of PIXEL_SIZE so the reference grid aligns with the fixture
ORIGIN_Y = 4_000_020.0
GRID_SIZE = 40
BASE_POWER = 0.2
PATCH_RATIO = 2.0  # +3.0103 dB, comfortably above a 2 dB threshold
PATCH_ROWS, PATCH_COLUMNS = slice(12, 24), slice(12, 24)  # 12x12 cells = 12.6101 ha outer ring; 11.1689 ha reported once the hole is subtracted (delta 1.4412 ha)
TINY_ROWS, TINY_COLUMNS = slice(30, 33), slice(30, 33)  # 3x3 = 0.4504 ha served
HOLE_ROWS, HOLE_COLUMNS = slice(15, 19), slice(15, 19)  # unchanged block inside the patch
MISSING_ROWS, MISSING_COLUMNS = slice(2, 8), slice(2, 8)  # isolated nodata block, outside the patch

#: Written with a "+00:00" offset on purpose: the served document must normalise
#: to a "Z" suffix instead of echoing the input spelling.
BASELINE_ACQUIRED = "2026-03-04T10:15:00+00:00"
FOLLOWUP_ACQUIRED = "2026-09-19T10:15:00Z"
BASELINE_ISO = "2026-03-04T10:15:00Z"
FOLLOWUP_ISO = "2026-09-19T10:15:00Z"

THRESHOLD_DB = 2.0
IMAGERY_KEYS = ("before", "after", "change")
GEOD = Geod(ellps="WGS84")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _write_raster(path: Path, data: np.ndarray, *, nodata: float | None = -9999.0) -> str:
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": CRS_UTM,
        "transform": from_origin(ORIGIN_X, ORIGIN_Y, PIXEL_SIZE, PIXEL_SIZE),
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype("float32"), 1)
        dst.update_tags(
            quantity="sigma0",
            units="linear_power",
            representation="linear_power",
            note="synthetic test fixture, not a real prepared product",
        )
    return str(path)


def _stable_grid() -> np.ndarray:
    return np.full((GRID_SIZE, GRID_SIZE), BASE_POWER, dtype="float64")


def _followup_grid(*, with_hole: bool = False, with_tiny: bool = False, with_missing: bool = False) -> np.ndarray:
    data = _stable_grid()
    data[PATCH_ROWS, PATCH_COLUMNS] = BASE_POWER * PATCH_RATIO
    if with_tiny:
        data[TINY_ROWS, TINY_COLUMNS] = BASE_POWER * PATCH_RATIO
    if with_hole:
        # unchanged (not nodata) block: evaluable but not changed, so it must be
        # preserved as an interior ring of the region polygon
        data[HOLE_ROWS, HOLE_COLUMNS] = BASE_POWER
    if with_missing:
        # genuinely missing data (nodata) outside the changed patch
        data[MISSING_ROWS, MISSING_COLUMNS] = -9999.0
    return data


def _scene(path: str, *, scene_id: str, acquired_at: str, product_type: str) -> dict[str, Any]:
    return {
        "id": scene_id,
        "acquired_at": acquired_at,
        "polarization": "C",
        "beam_mode": "S",
        "orbit_direction": "DESCENDING",
        "relative_orbit": 12345,
        "product_type": product_type,
        "source_collection": "Radarsat-2_Tropical_Forest_Products",
        "catalog_url": f"https://catalogue.example.invalid/synthetic/{scene_id}",
        "path": path,
        "radiometry": {
            "quantity": "sigma0",
            "representation": "linear_power",
            "calibration": "verified",
            "geocoding": "verified",
            "terrain_correction": "verified",
            "processing_steps": ["synthetic integration fixture: not a real prepared product"],
        },
    }


def _write_manifest(path: Path, before: str, after: str) -> str:
    manifest = {
        "schema_version": 1,
        "scenes": [
            _scene(before, scene_id="SYNTH-BEFORE", acquired_at=BASELINE_ACQUIRED, product_type="SLC"),
            _scene(after, scene_id="SYNTH-AFTER", acquired_at=FOLLOWUP_ACQUIRED, product_type="GRD"),
        ],
        "registration": {
            "status": "passed",
            "residual_pixels": 0.8,
            "diagnostic": "synthetic integration fixture diagnostic, not real registration evidence",
        },
    }
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return str(path)


def _produce(root: Path, name: str, *, followup: np.ndarray | None = None, min_area_ha: float = 0.0) -> Path:
    """Run the real producer over generated rasters and return the bundle directory."""
    workdir = root / name
    workdir.mkdir(parents=True, exist_ok=True)
    before_path = _write_raster(workdir / "before.tif", _stable_grid())
    after_grid = _stable_grid() if followup is None else followup
    after_path = _write_raster(workdir / "after.tif", after_grid)
    manifest_path = _write_manifest(workdir / "manifest.json", before_path, after_path)
    bundle_dir = workdir / "bundle"
    run_change_detection(manifest_path, str(bundle_dir), THRESHOLD_DB, min_area_ha)
    return bundle_dir


@pytest.fixture(scope="module")
def produced(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One producer run per scenario, shared by the read-only tests."""
    root = tmp_path_factory.mktemp("produced-bundles")
    return {
        "changed": _produce(root, "changed", followup=_followup_grid()),
        "unchanged": _produce(root, "unchanged"),
        "holes": _produce(root, "holes", followup=_followup_grid(with_hole=True, with_missing=True)),
        "tiny_filtered": _produce(root, "tiny-filtered", followup=_followup_grid(with_tiny=True), min_area_ha=0.5),
        "tiny_kept": _produce(root, "tiny-kept", followup=_followup_grid(with_tiny=True), min_area_ha=0.0),
    }


def _client(bundle_dir: Path | str) -> TestClient:
    return TestClient(create_app(bundle_dir))


def _get_json(client: TestClient, url: str) -> Any:
    response = client.get(url)
    assert response.status_code == 200, f"GET {url} -> {response.status_code}: {response.text[:400]}"
    return response.json()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _geodesic_area_ha(geometry: Any) -> float:
    return abs(GEOD.geometry_area_perimeter(geometry)[0]) / 10_000.0


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def client_url_is_absolute(url: str) -> bool:
    """True when the declared URL leaves this origin (an absolute or //-prefixed URL)."""
    parts = urlsplit(url)
    return bool(parts.scheme or parts.netloc)


def _assert_declared_urls(served: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The API declares its own preview URLs; check shape, not a hardcoded spelling.

    Each declared URL must be same-origin, address exactly its own imagery key,
    and name the analysis generation it belongs to, so a client can never be
    handed a preview from a superseded run.
    """
    assert set(served["imagery"]) == set(IMAGERY_KEYS)
    parsed: dict[str, dict[str, Any]] = {}
    for key in IMAGERY_KEYS:
        entry = served["imagery"][key]
        parts = urlsplit(entry["url"])
        assert not parts.scheme and not parts.netloc, f"{entry['url']!r} must be a same-origin URL"
        assert parts.path == f"/api/imagery/{key}", f"{entry['url']!r} must address the {key} preview"
        query = parse_qs(parts.query)
        assert set(query) == {"analysis_id"}, f"{entry['url']!r} must carry exactly analysis_id"
        assert query["analysis_id"] == [served["analysis_id"]], (
            f"{key}: the declared URL must name analysis {served['analysis_id']}, got {entry['url']!r}"
        )
        # the route segment is the imagery key; the file on disk is "<key>.png".
        # These are deliberately different names and must not be conflated.
        assert Path(parts.path).name == key, f"{entry['url']!r} must address the {key} route"
        assert entry["path"] == f"{key}.png", f"{key}: the declared file name must be {key}.png"
        parsed[key] = {"url": entry["url"], "parts": parts, "query": query, "entry": entry}
    return parsed


def _evaluable_cells(bundle_dir: Path) -> np.ndarray:
    """Cells of the published change raster that carry a dB observation."""
    with rasterio.open(bundle_dir / "change.tif") as src:
        return np.isfinite(src.read(1))


def _invalid_share(metrics: dict[str, Any]) -> float:
    """Share of the analysis area the metrics report as not evaluable."""
    return float(metrics["not_evaluable_area_ha"]) / float(metrics["analysis_area_ha"])


def _walk_keys(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


def _walk_strings(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item)
    elif isinstance(node, str):
        yield node


# --------------------------------------------------------------------------- #
# readiness lifecycle: awaiting_analysis -> produced -> ready, without a restart
# --------------------------------------------------------------------------- #
def test_status_reports_awaiting_then_ready_for_a_produced_bundle(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "current"
    bundle_dir.mkdir()
    client = _client(bundle_dir)

    waiting = _get_json(client, "/api/status")
    assert waiting["state"] == "awaiting_analysis"
    assert waiting["analysis_id"] is None
    for url in ("/api/analysis", "/api/regions", "/api/imagery/before"):
        assert client.get(url).status_code == 404, f"{url} must be 404 before an analysis exists"

    # the producer publishes into the same directory the app is already watching
    run_change_detection(
        _write_manifest(
            tmp_path / "manifest.json",
            _write_raster(tmp_path / "before.tif", _stable_grid()),
            _write_raster(tmp_path / "after.tif", _followup_grid()),
        ),
        str(bundle_dir),
        THRESHOLD_DB,
        0.0,
    )

    ready = _get_json(client, "/api/status")
    assert ready["state"] == "ready"
    assert ready["schema_version"] == 1
    assert ready["scene_count"] == 2
    assert ready["analysis_id"] == _read_json(bundle_dir / "analysis.json")["analysis_id"]
    assert _get_json(client, "/health") == {"status": "ok", "service": "forestwatch-api"}
    assert client.get("/api/analysis").status_code == 200


# --------------------------------------------------------------------------- #
# changed pair: producer output served verbatim
# --------------------------------------------------------------------------- #
def test_changed_pair_analysis_is_served_verbatim(produced: dict[str, Path]) -> None:
    bundle_dir = produced["changed"]
    on_disk = _read_json(bundle_dir / "analysis.json")
    served = _get_json(_client(bundle_dir), "/api/analysis")

    # the only field the API may add is the declared preview URL; nothing else is
    # re-serialised, rounded or invented
    without_urls = copy.deepcopy(served)
    for key in IMAGERY_KEYS:
        assert "url" in without_urls["imagery"][key]
        without_urls["imagery"][key].pop("url")
    assert without_urls == on_disk

    declared = _assert_declared_urls(served)
    for key in IMAGERY_KEYS:
        assert client_url_is_absolute(declared[key]["url"]) is False

    assert served["schema_version"] == 1
    assert served["method"]["threshold_db"] == THRESHOLD_DB
    assert served["method"]["change_definition"] == "10*log10(after/before)"
    assert [scene["id"] for scene in served["scenes"]] == ["SYNTH-BEFORE", "SYNTH-AFTER"]
    assert served["metrics"]["region_count"] >= 1
    assert served["demo_region_id"] is None or served["demo_region_id"]
    assert served["limitations"], "a produced analysis must carry its limitations"


def test_changed_pair_regions_are_served_and_addressable(produced: dict[str, Path]) -> None:
    bundle_dir = produced["changed"]
    client = _client(bundle_dir)
    collection = _get_json(client, "/api/regions")
    geojson = _read_json(bundle_dir / "regions.geojson")

    assert collection == geojson
    assert collection["type"] == "FeatureCollection"
    features = collection["features"]
    assert len(features) == _get_json(client, "/api/analysis")["metrics"]["region_count"]

    ids = [feature["id"] for feature in features]
    assert len(set(ids)) == len(ids)
    for feature in features:
        assert feature["properties"]["region_id"] == feature["id"]
        single = _get_json(client, f"/api/regions/{feature['id']}")
        assert single == feature
        assert _get_json(client, f"/api/regions/{feature['id']}").get("id") == feature["id"]

    missing = client.get("/api/regions/R999")
    assert missing.status_code == 404
    assert "R999" in missing.json()["detail"]
    assert _get_json(client, "/openapi.json")["info"]["title"] == "ForestWatch result API"


def test_detected_region_matches_the_generated_patch(produced: dict[str, Path]) -> None:
    """The served geometry must be the generated patch, in WGS84, with a dB sign."""
    bundle_dir = produced["changed"]
    features = _get_json(_client(bundle_dir), "/api/regions")["features"]
    assert len(features) == 1
    feature = features[0]
    properties = feature["properties"]

    # the fixture raises linear power by 2x inside rows/columns 12..23
    assert properties["change_db"] == pytest.approx(10.0 * math.log10(PATCH_RATIO), abs=0.6)
    assert properties["magnitude_db"] >= abs(properties["change_db"])
    assert properties["priority_score"] == pytest.approx(
        properties["magnitude_db"] * math.sqrt(properties["area_ha"]), rel=1e-9
    )
    assert properties["priority_units"] == "dB sqrt(ha)"
    assert properties["priority_formula"] == "magnitude_db * sqrt(area_ha)"

    geometry = shapely_shape(feature["geometry"])
    assert geometry.is_valid, "a served region polygon must be valid"
    assert geometry.geom_type == "Polygon"
    # UTM 33N around easting 500000, northing 4000020 -> near 15 degrees east
    west, south, east, north = geometry.bounds
    assert 14.0 < west < 16.0 and 35.0 < south < 37.0
    assert geometry.area > 0
    # the patch must sit inside the analysis extent, not fill it
    bbox = _get_json(_client(bundle_dir), "/api/analysis")["bbox"]
    assert bbox[0] < west and east < bbox[2] and bbox[1] < south and north < bbox[3]


# --------------------------------------------------------------------------- #
# area reconciliation in hectares
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario", ["changed", "unchanged", "holes", "tiny_filtered", "tiny_kept"])
def test_served_areas_reconcile(produced: dict[str, Path], scenario: str) -> None:
    bundle_dir = produced[scenario]
    client = _client(bundle_dir)
    analysis = _get_json(client, "/api/analysis")
    features = _get_json(client, "/api/regions")["features"]
    metrics = analysis["metrics"]

    region_total = sum(feature["properties"]["area_ha"] for feature in features)
    assert metrics["total_changed_area_ha"] == pytest.approx(region_total, abs=AREA_TOLERANCE_HA), (
        "the declared changed area must equal the sum of the served region areas"
    )
    assert metrics["region_count"] == len(features)
    partition = metrics["valid_area_ha"] + metrics["not_evaluable_area_ha"]
    assert partition == pytest.approx(metrics["analysis_area_ha"], abs=AREA_TOLERANCE_HA), (
        "valid + not evaluable must equal the analysis area"
    )
    assert metrics["total_changed_area_ha"] <= metrics["valid_area_ha"] + AREA_TOLERANCE_HA
    assert metrics["scene_count"] == len(analysis["scenes"]) == 2

    for feature in features:
        properties = feature["properties"]
        assert properties["area_ha"] == pytest.approx(
            _geodesic_area_ha(shapely_shape(feature["geometry"])), abs=1e-3
        ), "each served area_ha must be the geodesic area of the geometry it ships with"


def test_holes_change_reported_area_not_the_polygon_area(produced: dict[str, Path]) -> None:
    bundle_dir = produced["holes"]
    client = _client(bundle_dir)
    features = _get_json(client, "/api/regions")["features"]
    assert len(features) == 1
    geometry = shapely_shape(features[0]["geometry"])

    assert len(geometry.interiors) == 1, "the unchanged block inside the patch must survive as a hole"
    area_ha = features[0]["properties"]["area_ha"]
    outer_only_ha = _geodesic_area_ha(Polygon(geometry.exterior))
    assert area_ha < outer_only_ha, "area_ha must subtract the interior hole"
    # the hole is the generated 4x4 unchanged block (16 cells of 900 m2)
    assert outer_only_ha - area_ha == pytest.approx(16 * PIXEL_SIZE * PIXEL_SIZE / 10_000.0, rel=0.05)
    assert area_ha == pytest.approx(_geodesic_area_ha(geometry), abs=1e-3)


# --------------------------------------------------------------------------- #
# no-change pair
# --------------------------------------------------------------------------- #
def test_no_change_pair_serves_an_empty_region_set(produced: dict[str, Path]) -> None:
    bundle_dir = produced["unchanged"]
    client = _client(bundle_dir)
    analysis = _get_json(client, "/api/analysis")
    collection = _get_json(client, "/api/regions")

    assert analysis["metrics"]["region_count"] == 0
    assert analysis["metrics"]["total_changed_area_ha"] == 0.0
    assert collection["features"] == []
    assert client.get("/api/regions/R001").status_code == 404
    # the previews are still produced for an unchanged pair
    for key in ("before", "after", "change"):
        assert client.get(f"/api/imagery/{key}").status_code == 200
    # The reference grid is snapped outward to whole pixels, so it carries a
    # one-cell border with no source support. That band, and only that band, is
    # not evaluable: the reported hectares must match the published raster, and
    # the interior must be completely evaluable.
    evaluable = _evaluable_cells(bundle_dir)
    invalid = ~evaluable
    assert invalid.any(), "the snapped analysis grid has a border band without source support"
    assert not invalid[2:-2, 2:-2].any(), "a complete pair must leave no interior cell unevaluable"
    assert _invalid_share(analysis["metrics"]) == pytest.approx(
        float(invalid.sum()) / float(evaluable.size), rel=1e-3
    )
    assert analysis["metrics"]["valid_area_ha"] + analysis["metrics"]["not_evaluable_area_ha"] == pytest.approx(
        analysis["metrics"]["analysis_area_ha"], abs=AREA_TOLERANCE_HA
    )


# --------------------------------------------------------------------------- #
# missing / nodata data
# --------------------------------------------------------------------------- #
def test_missing_support_is_reported_as_not_evaluable(produced: dict[str, Path]) -> None:
    bundle_dir = produced["holes"]
    analysis = _get_json(_client(bundle_dir), "/api/analysis")
    metrics = analysis["metrics"]

    assert metrics["not_evaluable_area_ha"] > 0.0, "the nodata block must be reported as not evaluable"
    assert metrics["valid_area_ha"] < metrics["analysis_area_ha"]
    assert metrics["total_changed_area_ha"] <= metrics["valid_area_ha"] + AREA_TOLERANCE_HA
    # the whole bundle still validates, so the API keeps serving it
    assert _get_json(_client(bundle_dir), "/api/status")["state"] == "ready"

    # the reported hectares must be the share of cells the published change raster
    # actually leaves without an observation, including the missing interior block
    evaluable = _evaluable_cells(bundle_dir)
    invalid = ~evaluable
    assert invalid[2:-2, 2:-2].any(), "the nodata block must be missing inside the analysis extent"
    assert _invalid_share(metrics) == pytest.approx(
        float(invalid.sum()) / float(evaluable.size), rel=1e-3
    ), "not_evaluable_area_ha must match the unevaluable cells of change.tif"
    with rasterio.open(bundle_dir / "mask.tif") as src:
        mask = src.read(1)
        nodata_value = src.nodata
    assert nodata_value is not None, "the derived mask must declare real nodata metadata"
    assert (mask == nodata_value).any(), "not-evaluable cells must be nodata in mask.tif"
    assert (mask != nodata_value).any(), "evaluable cells must not be nodata"


def test_previews_are_transparent_where_support_is_missing(produced: dict[str, Path]) -> None:
    bundle_dir = produced["holes"]
    response = _client(bundle_dir).get("/api/imagery/change")
    assert response.status_code == 200
    image = Image.open(io.BytesIO(response.content)).convert("RGBA")
    alpha = np.asarray(image)[:, :, 3]
    with rasterio.open(bundle_dir / "change.tif") as src:
        evaluable = np.isfinite(src.read(1))
    # This fixture is far below the producer's preview cap, so the preview grid
    # is the reference grid one to one; that lets the PNG alpha be compared with
    # the published change raster cell by cell.
    assert alpha.shape == evaluable.shape, (
        f"preview grid {alpha.shape} does not match the change raster {evaluable.shape}"
    )
    # The preview is bilinear-resampled, so only cells whose whole 3x3 support
    # agrees are compared: interior evaluable cells opaque, deep nodata cells clear.
    window = (slice(3, -3), slice(3, -3))
    core = evaluable[window]
    interior = ndimage.binary_erosion(core, np.ones((3, 3), bool), border_value=0)
    deep_missing = ~ndimage.binary_dilation(core, np.ones((3, 3), bool), border_value=0)
    assert interior.any() and deep_missing.any()
    assert (alpha[window][interior] == 255).all(), "evaluable preview cells must be opaque"
    assert (alpha[window][deep_missing] == 0).all(), "unsupported preview cells must be transparent"


# --------------------------------------------------------------------------- #
# minimum-area filtering
# --------------------------------------------------------------------------- #
def test_minimum_area_filter_removes_small_regions(produced: dict[str, Path]) -> None:
    kept = _get_json(_client(produced["tiny_kept"]), "/api/regions")["features"]
    filtered = _get_json(_client(produced["tiny_filtered"]), "/api/regions")["features"]

    assert len(kept) == 2, "without an area filter both generated patches are reported"
    assert len(filtered) == 1, "the 0.081 ha patch must be dropped by min_area_ha=0.5"
    areas = sorted(feature["properties"]["area_ha"] for feature in kept)
    assert areas[0] < 0.5 <= areas[1]
    assert filtered[0]["properties"]["area_ha"] >= 0.5

    unfiltered_analysis = _get_json(_client(produced["tiny_kept"]), "/api/analysis")
    filtered_analysis = _get_json(_client(produced["tiny_filtered"]), "/api/analysis")
    assert filtered_analysis["method"]["minimum_area_ha"] == 0.5
    assert unfiltered_analysis["method"]["minimum_area_ha"] == 0.0
    # the analysis id must record the parameter, so two thresholds are two analyses
    assert unfiltered_analysis["analysis_id"] != filtered_analysis["analysis_id"]
    assert (
        filtered_analysis["metrics"]["total_changed_area_ha"]
        < unfiltered_analysis["metrics"]["total_changed_area_ha"]
    )


# --------------------------------------------------------------------------- #
# previews: real PNG bytes, one shared grid
# --------------------------------------------------------------------------- #
def test_imagery_serves_the_real_generated_pngs(produced: dict[str, Path]) -> None:
    bundle_dir = produced["changed"]
    client = _client(bundle_dir)
    analysis = _get_json(client, "/api/analysis")
    declared = _assert_declared_urls(analysis)

    sizes = set()
    for key in IMAGERY_KEYS:
        entry = analysis["imagery"][key]
        response = client.get(declared[key]["url"])
        assert response.status_code == 200, f"{key}: GET {declared[key]['url']} -> {response.status_code}"
        assert response.headers["content-type"].startswith("image/png")
        on_disk = (bundle_dir / entry["path"]).read_bytes()
        assert response.content == on_disk, "the served preview must be the produced file byte for byte"
        assert response.content.startswith(PNG_MAGIC)
        image = Image.open(io.BytesIO(response.content))
        assert image.mode == "RGBA"
        assert image.size[0] > 1 and image.size[1] > 1, "a preview must be a real image, not a stub"
        sizes.add(image.size)

    assert len(sizes) == 1, "all three previews must share one grid so they overlay exactly"
    # the bounds a preview is declared with are the bounds it is served under
    for key in IMAGERY_KEYS:
        assert analysis["imagery"][key]["bounds"] == analysis["bbox"]
    assert client.get("/api/imagery/sideways").status_code == 404


def test_preview_bounds_equal_the_analysis_bbox(produced: dict[str, Path]) -> None:
    bundle_dir = produced["changed"]
    analysis = _get_json(_client(bundle_dir), "/api/analysis")
    bbox = analysis["bbox"]
    assert len(bbox) == 4 and bbox[0] < bbox[2] and bbox[1] < bbox[3]

    for key in IMAGERY_KEYS:
        bounds = analysis["imagery"][key]["bounds"]
        assert bounds == bbox, f"imagery.{key}.bounds must equal the analysis bbox exactly"
        assert analysis["imagery"][key]["label"].strip()

    # the declared bounds must also match the extent of the published rasters
    with rasterio.open(bundle_dir / "change.tif") as src:
        assert src.crs.to_epsg() == 32633
        west, south, east, north = src.bounds

    raster_bounds = list(transform_bounds(32633, 4326, west, south, east, north, densify_pts=21))
    assert raster_bounds == pytest.approx(bbox, abs=1e-6)


def test_before_and_after_previews_share_one_stretch(produced: dict[str, Path]) -> None:
    """A pooled stretch is what makes the two dates comparable; check it is declared."""
    imagery = _get_json(_client(produced["changed"]), "/api/analysis")["imagery"]
    labels = {key: imagery[key]["label"] for key in ("before", "after")}
    stretches = {
        label.rsplit("pooled stretch ", 1)[1] for label in labels.values() if "pooled stretch" in label
    }
    assert stretches, f"before/after labels must declare their stretch, got {labels}"
    assert len(stretches) == 1, "the two previews must share a single pooled stretch"


# --------------------------------------------------------------------------- #
# UTC timestamps
# --------------------------------------------------------------------------- #
def test_served_dates_are_utc_normalised_and_distinct(produced: dict[str, Path]) -> None:
    bundle_dir = produced["changed"]
    client = _client(bundle_dir)
    analysis = _get_json(client, "/api/analysis")
    scenes = analysis["scenes"]

    stamps = [scene["acquired_at"] for scene in scenes]
    for value in stamps:
        assert value.endswith("Z"), f"{value!r} must be a Z-suffixed UTC timestamp"
        assert _parse_utc(value).utcoffset() == timedelta(0)
    # the manifest wrote the baseline with a "+00:00" offset: it must be normalised, not echoed
    assert stamps[0] == BASELINE_ISO
    assert stamps[1] == FOLLOWUP_ISO
    assert _parse_utc(stamps[0]) < _parse_utc(stamps[1])
    assert len({_parse_utc(value).date() for value in stamps}) == 2

    for feature in _get_json(client, "/api/regions")["features"]:
        properties = feature["properties"]
        assert properties["baseline_at"] == BASELINE_ISO
        assert properties["detected_at"] == FOLLOWUP_ISO
        assert properties["observation_interval"] == {"start": BASELINE_ISO, "end": FOLLOWUP_ISO}
        assert _parse_utc(properties["baseline_at"]) < _parse_utc(properties["detected_at"])
        series = properties["time_series"]
        assert [point["acquired_at"] for point in series] == [BASELINE_ISO, FOLLOWUP_ISO]
        assert all(0.0 <= point["valid_fraction"] <= 1.0 for point in series)

    # a non-UTC acquisition time must be refused by the producer, not silently shifted
    bad_manifest = json.loads((bundle_dir.parent / "manifest.json").read_text(encoding="utf-8"))
    bad_manifest["scenes"][0]["acquired_at"] = "2026-03-04T11:15:00+01:00"
    bad_path = bundle_dir.parent / "non_utc.json"
    bad_path.write_text(json.dumps(bad_manifest, indent=2), encoding="utf-8")

    with pytest.raises(ChangeError, match="UTC"):
        run_change_detection(str(bad_path), str(bundle_dir.parent / "rejected"), THRESHOLD_DB, 0.0)
    assert not (bundle_dir.parent / "rejected").exists()


# --------------------------------------------------------------------------- #
# unavailable values stay unavailable
# --------------------------------------------------------------------------- #
def test_persistence_and_anomaly_are_served_as_unavailable(produced: dict[str, Path]) -> None:
    bundle_dir = produced["changed"]
    response = _client(bundle_dir).get("/api/regions")
    assert response.status_code == 200
    compact = "".join(response.text.split())
    assert '"rate":null' in compact, "an unavailable rate must be null, not 0"
    assert '"historical_anomaly":null' in compact
    assert '"rate":0' not in compact, "an unavailable rate must never be serialised as zero"

    for feature in response.json()["features"]:
        persistence = feature["properties"]["persistence"]
        assert persistence["status"] == "not_evaluable"
        assert persistence["rate"] is None
        assert persistence["observations_after_detection"] == 0
        assert persistence["changed_observations"] == 0
        assert feature["properties"]["historical_anomaly"] is None


def test_no_confidence_or_causal_attribution_is_served(produced: dict[str, Path]) -> None:
    client = _client(produced["changed"])
    documents = {
        "analysis": _get_json(client, "/api/analysis"),
        "regions": _get_json(client, "/api/regions"),
    }
    forbidden_keys = {
        "confidence",
        "confidence_score",
        "probability",
        "likelihood",
        "deforestation",
        "deforestation_probability",
        "forest_loss",
        "cause",
        "severity_score",
        "risk",
    }
    for name, document in documents.items():
        offending = sorted(set(_walk_keys(document)) & forbidden_keys)
        assert not offending, f"{name} must not expose {offending}"

    for feature in documents["regions"]["features"]:
        explanation = feature["properties"]["explanation"]
        assert any(
            phrase in explanation for phrase in ("not determined", "undetermined", "cannot separate")
        ), f"a served explanation must disclaim causation, got {explanation[:200]}"
        for claim in ("% confident", "confidence of", "probable deforestation", "confirmed deforestation"):
            assert claim not in explanation.lower()

    for limitation in documents["analysis"]["limitations"]:
        for claim in ("% confident", "confidence of", "confirmed deforestation"):
            assert claim not in limitation.lower()


# --------------------------------------------------------------------------- #
# corruption and repair
# --------------------------------------------------------------------------- #
def _bundle_copy(tmp_path: Path, source: Path) -> Path:
    target = tmp_path / "bundle"
    target.mkdir(parents=True)
    for item in sorted(source.iterdir()):
        if item.is_file():
            target.joinpath(item.name).write_bytes(item.read_bytes())
    return target


def test_corrupted_analysis_is_an_error_and_repair_is_reloaded(tmp_path: Path, produced: dict[str, Path]) -> None:
    bundle_dir = _bundle_copy(tmp_path, produced["changed"])
    client = _client(bundle_dir)
    assert _get_json(client, "/api/status")["state"] == "ready"
    analysis_before = _get_json(client, "/api/analysis")

    original = (bundle_dir / "analysis.json").read_bytes()
    (bundle_dir / "analysis.json").write_text("{ this is not json", encoding="utf-8")
    broken = _get_json(client, "/api/status")
    assert broken["state"] == "error"
    assert "analysis.json" in broken["message"]
    for url in ("/api/analysis", "/api/regions", "/api/regions/R001", "/api/imagery/change"):
        response = client.get(url)
        assert response.status_code == 503, f"{url} must be 503 while the bundle is broken"
        assert "detail" in response.json()
    # health stays useful even when the bundle is broken
    assert _get_json(client, "/health")["status"] == "ok"

    (bundle_dir / "analysis.json").write_bytes(original)
    repaired = _get_json(client, "/api/status")
    assert repaired["state"] == "ready"
    assert _get_json(client, "/api/analysis") == analysis_before


def test_inconsistent_metrics_are_refused_then_repaired(tmp_path: Path, produced: dict[str, Path]) -> None:
    bundle_dir = _bundle_copy(tmp_path, produced["changed"])
    client = _client(bundle_dir)
    original = json.loads((bundle_dir / "analysis.json").read_text(encoding="utf-8"))

    tampered = copy.deepcopy(original)
    tampered["metrics"]["region_count"] = 99
    (bundle_dir / "analysis.json").write_text(json.dumps(tampered, indent=2), encoding="utf-8")
    status = _get_json(client, "/api/status")
    assert status["state"] == "error"
    assert "region_count" in status["message"]
    assert client.get("/api/regions").status_code == 503

    # a changed-area total that does not reconcile with the served regions
    tampered = copy.deepcopy(original)
    tampered["metrics"]["total_changed_area_ha"] = original["metrics"]["total_changed_area_ha"] + 5.0
    (bundle_dir / "analysis.json").write_text(json.dumps(tampered, indent=2), encoding="utf-8")
    status = _get_json(client, "/api/status")
    assert status["state"] == "error"
    assert "total_changed_area_ha" in status["message"]

    (bundle_dir / "analysis.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
    assert _get_json(client, "/api/status")["state"] == "ready"


def test_damaged_preview_is_refused_then_repaired(tmp_path: Path, produced: dict[str, Path]) -> None:
    bundle_dir = _bundle_copy(tmp_path, produced["changed"])
    client = _client(bundle_dir)
    declared_url = _get_json(client, "/api/analysis")["imagery"]["change"]["url"]
    preview = bundle_dir / "change.png"
    original = preview.read_bytes()

    preview.write_bytes(b"not a png at all")
    status = _get_json(client, "/api/status")
    assert status["state"] == "error"
    assert client.get(declared_url).status_code == 503
    assert str(bundle_dir) not in status["message"] and str(preview) not in status["message"]

    preview.write_bytes(original)
    assert _get_json(client, "/api/status")["state"] == "ready"
    assert client.get("/api/imagery/change").content == original


def test_half_published_bundle_is_not_served(tmp_path: Path, produced: dict[str, Path]) -> None:
    """The producer publishes analysis.json last; a run interrupted earlier must not be served."""
    bundle_dir = _bundle_copy(tmp_path, produced["changed"])
    client = _client(bundle_dir)
    (bundle_dir / "regions.geojson").unlink()
    status = _get_json(client, "/api/status")
    assert status["state"] == "error"
    assert "regions.geojson" in status["message"]
    assert client.get("/api/regions").status_code == 503


def test_bundle_dir_symlink_is_refused(tmp_path: Path, produced: dict[str, Path]) -> None:
    real = _bundle_copy(tmp_path / "real", produced["changed"])
    link = tmp_path / "current"
    link.symlink_to(real, target_is_directory=True)
    client = _client(link)
    status = _get_json(client, "/api/status")
    assert status["state"] == "error"
    assert client.get("/api/analysis").status_code == 503
    # the message is a sanitised contract pointer: wording may evolve, but no
    # filesystem location may ever appear in it
    message = status["message"]
    assert message.strip()
    for leaked in (str(real), str(link), str(tmp_path), "/tmp/"):
        assert leaked not in message


def _publish_into(parent: Path, *, threshold_db: float, followup: np.ndarray | None = None) -> Path:
    """Publish a generation the way the operator does, and return its directory.

    The producer publishes atomically into ``<parent>/current``: a finished bundle
    is renamed into ``.<root>.gen-<analysis_id>`` and the relative pointer at
    ``current`` is swapped. A second run through the same pointer yields the next
    generation and retains the previous one.
    """
    parent.mkdir(parents=True, exist_ok=True)
    manifest = _write_manifest(
        parent / "manifest.json",
        _write_raster(parent / "before.tif", _stable_grid()),
        _write_raster(parent / "after.tif", _followup_grid() if followup is None else followup),
    )
    root = parent / "current"
    run_change_detection(manifest, str(root), threshold_db, 0.0)
    return root.resolve()


# --------------------------------------------------------------------------- #
# generation safety: a superseded preview is never served
# --------------------------------------------------------------------------- #
def test_superseded_generation_preview_is_refused(tmp_path: Path, produced: dict[str, Path]) -> None:
    """A client holding the previous analysis id must not be served the new images."""
    published = tmp_path / "published"
    first_generation = _publish_into(published, threshold_db=THRESHOLD_DB)
    root = published / "current"

    client = _client(root)
    first = _get_json(client, "/api/analysis")
    stale = _assert_declared_urls(first)
    stale_bytes = {key: (first_generation / f"{key}.png").read_bytes() for key in IMAGERY_KEYS}

    # a genuinely different pair, so the two generations cannot share preview bytes
    second_generation = _publish_into(
        published, threshold_db=THRESHOLD_DB + 0.5, followup=_followup_grid(with_tiny=True)
    )
    assert second_generation != first_generation

    second = _get_json(client, "/api/analysis")
    assert second["analysis_id"] != first["analysis_id"], "a new run must publish a new analysis id"
    current = _assert_declared_urls(second)
    fresh_bytes = {key: (second_generation / f"{key}.png").read_bytes() for key in IMAGERY_KEYS}
    assert fresh_bytes["change"] != stale_bytes["change"], "the two generations must differ for this to test anything"

    for key in IMAGERY_KEYS:
        assert current[key]["url"] != stale[key]["url"]
        response = client.get(stale[key]["url"])
        assert response.status_code == 409, (
            f"{key}: a superseded generation answered {response.status_code}, expected 409 Conflict"
        )
        assert response.content != fresh_bytes[key]
        served_current = client.get(current[key]["url"])
        assert served_current.status_code == 200
        assert served_current.content == fresh_bytes[key], f"{key}: current generation must serve current bytes"

    # the producer retains the superseded generation directory
    assert first_generation.is_dir()
    assert _get_json(client, "/api/status")["analysis_id"] == second["analysis_id"]


def test_published_generation_is_immutable_and_root_loss_is_not_served(
    tmp_path: Path, produced: dict[str, Path]
) -> None:
    """Two generation guarantees: no rewriting in place, and no images once the root is gone."""
    published = tmp_path / "published"
    generation = _publish_into(published, threshold_db=THRESHOLD_DB)
    root = published / "current"

    # republishing over an existing non-empty generation directory is refused, and
    # the refused run leaves every byte of that generation alone
    before = {item.name: item.read_bytes() for item in generation.iterdir() if item.is_file()}
    manifest = _write_manifest(
        tmp_path / "republish.json",
        _write_raster(tmp_path / "re-before.tif", _stable_grid()),
        _write_raster(tmp_path / "re-after.tif", _followup_grid()),
    )
    with pytest.raises(ChangeError, match="non-empty"):
        run_change_detection(manifest, str(generation), THRESHOLD_DB + 0.5, 0.0)
    after = {item.name: item.read_bytes() for item in generation.iterdir() if item.is_file()}
    assert after == before, "a refused publish must leave the existing generation untouched"
    assert _get_json(_client(root), "/api/analysis")["analysis_id"] == json.loads(
        (generation / "analysis.json").read_text(encoding="utf-8")
    )["analysis_id"]

    # a generation root that disappears must not be served as if it were still there
    client = _client(root)
    analysis = _get_json(client, "/api/analysis")
    declared = _assert_declared_urls(analysis)
    assert all((generation / f"{key}.png").is_file() for key in IMAGERY_KEYS)
    generation.rename(tmp_path / "moved-away")
    status = _get_json(client, "/api/status")
    assert status["state"] == "error", "a vanished generation root must not report ready"
    for key in IMAGERY_KEYS:
        assert client.get(declared[key]["url"]).status_code in (404, 409, 503), (
            f"{key}: a vanished generation root must not yield image bytes"
        )
    assert str(tmp_path) not in status["message"] and str(generation) not in status["message"]


def test_escaping_or_absolute_imagery_path_is_refused(tmp_path: Path, produced: dict[str, Path]) -> None:
    bundle_dir = _bundle_copy(tmp_path / "gen", produced["changed"])
    client = _client(bundle_dir)
    declared_url = _get_json(client, "/api/analysis")["imagery"]["before"]["url"]
    original = json.loads((bundle_dir / "analysis.json").read_text(encoding="utf-8"))

    for candidate in ("/etc/passwd", "../outside.png", "previews/before.png", "before.png/"):
        tampered = copy.deepcopy(original)
        tampered["imagery"]["before"]["path"] = candidate
        (bundle_dir / "analysis.json").write_text(json.dumps(tampered, indent=2), encoding="utf-8")
        status = _get_json(client, "/api/status")
        assert status["state"] == "error", f"{candidate!r} must be refused"
        assert client.get(declared_url).status_code == 503
        message = status["message"]
        assert candidate not in message and str(bundle_dir) not in message and str(tmp_path) not in message

    (bundle_dir / "analysis.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
    assert _get_json(client, "/api/status")["state"] == "ready"
    assert client.get(declared_url).status_code == 200


def test_symlinked_preview_is_refused(tmp_path: Path, produced: dict[str, Path]) -> None:
    bundle_dir = _bundle_copy(tmp_path / "gen", produced["changed"])
    client = _client(bundle_dir)
    declared_url = _get_json(client, "/api/analysis")["imagery"]["change"]["url"]
    outside = tmp_path / "outside.png"
    outside.write_bytes((bundle_dir / "change.png").read_bytes())
    (bundle_dir / "change.png").unlink()
    (bundle_dir / "change.png").symlink_to(outside)

    status = _get_json(client, "/api/status")
    assert status["state"] == "error"
    assert client.get(declared_url).status_code == 503
    message = status["message"]
    assert str(outside) not in message and str(bundle_dir) not in message and str(tmp_path) not in message


# --------------------------------------------------------------------------- #
# read-only surface
# --------------------------------------------------------------------------- #
def test_only_read_verbs_are_exposed(tmp_path: Path, produced: dict[str, Path]) -> None:
    client = _client(produced["changed"])
    schema = _get_json(client, "/openapi.json")
    for path, operations in schema["paths"].items():
        assert set(operations) <= {"get", "head", "options"}, f"{path} must stay read-only, found {set(operations)}"
    for verb, url in (("post", "/api/analysis"), ("put", "/api/regions"), ("delete", "/api/imagery/change")):
        assert client.request(verb, url).status_code in (404, 405), f"{verb.upper()} {url} must not be accepted"
    # the bundle directory is not mutated by serving it
    bundle_dir = produced["changed"]
    before = {item.name: (item.stat().st_mtime_ns, item.stat().st_size) for item in bundle_dir.iterdir()}
    declared = _assert_declared_urls(_get_json(client, "/api/analysis"))
    for url in ("/api/analysis", "/api/regions", *(declared[key]["url"] for key in IMAGERY_KEYS)):
        assert client.get(url).status_code == 200
    after = {item.name: (item.stat().st_mtime_ns, item.stat().st_size) for item in bundle_dir.iterdir()}
    assert before == after
    assert set(before) == {
        "analysis.json",
        "regions.geojson",
        "change.tif",
        "mask.tif",
        "before.png",
        "after.png",
        "change.png",
    }