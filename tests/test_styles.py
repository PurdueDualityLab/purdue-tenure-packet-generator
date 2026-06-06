"""Isolated tests for pubs_emitter.styles.

Each test pins one invariant on the registry or emit primitives in
isolation — no `rtf.py` imports, no document-level dependencies. The
goal is the same as `tests/test_evaluations.py`: the module's API
contract is the test surface; the rest of the codebase consumes it.
"""
from __future__ import annotations

import io

import pytest

from pubs_emitter.styles import (
    BORDER_BLOCKS,
    PAGE_BREAK_BEFORE,
    SPACING,
    STYLES,
    _close_for,
    emit_styled,
    styled_inline,
)


# ----- Close-tag derivation ----------------------------------------------


class TestCloseFor:
    """`_close_for` walks the open-tag tokens of a style's open sequence
    and pairs each with its registered close. Font size, paragraph
    style (`\\sN`), alignment (`\\qc`), and font face (`\\f0`) reset
    via the trailing body-paragraph re-baseline, not via a closing
    token — so they don't contribute to the close string."""

    def test_empty_open_produces_empty_close(self) -> None:
        assert _close_for("") == ""

    def test_bold_alone(self) -> None:
        assert _close_for(r"\b") == r"\b0"

    def test_italic_alone(self) -> None:
        assert _close_for(r"\i") == r"\i0"

    def test_underline_alone(self) -> None:
        assert _close_for(r"\ul") == r"\ulnone"

    def test_bold_plus_font_size_closes_bold_only(self) -> None:
        """Font size (`\\fs{N}`) doesn't need a closing token — the
        trailing body-reset paragraph carries the body font."""
        assert _close_for(r"\b\fs28") == r"\b0"

    def test_nested_emphases_close_in_reverse_order(self) -> None:
        """Closes emit in REVERSE order of opens so nested formatting
        unnests correctly: `\\i\\b\\ul` → `\\ulnone\\b0\\i0`."""
        assert _close_for(r"\i\b\ul") == r"\ulnone\b0\i0"

    def test_paragraph_style_does_not_close(self) -> None:
        """`\\sN` paragraph styles (Word "heading N") reset via the
        trailing body-paragraph re-baseline, not by a closing token."""
        assert _close_for(r"\s1\b\fs32") == r"\b0"

    def test_alignment_does_not_close(self) -> None:
        """`\\qc` (center alignment) resets via the paragraph reset."""
        assert _close_for(r"\qc\b\ul\fs28") == r"\ulnone\b0"


# ----- Registry well-formedness ------------------------------------------


class TestRegistryWellFormed:
    """Every key in the satellite registries (SPACING, PAGE_BREAK_BEFORE,
    BORDER_BLOCKS) must refer to a real STYLES entry — a key with no
    style is a silent no-op and a latent typo trap."""

    def test_every_spacing_key_is_a_style(self) -> None:
        unknown = set(SPACING) - set(STYLES)
        assert not unknown, f"SPACING refers to unknown styles: {sorted(unknown)}"

    def test_every_page_break_key_is_a_style(self) -> None:
        unknown = PAGE_BREAK_BEFORE - set(STYLES)
        assert not unknown, (
            f"PAGE_BREAK_BEFORE refers to unknown styles: {sorted(unknown)}"
        )

    def test_every_border_key_is_a_style(self) -> None:
        unknown = set(BORDER_BLOCKS) - set(STYLES)
        assert not unknown, (
            f"BORDER_BLOCKS refers to unknown styles: {sorted(unknown)}"
        )

    def test_every_style_open_sequence_starts_with_backslash(self) -> None:
        """Empty open sequence is allowed (passthrough/no-op styles);
        otherwise every open string must look like RTF control words."""
        for name, opens in STYLES.items():
            if not opens:
                continue
            assert opens.startswith("\\"), (
                f"STYLES[{name!r}] = {opens!r} doesn't look like an RTF control sequence"
            )

    def test_no_duplicate_styles(self) -> None:
        """A style key appears at most once — STYLES is a regular dict
        so duplicates would already collapse, but the test pins the
        intent."""
        keys = list(STYLES.keys())
        assert len(keys) == len(set(keys))


# ----- emit_styled paragraph shape ---------------------------------------


