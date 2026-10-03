"""Tests for the read-only RADARSAT-2 calibration preflight.

Fixture honesty
---------------

Every fixture is SYNTHETIC, built inside pytest ``tmp_path``, and labelled as
such. The XML reproduces the real RADARSAT-2 element structure that this
preflight depends on:

``product.xml``

* ``imageAttributes/rasterAttributes/dataType`` -> ``Mag`` or ``Complex``
* ``imageAttributes/rasterAttributes/{bitsPerSample, numberOfLines,
  numberOfSamplesPerLine}``
* ``imageAttributes/transmitterReceiverPolarisation``
* ``calibration/.../lookupTable`` where the **filename is the element text** and
  the lookup dimension is the ``selected`` attribute.

sigma0 lookup table

.. code-block:: xml

    <lut>
      <offset>SCALAR</offset>
      <gains>G0 G1 G2 ...</gains>   <!-- one gain per image column -->
    </lut>

There is deliberately **no** ``gainList``, no ``pol`` element and no
``incidenceAngle``/``width`` pair: that schema is not what RADARSAT-2 uses and
this preflight does not parse it.

These tests also assert what the tool must *not* claim: no raw-sample power
assertion, no SGF driver, no calibration performed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from processing import calibration_preflight as cp  # noqa: E402

BANNER = "SYNTHETIC TEST FIXTURE - NOT A REAL RADARSAT-2 PRODUCT"

PRODUCT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!-- {banner} -->
<product xmlns:gml="http://www.opengis.net/gml">
  <imageAttributes>
    <productType>{product_type}</productType>
    <acquisitionType>{acquisition_type}</acquisitionType>
    <transmitterReceiverPolarisation>{pols}</transmitterReceiverPolarisation>
    <rasterAttributes>
      <bitsPerSample>{bits}</bitsPerSample>
      <numberOfLines>{lines}</numberOfLines>
      <numberOfSamplesPerLine>{samples}</numberOfSamplesPerLine>
      <dataType>{data_type}</dataType>
    </rasterAttributes>
  </imageAttributes>
  <calibration>
    <sigmaZero>
      <lookupTable selected="{selected}">{lut_name}</lookupTable>
    </sigmaZero>
  </calibration>
</product>
"""

LUT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!-- {banner} -->
<lut>
  <offset>{offset}</offset>
  <gains>{gains}</gains>
