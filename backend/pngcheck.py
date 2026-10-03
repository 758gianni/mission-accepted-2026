"""PNG integrity verification for declared imagery previews.

Checking the eight-byte signature is not enough: a truncated file, a signature
followed by zeros, or a payload with a stale CRC all satisfy it while no decoder
can read the image. Every declared preview is therefore parsed chunk by chunk
and its pixel stream is actually decompressed before the bundle is accepted.

The checks are implemented against the standard library only. Pillow is used as
an extra cross-check when it happens to be importable, but it is never required,
so this module adds no dependency.

All limits are bounded so that a hostile or simply broken file cannot make the
service allocate without end.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: Refuse to read a declared preview larger than this.
MAX_IMAGE_BYTES = 32 * 1024 * 1024

#: Refuse a single chunk larger than this.
MAX_CHUNK_BYTES = 8 * 1024 * 1024

#: Refuse a decompressed pixel stream larger than this.
MAX_PIXEL_BYTES = 128 * 1024 * 1024

#: Refuse an absurd width or height.
MAX_DIMENSION = 100_000

#: Channels per pixel by PNG colour type.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

#: Bit depths permitted for each colour type.
_BIT_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}

_ADAM7 = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


class PngRejected(ValueError):
    """The bytes are not a complete, decodable PNG image.

    The message is a short sanitised reason; it never embeds file contents.
    """


@dataclass(frozen=True)
class PngInfo:
    """What was verified about a decoded preview."""

    width: int
    height: int
    bit_depth: int
    colour_type: int
    interlaced: bool
    size_bytes: int


def _reject(reason: str) -> None:
    raise PngRejected(reason)


def _read_bounded(decompressor: zlib._Decompress, data: bytes) -> bytes:
    """Inflate ``data``, refusing output beyond ``MAX_PIXEL_BYTES``."""
    out = decompressor.decompress(data, MAX_PIXEL_BYTES + 1)
    if len(out) > MAX_PIXEL_BYTES or decompressor.unconsumed_tail:
        _reject("declared image expands to an unreasonable size")
    return out


def _iter_chunks(data: bytes):
    offset = len(PNG_SIGNATURE)
    total = len(data)
    while offset < total:
        if total - offset < 8:
            _reject("declared image ends inside a chunk header")
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        if length > MAX_CHUNK_BYTES:
            _reject("declared image contains an oversized chunk")
        end = offset + 8 + length
        if total - end < 4:
            _reject("declared image ends inside a chunk")
        payload = data[offset + 8 : end]
        (expected_crc,) = struct.unpack(">I", data[end : end + 4])
        if zlib.crc32(tag + payload) & 0xFFFFFFFF != expected_crc:
            _fail_chunk(tag)
        if not tag.isalpha() or not tag.isascii():
            _fail_chunk(tag)
        yield tag, payload
        offset = end + 4
        if tag == b"IEND":
            if length:
                _reject("declared image has a malformed end chunk")
            if offset != total:
                _reject("declared image has trailing data after the end chunk")
            return
    _reject("declared image is truncated: no end chunk")


def _fail_chunk(tag: bytes) -> None:
    try:
        name = tag.decode("ascii")
    except UnicodeDecodeError:
        name = "unknown"
    if not name.isalpha():
        _reject("declared image has a malformed chunk header")
    _reject(f"declared image has a corrupt {name} chunk")


def _row_bytes(width: int, bits_per_pixel: int) -> int:
    return (width * bits_per_pixel + 7) // 8


def _scanline_layout(
    width: int, height: int, bits_per_pixel: int, interlaced: bool
) -> list[tuple[int, int]]:
    """``(rows, row_bytes)`` for each Adam7 pass, or one pass when not interlaced.

    The rows are contiguous in the inflated stream, so this is what lets every
    scanline's filter byte be checked.
    """
    if not interlaced:
        return [(height, _row_bytes(width, bits_per_pixel))]
    layout: list[tuple[int, int]] = []
    for x_start, y_start, x_step, y_step in _ADAM7:
        pass_width = 0 if width <= x_start else (width - x_start + x_step - 1) // x_step
        pass_height = 0 if height <= y_start else (height - y_start + y_step - 1) // y_step
        if pass_width and pass_height:
            layout.append((pass_height, _row_bytes(pass_width, bits_per_pixel)))
    return layout


def _check_scanline_filters(raw: bytes, layout: list[tuple[int, int]]) -> None:
    """Every scanline must start with a defined PNG filter type.

    Filter 5 and above do not exist. A file can inflate to exactly the right
    length and carry nonsense filter bytes and still look structurally perfect
    to a chunk walker, so this is checked explicitly rather than trusted.
    """
    offset = 0
    for rows, row_bytes in layout:
        stride = 1 + row_bytes
        for _ in range(rows):
            if raw[offset] > 4:
                _reject("declared image has an invalid scanline filter type")
            offset += stride


def verify_png(data: bytes) -> PngInfo:
    """Verify ``data`` structurally and then decode it for real.

    ``decode_check`` is mandatory, so a bundle is only ever served when a real
    decoder has read every pixel.
    """
    info = verify_png_structure(data)
    decode_check(data)
    return info


def verify_png_structure(data: bytes) -> PngInfo:
    """Structural verification of a PNG, without decoding it.

    Verifies the signature, the chunk framing, every chunk CRC, the IHDR
    geometry and bit depth, the presence and consecutiveness of IDAT, that the
    pixel stream inflates cleanly to exactly the declared size, that the deflate
    stream is complete with a verified checksum, and that every scanline
    declares one of the five defined filter types.

    Exposed separately so these guarantees can be tested directly rather than
    being masked by the decoder step.
    """
    if len(data) > MAX_IMAGE_BYTES:
        _reject("declared image is larger than the permitted preview size")
    if not data.startswith(PNG_SIGNATURE):
        _reject("declared image is not a PNG file")
    if len(data) == len(PNG_SIGNATURE):
        _reject("declared image is truncated")

    header: tuple[int, int, int, int, int, int] | None = None
    seen_end = False
    idat_started = False
    idat_finished = False
    idat = bytearray()

    for tag, payload in _iter_chunks(data):
        if header is None and tag != b"IHDR":
            _reject("declared image does not start with a header chunk")
        if tag == b"IHDR":
            if header is not None:
                _reject("declared image has more than one header chunk")
            if len(payload) != 13:
                _reject("declared image has a malformed header chunk")
            header = struct.unpack(">IIBBBBB", payload)
        elif tag == b"PLTE":
            if header is None:
                _reject("declared image has a palette before its header")
            if len(payload) == 0 or len(payload) % 3:
                _reject("declared image has a malformed palette")
        elif tag == b"IDAT":
            if header is None:
                _reject("declared image has pixel data before its header")
            if idat_finished:
                _reject("declared image has non-consecutive pixel data chunks")
            idat_started = True
            idat += payload
        elif tag == b"IEND":
            seen_end = True
        elif tag == b"tRNS":
            if header is None or header[3] not in (0, 2, 3):
                _reject("declared image has transparency data for an opaque colour type")
        elif idat_finished:
            _reject("declared image has data chunks out of order")
        if idat_started and tag != b"IDAT":
            idat_finished = True

    if header is None or not seen_end:
        _reject("declared image is truncated")
    width, height, bit_depth, colour_type, compression, filtering, interlace = header
    if not 0 < width <= MAX_DIMENSION or not 0 < height <= MAX_DIMENSION:
        _reject("declared image dimensions are out of range")
    if colour_type not in _CHANNELS:
        _reject("declared image uses a reserved colour type")
    if bit_depth not in _BIT_DEPTHS[colour_type]:
        _reject("declared image uses an invalid bit depth for its colour type")
    if compression:
        _reject("declared image uses an unknown compression method")
    if filtering:
        _reject("declared image uses an unknown filter method")
    if interlace not in (0, 1):
        _reject("declared image uses an unknown interlace method")
    if colour_type == 3 and b"PLTE" not in data[: len(data) - 12]:
        _reject("declared image is palette-based but has no palette")
    if not idat:
        _reject("declared image contains no pixel data")

    bits_per_pixel = bit_depth * _CHANNELS[colour_type]
    layout = _scanline_layout(width, height, bits_per_pixel, bool(interlace))
    expected = sum(rows * (1 + row_bytes) for rows, row_bytes in layout)
    if expected > MAX_PIXEL_BYTES:
        _reject("declared image expands to an unreasonable size")

    decompressor = zlib.decompressobj()
    try:
        raw = _read_bounded(decompressor, bytes(idat))
        tail = decompressor.flush()
    except zlib.error:
        _reject("declared image pixel data could not be decoded")
    if len(tail) > MAX_PIXEL_BYTES:
        _reject("declared image expands to an unreasonable size")
    raw += tail
    if decompressor.unused_data:
        _reject("declared image has trailing compressed data")
    if not decompressor.eof:
        # The deflate stream ended without its trailing adler32 checksum.
        _reject("declared image pixel data is truncated")
    if len(raw) != expected:
        _reject("declared image pixel data does not match its declared geometry")
    _check_scanline_filters(raw, layout)

    return PngInfo(
        width=width,
        height=height,
        bit_depth=bit_depth,
        colour_type=colour_type,
        interlaced=bool(interlace),
        size_bytes=len(data),
    )


def decode_check(data: bytes) -> None:
    """Decode the image for real with Pillow.

    Pillow is a runtime requirement of this service (see
    requirements-backend.txt). The structural checks above are defence in depth,
    but they cannot reconstruct pixels, so the genuine decoder is required
    rather than optional: a bundle is only served when a real decoder has read
    every pixel.
    """
    try:
        import io

        from PIL import Image, UnidentifiedImageError
    except ImportError:
        _reject("no PNG decoder is available to verify the declared image")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        _reject("declared image could not be decoded")


__all__ = [
    "MAX_CHUNK_BYTES",
    "MAX_DIMENSION",
    "MAX_IMAGE_BYTES",
    "MAX_PIXEL_BYTES",
    "PNG_SIGNATURE",
    "PngInfo",
    "PngRejected",
    "decode_check",
    "verify_png",
    "verify_png_structure",
]
