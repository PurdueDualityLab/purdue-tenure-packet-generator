"""Tests for image_embed (Q7) — RTF picture embedding for the V.A.1
supporting-documentation use case.

Covers:
  * PNG dim reading from the IHDR chunk
  * JPEG dim reading via SOF marker walk
  * Format auto-detection
  * Aspect-ratio-preserving clamp to max width
  * Missing file is build-fatal
  * Unknown format is build-fatal
  * Emit produces a `\\pict\\<blip>\\picwgoal\\pichgoal hex` block
  * `\\pard\\li{indent}` wrap places the picture at the requested indent
"""
from __future__ import annotations

import io
import struct

import pytest


# ---------------------------------------------------------------------------
# Fixture image bytes — handcrafted minimal PNG + JPEG so the tests are
# self-contained (no on-disk fixtures, no PIL dependency).
# ---------------------------------------------------------------------------


def _minimal_png(width: int, height: int) -> bytes:
    """Build a minimal but valid PNG with IHDR + IDAT + IEND chunks.

    Image data is one all-zero scanline per row (filter byte 0 + N
    zero bytes per row), zlib-compressed. The result parses cleanly
    in a PNG decoder; only IHDR's dimensions matter for our purposes.
    """
    import zlib
    signature = bytes((137, 80, 78, 71, 13, 10, 26, 10))
    # IHDR payload: width(4) + height(4) + bit_depth(1) + color_type(1)
    # + compression(1) + filter(1) + interlace(1)
    ihdr_payload = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)

    def _chunk(name: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(name + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", crc)

    ihdr = _chunk(b"IHDR", ihdr_payload)
    # IDAT: one filter byte + width zero bytes per scanline.
    raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    idat = _chunk(b"IDAT", zlib.compress(raw))
    iend = _chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


def _minimal_jpeg(width: int, height: int) -> bytes:
    """Build a stub JPEG byte string that's enough to parse dimensions
    from. We don't need decode-validity for image_embed.

    Structure: SOI + APP0 (JFIF) + SOF0 frame header + EOI. The SOF0
    segment carries the dims in the layout image_embed walks.
    """
    soi = b"\xff\xd8"
    # APP0 segment: marker + length + "JFIF\0" + version + density + thumb
    app0_payload = (
        b"JFIF\x00"
        + struct.pack(">BB", 1, 1)         # version 1.1
        + struct.pack(">BHHBB", 1, 72, 72, 0, 0)  # density unit + x + y + thumb
    )
    app0 = b"\xff\xe0" + struct.pack(">H", len(app0_payload) + 2) + app0_payload
    # SOF0: marker + length + precision + height + width + components
    sof0_payload = (
        struct.pack(">B", 8)               # precision
        + struct.pack(">HH", height, width)
        + struct.pack(">B", 1)             # one component
        + b"\x01\x11\x00"                  # component id + sampling factors + Q-table
    )
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof0_payload) + 2) + sof0_payload
    eoi = b"\xff\xd9"
    return soi + app0 + sof0 + eoi


# ---------------------------------------------------------------------------
# Dimension parsing
# ---------------------------------------------------------------------------


def test_png_dimensions_extracted_from_ihdr() -> None:
    from pubs_emitter.image_embed import _read_png_dimensions
    data = _minimal_png(320, 240)
    assert _read_png_dimensions(data) == (320, 240)


def test_png_signature_required() -> None:
    from pubs_emitter.image_embed import _read_png_dimensions
    with pytest.raises(ValueError, match="signature"):
        _read_png_dimensions(b"not a png at all" + b"\x00" * 24)


def test_jpeg_dimensions_extracted_from_sof0() -> None:
    from pubs_emitter.image_embed import _read_jpeg_dimensions
    data = _minimal_jpeg(500, 750)
    assert _read_jpeg_dimensions(data) == (500, 750)


def test_jpeg_soi_required() -> None:
    from pubs_emitter.image_embed import _read_jpeg_dimensions
    with pytest.raises(ValueError, match="SOI"):
        _read_jpeg_dimensions(b"not a jpeg at all")


def test_detect_format_png() -> None:
    from pubs_emitter.image_embed import _detect_format
    assert _detect_format(_minimal_png(10, 10)) == "png"


def test_detect_format_jpeg() -> None:
    from pubs_emitter.image_embed import _detect_format
    assert _detect_format(_minimal_jpeg(10, 10)) == "jpeg"


