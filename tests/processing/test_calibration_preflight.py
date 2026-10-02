"""Tests for the read-only RADARSAT-2 calibration preflight.

Scope and honesty notes:

* Every fixture here is SYNTHETIC and built inside pytest ``tmp_path``. The
  product XML is a *tiny, hand-written* imitation that reproduces the authentic
  RADARSAT-2 ``product.xml`` element paths and the CSA sigma0 lookup-table
  element paths, with obviously-fake values. No real delivered product, no real
  backscatter, no synthetic stand-in for a real acquisition, and no credentials
  or network access are involved.
* These tests assert what the preflight *refuses* to claim. They never assert
  that a product is georeferenced, calibrated or processed.
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


# ---------------------------------------------------------------------------
# Synthetic fixtures (tiny, clearly not a real product)
# ---------------------------------------------------------------------------

SYNTHETIC_BANNER = "SYNTHETIC TEST FIXTURE - NOT A REAL RADARSAT-2 PRODUCT"

# Authentic RADARSAT-2 element paths used below:
#   product/imageAttributes/{productType, sampleType, bitsPerSample,
#     numberOfLines, numberOfSamplesPerLine, nearRangeIncidenceAngle,
#     farRangeIncidenceAngle, transmitterReceiverPolarisation}
#   product/calibration/calibrationLookupTable/@href
#   product/calibration/noiseLookupTable/@href
#   product/imageGeometry/imageTiePoints/...
PRODUCT_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!-- {banner}. Values are invented for schema testing only. -->
<product xmlns:gml="http://www.opengis.net/gml">
  <productIdentifier>
    <name>SYNTHETIC_RS2_TEST_PRODUCT</name>
  </productIdentifier>
  <imageAttributes>
    <productType>{product_type}</productType>
    <acquisitionMode>STANDARD</acquisitionMode>
    <beamMode>IW3</beamMode>
    <transmitterReceiverPolarisation>{pols}</transmitterReceiverPolarisation>
    <numberOfLines>{lines}</numberOfLines>
    <numberOfSamplesPerLine>{samples}</numberOfSamplesPerLine>
    <sampleType>{sample_type}</sampleType>
    <bitsPerSample>{bits}</bitsPerSample>
    <nearRangeIncidenceAngle>{near_angle}</nearRangeIncidenceAngle>
    <farRangeIncidenceAngle>{far_angle}</farRangeIncidenceAngle>
    <productFirstLineUtcTime>2019-03-17T11:00:12.000000Z</productFirstLineUtcTime>
  </imageAttributes>
  <calibration>
    <calibrationLookupTable href="{lut_href}"/>
    <noiseLookupTable href="calibration/noise.xml"/>
  </calibration>
</product>
"""

# Authentic CSA sigma0 lookup-table element paths used below:
#   sigmaZeroLookupTable/lut/gainList/gain/{pol, step, incidenceAngle, gain, width}
#   sigmaZeroLookupTable/lut/offsetList/offset/{pol, step, incidenceAngle, offset}
SIGMA_LUT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!-- {banner}. Gains/widths/offsets are invented for schema testing only. -->
<sigmaZeroLookupTable>
  <lut>
    <gainList>
{gains}
    </gainList>
    <offsetList>
{offsets}
    </offsetList>
  </lut>