</lut>
"""


def make_product(
    root: Path,
    *,
    product_type: str = "SGX",
    acquisition_type: str = "L1",
    data_type: str = "Complex",
    bits: int | str = 16,
    pols: str = "HH",
    lines: int | str = 4,
    samples: int | str = 6,
    lut_name: str = "calibration/sigma0_polarization_HH.xml",
    selected: str = "incidenceAngleCorrection",
    offset: str = "1.5",
    gains: str | None = None,
    lut_dir: str = "",
    write_lut: bool = True,
    write_xml: bool = True,
    imagery: dict[str, list[str]] | None = None,
    xml_body: str | None = None,
    lut_body: str | None = None,
) -> Path:
    product = root / "SYNTHETIC_RS2_PRODUCT"
    product.mkdir(parents=True, exist_ok=True)

    if write_xml and xml_body is None:
        xml_body = PRODUCT_XML.format(
            banner=BANNER,
            product_type=product_type,
            acquisition_type=acquisition_type,
            pols=pols,
            bits=bits,
            lines=lines,
            samples=samples,
            data_type=data_type,
            selected=selected,
            lut_name=lut_name,
        )
    if xml_body is not None:
        (product / "product.xml").write_text(xml_body, encoding="utf-8")

    # Supplying LUT content implies writing it, so a test can override gains or
    # use a custom body without having to set write_lut explicitly.
    if write_lut or lut_body is not None or gains is not None:
        target = product / (f"{lut_dir}{lut_name}" if lut_dir else lut_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            lut_body
            if lut_body is not None
            else LUT_XML.format(
                banner=BANNER,
                offset=offset if offset is not None else "0",
                gains=gains if gains is not None else " ".join(["0.5"] * int(samples or 0)),
            ),
            encoding="utf-8",
        )

    plan = (
        {"HH": ["imagery/imagery_HH_I.tif", "imagery/imagery_HH_Q.tif"]}
        if imagery is None
        else imagery
    )
    for pol, names in plan.items():
        for name in names:
            target = product / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"II*\x00SYNTHETIC-IMAGERY-PLACEHOLDER")
    return product


def _codes(report: dict) -> set[str]:
    return {item["code"] for item in report["findings"]}


def _blocking(report: dict) -> set[str]:
    return {item["code"] for item in report["blocking"]}


def _no_lut(product: Path) -> Path:
    """Remove the lookup table and keep the reference, for missing-file cases."""
    for path in product.rglob("*.xml"):
        if path.name != "product.xml":
            path.unlink()
    return product


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_synthetic_complex_product_is_ready_for_calibration(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path))

    assert report["status"] == "ready_for_calibration"
    assert report["blocking"] == []
    assert report["product"]["data_type"] == "Complex"
    assert report["product"]["representation"] == "complex"
    assert report["product"]["data_type_element_path"] == "imageattributes/rasterattributes/datatype"
    assert report["sigma_lut"]["offset"] == pytest.approx(1.5)
    assert report["sigma_lut"]["gain_count"] == 6
    assert report["sigma_lut"]["covers_raster_width"] is True
    assert report["sigma_lut"]["lut_element_path"] == "lut"


def test_lookup_table_filename_is_element_text_not_an_attribute(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path))

    assert report["product"]["lut_reference_element_path"] == "calibration/sigmazero/lookuptable"
    assert report["sigma_lut"]["reference"] == "calibration/sigma0_polarization_HH.xml"
    assert report["product"]["lut_selected"] == "incidenceAngleCorrection"
    assert report["status"] == "ready_for_calibration"


def test_preflight_never_claims_processing_happened(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path))

    assert report["calibration_performed"] is False
    assert report["geocoding_performed"] is False
    assert report["pixels_read"] is False
    assert report["read_only"] is True
    assert report["network_used"] is False
    assert report["credentials_used"] is False
    assert "verified" not in report["status"]
    assert "ready_for_calibration" in report["semantics"]["statement"]


def test_preflight_creates_nothing_in_the_product(tmp_path: Path) -> None:
    product = make_product(tmp_path)
    before = sorted(str(p.relative_to(product)) for p in product.rglob("*"))

    cp.preflight(product)

    assert sorted(str(p.relative_to(product)) for p in product.rglob("*")) == before


# ---------------------------------------------------------------------------
# Raw source versus calibrated representation: the corrected science
# ---------------------------------------------------------------------------


def test_raw_mag_dn_is_amplitude_not_power(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, data_type="Mag", bits=16))

    raw = report["representations"]["raw_source"]
    assert raw["is_power"] is False
    assert "amplitude" in raw["stored_values"].lower()
    assert raw["representation"] == "magnitude"


def test_mag_calibration_formula_squares_dn_and_divides_by_gain(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, data_type="Mag", bits=16))

    future = report["representations"]["future_calibrated"]
    assert future["formula"] == "sigma0 = (DN**2 + offset) / gain[column]"
    assert future["gain_is_applied"] is True
    assert future["requires_magnitude_squared"] is True


def test_complex_formula_includes_gain_and_is_not_bare_magnitude_squared(
    tmp_path: Path,
) -> None:
    report = cp.preflight(make_product(tmp_path, data_type="Complex"))

    future = report["representations"]["future_calibrated"]
    assert "gain[column]" in future["formula"]
    assert future["formula"] != "sigma0 = abs(I+jQ)**2"
    assert future["gain_is_applied"] is True
    assert "abs(I+jQ)**2 ALONE is not sigma0" in future["note"]


def test_calibrated_sigma0_band_is_already_power_and_not_squared_again(
    tmp_path: Path,
) -> None:
    report = cp.preflight(make_product(tmp_path, data_type="Mag", bits=16))

    future = report["representations"]["future_calibrated"]
    assert future["output_is_power"] is True
    assert future["square_output_again"] is False
    assert future["gdal_band_metadata_item"] == "RADARSAT_2_CALIB:SIGMA0"


def test_representations_are_separate_and_no_calibration_is_done(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, data_type="Complex"))

    reps = report["representations"]
    assert reps["calibration_performed_by_this_tool"] is False
    assert reps["raw_source"]["is_power"] is False
    assert reps["future_calibrated"]["calibrated"] is False
    assert reps["raw_source"] is not reps["future_calibrated"]


def test_no_representation_rule_when_data_type_is_unknown(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, data_type="Mystery"))

    future = report["representations"]["future_calibrated"]
    assert future["formula"] is None
    assert future["gain_is_applied"] is None
    assert "DATA_TYPE_UNSUPPORTED" in _blocking(report)


def test_formula_is_not_claimed_to_be_gdal_verified(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path))

    reps = report["representations"]
    assert reps["formula_verified_against_gdal_source"] is False
    assert "NOT been verified" in reps["formula_verification_note"]


# ---------------------------------------------------------------------------
# dataType, bits, dimensions, polarizations
# ---------------------------------------------------------------------------


def test_missing_data_type_is_blocked(tmp_path: Path) -> None:
    body = PRODUCT_XML.format(
        banner=BANNER,
        product_type="SGX",
        acquisition_type="L1",
        pols="HH",
        bits=16,
        lines=4,
        samples=6,
        data_type="Complex",
        selected="incidenceAngleCorrection",
        lut_name="calibration/sigma0_polarization_HH.xml",
    ).replace("      <dataType>Complex</dataType>\n", "")
    report = cp.preflight(make_product(tmp_path, xml_body=body))

    assert "DATA_TYPE_MISSING" in _blocking(report)


@pytest.mark.parametrize("bits", ["8", "16"])
def test_mag_accepts_8_and_16_bits(tmp_path: Path, bits: str) -> None:
    report = cp.preflight(make_product(tmp_path, data_type="Mag", bits=bits))

    assert report["status"] == "ready_for_calibration"


def test_unsupported_bit_depth_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, data_type="Complex", bits=12))

    assert "BITS_UNSUPPORTED" in _blocking(report)


@pytest.mark.parametrize("field", ["numberOfLines", "numberOfSamplesPerLine"])
def test_missing_dimensions_are_blocked(tmp_path: Path, field: str) -> None:
    body = PRODUCT_XML.format(
        banner=BANNER,
        product_type="SGX",
        acquisition_type="L1",
        pols="HH",
        bits=16,
        lines=4,
        samples=6,
        data_type="Complex",
        selected="incidenceAngleCorrection",
        lut_name="calibration/sigma0_polarization_HH.xml",
    )
    body = "\n".join(line for line in body.splitlines() if f"<{field}>" not in line)
    report = cp.preflight(make_product(tmp_path, xml_body=body))

    assert "DIMENSIONS_MISSING" in _blocking(report)


def test_missing_polarizations_are_blocked(tmp_path: Path) -> None:
    body = PRODUCT_XML.format(
        banner=BANNER,
        product_type="SGX",
        acquisition_type="L1",
        pols="HH",
        bits=16,
        lines=4,
        samples=6,
        data_type="Complex",
        selected="incidenceAngleCorrection",
        lut_name="calibration/sigma0_polarization_HH.xml",
    )
    body = "\n".join(
        line for line in body.splitlines() if "transmitterReceiverPolarisation" not in line
    )
    report = cp.preflight(make_product(tmp_path, xml_body=body))

    assert "POLARIZATIONS_MISSING" in _blocking(report)


# ---------------------------------------------------------------------------
# product.xml containment and bounded scan
# ---------------------------------------------------------------------------


def test_product_xml_symlink_escaping_the_product_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.xml"
    outside.write_text("<product/>", encoding="utf-8")
    product = tmp_path / "SYNTHETIC_RS2_PRODUCT"
    product.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, product / "product.xml")

    report = cp.preflight(product)

    assert "PRODUCT_XML_PATH_ESCAPE" in _blocking(report)
    assert report["product"]["data_type"] is None


def test_product_xml_symlink_inside_the_product_is_accepted(tmp_path: Path) -> None:
    product = make_product(tmp_path, imagery={"HH": []})
    os.rename(product / "product.xml", product / "real_product.xml")
    os.symlink(product / "real_product.xml", product / "product.xml")

    report = cp.preflight(product)

    assert report["product"]["data_type"] == "Complex"
    assert "PRODUCT_XML_PATH_ESCAPE" not in _codes(report)


def test_oversized_product_xml_reports_explicit_truncation(tmp_path: Path) -> None:
    body = PRODUCT_XML.format(
        banner=BANNER,
        product_type="SGX",
        acquisition_type="L1",
        pols="HH",
        bits=16,
        lines=4,
        samples=6,
        data_type="Complex",
        selected="incidenceAngleCorrection",
        lut_name="calibration/sigma0_polarization_HH.xml",
    ) + "<!--" + ("x" * 200_000) + "-->"
    product = make_product(tmp_path, xml_body=body, write_lut=False, imagery={})
    original = cp.LIMITS["max_product_xml_bytes"]
    cp.LIMITS["max_product_xml_bytes"] = 1024
    try:
        report = cp.preflight(product)
    finally:
        cp.LIMITS["max_product_xml_bytes"] = original

    assert "PRODUCT_XML_SCAN_TRUNCATED" in _blocking(report)
    assert report["product"]["data_type"] is None


def test_oversized_lookup_table_reports_explicit_truncation(tmp_path: Path) -> None:
    product = make_product(tmp_path, write_lut=False, gains="0.5 " * 40000)
    original = cp.LIMITS["max_lut_bytes"]
    cp.LIMITS["max_lut_bytes"] = 256
    try:
        report = cp.preflight(product)
    finally:
        cp.LIMITS["max_lut_bytes"] = original

    assert "LUT_SCAN_TRUNCATED" in _blocking(report)
    assert report["sigma_lut"]["status"] == "truncated"
    assert report["sigma_lut"]["gain_count"] == 0


def test_missing_product_xml_is_blocked(tmp_path: Path) -> None:
    product = tmp_path / "SYNTHETIC_RS2_PRODUCT"
    product.mkdir(parents=True)

    report = cp.preflight(product)

    assert "PRODUCT_XML_MISSING" in _blocking(report)


def test_unparseable_product_xml_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, xml_body="<product><oops>"))

    assert "PRODUCT_XML_UNPARSEABLE" in _blocking(report)


# ---------------------------------------------------------------------------
# Lookup-table reference containment
# ---------------------------------------------------------------------------


def test_missing_lookup_table_reference_is_blocked(tmp_path: Path) -> None:
    body = PRODUCT_XML.format(
        banner=BANNER,
        product_type="SGX",
        acquisition_type="L1",
        pols="HH",
        bits=16,
        lines=4,
        samples=6,
        data_type="Complex",
        selected="incidenceAngleCorrection",
        lut_name="calibration/sigma0_polarization_HH.xml",
    )
    body = "\n".join(line for line in body.splitlines() if "lookupTable" not in line)
    report = cp.preflight(make_product(tmp_path, xml_body=body))

    assert "LUT_REFERENCE_MISSING" in _blocking(report)


def test_referenced_lookup_table_absent_is_blocked(tmp_path: Path) -> None:
    product = _no_lut(make_product(tmp_path))

    report = cp.preflight(product)

    assert "LUT_MISSING" in _blocking(report)
    assert report["sigma_lut"]["status"] == "missing"


@pytest.mark.parametrize(
    "hostile", ["../../../../etc/passwd", "/etc/passwd", "calibration/../../escape/x.xml"]
)
def test_lookup_table_reference_escaping_the_product_is_refused(
    tmp_path: Path, hostile: str
) -> None:
    product = make_product(tmp_path, lut_name=hostile, write_lut=False, imagery={"HH": []})

    report = cp.preflight(product)

    assert "LUT_PATH_ESCAPE" in _blocking(report)
    assert report["sigma_lut"]["status"] == "refused"
    assert report["sigma_lut"]["gain_count"] == 0


def test_lookup_table_symlink_outside_the_product_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "sigma0.xml"
    outside.parent.mkdir(parents=True)
    outside.write_text(LUT_XML.format(banner=BANNER, offset="1", gains="0.5 0.5"), encoding="utf-8")
    product = make_product(tmp_path, write_lut=False, imagery={"HH": []})
    (product / "calibration").mkdir(parents=True, exist_ok=True)
    os.symlink(outside, product / "calibration" / "sigma0_polarization_HH.xml")

    report = cp.preflight(product)

    assert "LUT_PATH_ESCAPE" in _blocking(report)


def test_corrupt_lookup_table_is_blocked(tmp_path: Path) -> None:
    product = make_product(tmp_path, lut_body="<lut><offset>1")

    report = cp.preflight(product)

    assert "LUT_CORRUPT" in _blocking(report)
    assert report["sigma_lut"]["status"] == "corrupt"


def test_lookup_table_without_lut_element_is_blocked(tmp_path: Path) -> None:
    product = make_product(tmp_path, lut_body="<other><offset>1</offset></other>")

    report = cp.preflight(product)

    assert "LUT_NO_LUT_ELEMENT" in _blocking(report)


# ---------------------------------------------------------------------------
# Lookup-table numerics
# ---------------------------------------------------------------------------


def test_non_finite_offset_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, offset="nan"))

    assert "LUT_OFFSET_NOT_FINITE" in _blocking(report)
    assert report["sigma_lut"]["offset_finite"] is False


def test_infinite_offset_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, offset="INF"))

    assert "LUT_OFFSET_NOT_FINITE" in _blocking(report)


def test_zero_offset_is_accepted(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, offset="0"))

    assert report["sigma_lut"]["offset_finite"] is True
    assert report["status"] == "ready_for_calibration"


def test_non_finite_gain_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, gains="0.5 0.6 nan 0.8 0.9 1.0"))

    assert "LUT_GAINS_NOT_FINITE" in _blocking(report)
    assert report["sigma_lut"]["non_finite_gain_count"] == 1


def test_infinite_gain_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, gains="0.5 0.6 inf 0.8 0.9 1.0"))

    assert "LUT_GAINS_NOT_FINITE" in _blocking(report)


def test_zero_gain_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, gains="0.5 0.6 0.0 0.8 0.9 1.0"))

    assert "LUT_GAINS_NOT_POSITIVE" in _blocking(report)
    assert report["sigma_lut"]["non_positive_gain_count"] == 1


def test_negative_gain_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, gains="0.5 0.6 -0.2 0.8 0.9 1.0"))

    assert "LUT_GAINS_NOT_POSITIVE" in _blocking(report)


def test_unparseable_gain_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, gains="0.5 0.6 abc 0.8 0.9 1.0"))

    assert "LUT_GAINS_UNPARSEABLE" in _blocking(report)


def test_gain_list_shorter_than_raster_width_is_blocked(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, samples=6, gains="0.5 0.6 0.7"))

    assert "LUT_GAINS_DO_NOT_COVER_WIDTH" in _blocking(report)
    assert report["sigma_lut"]["covers_raster_width"] is False
    assert "columns 3..5" in str(report["findings"])


def test_gain_list_longer_than_raster_width_is_accepted(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, samples=6, gains=" ".join(["0.5"] * 10)))

    assert report["sigma_lut"]["covers_raster_width"] is True
    assert report["status"] == "ready_for_calibration"


def test_gain_coverage_is_unknown_when_width_is_unknown(tmp_path: Path) -> None:
    body = PRODUCT_XML.format(
        banner=BANNER,
        product_type="SGX",
        acquisition_type="L1",
        pols="HH",
        bits=16,
        lines=4,
        samples=6,
        data_type="Complex",
        selected="incidenceAngleCorrection",
        lut_name="calibration/sigma0_polarization_HH.xml",
    )
    body = "\n".join(
        line for line in body.splitlines() if "numberOfSamplesPerLine" not in line
    )
    report = cp.preflight(make_product(tmp_path, xml_body=body))

    assert report["sigma_lut"]["covers_raster_width"] is None
    assert "WIDTH_UNKNOWN" in _codes(report)
    assert "WIDTH_UNKNOWN" not in _blocking(report)


def test_no_incidence_angle_or_width_schema_is_parsed(tmp_path: Path) -> None:
    # A gainList/pol/incidenceAngle/width document is NOT the RADARSAT-2 schema.
    body = """<?xml version="1.0" encoding="UTF-8"?>
    <lut>
      <gainList>
        <gain><pol>HH</pol><incidenceAngle>30</incidenceAngle>
        <gain>0.5</gain><width>10</width></gain>
      </gainList>
      <offset>1</offset>
    </lut>"""
    product = make_product(tmp_path, lut_body=body, write_lut=False)

    report = cp.preflight(product)

    assert report["sigma_lut"]["gain_count"] == 0
    assert "LUT_GAINS_UNPARSEABLE" in _blocking(report)


# ---------------------------------------------------------------------------
# Imagery
# ---------------------------------------------------------------------------


def test_missing_imagery_for_a_polarization_is_blocked(tmp_path: Path) -> None:
    product = make_product(
        tmp_path, pols="HH HV", imagery={"HH": ["imagery/imagery_HH_I.tif"]}
    )

    report = cp.preflight(product)

    assert "IMAGERY_MISSING_FOR_POL" in _blocking(report)
    assert report["imagery"]["missing_polarizations"] == ["HV"]


def test_complex_polarization_missing_quadrature_is_flagged(tmp_path: Path) -> None:
    product = make_product(tmp_path, imagery={"HH": ["imagery/imagery_HH_I.tif"]})

    report = cp.preflight(product)

    assert "IMAGERY_INCOMPLETE_FOR_POL" in _blocking(report)


# ---------------------------------------------------------------------------
# Drivers: RS2 is the driver; SGF/CGX are not; RCM is separate
# ---------------------------------------------------------------------------


def test_rs2_driver_is_measured_from_the_real_gdal_registry() -> None:
    capabilities = cp.gdal_driver_capabilities()

    rs2 = capabilities["drivers"]["RS2"]
    assert rs2["present"] is True, (
        "the RS2 driver must be measured as present; rasterio's filtered driver list "
        "hid it and this probe uses the full libgdal registry instead"
    )
    assert capabilities["delivered_product_driver"] == "RS2"
    assert capabilities["gdal_version"]


def test_sgf_and_cgx_are_recorded_as_not_the_rs2_driver() -> None:
    capabilities = cp.gdal_driver_capabilities()

    assert capabilities["not_the_rs2_driver"] == ["SGF", "CGX"]
    for name in ("SGF", "CGX"):
        assert capabilities["drivers"][name]["present"] is False
        assert capabilities["drivers"][name]["route_claimed"] is False


def test_rcm_is_reported_as_a_separate_driver_not_a_fallback() -> None:
    capabilities = cp.gdal_driver_capabilities()

    separate = capabilities["separate_driver"]
    assert separate["name"] == "RCM"
    assert "separate" in separate["relationship"]
    assert "not mixed" in separate["relationship"]


def test_product_type_is_never_used_as_a_driver_name(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path, product_type="SGX"))

    capabilities = report["gdal_capabilities"]
    assert "productType" in capabilities["product_type_is_not_a_driver"]
    assert "never used to infer a" in capabilities["product_type_is_not_a_driver"]
    assert "SGX" not in {item["code"] for item in report["findings"]}


def test_gdal_calib_domain_constants_are_the_verified_ones() -> None:
    assert cp.DELIVERED_PRODUCT_DRIVER == "RS2"
    assert cp.SEPARATE_DRIVER == "RCM"
    assert cp.NOT_THE_RS2_DRIVER == ("SGF", "CGX")


def test_report_never_claims_sgf_is_the_route(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path))
    blob = json.dumps(report).lower()

    assert "sgf is the driver" not in blob
    assert "sgf driver is present" not in blob


def test_probe_source_names_the_full_registry_not_a_filtered_list() -> None:
    capabilities = cp.gdal_driver_capabilities()

    assert capabilities["probe_is_measurement"] is True
    assert "full GDAL driver registry" in capabilities["probe_source"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_writes_json_outside_the_product(tmp_path: Path) -> None:
    product = make_product(tmp_path)
    out = tmp_path / "report.json"

    completed = subprocess.run(
        [sys.executable, "-m", "processing.calibration_preflight", str(product), "--out", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["tool"] == "processing.calibration_preflight"
    assert payload["network_used"] is False
    assert payload["credentials_used"] is False
    assert not out.is_relative_to(product)


def test_cli_refuses_to_write_inside_the_product_directory(tmp_path: Path) -> None:
    product = make_product(tmp_path)
    out = product / "report.json"

    completed = subprocess.run(
        [sys.executable, "-m", "processing.calibration_preflight", str(product), "--out", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "inside the read-only product directory" in completed.stderr
    assert not out.exists()


def test_cli_returns_nonzero_for_a_missing_directory(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "processing.calibration_preflight", str(tmp_path / "nope")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "error" in completed.stderr.lower()


def test_report_is_json_serialisable(tmp_path: Path) -> None:
    report = cp.preflight(make_product(tmp_path))

    json.dumps(report)