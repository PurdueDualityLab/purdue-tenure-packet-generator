"""Unit tests for pubs_emitter.latex."""
from __future__ import annotations

import pytest

from pubs_emitter.latex import decode_latex, rtf_escape_unicode


class TestDecodeLatex:
    def test_passthrough_ascii(self) -> None:
        assert decode_latex("hello world") == "hello world"

    def test_c_cedilla(self) -> None:
        # {\c{C}}akar  →  Çakar
        assert decode_latex("{\\c{C}}akar") == "Çakar"

    def test_acute_e(self) -> None:
        assert decode_latex("{\\'e}") == "é"

    def test_bare_ampersand_preserved(self) -> None:
        # `&` is a LaTeX cell delimiter; the sentinel-wrap should preserve it.
        assert "&" in decode_latex("S&P")
        assert "&" in decode_latex("IEEE Design&Test")

    def test_braced_passthrough(self) -> None:
        # {NLP} should render as NLP.
        assert decode_latex("{NLP}") == "NLP"

    def test_escaped_ampersand(self) -> None:
        # `\&` is the proper LaTeX escape; pylatexenc handles it.
        assert "&" in decode_latex(r"S\&P")


class TestRtfEscapeUnicode:
    def test_ascii_passthrough(self) -> None:
        assert rtf_escape_unicode("hello") == "hello"

    def test_c_cedilla_signed(self) -> None:
        # Ç = U+00C7 = 199 (< 0x7FFF so positive signed16).
        assert rtf_escape_unicode("Ç") == r"\u199?"

    def test_supplementary_plane_emits_surrogate_pair(self) -> None:
        # U+1F600 (grinning face). Should emit two \u<num>? tokens (surrogates).
        out = rtf_escape_unicode("\U0001F600")
        # Both pieces are negative signed16 (≥ 0xD800).
        assert out.count(r"\u") == 2
        assert out.endswith("?")

    @pytest.mark.parametrize(
        "ch, expected",
        [("é", r"\u233?"), ("ö", r"\u246?"), ("ä", r"\u228?")],
    )
    def test_common_latin(self, ch: str, expected: str) -> None:
        assert rtf_escape_unicode(ch) == expected

    def test_high_bmp_codepoint_negative_signed16(self) -> None:
        # U+E000 (Private Use Area, > 0x7FFF). Signed16 wraps to negative.
        out = rtf_escape_unicode("")
        assert out.startswith(r"\u-")  # 0xE000 - 0x10000 = -8192
