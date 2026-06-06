"""Phase-1 tests for the markdown-master walker.

Covers the 12-test checklist in
`docs/design/markdown-master-outline-refactor.md` §"Tests (Phase 1)":

  parser:
    1. test_parse_headings_by_depth
    2. test_parse_extracts_code_and_title
    3. test_parse_directive_isolated_line
    4. test_parse_directive_inline_is_literal
    5. test_parse_paragraphs_split_on_blank_lines
    6. test_parse_strips_c_style_comments

  walker:
    7. test_walker_emits_bookmark_per_heading
    8. test_walker_dispatches_directive
    9. test_walker_missing_directive_exits
    10. test_walker_resolves_at_refs_in_paragraphs
    11. test_walker_substitutes_macros_in_paragraphs
    12. test_walker_collects_declared_codes_into_ref_index
"""
from __future__ import annotations

import io
import logging
from typing import IO

import pytest

from pubs_emitter import directives as directives_mod
from pubs_emitter.section_walker import (
    DirectiveNode,
    HeadingNode,
    ParagraphNode,
    RenderContext,
    parse_section_prose,
    walk_section_prose,
)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_headings_by_depth() -> None:
    """`#`/`##`/`###`/`####` map to depths 1/2/3/4 respectively."""
    text = (
        "# III. MATERIAL FOR EVALUATION\n"
        "\n"
        "## A. GENERAL INFORMATION\n"
        "\n"
        "### A.1 Name and identifiers\n"
        "\n"
        "#### A.1.1 Subentry\n"
    )
    nodes = parse_section_prose(text)
    headings = [n for n in nodes if isinstance(n, HeadingNode)]
    assert [h.depth for h in headings] == [1, 2, 3, 4]


def test_parse_extracts_code_and_title() -> None:
    """`## A.1 Name and identifiers` → code='A.1', title='Name and identifiers'.
    Roman + letter + dotted codes all extract; trailing-period on code is
    stripped."""
    text = (
        "# III. MATERIAL\n"
        "\n"
        "## A. GENERAL INFO\n"
        "\n"
        "### A.1 Name and identifiers\n"
        "\n"
        "#### C.16.2.3 Products\n"
    )
    nodes = parse_section_prose(text)
    headings = [n for n in nodes if isinstance(n, HeadingNode)]
    assert (headings[0].code, headings[0].title) == ("III", "MATERIAL")
    assert (headings[1].code, headings[1].title) == ("A", "GENERAL INFO")
    assert (headings[2].code, headings[2].title) == ("A.1", "Name and identifiers")
    assert (headings[3].code, headings[3].title) == ("C.16.2.3", "Products")


def test_parse_directive_isolated_line() -> None:
    """`!FOO!` on its own line → DirectiveNode."""
    text = (
        "### C.19 Patents\n"
        "\n"
        "!PATENTS_TABLE!\n"
    )
    nodes = parse_section_prose(text)
    directives = [n for n in nodes if isinstance(n, DirectiveNode)]
    assert len(directives) == 1
    assert directives[0].name == "PATENTS_TABLE"


def test_parse_directive_inline_is_literal() -> None:
    """`text !FOO! more text` is literal prose, not a directive."""
    text = (
        "### A.1 Identifiers\n"
        "\n"
        "Hello !INLINE! more text.\n"
    )
    nodes = parse_section_prose(text)
    directives = [n for n in nodes if isinstance(n, DirectiveNode)]
    paragraphs = [n for n in nodes if isinstance(n, ParagraphNode)]
    assert directives == []
    assert len(paragraphs) == 1
    assert "!INLINE!" in paragraphs[0].text