def test_detect_format_rejects_unknown() -> None:
    from pubs_emitter.image_embed import _detect_format
    with pytest.raises(ValueError, match="unsupported image format"):
        _detect_format(b"GIF89a\x10\x00")


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def test_clamp_below_max_width_preserves_natural_size() -> None:
    """A small image (well under 5 inches) renders at its native size
    converted to twips at 96 DPI."""
    from pubs_emitter.image_embed import _clamp_to_max_width
    # 200 x 100 px at 96 DPI = (200*15, 100*15) = (3000, 1500) twips
    # (well under 5" = 7200 twips), so no clamp.
    assert _clamp_to_max_width(200, 100, max_width_inches=5.0) == (3000, 1500)


def test_clamp_above_max_width_scales_with_aspect_ratio_preserved() -> None:
    """A wide image scales down to fit the max width; height scales
    proportionally."""
    from pubs_emitter.image_embed import _clamp_to_max_width
    # 800 x 400 px (2:1 aspect) at 96 DPI = (12000, 6000) twips.
    # Max width 5" = 7200 twips → scale = 0.6 → (7200, 3600).
    w, h = _clamp_to_max_width(800, 400, max_width_inches=5.0)
    assert w == 7200
    # Allow ±1 twip slack from rounding.
    assert abs(h - 3600) <= 1


# ---------------------------------------------------------------------------
# End-to-end emit
# ---------------------------------------------------------------------------


def test_emit_image_writes_pict_block_with_dims_and_hex(tmp_path) -> None:
    """A full emit: write a PNG to tmp, call emit_image, assert the
    output contains the `\\pict\\pngblip\\picwgoal\\pichgoal` shape
    with the expected hex payload."""
    from pubs_emitter.image_embed import emit_image
    png = _minimal_png(100, 50)
    p = tmp_path / "screen.png"
    p.write_bytes(png)
    buf = io.StringIO()
    emit_image(buf, str(p), indent_twips=720, max_width_inches=5.0)
    out = buf.getvalue()
    # Indent wrap.
    assert "\\pard\\li720" in out
    # Picture envelope.
    assert "\\pict" in out
    assert "\\pngblip" in out
    # Dimensions (96 DPI conversion: 100 px → 1500 twips, 50 → 750).
    assert "\\picwgoal1500" in out
    assert "\\pichgoal750" in out
    # Hex-encoded payload survives.
    assert png.hex() in out
    # Trailing paragraph break.
    assert out.rstrip().endswith("\\par")


def test_emit_image_writes_jpeg_blip(tmp_path) -> None:
    from pubs_emitter.image_embed import emit_image
    jpeg = _minimal_jpeg(60, 30)
    p = tmp_path / "screen.jpg"
    p.write_bytes(jpeg)
    buf = io.StringIO()
    emit_image(buf, str(p), indent_twips=0)
    out = buf.getvalue()
    assert "\\jpegblip" in out
    assert jpeg.hex() in out


def test_emit_image_missing_file_is_build_fatal(tmp_path) -> None:
    """Per Q7 design: missing file is sys.exit(1), not a silent skip
    or warning."""
    from pubs_emitter.image_embed import emit_image
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc:
        emit_image(buf, str(tmp_path / "missing.png"))
    assert exc.value.code == 1


def test_emit_image_relative_path_resolved_against_project_root(tmp_path) -> None:
    """Relative paths resolve against `project_root` when provided."""
    from pubs_emitter.image_embed import emit_image
    png = _minimal_png(40, 20)
    subdir = tmp_path / "assets" / "supporting-docs"
    subdir.mkdir(parents=True)
    (subdir / "shot.png").write_bytes(png)
    buf = io.StringIO()
    emit_image(
        buf, "assets/supporting-docs/shot.png",
        project_root=str(tmp_path), indent_twips=480,
    )
    out = buf.getvalue()
    assert "\\pict" in out
    assert "\\pngblip" in out
    assert "\\li480" in out


def test_emit_image_unsupported_format_is_build_fatal(tmp_path) -> None:
    from pubs_emitter.image_embed import emit_image
    p = tmp_path / "bad.gif"
    p.write_bytes(b"GIF89a" + b"\x00" * 20)
    buf = io.StringIO()
    with pytest.raises(ValueError, match="unsupported image format"):
        emit_image(buf, str(p))
