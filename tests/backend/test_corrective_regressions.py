"""Corrective regressions for the three reproduced gaps.

1. Non-finite numbers must be rejected wherever they appear, including inside
   unknown extension keys, instead of reaching the response serialiser.
2. A CORS preflight from an allowed origin must actually succeed, and a
   disallowed origin must get no allow header.
3. A declared preview must really decode: invalid scanline filters and a
   truncated zlib stream must be refused even when Pillow is absent.
"""

from __future__ import annotations

import builtins
import json
import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import pngcheck
from backend.app import create_app
from conftest import reencode_png, write_bundle

DATA_ENDPOINTS = ("/api/analysis", "/api/regions", "/api/regions/synthetic-region-1")


def client_for(tmp_path: Path, name: str) -> TestClient:
    bundle = write_bundle(tmp_path / name)
    return TestClient(create_app(bundle_dir=bundle), raise_server_exceptions=False)


def rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"fixture marker missing: {old!r}"
    path.write_text(text.replace(old, new, 1))


def assert_clean_error(client: TestClient) -> None:
    status = client.get("/api/status")
    assert status.headers["content-type"].startswith("application/json")
    body = status.json()
    assert body["state"] == "error"
    for path in DATA_ENDPOINTS:
        assert client.get(path).status_code == 503, path


# ---------------------------------------------------------------------------
# 1. finite numbers everywhere
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("literal", ["1e400", "-1e400", "1e999", "1.7976931348623159e999"])
def test_overflowing_float_literal_is_rejected(tmp_path: Path, literal: str) -> None:
    client = client_for(tmp_path, f"of-{literal}")
    bundle = Path(client.app.state.bundle_dir)
    rewrite(bundle / "analysis.json", '"schema_version": 1',
            f'"schema_version": 1, "vendor_extension": {{"value": {literal}}}')
    assert_clean_error(client)


def test_overflowing_float_in_nested_unknown_key_rejected(tmp_path: Path) -> None:
    client = client_for(tmp_path, "nested")
    bundle = Path(client.app.state.bundle_dir)
    rewrite(
        bundle / "regions.geojson",
        '"type": "FeatureCollection"',
        '"type": "FeatureCollection", "x": [[[{"deep": [1, 2, {"w": 1e400}]}]]]',
    )
    assert_clean_error(client)


def test_overflowing_float_in_known_numeric_field_rejected(tmp_path: Path) -> None:
    client = client_for(tmp_path, "known")
    bundle = Path(client.app.state.bundle_dir)
    rewrite(bundle / "analysis.json", '"threshold_db": 1.0', '"threshold_db": 1e400')
    assert_clean_error(client)


def test_huge_integer_literal_is_rejected_without_crashing(tmp_path: Path) -> None:
    client = client_for(tmp_path, "bignum")
    bundle = Path(client.app.state.bundle_dir)
    rewrite(bundle / "regions.geojson", '"area_ha": 1.25', '"area_ha": ' + "9" * 400)
    assert_clean_error(client)


def test_negative_huge_integer_literal_rejected(tmp_path: Path) -> None:
    client = client_for(tmp_path, "negbignum")
    bundle = Path(client.app.state.bundle_dir)
    rewrite(bundle / "regions.geojson", '"area_ha": 1.25', '"area_ha": -' + "9" * 400)
    assert_clean_error(client)


def test_legal_extension_keys_are_still_accepted(tmp_path: Path) -> None:
    client = client_for(tmp_path, "legal")
    bundle = Path(client.app.state.bundle_dir)
    rewrite(
        bundle / "analysis.json",
        '"schema_version": 1',
        '"schema_version": 1, "vendor_extension": {"producer": "acme", '
        '"version": [1, 2, 3], "note": null, "ratio": 0.5, "deep": {"a": {"b": 1.5}}}',
    )
    body = client.get("/api/status").json()
    assert body["state"] == "ready"
    served = client.get("/api/analysis").json()
    assert served["vendor_extension"] == {
        "producer": "acme",
        "version": [1, 2, 3],
        "note": None,
        "ratio": 0.5,
        "deep": {"a": {"b": 1.5}},
    }


