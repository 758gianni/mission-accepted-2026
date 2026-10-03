"""Tests for the credential-free local RADARSAT-2 dataset reconnaissance CLI.

Scope: these tests only build throwaway fixtures inside pytest ``tmp_path``
directories. No real credentials, no network, no EODMS authentication, no
extraction of archives.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from processing import inventory  # noqa: E402


def _has_rasterio() -> bool:
    return inventory.rasterio is not None


needs_rasterio = pytest.mark.skipif(
    not _has_rasterio(), reason="rasterio not installed (optional dependency)"
)


NAMESPACED_PRODUCT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<product xmlns="http://www.interschema.com/product"
         xmlns:gml="http://www.opengis.net/gml"
         xmlns:safe="http://safe.europa.eu/safe/">
  <safe:remarks>reconnaissance fixture</safe:remarks>
  <productIdentifier>
    <name>RS2_2019_TEST_PRODUCT</name>
    <creator>fixture</creator>
  </productIdentifier>
  <processingLevel>L1</processingLevel>
  <productType>SLC</productType>
  <sensorMode>IW</sensorMode>
  <beamMode>IW3</beamMode>
  <beamName>IW3</beamName>
  <sampleType>COMPLEX_IQ</sampleType>
  <bitsPerSample>32</bitsPerSample>
  <transmitterReceiverPolarisation>HH HV</transmitterReceiverPolarisation>
  <imageAttributes>
    <productFirstLineUtcTime>2019-03-04T12:30:15.123456Z</productFirstLineUtcTime>
    <productLastLineUtcTime>2019-03-04T12:30:19.654321Z</productLastLineUtcTime>
    <ascendingPassTime>00:12:33</ascendingPassTime>
    <pass>ascending</pass>
  </imageAttributes>
  <orbitProperties>
    <absoluteOrbitNumber>24315</absoluteOrbitNumber>
    <orbitType>POE</orbitType>
    <firstStartTime>2019-03-04T12:28:00.000000Z</firstStartTime>
  </orbitProperties>
  <calibration>
    <calibrationLookupTable href="calibration/beta0.xml"/>
    <noiseLookupTable href="calibration/noise.xml"/>
    <orbitFile>POE_01234.Orbit.statevector.epr</orbitFile>
  </calibration>
  <geolocation>
    <imageTiePoints>
      <imageTiePoint><line>0</line><pixel>0</pixel></imageTiePoint>
      <imageTiePoint><line>1</line><pixel>2</pixel></imageTiePoint>
      <imageTiePoint><line>2</line><pixel>4</pixel></imageTiePoint>
    </imageTiePoints>
    <gcpList><gcp><row>0</row><col>0</col></gcp></gcpList>
  </geolocation>
  <footprint>
    <gml:Polygon srsName="EPSG:4326">
      <gml:pos>-1.0 50.0</gml:pos>
    </gml:Polygon>
  </footprint>
</product>
"""

PLAIN_PRODUCT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<product>
  <sceneId>RS2_PLAIN_SCENE</sceneId>
  <productType>GRD</productType>
  <acquisitionTime>2018-07-01 08:00:00Z</acquisitionTime>
  <polarization>VV</polarization>