def test_parse_paragraphs_split_on_blank_lines() -> None:
    """Blank-line-separated text becomes separate ParagraphNodes; internal
    soft-wraps within a paragraph collapse to spaces."""
    text = (
        "### A.1 Test\n"
        "\n"
        "First paragraph here.\n"
        "Second line of first paragraph.\n"
        "\n"
        "Second paragraph here.\n"
        "\n"
        "Third paragraph.\n"
    )
    nodes = parse_section_prose(text)
    paragraphs = [n for n in nodes if isinstance(n, ParagraphNode)]
    assert len(paragraphs) == 3
    assert paragraphs[0].text == "First paragraph here. Second line of first paragraph."
    assert paragraphs[1].text == "Second paragraph here."
    assert paragraphs[2].text == "Third paragraph."


def test_parse_strips_c_style_comments() -> None:
    """`/* ... */` comment blocks are removed before parsing — the
    rendered packet must not include editor-only authoring guidance."""
    text = (
        "### A.1 Test\n"
        "\n"
        "/* TEMPLATE PROMPT: edit me, do not ship. */\n"
        "\n"
        "Real prose that should appear.\n"
        "\n"
        "/* Another reminder block\n"
        "   spanning multiple lines. */\n"
        "\n"
        "More real prose.\n"
    )
    nodes = parse_section_prose(text)
    paragraphs = [n for n in nodes if isinstance(n, ParagraphNode)]
    bodies = [p.text for p in paragraphs]
    assert any("Real prose" in b for b in bodies)
    assert any("More real prose" in b for b in bodies)
    # Neither comment body string survives anywhere.
    joined = "\n".join(bodies)
    assert "TEMPLATE PROMPT" not in joined
    assert "Another reminder" not in joined


# ---------------------------------------------------------------------------
# Walker tests
# ---------------------------------------------------------------------------


def _walk(text: str, ctx: RenderContext | None = None) -> tuple[str, RenderContext]:
    """Helper — run the walker against `text`, return (rtf_output, ctx)."""
    ctx = ctx or RenderContext()
    buf: IO[str] = io.StringIO()
    walk_section_prose(text, ctx, buf)
    return buf.getvalue(), ctx


def test_walker_emits_bookmark_per_heading() -> None:
    """Every heading the walker emits is wrapped in a `\\bkmkstart`
    bookmark whose name matches the code with `.` → `_`."""
    text = "### C.16.2 Subentry\n"
    rtf, _ = _walk(text)
    # `_emit_section_heading` for level-2 (dot-count 2 → level 2) emits
    # `_ref_anchor(code)` which uses `\\bkmkstart C_16_2`. Check both
    # the bookmark name and that it appears in the output.
    assert r"\bkmkstart C_16_2" in rtf


def test_walker_dispatches_directive() -> None:
    """A registered `!NAME!` directive's renderer is invoked."""
    seen: list[str] = []

    def _probe(ctx: RenderContext, out: IO[str]) -> None:
        seen.append("fired")
        out.write("\\pard probe\\par\n")

    directives_mod.DIRECTIVES["PROBE_PHASE1"] = _probe
    try:
        rtf, _ = _walk("!PROBE_PHASE1!\n")
        assert seen == ["fired"]
        assert "probe" in rtf
    finally:
        directives_mod.DIRECTIVES.pop("PROBE_PHASE1", None)


def test_walker_missing_directive_exits() -> None:
    """An unknown `!NAME!` is build-fatal (Q5 of the design doc)."""
    with pytest.raises(SystemExit) as exc:
        _walk("!NOT_REGISTERED_ANYWHERE!\n")
    assert exc.value.code == 1


def test_walker_resolves_at_refs_in_paragraphs() -> None:
    """`@bibkey` / `@id` / `@C.X.Y` in paragraph prose is resolved
    against `ctx.ref_index`. Resolved refs emit sentinel-wrapped codes
    that the final pass converts to RTF hyperlinks; verify the sentinel
    appears.

    Sentinel chars per builders.REF_LINK_OPEN / REF_LINK_CLOSE: 0x01 /
    0x02.
    """
    from pubs_emitter.builders import REF_LINK_CLOSE, REF_LINK_OPEN
    ctx = RenderContext(ref_index={"davis2018impact": "C.2.5"})
    text = (
        "### A.1 Test\n"
        "\n"
        "Reference to @davis2018impact appears here.\n"
    )
    rtf, _ = _walk(text, ctx)
    # The resolved code appears wrapped in the link sentinels.
    assert f"{REF_LINK_OPEN}C.2.5{REF_LINK_CLOSE}" in rtf


