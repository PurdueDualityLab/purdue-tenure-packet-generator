"""Single source of truth for every styled paragraph + inline run in
the rendered RTF.

Every paragraph-level emitter in `rtf.py` routes through `emit_styled`;
every mid-paragraph emphasis routes through `styled_inline`. A style
change touches ONE registry entry instead of N call sites.

See `docs/design/style-registry-refactor.md` for the design + migration
plan.
"""
from __future__ import annotations

import re
from typing import IO

# ----- Named constants (single source of truth for magic numbers) ----------
#
# RTF font sizes are in half-points (22 = 11pt). Spacing values are in
# twips (1440 twips = 1 inch). Extract every magic value here so a
# global "shrink everything by 1pt" or "tighten heading spacing" is a
# one-line edit in this section.

BODY_FONT_SIZE = 22         # 11pt — canonical body size
GROUP_HEADING_FS = 32       # 16pt — Roman + supergroup (V. / A. / C.)
SUBGROUP_HEADING_FS = 28    # 14pt — PUBLISHED WORK / etc.
SECTION_H1_FS = 22          # 11pt — C.1, A.1, B.1 (Word template themes the color)
SECTION_H2_FS = 22          # 11pt — C.5.1, C.16.1 (Purdue P&T uses body size at all heading levels)
SECTION_H3_FS = 22          # 11pt — C.16.2.3
INLINE_SUBHEADING_FS = 22   # 11pt — italic C.5 fallback labels

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

# Backwards-compat private alias (used by `\fs{_BODY_FONT_SIZE}`
# f-string in legacy callsites + tests). Prefer `BODY_FONT_SIZE` in
# new code.
_BODY_FONT_SIZE = BODY_FONT_SIZE


# ----- Style registry ----------------------------------------------------


