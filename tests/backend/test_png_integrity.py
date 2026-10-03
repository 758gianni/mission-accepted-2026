"""PNG integrity of declared imagery previews.

The service must not serve a file that merely starts with the PNG signature.
Every declared preview is fully decoded and checked for structural integrity
within bounded sizes, so a truncated or padded "PNG" from the producer is
reported as a broken bundle instead of a healthy one.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend import pngcheck
from conftest import PNG_1X1_GRAY, analysis_document, reencode_png, write_bundle

DATA_ENDPOINTS = (
    "/api/analysis",
    "/api/regions",
    "/api/regions/synthetic-region-1",
    "/api/imagery/before",
    "/api/imagery/change",
)


def client_with_change_png(tmp_path: Path, payload: bytes) -> TestClient:
    bundle = write_bundle(tmp_path)
    (bundle / "change.png").write_bytes(payload)
    return TestClient(create_app(bundle_dir=bundle))


def assert_broken(client: TestClient) -> None:
    body = client.get("/api/status").json()
    assert body["state"] == "error", body["message"]
    for path in DATA_ENDPOINTS:
        assert client.get(path).status_code == 503, path
    # Liveness is independent of the bundle and must stay green.
    assert client.get("/health").status_code == 200


def assert_served(client: TestClient) -> None:
    assert client.get("/api/status").json()["state"] == "ready"
    response = client.get("/api/imagery/change")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def truncate(data: bytes, length: int) -> bytes:
    return data[:length]


def corrupt_chunk_crc(data: bytes, tag: bytes) -> bytes:
    """Flip one byte inside the named chunk's payload, leaving its CRC stale."""
    offset = data.index(tag)
    length = struct.unpack(">I", data[offset - 4 : offset])[0]
    payload_start = offset + 4
    mutated = bytearray(data)
    mutated[payload_start + length - 1] ^= 0xFF
    return bytes(mutated)


def drop_last_chunk(data: bytes) -> bytes:
    return data[: -12]


def append_trailing(data: bytes, extra: bytes = b"\x00\x01\x02") -> bytes:
    return data + extra


# ---------------------------------------------------------------------------
# the reproduced finding
# ---------------------------------------------------------------------------


def test_truncated_producer_png_is_rejected(tmp_path: Path) -> None:
    # 43 bytes: long enough to carry a signature, a fake IHDR and a fake IDAT.
    payload = truncate(PNG_1X1_GRAY, 43)
    assert payload.startswith(pngcheck.PNG_SIGNATURE)
    assert_broken(client_with_change_png(tmp_path, payload))


def test_signature_followed_by_zeros_is_rejected(tmp_path: Path) -> None:
    assert_broken(
        client_with_change_png(tmp_path, pngcheck.PNG_SIGNATURE + b"\x00" * 64)
    )


def test_signature_only_is_rejected(tmp_path: Path) -> None:
    assert_broken(client_with_change_png(tmp_path, pngcheck.PNG_SIGNATURE))


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    assert_broken(client_with_change_png(tmp_path, b""))


def test_signature_with_bogus_chunk_lengths_is_rejected(tmp_path: Path) -> None:
    payload = pngcheck.PNG_SIGNATURE + struct.pack(">I", 0xFFFFFFF0) + b"IHDR"
    assert_broken(client_with_change_png(tmp_path, payload))


# ---------------------------------------------------------------------------
# structural integrity
# ---------------------------------------------------------------------------


def test_corrupt_ihdr_crc_is_rejected(tmp_path: Path) -> None:
    assert_broken(client_with_change_png(tmp_path, corrupt_chunk_crc(PNG_1X1_GRAY, b"IHDR")))


def test_corrupt_idat_payload_is_rejected(tmp_path: Path) -> None:
    assert_broken(client_with_change_png(tmp_path, corrupt_chunk_crc(PNG_1X1_GRAY, b"IDAT")))


