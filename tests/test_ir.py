"""Tests for the IR vocabulary (`src/pubs_emitter/ir.py`).

Pins:
  * Each Block / Run subclass is a `@dataclass(frozen=True)` (immutable
    + hashable + structural equality).
  * The `_runs(...)` helper wraps strings as Text, passes Runs through.
  * Block / Run instances are equal iff their fields are equal.
  * Frozen dataclasses reject mutation.

Layer 1 of the test strategy in
`docs/design/ir-based-emit-disentangling-260606.md` §"Test strategy".
"""
from __future__ import annotations

import pytest

from pubs_emitter import ir


class TestRunConstruction:
    def test_text_carries_literal(self) -> None:
        r = ir.Text("hello")
        assert r.text == "hello"

    def test_raw_run_carries_pre_formatted_rtf(self) -> None:
        r = ir.RawRun("\\b Davis, J.C.\\b0")
        assert r.rtf == r"\b Davis, J.C.\b0"

    def test_bold_wraps_inner_runs(self) -> None:
        inner: list[ir.Run] = [ir.Text("important")]
        b = ir.Bold(runs=inner)
        assert b.runs == inner

    def test_italic_wraps_inner_runs(self) -> None:
        i = ir.Italic(runs=[ir.Text("venue")])
        assert i.runs == [ir.Text("venue")]

    def test_styled_carries_style_name_and_runs(self) -> None:
        s = ir.Styled(style_name="venue_italic", runs=[ir.Text("ICSE")])
        assert s.style_name == "venue_italic"
        assert s.runs == [ir.Text("ICSE")]

    def test_ref_link_carries_code_and_display(self) -> None:
        rl = ir.RefLink(code="C.4.7", display="C.4.7")
        assert rl.code == "C.4.7"
        assert rl.display == "C.4.7"

    def test_hyperlink_carries_url_and_display(self) -> None:
        h = ir.Hyperlink(url="https://doi.org/10.x", display="DOI: 10.x")
        assert h.url == "https://doi.org/10.x"

    def test_superscript_carries_marker(self) -> None:
        sup = ir.Superscript(text="*")
        assert sup.text == "*"

    def test_bookmark_carries_name_and_runs(self) -> None:
        bm = ir.Bookmark(name="C_19_3", runs=[ir.Text("C.19.3")])
        assert bm.name == "C_19_3"
        assert bm.runs == [ir.Text("C.19.3")]


class TestBlockConstruction:
    def test_heading_minimal(self) -> None:
        h = ir.Heading(level=3, code="C.19", title="Patents.")
        assert h.level == 3
        assert h.code == "C.19"
        assert h.title == "Patents."
        # Defaults.
        assert h.bookmark is None
        assert h.suppress_page_break is False
        assert h.restart_list_numbering is False

    def test_heading_with_explicit_bookmark(self) -> None:
        h = ir.Heading(level=3, code="A.1", title="X", bookmark="V_A_1")
        assert h.bookmark == "V_A_1"

    def test_subgroup_heading_carries_title(self) -> None:
        s = ir.SubgroupHeading(title="PUBLISHED WORK")
        assert s.title == "PUBLISHED WORK"

    def test_paragraph_minimal(self) -> None:
        p = ir.Paragraph(runs=[ir.Text("hello")])
        assert p.runs == [ir.Text("hello")]
        assert p.indent_twips == 0
        assert p.style is None

    def test_paragraph_with_indent_and_style(self) -> None:
        p = ir.Paragraph(
            runs=[ir.Text("note")],
            indent_twips=720,
            style="intro_note",
        )
        assert p.indent_twips == 720
        assert p.style == "intro_note"

    def test_list_item_minimal(self) -> None:
        li = ir.ListItem(
            code="C.4.7",
            body=[ir.Text("title")],
            indent_twips=720,
        )
        assert li.code == "C.4.7"
        assert li.bookmark_prefix == ""

    def test_list_item_with_v_prefix(self) -> None:
        li = ir.ListItem(
            code="A.1.1",
            body=[],
            indent_twips=720,
            bookmark_prefix="V.",
        )
        assert li.bookmark_prefix == "V."

    def test_list_item_with_body_two_paragraphs(self) -> None:
        li = ir.ListItemWithBody(
            code="C.1.1",
            header=[ir.Text("title")],
            body_paragraph=[ir.Text("description")],
            indent_twips=720,
        )
        assert li.header == [ir.Text("title")]
        assert li.body_paragraph == [ir.Text("description")]

    def test_table_construction(self) -> None:
        t = ir.Table(
            column_widths_twips=[2400, 2000],
            header=[[ir.Text("A")], [ir.Text("B")]],
            rows=[[[ir.Text("a")], [ir.Text("b")]]],
        )
        assert t.column_widths_twips == [2400, 2000]
        assert len(t.rows) == 1

    def test_image_construction(self) -> None:
        img = ir.Image(path="assets/x.png", indent_twips=720)
        assert img.path == "assets/x.png"
        assert img.max_width_inches == 5.0

    def test_blank_paragraph_no_fields(self) -> None:
        bp = ir.BlankParagraph()
        assert isinstance(bp, ir.Block)

    def test_raw_rtf_block_carries_rtf(self) -> None:
        r = ir.RawRtfBlock(rtf="\\par hello\\par\n")
        assert r.rtf == "\\par hello\\par\n"


class TestFrozenSemantics:
    """Frozen dataclasses reject mutation post-construction. The IR is
    designed for immutability — runtime can hash + cache subtrees
    safely."""

    def test_text_rejects_mutation(self) -> None:
        t = ir.Text("hello")
        with pytest.raises((AttributeError, Exception)):
            t.text = "world"  # type: ignore[misc]

    def test_heading_rejects_mutation(self) -> None:
        h = ir.Heading(level=3, code="C.19", title="X")
        with pytest.raises((AttributeError, Exception)):
            h.level = 4  # type: ignore[misc]

    def test_runs_equal_iff_fields_equal(self) -> None:
        assert ir.Text("x") == ir.Text("x")
        assert ir.Text("x") != ir.Text("y")
        assert ir.Bold(runs=[ir.Text("a")]) == ir.Bold(runs=[ir.Text("a")])
        # Different subclasses with same field values are NOT equal.
        assert ir.Bold(runs=[ir.Text("x")]) != ir.Italic(runs=[ir.Text("x")])


class TestRunsHelper:
    """`ir._runs(...)` is the convenience that lets tests + renderers
    avoid `Text(...)` boilerplate for the common case."""

    def test_strings_become_text(self) -> None:
        out = ir._runs("hello", "world")
        assert out == [ir.Text("hello"), ir.Text("world")]

    def test_runs_pass_through(self) -> None:
        b = ir.Bold(runs=[ir.Text("x")])
        out = ir._runs("hi", b, "bye")
        assert out == [ir.Text("hi"), b, ir.Text("bye")]

    def test_invalid_input_raises(self) -> None:
        with pytest.raises(TypeError):
            ir._runs(123)