# Open-tag RTF sequence for each style. Style names are documentation:
# pick the most semantic name so callsites read as policy intent.
#
# Font selection (`\f0` Times New Roman) is applied by `emit_styled`
# itself, not duplicated here, so a callsite can't accidentally drop
# back to Word's UI default font after `\plain` resets.
STYLES: dict[str, str] = {
    # --- Paragraph-level styles ---
    # Default body — used by the leading "fs reset" paragraph that
    # opens the document and the trailing re-baseline emitted after
    # every styled paragraph.
    "body":                 rf"\fs{BODY_FONT_SIZE}",
    # Roman section heading ("MATERIAL PREPARED BY THE CANDIDATE",
    # "Supporting Documentation for Pending Publications.") — Word
    # "heading 1" (P&T template's top-level style); the template's
    # heading-1 list auto-numbers as I., II., III., ..., so emitters
    # MUST NOT include the Roman numeral in the rendered text (would
    # produce "I. III. MATERIAL..." doubled). Font size + color come
    # from the template's heading-1 theme (orange in the Purdue P&T
    # template) — no explicit \fs override so the theme wins.
    "roman_section":        r"\s1\b",
    # Letter group heading ("GENERAL INFORMATION", "SELF-EVALUATION",
    # "SUPPORTING INFORMATION") — Word "heading 2" (P&T template's
    # A./B./C. level). The template's heading-2 style is link to a
    # multilevel list that auto-numbers as A./B./C.; emitters DROP the
    # literal letter prefix so Word's list provides it (otherwise
    # doubled, e.g. "B. A. GENERAL…" when Word's list lands on B and
    # we emitted A). Theme drives size + color.
    "group_heading":        r"\s2\b",
    # Subgroup heading (PUBLISHED WORK, EXTERNAL VISIBILITY, ...) —
    # P&T template's "Heading 4 + Bold, Not Italic, Underline, Centered"
    # variant: `\s4` (Word heading 4) PLUS direct overrides for the
    # template's variant. Heading 4's default is italic, so `\i0`
    # explicitly forces italic OFF (matches the "Not Italic" annotation
    # in Word's style panel). Heading 4 is shared with `section_h2` for
    # the stylesheet declaration — both reference the same `\s4` style,
    # which is fine since RTF style IDs are global per document.
    "subgroup_heading":     rf"\s4\qc\b\ul\fs{SUBGROUP_HEADING_FS}\i0",
    # Level-1 section heading (C.1, A.1, B.1, ...) — Word "heading 3"
    # so the auto-TOC picks it up at the section level. Bold + body-size
    # font (`\fs{SECTION_H1_FS}` = fs22 = 11pt) gives visual weight
    # without competing with the heading-1/heading-2 P&T template theme.
    # The template doesn't auto-number this level, so emitters DO
    # include the "A.1" / "C.1" prefix as literal text.
    "section_h1":           rf"\s3\b\fs{SECTION_H1_FS}",
    # Level-2 section heading (C.5.1, C.16.1, ...). Word "heading 4".
    "section_h2":           rf"\s4\i\fs{SECTION_H2_FS}",
    # Level-3 section heading (C.16.2.3, ...). Word "heading 4" still
    # so the TOC doesn't explode; visual differentiation comes from
    # the smaller font + indent.
    "section_h3":           rf"\s4\i\fs{SECTION_H3_FS}",
    # Level-4 section heading (rare; reserved). Word "heading 4".
    "section_h4":           rf"\s4\i\fs{BODY_FONT_SIZE}",
    # Inline subheading — italic + fs26, NO heading style (does not
    # appear in TOC). Used for C.5 subcategory fallback labels.
    "inline_subheading":    rf"\i\fs{INLINE_SUBHEADING_FS}",
    # Career-phase divider — italic body-font text inside top + bottom
    # borders. No heading style; the borders communicate structure.
    "career_phase_divider": rf"\i\fs{BODY_FONT_SIZE}",
    # Introductory note — italic body-font paragraph orienting the
    # reader at the top of a section (grant totals, C.22 cross-refs,
    # C.24 service intro, etc.).
    "intro_note":           rf"\i\fs{BODY_FONT_SIZE}",
    # N/A placeholder — plain body-font (no emphasis), used when a
    # section has no YAML data but the heading still emits. Preserves
    # the longstanding "N/A" visual — readers scan for the cue and
    # italic would change its weight too much.
    "na_placeholder":       rf"\fs{BODY_FONT_SIZE}",
    # --- Inline (mid-paragraph) styles for styled_inline() ---
    # Field label inside an entry body (A.1 "Name", "ORCID",
    # "Google Scholar").
    "field_label":          r"\i",
    # Grant total label inside the intro-note ("Total amount of
    # external funding as PI:"). Italic to match the surrounding
    # intro-note style.
    "grant_total_label":    r"\i",
    # Bold header cell content inside an `RtfTable` row. Routed via
    # `styled_inline` from `RtfTable._render_row` so the bold-wrap
    # decision lives in the registry, not inline at every renderer
    # that builds a header row.
    "table_header":         r"\b",
    # Italic venue / title inside a citation body — used by
    # `format_journal_citation`, `render_under_review_section`,
    # `render_invited_talk`, `render_media_appearance`, A.2 thesis
    # title, etc.
    "venue_italic":         r"\i",
    # Underlined tier label / due-date / society — used by C.2/C.4
    # tier marker, A.1 under-review due-date, C.7 leadership-role
    # society field.
    "underline_marker":     r"\ul",
    # Bold summary leader in a hanging-indent entry body — used by
    # C.20 entrepreneurial activities + C.21 technology transfer
    # ("Summary: description.").
    "entry_summary":        r"\b",
    # Bold name in a header-style entry (C.22 software product name,
    # C.19 patent row code).
    "entry_name":           r"\b",
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
# divider). Captured here so the verbose border-control RTF lives in
# one place.
BORDER_BLOCKS: dict[str, str] = {
    "career_phase_divider":
        r"\brdrt\brdrs\brdrw15\brsp40\brdrb\brdrs\brdrw15",
}


# Close-tag pairings. `\fs{N}` resets via the trailing body-paragraph
# re-baseline, not via a closing token, so font-size doesn't need a
# close. Likewise `\qc` (alignment) resets via the paragraph reset.
# `\sN` (paragraph style) and `\f0` (font face) similarly reset across
# paragraphs.
_CLOSE_TAGS: dict[str, str] = {
    r"\b":  r"\b0",
    r"\i":  r"\i0",
    r"\ul": r"\ulnone",
}


def _close_for(open_codes: str) -> str:
    """Derive the close-tag sequence for an open-tag string.

    Walks the open-tag tokens (e.g. `\\b\\fs28` → ['\\b', '\\fs28'])
    and pairs each with its close where one exists. Font-size resets
    are handled by the body-paragraph re-baseline after the styled
    paragraph, not by a closing token, so `\\fs{N}` doesn't contribute
    to close. Closes emit in REVERSE order of opens so nested formatting
    (e.g. `\\b\\ul` → `\\ulnone\\b0`) unnests correctly.
    """
    tokens = re.findall(r"\\[a-z]+", open_codes)
    closes = [_CLOSE_TAGS[t] for t in tokens if t in _CLOSE_TAGS]
    return "".join(reversed(closes))


# ----- Stylesheet declarations (derived from STYLES) ---------------------


# Map each STYLES entry that targets a Word heading level (via `\sN`)
# to (Word style-name, sb, sa). Word's "Insert Table of Contents"
# scans for paragraphs styled with these named styles — so the
# stylesheet declaration MUST list them exactly. Derived from the
# registry so a registry edit (e.g., bumping `SECTION_H1_FS` to 30)
# automatically updates the stylesheet declaration too.
_HEADING_STYLE_NAMES: list[tuple[str, str, int, int]] = [
    # (style_key, Word-style-name, sb, sa). Maps each registry style key
    # to its Word "heading N" name in the stylesheet block. The P&T
    # template's TOC pulls heading 1 (III. Roman), heading 2 (A./B./C.),
    # heading 3 (A.1/C.1). Subgroup bands (PUBLISHED WORK) and deeper
    # nested headings (C.5.1, C.16.2.3) are intentionally absent — they
    # carry only direct visual formatting, no `\sN` style ID.
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
    """Emit a fully-styled paragraph applying `STYLES[style]`.

    The single paragraph-level emit primitive — every styled paragraph
    in the doc routes through here. The function:
      * (Optionally) emits a hard page break before the paragraph
        when `style ∈ PAGE_BREAK_BEFORE`.
      * Resets paragraph formatting (`\\pard\\plain`).
      * Selects Times New Roman explicitly (`\\f0`) so Word doesn't
        fall back to its UI default after `\\plain` resets.
      * Applies the indent + spacing (`\\li{N}\\sb{N}\\sa{N}`).
      * Applies any border block (career-phase divider only today).
      * Applies the style's open RTF control sequence (`STYLES[style]`).
      * Writes the (already-escaped) text.
      * Applies the matching close sequence.
      * Re-baselines the body paragraph that follows.

    `text` is assumed to already be RTF-safe (escaped via `escape_rtf`
    or built from sentinel-bearing RTF fragments). This primitive
    does NOT escape — that's the caller's job.
    """
    prefix = STYLES[style]
    sb, sa = SPACING.get(style, (0, 0))
    border = BORDER_BLOCKS.get(style, "")
    close = _close_for(prefix)
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
    """Return an inline RTF fragment with `STYLES[style]` applied.

    For mid-paragraph emphasis where the surrounding paragraph context
    is owned by another emitter — e.g., A.1 field labels embedded
    inside `_emit_list_item`'s body string, or grant total labels
    embedded inside an intro-note paragraph.

    `text` is assumed to already be RTF-safe; this primitive does NOT
    escape.
    """
    prefix = STYLES[style]
    close = _close_for(prefix)
    return f"{prefix} {text}{close}"