</sigmaZeroLookupTable>
"""

GAIN_ENTRY = """      <gain>
        <pol>{pol}</pol>
        <step>SSG1</step>
        <incidenceAngle>{angle}</incidenceAngle>
        <gain>{gain}</gain>
        <width>{width}</width>
      </gain>"""

OFFSET_ENTRY = """      <offset>
        <pol>{pol}</pol>
        <step>SSG1</step>
        <incidenceAngle>{angle}</incidenceAngle>
        <offset>{offset}</offset>
      </offset>"""


def _gain(pol: str, angle: float, gain: float, width: float) -> str:
    return GAIN_ENTRY.format(pol=pol, angle=angle, gain=gain, width=width)


def _offset(pol: str, angle: float, value: float) -> str:
    return OFFSET_ENTRY.format(pol=pol, angle=angle, offset=value)


def write_sigma_lut(
    path: Path,
    *,
    gains: list[str],
    offsets: list[str] | None = None,
    body: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if body is None:
        body = SIGMA_LUT_TEMPLATE.format(
            banner=SYNTHETIC_BANNER,
            gains="\n".join(gains),
            offsets="\n".join(offsets if offsets is not None else []),
        )
    path.write_text(body, encoding="utf-8")
    return path


def make_product(
    root: Path,
    *,
    product_type: str = "SLC",
    sample_type: str = "COMPLEX_IQ",
    bits: int | str = 32,
    pols: str = "HH",
    lines: int | str = 8,
    samples: int | str = 8,
    near_angle: float | str = 30.0,
    far_angle: float | str = 50.0,
    lut_href: str = "calibration/sigma0.xml",
    write_lut: bool = True,
    lut_body: str | None = None,
    lut_gains: list[str] | None = None,
    lut_offsets: list[str] | None = None,
    imagery: dict[str, list[str]] | None = None,
    write_product_xml: bool = True,
    product_xml_body: str | None = None,
) -> Path:
    """Create a synthetic product directory and return its path."""
    product = root / "SYNTHETIC_RS2_TEST_PRODUCT"
    product.mkdir(parents=True, exist_ok=True)

    if product_xml_body is None and write_product_xml:
        product_xml_body = PRODUCT_XML_TEMPLATE.format(
            banner=SYNTHETIC_BANNER,
            product_type=product_type,
            sample_type=sample_type,
            bits=bits,
            pols=pols,
            lines=lines,
            samples=samples,
            near_angle=near_angle,
            far_angle=far_angle,
            lut_href=lut_href,
        )
    if product_xml_body is not None:
        (product / "product.xml").write_text(product_xml_body, encoding="utf-8")

    # Writing the lookup table is implied whenever LUT content is supplied
    # explicitly, so a test can override entries without disabling the file.
    if lut_gains is not None or lut_offsets is not None or lut_body is not None:
        write_lut = True

    if write_lut:
        gains = lut_gains
        if gains is None:
            # Bins of width 20 centred at 30/50 cover [20,60]: the [30,50] span.
            gains = [_gain("HH", 30.0, 0.62, 20.0), _gain("HH", 50.0, 0.55, 20.0)]
        write_sigma_lut(
            product / "calibration" / "sigma0.xml",
            gains=gains,
            offsets=lut_offsets
            if lut_offsets is not None
            else [_offset("HH", 30.0, 0.0), _offset("HH", 50.0, 0.0)],
            body=lut_body,
        )

    # Minimal but real TIFF-ish placeholder bytes are not enough for GDAL; the
    # preflight only requires that the referenced imagery file exists, so a small
    # deterministic payload is used. ``imagery={}`` means "no imagery at all".
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
    return {finding["code"] for finding in report["findings"]}


def _blocking_codes(report: dict) -> set[str]:
    return {finding["code"] for finding in report["blocking"]}


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_synthetic_complex_product_is_ready_for_calibration(tmp_path: Path) -> None:
    product = make_product(tmp_path)

    report = cp.preflight(product)

    assert report["status"] == "ready_for_calibration"
    assert report["ready_for_calibration"] is True
    assert report["blocking"] == []
    assert report["product"]["sample_representation"] == "complex_iq"
    assert report["product"]["dimensions"] == {"number_of_lines": 8, "number_of_samples_per_line": 8}
    assert report["product"]["polarizations"] == ["HH"]
    assert report["sigma_lut"]["contained"] is True
    assert report["sigma_lut"]["gain_count"] == 2
    assert report["sigma_lut"]["offset_count"] == 2


def test_preflight_never_claims_the_product_was_processed(tmp_path: Path) -> None:
    product = make_product(tmp_path)

    report = cp.preflight(product)

    assert report["status"] == "ready_for_calibration"
    assert "verified" not in report["status"]
    assert report["calibration_performed"] is False
    assert report["geocoding_performed"] is False
    assert report["pixels_read"] is False
    assert report["read_only"] is True
    assert "ready_for_calibration" in report["semantics"]["statement"]


def test_preflight_creates_no_files_in_the_product_directory(tmp_path: Path) -> None:
    product = make_product(tmp_path)

    def snapshot() -> list[str]:
        return sorted(str(p.relative_to(product)) for p in product.rglob("*"))

    before = snapshot()
    cp.preflight(product)
    after = snapshot()

    assert before == after


# ---------------------------------------------------------------------------
# Detected (Mag) versus Complex power semantics
# ---------------------------------------------------------------------------


def test_detected_magnitude_samples_are_already_linear_power(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        product_type="SGF",
        sample_type="MAG",
        bits=16,
        imagery={"HH": ["imagery/image_HH.tif"]},
    )

    report = cp.preflight(product)

    assert report["status"] == "ready_for_calibration"
    assert report["product"]["sample_representation"] == "magnitude"
    output = report["calibration_output"]
    assert output["expected_band_semantics"] == "linear_power_sigma0"
    assert output["apply_magnitude_squared"] is False
    assert "do not square" in output["rule"].lower()


def test_complex_samples_require_magnitude_squared(tmp_path: Path) -> None:
    product = make_product(tmp_path)

    report = cp.preflight(product)

    output = report["calibration_output"]
    assert report["product"]["sample_representation"] == "complex_iq"
    assert output["expected_band_semantics"] == "complex_iq_pairs"
    assert output["apply_magnitude_squared"] is True
    assert "magnitude" in output["rule"].lower()


def test_magnitude_and_complex_do_not_share_a_power_rule(tmp_path: Path) -> None:
    detected = cp.preflight(
        make_product(
            tmp_path / "detected",
            product_type="SGF",
            sample_type="MAG",
            bits=16,
            imagery={"HH": ["imagery/image_HH.tif"]},
        )
    )
    complex_ = cp.preflight(make_product(tmp_path / "complex"))

    assert (
        detected["calibration_output"]["expected_band_semantics"]
        != complex_["calibration_output"]["expected_band_semantics"]
    )


# ---------------------------------------------------------------------------
# product.xml problems
# ---------------------------------------------------------------------------


def test_missing_product_xml_is_blocked(tmp_path: Path) -> None:
    product = make_product(tmp_path, write_product_xml=False)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "PRODUCT_XML_MISSING" in _blocking_codes(report)


def test_unparseable_product_xml_is_blocked(tmp_path: Path) -> None:
    product = make_product(tmp_path, product_xml_body="<product><unclosed>")

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "PRODUCT_XML_UNPARSEABLE" in _blocking_codes(report)


def test_unknown_sample_type_is_unsupported(tmp_path: Path) -> None:
    product = make_product(tmp_path, sample_type="WEIRD")

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SAMPLE_TYPE_UNSUPPORTED" in _blocking_codes(report)


def test_absent_sample_type_is_blocked(tmp_path: Path) -> None:
    body = PRODUCT_XML_TEMPLATE.format(
        banner=SYNTHETIC_BANNER,
        product_type="SLC",
        sample_type="COMPLEX_IQ",
        bits=32,
        pols="HH",
        lines=8,
        samples=8,
        near_angle=30.0,
        far_angle=50.0,
        lut_href="calibration/sigma0.xml",
    ).replace("    <sampleType>COMPLEX_IQ</sampleType>\n", "")
    product = make_product(tmp_path, product_xml_body=body)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SAMPLE_TYPE_MISSING" in _blocking_codes(report)


def test_unsupported_bit_depth_is_blocked(tmp_path: Path) -> None:
    product = make_product(tmp_path, bits=12)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "BITS_UNSUPPORTED" in _blocking_codes(report)


def test_missing_bit_depth_is_blocked(tmp_path: Path) -> None:
    body = PRODUCT_XML_TEMPLATE.format(
        banner=SYNTHETIC_BANNER,
        product_type="SLC",
        sample_type="COMPLEX_IQ",
        bits=32,
        pols="HH",
        lines=8,
        samples=8,
        near_angle=30.0,
        far_angle=50.0,
        lut_href="calibration/sigma0.xml",
    ).replace("    <bitsPerSample>32</bitsPerSample>\n", "")
    product = make_product(tmp_path, product_xml_body=body)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "BITS_MISSING" in _blocking_codes(report)


@pytest.mark.parametrize("field", ["numberOfLines", "numberOfSamplesPerLine"])
def test_missing_dimensions_are_blocked(tmp_path: Path, field: str) -> None:
    body = PRODUCT_XML_TEMPLATE.format(
        banner=SYNTHETIC_BANNER,
        product_type="SLC",
        sample_type="COMPLEX_IQ",
        bits=32,
        pols="HH",
        lines=8,
        samples=8,
        near_angle=30.0,
        far_angle=50.0,
        lut_href="calibration/sigma0.xml",
    )
    body = "\n".join(
        line for line in body.splitlines() if f"<{field}>" not in line
    )
    product = make_product(tmp_path, product_xml_body=body)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "DIMENSIONS_MISSING" in _blocking_codes(report)


def test_missing_polarizations_are_blocked(tmp_path: Path) -> None:
    body = PRODUCT_XML_TEMPLATE.format(
        banner=SYNTHETIC_BANNER,
        product_type="SLC",
        sample_type="COMPLEX_IQ",
        bits=32,
        pols="HH",
        lines=8,
        samples=8,
        near_angle=30.0,
        far_angle=50.0,
        lut_href="calibration/sigma0.xml",
    )
    body = "\n".join(
        line
        for line in body.splitlines()
        if "transmitterReceiverPolarisation" not in line
    )
    product = make_product(tmp_path, product_xml_body=body)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "POLARIZATIONS_MISSING" in _blocking_codes(report)


# ---------------------------------------------------------------------------
# Sigma LUT reference, containment and reading
# ---------------------------------------------------------------------------


def test_absent_sigma_lut_reference_is_blocked(tmp_path: Path) -> None:
    product = make_product(tmp_path, write_lut=False)
    body = (product / "product.xml").read_text(encoding="utf-8")
    body = "\n".join(
        line for line in body.splitlines() if "calibrationLookupTable" not in line
    )
    (product / "product.xml").write_text(body, encoding="utf-8")

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_REFERENCE_MISSING" in _blocking_codes(report)


def test_referenced_sigma_lut_file_absent_is_blocked(tmp_path: Path) -> None:
    product = make_product(tmp_path, write_lut=False)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_MISSING" in _blocking_codes(report)
    assert report["sigma_lut"]["parse_status"] == "missing"


@pytest.mark.parametrize(
    "hostile_href",
    [
        "../../../../etc/passwd",
        "/etc/passwd",
        "calibration/../../escape/sigma0.xml",
    ],
)
def test_sigma_lut_reference_escaping_the_product_is_refused(
    tmp_path: Path, hostile_href: str
) -> None:
    product = make_product(tmp_path, write_lut=False, lut_href=hostile_href)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_PATH_ESCAPE" in _blocking_codes(report)
    assert report["sigma_lut"]["contained"] is False
    # The hostile target must never have been read.
    assert report["sigma_lut"]["parse_status"] == "not_attempted"


def test_symlinked_sigma_lut_pointing_outside_the_product_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "sigma0.xml"
    write_sigma_lut(outside, gains=[_gain("HH", 40.0, 0.5, 40.0)])
    product = make_product(tmp_path, write_lut=False)
    link = product / "calibration" / "sigma0.xml"
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, link)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_PATH_ESCAPE" in _blocking_codes(report)
    assert report["sigma_lut"]["parse_status"] == "not_attempted"


def test_symlinked_sigma_lut_inside_the_product_is_accepted(tmp_path: Path) -> None:
    product = make_product(tmp_path, write_lut=False)
    real = product / "calibration" / "real_sigma0.xml"
    write_sigma_lut(
        real,
        gains=[_gain("HH", 30.0, 0.62, 20.0), _gain("HH", 50.0, 0.55, 20.0)],
        offsets=[_offset("HH", 30.0, 0.0), _offset("HH", 50.0, 0.0)],
    )
    os.symlink(real, product / "calibration" / "sigma0.xml")

    report = cp.preflight(product)

    assert report["status"] == "ready_for_calibration"
    assert report["sigma_lut"]["contained"] is True
    assert report["sigma_lut"]["gain_count"] == 2


def test_absolute_symlinked_calibration_directory_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside_lut_dir"
    write_sigma_lut(
        outside / "sigma0.xml",
        gains=[_gain("HH", 40.0, 0.5, 40.0)],
        offsets=[_offset("HH", 40.0, 0.0)],
    )
    product = make_product(tmp_path, write_lut=False)
    os.symlink(outside, product / "calibration")

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_PATH_ESCAPE" in _blocking_codes(report)


def test_corrupt_sigma_lut_is_blocked(tmp_path: Path) -> None:
    product = make_product(tmp_path, lut_body="<sigmaZeroLookupTable><lut>")

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_CORRUPT" in _blocking_codes(report)
    assert report["sigma_lut"]["parse_status"] == "corrupt"


def test_empty_sigma_lut_is_blocked(tmp_path: Path) -> None:
    body = SIGMA_LUT_TEMPLATE.format(banner=SYNTHETIC_BANNER, gains="", offsets="")
    product = make_product(tmp_path, lut_body=body)

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_NO_ENTRIES" in _blocking_codes(report)


# ---------------------------------------------------------------------------
# Sigma LUT numeric gates
# ---------------------------------------------------------------------------


def test_non_finite_gain_is_blocked(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        write_lut=False,
        lut_gains=[_gain("HH", 30.0, "NaN", 20.0), _gain("HH", 50.0, 0.55, 20.0)],
    )

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_GAIN_NOT_FINITE" in _blocking_codes(report)


def test_infinite_gain_is_blocked(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        write_lut=False,
        lut_gains=[_gain("HH", 30.0, "INF", 20.0), _gain("HH", 50.0, 0.55, 20.0)],
    )

    report = cp.preflight(product)

    assert "SIGMA_LUT_GAIN_NOT_FINITE" in _blocking_codes(report)


def test_zero_gain_is_blocked(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        write_lut=False,
        lut_gains=[_gain("HH", 30.0, 0.0, 20.0), _gain("HH", 50.0, 0.55, 20.0)],
    )

    report = cp.preflight(product)

    assert "SIGMA_LUT_GAIN_NOT_POSITIVE" in _blocking_codes(report)


def test_negative_gain_is_blocked(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        write_lut=False,
        lut_gains=[_gain("HH", 30.0, -0.2, 20.0), _gain("HH", 50.0, 0.55, 20.0)],
    )

    report = cp.preflight(product)

    assert "SIGMA_LUT_GAIN_NOT_POSITIVE" in _blocking_codes(report)


def test_non_finite_offset_is_blocked(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        write_lut=False,
        lut_offsets=[_offset("HH", 30.0, "nan"), _offset("HH", 50.0, 0.0)],
    )

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_OFFSET_NOT_FINITE" in _blocking_codes(report)


def test_zero_offset_is_accepted(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        write_lut=False,
        lut_offsets=[_offset("HH", 30.0, "0"), _offset("HH", 50.0, "0.0")],
    )

    report = cp.preflight(product)

    assert report["status"] == "ready_for_calibration"
    assert report["sigma_lut"]["offset_finite"] is True


def test_gain_missing_for_a_declared_polarization_is_blocked(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        pols="HH HV",
        write_lut=False,
        lut_gains=[_gain("HH", 30.0, 0.62, 20.0), _gain("HH", 50.0, 0.55, 20.0)],
        lut_offsets=[_offset("HH", 30.0, 0.0), _offset("HH", 50.0, 0.0)],
        imagery={"HH": ["imagery/imagery_HH_I.tif"], "HV": ["imagery/imagery_HV_I.tif"]},
    )

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_NO_GAIN_FOR_POL" in _blocking_codes(report)
    assert "HV" in str(report["sigma_lut"]["poles_without_gains"])


def test_width_coverage_gap_is_blocked(tmp_path: Path) -> None:
    # Bins centred at 30 and 50, each 20 wide, cover [20, 40] and [40, 60].
    # The product declares a required span of [30, 55] -> still covered, so
    # instead declare [20, 60] exactly and shrink one bin to open a gap.
    product = make_product(
        tmp_path,
        near_angle=20.0,
        far_angle=60.0,
        write_lut=False,
        lut_gains=[_gain("HH", 30.0, 0.62, 10.0), _gain("HH", 50.0, 0.55, 10.0)],
    )

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_WIDTH_COVERAGE_GAP" in _blocking_codes(report)
    assert report["sigma_lut"]["width_coverage"]["covered"] is False
    assert report["sigma_lut"]["width_coverage"]["uncovered_ranges"]


def test_width_coverage_is_reported_per_polarization(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        pols="HH HV",
        write_lut=False,
        lut_gains=[
            _gain("HH", 30.0, 0.62, 20.0),
            _gain("HH", 50.0, 0.55, 20.0),
            _gain("HV", 30.0, 0.61, 4.0),
            _gain("HV", 50.0, 0.54, 4.0),
        ],
        lut_offsets=[_offset("HH", 30.0, 0.0), _offset("HV", 30.0, 0.0)],
        imagery={"HH": ["imagery/imagery_HH_I.tif"], "HV": ["imagery/imagery_HV_I.tif"]},
    )

    report = cp.preflight(product)

    assert report["sigma_lut"]["width_coverage"]["per_polarization"]["HH"]["covered"] is True
    assert report["sigma_lut"]["width_coverage"]["per_polarization"]["HV"]["covered"] is False
    assert "SIGMA_LUT_WIDTH_COVERAGE_GAP" in _blocking_codes(report)


def test_absent_incidence_range_downgrades_coverage_to_unknown(tmp_path: Path) -> None:
    body = PRODUCT_XML_TEMPLATE.format(
        banner=SYNTHETIC_BANNER,
        product_type="SLC",
        sample_type="COMPLEX_IQ",
        bits=32,
        pols="HH",
        lines=8,
        samples=8,
        near_angle=30.0,
        far_angle=50.0,
        lut_href="calibration/sigma0.xml",
    )
    body = "\n".join(
        line
        for line in body.splitlines()
        if "RangeIncidenceAngle" not in line
    )
    product = make_product(tmp_path, product_xml_body=body)

    report = cp.preflight(product)

    coverage = report["sigma_lut"]["width_coverage"]
    assert coverage["required_range"] is None
    assert coverage["evaluated"] is False
    assert "INCIDENCE_RANGE_MISSING" in _codes(report)
    assert "INCIDENCE_RANGE_MISSING" not in _blocking_codes(report)


def test_width_semantics_convention_is_recorded(tmp_path: Path) -> None:
    product = make_product(tmp_path)

    report = cp.preflight(product)

    coverage = report["sigma_lut"]["width_coverage"]
    assert coverage["convention"] == "bin-width"
    assert coverage["convention_verified_against_real_product"] is False


# ---------------------------------------------------------------------------
# Imagery existence
# ---------------------------------------------------------------------------


def test_missing_imagery_for_a_polarization_is_blocked(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        pols="HH HV",
        write_lut=False,
        lut_gains=[
            _gain("HH", 30.0, 0.62, 20.0),
            _gain("HH", 50.0, 0.55, 20.0),
            _gain("HV", 30.0, 0.61, 20.0),
            _gain("HV", 50.0, 0.54, 20.0),
        ],
        lut_offsets=[_offset("HH", 30.0, 0.0), _offset("HV", 30.0, 0.0)],
        imagery={"HH": ["imagery/imagery_HH_I.tif"]},
    )

    report = cp.preflight(product)

    assert report["status"] == "blocked"
    assert "IMAGERY_MISSING_FOR_POL" in _blocking_codes(report)
    assert report["imagery"]["missing_polarizations"] == ["HV"]


def test_no_imagery_at_all_is_blocked(tmp_path: Path) -> None:
    product = make_product(tmp_path, imagery={})

    report = cp.preflight(product)

    assert "IMAGERY_MISSING_FOR_POL" in _blocking_codes(report)


def test_complex_polarization_is_split_into_i_and_q_pairs(tmp_path: Path) -> None:
    product = make_product(
        tmp_path,
        imagery={"HH": ["imagery/imagery_HH_I.tif", "imagery/imagery_HH_Q.tif"]},
    )

    report = cp.preflight(product)

    assert report["status"] == "ready_for_calibration"
    entry = report["imagery"]["by_polarization"]["HH"]
    assert entry["files"] == [
        "imagery/imagery_HH_I.tif",
        "imagery/imagery_HH_Q.tif",
    ]
    assert entry["components"] == ["I", "Q"]


def test_complex_polarization_missing_one_component_is_flagged(tmp_path: Path) -> None:
    product = make_product(tmp_path, imagery={"HH": ["imagery/imagery_HH_I.tif"]})

    report = cp.preflight(product)

    assert "IMAGERY_INCOMPLETE_FOR_POL" in _blocking_codes(report)


def test_oversized_sigma_lut_is_refused_without_parsing(tmp_path: Path) -> None:
    product = make_product(tmp_path)

    report = cp.preflight(product, max_lut_bytes=16)

    assert report["status"] == "blocked"
    assert "SIGMA_LUT_TOO_LARGE" in _blocking_codes(report)
    assert report["sigma_lut"]["parse_status"] == "too_large"
    assert report["sigma_lut"]["gain_count"] == 0


def test_invalid_width_semantics_is_rejected(tmp_path: Path) -> None:
    product = make_product(tmp_path)

    with pytest.raises(ValueError):
        cp.preflight(product, width_semantics="guess")


# ---------------------------------------------------------------------------
# GDAL driver capability: measured, never assumed
# ---------------------------------------------------------------------------


def test_gdal_capabilities_are_measured_from_the_installed_gdal() -> None:
    capabilities = cp.gdal_driver_capabilities()

    assert capabilities["probe_source"]
    assert set(capabilities["drivers"]).issuperset({"SGF", "CGX", "GTiff"})
    for name, record in capabilities["drivers"].items():
        assert isinstance(record["present"], bool), name
    if capabilities.get("gdal_version"):
        assert capabilities["gdal_version"]


def test_sgf_presence_matches_the_actual_gdal_registry() -> None:
    capabilities = cp.gdal_driver_capabilities()
    truth = capabilities["drivers"]["SGF"]["present"]

    if not truth:
        # No SGF driver here: the preflight must say so and must not fabricate
        # a route, and must not call SGF ScanSAR or geocoded.
        assert capabilities["drivers"]["SGF"]["route_claimed"] is False
        assert "not available" in capabilities["drivers"]["SGF"]["note"].lower() or (
            "absent" in capabilities["drivers"]["SGF"]["note"].lower()
        )


def test_preflight_never_claims_sgf_is_scansar_or_geocoded(tmp_path: Path) -> None:
    product = make_product(tmp_path, product_type="SGF", sample_type="MAG", bits=16)

    report = cp.preflight(product)

    sgf = report["gdal_capabilities"]["drivers"]["SGF"]
    assert sgf.get("scansar") is None
    assert sgf.get("geocoded") is None
    blob = json.dumps(report).lower()
    for forbidden in ("sgf is scansar", "sgf is geocoded", "sgf is ground range detected"):
        assert forbidden not in blob


def test_driver_route_is_never_forced_when_the_driver_is_absent(tmp_path: Path) -> None:
    product = make_product(tmp_path, product_type="SGF", sample_type="MAG", bits=16)

    strict = cp.preflight(product, require_gdal_driver=True)
    lenient = cp.preflight(product, require_gdal_driver=False)

    if not cp.gdal_driver_capabilities()["drivers"]["SGF"]["present"]:
        assert strict["status"] == "blocked"
        assert "GDAL_DRIVER_UNAVAILABLE" in _blocking_codes(strict)
        assert lenient["status"] != "blocked"
        assert "GDAL_DRIVER_UNAVAILABLE" in _codes(lenient)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_emits_json_and_reports_blocked_status(tmp_path: Path) -> None:
    product = make_product(tmp_path, lut_body="<broken>")
    out = tmp_path / "preflight.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "processing.calibration_preflight",
            str(product),
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["tool"] == "processing.calibration_preflight"
    assert payload["network_used"] is False
    assert payload["credentials_used"] is False


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
    product = make_product(tmp_path)

    report = cp.preflight(product)

    json.dumps(report)  # must not raise