def test_walker_substitutes_macros_in_paragraphs() -> None:
    """`#MACRO_NAME` in paragraph prose substitutes against `ctx.macros`."""
    ctx = RenderContext(macros={"NUM_PAPERS": "42"})
    text = (
        "### B.1 Self-evaluation\n"
        "\n"
        "I have published #NUM_PAPERS papers.\n"
    )
    rtf, _ = _walk(text, ctx)
    assert "42" in rtf
    # `#NUM_PAPERS` did not survive unsubstituted.
    assert "#NUM_PAPERS" not in rtf


def test_walker_collects_declared_codes_into_ref_index() -> None:
    """Every heading code the walker sees is registered as a
    self-resolving entry in `ctx.ref_index` so `@C.X.Y` cross-refs
    target markdown-declared sections even when no YAML entry exists.

    Pre-existing entries (e.g., a YAML-derived `C.16.2 → C.16.2.5`)
    are NOT overwritten — `setdefault` semantics.
    """
    from pubs_emitter.builders import REF_LINK_CLOSE, REF_LINK_OPEN
    ctx = RenderContext()
    text = (
        "### C.16.2 Outline section\n"
        "\n"
        "See @C.16.2.1 for the full discussion.\n"
        "\n"
        "#### C.16.2.1 Subsection\n"
        "\n"
        "Body.\n"
    )
    rtf, ctx_out = _walk(text, ctx)
    # Both declared codes are in ref_index after the walk.
    assert "C.16.2" in ctx_out.ref_index
    assert "C.16.2.1" in ctx_out.ref_index
    # Self-resolving — declared code maps to itself.
    assert ctx_out.ref_index["C.16.2"] == "C.16.2"
    assert ctx_out.ref_index["C.16.2.1"] == "C.16.2.1"
    # The `@C.16.2.1` in the paragraph resolved via the raw-code path
    # to a sentinel-wrapped code regardless of ref_index population —
    # but the augmentation is what makes `@C.16.2.1` viable as an
    # `@id`-form ref when a YAML entry binds it. Smoke-check that the
    # paragraph emitted ANY sentinel pair.
    assert REF_LINK_OPEN in rtf and REF_LINK_CLOSE in rtf


# ---------------------------------------------------------------------------
# Extra Phase-1 smoke: end-to-end heading + paragraph + directive run
# ---------------------------------------------------------------------------


def test_walker_smoke_full_sequence() -> None:
    """One mixed source runs end-to-end without raising — sanity check
    that the three node kinds interleave cleanly."""
    text = (
        "# III. MATERIAL\n"
        "\n"
        "## A. GENERAL INFORMATION\n"
        "\n"
        "### A.1 Identifiers\n"
        "\n"
        "Some prose at A.1.\n"
        "\n"
        "!HELLO!\n"
        "\n"
        "#### A.1.1 Subentry\n"
        "\n"
        "More prose.\n"
    )
    rtf, _ = _walk(text)
    # Depth-3 and depth-4 emits go through `_emit_section_heading`
    # which wraps the code in `\\bkmkstart …`. Depth-1 (roman) and
    # depth-2 (group) emitters today render title-only — bookmark
    # emission for those depths is a Phase-4 cleanup item (the
    # walker may choose to emit its own bookmark wrap for III / A
    # before delegating to the title emitter).
    for bn in ("A_1", "A_1_1"):
        assert f"\\bkmkstart {bn}" in rtf
    # Depth-1 and depth-2 titles still rendered.
    assert "MATERIAL" in rtf
    assert "GENERAL INFORMATION" in rtf
    # Hello directive fired.
    assert "!HELLO! directive fired" in rtf
