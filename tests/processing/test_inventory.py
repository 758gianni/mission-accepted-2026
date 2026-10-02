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
    assert raster["pixel_spacing"] == {"x": 10.0, "y": 10.0}
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
    raster = report["scenes"][0]["rasters"][0]
    assert raster["crs"] is None
    assert raster["epsg"] is None
    assert "raster[imagery.tif].crs" in report["scenes"][0]["missing"]
    assert "DEM source not decided for this project" in (
        report["scenes"][0]["preprocessing"]["unknowns"]
    )


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
