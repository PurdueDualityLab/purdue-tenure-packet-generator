"""Phase 3 of the IR refactor — `walk_to_blocks` bridge tests.

Pins: `walk_to_blocks(text, ctx)` + `RtfWriter().render(blocks)`
produces byte-identical output to the legacy `walk_section_prose(text,
ctx, out)`. This is the zero-behavior-change foundation that lets
Phase 4+ migrations replace `RawRtfBlock` segments with real IR one
section at a time.

The bridge captures each node's legacy RTF emit into a `RawRtfBlock`;
the writer copies the payload verbatim, so byte-identity is
mechanically guaranteed. These tests pin the invariant against
realistic walker inputs (headings, paragraphs, directives) to catch
regressions if the bridge logic ever drifts.
"""
from __future__ import annotations

import io

from pubs_emitter.section_walker import (
    RenderContext,
    walk_section_prose,
    walk_to_blocks,
)
from pubs_emitter.writer_rtf import RtfWriter


def _legacy_rtf(text: str, ctx: RenderContext) -> str:
    buf = io.StringIO()
    walk_section_prose(text, ctx, buf)
    return buf.getvalue()


def _ir_rtf(text: str, ctx: RenderContext) -> str:
    blocks = walk_to_blocks(text, ctx)
    return RtfWriter().render(blocks)


class TestWalkerBridgeByteIdentity:
    """The IR path matches the legacy path for every walker construct."""

    def test_empty_text_produces_empty(self) -> None:
        ctx = RenderContext()
        assert _legacy_rtf("", ctx) == ""
        assert _ir_rtf("", ctx) == ""

    def test_single_heading(self) -> None:
        ctx = RenderContext()
        text = "### C.19 Issued U.S. and International Patents.\n"
        assert _ir_rtf(text, ctx) == _legacy_rtf(text, ctx)

    def test_heading_plus_paragraph(self) -> None:
        ctx = RenderContext()
        text = (
            "### C.19 Issued U.S. and International Patents.\n"
            "\n"
            "This is an intro paragraph for the patents section.\n"
        )
        assert _ir_rtf(text, ctx) == _legacy_rtf(text, ctx)

    def test_multiple_headings_and_paragraphs(self) -> None:
        ctx = RenderContext()
        text = (
            "### C.16.1 Overview\n"
            "\n"
            "Intro for the overview section.\n"
            "\n"
            "#### C.16.2.2 Other Undergraduate Research Pathways.\n"
            "\n"
            "Pathways intro paragraph.\n"
        )
        assert _ir_rtf(text, ctx) == _legacy_rtf(text, ctx)

    def test_paragraph_with_markdown_emphasis(self) -> None:
        ctx = RenderContext()
        text = (
            "### B.1 Summary of achievements.\n"
            "\n"
            "Plain text with **bold** and *italic* emphasis.\n"
        )
        assert _ir_rtf(text, ctx) == _legacy_rtf(text, ctx)

    def test_bridge_yields_list_of_blocks(self) -> None:
        """Sanity-check the return type — a real list of IR blocks,
        not a flat string. Each non-empty node contributes one
        RawRtfBlock (Phase 3 contract)."""
        from pubs_emitter.ir import RawRtfBlock
        ctx = RenderContext()
        text = (
            "### C.19 Issued U.S. and International Patents.\n"
            "\n"
            "Intro paragraph.\n"
        )
        blocks = walk_to_blocks(text, ctx)
        assert all(isinstance(b, RawRtfBlock) for b in blocks)
        # Heading + paragraph → 2 blocks.
        assert len(blocks) == 2
