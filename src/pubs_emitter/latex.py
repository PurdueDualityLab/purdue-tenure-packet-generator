"""LaTeX → Unicode decoding + RTF Unicode escaping for bib field text."""
from __future__ import annotations

import re
from typing import cast

from pylatexenc.latex2text import LatexNodes2Text


_DECODER = LatexNodes2Text()

# Sentinel used to round-trip a bare `&` through pylatexenc, which otherwise
# converts it to whitespace (LaTeX treats `&` as a table-cell delimiter).
# Real bib text doesn't contain NUL bytes, so the substitution is reversible.
_AMP_SENTINEL = "\x00AMP\x00"
_BARE_AMP_RE = re.compile(r"(?<!\\)&")


def decode_latex(s: str) -> str:
    """Convert LaTeX escapes to Unicode.

    `{\\c{C}}` → `Ç`, `{\\'e}` → `é`, `{NLP}` → `NLP`, etc.

    Bare `&` (common in bib venue names like "S&P", "IEEE Design&Test") is
    preserved by sentinel-wrapping around the decoder. `\\&` (proper LaTeX
    escape) is handled by pylatexenc itself.
    """
    safe = _BARE_AMP_RE.sub(_AMP_SENTINEL, s)
    decoded = cast(str, _DECODER.latex_to_text(safe))
    return decoded.replace(_AMP_SENTINEL, "&")


def rtf_escape_unicode(s: str) -> str:
    """Encode non-ASCII chars as RTF `\\u<signed16>?` escape sequences.

    Our RTF header declares `\\ansicpg1252`, so Word reads the file as cp1252.
    Raw UTF-8 bytes (e.g. `\\xc3\\x87` for Ç) get mangled to `Ã‡`. The canonical
    RTF Unicode escape is `\\u<num>?` where:
      - `num` is the codepoint as a SIGNED 16-bit int (values > 0x7FFF
        wrap to negative);
      - `?` is the 1-char fallback rendered by non-Unicode-aware readers.

    Supplementary-plane codepoints (> 0xFFFF) encode as UTF-16 surrogate pairs.
    """
    parts: list[str] = []
    for ch in s:
        cp = ord(ch)
        if cp < 128:
            parts.append(ch)
        elif cp <= 0xFFFF:
            signed = cp if cp <= 0x7FFF else cp - 0x10000
            parts.append(f"\\u{signed}?")
        else:
            base = cp - 0x10000
            high = 0xD800 + (base >> 10) - 0x10000  # always > 0x7FFF, so always negative
            low = 0xDC00 + (base & 0x3FF) - 0x10000
            parts.append(f"\\u{high}?\\u{low}?")
    return "".join(parts)
