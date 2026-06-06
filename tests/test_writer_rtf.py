"""Tests for `RtfWriter` (`src/pubs_emitter/writer_rtf.py`).

Phase 2 of the IR refactor ships the writer with `RawRtfBlock` +
`RawRun` + `Text` support only — other block / run types raise
`NotImplementedError` until their migration arms land in Phase 4+.

These tests pin the Phase-2 surface so the migration waves have a
solid foundation to extend.
"""
from __future__ import annotations

import pytest

from pubs_emitter import ir
from pubs_emitter.writer_rtf import RtfWriter


class TestRtfWriterRawRtfPassThrough:
    """`RawRtfBlock` is the migration escape hatch — the writer copies
    its `.rtf` field verbatim to the output."""

    def test_empty_block_list_renders_empty_string(self) -> None:
        assert RtfWriter().render([]) == ""

    def test_single_raw_rtf_block_passes_through(self) -> None:
        out = RtfWriter().render([ir.RawRtfBlock(rtf=r"\par hello\par")])
        assert out == r"\par hello\par"

    def test_multiple_raw_rtf_blocks_concatenate(self) -> None:
        out = RtfWriter().render([
            ir.RawRtfBlock(rtf="A"),
            ir.RawRtfBlock(rtf="B"),
            ir.RawRtfBlock(rtf="C"),
        ])
        assert out == "ABC"

    def test_raw_rtf_block_preserves_backslashes_and_braces(self) -> None:
        """The escape hatch is intentionally string-faithful — no
        escaping. The renderer that produced the RTF is the one that
        had to escape; the writer just writes."""
        payload = r"\pard\plain\f0\fs22 hi {\b\fs28 bold}\b0\par"
        assert RtfWriter().render([ir.RawRtfBlock(rtf=payload)]) == payload


class TestRtfWriterUnsupportedBlocks:
    """Unmigrated Block types raise NotImplementedError loudly so the
    walker bridge wraps them in `RawRtfBlock` instead of silently
    falling through. As Phase 5+ migrations land, individual types
    come online here.

    Phase 2 (LANDED): RawRtfBlock.
    Phase 4 (LANDED): Table, BlankParagraph.
    Phase 5 Wave A (LANDED): ListItem, Heading, SubgroupHeading,
        Paragraph, Image.
    Phase 5 (PENDING): ListItemWithBody.
    """

    @pytest.mark.parametrize("block", [
        ir.ListItemWithBody(
            code="C.1.1", header=[], body_paragraph=[], indent_twips=720,
        ),
    ])
    def test_unsupported_block_raises(self, block: ir.Block) -> None:
        with pytest.raises(NotImplementedError):
            RtfWriter().render([block])


class TestRtfWriterRuns:
    """Run-level dispatch is reachable today only via direct unit-test
    calls (block dispatch only hits RawRtfBlock). Pinning the run
    shapes early so Phase 4+ writer arms have a stable foundation."""

    def _render_run(self, run: ir.Run) -> str:
        import io
        buf = io.StringIO()
        RtfWriter()._render_run(buf, run)
        return buf.getvalue()

    def test_text_run_escapes_rtf_specials(self) -> None:
        # `\` → `\\`, `{` → `\{`, `}` → `\}` per builders.escape_rtf.
        assert self._render_run(ir.Text("a \\ b")) == r"a \\ b"
        assert self._render_run(ir.Text("{x}")) == r"\{x\}"

    def test_text_run_with_plain_ascii_passes_unchanged(self) -> None:
        assert self._render_run(ir.Text("hello world")) == "hello world"

    def test_raw_run_passes_rtf_through_unchanged(self) -> None:
        assert self._render_run(ir.RawRun(rtf=r"\b Davis, J.C.\b0")) == \
            r"\b Davis, J.C.\b0"

    @pytest.mark.parametrize("run", [
        ir.Bold(runs=[ir.Text("x")]),
        ir.Italic(runs=[ir.Text("x")]),
        ir.RefLink(code="C.4.7", display="C.4.7"),
        ir.Hyperlink(url="https://x", display="x"),
        ir.Superscript(text="*"),
    ])
    def test_unsupported_run_raises(self, run: ir.Run) -> None:
        import io
        with pytest.raises(NotImplementedError):
            RtfWriter()._render_run(io.StringIO(), run)
