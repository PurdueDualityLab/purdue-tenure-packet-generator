"""Tests for `pubs_emitter.renderers.patents` — Phase 4 of the IR
refactor (C.19 Patents pilot).

Two test layers:
  1. **IR shape** — assert `render_patents_section_blocks(...)` returns
     the expected `list[Block]` for given input.
  2. **Byte-identity** — assert `RtfWriter().render(blocks)` produces
     the same RTF as the legacy `render_patents_section(out)`.

Layer 1 is the load-bearing win of the IR refactor: the renderer's
correctness is testable independently of RTF dialect, so a future
HTML / LaTeX writer can validate the same IR without re-validating
the renderer.
"""
from __future__ import annotations

import io

import pytest

from pubs_emitter import ir
from pubs_emitter.renderers.patents import render_patents_section_blocks
from pubs_emitter.rtf import render_patents_section
from pubs_emitter.types import Patent
from pubs_emitter.writer_rtf import RtfWriter


def _patent(**overrides: object) -> Patent:
    """Test helper — build a Patent with sensible defaults + overrides.
    `_replace(**overrides)` is inherently `dict[str, Any]`-shaped on
    NamedTuples; the cast keeps mypy quiet without weakening the
    production type."""
    base = Patent(
        year=2024, year_str="2024", title="Sample patent",
        co_inventors="\\b Davis, J.C.\\b0", date="2024-01-15",
        number="US12345678", impact="Cited in industry standard X.",
    )
    return base._replace(**overrides)  # type: ignore[arg-type]


class TestRenderPatentsBlocksIRShape:
    """Layer 1: IR shape — the renderer's output is testable without
    any RTF strings appearing in the test assertions."""

    def test_empty_list_returns_empty_blocks(self) -> None:
        assert render_patents_section_blocks([]) == []

    def test_single_patent_returns_table_plus_blank(self) -> None:
        blocks = render_patents_section_blocks([_patent()])
        assert len(blocks) == 2
        assert isinstance(blocks[0], ir.Table)
        assert isinstance(blocks[1], ir.BlankParagraph)

    def test_table_carries_canonical_column_widths(self) -> None:
        from pubs_emitter.config import PATENT_TABLE_WIDTHS
        t = render_patents_section_blocks([_patent()])[0]
        assert isinstance(t, ir.Table)
        assert t.column_widths_twips == PATENT_TABLE_WIDTHS
        assert len(t.column_widths_twips) == 5

    def test_table_header_is_five_columns(self) -> None:
        t = render_patents_section_blocks([_patent()])[0]
        assert isinstance(t, ir.Table)
        assert len(t.header) == 5
        # Each header cell is `[Text("...")]`.
        assert t.header[0] == [ir.Text("Title")]
        assert t.header[1] == [ir.Text("Co-Inventors")]
        assert t.header[2] == [ir.Text("Issue Date")]
        assert t.header[3] == [ir.Text("Number")]
        assert t.header[4] == [ir.Text("Impact")]

    def test_row_carries_bookmark_on_title_cell(self) -> None:
        """Title cell shape: `BraceScope[Styled(entry_name, [Bookmark(name)]),
        Text(".")] + Text(" ") + Text(title)`. The brace-scope keeps
        the period from being consumed by the `\\b0` close delimiter."""
        t = render_patents_section_blocks([_patent(title="P1")])[0]
        assert isinstance(t, ir.Table)
        title_cell = t.rows[0][0]
        # Position 0: BraceScope wrapping Styled+Text(".")
        assert isinstance(title_cell[0], ir.BraceScope)
        scope = title_cell[0]
        assert isinstance(scope.runs[0], ir.Styled)
        styled = scope.runs[0]
        assert styled.style_name == "entry_name"
        # Inside Styled: Bookmark wrapping the entry code text.
        bookmark = styled.runs[0]
        assert isinstance(bookmark, ir.Bookmark)
        assert bookmark.name == "C_19_1"
        assert bookmark.runs == [ir.Text("C.19.1")]
        # BraceScope position 1: literal period.
        assert scope.runs[1] == ir.Text(".")
        # After the brace-scope: space, then the title.
        assert title_cell[1] == ir.Text(" ")
        assert title_cell[2] == ir.Text("P1")

    def test_coinventors_cell_is_rawrun_passthrough(self) -> None:
        """`format_inventors` returns pre-formatted RTF with `\\b ...\\b0`
        bold-for-me markers; the renderer wraps in `RawRun` so the
        writer doesn't try to escape them."""
        t = render_patents_section_blocks([_patent(
            co_inventors=r"\b Davis, J.C.\b0 and Smith, A.",
        )])[0]
        assert isinstance(t, ir.Table)
        cell = t.rows[0][1]
        assert cell == [ir.RawRun(r"\b Davis, J.C.\b0 and Smith, A.")]

    def test_multiple_patents_increment_entry_codes(self) -> None:
        t = render_patents_section_blocks([
            _patent(title="P1"), _patent(title="P2"), _patent(title="P3"),
        ])[0]
        assert isinstance(t, ir.Table)
        assert len(t.rows) == 3
        for idx, row in enumerate(t.rows, 1):
            title_cell = row[0]
            scope = title_cell[0]
            assert isinstance(scope, ir.BraceScope)
            styled = scope.runs[0]
            assert isinstance(styled, ir.Styled)
            bookmark = styled.runs[0]
            assert isinstance(bookmark, ir.Bookmark)
            assert bookmark.name == f"C_19_{idx}"


class TestRenderPatentsBlocksByteIdentity:
    """Layer 2: byte-identity — feeding the IR through `RtfWriter`
    produces the same RTF as the legacy renderer."""

    def _legacy_rtf(self, patents: list[Patent]) -> str:
        buf = io.StringIO()
        render_patents_section(patents, buf)
        return buf.getvalue()

    def _ir_rtf(self, patents: list[Patent]) -> str:
        # The IR renderer skips the heading (it lands from the markdown
        # outline via the walker). To match the legacy renderer's
        # output, prepend the heading the legacy emits.
        from pubs_emitter.config import SECTION_CODES, SECTION_HEADINGS
        from pubs_emitter.rtf import _emit_section_heading
        if not patents:
            return ""
        buf = io.StringIO()
        _emit_section_heading(
            buf, SECTION_CODES["Patents"], SECTION_HEADINGS["Patents"],
        )
        buf.write(RtfWriter().render(render_patents_section_blocks(patents)))
        return buf.getvalue()

    def test_empty_list_is_identical(self) -> None:
        assert self._ir_rtf([]) == self._legacy_rtf([])

    def test_single_patent_is_identical(self) -> None:
        patents = [_patent()]
        assert self._ir_rtf(patents) == self._legacy_rtf(patents)

    def test_multiple_patents_are_identical(self) -> None:
        patents = [
            _patent(title="Patent A", number="US1111", date="2022-03-01"),
            _patent(title="Patent B", number="US2222", date="2023-07-15"),
            _patent(title="Patent C", number="US3333", date="2024-01-30"),
        ]
        assert self._ir_rtf(patents) == self._legacy_rtf(patents)

    def test_patent_with_special_chars_in_title_is_escaped(self) -> None:
        patents = [_patent(title="Method for {compiling} & linking")]
        assert self._ir_rtf(patents) == self._legacy_rtf(patents)