def test_deflated_stream_that_does_not_decode_is_rejected(tmp_path: Path) -> None:
    ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 0, 0, 0, 0)
    payload = (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", b"\x78\x9c" + b"\xff" * 16)
        + chunk(b"IEND", b"")
    )
    assert_broken(client_with_change_png(tmp_path, payload))


def test_missing_iend_is_rejected(tmp_path: Path) -> None:
    assert_broken(client_with_change_png(tmp_path, drop_last_chunk(PNG_1X1_GRAY)))


def test_trailing_bytes_after_iend_are_rejected(tmp_path: Path) -> None:
    assert_broken(client_with_change_png(tmp_path, append_trailing(PNG_1X1_GRAY)))


def test_declared_height_larger_than_pixel_data_is_rejected(tmp_path: Path) -> None:
    ihdr = struct.pack(">IIBBBBB", 1, 4096, 8, 0, 0, 0, 0)
    payload = (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + chunk(b"IEND", b"")
    )
    assert_broken(client_with_change_png(tmp_path, payload))


def test_zero_dimension_is_rejected(tmp_path: Path) -> None:
    ihdr = struct.pack(">IIBBBBB", 0, 1, 8, 0, 0, 0, 0)
    payload = (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00"))
        + chunk(b"IEND", b"")
    )
    assert_broken(client_with_change_png(tmp_path, payload))


def test_plain_text_is_rejected(tmp_path: Path) -> None:
    assert_broken(client_with_change_png(tmp_path, b"not a png at all"))


def test_idat_split_across_chunks_is_accepted(tmp_path: Path) -> None:
    raw = zlib.compress(b"\x00\x00")
    split = len(raw) // 2
    payload = (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", raw[:split])
        + chunk(b"IDAT", raw[split:])
        + chunk(b"IEND", b"")
    )
    assert_served(client_with_change_png(tmp_path, payload))


def test_unknown_ancillary_chunk_is_accepted(tmp_path: Path) -> None:
    payload = (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        + chunk(b"tEXt", b"Comment\x00synthetic")
        + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + chunk(b"IEND", b"")
    )
    assert_served(client_with_change_png(tmp_path, payload))


def test_palette_png_without_plte_is_rejected(tmp_path: Path) -> None:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0)
    payload = (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + chunk(b"IEND", b"")
    )
    assert_broken(client_with_change_png(tmp_path, payload))


def test_invalid_bit_depth_for_colour_type_is_rejected(tmp_path: Path) -> None:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 4, 2, 0, 0, 0)  # truecolour at 4 bpp
    payload = (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )
    assert_broken(client_with_change_png(tmp_path, payload))


def test_reserved_colour_type_is_rejected(tmp_path: Path) -> None:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 5, 0, 0, 0)
    payload = (
        pngcheck.PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + chunk(b"IEND", b"")
    )
    assert_broken(client_with_change_png(tmp_path, payload))


# ---------------------------------------------------------------------------
# bounded sizes
# ---------------------------------------------------------------------------


def test_oversize_file_is_refused_before_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pngcheck, "MAX_IMAGE_BYTES", 32)
    assert_broken(client_with_change_png(tmp_path, PNG_1X1_GRAY))


def test_excessive_declared_pixel_data_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pngcheck, "MAX_PIXEL_BYTES", 8)
    assert_broken(client_with_change_png(tmp_path, reencode_png(64, 64)))


def test_oversize_ihdr_dimension_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pngcheck, "MAX_DIMENSION", 4)
    assert_broken(client_with_change_png(tmp_path, reencode_png(8, 8)))


def test_generator_rejects_overrun_decompression() -> None:
    class Exploding:
        def decompress(self, data: object, max_length: int = 0) -> bytes:
            return b"\x00" * (max_length + 1)

        @property
        def unconsumed_tail(self) -> bytes:
            return b"tail"

        def flush(self) -> bytes:
            return b""

    with pytest.raises(pngcheck.PngRejected):
        pngcheck._read_bounded(Exploding(), pngcheck.PNG_SIGNATURE)


