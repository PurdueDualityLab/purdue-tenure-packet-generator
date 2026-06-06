"""RTF picture embedding — load a PNG or JPEG, hex-encode the bytes,
emit a `{\\pict\\<blip>\\picwgoal\\pichgoal hex}` block at the requested
indent. Supports the Q7 "supporting documentation" use case for V.A.1
under-review papers (and any future site that needs an inline screenshot
embedded at a section's body indent).

Two image formats supported:
  * **PNG** — `\\pngblip`. Reads width/height from the IHDR chunk.
  * **JPEG** — `\\jpegblip`. Walks the SOI/marker-segment chain to find
    the SOF0/SOF2 frame header with width/height.

Dimensions are encoded as `\\picwgoal` / `\\pichgoal` in twips (1/20
point). The default 96 DPI conversion is correct for screenshots saved
out of every common screenshot tool on macOS / Windows / Linux. A
`max_width_inches` cap clamps oversized images with aspect ratio
preserved so a 4K screenshot doesn't blow past the page width.

Missing file is build-fatal — same fail-loud semantics as the existing
`@-ref` / `#macro` unresolved pipeline. Silent fallback would mask
authoring errors (typo'd path, file moved, never committed).

Design: docs/design/markdown-master-outline-refactor.md §Q7.
"""
from __future__ import annotations

import logging
import os
import struct
import sys
from typing import IO, Optional


log = logging.getLogger(__name__)


# 1 inch = 72 points = 1440 twips. Default 96 DPI matches screenshot
# defaults across macOS / Windows / Linux. If a future use case needs
# a different DPI (e.g., scanned document at 300 DPI), expose a
# per-call override on `emit_image`.
_PX_TO_TWIPS_AT_96_DPI = 1440 / 96  # = 15

# Default page-width clamp. Stays inside the 6.5" usable width on US
# Letter at standard margins. Bigger images shrink (aspect ratio
# preserved); smaller images render at their natural size.
_DEFAULT_MAX_WIDTH_INCHES = 5.0


# ---------------------------------------------------------------------------
# Format detection + dimension reading
# ---------------------------------------------------------------------------

_PNG_SIGNATURE = bytes((137, 80, 78, 71, 13, 10, 26, 10))


def _read_png_dimensions(data: bytes) -> tuple[int, int]:
    """Extract (width, height) in pixels from a PNG byte string.

    The PNG IHDR chunk is the first chunk after the 8-byte signature,
    and IHDR carries width + height as the first 8 bytes of its data
    payload (both big-endian uint32). So bytes 16-23 of the file are
    always the dimensions for a valid PNG.
    """
    if len(data) < 24:
        raise ValueError("PNG file too short to contain IHDR")
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("PNG signature missing")
    # Bytes 8-11 are the IHDR chunk length, 12-15 are "IHDR", 16-19 are
    # width, 20-23 are height (all big-endian).
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise ValueError(f"PNG has non-positive dimensions: {width}x{height}")
    return width, height


# JPEG SOI marker (start of image).
_JPEG_SOI = b"\xff\xd8"
# SOFn markers that carry the frame dimensions. SOF0 = baseline DCT;
# SOF2 = progressive DCT — the two we see in practice for screenshots.
_JPEG_SOF_MARKERS: frozenset[bytes] = frozenset({
    b"\xff\xc0",  # SOF0 — baseline
    b"\xff\xc1",  # SOF1 — extended sequential
    b"\xff\xc2",  # SOF2 — progressive
    b"\xff\xc3",  # SOF3 — lossless
    b"\xff\xc5",  # SOF5
    b"\xff\xc6",  # SOF6
    b"\xff\xc7",  # SOF7
    b"\xff\xc9",  # SOF9 — extended sequential, arithmetic
    b"\xff\xca",  # SOF10 — progressive, arithmetic
    b"\xff\xcb",  # SOF11 — lossless, arithmetic
    b"\xff\xcd",  # SOF13
    b"\xff\xce",  # SOF14
    b"\xff\xcf",  # SOF15
})