</product>
"""

MALFORMED_PRODUCT_XML = "<product><productType>SLC</productType><sceneId>TRUNCATED"


def _write_product(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _make_raster(path: Path, *, epsg: int = 32633, nodata: int = 0, bands: int = 2) -> Path:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 12, 7
    transform = from_origin(400000.0, 5200000.0, 10.0, 10.0)
    data = np.arange(width * height * bands, dtype="float32").reshape(bands, height, width)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": bands,
        "dtype": "float32",
        "crs": rasterio.crs.CRS.from_epsg(epsg),
        "transform": transform,
        "nodata": float(nodata),
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    return path


# ---------------------------------------------------------------------------
# namespace-independent XML parsing
# ---------------------------------------------------------------------------


def test_namespaced_product_xml_yields_scene_fields(tmp_path):
    _write_product(tmp_path / "RS2_SLC" / "product.xml", NAMESPACED_PRODUCT_XML)
    report = inventory.scan(tmp_path)

    assert report["scene_count"] == 1
    scene = report["scenes"][0]
    assert scene["scene_id"] == "RS2_2019_TEST_PRODUCT"
    assert scene["product_type"] == "SLC"
    assert scene["sample_type"] == "COMPLEX_IQ"
    assert scene["bits_per_sample"] == 32
    assert scene["polarizations"] == ["HH", "HV"]
    assert scene["beam_mode"] == "IW3"
    assert scene["mode"] == "IW"
    assert scene["acquisition"]["start"] == "2019-03-04T12:30:15.123456Z"
    assert scene["acquisition"]["end"] == "2019-03-04T12:30:19.654321Z"
    assert scene["acquisition"]["duration_seconds"] == pytest.approx(4.530865, abs=1e-4)
    assert scene["orbits"]["number"] == 24315
    assert scene["orbits"]["type"] == "POE"
    assert scene["orbits"]["direction"] == "ascending"
    assert scene["calibration"]["lut_present"] is True
    assert scene["calibration"]["noise_present"] is True
    assert scene["calibration"]["orbit_files_present"] is True
    assert "calibration/beta0.xml" in scene["calibration"]["lut_references"]
    assert "calibration/noise.xml" in scene["calibration"]["noise_references"]
    assert scene["geolocation"]["tiepoint_count"] == 3
    assert scene["geolocation"]["gcp_count"] == 1
    assert report["errors"] == []


def test_same_xml_without_namespaces_parses_identically(tmp_path):
    """Namespace prefixes must not change the outcome."""
    prefixed = tmp_path / "prefixed"
    plain = tmp_path / "plain"
    _write_product(prefixed / "product.xml", NAMESPACED_PRODUCT_XML)
    ET.register_namespace("", "http://www.interschema.com/product")
    root = ET.fromstring(NAMESPACED_PRODUCT_XML)
    plain_xml = ET.tostring(root, encoding="unicode")
    _write_product(plain / "product.xml", plain_xml)

    a = inventory.scan(prefixed)["scenes"][0]
    b = inventory.scan(plain)["scenes"][0]

    for key in (
        "scene_id",
        "product_type",
        "sample_type",
        "polarizations",
        "beam_mode",
        "orbits",
        "calibration",
        "geolocation",
    ):
        assert a[key] == b[key], key


def test_prefixed_namespace_elements_are_recognised(tmp_path):
    body = """<?xml version="1.0"?>
    <p:product xmlns:p="urn:x" xmlns:t="urn:y">
      <t:sceneId>PREFIXED_SCENE</t:sceneId>
      <p:productType>GRD</p:productType>
      <p:polarisation>VV</p:polarisation>
    </p:product>
    """
    _write_product(tmp_path / "product.xml", body)
    scene = inventory.scan(tmp_path)["scenes"][0]
    assert scene["scene_id"] == "PREFIXED_SCENE"
    assert scene["product_type"] == "GRD"
    assert scene["polarizations"] == ["VV"]


def test_scene_id_prefers_product_identifier_child_name(tmp_path):
    body = """<?xml version="1.0"?>
    <product xmlns="urn:z">
      <name>generic_name_element</name>
      <productIdentifier><name>RS2_FROM_IDENTIFIER</name></productIdentifier>
    </product>
    """
    _write_product(tmp_path / "product.xml", body)
    scene = inventory.scan(tmp_path)["scenes"][0]
    assert scene["scene_id"] == "RS2_FROM_IDENTIFIER"


def test_pol_field_parsing_variants(tmp_path):
    body = """<?xml version="1.0"?>
    <product xmlns="urn:z">
      <sceneId>POLS</sceneId>
      <polarization>HH+HV,VV</polarization>
    </product>
    """
    _write_product(tmp_path / "product.xml", body)
    scene = inventory.scan(tmp_path)["scenes"][0]
    assert scene["polarizations"] == ["HH", "HV", "VV"]


# ---------------------------------------------------------------------------
# missing metadata / malformed input
# ---------------------------------------------------------------------------


def test_absent_metadata_is_reported_as_missing_not_invented(tmp_path):
    _write_product(tmp_path / "minimal" / "product.xml", PLAIN_PRODUCT_XML)
    report = inventory.scan(tmp_path)
    scene = report["scenes"][0]

    missing = set(scene["missing"])
    assert "scene_id" not in missing
    assert "product_type" not in missing
    for expected in (
        "beam_mode",
        "mode",
        "sample_type",
        "orbits.number",
        "geolocation.tiepoints",
        "geolocation.gcps",
        "calibration.lut",
        "calibration.noise",
        "calibration.orbit_files",
    ):
        assert expected in missing, expected
    assert scene["beam_mode"] is None
    assert scene["rasters"] == []
    assert report["missing_data"], "top-level missing_data summary must be populated"


def test_malformed_xml_yields_error_and_no_scene(tmp_path):
    _write_product(tmp_path / "broken" / "product.xml", MALFORMED_PRODUCT_XML)
    report = inventory.scan(tmp_path)

    assert report["scenes"] == []
    assert report["scene_count"] == 0
    assert len(report["errors"]) == 1
    error = report["errors"][0]
    assert "product.xml" in error["path"]
    assert error["stage"] == "xml"
    assert "parse" in error["message"].lower()


def test_empty_directory_reports_zero_scenes_clearly(tmp_path):
    empty = tmp_path / "data" / "raw"
    empty.mkdir(parents=True)
    report = inventory.scan(empty)

    assert report["scenes"] == []
    assert report["scene_count"] == 0
    assert report["zero_scenes"] is True
    assert "no RADARSAT-2 scenes" in report["summary"]
    assert report["errors"] == []


def test_directory_without_any_product_reports_zero_scenes(tmp_path):
    (tmp_path / "notes.txt").write_text("nothing to see", encoding="utf-8")
    report = inventory.scan(tmp_path)
    assert report["scene_count"] == 0
    assert report["zero_scenes"] is True
    assert any("product.xml" in w for w in report["warnings"])


# ---------------------------------------------------------------------------
# ZIP handling: no extraction, no path traversal, no writes
# ---------------------------------------------------------------------------


def test_zip_members_are_not_extracted_and_traversal_entries_are_skipped(tmp_path):
    data_dir = tmp_path / "data" / "raw"
    data_dir.mkdir(parents=True)
    zip_path = data_dir / "RS2_ARCHIVE.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("IMAGEDATA/product.xml", NAMESPACED_PRODUCT_XML)
        zf.writestr("../EVIL_TRAVERSAL.xml", PLAIN_PRODUCT_XML)
        zf.writestr("/ABSOLUTE_PATH.xml", PLAIN_PRODUCT_XML)
        zf.writestr("..\\WINDOWS_TRAVERSAL.xml", PLAIN_PRODUCT_XML)
        zf.writestr("nested/../../EVIL_DEEP.xml", PLAIN_PRODUCT_XML)

    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    report = inventory.scan(data_dir)

    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert before == after, "inventory must not create or extract any file"

    assert report["scene_count"] == 1
    scene = report["scenes"][0]
    assert scene["scene_id"] == "RS2_2019_TEST_PRODUCT"
    assert scene["source"]["container"] == "zip"

    traversal_terms = ("traversal", "absolute", "unsafe")
    skipped = [w for w in report["warnings"] if any(t in w.lower() for t in traversal_terms)]
    assert len(skipped) == 4, skipped
    assert "EVIL_TRAVERSAL" in " ".join(skipped)
    assert "WINDOWS_TRAVERSAL" in " ".join(skipped)
    assert "EVIL_DEEP" in " ".join(skipped)
    assert not any(s["scene_id"] == "RS2_PLAIN_SCENE" for s in report["scenes"])


def test_corrupt_zip_is_reported_as_error(tmp_path):
    (tmp_path / "broken.zip").write_bytes(b"PK\x03\x04 truncated garbage")
    report = inventory.scan(tmp_path)
    assert report["scene_count"] == 0
    assert any(e["stage"] == "zip" for e in report["errors"])


def test_zip_with_several_products_keeps_rasters_with_their_own_product(
    tmp_path,
):
    body = "<product xmlns='urn:x'><sceneId>{}</sceneId><productType>GRD</productType></product>"
    zip_path = tmp_path / "multi.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("product.xml", body.format("ROOT_SCENE"))
        zf.writestr("ANNO/product.xml", body.format("ANNO_SCENE"))
        zf.writestr("root_raster.tif", b"not a raster")
        zf.writestr("ANNO/inner.tif", b"not a raster")

    report = inventory.scan(tmp_path)
    rasters = {
        scene["scene_id"]: [r["name"] for r in scene["rasters"]] for scene in report["scenes"]
    }
    assert rasters == {"ROOT_SCENE": ["root_raster.tif"], "ANNO_SCENE": ["ANNO/inner.tif"]}


# ---------------------------------------------------------------------------
# raster + GCP metadata
# ---------------------------------------------------------------------------


@needs_rasterio
def test_raster_metadata_is_reported(tmp_path):
    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_GRD"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    _make_raster(scene_dir / "imagery.tif", epsg=32633, nodata=0, bands=2)

    report = inventory.scan(data_dir)
    scene = report["scenes"][0]
    assert len(scene["rasters"]) == 1
    raster = scene["rasters"][0]

    assert raster["name"] == "imagery.tif"
    assert raster["epsg"] == 32633
    assert raster["crs"] and "32633" in raster["crs"]
    assert raster["shape"] == {"width": 12, "height": 7}
    assert raster["count"] == 2
    assert raster["dtype"] == "float32"
    assert raster["dtypes"] == ["float32", "float32"]
    assert raster["nodata"] == 0.0
    assert raster["transform"] == pytest.approx([400000.0, 10.0, 0.0, 5200000.0, 0.0, -10.0])
    assert raster["pixel_spacing"] == {
        "column": 10.0,
        "row": 10.0,
        "x": 10.0,
        "y": 10.0,
    }
    assert raster["pixel_step_vectors"] == {"column": [10.0, 0.0], "row": [0.0, -10.0]}
    assert raster["rotation_degrees"] == pytest.approx(0.0)
    assert raster["georeferencing"]["source"] == "affine"
    assert raster["georeferencing"]["gdal_placeholder_warning"] is False
    assert raster["crs_units"] == "metre"
    assert raster["error"] is None
    assert raster["transform_order"] == "gdal (c, a, b, f, d, e)"
    assert not [m for m in scene["missing"] if m.startswith("raster[")]


@needs_rasterio
def test_raster_without_crs_does_not_get_an_invented_crs(tmp_path):
    import numpy as np
    import rasterio

    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_NOCRS"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    path = scene_dir / "imagery.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=4,
        width=5,
        count=1,
        dtype="uint8",
    ) as dst:
        dst.write(np.zeros((1, 4, 5), dtype="uint8"))

    report = inventory.scan(data_dir)
    scene = report["scenes"][0]
    raster = scene["rasters"][0]
    assert raster["crs"] is None
    assert raster["epsg"] is None
    assert "raster[imagery.tif].crs" in scene["missing"]
    # GDAL returns an identity placeholder for an ungeoreferenced raster; that
    # placeholder must not be reported as a real transform or 1x1 spacing.
    assert raster["transform"] is None
    assert raster["pixel_spacing"] is None
    assert raster["georeferencing"]["source"] == "none"
    assert "raster[imagery.tif].transform" in scene["missing"]
    assert "raster[imagery.tif].pixel_spacing" in scene["missing"]
    assert "DEM source not decided for this project" in (scene["preprocessing"]["unknowns"])


@needs_rasterio
def test_raster_inside_zip_is_read_without_extraction(tmp_path):
    data_dir = tmp_path / "raw"
    data_dir.mkdir(parents=True)
    tif = _make_raster(tmp_path / "staging" / "imagery.tif", epsg=32632)
    zip_path = data_dir / "SCENE.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(tif, "IMAGEDATA/imagery.tif")
        zf.writestr("IMAGEDATA/product.xml", PLAIN_PRODUCT_XML)

    before = sorted(p.name for p in data_dir.iterdir())
    report = inventory.scan(data_dir)
    after = sorted(p.name for p in data_dir.iterdir())

    assert before == after == ["SCENE.zip"]
    raster = report["scenes"][0]["rasters"][0]
    assert raster["inside_archive"] is True
    assert raster["epsg"] == 32632
    assert raster["shape"] == {"width": 12, "height": 7}


@needs_rasterio
def test_gdal_gcp_and_tiepoint_metadata_is_counted(tmp_path):
    import numpy as np
    import rasterio
    from rasterio.control import GroundControlPoint

    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_GCP"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    gcps = [
        GroundControlPoint(row=0, col=0, x=400000.0, y=5200000.0),
        GroundControlPoint(row=1, col=1, x=400010.0, y=5199990.0),
    ]
    with rasterio.open(
        scene_dir / "imagery.tif",
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="uint16",
        gcps=gcps,
        crs=rasterio.crs.CRS.from_epsg(32633),
    ) as dst:
        dst.write(np.zeros((1, 4, 4), dtype="uint16"))

    scene = inventory.scan(data_dir)["scenes"][0]
    raster = scene["rasters"][0]
    assert raster["gcp_count"] == 2
    assert raster["gcps"] == [
        {"row": 0.0, "col": 0.0, "x": 400000.0, "y": 5200000.0, "z": 0.0},
        {"row": 1.0, "col": 1.0, "x": 400010.0, "y": 5199990.0, "z": 0.0},
    ]
    # A GCP-only raster has no affine georeferencing: GDAL returns identity by
    # convention, which must not be reported as a real transform.
    assert raster["transform"] is None
    assert raster["pixel_spacing"] is None
    assert raster["georeferencing"]["source"] == "gcp"
    assert raster["georeferencing"]["gcp_count"] == 2
    assert "raster[imagery.tif].transform" in scene["missing"]
    # rasterio does not expose GDAL tie points; that is stated, not invented.
    assert raster["tiepoint_count"] is None
    assert "not exposed by rasterio" in raster["tiepoint_source"]
    # XML-level tiepoint metadata is reported per scene instead.
    assert scene["geolocation"]["tiepoint_count"] == 0


def test_rasterio_absence_is_graceful_and_noted(tmp_path, monkeypatch):
    import processing.inventory as inv

    monkeypatch.setattr(inv, "rasterio", None)
    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_NORASTRIO"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    (scene_dir / "imagery.tif").write_bytes(b"II*\x00not really a tiff")

    report = inventory.scan(data_dir)
    scene = report["scenes"][0]
    assert scene["rasters"][0]["crs"] is None
    assert scene["rasters"][0]["epsg"] is None
    assert scene["rasters"][0]["error"] is not None
    assert report["rasterio"]["available"] is False
    assert any("rasterio" in w.lower() for w in report["warnings"])
    assert "raster[imagery.tif].crs" in scene["missing"]


@needs_rasterio
def test_unreadable_raster_is_reported_without_aborting_scan(tmp_path):
    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_BADRASTER"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    (scene_dir / "imagery.tif").write_bytes(b"definitely not a raster")

    report = inventory.scan(data_dir)
    assert report["scene_count"] == 1
    raster = report["scenes"][0]["rasters"][0]
    assert raster["error"] is not None
    assert raster["crs"] is None


# ---------------------------------------------------------------------------
# georeferencing honesty, strict JSON, rotated/sheared spacing
# ---------------------------------------------------------------------------


@needs_rasterio
def test_declared_identity_geotransform_is_not_rejected_blindly(tmp_path):
    """An explicitly declared identity geotransform is kept, but flagged.

    GDAL emits ``NotGeoreferencedWarning`` ("the identity matrix will be
    returned") when it substitutes a placeholder. A file that declares identity
    does not trigger that, so it must be reported as-is instead of nulled.
    """
    import warnings

    import numpy as np
    import rasterio
    from rasterio.transform import Affine

    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_IDENTITY"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    path = scene_dir / "imagery.tif"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=4,
            width=4,
            count=1,
            dtype="uint8",
            crs=rasterio.crs.CRS.from_epsg(4326),
            transform=Affine.identity(),
        ) as dst:
            dst.write(np.zeros((1, 4, 4), dtype="uint8"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with rasterio.open(path) as handle:
            reported = tuple(handle.transform)[:6]
    assert not [w for w in caught if "identity matrix" in str(w.message)], [
        str(w.message) for w in caught
    ]
    assert reported == (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)

    raster = inventory.scan(data_dir)["scenes"][0]["rasters"][0]
    # GDAL ordering (c, a, b, f, d, e) of Affine.identity()
    assert raster["transform"] == [0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    assert raster["georeferencing"]["gdal_placeholder_warning"] is False
    assert raster["georeferencing"]["source"] == "affine"
    assert raster["georeferencing"]["identity_transform"] is True
    assert raster["pixel_spacing"] == {
        "column": 1.0,
        "row": 1.0,
        "x": 1.0,
        "y": 1.0,
    }


@needs_rasterio
def test_missing_georeferencing_detected_from_metadata_without_the_warning(tmp_path, monkeypatch):
    """A silent driver must still be caught by the metadata cross-check."""
    import warnings

    import numpy as np
    import rasterio

    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_SILENT"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    path = scene_dir / "imagery.tif"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with rasterio.open(
            path, "w", driver="GTiff", height=3, width=3, count=1, dtype="uint8"
        ) as dst:
            dst.write(np.zeros((1, 3, 3), dtype="uint8"))

    monkeypatch.setattr(inventory, "_is_gdal_placeholder_warning", lambda caught: False)
    raster = inventory.scan(data_dir)["scenes"][0]["rasters"][0]
    assert raster["georeferencing"]["gdal_placeholder_warning"] is False
    assert raster["georeferencing"]["source"] == "none"
    assert raster["transform"] is None
    assert raster["pixel_spacing"] is None


@needs_rasterio
def test_rotated_transform_uses_column_and_row_step_lengths(tmp_path):
    """For Affine(a,b,c,d,e,f) the column step is (a,d) and the row step is (b,e)."""
    import math

    import numpy as np
    import rasterio

    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_ROTATED"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    path = scene_dir / "imagery.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=5,
        width=5,
        count=1,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(32633),
        transform=rasterio.Affine(a=3.0, b=4.0, c=100.0, d=12.0, e=-5.0, f=200.0),
    ) as dst:
        dst.write(np.zeros((1, 5, 5), dtype="float32"))

    raster = inventory.scan(data_dir)["scenes"][0]["rasters"][0]
    assert raster["transform"] == pytest.approx([100.0, 3.0, 4.0, 200.0, 12.0, -5.0])
    assert raster["pixel_step_vectors"] == {"column": [3.0, 12.0], "row": [4.0, -5.0]}
    assert raster["pixel_spacing"]["column"] == pytest.approx(math.hypot(3.0, 12.0))
    assert raster["pixel_spacing"]["row"] == pytest.approx(math.hypot(4.0, 5.0))
    assert raster["pixel_spacing"]["x"] == pytest.approx(math.hypot(3.0, 12.0))
    assert raster["pixel_spacing"]["y"] == pytest.approx(math.hypot(4.0, 5.0))
    assert raster["rotation_degrees"] == pytest.approx(math.degrees(math.atan2(12.0, 3.0)))
    # The old abs-diagonal behaviour is the bug being regressed against.
    assert raster["pixel_spacing"]["column"] != pytest.approx(3.0)
    assert raster["pixel_spacing"]["row"] != pytest.approx(5.0)


@needs_rasterio
def test_sheared_transform_uses_column_and_row_step_lengths(tmp_path):
    import math

    import numpy as np
    import rasterio

    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_SHEARED"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    path = scene_dir / "imagery.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=5,
        width=5,
        count=1,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(32633),
        transform=rasterio.Affine(a=10.0, b=3.0, c=0.0, d=1.0, e=-20.0, f=0.0),
    ) as dst:
        dst.write(np.zeros((1, 5, 5), dtype="float32"))

    raster = inventory.scan(data_dir)["scenes"][0]["rasters"][0]
    assert raster["pixel_step_vectors"] == {"column": [10.0, 1.0], "row": [3.0, -20.0]}
    assert raster["pixel_spacing"]["column"] == pytest.approx(math.hypot(10.0, 1.0))
    assert raster["pixel_spacing"]["row"] == pytest.approx(math.hypot(3.0, 20.0))


@needs_rasterio
@pytest.mark.parametrize(
    ("nodata", "kind"),
    [
        (float("nan"), "nan"),
        (float("inf"), "posinf"),
        (float("-inf"), "neginf"),
        (-9999.0, "value"),
    ],
)
def test_nonfinite_nodata_is_encoded_explicitly(tmp_path, nodata, kind):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    data_dir = tmp_path / "raw"
    scene_dir = data_dir / kind
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    path = scene_dir / "imagery.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(32633),
        transform=from_origin(0.0, 0.0, 1.0, 1.0),
        nodata=nodata,
    ) as dst:
        dst.write(np.zeros((1, 2, 2), dtype="float32"))

    report = inventory.scan(data_dir)
    raster = report["scenes"][0]["rasters"][0]
    assert raster["nodata_kind"] == kind
    if kind == "value":
        assert raster["nodata"] == -9999.0
    else:
        assert raster["nodata"] is None, "nonfinite nodata must not leak into JSON"
    # The whole report must survive strict serialisation.
    text = json.dumps(report, allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text


def test_nonfinite_values_are_replaced_in_reports_without_rasterio(tmp_path, monkeypatch):
    import processing.inventory as inv

    monkeypatch.setattr(inv, "rasterio", None)
    data_dir = tmp_path / "raw"
    _write_product(data_dir / "SCENE" / "product.xml", PLAIN_PRODUCT_XML)
    report = inventory.scan(data_dir)
    # Degenerate timestamps produce None, and nothing non-finite may remain.
    report["scene_count"] = float("nan")
    report["scenes"][0]["acquisition"]["duration_seconds"] = float("inf")
    safe = inventory.json_safe(report)
    assert safe["scene_count"] is None
    assert safe["scenes"][0]["acquisition"]["duration_seconds"] is None
    json.dumps(safe, allow_nan=False)


@needs_rasterio
def test_cli_json_output_is_strict_for_nan_nodata(tmp_path):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "SCENE"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    with rasterio.open(
        scene_dir / "imagery.tif",
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="float32",
        crs=rasterio.crs.CRS.from_epsg(32633),
        transform=from_origin(0.0, 0.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as dst:
        dst.write(np.zeros((1, 2, 2), dtype="float32"))

    out = tmp_path / "reports" / "inventory.json"
    result = _run_cli(str(data_dir), "--out", str(out), cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    parsed = json.loads(text)
    assert parsed["scenes"][0]["rasters"][0]["nodata"] is None
    assert parsed["scenes"][0]["rasters"][0]["nodata_kind"] == "nan"

    def _reject(value):
        raise AssertionError(f"non-strict JSON token: {value}")

    json.loads(text, parse_constant=_reject)


# ---------------------------------------------------------------------------
# authentic GDAL RS2 product.xml (official autotest fixture, vendored verbatim)
# ---------------------------------------------------------------------------

# Verbatim copy of the official GDAL RS2 driver autotest fixture:
#   https://github.com/OSGeo/gdal/blob/master/autotest/gdrivers/data/rs2/product.xml
#   blob sha256 of the fetched copy: see provenance recorded in docs/INVENTORY.md
# Element names below are the ones GDAL's RS2 driver (frmts/rs2/rs2dataset.cpp)
# actually reads: product.sourceAttributes.{satellite,sensor,beamModeMnemonic,
# rawDataStartTime}, product.imageGenerationParameters.generalProcessingInformation
# .productType, product.imageAttributes.rasterAttributes.{dataType,bitsPerSample,
# numberOfSamplesPerLine,numberOfLines,sampledPixelSpacing,sampledLineSpacing},
# product.imageAttributes.geographicInformation.geolocationGrid.imageTiePoint,
# product.imageAttributes.lookupTable[@incidenceAngleCorrection] and
# product.imageAttributes.fullResolutionImageData[@pole].
# The file itself states: "Completely artificially (and certainly not spec
# complying) RS2 product.xml" - it is GDAL's synthetic test data, not a real
# acquisition, so no field value here may be treated as a real observation.
# Byte-for-byte copy of the official GDAL RS2 driver autotest fixture, fetched from
#   https://raw.githubusercontent.com/OSGeo/gdal/master/autotest/gdrivers/data/rs2/product.xml
# sha256 892b1ea2bfdd12e46549e336dad6f08f5ced1ca6393c26daa3196084afe6028a (asserted below so upstream drift is detectable).
# The file itself says: "Completely artificially (and certainly not spec complying)
# RS2 product.xml" - it is GDAL's synthetic driver test data, so no value in it is a
# real observation. The element names it exercises are the ones GDAL's RS2 driver
# (frmts/rs2/rs2dataset.cpp) reads; see docs/INVENTORY.md for the full mapping.
GDAL_RS2_PRODUCT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!-- Completely artificially (and certainly not spec complying) RS2 product.xml -->
<product xmlns="http://foo.bar/rs2">
    <sourceAttributes>
        <satellite>SATELLITE</satellite>
        <sensor>SENSOR</sensor>
        <beamModeMnemonic>BEAM_MODE_MNEMONIC</beamModeMnemonic>
        <rawDataStartTime>2009-03-13T00:00:00Z</rawDataStartTime>
    </sourceAttributes>
    <imageGenerationParameters>
        <generalProcessingInformation>
            <productType>PRODUCT_TYPE</productType>
        </generalProcessingInformation>
        <sarProcessingInformation>
            <incidenceAngleNearRange>10</incidenceAngleNearRange>
            <incidenceAngleFarRange>20</incidenceAngleFarRange>
            <slantRangeNearEdge>30</slantRangeNearEdge>
        </sarProcessingInformation>
    </imageGenerationParameters>
    <imageAttributes>
        <rasterAttributes>
            <dataType>Mag</dataType>
            <bitsPerSample>8</bitsPerSample>
            <numberOfSamplesPerLine>20</numberOfSamplesPerLine>
            <numberOfLines>20</numberOfLines>
            <sampledPixelSpacing>1</sampledPixelSpacing>
            <sampledLineSpacing>1</sampledLineSpacing>
        </rasterAttributes>
        <geographicInformation>
            <geolocationGrid>
                <imageTiePoint>
                    <imageCoordinate>
                        <line>0</line>
                        <pixel>0</pixel>
                    </imageCoordinate>
                    <geodeticCoordinate>
                        <latitude>49</latitude>
                        <longitude>2</longitude>
                    </geodeticCoordinate>
                </imageTiePoint>
                <imageTiePoint>
                    <imageCoordinate>
                        <line>0</line>
                        <pixel>20</pixel>
                    </imageCoordinate>
                    <geodeticCoordinate>
                        <latitude>49</latitude>
                        <longitude>3</longitude>
                    </geodeticCoordinate>
                </imageTiePoint>
                <imageTiePoint>
                    <imageCoordinate>
                        <line>20</line>
                        <pixel>0</pixel>
                    </imageCoordinate>
                    <geodeticCoordinate>
                        <latitude>48</latitude>
                        <longitude>2</longitude>
                    </geodeticCoordinate>
                </imageTiePoint>
                <imageTiePoint>
                    <imageCoordinate>
                        <line>20</line>
                        <pixel>20</pixel>
                    </imageCoordinate>
                    <geodeticCoordinate>
                        <latitude>48</latitude>
                        <longitude>3</longitude>
                    </geodeticCoordinate>
                </imageTiePoint>
            </geolocationGrid>
            <rationalFunctions>
              <!-- dummy and invalid values ! -->
              <biasError units="m">biasError</biasError>
              <randomError units="m">randomError</randomError>
              <lineFitQuality>lineFitQuality</lineFitQuality>
              <pixelFitQuality>pixelFitQuality</pixelFitQuality>
              <lineOffset>lineOffset</lineOffset>
              <pixelOffset>pixelOffset</pixelOffset>
              <latitudeOffset units="deg">latitudeOffset</latitudeOffset>
              <longitudeOffset units="deg">longitudeOffset</longitudeOffset>
              <heightOffset units="m">heightOffset</heightOffset>
              <lineScale>lineScale</lineScale>
              <pixelScale>pixelScale</pixelScale>
              <latitudeScale>latitudeScale</latitudeScale>
              <longitudeScale>longitudeScale</longitudeScale>
              <heightScale>heightScale</heightScale>
              <lineNumeratorCoefficients>lineNumeratorCoefficients</lineNumeratorCoefficients>
              <lineDenominatorCoefficients>lineDenominatorCoefficients</lineDenominatorCoefficients>
              <pixelNumeratorCoefficients>pixelNumeratorCoefficients</pixelNumeratorCoefficients>
              <pixelDenominatorCoefficients>pixelDenominatorCoefficients</pixelDenominatorCoefficients>
            </rationalFunctions>
            <referenceEllipsoidParameters>
                <ellipsoidName>WGS84</ellipsoidName>
                <semiMajorAxis>6378137</semiMajorAxis>
                <semiMinorAxis>6356752.314245179</semiMinorAxis>
            </referenceEllipsoidParameters>
        </geographicInformation>
        <lookupTable incidenceAngleCorrection="Beta Nought">lut.xml</lookupTable>
        <lookupTable incidenceAngleCorrection="Sigma Nought">lut.xml</lookupTable>
        <lookupTable incidenceAngleCorrection="Gamma">lut.xml</lookupTable>
        <fullResolutionImageData pole="HH">byte_scanline.tif</fullResolutionImageData>
        <fullResolutionImageData pole="HV">byte_scanline.tif</fullResolutionImageData>
    </imageAttributes>
</product>
"""
GDAL_RS2_PRODUCT_XML_SHA256 = "892b1ea2bfdd12e46549e336dad6f08f5ced1ca6393c26daa3196084afe6028a"


def test_vendored_gdal_rs2_fixture_is_unmodified():
    import hashlib

    digest = hashlib.sha256(GDAL_RS2_PRODUCT_XML.encode("utf-8")).hexdigest()
    assert digest == GDAL_RS2_PRODUCT_XML_SHA256, (
        "the vendored GDAL RS2 fixture drifted from upstream; re-fetch it and update the "
        "digest instead of editing it by hand"
    )


def test_official_gdal_rs2_product_xml_is_parsed(tmp_path):
    """Authentic GDAL RS2 fixture: documented element names must be recognised."""
    data_dir = tmp_path / "raw"
    _write_product(data_dir / "RS2_OFFICIAL" / "product.xml", GDAL_RS2_PRODUCT_XML)
    report = inventory.scan(data_dir)
    assert report["errors"] == []
    scene = report["scenes"][0]

    # The official fixture uses GDAL's placeholder values, not real observations.
    assert scene["product_type"] == "PRODUCT_TYPE"
    assert scene["beam_mode"] == "BEAM_MODE_MNEMONIC"
    assert scene["sample_type"] == "Mag"
    assert scene["bits_per_sample"] == 8
    # polarizations come from fullResolutionImageData/@pole in this schema
    assert scene["polarizations"] == ["HH", "HV"]
    assert scene["acquisition"]["start"] == "2009-03-13T00:00:00Z"
    assert scene["acquisition"]["sensor"] == "SENSOR"
    assert scene["sampled_pixel_spacing"] == 1.0
    assert scene["sampled_line_spacing"] == 1.0
    assert scene["raster_attributes"]["number_of_samples_per_line"] == 20
    assert scene["raster_attributes"]["number_of_lines"] == 20
    assert scene["geolocation"]["tiepoint_count"] == 4
    assert scene["geolocation"]["has_tiepoints"] is True
    assert scene["geolocation"]["rational_functions_present"] is True
    assert scene["geolocation"]["ellipsoid_name"] == "WGS84"
    assert scene["calibration"]["lut_present"] is True
    assert scene["calibration"]["lut_references"] == ["lut.xml"]
    assert scene["radar_geometry"]["incidence_angle_near_range"] == 10.0
    assert scene["radar_geometry"]["incidence_angle_far_range"] == 20.0
    assert scene["radar_geometry"]["slant_range_near_edge"] == 30.0
    # The official fixture carries no scene identifier; that is reported, not faked.
    assert scene["scene_id"] is None
    assert "scene_id" in scene["missing"]
    assert "acquisition.end" in scene["missing"]
    # PRODUCT_TYPE is GDAL's placeholder, so no product-specific chain may be claimed.
    assert scene["preprocessing"]["recommended"].startswith("No decision")
    assert "unrecognised" in scene["preprocessing"]["recommended"]
    assert scene["preprocessing"]["unknowns"]
    json.dumps(report, allow_nan=False)


def test_official_gdal_rs2_fixture_matches_a_prefixed_copy(tmp_path):
    """Namespace-independence must hold for the official schema too."""
    import xml.etree.ElementTree as ET

    data_dir = tmp_path / "raw"
    default_ns = _write_product(data_dir / "a" / "product.xml", GDAL_RS2_PRODUCT_XML)
    del default_ns
    root = ET.fromstring(GDAL_RS2_PRODUCT_XML)
    ET.register_namespace("rs", "http://foo.bar/rs2")
    ET.register_namespace("x", "http://example.invalid/x")
    prefixed = ET.tostring(root, encoding="unicode")
    _write_product(data_dir / "b" / "product.xml", prefixed)

    plain_scene = inventory.scan(data_dir / "a")["scenes"][0]
    prefixed_scene = inventory.scan(data_dir / "b")["scenes"][0]
    for key in ("product_type", "polarizations", "beam_mode", "acquisition", "geolocation"):
        assert plain_scene[key] == prefixed_scene[key], key


# ---------------------------------------------------------------------------
# safety: credential-like paths and symlink escapes
# ---------------------------------------------------------------------------


def test_credential_like_paths_are_never_opened(tmp_path):
    data_dir = tmp_path / "raw"
    data_dir.mkdir(parents=True)
    _write_product(data_dir / ".env" / "product.xml", NAMESPACED_PRODUCT_XML)
    _write_product(data_dir / ".aws" / "product.xml", NAMESPACED_PRODUCT_XML)
    _write_product(data_dir / ".codex" / "product.xml", NAMESPACED_PRODUCT_XML)
    _write_product(data_dir / ".eodms" / "product.xml", NAMESPACED_PRODUCT_XML)
    _write_product(data_dir / "credentials" / "product.xml", NAMESPACED_PRODUCT_XML)
    (data_dir / "aws_credentials.xml").write_text(NAMESPACED_PRODUCT_XML, encoding="utf-8")
    _write_product(data_dir / "RS2_REAL" / "product.xml", NAMESPACED_PRODUCT_XML)

    report = inventory.scan(data_dir)
    assert report["scene_count"] == 1
    assert report["scenes"][0]["source"]["path"].endswith("RS2_REAL")
    assert len([w for w in report["warnings"] if "skipped" in w.lower()]) >= 6


def test_symlink_escaping_the_data_dir_is_skipped(tmp_path):
    data_dir = tmp_path / "raw"
    data_dir.mkdir(parents=True)
    outside = tmp_path / "outside" / "product.xml"
    _write_product(outside, NAMESPACED_PRODUCT_XML)
    _write_product(data_dir / "RS2_REAL" / "product.xml", NAMESPACED_PRODUCT_XML)
    try:
        os.symlink(outside, data_dir / "escape.xml")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    report = inventory.scan(data_dir)
    assert report["scene_count"] == 1
    assert any("symlink" in w.lower() for w in report["warnings"])


def test_scan_refuses_a_file_instead_of_a_directory(tmp_path):
    target = tmp_path / "not_a_dir.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        inventory.scan(target)


# ---------------------------------------------------------------------------
# footprint caveat + preprocessing guidance
# ---------------------------------------------------------------------------


def test_epsg4326_footprint_is_not_claimed_as_geocoded_raster(tmp_path):
    _write_product(tmp_path / "RS2_FP" / "product.xml", NAMESPACED_PRODUCT_XML)
    report = inventory.scan(tmp_path)
    scene = report["scenes"][0]

    assert scene["footprint"]["srs_name"] == "EPSG:4326"
    assert scene["footprint"]["is_geographic"] is True
    assert "does not imply" in scene["footprint"]["caveat"]
    assert scene["rasters"] == []
    assert "raster" in scene["missing"]
    assert any(
        "footprint" in w.lower() and "does not imply" in w.lower() for w in report["warnings"]
    ), report["warnings"]


def test_preprocessing_decision_for_slc_lists_unknowns(tmp_path):
    _write_product(tmp_path / "RS2_SLC" / "product.xml", NAMESPACED_PRODUCT_XML)
    scene = inventory.scan(tmp_path)["scenes"][0]
    decision = scene["preprocessing"]

    assert "noise" in decision["recommended"].lower()
    assert "orbit" in decision["recommended"].lower()
    assert decision["unknowns"], "unknowns must never be empty"
    performed = " ".join(decision["not_performed"]).lower()
    assert "no radiometric calibration performed" in performed
    assert "change detection" in performed
    assert "no pixels were read" in performed


def test_preprocessing_decision_for_grd(tmp_path):
    _write_product(tmp_path / "RS2_GRD" / "product.xml", PLAIN_PRODUCT_XML)
    decision = inventory.scan(tmp_path)["scenes"][0]["preprocessing"]
    assert "speckle" in decision["recommended"].lower()
    assert "terrain" in decision["recommended"].lower()
    assert any("dem" in u.lower() for u in decision["unknowns"])


def test_preprocessing_decision_is_unknown_when_product_type_absent(tmp_path):
    body = "<product xmlns='urn:z'><sceneId>MYSTERY</sceneId></product>"
    _write_product(tmp_path / "product.xml", body)
    scene = inventory.scan(tmp_path)["scenes"][0]
    assert scene["product_type"] is None
    assert scene["preprocessing"]["recommended"].lower().startswith("no decision")
    assert scene["preprocessing"]["unknowns"]


def test_change_detection_and_calibration_are_not_attempted(tmp_path):
    """The inventory stage must not read pixel values or write derived products."""
    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "RS2_GRD"
    _write_product(scene_dir / "product.xml", PLAIN_PRODUCT_XML)
    _make_raster(scene_dir / "imagery.tif")
    before = sorted(p.relative_to(data_dir).as_posix() for p in data_dir.rglob("*"))

    inventory.scan(data_dir)

    after = sorted(p.relative_to(data_dir).as_posix() for p in data_dir.rglob("*"))
    assert before == after


@needs_rasterio
def test_raster_without_product_xml_is_reported_as_orphan(tmp_path):
    data_dir = tmp_path / "raw"
    loose = data_dir / "loose"
    loose.mkdir(parents=True)
    _make_raster(loose / "imagery.tif", epsg=32631)
    _write_product(data_dir / "RS2_REAL" / "product.xml", PLAIN_PRODUCT_XML)

    report = inventory.scan(data_dir)
    assert report["scene_count"] == 1
    assert [raster["name"] for raster in report["orphan_rasters"]] == ["imagery.tif"]
    assert report["orphan_rasters"][0]["epsg"] == 32631
    assert any("orphan" in w for w in report["warnings"])


@needs_rasterio
def test_raster_next_to_malformed_product_xml_is_reported_as_orphan(tmp_path):
    data_dir = tmp_path / "raw"
    scene_dir = data_dir / "broken"
    _write_product(scene_dir / "product.xml", MALFORMED_PRODUCT_XML)
    _make_raster(scene_dir / "imagery.tif", epsg=32631)

    report = inventory.scan(data_dir)
    assert report["scene_count"] == 0
    assert len(report["errors"]) == 1
    assert [raster["name"] for raster in report["orphan_rasters"]] == ["imagery.tif"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "processing.inventory", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_cli_writes_json_report(tmp_path):
    data_dir = tmp_path / "raw"
    _write_product(data_dir / "RS2_SLC" / "product.xml", NAMESPACED_PRODUCT_XML)
    out = tmp_path / "reports" / "inventory.json"

    result = _run_cli(str(data_dir), "--out", str(out), cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert out.exists()

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["scene_count"] == 1
    assert report["scenes"][0]["scene_id"] == "RS2_2019_TEST_PRODUCT"
    assert report["tool"] == "processing.inventory"
    assert str(data_dir) in report["data_dir"]


def test_cli_reports_zero_scenes_on_stdout(tmp_path):
    data_dir = tmp_path / "empty"
    data_dir.mkdir()
    result = _run_cli(str(data_dir), cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["scene_count"] == 0
    assert report["zero_scenes"] is True


def test_cli_missing_directory_exits_nonzero(tmp_path):
    result = _run_cli(str(tmp_path / "does_not_exist"), cwd=REPO_ROOT)
    assert result.returncode == 2
    assert "not found" in result.stderr.lower()


def test_cli_output_is_deterministic_enough_for_repeat_runs(tmp_path):
    data_dir = tmp_path / "raw"
    _write_product(data_dir / "RS2_SLC" / "product.xml", NAMESPACED_PRODUCT_XML)
    first = inventory.scan(data_dir)
    second = inventory.scan(data_dir)
    for report in (first, second):
        report.pop("generated_utc", None)
    assert first == second


def test_module_exposes_scan_and_main(tmp_path):
    assert callable(inventory.scan)
    assert callable(inventory.main)
    assert callable(inventory.recommend_preprocessing)
