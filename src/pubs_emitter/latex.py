"""LaTeX → Unicode decoding for bib field text."""
from __future__ import annotations

from typing import cast

from pylatexenc.latex2text import LatexNodes2Text


_DECODER = LatexNodes2Text()


def decode_latex(s: str) -> str:
    """Convert LaTeX escapes to Unicode.

    `{\\c{C}}` → `Ç`, `{\\'e}` → `é`, `{NLP}` → `NLP`, etc.
    """
    return cast(str, _DECODER.latex_to_text(s))
