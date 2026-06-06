"""Single source of truth for every styled paragraph + inline run in
the rendered RTF.

Every paragraph-level emitter in `rtf.py` routes through `emit_styled`;
every mid-paragraph emphasis routes through `styled_inline`. A style
change touches ONE registry entry instead of N call sites.

**Registry shape (2026-06-06 refactor):** each style is described by
its typographic properties (`StyleAttrs` — bold, italic, font-size-pt,
heading-level, etc.). The RTF encoding lives in
[`styles_rtf.py`](styles_rtf.py); the legacy `STYLES: dict[str, str]`
is now derived. An editor adding a new style writes
`StyleAttrs(font_size_pt=14, bold=True, heading_level=3)`, not raw
RTF control words.

See `docs/design/generic-stylesheet-abstraction-260606.md` for the
design + migration plan. A future `styles_html.py` would parallel
`styles_rtf.py` for HTML output.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import IO, Literal, Optional

from . import styles_rtf

# ----- Named constants (single source of truth for magic numbers) ----------
#
# RTF font sizes are exposed here in POINTS (the format-agnostic unit).
# `styles_rtf.to_rtf_open` converts to RTF half-points on the wire.
# Spacing values stay in twips (1440 twips = 1 inch) because both
# RTF and Word's UI surface twip-shaped values directly.

BODY_FONT_PT = 11           # Canonical body size
GROUP_HEADING_PT = 16       # Roman + supergroup (V. / A. / C.)
SUBGROUP_HEADING_PT = 14    # PUBLISHED WORK / etc.
SECTION_H1_PT = 11          # C.1, A.1, B.1
SECTION_H2_PT = 11          # C.5.1, C.16.1
SECTION_H3_PT = 11          # C.16.2.3
INLINE_SUBHEADING_PT = 11   # italic C.5 fallback labels

# Stylesheet declaration spacing (sb/sa) per heading level. Carried into
# both the `\s{N}` stylesheet declaration AND the runtime paragraph
# emit so Word's TOC + paragraph rendering agree.
SECTION_H1_SB = 240
SECTION_H1_SA = 120
SECTION_H2_SB = 200
SECTION_H2_SA = 100
SECTION_H3_SB = 160
SECTION_H3_SA = 80
SECTION_H4_SB = 120
SECTION_H4_SA = 60

# Runtime spacing (only for styles that explicitly want sb/sa around
# the styled paragraph — most heading classes get visual separation
# from the trailing body-reset paragraph instead).
INTRO_NOTE_SA = 120
INLINE_SUBHEADING_SB = 120
INLINE_SUBHEADING_SA = 60
CAREER_PHASE_SB = 120
CAREER_PHASE_SA = 240

# Backwards-compat: half-points exposure. Some callsites and tests still
# reference fs-shaped magic numbers; keep the public names so we don't
# churn unrelated call sites in this refactor.
BODY_FONT_SIZE = BODY_FONT_PT * 2          # \fs22
GROUP_HEADING_FS = GROUP_HEADING_PT * 2    # \fs32
SUBGROUP_HEADING_FS = SUBGROUP_HEADING_PT * 2  # \fs28
SECTION_H1_FS = SECTION_H1_PT * 2          # \fs22
SECTION_H2_FS = SECTION_H2_PT * 2          # \fs22
SECTION_H3_FS = SECTION_H3_PT * 2          # \fs22
INLINE_SUBHEADING_FS = INLINE_SUBHEADING_PT * 2  # \fs22
_BODY_FONT_SIZE = BODY_FONT_SIZE


# ----- StyleAttrs — typed, format-agnostic style attributes ---------------


Alignment = Literal["left", "center", "right", "justify"]


@dataclass(frozen=True)
class StyleAttrs:
    """Format-agnostic description of one named style.

    Each field is a typographic property; `styles_rtf.py` translates
    these into RTF. A future HTML translator would map the same fields
    to inline-CSS or `<span class="...">`.

    Tristate `italic`:
      * `None`  — don't emit any italic control (inherit).
      * `True`  — emit `\\i` (italic ON).
      * `False` — emit `\\i0` (explicit italic OFF). Used when the
        parent heading style is italic-by-default and the variant
        forces it off (see `subgroup_heading`).
    """
    font_size_pt: Optional[int] = None
    bold: bool = False
    italic: Optional[bool] = None
    underline: bool = False
    alignment: Alignment = "left"
    indent_twips: int = 0
    space_before_twips: int = 0
    space_after_twips: int = 0
    page_break_before: bool = False
    heading_level: Optional[int] = None
    border_block: str = ""
    is_character_style: bool = False


# ----- Style registry ----------------------------------------------------


# The authoritative registry — typed `StyleAttrs` per name. Paragraph
# vs character distinction lives in `is_character_style`. The legacy
# `STYLES: dict[str, str]` below is now derived from this registry by
# applying the RTF translator.
_STYLES_ATTRS: dict[str, StyleAttrs] = {
    # --- Paragraph-level styles ---
    # Default body — used by the leading "fs reset" paragraph that
    # opens the document and the trailing re-baseline emitted after
    # every styled paragraph.
    "body": StyleAttrs(font_size_pt=BODY_FONT_PT),
    # Roman section heading ("MATERIAL PREPARED BY THE CANDIDATE",
    # "Supporting Documentation for Pending Publications.") — Word
    # "heading 1" (P&T template's top-level style); the template's
    # heading-1 list auto-numbers as I., II., III., ..., so emitters
    # MUST NOT include the Roman numeral in the rendered text (would
    # produce "I. III. MATERIAL..." doubled). Font size + color come
    # from the template's heading-1 theme (orange in the Purdue P&T
    # template) — no explicit font size override so the theme wins.
    "roman_section": StyleAttrs(bold=True, heading_level=1),
    # Letter group heading ("GENERAL INFORMATION", "SELF-EVALUATION",
    # "SUPPORTING INFORMATION") — Word "heading 2" (P&T template's
    # A./B./C. level). The template's heading-2 style is link to a
    # multilevel list that auto-numbers as A./B./C.; emitters DROP the
    # literal letter prefix so Word's list provides it (otherwise
    # doubled, e.g. "B. A. GENERAL…" when Word's list lands on B and
    # we emitted A). Theme drives size + color.
    "group_heading": StyleAttrs(bold=True, heading_level=2),
    # Subgroup heading (PUBLISHED WORK, EXTERNAL VISIBILITY, ...) —
    # P&T template's "Heading 4 + Bold, Not Italic, Underline, Centered"
    # variant: `\s4` (Word heading 4) PLUS direct overrides for the
    # template's variant. Heading 4's default is italic, so `italic=False`
    # explicitly forces italic OFF (matches the "Not Italic" annotation
    # in Word's style panel). Heading 4 is shared with `section_h2` for
    # the stylesheet declaration — both reference the same `\s4` style,
    # which is fine since RTF style IDs are global per document.
    "subgroup_heading": StyleAttrs(
        bold=True, italic=False, underline=True,
        alignment="center",
        font_size_pt=SUBGROUP_HEADING_PT,
        heading_level=4,
    ),
    # Level-1 section heading (C.1, A.1, B.1, ...) — Word "heading 3"
    # so the auto-TOC picks it up at the section level. Bold + body-size
    # font gives visual weight without competing with the heading-1 /
    # heading-2 P&T template theme. The template doesn't auto-number
    # this level, so emitters DO include the "A.1" / "C.1" prefix as
    # literal text.
    "section_h1": StyleAttrs(
        bold=True, font_size_pt=SECTION_H1_PT, heading_level=3,
    ),
    # Level-2 section heading (C.5.1, C.16.1, ...). Word "heading 4".
    "section_h2": StyleAttrs(
        italic=True, font_size_pt=SECTION_H2_PT, heading_level=4,
    ),
    # Level-3 section heading (C.16.2.3, ...). Word "heading 4" still
    # so the TOC doesn't explode; visual differentiation comes from
    # the smaller font + indent.
    "section_h3": StyleAttrs(
        italic=True, font_size_pt=SECTION_H3_PT, heading_level=4,
    ),
    # Level-4 section heading (rare; reserved). Word "heading 4".
    "section_h4": StyleAttrs(
        italic=True, font_size_pt=BODY_FONT_PT, heading_level=4,
    ),
    # Inline subheading — italic + body-size, NO heading style (does not
    # appear in TOC). Used for C.5 subcategory fallback labels.
    "inline_subheading": StyleAttrs(
        italic=True, font_size_pt=INLINE_SUBHEADING_PT,
    ),
    # Career-phase divider — italic body-font text inside top + bottom
    # borders. No heading style; the borders communicate structure.
    "career_phase_divider": StyleAttrs(
        italic=True, font_size_pt=BODY_FONT_PT,
        border_block=r"\brdrt\brdrs\brdrw15\brsp40\brdrb\brdrs\brdrw15",
    ),
    # Introductory note — italic body-font paragraph orienting the
    # reader at the top of a section (grant totals, C.22 cross-refs,
    # C.24 service intro, etc.).
    "intro_note": StyleAttrs(italic=True, font_size_pt=BODY_FONT_PT),
    # N/A placeholder — plain body-font (no emphasis), used when a
    # section has no YAML data but the heading still emits. Preserves
    # the longstanding "N/A" visual — readers scan for the cue and
    # italic would change its weight too much.
    "na_placeholder": StyleAttrs(font_size_pt=BODY_FONT_PT),
    # --- Inline (mid-paragraph) styles for styled_inline() ---
    # Field label inside an entry body (A.1 "Name", "ORCID",
    # "Google Scholar").
    "field_label": StyleAttrs(italic=True, is_character_style=True),
    # Grant total label inside the intro-note ("Total amount of
    # external funding as PI:"). Italic to match the surrounding
    # intro-note style.
    "grant_total_label": StyleAttrs(italic=True, is_character_style=True),
    # Bold header cell content inside an `RtfTable` row. Routed via
    # `styled_inline` from `RtfTable._render_row` so the bold-wrap
    # decision lives in the registry, not inline at every renderer
    # that builds a header row.
    "table_header": StyleAttrs(bold=True, is_character_style=True),
    # Italic venue / title inside a citation body — used by
    # `format_journal_citation`, `render_under_review_section`,
    # `render_invited_talk`, `render_media_appearance`, A.2 thesis
    # title, etc.
    "venue_italic": StyleAttrs(italic=True, is_character_style=True),
    # Underlined tier label / due-date / society — used by C.2/C.4
    # tier marker, A.1 under-review due-date, C.7 leadership-role
    # society field.
    "underline_marker": StyleAttrs(underline=True, is_character_style=True),
    # Bold summary leader in a hanging-indent entry body — used by
    # C.20 entrepreneurial activities + C.21 technology transfer
    # ("Summary: description.").
    "entry_summary": StyleAttrs(bold=True, is_character_style=True),
    # Bold name in a header-style entry (C.22 software product name,
    # C.19 patent row code).
    "entry_name": StyleAttrs(bold=True, is_character_style=True),
}


# Legacy public name — derived from `_STYLES_ATTRS` via the RTF
# translator. Kept so existing tests and the raw-control-code lint
# (which reads `STYLES` to allowlist registry-supplied codes) keep
# working without churn. Editors should add new entries to
# `_STYLES_ATTRS` (typed), not `STYLES` (raw RTF).
STYLES: dict[str, str] = {
    name: styles_rtf.to_rtf_open(attrs) for name, attrs in _STYLES_ATTRS.items()
}


# (space-before, space-after) in twips for each paragraph-level style.
# Absent entry → (0, 0). Captured here so a "tweak the subgroup spacing"
# request is a one-line edit to this dict instead of a hunt through
# rtf.py for \sb/\sa magic numbers.
SPACING: dict[str, tuple[int, int]] = {
    # Heading-class styles rely on the trailing body-reset paragraph
    # for visual separation; no explicit sa/sb. Keep entries here only
    # for styles that DO want explicit spacing.
    "intro_note":           (0, INTRO_NOTE_SA),
    "inline_subheading":    (INLINE_SUBHEADING_SB, INLINE_SUBHEADING_SA),
    "career_phase_divider": (CAREER_PHASE_SB, CAREER_PHASE_SA),
}


# Styles that emit a hard page break (`\page`) BEFORE the paragraph,
# per the Purdue template's "Separate each major section with a page
# break" convention for CAPS BOLD UNDERLINED + Roman headings.
PAGE_BREAK_BEFORE: set[str] = {"subgroup_heading", "roman_section"}


# Styles whose paragraph carries top + bottom borders (career-phase
# divider). Derived from `_STYLES_ATTRS.border_block` — kept as a
# public dict so existing call sites and tests continue to work.
BORDER_BLOCKS: dict[str, str] = {
    name: attrs.border_block
    for name, attrs in _STYLES_ATTRS.items()
    if attrs.border_block
}


def _close_for(open_codes: str) -> str:
    """Derive the close-tag sequence for an open-tag string.

    Back-compat wrapper around the typed `styles_rtf.to_rtf_close` —
    look up the style by its open string and return the registered
    close. Kept so existing tests and any legacy callers continue to
    work; prefer `styles_rtf.to_rtf_close(attrs)` for new code.

    Walks the legacy `STYLES` dict in reverse lookup, since not every
    caller of `_close_for` has a `StyleAttrs` handle. For a literal
    RTF string that doesn't match any registered open, the close is
    derived per the legacy walk-tokens-pair-with-closes algorithm.
    """
    # Fast path: open_codes is a known registered style → use typed close.
    for name, opens in STYLES.items():
        if opens == open_codes:
            return styles_rtf.to_rtf_close(_STYLES_ATTRS[name])
    # Fallback for ad-hoc strings (tests, edge cases). Walk the tokens
    # in lookup order, pair each with its known close, emit closes in
    # reverse open-order.
    import re
    _CLOSE_TAGS = {r"\b": r"\b0", r"\i": r"\i0", r"\ul": r"\ulnone"}
    tokens = re.findall(r"\\[a-z]+", open_codes)
    closes = [_CLOSE_TAGS[t] for t in tokens if t in _CLOSE_TAGS]
    return "".join(reversed(closes))


# ----- Stylesheet declarations (derived from STYLES) ---------------------


# Map each StyleAttrs entry that targets a Word heading level (via
# `heading_level`) to (Word style-name, sb, sa). Word's "Insert Table
# of Contents" scans for paragraphs styled with these named styles —
# so the stylesheet declaration MUST list them exactly. Derived from
# the registry so a registry edit (e.g., bumping `SECTION_H1_PT`)
# automatically updates the stylesheet declaration too.
_HEADING_STYLE_NAMES: list[tuple[str, str, int, int]] = [
    # (style_key, Word-style-name, sb, sa). Maps each registry style key
    # to its Word "heading N" name in the stylesheet block. The P&T
    # template's TOC pulls heading 1 (III. Roman), heading 2 (A./B./C.),
    # heading 3 (A.1/C.1). Subgroup bands (PUBLISHED WORK) and deeper
    # nested headings (C.5.1, C.16.2.3) are intentionally absent — they
    # carry only direct visual formatting, no heading_level.
    ("roman_section",    "heading 1", SECTION_H1_SB, SECTION_H1_SA),
    ("group_heading",    "heading 2", SECTION_H2_SB, SECTION_H2_SA),
    ("section_h1",       "heading 3", SECTION_H3_SB, SECTION_H3_SA),
    ("section_h2",       "heading 4", SECTION_H4_SB, SECTION_H4_SA),
]


def format_stylesheet_block() -> str:
    """Return the RTF `{\\stylesheet …}` block, derived from STYLES.

    Each `\\sN` paragraph style declaration carries the same open-tag
    sequence used at runtime, plus `\\sb` / `\\sa` + `\\keepn`
    (Word-conventional "keep with next paragraph" for headings) +
    `\\sbasedon0\\snext0` (parent on default; next paragraph on
    default — Word convention for headings).

    The Hyperlink character style (`\\cs1`) is hand-written because
    it's not in STYLES (it's a Word character-style, not a paragraph
    style; routes via `_code_link` / `\\field` HYPERLINK, not via
    `emit_styled`).
    """
    parts: list[str] = ["{\\stylesheet"]
    for style_key, word_name, sb, sa in _HEADING_STYLE_NAMES:
        opens = STYLES[style_key]
        # The stylesheet declaration uses the same open-tag sequence
        # as the runtime emit (minus `\sN` which IS the style id).
        # Re-emit `\sN` explicitly so the declaration is
        # self-contained.
        # `\keepn`: Word "keep with next" — heading sticks with
        # following paragraph at page breaks.
        parts.append(
            f"{{{opens}\\sb{sb}\\sa{sa}\\keepn"
            f" \\sbasedon0\\snext0 {word_name};}}"
        )
    # Hyperlink character style (not in STYLES — it's a `\cs` not `\s`).
    parts.append(
        r"{\*\cs1\additive\cf1\ul"
        r" \sbasedon10 Hyperlink;}"
    )
    parts.append("}")
    return "".join(parts)


# ----- Emit primitives ---------------------------------------------------


def emit_styled(
    out: IO[str],
    style: str,
    text: str,
    *,
    indent: int = 0,
    suppress_page_break: bool = False,
) -> None:
    """Emit a fully-styled paragraph applying `_STYLES_ATTRS[style]`.

    The single paragraph-level emit primitive — every styled paragraph
    in the doc routes through here. The function:
      * (Optionally) emits a hard page break before the paragraph
        when `style ∈ PAGE_BREAK_BEFORE`.
      * Resets paragraph formatting (`\\pard\\plain`).
      * Selects Times New Roman explicitly (`\\f0`) so Word doesn't
        fall back to its UI default after `\\plain` resets.
      * Applies the indent + spacing (`\\li{N}\\sb{N}\\sa{N}`).
      * Applies any border block (career-phase divider only today).
      * Applies the style's open RTF control sequence (via
        `styles_rtf.to_rtf_open`).
      * Writes the (already-escaped) text.
      * Applies the matching close sequence (via
        `styles_rtf.to_rtf_close`).
      * Re-baselines the body paragraph that follows.

    `text` is assumed to already be RTF-safe (escaped via `escape_rtf`
    or built from sentinel-bearing RTF fragments). This primitive
    does NOT escape — that's the caller's job.
    """
    attrs = _STYLES_ATTRS[style]
    prefix = styles_rtf.to_rtf_open(attrs)
    close = styles_rtf.to_rtf_close(attrs)
    sb, sa = SPACING.get(style, (0, 0))
    border = attrs.border_block
    if style in PAGE_BREAK_BEFORE and not suppress_page_break:
        out.write("\\pard\\page\\par\n")
    spacing = ""
    if sb:
        spacing += f"\\sb{sb}"
    if sa:
        spacing += f"\\sa{sa}"
    out.write(
        f"\\pard\\plain\\f0\\li{indent}{spacing}{border}{prefix} "
        f"{text}{close}\\par\n"
        f"\\pard\\plain\\f0\\fs{_BODY_FONT_SIZE}\\par\n"
    )


def styled_inline(style: str, text: str) -> str:
    """Return an inline RTF fragment with `_STYLES_ATTRS[style]` applied.

    For mid-paragraph emphasis where the surrounding paragraph context
    is owned by another emitter — e.g., A.1 field labels embedded
    inside `_emit_list_item`'s body string, or grant total labels
    embedded inside an intro-note paragraph.

    `text` is assumed to already be RTF-safe; this primitive does NOT
    escape.
    """
    attrs = _STYLES_ATTRS[style]
    prefix = styles_rtf.to_rtf_open(attrs)
    close = styles_rtf.to_rtf_close(attrs)
    return f"{prefix} {text}{close}"