def test_large_but_finite_numbers_are_accepted(tmp_path: Path) -> None:
    client = client_for(tmp_path, "finite")
    bundle = Path(client.app.state.bundle_dir)
    rewrite(bundle / "analysis.json", '"schema_version": 1',
            '"schema_version": 1, "vendor_extension": {"big": 1e308, "small": -1e-308, '
            '"exp": 1e300}')
    assert client.get("/api/status").json()["state"] == "ready"
    assert client.get("/api/analysis").json()["vendor_extension"]["big"] == 1e308


def test_non_finite_then_repaired_returns_to_ready(tmp_path: Path) -> None:
    client = client_for(tmp_path, "lifecycle")
    bundle = Path(client.app.state.bundle_dir)
    good = (bundle / "analysis.json").read_text()
    rewrite(bundle / "analysis.json", '"threshold_db": 1.0', '"threshold_db": 1e400')
    assert_clean_error(client)
    assert client.get("/api/analysis").status_code == 503
    (bundle / "analysis.json").write_text(good)
    assert client.get("/api/status").json()["state"] == "ready"
    assert client.get("/api/analysis").status_code == 200


def test_regions_lifecycle_with_non_finite(tmp_path: Path) -> None:
    client = client_for(tmp_path, "regionslife")
    bundle = Path(client.app.state.bundle_dir)
    good = (bundle / "regions.geojson").read_text()
    rewrite(bundle / "regions.geojson", '"area_ha": 1.25', '"area_ha": 1e400')
    assert_clean_error(client)
    (bundle / "regions.geojson").write_text(good)
    assert client.get("/api/regions").status_code == 200


# ---------------------------------------------------------------------------
# 2. CORS preflight
# ---------------------------------------------------------------------------


PREFLIGHT = {
    "Origin": "http://localhost:5173",
    "Access-Control-Request-Method": "GET",
    "Access-Control-Request-Headers": "content-type",
}


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://localhost:5173"])
def test_preflight_from_allowed_origin_succeeds(client: TestClient, origin: str) -> None:
    response = client.options(
        "/api/status", headers={**PREFLIGHT, "Origin": origin}
    )
    assert response.status_code in (200, 204)
    assert response.headers["access-control-allow-origin"] == origin
    allowed = response.headers["access-control-allow-methods"]
    assert "GET" in allowed
    assert "HEAD" in allowed
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "content-type" in allowed_headers
    assert "accept" in allowed_headers
    assert response.headers["access-control-max-age"]


def test_preflight_works_on_every_data_route(client: TestClient) -> None:
    for path in ("/api/status", "/api/analysis", "/api/regions",
                 "/api/regions/synthetic-region-1", "/api/imagery/before"):
        response = client.options(path, headers=PREFLIGHT)
        assert response.status_code in (200, 204), path
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173", path


def test_preflight_from_disallowed_origin_gets_no_allow_header(client: TestClient) -> None:
    response = client.options(
        "/api/status",
        headers={**PREFLIGHT, "Origin": "https://evil.example.com"},
    )
    assert "access-control-allow-origin" not in response.headers
    assert response.status_code >= 400