class TestEmitStyledParagraphShape:
    """`emit_styled` produces a paragraph with `\\pard\\plain\\f0` reset,
    indent, spacing, border block, open tags, text, close tags, then
    the trailing body-paragraph re-baseline."""

    def test_body_style_minimal_shape(self) -> None:
        buf = io.StringIO()
        emit_styled(buf, "body", "hello")
        out = buf.getvalue()
        # Opening sequence: paragraph reset + font face + indent + open tags.
        assert out.startswith("\\pard\\plain\\f0\\li0\\fs22 hello\\par\n")
        # Trailing body re-baseline paragraph.
        assert out.endswith("\\pard\\plain\\f0\\fs22\\par\n")

    def test_indent_applied(self) -> None:
        buf = io.StringIO()
        emit_styled(buf, "body", "x", indent=720)
        assert "\\li720" in buf.getvalue()

    def test_intro_note_carries_trailing_spacing(self) -> None:
        """SPACING["intro_note"] = (0, 120) → `\\sa120` appears in the
        open paragraph."""
        buf = io.StringIO()
        emit_styled(buf, "intro_note", "x")
        out = buf.getvalue()
        assert "\\sa120" in out
        # No leading sb (SPACING is (0, 120)).
        assert "\\sb" not in out.split("\\par")[0]

    def test_subgroup_heading_emits_page_break_before(self) -> None:
        """Subgroup headings are in PAGE_BREAK_BEFORE, so the emit
        starts with `\\pard\\page\\par` before the styled paragraph."""
        buf = io.StringIO()
        emit_styled(buf, "subgroup_heading", "PUBLISHED WORK")
        out = buf.getvalue()
        assert out.startswith("\\pard\\page\\par\n")
        # Then the styled paragraph with the subgroup open tags.
        # Heading 4 + bold + underline + centered + force-not-italic.
        # `\s4` brings Word's heading 4 style; `\i0` overrides heading 4's
        # italic default to match the P&T "Not Italic" annotation.
        assert "\\s4\\qc\\b\\ul\\fs28\\i0 PUBLISHED WORK" in out

    def test_career_phase_divider_emits_border_block(self) -> None:
        """BORDER_BLOCKS["career_phase_divider"] is the top/bottom border
        RTF, applied between spacing and open tags."""
        buf = io.StringIO()
        emit_styled(buf, "career_phase_divider", "PhD studies", indent=720)
        out = buf.getvalue()
        assert "\\brdrt\\brdrs\\brdrw15\\brsp40" in out
        assert "\\brdrb\\brdrs\\brdrw15" in out
        # Surrounded by `\i\fs22 … \i0`.
        assert "\\i\\fs22 PhD studies\\i0\\par" in out
        # Spacing (120, 240) applied.
        assert "\\sb120\\sa240" in out

    def test_section_heading_h1_applies_s3_word_style(self) -> None:
        """`section_h1` carries `\\s3` (Word "heading 3") so the auto-
        TOC picks it up at the section level. Bold + body-size font
        (fs22) makes A.1 / C.1 visually stand out without competing
        with the heading-1 / heading-2 P&T theme."""
        buf = io.StringIO()
        emit_styled(buf, "section_h1", "C.2 Journals", indent=0)
        out = buf.getvalue()
        assert "\\s3\\b\\fs22 C.2 Journals\\b0\\par" in out

    def test_section_heading_h2_applies_s4_and_italic(self) -> None:
        """Level-2 sub-section headings are `\\s4` (Word "heading 4")
        + italic, so they're visually quieter than their parent."""
        buf = io.StringIO()
        emit_styled(buf, "section_h2", "C.5.1 Magazine", indent=240)
        out = buf.getvalue()
        assert "\\s4\\i\\fs22 C.5.1 Magazine\\i0\\par" in out
        assert "\\li240" in out

    def test_text_is_not_escaped(self) -> None:
        """`emit_styled` assumes RTF-safe text — escaping is the caller's
        responsibility (the registry doesn't know what the caller's
        text contains)."""
        buf = io.StringIO()
        emit_styled(buf, "body", "Already\\bescaped\\b0 text")
        # Verbatim passthrough — no double-escape.
        assert "Already\\bescaped\\b0 text" in buf.getvalue()

    def test_unknown_style_raises_keyerror(self) -> None:
        buf = io.StringIO()
        with pytest.raises(KeyError):
            emit_styled(buf, "nope_not_a_style", "x")


# ----- styled_inline fragment shape --------------------------------------


