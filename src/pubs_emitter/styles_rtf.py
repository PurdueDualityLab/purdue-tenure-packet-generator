"""RTF translator for the format-agnostic `StyleAttrs` substrate.

This is the ONLY module in the codebase that knows about raw RTF
control words. `styles.py` carries typed attributes — bold, italic,
font-size-pt, heading-level — and this module emits the corresponding
`\\b`, `\\i`, `\\fs{N}`, `\\sN` strings.

The translator is byte-faithful to the legacy hand-written RTF strings
in `styles.STYLES`. Every output of `to_rtf_open(attrs)` matches the
legacy string for that style; every output of `to_rtf_close(attrs)`
matches the legacy `_close_for(STYLES[name])`. Pinned by
`tests/test_styles.py::TestStyleAttrsTranslator`.

A future `styles_html.py` (HTML emit) would parallel this module:
same `StyleAttrs` input, different format output.

Design: `docs/design/generic-stylesheet-abstraction-260606.md`.

Ordering rules for `to_rtf_open` (matches the legacy strings):

  1. `\\sN`        — Word heading-style ID  (`heading_level`)
  2. `\\qc`/`\\qr`/`\\qj` — alignment        (`alignment`)
  3. `\\b`         — bold open               (`bold`)
  4. `\\i`         — italic open (italic=True only)
  5. `\\ul`        — underline open          (`underline`)
  6. `\\fs{N}`     — font size (half-points) (`font_size_pt`)
  7. `\\i0`        — italic explicit OFF (italic=False only)

`\\i0` lives at position 7 because Word's heading-4 style is italic
by default; direct formatting needs `\\i0` to override AFTER all other
direct formatting has been applied. The subgroup_heading style is the
canonical site for this pattern.

`to_rtf_close` emits in reverse open-order, matching the legacy
`_close_for` behavior:

  * `\\ulnone`  if `underline`
  * `\\i0`      if `italic is True` (italic=False at open doesn't need close)
  * `\\b0`      if `bold`

`\\sN`, `\\qc`, `\\fs{N}`, and `\\f0` reset via the trailing
body-paragraph re-baseline, so they don't contribute to close.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .styles import StyleAttrs


def to_rtf_open(attrs: StyleAttrs) -> str:
    """Translate a `StyleAttrs` into the leading RTF control-word run.

    The output matches the legacy `STYLES[name]` string byte-for-byte
    (no trailing space — `emit_styled`/`styled_inline` add the
    delimiter when concatenating with text).
    """
    parts: list[str] = []
    if attrs.heading_level is not None:
        parts.append(f"\\s{attrs.heading_level}")
    if attrs.alignment == "center":
        parts.append("\\qc")
    elif attrs.alignment == "right":
        parts.append("\\qr")
    elif attrs.alignment == "justify":
        parts.append("\\qj")
    if attrs.bold:
        parts.append("\\b")
    if attrs.italic is True:
        parts.append("\\i")
    if attrs.underline:
        parts.append("\\ul")
    if attrs.font_size_pt is not None:
        # RTF `\fs` is half-points: 22 = 11pt, 28 = 14pt, 32 = 16pt.
        parts.append(f"\\fs{attrs.font_size_pt * 2}")
    if attrs.italic is False:
        # Explicit italic-OFF — emitted AFTER \fs so it overrides any
        # heading-style italic default. Used by subgroup_heading
        # (Word heading-4's italic-by-default needs to be cancelled).
        parts.append("\\i0")
    return "".join(parts)


def to_rtf_close(attrs: StyleAttrs) -> str:
    """Translate a `StyleAttrs` into the trailing close-tag run.

    Closes emit in reverse open-order. `\\sN` / `\\qc` / `\\fs{N}` reset
    via the body-paragraph re-baseline, so they don't appear here.

    Legacy parity note: when `italic is False` (open emits `\\i0`),
    the close ALSO emits `\\i0`. The legacy `_close_for` regex matched
    `\\i` inside the open string's trailing `\\i0` and paired it with
    `\\i0` — a quirky doubled-`\\i0`. Reproduce here for byte-identity
    of shipped output (Word's parser idempotently treats italic-off as
    italic-off — harmless but lives in shipped RTF).
    """
    # Close order = reverse of open emit position:
    #   italic=False (open pos 7, AFTER \fs) closes FIRST
    #   underline   (open pos 5) closes after \i0-from-italic-false
    #   italic=True (open pos 4, BEFORE \ul) closes after \ulnone
    #   bold        (open pos 3) closes LAST
    parts: list[str] = []
    if attrs.italic is False:
        parts.append("\\i0")
    if attrs.underline:
        parts.append("\\ulnone")
    if attrs.italic is True:
        parts.append("\\i0")
    if attrs.bold:
        parts.append("\\b0")
    return "".join(parts)