# ---------------------------------------------------------------------------
# valid images keep working
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(1, 1), (2, 2), (7, 5), (16, 16)])
def test_valid_greyscale_pngs_are_served(tmp_path: Path, size: tuple[int, int]) -> None:
    assert_served(client_with_change_png(tmp_path, reencode_png(*size)))


def test_rgba_png_is_served(tmp_path: Path) -> None:
    payload = _pillow_png("RGBA", (4, 3))
    if payload is None:
        pytest.skip("Pillow unavailable")
    assert_served(client_with_change_png(tmp_path, payload))


def test_palette_png_is_served(tmp_path: Path) -> None:
    payload = _pillow_png("P", (5, 5))
    if payload is None:
        pytest.skip("Pillow unavailable")
    assert_served(client_with_change_png(tmp_path, payload))


def test_sixteen_bit_png_is_served(tmp_path: Path) -> None:
    payload = _pillow_png("I;16", (4, 4))
    if payload is None:
        pytest.skip("Pillow unavailable")
    assert_served(client_with_change_png(tmp_path, payload))


def test_interlaced_png_is_served(tmp_path: Path) -> None:
    payload = _pillow_png("L", (9, 7), interlace=True)
    if payload is None:
        pytest.skip("Pillow unavailable")
    assert_served(client_with_change_png(tmp_path, payload))


def _pillow_png(mode: str, size: tuple[int, int], interlace: bool = False) -> bytes | None:
    """Build a real PNG with Pillow when it is available, else return None.

    Pillow is optional for the service and optional for the tests; the service
    must never require it. These cases only widen the set of legitimately valid
    images that the stdlib validator has to accept.
    """
    try:
        import io

        from PIL import Image
    except ImportError:
        return None
    if mode == "P":
        image = Image.new("P", size)
    elif mode == "I;16":
        image = Image.new("I;16", size)
    else:
        image = Image.new(mode, size)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", interlace=1 if interlace else 0)
    payload = buffer.getvalue()
    Image.open(io.BytesIO(payload)).load()
    return payload


# ---------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------


def test_bundle_recovers_when_the_image_is_repaired(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "change.png").write_bytes(PNG_1X1_GRAY[:43])
    client = TestClient(create_app(bundle_dir=bundle))
    assert_broken(client)
    (bundle / "change.png").write_bytes(reencode_png(4, 4))
    assert client.get("/api/status").json()["state"] == "ready"
    assert client.get("/api/imagery/change").status_code == 200


def test_bundle_recovers_when_analysis_is_repaired(tmp_path: Path) -> None:
    import json

    bundle = write_bundle(tmp_path)
    (bundle / "change.png").write_bytes(PNG_1X1_GRAY[:43])
    client = TestClient(create_app(bundle_dir=bundle))
    assert_broken(client)
    (bundle / "change.png").write_bytes(reencode_png(2, 2))
    (bundle / "analysis.json").write_text(json.dumps(analysis_document()))
    assert client.get("/api/status").json()["state"] == "ready"


def test_healthy_siblings_are_not_served_from_a_broken_bundle(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "change.png").write_bytes(PNG_1X1_GRAY[:43])
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/imagery/before").status_code == 503
    assert client.get("/api/imagery/after").status_code == 503


def test_corrupt_swap_after_validation_is_refused(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/imagery/change").status_code == 200
    (bundle / "change.png").write_bytes(PNG_1X1_GRAY[:43])
    response = client.get("/api/imagery/change")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")


def test_error_message_names_the_image_not_the_bytes(tmp_path: Path) -> None:
    message = client_with_change_png(
        tmp_path, PNG_1X1_GRAY[:43]
    ).get("/api/status").json()["message"]
    assert "change" in message
    assert "IHDR" not in message or "png" in message.lower()
    assert "\x00" not in message