class TestStyledInline:
    """`styled_inline` produces a mid-paragraph RTF fragment — no
    paragraph reset, no trailing `\\par`. For embedding inside a
    surrounding paragraph emitted by another helper."""

    def test_field_label_emits_italic_wrap(self) -> None:
        assert styled_inline("field_label", "Name") == r"\i Name\i0"

    def test_styled_inline_has_no_paragraph_reset(self) -> None:
        """No `\\pard` — caller owns the paragraph."""
        frag = styled_inline("field_label", "Name")
        assert "\\pard" not in frag

    def test_styled_inline_has_no_trailing_par(self) -> None:
        frag = styled_inline("field_label", "Name")
        assert not frag.endswith("\\par")
        assert not frag.endswith("\\par\n")

    def test_styled_inline_unknown_style_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            styled_inline("nope_not_a_style", "x")

    def test_styled_inline_with_empty_text(self) -> None:
        """Edge case: empty text still emits the open + close wrap."""
        assert styled_inline("field_label", "") == r"\i \i0"


# ----- Round-trip: open + close in the produced paragraph ----------------


class TestOpenCloseBalance:
    """Every paragraph `emit_styled` produces must balance — every open
    formatting token has its close, in the right order. A mismatch
    would let bold/italic/underline leak into the body re-baseline."""

    @pytest.mark.parametrize("style", sorted(STYLES))
    def test_every_style_emits_balanced_open_close(self, style: str) -> None:
        buf = io.StringIO()
        emit_styled(buf, style, "TXT")
        out = buf.getvalue()
        # The TXT marker sits between the open tags and the close tags.
        # Within `\\b`/`\\i`/`\\ul` formatting, count opens vs closes.
        for open_tag, close_tag in [
            (r"\b ", r"\b0"),
            (r"\i ", r"\i0"),
            (r"\ul ", r"\ulnone"),
            (r"\b\fs", r"\b0"),
            (r"\i\fs", r"\i0"),
        ]:
            opens = out.count(open_tag)
            closes = out.count(close_tag)
            # If the style opened this formatting, it must close it.
            if opens and not closes:
                pytest.fail(
                    f"STYLES[{style!r}] opens {open_tag!r} {opens}x "
                    f"but never closes with {close_tag!r}",
                )


# ----- StyleAttrs typed-registry → RTF translator pin tests --------------


from pubs_emitter import styles_rtf
from pubs_emitter.styles import _STYLES_ATTRS, StyleAttrs