def _read_jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Extract (width, height) in pixels from a JPEG byte string.

    Walks the marker-segment chain looking for the SOFn frame header.
    Frame header layout per JPEG spec:
        [marker:2][length:2][precision:1][height:2][width:2][...]
    Heights and widths are big-endian uint16.
    """
    if not data.startswith(_JPEG_SOI):
        raise ValueError("JPEG SOI marker missing")
    pos = 2
    n = len(data)
    while pos < n - 1:
        # Each marker starts with 0xFF; runs of 0xFF padding are skipped.
        if data[pos] != 0xFF:
            raise ValueError(f"JPEG marker expected at offset {pos}")
        # Walk past 0xFF padding.
        while pos < n and data[pos] == 0xFF:
            pos += 1
        if pos >= n:
            break
        marker = bytes((0xFF, data[pos]))
        pos += 1
        # Standalone markers (no segment body): SOI (already past),
        # EOI, TEMporary marker, RSTn. None reach this loop in
        # practice, but guard against malformed input.
        if marker in (b"\xff\xd8", b"\xff\xd9", b"\xff\x01"):
            continue
        # All other markers carry a 2-byte big-endian length that
        # includes the length field itself.
        if pos + 2 > n:
            raise ValueError("JPEG segment length truncated")
        seg_len = struct.unpack(">H", data[pos:pos + 2])[0]
        if marker in _JPEG_SOF_MARKERS:
            # Frame header — pull dimensions from inside the segment.
            # Layout after length: 1 byte precision, 2 bytes height,
            # 2 bytes width.
            if pos + 7 > n:
                raise ValueError("JPEG SOF segment truncated")
            height, width = struct.unpack(">HH", data[pos + 3:pos + 7])
            if width <= 0 or height <= 0:
                raise ValueError(
                    f"JPEG has non-positive dimensions: {width}x{height}"
                )
            return width, height
        pos += seg_len
    raise ValueError("JPEG SOF segment not found")


def _detect_format(data: bytes) -> str:
    """Return 'png' or 'jpeg' for the given image byte string.

    Raises ValueError for unsupported formats — currently the
    embedder only supports PNG + JPEG since those cover the
    screenshot-of-email and screenshot-of-confirmation-page use cases.
    """
    if data.startswith(_PNG_SIGNATURE):
        return "png"
    if data.startswith(_JPEG_SOI):
        return "jpeg"
    raise ValueError(
        "unsupported image format (only PNG + JPEG supported); "
        "first 8 bytes: " + data[:8].hex()
    )


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def _clamp_to_max_width(
    width_px: int, height_px: int, max_width_inches: float,
) -> tuple[int, int]:
    """Clamp the image's display dimensions to a max width while
    preserving aspect ratio. Returns (width_twips, height_twips)."""
    width_twips = int(round(width_px * _PX_TO_TWIPS_AT_96_DPI))
    height_twips = int(round(height_px * _PX_TO_TWIPS_AT_96_DPI))
    max_width_twips = int(round(max_width_inches * 1440))
    if width_twips <= max_width_twips:
        return width_twips, height_twips
    scale = max_width_twips / width_twips
    return max_width_twips, int(round(height_twips * scale))


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------


def _hex_encode(data: bytes) -> str:
    """RTF picture data is hex-encoded ASCII (two chars per byte). RTF
    parsers tolerate arbitrary whitespace inside the hex run, but we
    emit unbroken hex for compactness — the rendered packet stays
    small relative to the source PNG (~2x source size since each byte
    becomes two hex chars)."""
    return data.hex()


def emit_image(
    out: IO[str],
    image_path: str,
    *,
    indent_twips: int = 0,
    max_width_inches: float = _DEFAULT_MAX_WIDTH_INCHES,
    project_root: Optional[str] = None,
) -> None:
    """Emit a `{\\pict ...}` block for the image at `image_path`.

    Path resolution:
      * Absolute paths used as-is.
      * Relative paths resolved against `project_root` when provided,
        else the current working directory.

    Image format inferred from the file's magic bytes (PNG signature
    or JPEG SOI marker); unsupported formats are build-fatal.

    Layout:
      * Wrapped in `\\pard\\li<indent_twips>` so the picture flows at
        the same column where surrounding body text lands.
      * `\\picwgoal` + `\\pichgoal` in twips, clamped to
        `max_width_inches` with aspect ratio preserved.
      * Trailing `\\par` separates the picture from the next paragraph.

    Missing file is build-fatal (sys.exit(1)) — silent fallback would
    mask an authoring error.
    """
    resolved_path = image_path
    if not os.path.isabs(resolved_path):
        base = project_root or os.getcwd()
        resolved_path = os.path.join(base, image_path)
    if not os.path.exists(resolved_path):
        log.error(
            "supporting_image not found: %s (resolved from %r); "
            "fix the path or remove the field.",
            resolved_path, image_path,
        )
        sys.exit(1)
    with open(resolved_path, "rb") as f:
        data = f.read()
    fmt = _detect_format(data)
    if fmt == "png":
        width_px, height_px = _read_png_dimensions(data)
        blip = r"\pngblip"
    else:  # jpeg
        width_px, height_px = _read_jpeg_dimensions(data)
        blip = r"\jpegblip"
    width_twips, height_twips = _clamp_to_max_width(
        width_px, height_px, max_width_inches,
    )
    hex_data = _hex_encode(data)
    out.write(
        f"\\pard\\li{indent_twips} "
        f"{{\\pict{blip}\\picwgoal{width_twips}\\pichgoal{height_twips}\n"
        f"{hex_data}\n}}\\par\n"
    )