def test_preflight_without_origin_is_not_granted(client: TestClient) -> None:
    response = client.options(
        "/api/status", headers={"Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in response.headers


def test_preflight_does_not_advertise_write_methods(client: TestClient) -> None:
    allowed = client.options("/api/status", headers=PREFLIGHT).headers[
        "access-control-allow-methods"
    ]
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert verb not in allowed.upper()


def test_read_only_behaviour_is_unchanged_by_preflight(client: TestClient) -> None:
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/analysis").status_code == 405, method
    assert client.get("/health").status_code == 200
    assert client.get("/api/status").json()["state"] == "ready"


def test_preflight_still_restricted_after_cors_reconfiguration(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path / "cors")
    app = create_app(bundle_dir=bundle, cors_origins=["http://localhost:4173"])
    client = TestClient(app)
    assert client.options(
        "/api/status", headers={**PREFLIGHT, "Origin": "http://localhost:4173"}
    ).status_code in (200, 204)
    assert "access-control-allow-origin" not in client.options(
        "/api/status", headers=PREFLIGHT
    ).headers


# ---------------------------------------------------------------------------
# 3. previews must really decode
# ---------------------------------------------------------------------------


def _png_with_idat(raw: bytes) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    return (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def without_pillow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate an environment where Pillow is not importable."""
    real = builtins.__import__

    def fake(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("PIL"):
            raise ImportError("Pillow is not installed")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)


@pytest.mark.parametrize("filter_byte", [5, 6, 9, 200])
def test_invalid_scanline_filter_rejected_without_pillow(
    without_pillow: None, filter_byte: int
) -> None:
    payload = _png_with_idat(zlib.compress(bytes([filter_byte, 0])))
    with pytest.raises(pngcheck.PngRejected):
        pngcheck.verify_png_structure(payload)


def test_truncated_zlib_stream_rejected_without_pillow(without_pillow: None) -> None:
    payload = _png_with_idat(zlib.compress(b"\x00\x00")[:-4])
    with pytest.raises(pngcheck.PngRejected):
        pngcheck.verify_png_structure(payload)


def test_wrong_adler32_rejected_without_pillow(without_pillow: None) -> None:
    good = zlib.compress(b"\x00\x00")
    payload = _png_with_idat(good[:-4] + b"\x00\x00\x00\x00")
    with pytest.raises(pngcheck.PngRejected):
        pngcheck.verify_png_structure(payload)


@pytest.mark.parametrize("filter_byte", [0, 1, 2, 3, 4])
def test_valid_filter_types_accepted_without_pillow(
    without_pillow: None, filter_byte: int
) -> None:
    # A 1x1 8-bit greyscale row is one filter byte plus one sample byte.
    payload = _png_with_idat(zlib.compress(bytes([filter_byte, 0])))
    assert pngcheck.verify_png_structure(payload).width == 1


def test_decoding_is_mandatory_not_optional(without_pillow: None) -> None:
    # The decoder is a requirement, so a valid PNG is still refused when no
    # decoder exists rather than being served on structural evidence alone.
    with pytest.raises(pngcheck.PngRejected):
        pngcheck.verify_png(reencode_png(2, 2))


def test_multiline_filters_are_checked_on_every_scanline(
    without_pillow: None,
) -> None:
    # Two rows, the second carrying an invalid filter byte.
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    payload = (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 2, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x07\x00"))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(pngcheck.PngRejected):
        pngcheck.verify_png_structure(payload)


def test_bad_filter_preview_makes_the_bundle_error(
    tmp_path: Path, without_pillow: None
) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "change.png").write_bytes(_png_with_idat(zlib.compress(b"\x05\x00")))
    client = TestClient(create_app(bundle_dir=bundle), raise_server_exceptions=False)
    assert client.get("/api/status").json()["state"] == "error"
    assert client.get("/api/imagery/change").status_code == 503


def test_all_previews_must_share_dimensions(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "change.png").write_bytes(reencode_png(2, 2))
    for key in ("before", "after"):
        (bundle / f"{key}.png").write_bytes(reencode_png(1, 1))
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "error"


def test_previews_sharing_dimensions_are_served(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    for key in ("before", "after", "change"):
        (bundle / f"{key}.png").write_bytes(reencode_png(3, 3))
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "ready"
    for key in ("before", "after", "change"):
        assert client.get(f"/api/imagery/{key}").status_code == 200


def test_image_is_bounded_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pngcheck, "MAX_IMAGE_BYTES", 16)
    bundle = write_bundle(tmp_path)
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "error"


def test_oversize_image_is_not_read_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size gate must consult the file stat, not materialise the file."""
    bundle = write_bundle(tmp_path)
    seen: list[int] = []
    real_read = Path.read_bytes

    def tracking_read(self: Path) -> bytes:
        seen.append(1)
        return real_read(self)

    monkeypatch.setattr(pngcheck, "MAX_IMAGE_BYTES", 8)
    monkeypatch.setattr(Path, "read_bytes", tracking_read)
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "error"
    assert not seen, "oversize image was read into memory before the size gate"