class TestStyleAttrsTranslator:
    """`styles_rtf.to_rtf_open(_STYLES_ATTRS[name])` must produce the
    same RTF as the legacy hand-written `STYLES[name]` string —
    byte-identical for every registered style. This is the structural
    defense behind the 2026-06-06 styles refactor: the translator IS
    the source of truth post-refactor, so a pin on every name guards
    against silent regression on later edits."""

    # Hand-pinned legacy RTF strings — frozen from the pre-refactor
    # state. If a future edit changes a style's RTF, update this dict
    # in the same commit so the test still pins what shipped.
    _LEGACY_OPENS: dict[str, str] = {
        "body":                 r"\fs22",
        "roman_section":        r"\s1\b",
        "group_heading":        r"\s2\b",
        "subgroup_heading":     r"\s4\qc\b\ul\fs28\i0",
        "section_h1":           r"\s3\b\fs22",
        "section_h2":           r"\s4\i\fs22",
        "section_h3":           r"\s4\i\fs22",
        "section_h4":           r"\s4\i\fs22",
        "inline_subheading":    r"\i\fs22",
        "career_phase_divider": r"\i\fs22",
        "intro_note":           r"\i\fs22",
        "na_placeholder":       r"\fs22",
        "field_label":          r"\i",
        "grant_total_label":    r"\i",
        "table_header":         r"\b",
        "venue_italic":         r"\i",
        "underline_marker":     r"\ul",
        "entry_summary":        r"\b",
        "entry_name":           r"\b",
    }

    _LEGACY_CLOSES: dict[str, str] = {
        "body":                 "",
        "roman_section":        r"\b0",
        "group_heading":        r"\b0",
        # `\i0\ulnone\b0` — legacy `_close_for` matched `\i` inside
        # `\i0` at open and paired it with `\i0`, so the close emits
        # the doubled `\i0`. Preserved here for byte-identity.
        "subgroup_heading":     r"\i0\ulnone\b0",
        "section_h1":           r"\b0",
        "section_h2":           r"\i0",
        "section_h3":           r"\i0",
        "section_h4":           r"\i0",
        "inline_subheading":    r"\i0",
        "career_phase_divider": r"\i0",
        "intro_note":           r"\i0",
        "na_placeholder":       "",
        "field_label":          r"\i0",
        "grant_total_label":    r"\i0",
        "table_header":         r"\b0",
        "venue_italic":         r"\i0",
        "underline_marker":     r"\ulnone",
        "entry_summary":        r"\b0",
        "entry_name":           r"\b0",
    }

    def test_every_style_has_a_pin(self) -> None:
        """Pin coverage matches registry: a new style added without a
        pin would slip through silent regression."""
        assert set(self._LEGACY_OPENS.keys()) == set(_STYLES_ATTRS.keys())
        assert set(self._LEGACY_CLOSES.keys()) == set(_STYLES_ATTRS.keys())

    @pytest.mark.parametrize("name", sorted(_STYLES_ATTRS))
    def test_open_byte_identical_to_legacy(self, name: str) -> None:
        attrs = _STYLES_ATTRS[name]
        produced = styles_rtf.to_rtf_open(attrs)
        assert produced == self._LEGACY_OPENS[name], (
            f"to_rtf_open(_STYLES_ATTRS[{name!r}]) = {produced!r} "
            f"but legacy STYLES[{name!r}] = {self._LEGACY_OPENS[name]!r}"
        )

    @pytest.mark.parametrize("name", sorted(_STYLES_ATTRS))
    def test_close_byte_identical_to_legacy(self, name: str) -> None:
        attrs = _STYLES_ATTRS[name]
        produced = styles_rtf.to_rtf_close(attrs)
        assert produced == self._LEGACY_CLOSES[name], (
            f"to_rtf_close(_STYLES_ATTRS[{name!r}]) = {produced!r} "
            f"but legacy close = {self._LEGACY_CLOSES[name]!r}"
        )

    def test_styles_dict_derived_from_attrs(self) -> None:
        """The public `STYLES: dict[str, str]` is derived from
        `_STYLES_ATTRS` via the translator — no hand-written drift."""
        for name, attrs in _STYLES_ATTRS.items():
            assert STYLES[name] == styles_rtf.to_rtf_open(attrs)


class TestStyleAttrsDataclass:
    """`StyleAttrs` is the typed, frozen substrate. Pin its construction
    semantics so the dataclass shape doesn't drift silently."""

    def test_default_construction_is_no_op(self) -> None:
        """An all-defaults StyleAttrs emits an empty RTF run — the
        baseline "do nothing" style."""
        attrs = StyleAttrs()
        assert styles_rtf.to_rtf_open(attrs) == ""
        assert styles_rtf.to_rtf_close(attrs) == ""

    def test_italic_tristate_none_emits_nothing(self) -> None:
        attrs = StyleAttrs(italic=None)
        assert "\\i" not in styles_rtf.to_rtf_open(attrs)
        assert "\\i0" not in styles_rtf.to_rtf_open(attrs)

    def test_italic_true_emits_open_and_close(self) -> None:
        attrs = StyleAttrs(italic=True)
        assert styles_rtf.to_rtf_open(attrs) == r"\i"
        assert styles_rtf.to_rtf_close(attrs) == r"\i0"

    def test_italic_false_emits_i0_open_and_legacy_doubled_i0_close(self) -> None:
        """The `italic=False` tristate emits explicit `\\i0` at OPEN
        time (overrides a heading-style italic default) AND emits a
        second `\\i0` at close for legacy `_close_for` parity (the
        legacy regex matched `\\i` inside `\\i0` and paired it with
        `\\i0`). Word's parser treats italic-off idempotently — the
        doubled emit is harmless and lives in shipped output, so we
        preserve it under the byte-identity invariant."""
        attrs = StyleAttrs(italic=False, bold=True)
        # Open: `\b\i0` — italic-off trailing.
        assert styles_rtf.to_rtf_open(attrs) == r"\b\i0"
        # Close: `\i0\b0` — doubled \i0 for legacy parity.
        assert styles_rtf.to_rtf_close(attrs) == r"\i0\b0"

    def test_frozen_dataclass_rejects_mutation(self) -> None:
        attrs = StyleAttrs(bold=True)
        with pytest.raises((AttributeError, Exception)):
            attrs.bold = False  # type: ignore[misc]
