"""RTF output: table builder + per-citation rendering + section assembly."""
from __future__ import annotations

import io
import logging
import re
from typing import IO, Optional, Sequence

from .builders import escape_rtf
from .styles import emit_styled, styled_inline
from .config import (
    ORG_EXPANSIONS,
    PATENT_TABLE_WIDTHS,
    RANKED_SECTIONS,
    SECTION_CODES,
    SECTION_HEADINGS,
    SECTION_ORDER,
    TIER_LABELS,
)
from .types import (
    Award, BibEntry, CandidateInformation, Citation, ConferencePresentation,
    CourseDevelopment, CourseTaught, Degree, EntrepreneurialActivity, Grant,
    GrantPerson, Identifiers, InvitedTalk, KeyWork, LeadershipRole,
    MediaAppearance, OtherPosition, Patent, PostdocVisiting,
    ProfessionalMembership, Publications, Section,
    ServiceEntry, SoftwareProduct, Student, StudentAward,
    TechnologyTransfer, UndergradPathway, UndergradProduct, UnderReview,
)
from .authors import parse_name_parts
from .venue import parse_venue
from .latex import decode_latex
from .venue import normalize_title


log = logging.getLogger(__name__)


# Module-level dict carrying optional hand-authored intro prose, keyed
# by section code (`A.1`, `C.5.4`, `C.16.2.1`, …). Set at the top of
# `write_rtf` from the `section_prose` argument; consulted by
# `_emit_section_heading` after each heading emit. Module-level (not
# threaded through every helper) because section headings emit from
# ~20 sites and threading would noisy every signature. Single-threaded
# render is the only supported mode — no thread-safety concern.
_section_prose: dict[str, list[str]] = {}


# Per-cell 4-side single-line border block, shared across every table in
# the document (RtfTable + hand-rolled student/postdoc/grant rows). Without
# this block emitted between `\trowd...` and each `\cellx`, Word's RTF
# parser drops vertical borders and the row contents collapse visually.
_CELL_BORDER_BLOCK = (
    "\\clbrdrt\\brdrs\\brdrw15"
    "\\clbrdrb\\brdrs\\brdrw15"
    "\\clbrdrl\\brdrs\\brdrw15"
    "\\clbrdrr\\brdrs\\brdrw15"
)


class RtfTable:
    """Composes an RTF table. Column widths in twips (1440 twips = 1 inch).

    Usage:
        t = RtfTable([2400, 2000, 1600])
        t.add_header(["A", "B", "C"])
        t.add_row(["1", "2", "3"])
        rtf_string = t.render()
    """

    def __init__(self, column_widths: list[int]):
        if not column_widths:
            raise ValueError("RtfTable requires at least one column")
        self.column_widths = column_widths
        cumulative = 0
        self._cellx: list[int] = []
        for w in column_widths:
            cumulative += w
            self._cellx.append(cumulative)
        self._rows: list[tuple[list[str], bool]] = []

    def add_header(self, cells: list[str]) -> None:
        self._check_arity(cells)
        self._rows.append((cells, True))

    def add_row(self, cells: list[str]) -> None:
        self._check_arity(cells)
        self._rows.append((cells, False))

    def render(self) -> str:
        return "".join(self._render_row(cells, h) for cells, h in self._rows)

    def _check_arity(self, cells: list[str]) -> None:
        if len(cells) != len(self.column_widths):
            raise ValueError(
                f"Expected {len(self.column_widths)} cells, got {len(cells)}",
            )

    def _render_row(self, cells: list[str], is_header: bool) -> str:
        parts: list[str] = [r"\trowd\trgaph108\trleft0"]
        # Per-cell 4-side single-line border. Without this, viewers render the
        # row as one continuous strip and adjacent cell contents visually
        # concatenate. The hand-rolled student/postdoc renderers emit the
        # same border block per cellx — keep RtfTable consistent.
        for pos in self._cellx:
            parts.append(_CELL_BORDER_BLOCK)
            parts.append(rf"\cellx{pos}")
        # Each cell's content paragraph MUST start with `\pard\intbl` (per
        # the RTF spec — "every paragraph in a table row must have \intbl
        # specified or inherited"). Without it, TextEdit + Word treat the
        # row as inline text with literal \cell markers and the table
        # structure is lost — the cells visually concatenate AND pasting
        # into Word doesn't produce a real table.
        for cell in cells:
            # Header cells route bold through `styled_inline("table_header", …)`
            # so the bold-wrap decision lives in the style registry, not
            # inline at the table renderer. Body cells emit verbatim.
            content = styled_inline("table_header", cell) if is_header else cell
            parts.append(rf"\pard\intbl {content}\cell")
        parts.append("\\row\n")
        return "".join(parts)


def apply_acronym_expansions(venue: str, done: set[str]) -> str:
    """Spell out an org acronym on its first occurrence in this section."""
    for acronym, expansion in ORG_EXPANSIONS.items():
        if acronym in done:
            continue
        if re.search(rf"\b{acronym}\b", venue):
            venue = re.sub(rf"\b{acronym}\b", expansion, venue, count=1)
            done.add(acronym)
    return venue


def render_link_field(link: str) -> str:
    """RTF for the hyperlink + visible prefix.

      https://doi.org/X         →  DOI: X
      https://nvd.nist.gov/...  →  CVE-XXXX-NNNN  (no prefix; the ID is self-descriptive)
      anything else             →  URL: <full>

    The trailing space after the prefix is LOAD-BEARING — without it the
    rendered output reads `DOI:10.1145/...` (no breathing room), which
    the "every punctuation followed by text gets a space" check flags.
    """
    if not link:
        return ""
    if link.startswith("https://doi.org/"):
        prefix = "DOI: "
        display = link[len("https://doi.org/"):]
    elif "nvd.nist.gov/vuln/detail/" in link:
        prefix = ""
        display = link.rsplit("/", 1)[-1]
    else:
        prefix = "URL: "
        display = link
    return (
        f' {prefix}{{\\field{{\\*\\fldinst HYPERLINK "{link}"}}'
        f'{{\\fldrslt {display}}}}}'
    )


def render_citation(
    cit: Citation,
    expansion_done: set[str],
    paper_index: Optional[dict[str, str]] = None,
    key_work_index: Optional[dict[str, str]] = None,
) -> str:
    """RTF body for one citation (no paragraph wrapping).

    `paper_index` maps normalized-bib-title → section code (e.g. "C.4.7").
    When `cit.back_ref_title` is set, the back-pointer wording reflects
    the relationship: CVE entries render "(discovered as part of C.4.7)"
    (the CVE was found during that paper's work); non-CVE entries render
    "(see C.4.7)" (generic cross-reference).

    `key_work_index` maps normalized-bib-title → "C.1.N". When this citation's
    title is itself a key work, a "(listed as C.1.N)" cross-link is appended.
    Pass None when rendering inside the C.1 section itself to avoid self-reference.
    """
    venue = apply_acronym_expansions(cit.venue, expansion_done) if cit.venue else ""
    if cit.rank == "CVE":
        # CVE entries lead with the CVE-ID hyperlink (e.g.
        # "CVE-2023-37459. Davis, J. ...") instead of trailing it after
        # the venue. `render_link_field` returns a leading-space form for
        # the body-appended use case; strip it for the prefix position.
        cve_id_field = render_link_field(cit.link).lstrip()
        body = f"{cve_id_field}. {cit.authors_rtf} ({cit.year_str}). {escape_rtf(cit.title)}"
        if venue:
            body += f", {styled_inline('venue_italic', escape_rtf(venue))}"
        body += "."
    else:
        # No comma between the last author and the year — the last-author
        # `*` superscript marker already terminates the author list, and
        # a trailing comma before `(YEAR)` reads as a typo.
        body = f"{cit.authors_rtf} ({cit.year_str}). {escape_rtf(cit.title)}"
        if venue:
            body += f", {styled_inline('venue_italic', escape_rtf(venue))}"
        body += f"{escape_rtf(cit.details)}."
        # Append DOI (if any) and close it with a period so the regular
        # citation is fully terminated before the tier marker.
        link_field = render_link_field(cit.link)
        if link_field:
            body += link_field + "."
    # Tier marker: underlined, NOT italicized, period after.
    # "Venue rank: " prefix only for peer-reviewed venue categories
    # (Journals + Conferences); bare label for everything else.
    # `Tier N` is kept atomic via an RTF non-breaking space (`\~`) so
    # the digit doesn't orphan onto the next line — caught 260603 on
    # the "Sense of time" USENIX citation. Other multi-word labels
    # ("Technical report", "Security disclosure", "Book chapter") may
    # wrap naturally — the awkward case is digit-orphaning.
    prefix = "Venue rank: " if cit.section in RANKED_SECTIONS else ""
    tier_label = TIER_LABELS[cit.rank].replace("Tier ", "Tier\\~")
    body += f" {styled_inline('underline_marker', f'{prefix}{tier_label}')}."
    if cit.back_ref_title and paper_index:
        ref = paper_index.get(normalize_title(cit.back_ref_title))
        if ref:
            # CVE entries: the linked paper IS where the vuln was
            # discovered / disclosed during. Non-CVE: generic cross-ref.
            if cit.rank == "CVE":
                body += f" (discovered as part of {_code_link(ref)})"
            else:
                body += f" (see {_code_link(ref)})"
    if key_work_index:
        kw_ref = key_work_index.get(normalize_title(cit.title))
        if kw_ref:
            # The kw_ref target is always a C.1.X code (the key-work
            # section). Use "(key paper {ref})" wording instead of the
            # generic "(listed as {ref})" — flags promotion-relevant
            # papers explicitly without making the reader hop over to
            # C.1 to know it's a designated key work.
            body += f" (key paper {_code_link(kw_ref)})"
    return body


def render_key_work_citation(
    kw: KeyWork,
    expansion_done: set[str],
    paper_index: dict[str, str],
) -> str:
    """Citation-only RTF for a C.1 key work (impact handled separately).

    Suppresses key_work_index so a C.1 entry doesn't self-reference; adds a
    `(listed as C.X.Y)` cross-link to the paper's canonical bib location.
    """
    body = render_citation(kw.citation, expansion_done, paper_index, key_work_index=None)
    canonical = paper_index.get(normalize_title(kw.citation.title))
    if canonical:
        body += f" (listed as {_code_link(canonical)})"
    return body


## ----- Career-phase dividers (C.1 – C.5) ----------------------------------
#
# Publication entries are partitioned into two visual regions:
#   * year ≤ 2020 → "PhD studies at Virginia Tech"
#   * year ≥ 2021 → "Assistant Professor at Purdue"
#
# This is a VISUAL CUE ONLY — the section's C.X.N numbering is NOT reset
# across the boundary. The label appears as a thin top-border paragraph
# with the bold phase name inline, fired the first time a year on the
# other side of the boundary appears in the section's iteration order.
# Patents (C.19) are excluded — they have their own clear marker.

_CAREER_BOUNDARY_YEAR = 2020  # ≤ boundary → PhD; > boundary → AP


def _emit_inline_heading(
    out: IO[str],
    text: str,
    indent: int,
    *,
    border: bool = False,
    font_size: int = 26,
) -> None:
    """Emit an inline heading — an italic sub-label that visually
    sub-divides a numbered list within a section. Single source of truth
    for the "label-that's-not-a-section-heading-but-also-not-a-list-entry"
    paragraph shape across the renderer.

    Three call sites:
      * C.5 subcategory subheading
        ("Magazine" / "Technical Reports" / "Direct computing industry
        impacts") — between subgroups of `C.5.N` entries.
      * C.16.2.4 / C.16.3.3 student-awards tier label
        ("National and International Awards" / "University-Level Awards")
        — between tier subgroups of `{code}.N` entries.
      * Career-phase divider
        ("PhD studies at Virginia Tech" / "Assistant Professor at Purdue
        University") — between year-bucketed runs of citations within
        C.1-C.5. `border=True` here so the marker reads as a fully
        enclosed region boundary.

    Italic (not bold) styling is load-bearing: a bold inline heading
    reads as a section heading (same visual class as "C.16.2.4
    Undergraduate Awards"), confusing the tenure-packet reader about
    what's hierarchically a heading vs sub-label. Italic + larger-than-
    body font (fs26 vs fs24) gives a clear "subheading" cue without
    competing with the section-heading band.

    `indent` should match the section's entry LABEL column (i.e.,
    `_label_position_for_code(f"{section_code}.1")`) so the inline
    heading sits at the same column the "C.X.Y.N." prefixes start at —
    leading the sub-list visually rather than floating to the left.
    """
    # Border variant → `career_phase_divider` style (italic body-font
    # text inside top + bottom borders, sb120/sa240). Non-border
    # variant → `inline_subheading` style (italic fs26, sb120/sa60).
    # Both route through `emit_styled`; the registry owns the open
    # tags, spacing, and (for border) the border-block RTF.
    style = "career_phase_divider" if border else "inline_subheading"
    emit_styled(out, style, escape_rtf(text), indent=indent)


def _career_phase_for_year(year: int) -> str:
    """('phd' | 'ap') based on the publication year. The sentinel year
    9999 ("In Press" / unparseable) is bucketed as 'ap' so under-review
    or just-accepted work renders in the post-PhD region."""
    return "phd" if year <= _CAREER_BOUNDARY_YEAR else "ap"


_CAREER_PHASE_LABELS: dict[str, str] = {
    "phd": "PhD studies at Virginia Tech",
    "ap":  "Assistant Professor at Purdue University",
}


def _maybe_emit_career_phase_divider(
    out: IO[str], year: int, current_phase: str, indent: int,
) -> str:
    """Emit a top-bordered, bold-labeled divider paragraph the first time
    a publication's year crosses into a new career phase relative to
    `current_phase`. The divider is purely visual — section numbering
    flows through unchanged.

    Returns the new phase so the caller can thread it through the
    iteration loop. The first entry of any section always crosses from
    `current_phase=""` into a phase — i.e., the first entry of a section
    always emits a leading divider so the region's label is anchored at
    the top of the region.
    """
    phase = _career_phase_for_year(year)
    if phase == current_phase:
        return current_phase
    label = _CAREER_PHASE_LABELS[phase]
    # `\brdrt + \brdrb` draws thin horizontal lines ABOVE AND BELOW the
    # label paragraph so the marker reads as a fully enclosed band. The
    # earlier single-top-border form left the label visually attached to
    # the citation directly below it (which read as "the rule belongs to
    # this citation"); both rules anchor the label to the boundary.
    # NO em-dash glyphs around the label — `\ansicpg1252` documents
    # mangle literal U+2014 ("— — —" → "â€"") when Word's RTF reader
    # encounters raw UTF-8 bytes. Italic label inside a top + bottom
    # border alone is the marker. Routes through the shared inline-
    # heading helper so the "italic, not bold" + indent decision lives
    # in one place across all three inline-heading sites.
    _emit_inline_heading(out, label, indent, border=True)
    return phase


def _emit_author_marker_legend(out: IO[str]) -> None:
    """Notation block — explains the per-author markup the citation
    renderer (`authors.format_author`) emits across every C.X section.

    Markers in lock-step with `format_author` (`src/pubs_emitter/authors.py`):
      * **Bold**           → candidate (Davis), via the `ME` config list
      * `*` (superscript)  → corresponding author (`is_last=True` by default,
                              overridable via `CORRESPONDING_AUTHORS`)
      * `#` (superscript)  → senior co-author / PhD or post-doc advisor,
                              via the `advisors` config list
      * `G` (superscript)  → graduate student supervised by Davis
                              (`STUDENTS["G"]` ∪ YAML `graduate_students`)
      * `U` (superscript)  → undergraduate student supervised by Davis
                              (`STUDENTS["U"]`)

    Rendered as an italic header line + 5 indented bullet entries; the
    superscripts in each bullet use the same `\\super`/`\\nosupersub{{}}`
    markup `format_author` produces so the legend visually matches the
    actual citation form. Italic notation block is set apart from the
    surrounding numbered list shape.

    Layout: "Notation:" sits at C.1's body indent (one level right of
    the C.1 heading); bullets are indented ONE FURTHER step right
    (under "Notation:") with the bullet glyph in a small hanging
    gutter. Previously the bullets emitted with `\\fi-360` outdent at
    the same `li` as "Notation:", which dropped the bullet glyph LEFT
    of "Notation:" — visually backwards.
    """
    notation_li = _body_indent_for_code(SECTION_CODES["Key Works"])
    # Bullet glyph one step right of "Notation:", text one further step.
    # Together the glyph + text both visibly nest under the "Notation:"
    # word so the reader's eye reads the list as belonging to it.
    bullet_li = notation_li + 2 * _HEADING_INDENT_PER_LEVEL
    bullet_fi = -_HEADING_INDENT_PER_LEVEL  # small gutter for the glyph
    bullet_tx = bullet_li
    out.write(
        f"\\pard\\li{notation_li} {styled_inline('field_label', 'Notation:')}\\par\n"
    )

    def _bullet(rtf_body: str) -> None:
        out.write(
            f"\\pard\\li{bullet_li}\\fi{bullet_fi}\\tx{bullet_tx} "
            f"\\u8226?\\tab {rtf_body}\\par\n"
        )

    # lint-allow: raw-rtf — "Bold" demonstrates the marker visually.
    _bullet("\\b Bold\\b0  denotes the candidate (James C. Davis).")
    _bullet(
        "\\super *\\nosupersub{} denotes the corresponding author."
    )
    _bullet(
        "\\super #\\nosupersub{} denotes PhD or postdoc advisor."
    )
    _bullet(
        "\\super G\\nosupersub{} denotes a graduate student supervised "
        "by the candidate."
    )
    _bullet(
        "\\super U\\nosupersub{} denotes an undergraduate student "
        "supervised by the candidate."
    )
    # Trailing blank for visual spacing before the first entry.
    out.write("\\pard\\par\n")


def render_key_works_section(
    key_works: list[KeyWork],
    paper_index: dict[str, str],
    out: IO[str],
) -> None:
    """Emit C.1 entries as TWO paragraphs each:

      1. Hanging-indent paragraph: `C.1.N.\\tab citation text` (matches other sections).
      2. Indented block paragraph: impact text, NOT italic, indented under the citation.

    Two real paragraphs (not `\\line` soft-breaks) so Word paste preserves
    the layout cleanly. The author-marker legend (Bold / * / # / G / U)
    is emitted ONCE here, just below the C.1 heading, since C.1 is the
    first section that displays formatted citations.
    """
    if not key_works:
        return
    code = SECTION_CODES["Key Works"]
    heading = SECTION_HEADINGS["Key Works"]
    _emit_section_heading(out, code, heading)
    _emit_author_marker_legend(out)
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(key_works)))
    expansion_done: set[str] = set()
    phase = ""
    for idx, kw in enumerate(key_works, 1):
        phase = _maybe_emit_career_phase_divider(
            out, kw.citation.year, phase, indent,
        )
        citation = render_key_work_citation(kw, expansion_done, paper_index)
        _emit_list_item_with_body(
            out, f"{code}.{idx}", citation, escape_rtf(kw.impact), indent=indent,
        )


def order_citations_for_emission(
    section: Section, citations: list[Citation],
) -> list[Citation]:
    """Return citations in the order the renderer will emit them.

    Single source of truth for "what's the i-th C.X entry?" — used by
    both `build_paper_index` (to produce the back-pointer code) AND the
    renderers (to actually emit). Default: pass-through (renderer
    iterates input order). Per-section override: C.5's subcategory
    grouping (Magazine → Technical Reports → Direct computing industry
    impacts) is applied here so paper_index["C.5.N"] always matches the
    rendered C.5.N bookmark.

    If a future renderer re-orders its input, the re-order belongs HERE
    — not duplicated between `build_paper_index` and the renderer. The
    260603 RCA: paper_index used source order, renderer regrouped by
    subcategory, the two diverged → C.16.2.3.25 linked at C.5.8 but the
    rendered C.5.8 was a different paper.
    """
    if section == "Other publications and products":
        by_subcat: dict[str, list[Citation]] = {}
        for cit in citations:
            subcat = _C5_SUBCATEGORY_BY_RANK.get(cit.rank, "Other")
            by_subcat.setdefault(subcat, []).append(cit)
        def _subcat_key(s: str) -> tuple[int, str]:
            try:
                return (_C5_SUBCATEGORY_ORDER.index(s), "")
            except ValueError:
                return (len(_C5_SUBCATEGORY_ORDER), s.lower())
        return [
            cit for subcat in sorted(by_subcat.keys(), key=_subcat_key)
            for cit in by_subcat[subcat]
        ]
    return citations


def build_paper_index(publications: Publications) -> dict[str, str]:
    """Map normalized-paper-title → 'C.X.Y' for back-pointer resolution.

    Numbering goes through `order_citations_for_emission` so the index
    codes match the rendered bookmarks for every section, including the
    ones whose renderers re-order their input (C.5). CVEs are excluded
    from the index (they'd be back-pointer chains).
    """
    index: dict[str, str] = {}
    for section in SECTION_ORDER:
        if section == "Patents":
            continue
        ordered = order_citations_for_emission(
            section, publications.get(section, []),
        )
        section_code = SECTION_CODES[section]
        # C.5 uses subcategory-nested codes: C.5.{subcat_idx}.{within_idx}
        # where subcat_idx is the subcategory's position in
        # _C5_SUBCATEGORY_ORDER. Every other section uses the flat
        # `{section_code}.{idx}` form. Keep the loop unified by tracking
        # within-subcat counters when needed.
        is_c5 = section == "Other publications and products"
        prev_subcat: Optional[str] = None
        within_idx = 0
        flat_idx = 0
        for cit in ordered:
            if cit.rank == "CVE":
                continue
            flat_idx += 1
            if is_c5:
                subcat = _C5_SUBCATEGORY_BY_RANK.get(cit.rank, "Other")
                if subcat != prev_subcat:
                    within_idx = 0
                    prev_subcat = subcat
                within_idx += 1
                subcat_idx = _C5_SUBCATEGORY_IDX.get(subcat, 0)
                code = f"{section_code}.{subcat_idx}.{within_idx}"
            else:
                code = f"{section_code}.{flat_idx}"
            if cit.title:
                index[normalize_title(cit.title)] = code
    return index


def _emit_group_heading(
    out: IO[str], code: str, title: str, *, restart_numbering: bool = False,
) -> None:
    """Emit a group heading ("GENERAL INFORMATION", "SELF-EVALUATION",
    etc.). Uses Word "heading 2" (P&T template's A./B./C. level). The
    template's heading-2 multilevel list auto-numbers the letter
    prefix, so emitters DROP the literal "A." / "B." / "C." from the
    rendered text — Word's list provides it.

    `restart_numbering=True` emits an `{\\*\\pn\\pnlvlbody\\pnstart1}`
    list-restart marker before the heading. Use on the FIRST heading-2
    emit per Roman section so Word's heading-2 list restarts at A
    instead of continuing the doc-global sequence (B/C/D…). `code` is
    preserved in the signature for callsite intent + future cross-ref
    bookmarks.
    """
    # Leading blank for breathing room above the group heading
    # (preserves the visual spacing the OLD inline emit had at this
    # site).
    out.write(f"\\pard\\plain\\f0\\fs{_BODY_FONT_SIZE}\\par\n")
    if restart_numbering:
        # `\pnstart1` resets the paragraph's auto-numbering start value
        # to 1. The `\pn` group is paragraph-level numbering metadata
        # that Word respects when computing the list letter for the
        # paragraph that follows. Emitting it in the breathing-room
        # paragraph keeps the styled heading clean.
        out.write("\\pard\\plain{\\*\\pn\\pnlvlbody\\pnstart1}\\par\n")
    emit_styled(out, "group_heading", title)


def _emit_roman_section_heading(
    out: IO[str], roman: str, title: str, *, suppress_page_break: bool = False,
) -> None:
    """Emit a Purdue-template Roman-numeral section heading (e.g.
    "V. Supporting Documentation for Pending Publications.").

    Distinguished from `_emit_group_heading` (A./B./C. sub-section
    groups, which use CAPS BOLD text) by Title-Case rendering — the
    Roman numeral is a TOP-LEVEL document section marker (the
    candidate's tenure-packet template uses I., II., ..., V. for the
    primary outline). Bold + fs32 + left-aligned + Title-Case.

    Preceded by a hard page break, matching the template's "separate
    each major section with a page break" convention for the CAPS-
    BOLD-UNDERLINED + Roman headings — EXCEPT when this heading is the
    very first element of the document (set `suppress_page_break=True`),
    where the leading page break would push the title onto a blank
    second page. The III. opener at the top of the doc uses this; V.
    keeps the default page break.
    """
    # Routes through `emit_styled("roman_section", …)`. The roman_section
    # style in the registry carries the page break (PAGE_BREAK_BEFORE)
    # + `\s1` Word "heading 1" marker + bold. The P&T template's
    # heading-1 list auto-numbers the Roman prefix, so this emit drops
    # the `roman` argument from the rendered text (would produce "I.
    # III. MATERIAL…" doubled otherwise). `roman` is kept in the
    # signature for callsite intent.
    emit_styled(
        out, "roman_section", escape_rtf(title),
        suppress_page_break=suppress_page_break,
    )


def _emit_intro_note(
    out: IO[str], text: str, *, indent: int = 0,
) -> None:
    """Emit an italic "introductory note" paragraph — the styled
    one-liner that orients the reader at the top of a section before
    the entries begin.

    Use sites:
      * C.9 Conference Presentations — explains the lead-author-talks
        convention
      * C.10-C.13 Grants — section totals ("Total amount of external
        gifts and voluntary support: $421,478")
      * C.22 Software Products — cross-ref to C.5 for non-software impact
      * C.24 Profession Service — peer-review framing
      * Future: any single-line orienter that introduces a section

    All such notes share italic styling so a reader scanning the doc
    sees "this is meta about the section that follows" at a glance,
    distinct from entries and headings. The `indent` param matches the
    section's body indent (use `_body_indent_for_code(code)`) when the
    note should align with the entries it introduces.
    """
    # Routes through `emit_styled("intro_note", …)`. The intro_note
    # style in the registry carries the italic emphasis + body-font
    # size + sa120 trailing spacing.
    emit_styled(out, "intro_note", text, indent=indent)


def _emit_subgroup_heading(out: IO[str], title: str) -> None:
    """Emit a centered, bold, underlined sub-group heading that divides
    Section C into thematic clusters — PUBLISHED WORK (C.1-C.5),
    EXTERNAL VISIBILITY (C.6 onward), MENTORING (C.14 onward),
    LEARNING (C.17 onward). Matches the Purdue template's banner-style
    sub-group dividers from the candidate's source template screenshots.

    Each subgroup heading is preceded by a HARD PAGE BREAK per the
    Purdue template convention ("Separate each major section with a
    page break. These are identified in the template with CAPS BOLD
    UNDERLINED."). So every CAPS-BOLD-UNDERLINED heading in the
    rendered packet begins a fresh page.

    Visual class: lighter than `_emit_group_heading` (no Roman code +
    title pair) but heavier than `_emit_section_heading` because it
    groups MULTIPLE sections under one label. fs28 + bold + underlined
    + centered keeps it distinct from a section heading (fs28 + bold +
    left-aligned) and a group heading (fs32 + bold + left-aligned).
    """
    # Routes through `emit_styled("subgroup_heading", …)`. The
    # subgroup style in the registry already carries the page break
    # (PAGE_BREAK_BEFORE), the `\s2` Word "heading 2" marker, and the
    # CAPS BOLD UNDERLINED centered styling.
    emit_styled(out, "subgroup_heading", escape_rtf(title))


def _emit_external_url(label: str, url: str) -> str:
    """Inline RTF HYPERLINK targeting an external URL.

    Returns an RTF fragment (no surrounding paragraph) suitable for splicing
    into a paragraph. `label` renders unmodified before the link; the link
    itself shows the URL as its own display text with the `\\cs1 Hyperlink`
    character style so Word copy-paste preserves the blue/underline.
    """
    # \cs1 + \cf1\ul = belt-and-suspenders: named char style for Word
    # clipboard transit + inline fallback for basic RTF viewers.
    return (
        f"{label}{{\\field{{\\*\\fldinst HYPERLINK \"{url}\"}}"
        f"{{\\fldrslt {{\\cs1\\cf1\\ul {escape_rtf(url)}}}}}}}"
    )


def _render_a1_identifiers(out: IO[str], ident: Identifiers) -> None:
    """A.1 sub-renderer — numbered list (A.1.1, A.1.2, A.1.3).

    Shape per entry: `A.1.N. {bold label}: {value or URL hyperlink}`.
    Empty fields are SKIPPED entirely (no orphan entry), so the numbering
    is dense over the present fields (e.g., if no ORCID, A.1.1 = Name
    and A.1.2 = Google Scholar). Identifiers presence is voluntary —
    every emitted entry IS a populated row.

    Bookmark namespace: A.1.N → bookmark name `A_1_N`. Section V's
    Under Review entries live at the SAME visible code prefix (A.1.M)
    but are bookmarked with a `V_` prefix (`V_A_1_M`) so the two
    sections' bookmarks don't collide. Cross-refs to Section V's A.1
    entries carry the pipe-form ("Section V, A.1.M|V.A.1.M") so the
    display reads "Section V, A.1.M" while the hyperlink targets
    `V_A_1_M`.
    """
    code = SECTION_CODES["Identifiers"]
    # Build the rows in emission order; skip empty fields entirely so
    # numbering stays dense.
    rows: list[tuple[str, str]] = []
    if ident.name:
        rows.append(("Name", escape_rtf(ident.name)))
    if ident.orcid:
        rows.append(("ORCID", _emit_external_url("", ident.orcid)))
    if ident.google_scholar:
        rows.append(
            ("Google Scholar", _emit_external_url("", ident.google_scholar))
        )
    if not rows:
        out.write("\\pard\\par\n")
        return
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(rows)))
    for idx, (label, body_rtf) in enumerate(rows, 1):
        # `styled_inline("field_label", …)` returns `\i Name\i0` —
        # italic mid-paragraph label. Keeps the A.1.N codes visually
        # leading the row while the field name reads as a qualifier.
        body = f"{styled_inline('field_label', escape_rtf(label))} : {body_rtf}"
        _emit_list_item(out, f"{code}.{idx}", body, indent=indent)


def _render_a2_degrees(out: IO[str], degrees: list[Degree]) -> None:
    """A.2 sub-renderer — numbered list (A.2.1, A.2.2, ...).

    Per-entry body: "{institution}, {years}. {degree}. {thesis_kind}:
    /italic thesis_title/, supervised by {advisor}." Thesis bits are
    omitted when `thesis_title` is empty (degrees without a thesis).
    """
    if not degrees:
        emit_styled(out, "na_placeholder", "N/A", indent=720)
        return
    code = SECTION_CODES["Degrees"]
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(degrees)))
    for idx, d in enumerate(degrees, 1):
        body = f"{escape_rtf(d.institution)}, {escape_rtf(d.years)}. {escape_rtf(d.degree)}."
        if d.thesis_title:
            thesis_label = d.thesis_kind or "Thesis"
            body += (
                f" {escape_rtf(thesis_label)}: "
                f"{styled_inline('venue_italic', escape_rtf(d.thesis_title))}"
            )
            if d.advisor:
                body += f", supervised by {escape_rtf(d.advisor)}"
            body += "."
        _emit_list_item(out, f"{code}.{idx}", body, indent=indent)


def _render_a3_positions_at_purdue(out: IO[str], positions: list[str]) -> None:
    """A.3 sub-renderer — numbered list (A.3.1, A.3.2, ...).

    A list rather than a single prose line so promotions append a new
    entry (Assistant → Associate → Full) instead of editing the prose.
    Empty list → indented "N/A" line.
    """
    if not positions:
        indent = _body_indent_for_code(SECTION_CODES["Positions at Purdue"])
        emit_styled(out, "na_placeholder", "N/A", indent=indent)
        return
    code = SECTION_CODES["Positions at Purdue"]
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(positions)))
    for idx, p in enumerate(positions, 1):
        _emit_list_item(out, f"{code}.{idx}", escape_rtf(p), indent=indent)


def _render_a4_positions_at_other(out: IO[str], positions: list[OtherPosition]) -> None:
    """A.4 sub-renderer — numbered list (A.4.1, A.4.2, ...).

    Per-entry body: "{title}, {years}. {organization} ({acronym})." The
    parenthetical acronym is omitted when `acronym` is empty.
    """
    if not positions:
        emit_styled(out, "na_placeholder", "N/A", indent=720)
        return
    code = SECTION_CODES["Positions at Other Institutions"]
    indent = _hanging_indent_for_codes(
        _section_codes_up_to(code, len(positions))
    )
    for idx, p in enumerate(positions, 1):
        body = (
            f"{escape_rtf(p.title)}, {escape_rtf(p.years)}. "
            f"{escape_rtf(p.organization)}"
        )
        if p.acronym:
            body += f" ({escape_rtf(p.acronym)})"
        body += "."
        _emit_list_item(out, f"{code}.{idx}", body, indent=indent)


def _render_a5_licenses(out: IO[str], prose: str) -> None:
    """A.5 sub-renderer — single free-form paragraph (no numbering).

    Empty string → "N/A" (matches the user's source-of-truth screenshot).
    """
    text = prose.strip() if prose else ""
    if not text:
        text = "N/A"
    indent = _body_indent_for_code(SECTION_CODES["Licenses"])
    out.write(f"\\pard\\li{indent} {escape_rtf(text)}\\par\\par\n")


# A.6 table column widths (twips). Sum = 9360 = 6.5" usable on US Letter.
# Matches the user's source-of-truth doc layout: roughly Name 35%, Date 12%,
# Significance 53%.
_AWARDS_TABLE_WIDTHS: list[int] = [3360, 1100, 4900]


def index_awards(awards: list[Award]) -> list[tuple[str, Award]]:
    """Walk the awards list in tier-grouped + chrono order and yield
    `(A.6.N, award)` for each visible entry.

    Mirrors the renderer's emit order so the cli `_register` pass can
    build ref_index keys that match the rendered numbering exactly.
    Order: ALL externals (chrono asc) THEN ALL internals (chrono asc).
    Within a year, stable YAML order is preserved.
    """
    code = SECTION_CODES["Awards"]
    externals = sorted(
        [a for a in awards if a.tier == "external"], key=lambda a: a.year,
    )
    internals = sorted(
        [a for a in awards if a.tier == "internal"], key=lambda a: a.year,
    )
    out: list[tuple[str, Award]] = []
    n = 0
    for a in externals + internals:
        n += 1
        out.append((f"{code}.{n}", a))
    return out


def _render_a6_awards(out: IO[str], awards: list[Award]) -> None:
    """A.6 sub-renderer — 3-column table with EXTERNAL + INTERNAL groups.

    Columns: Name | Date | Significance. Each tier group leads with a
    bold-header divider row carrying the column titles ("EXTERNAL
    RECOGNITIONS / DATE / BRIEF DESCRIPTION OF SIGNIFICANCE"). Within a
    tier the rows are chronologically ascending by `year`; YAML order
    breaks year ties (stable sort).

    `significance` may carry @-refs (resolved upstream in
    `resolve_refs_in_list`, so the field already contains pipe-form
    sentinels by the time this renderer runs — they pass through
    verbatim and `_finalize_ref_hyperlinks` turns them into clickable
    hyperlinks during the post-pass).

    Numbering: A.6.1, A.6.2, ... runs flat across both tiers in emit
    order (externals first, then internals). Matches `index_awards`.
    """
    if not awards:
        emit_styled(out, "na_placeholder", "N/A", indent=720)
        return

    externals = sorted(
        [a for a in awards if a.tier == "external"], key=lambda a: a.year,
    )
    internals = sorted(
        [a for a in awards if a.tier == "internal"], key=lambda a: a.year,
    )

    code = SECTION_CODES["Awards"]
    table = RtfTable(_AWARDS_TABLE_WIDTHS)
    n = 0

    def _emit_group(label: str, group: list[Award]) -> None:
        nonlocal n
        # Group divider — a bold "category" row spanning the same 3 columns.
        # The DATE + SIGNIFICANCE column headers repeat per group so the
        # user can scan either group standalone (matches the screenshot).
        table.add_header([label, "DATE", "BRIEF DESCRIPTION OF SIGNIFICANCE"])
        for a in group:
            n += 1
            # Each row's first cell carries the bookmark anchor for A.6.N.
            name_cell = (
                f"{_ref_anchor(f'{code}.{n}')} {escape_rtf(a.name)}"
            )
            date_cell = escape_rtf(a.year_str or str(a.year))
            # Significance may already carry sentinel-wrapped refs from
            # resolve_refs_in_list; escape_rtf preserves them (sentinels
            # are <0x80 and not in the escape set).
            sig_cell = escape_rtf(a.significance)
            table.add_row([name_cell, date_cell, sig_cell])

    _emit_group("EXTERNAL RECOGNITIONS", externals)
    _emit_group("INTERNAL RECOGNITIONS", internals)

    out.write(table.render())
    # Trailing blank for visual spacing before A.7.
    out.write("\\pard\\par\n")


def _render_a7_memberships(
    out: IO[str], memberships: list[ProfessionalMembership],
) -> None:
    """A.7 sub-renderer — numbered list (A.7.1, A.7.2, ...).

    Per-entry body: "{level}, {organization} ({acronym})." The
    parenthetical acronym is omitted when `acronym` is empty.
    """
    if not memberships:
        emit_styled(out, "na_placeholder", "N/A", indent=720)
        return
    code = SECTION_CODES["Professional Memberships"]
    indent = _hanging_indent_for_codes(
        _section_codes_up_to(code, len(memberships))
    )
    for idx, m in enumerate(memberships, 1):
        body = (
            f"{escape_rtf(m.level)}, {escape_rtf(m.organization)}"
        )
        if m.acronym:
            body += f" ({escape_rtf(m.acronym)})"
        _emit_list_item(out, f"{code}.{idx}", body, indent=indent)


_B_SECTIONS: tuple[tuple[Section, str], ...] = (
    ("B1 Summary",         "b1"),
    ("B2 Impact",          "b2"),
    ("B3 Vision",          "b3"),
    ("B4 External Events", "b4"),
    ("B5 COVID Impact",    "b5"),
)


_MARKDOWN_EMPHASIS_RE = re.compile(r"(\*\*[^*]+\*\*)|(\*[^*]+\*)")


def _markdown_inline_to_rtf(text: str) -> str:
    """Convert markdown `**bold**` and `*italic*` inline emphasis to RTF
    while escaping the non-markdown segments. Bold is matched first
    (greedy `**`) so `**X**` doesn't fall back to two `*X*` italics.

    Single-character emphasis tokens that aren't paired (literal `*` in
    body text) survive the regex unmatched and get RTF-escaped as plain
    text. Markdown nesting (`**bold *italic* inside**`) isn't supported
    — the inner `[^*]+` blocks both patterns from spanning a nested `*`;
    fine for B-section prose which doesn't nest in practice.

    Sentinel-wrapped `\\x01…\\x02` cross-refs survive intact: they don't
    contain `*`, so the regex skips them, and they pass through
    `escape_rtf` unchanged as documented at the post-pass site.
    """
    out_parts: list[str] = []
    pos = 0
    for m in _MARKDOWN_EMPHASIS_RE.finditer(text):
        out_parts.append(escape_rtf(text[pos:m.start()]))
        bold, italic = m.group(1), m.group(2)
        # lint-allow: raw-rtf — markdown→RTF inline emit; close is implicit
        # in the `{…}` group so emit_styled paragraph plumbing doesn't fit
        if bold is not None:
            inner = bold[2:-2]
            out_parts.append(r"{\b " + escape_rtf(inner) + r"}")
        else:
            inner = italic[1:-1]
            out_parts.append(r"{\i " + escape_rtf(inner) + r"}")
        pos = m.end()
    out_parts.append(escape_rtf(text[pos:]))
    return "".join(out_parts)


def render_self_evaluation_section(out: IO[str]) -> None:
    """Emit Section IV — B.1 through B.5 statements.

    Group-level "B. SELF-EVALUATION" heading, then each B.X sub-section
    heading. Prose for each B.X auto-emits from the module-level
    `_section_prose` dict via `_emit_section_heading` — no per-section
    plumbing here. Cross-references (`@bibkey`, `@id`, `@C.X.Y`) and
    `#MACRO_NAME` substitutions are pre-applied upstream in cli.py so
    the sentinel-wrapped refs in the prose render as clickable
    hyperlinks via the post-pass.
    """
    _emit_group_heading(out, "B.", "SELF-EVALUATION")
    for section, _field in _B_SECTIONS:
        code = SECTION_CODES[section]
        _emit_section_heading(out, code, SECTION_HEADINGS[section])


def render_candidate_information_section(
    candidate_info: CandidateInformation, out: IO[str],
) -> None:
    """Emit Section III front matter — A.1 through A.7 (A.6 skipped).

    Order:
        A. GENERAL INFORMATION  (group heading, fs32)
        A.1 Identifiers          (bullet list)
        A.2 Degrees              (numbered)
        A.3 Positions at Purdue  (prose)
        A.4 Positions at other   (numbered)
        A.5 Licenses             (prose; "N/A" allowed)
        A.7 Memberships          (numbered)

    A.6 is intentionally absent — that sub-section is handled outside
    this generator. The "A.6 gap" is visible in the rendered output by
    design (Purdue template convention: skip but don't renumber).
    """
    # The III. opener anchors the front matter at the top of the document.
    # `suppress_page_break=True` keeps the heading on page 1 (default V.
    # emit pushes a page break for mid-doc section breaks).
    _emit_roman_section_heading(
        out, "III", "MATERIAL PREPARED BY THE CANDIDATE",
        suppress_page_break=True,
    )
    # FIRST heading-2 emit after the III. Roman section — restart the
    # auto-numbering so Word starts at A. instead of continuing the
    # doc-global heading-2 list (which would land on B./C./… in the
    # P&T template, where I. and II. already each contribute an A.
    # entry).
    _emit_group_heading(out, "A.", "GENERAL INFORMATION", restart_numbering=True)

    _emit_section_heading(
        out, SECTION_CODES["Identifiers"], SECTION_HEADINGS["Identifiers"],
    )
    _render_a1_identifiers(out, candidate_info.identifiers)

    _emit_section_heading(
        out, SECTION_CODES["Degrees"], SECTION_HEADINGS["Degrees"],
    )
    _render_a2_degrees(out, candidate_info.degrees)

    _emit_section_heading(
        out, SECTION_CODES["Positions at Purdue"],
        SECTION_HEADINGS["Positions at Purdue"],
    )
    _render_a3_positions_at_purdue(out, candidate_info.positions_at_purdue)

    _emit_section_heading(
        out, SECTION_CODES["Positions at Other Institutions"],
        SECTION_HEADINGS["Positions at Other Institutions"],
    )
    _render_a4_positions_at_other(out, candidate_info.positions_at_other)

    _emit_section_heading(
        out, SECTION_CODES["Licenses"], SECTION_HEADINGS["Licenses"],
    )
    _render_a5_licenses(out, candidate_info.licenses)

    _emit_section_heading(
        out, SECTION_CODES["Awards"], SECTION_HEADINGS["Awards"],
    )
    _render_a6_awards(out, candidate_info.awards)

    _emit_section_heading(
        out, SECTION_CODES["Professional Memberships"],
        SECTION_HEADINGS["Professional Memberships"],
    )
    _render_a7_memberships(out, candidate_info.professional_memberships)


def render_pending_proposals_section(
    pending_proposals: list[Grant], out: IO[str],
) -> None:
    """Section V, A.2: pending grant proposals.

    Same table shape as C.10-C.13 (one 4-row grant table per entry,
    `_format_grant_table` reused) so a reader who knows the awarded-grant
    layout can read pending proposals at a glance. Bookmarks are
    prefixed with "V." so the Section V entries don't collide with
    Section III A.2 Degrees bookmarks at the same numeric code.

    Empty list → nothing emitted (Section V, A.2 simply doesn't appear).
    """
    if not pending_proposals:
        return
    code = SECTION_CODES["Pending Proposals"]
    heading = SECTION_HEADINGS["Pending Proposals"]
    _emit_section_heading(out, code, heading)
    for idx, grant in enumerate(pending_proposals, 1):
        out.write(_format_grant_table(
            grant, idx, code, bookmark_prefix="V.",
        ))
        out.write("\\pard\\par\n")  # blank paragraph between grant tables


def render_under_review_section(
    under_review: list[UnderReview], out: IO[str],
) -> None:
    """Section V, A.1: numbered list of in-flight submissions.

    Body shape per entry: `Authors. Title. Under review: /italic Venue/,
    NN pages. [Due: YYYY-MM-DD]`. The italic venue cell is prefixed with
    "Under review: " so the in-flight status is explicit on every entry
    (helpful when reviewers scan the appendix). The "Due" suffix only
    appears when the YAML entry carries a known `due_date`; entries
    without one render without a deadline marker. Sorted by `due_date`
    ascending so near-deadline submissions surface first.

    Bookmark namespace: entries use the `V_` prefix (V_A_1_1, V_A_1_2,
    ...) so they don't collide with Section III's A.1.N Identifiers
    bookmarks (A_1_1, A_1_2, A_1_3). Section V is the appendix; the
    namespace pattern mirrors Section V A.2 Pending Proposals
    (V_A_2_N). Cross-references to these entries carry the pipe-form
    "Section V, A.1.N|V.A.1.N" so the display reads "Section V, A.1.N"
    while the hyperlink targets `V_A_1_N`.
    """
    if not under_review:
        return
    code = SECTION_CODES["Under Review"]
    heading = SECTION_HEADINGS["Under Review"]
    _emit_section_heading(out, code, heading)
    indent = _hanging_indent_for_codes(
        _section_codes_up_to(code, len(under_review))
    )
    for idx, ur in enumerate(under_review, 1):
        body = (
            f"{ur.authors_rtf}. {escape_rtf(ur.title)}. "
            f"Under review: {styled_inline('venue_italic', escape_rtf(ur.venue))}"
        )
        if ur.pages:
            body += f", {escape_rtf(ur.pages)}"
        body += "."
        # Sentinel "9999-99-99" means no known deadline → suppress the marker.
        if ur.due_date and ur.due_date != "9999-99-99":
            body += f" {styled_inline('underline_marker', f'Due: {escape_rtf(ur.due_date)}')}."
        _emit_list_item(
            out, f"{code}.{idx}", body, indent=indent, bookmark_prefix="V.",
        )


# C.5 "Other publications and products" subcategory map. Each Citation's
# `rank` routes to a subcategory subheading; subheadings render in canonical
# order; numbering is flat (`C.5.N`) across all subcategories so each
# entry's back-pointer code is stable. arXiv preprints are technical reports.
_C5_SUBCATEGORY_BY_RANK: dict[str, str] = {
    "Magazine": "Magazine",
    "Preprint": "Technical Reports",
    "CVE": "Direct computing industry impacts",
    "Disclosure": "Direct computing industry impacts",
}

# Subcategories now own their own numeric slot under C.5: Magazine →
# C.5.1, Technical Reports → C.5.2, Direct computing industry impacts
# → C.5.3. Entries within get C.5.{subcat_idx}.{within_idx} so cross-
# references encode the subcategory directly in the code.
_C5_SUBCATEGORY_ORDER: tuple[str, ...] = (
    "Magazine",
    "Technical Reports",
    "Direct computing industry impacts",
)
_C5_SUBCATEGORY_IDX: dict[str, int] = {
    name: i + 1 for i, name in enumerate(_C5_SUBCATEGORY_ORDER)
}


def render_other_pubs_section(
    citations: list[Citation],
    paper_index: dict[str, str],
    key_work_index: dict[str, str],
    out: IO[str],
) -> None:
    """C.5: subcategory-grouped numbered list.

    Walks the pre-ordered citation list (from
    `order_citations_for_emission`) and emits a subcategory subheading
    each time the running subcategory changes. Numbering is FLAT across
    all subcategories (`C.5.1`, `C.5.2`, ...). The ordering itself is
    NOT defined here — the single source of truth is
    `order_citations_for_emission`, also used by `build_paper_index` to
    guarantee paper_index codes match the rendered bookmarks.
    """
    if not citations:
        return
    code = SECTION_CODES["Other publications and products"]
    heading = SECTION_HEADINGS["Other publications and products"]
    _emit_section_heading(out, code, heading)

    ordered = order_citations_for_emission(
        "Other publications and products", citations,
    )
    # Pre-compute the hanging-indent baseline against the longest code
    # this section will emit (C.5.3.N where N is the entry count of the
    # largest subcategory). Using a representative wide code keeps the
    # gutter consistent across subcategory boundaries.
    indent = _hanging_indent_for_codes(
        [f"{code}.{i}.{j}" for i in (1, 2, 3) for j in (1, len(ordered) or 1)]
    )
    expansion_done: set[str] = set()
    prev_subcat: Optional[str] = None
    # Career-phase dividers are tracked PER SUBCATEGORY (Magazine /
    # Technical Reports / Direct computing industry impacts) — when a new
    # subcategory begins, the phase counter resets so the divider can
    # fire again at the within-subcategory PhD→AP boundary.
    phase = ""
    within_idx = 0
    for cit in ordered:
        subcat = _C5_SUBCATEGORY_BY_RANK.get(cit.rank, "Other")
        if subcat != prev_subcat:
            # New subcategory → emit a sub-section heading (e.g.
            # "C.5.1 Magazine") instead of an inline label, so the
            # subcategory itself is a numbered cross-ref target and
            # entries underneath read as C.5.{subcat}.{within}.
            subcat_idx = _C5_SUBCATEGORY_IDX.get(subcat, 0)
            _emit_section_heading(out, f"{code}.{subcat_idx}", subcat)
            prev_subcat = subcat
            phase = ""        # reset; first entry of new subcategory re-fires divider
            within_idx = 0    # restart within-subcategory entry counter
        subcat_idx = _C5_SUBCATEGORY_IDX.get(subcat, 0)
        phase = _maybe_emit_career_phase_divider(
            out, cit.year, phase, indent,
        )
        within_idx += 1
        body = render_citation(
            cit, expansion_done, paper_index, key_work_index,
        )
        _emit_list_item(
            out, f"{code}.{subcat_idx}.{within_idx}", body, indent=indent,
        )


def render_invited_talk(talk: InvitedTalk) -> str:
    """Format per spec: 'Seminar on {topic}[: {subtitle}]. {venue}, {year_str}.'"""
    if talk.topic and talk.subtitle:
        head = f"Seminar on {escape_rtf(talk.topic)}: {escape_rtf(talk.subtitle)}"
    elif talk.topic:
        head = f"Seminar on {escape_rtf(talk.topic)}"
    elif talk.subtitle:
        head = f"Seminar: {escape_rtf(talk.subtitle)}"
    else:
        head = "Seminar"
    return f"{head}. {escape_rtf(talk.venue)}, {escape_rtf(talk.year_str)}."


def render_invited_talks_section(talks: list[InvitedTalk], out: IO[str]) -> None:
    if not talks:
        return
    code = SECTION_CODES["Invited Talks"]
    heading = SECTION_HEADINGS["Invited Talks"]
    _emit_section_heading(out, code, heading)
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(talks)))
    for idx, talk in enumerate(talks, 1):
        _emit_list_item(out, f"{code}.{idx}", render_invited_talk(talk), indent=indent)


def render_leadership_role(role: LeadershipRole) -> str:
    """C.7 format: 'Role, Description. Society. Year.'

    The society is the affiliated professional org (ACM SIGSOFT, IEEE, ASEE)
    and is rendered with the underlined `Society:` prefix to mirror the
    tier-marker styling on citations.
    """
    body = f"{escape_rtf(role.role)}, {escape_rtf(role.description)}, {escape_rtf(role.year_str)}."
    body += f" {styled_inline('underline_marker', f'Society: {escape_rtf(role.society)}')}."
    return body


def render_leadership_section(roles: list[LeadershipRole], out: IO[str]) -> None:
    if not roles:
        return
    code = SECTION_CODES["Leadership Roles"]
    heading = SECTION_HEADINGS["Leadership Roles"]
    _emit_section_heading(out, code, heading)
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(roles)))
    for idx, role in enumerate(roles, 1):
        _emit_list_item(out, f"{code}.{idx}", render_leadership_role(role), indent=indent)


_CONF_PRES_NOTE = (
    "In Dr. Davis's field, conferences are the primary publication venue. "
    "Typically, the lead author gives the talk as part of their professional development."
)


def _bib_entry_by_title_local(
    bib_entries: list[BibEntry], paper_title: str,
) -> Optional[BibEntry]:
    """Local helper (mirrors builders._bib_entry_by_title)."""
    from .venue import normalize_title  # local: avoids widening top-level imports
    target = normalize_title(paper_title)
    for e in bib_entries:
        if normalize_title(e.get("title", "")) == target:
            return e
    return None


def render_conference_presentation(
    pres: ConferencePresentation,
    bib_entries: list[BibEntry],
    paper_index: dict[str, str],
) -> str:
    """C.9: 'Talk at {venue} in {year}. Associated with publication {C.X.Y}.'"""
    from .venue import normalize_title
    paper = _bib_entry_by_title_local(bib_entries, pres.paper_title)
    if not paper:
        return f"[unresolved paper: {escape_rtf(pres.paper_title)}]"
    venue_raw = paper.get("booktitle") or paper.get("journal") or ""
    _, venue_clean = parse_venue(decode_latex(venue_raw))
    year = paper.get("year", "")
    ref = paper_index.get(normalize_title(pres.paper_title), "?")
    # Brace-scope the italic so the close-brace ends `\i` AND emits a
    # literal space — bare `\i0 in 2019` reads as "Engineeringin 2019"
    # because the space after `\i0` is consumed as the control-word
    # delimiter (same class as the `\b0 $X` bug we hit on grant totals).
    return (
        f"Talk at {{{styled_inline('venue_italic', escape_rtf(venue_clean))}}} in {escape_rtf(year)}. "
        f"Associated with publication {_code_link(ref)}."
    )


def render_conference_presentations_section(
    presentations: list[ConferencePresentation],
    bib_entries: list[BibEntry],
    paper_index: dict[str, str],
    out: IO[str],
) -> None:
    if not presentations:
        return
    code = SECTION_CODES["Conference Presentations"]
    heading = SECTION_HEADINGS["Conference Presentations"]
    _emit_section_heading(out, code, heading)
    # Explanatory note routed through `_emit_intro_note` → italic
    # intro-note style in the registry.
    _emit_intro_note(out, escape_rtf(_CONF_PRES_NOTE))
    # Sort by linked-paper year for chronological order
    from .venue import normalize_title
    def _year(p: ConferencePresentation) -> int:
        from .builders import parse_year
        bib = _bib_entry_by_title_local(bib_entries, p.paper_title)
        return parse_year(bib.get("year", "") if bib else "")
    sorted_pres = sorted(presentations, key=_year)
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(sorted_pres)))
    for idx, p in enumerate(sorted_pres, 1):
        _emit_list_item(
            out, f"{code}.{idx}",
            render_conference_presentation(p, bib_entries, paper_index),
            indent=indent,
        )


def render_media_appearance(media: MediaAppearance) -> str:
    """C.8 format: 'Title. Venue. Year. URL:...'"""
    body = f"{escape_rtf(media.title)}. {styled_inline('venue_italic', escape_rtf(media.venue))}, {escape_rtf(media.year_str)}."
    body += render_link_field(media.url)
    return body


def render_media_appearances_section(media: list[MediaAppearance], out: IO[str]) -> None:
    if not media:
        return
    code = SECTION_CODES["Media Appearances"]
    heading = SECTION_HEADINGS["Media Appearances"]
    _emit_section_heading(out, code, heading)
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(media)))
    for idx, m in enumerate(media, 1):
        _emit_list_item(out, f"{code}.{idx}", render_media_appearance(m), indent=indent)


def _format_usd(amount: int) -> str:
    """Render integer USD as '$NNN,NNN' with thousands separators."""
    return f"${amount:,}"


_GRANT_TABLE_TOTAL_TWIPS = 9360       # 6.5" usable width on US Letter.
_GRANT_TABLE_SPLIT_TWIPS = 6000       # date column ends here; amount starts.

# Single-line border on every side of every cell, 15-twip stroke.
# Alias to the canonical block defined near the top of this module so
# every table in the document shares one source of truth.
_GRANT_CELL_BORDER = _CELL_BORDER_BLOCK


_NSF_AWARD_URL_TEMPLATE = "https://www.nsf.gov/awardsearch/showAward?AWD_ID={}"


def _format_grant_amounts(grant: Grant) -> str:
    """Render the Row 2 amount cell — the TOTAL award amount, full stop.

    Purdue tenure template Row 2 shows a single dollar figure (the total
    award across all institutions). The per-recipient share is captured
    indirectly by the Row 3 responsibility percent (computed in
    `_format_role_responsibility_line` from `my_amount / total_amount`).

    Multi-institution NSF Collab awards still show the full total here;
    Davis's credited share is conveyed via the percentage on the role
    line below.
    """
    return _format_usd(grant.total_amount)


def _compute_responsibility_pct(grant: Grant) -> Optional[int]:
    """Davis's credited share as a percentage of the total award.

    Returns `round(my_amount / total_amount * 100)` when both are
    positive; `None` when amounts aren't populated (e.g., gifts with no
    dollar split). Sole-PI single-institution grants where
    `my_amount == total_amount` return 100.
    """
    if grant.total_amount > 0 and grant.my_amount > 0:
        return round(grant.my_amount / grant.total_amount * 100)
    return None


def _format_role_responsibility_line(grant: Grant) -> str:
    """Row 3: "{role} - {pct}%, [Purdue is (not) lead institution.] {responsibility prose}".

    Format rules:
      * `{role}` alone if no pct and no responsibility text.
      * `{role}, {responsibility}` for gifts (no computable pct).
      * `{role} - {pct}%, {responsibility}` for grants with a credited
        share. When pct == 100 (sole PI single-institution), the
        `- 100%` is suppressed since 100% is the implicit default and
        the line reads better as `PI, {responsibility}`.

    Lead-institution annotation is inlined ONLY for pending proposals
    (`status == "pending"`, routes to Section V, A.2): a reader of the
    V.A.2 appendix can't see at-a-glance whether the proposal is a
    Purdue-led submission or a multi-institution collab with Purdue as
    a sub-site. For C.10 / C.11 / C.12 / C.13 the question is conveyed
    by the section the grant lands in, so the annotation is suppressed
    there to keep the rendered row short.

    Detection: empty `lead_institution` OR the literal "Purdue
    University" → Purdue is lead; any other non-empty string → Purdue
    is not lead (and we name the lead by the field's value).
    """
    role = grant.role
    pct = _compute_responsibility_pct(grant)
    description = grant.responsibility or grant.activities or ""
    bits = [role]
    if pct is not None and pct != 100:
        bits.append(f" - {pct}%")
    if grant.status == "pending":
        lead = (grant.lead_institution or "").strip()
        if not lead or lead == "Purdue University":
            bits.append(", Purdue is lead institution")
        else:
            bits.append(f", Purdue is not lead institution (lead: {lead})")
    if description:
        bits.append(f", {description}")
    return "".join(bits)


def _format_grant_number_field(grant: Grant) -> str:
    """Row 1 grant_number field. NSF grants render as an RTF hyperlink to
    the official award page; other agencies render as plain text. Returns
    "" when grant has no `grant_number` (caller skips the field).
    """
    if not grant.grant_number:
        return ""
    display = escape_rtf(grant.grant_number)
    if grant.agency_short == "NSF":
        url = _NSF_AWARD_URL_TEMPLATE.format(grant.grant_number)
        return (
            f"{{\\field{{\\*\\fldinst HYPERLINK \"{url}\"}}"
            f"{{\\fldrslt {display}}}}}"
        )
    return display


def _format_grant_person(p: "GrantPerson") -> str:
    """Render one personnel record as the renderer-agnostic display string.

    Format (matches the Purdue tenure template's Row 4 personnel cell):
      * Purdue person      → "Role: Name, Department"
      * External person    → "Role: Name, Department, Institution"
      * NSF cross-inst PI  → "Role: Name, Department, Institution (NSF #X)"
      * Free-form note     → role="" + only name set → just the name
                             verbatim (escape hatch for cases like the
                             Qualcomm fellowship's "Project Supervisor of
                             winning team: ..." annotation).

    Department / institution / NSF-award components are omitted when
    empty. NSF award number moves to a trailing parenthetical so the
    `Role: Name` prefix is uniform across Purdue and external Co-PIs
    (template convention).
    """
    parts: list[str] = []
    if p.role:
        parts.append(f"{p.role}: ")
    parts.append(p.name)
    if p.department:
        parts.append(f", {p.department}")
    if p.institution:
        parts.append(f", {p.institution}")
    if p.nsf_award:
        parts.append(f" (NSF #{p.nsf_award})")
    return "".join(parts)


def _format_personnel_line(grant: Grant) -> str:
    """Row 4 content. Sole-PI grants (empty `personnel`) render "Sole PI";
    everything else is `; `-joined per-person records via `_format_grant_person`.
    """
    if not grant.personnel:
        return "Sole PI"
    return "; ".join(_format_grant_person(p) for p in grant.personnel)


def _format_grant_table(
    grant: Grant, idx: int, section_code: str,
    bookmark_prefix: str = "",
) -> str:
    """Render one grant as a 4-row RTF table matching the Purdue CV format.

    Layout (each row in its own `\\trowd`, borders on all sides):
      Row 1 (full width):  "{N}. [{grant_number-link}] {agency} / {title}"
      Row 2 (split):       "{start}-{end}."     |     "${amount}"  (right-aligned)
      Row 3 (full width):  "{role}[ (lead-institution annotation)][, {responsibility|activities}]"
      Row 4 (full width):  "{personnel}"   ("Sole PI" when grant.personnel is empty)

    `bookmark_prefix` namespaces the bookmark for cross-ref targeting
    without changing the displayed code. Used for Section V, A.2 pending
    proposals where the displayed code is "A.2.N" but the bookmark must
    be "V_A_2_N" to avoid colliding with Section III A.2 Degrees entries.
    """
    # --- Row 1: numbered head ---
    # Plain (non-bold) C.X.Y code — readers find their place via the
    # section heading + table boundaries, and removing bold here keeps
    # the cell content visually uniform with the rest of the row.
    # Brace-scope the trailing period so its space survives RTF's
    # control-word-delimiter eat-the-space rule. Emit the full
    # `{section_code}.{idx}` (e.g. "C.10.1") so every grant row has a
    # parseable cross-ref code matching `@id` references elsewhere.
    code = f"{section_code}.{idx}"
    head_bits: list[str] = [
        f"{_ref_anchor(code, bookmark_prefix)}. ",
    ]
    gn_field = _format_grant_number_field(grant)
    if gn_field:
        head_bits.append(f"{gn_field} ")
    head_bits.append(escape_rtf(grant.agency))
    head_bits.append(" / ")
    head_bits.append(escape_rtf(grant.title))
    head = "".join(head_bits)

    # --- Row 2: duration + amount ---
    # En-dash (U+2013) is the conventional separator for date ranges; escape_rtf
    # converts it to the cp1252-safe 舑? form.
    duration = escape_rtf(f"{grant.start_year}–{grant.end_year}.")
    amount = escape_rtf(_format_grant_amounts(grant))

    # --- Row 3: "{role} - {pct}%, {responsibility prose}" ---
    # Lead-institution annotation deliberately elided — the C.X section
    # the grant lands in (C.10 PI vs C.11 Co-PI etc.) already conveys it.
    role_line = escape_rtf(_format_role_responsibility_line(grant))

    # --- Row 4: personnel line (always emitted; "Sole PI" when none) ---
    personnel = escape_rtf(_format_personnel_line(grant))

    # Every cell's content paragraph starts with `\pard\intbl` per the RTF
    # spec ("every paragraph in a table row must have \intbl specified
    # or inherited"). Cell-specific alignment like `\qr` (right-align)
    # comes AFTER \intbl on the same paragraph.
    out: list[str] = []
    out.append(
        f"\\trowd\\trgaph108\\trleft0 "
        f"{_GRANT_CELL_BORDER}\\cellx{_GRANT_TABLE_TOTAL_TWIPS}\n"
        f"\\pard\\intbl {head}\\cell\\row\n"
    )
    out.append(
        f"\\trowd\\trgaph108\\trleft0 "
        f"{_GRANT_CELL_BORDER}\\cellx{_GRANT_TABLE_SPLIT_TWIPS}"
        f"{_GRANT_CELL_BORDER}\\cellx{_GRANT_TABLE_TOTAL_TWIPS}\n"
        f"\\pard\\intbl {duration}\\cell"
        f"\\pard\\intbl\\qr {amount}\\cell\\row\n"
    )
    out.append(
        f"\\trowd\\trgaph108\\trleft0 "
        f"{_GRANT_CELL_BORDER}\\cellx{_GRANT_TABLE_TOTAL_TWIPS}\n"
        f"\\pard\\intbl {role_line}\\cell\\row\n"
    )
    out.append(
        f"\\trowd\\trgaph108\\trleft0 "
        f"{_GRANT_CELL_BORDER}\\cellx{_GRANT_TABLE_TOTAL_TWIPS}\n"
        f"\\pard\\intbl {personnel}\\cell\\row\n"
    )
    return "".join(out)


def render_grants_section(
    section: Section,
    grants: list[Grant],
    out: IO[str],
) -> None:
    """Generic renderer for any of C.10 / C.11 / C.12 / C.13.

    Emits: section heading + optional `Total amount of ...: $X` line (per
    GRANT_TOTAL_LABELS) + one 4-row table per grant (see _format_grant_table
    for the row layout). Tables are separated by blank paragraphs so Word
    paste preserves spacing.

    `grant.inspired_by` / `grant.publication_outcomes` are validated +
    available on the Grant record but NOT emitted here. The intended consumer
    is the C.1 Key Works section (paper → originating grant connections
    render with the highlighted papers); cross-link rendering is deferred
    until that linkage shape is defined.
    """
    if not grants:
        return
    from .config import GRANT_TOTAL_LABELS  # local: limit import surface
    code = SECTION_CODES[section]
    heading = SECTION_HEADINGS[section]
    _emit_section_heading(out, code, heading)

    total_label = GRANT_TOTAL_LABELS.get(section)
    if total_label:
        # Section total sums `my_amount` — the tenure-credited share.
        # Emitted as an italic "introductory note" (see _emit_intro_note)
        # so it reads as orienting metadata for the grant tables that
        # follow, not as content competing with the entries.
        total_amount = sum(g.my_amount for g in grants)
        _emit_intro_note(
            out,
            f"{escape_rtf(total_label)}: "
            f"{escape_rtf(_format_usd(total_amount))}",
        )

    section_code = SECTION_CODES[section]
    for idx, grant in enumerate(grants, 1):
        out.write(_format_grant_table(grant, idx, section_code))
        out.write("\\pard\\par\n")  # blank paragraph between grant tables


def _author_matches_any(
    bib_author: str, candidates: list[tuple[str, str]],
) -> bool:
    """Structural match: bib author against any (last_norm, initials) candidate.

    Candidate side carries the canonical-name + alias forms pre-parsed.
    Match semantics same as `authors.lookup_student_type`: last name
    equal, bib initials a prefix of candidate's initials.
    """
    from .latex import decode_latex as _decode
    decoded = _decode(bib_author.strip())
    b_last, b_firsts = parse_name_parts(decoded)
    b_last_norm = b_last.lower()
    b_initials = "".join(f[0].upper() for f in b_firsts if f)
    for c_last_norm, c_initials in candidates:
        if b_last_norm != c_last_norm:
            continue
        if not b_initials or c_initials.startswith(b_initials):
            return True
    return False


def _build_name_candidates(names: list[str]) -> list[tuple[str, str]]:
    """Pre-parse `(last_norm, initials)` tuples for a list of name strings.

    The C.14 column scans every bib entry's authors against the
    canonical name + aliases of one student; pre-parsing the candidates
    once per student amortizes the parse cost across N bib entries.
    """
    out: list[tuple[str, str]] = []
    for n in names:
        if not n:
            continue
        last, firsts = parse_name_parts(n)
        out.append((last.lower(), "".join(f[0].upper() for f in firsts if f)))
    return out


def _student_pub_refs(
    student_name: str,
    bib_entries: list[BibEntry],
    paper_index: dict[str, str],
    *,
    aliases: tuple[str, ...] = (),
    under_review: Optional[list["UnderReview"]] = None,
    under_review_index: Optional[dict[int, str]] = None,
) -> list[str]:
    """Find all C.X.Y / Section V, A.1.N refs for papers this student co-authored.

    Scans the bib (C.2/C.3/C.4/C.5 sourced) AND the Section V, A.1 under-review
    list (when supplied). Match semantics: structural — last-name equality +
    canonical-initials.startswith(bib-initials), as in
    `authors.lookup_student_type`. Aliases extend the candidate set so
    students whose bib forms use different last names (accent fold,
    short form) are matched once per alias.

    Output is sorted: C.* refs first (by section + idx), then A.1 refs.
    Each ref may be either a bare code ("C.4.7") or pipe-form
    ("display|bookmark") — the sort uses the bookmark portion as its key.
    """
    from .venue import normalize_title
    candidates = _build_name_candidates([student_name, *aliases])
    refs: list[str] = []
    for entry in bib_entries:
        raw = entry.get("author", "")
        for author in raw.split(" and "):
            if _author_matches_any(author, candidates):
                title = entry.get("title", "")
                ref = paper_index.get(normalize_title(title)) if title else None
                if ref:
                    refs.append(ref)
                break  # this author matched; don't scan other authors of same paper
    if under_review and under_review_index is not None:
        for i, ur in enumerate(under_review):
            ref = under_review_index.get(i)
            if not ref:
                continue
            for author in ur.raw_authors:
                if _author_matches_any(author, candidates):
                    refs.append(ref)
                    break
    def _key(r: str) -> tuple[int, ...]:
        # C.X.Y refs sort first (prefix "0"), A.1.N refs after (prefix "1").
        # Pipe-form values store "display|bookmark"; key off the bookmark.
        # Section V's under-review entries now namespace their bookmark
        # with a leading "V." (V.A.1.N) so they don't collide with
        # Section III's A.1 Identifiers entries; strip the prefix here.
        bookmark = r.split("|", 1)[1] if "|" in r else r
        prefix = 0 if bookmark.startswith("C.") else 1
        body = (
            bookmark
            .removeprefix("V.")
            .removeprefix("C.")
            .removeprefix("A.")
        )
        return (prefix, *(int(x) for x in body.split(".")))
    return sorted(set(refs), key=_key)


# Column widths for the student tables (twips). Sum = 9360 = 6.5" usable.
_STUDENT_TABLE_WIDTHS: list[int] = [1800, 1100, 1400, 1000, 1900, 2160]

# Single-line border on every side, same stroke as the grant tables.
_STUDENT_CELL_BORDER = _GRANT_CELL_BORDER

# Purdue's mandated C.14 subsection order. Sort students by (tier, grad_year),
# then emit a short grey divider row at each tier transition so the reader
# can scan by group at a glance.
_STUDENT_TIER_LABELS: dict[int, str] = {
    1: "PhD students — Committee Chair",
    2: "PhD students — Committee Co-Chair",
    3: "D.Eng students",
    4: "MS Thesis students — Committee Chair",
    5: "MS Thesis students — Committee Co-Chair",
    6: "MS Non-Thesis students",
    7: "Other supervision and mentoring (committee member, etc.)",
}


def _student_tier(s: Student) -> int:
    """Map a Student's (degree, role) to its Purdue subsection tier (1-7).

    The mandated order: 1=PhD Chair, 2=PhD Co-Chair, 3=D.Eng,
    4=MS Thesis Chair, 5=MS Thesis Co-Chair, 6=MS Non-Thesis,
    7=other supervision (Committee member). Unrecognized degree → tier 7
    (drops into "other supervision" rather than mis-sorting).
    """
    role = s.role
    deg = s.degree
    if role == "Committee member":
        return 7
    if deg == "D.Eng":
        return 3
    if deg == "PhD":
        return 1 if role == "Chair" else 2
    if deg == "MS Thesis":
        return 4 if role == "Chair" else 5
    if deg == "MS Non-Thesis":
        return 6
    return 7


def _student_cellx_positions() -> list[int]:
    """Cumulative cellx positions for the 6-column student table."""
    positions: list[int] = []
    cumulative = 0
    for w in _STUDENT_TABLE_WIDTHS:
        cumulative += w
        positions.append(cumulative)
    return positions


def _render_student_header_row(cellx: list[int]) -> str:
    """Bold header row: column titles across the full 6-column width.

    Each cell's content paragraph is prefixed with `\\pard\\intbl` so the
    table renders as a real table in TextEdit + Word (without it, the
    \\cell separators are ignored and the row collapses to inline text).
    """
    parts = ["\\trowd\\trgaph108\\trleft0"]
    for pos in cellx:
        parts.append(f"{_STUDENT_CELL_BORDER}\\cellx{pos}")
    parts.append("\n")
    headers = [
        "Student Name", "Degree And Type", "Graduation Semester",
        "Role", "Related Publications", "Current Position and Affiliation",
    ]
    for h in headers:
        parts.append(f"\\pard\\intbl \\b {escape_rtf(h)}\\b0\\cell")
    parts.append("\\row\n")
    return "".join(parts)


def _render_student_tier_divider(tier: int, cellx: list[int]) -> str:
    """Grey-background row separating tier groups within C.14.

    Renders as a horizontal cell-MERGE across all 6 columns: the first
    cell gets `\\clmgf` (merge first) + the row's text content, each
    following cell gets `\\clmrg` (merge continuation) and an empty
    `\\cell`. Critical that the divider row's cellx positions MATCH the
    data-row cellx positions exactly — when consecutive rows share the
    same column structure, Word/TextEdit treat them as one table; when
    they differ (the old "single wide cellx9360" form), some viewers
    visually collapse the divider into the next data row, producing
    "PhD students — Committee Chair Wenxin JiangPhD" as a single line.
    """
    label = _STUDENT_TIER_LABELS[tier]
    parts: list[str] = ["\\trowd\\trgaph108\\trleft0"]
    for i, pos in enumerate(cellx):
        # First cell starts the merge (\clmgf); rest continue (\clmrg).
        merge = "\\clmgf" if i == 0 else "\\clmrg"
        parts.append(
            f"{_STUDENT_CELL_BORDER}\\clcbpat2{merge}\\cellx{pos}"
        )
    parts.append("\n")
    parts.append(f"\\pard\\intbl \\b {escape_rtf(label)}\\b0\\cell")
    # Empty cells for the continuation portion of the merge — every cellx
    # needs a matching \cell or Word will mis-parse the row. Each continues
    # the in-table paragraph property via \intbl.
    parts.extend("\\pard\\intbl\\cell" for _ in cellx[1:])
    parts.append("\\row\n")
    return "".join(parts)


def _render_student_data_row(
    s: Student,
    bib_entries: list[BibEntry],
    paper_index: dict[str, str],
    cellx: list[int],
    under_review: Optional[list[UnderReview]] = None,
    under_review_index: Optional[dict[int, str]] = None,
) -> str:
    """One 6-cell data row for a student."""
    pubs = _student_pub_refs(
        s.name, bib_entries, paper_index,
        aliases=s.aliases,
        under_review=under_review,
        under_review_index=under_review_index,
    )
    # Wrap each ref in `_code_link` sentinels so the post-write pass
    # converts them to clickable styled hyperlinks (same treatment as
    # cross-refs elsewhere in the doc). Sentinels survive `escape_rtf`
    # unchanged; `_finalize_ref_hyperlinks` substitutes them at write time.
    pubs_cell = ", ".join(_code_link(r) for r in pubs) if pubs else ""
    role_cell = (
        f"{s.role} (with {s.co_advisor})" if s.co_advisor else s.role
    )
    # Ongoing-student affiliation default: an empty `position` field on an
    # in-flight student (grad_year >= a sentinel "current" boundary) means
    # they're currently advised at Purdue. The "Current Position and
    # Affiliation" column reads as a data gap if left blank; the Purdue
    # default conveys the affiliation explicitly without requiring the
    # author to repeat "Purdue University" on every in-flight row.
    if not s.position and s.grad_year >= 2025:
        position_text = "Purdue University (in progress)"
    else:
        position_text = s.position
    # The position cell is a hybrid: position text (manually maintained)
    # plus an optional clickable "LinkedIn" link the reader can use to
    # verify freshness. Runtime LinkedIn scraping is NOT supported (their
    # ToS + 999/login-wall make it unworkable); periodic manual refresh
    # via the LinkedIn link IS the freshness path.
    if s.linkedin:
        linkedin_link = (
            f'{{\\field{{\\*\\fldinst HYPERLINK "{s.linkedin}"}}'
            f'{{\\fldrslt {{\\cs1\\cf1\\ul LinkedIn}}}}}}'
        )
        # The link is pre-rendered RTF — skip escape_rtf on this cell so
        # the HYPERLINK markup survives.
        position_cell_rtf = (
            f"{escape_rtf(position_text)} ({linkedin_link})"
            if position_text else linkedin_link
        )
    else:
        position_cell_rtf = escape_rtf(position_text)
    cells = [
        escape_rtf(s.name),
        escape_rtf(s.degree),
        escape_rtf(s.grad_display),
        escape_rtf(role_cell),
        escape_rtf(pubs_cell),
        position_cell_rtf,  # already-escaped + may contain a HYPERLINK field
    ]
    parts = ["\\trowd\\trgaph108\\trleft0"]
    for pos in cellx:
        parts.append(f"{_STUDENT_CELL_BORDER}\\cellx{pos}")
    parts.append("\n")
    for c in cells:
        parts.append(f"\\pard\\intbl {c}\\cell")
    parts.append("\\row\n")
    return "".join(parts)


def render_students_section(
    section: Section,
    students: list[Student],
    bib_entries: list[BibEntry],
    paper_index: dict[str, str],
    out: IO[str],
    under_review: Optional[list[UnderReview]] = None,
    under_review_index: Optional[dict[int, str]] = None,
    *,
    suppress_heading: bool = False,
) -> None:
    """C.14 / C.16 student-table renderer.

    For C.14 specifically: sorts by (Purdue tier, grad_year) so the table
    matches the mandated subsection order (PhD Chair → Co-Chair → D.Eng
    → MS Thesis Chair → Co-Chair → MS Non-Thesis → committee), and emits
    a short grey divider row at each tier transition. C.16 has no tier
    concept; the sort still applies but typically collapses to one tier.

    `suppress_heading=True` skips this renderer's own
    `_emit_section_heading` call — used by the C.16 call site which
    emits the C.16 base heading unconditionally (the section is the
    umbrella for the mentoring outline, not just the optional student
    table).
    """
    if not students:
        return
    code = SECTION_CODES[section]
    heading = SECTION_HEADINGS[section]
    if not suppress_heading:
        _emit_section_heading(out, code, heading)

    cellx = _student_cellx_positions()
    out.write(_render_student_header_row(cellx))

    sorted_students = sorted(
        students, key=lambda s: (_student_tier(s), s.grad_year),
    )
    prev_tier: Optional[int] = None
    for s in sorted_students:
        tier = _student_tier(s)
        if tier != prev_tier:
            out.write(_render_student_tier_divider(tier, cellx))
            prev_tier = tier
        out.write(_render_student_data_row(
            s, bib_entries, paper_index, cellx,
            under_review=under_review,
            under_review_index=under_review_index,
        ))
    out.write("\\pard\\par\n")


# ----- C.15: Postdocs + visiting scholars --------------------------------


_POSTDOC_TABLE_WIDTHS: list[int] = [1500, 1200, 1300, 1500, 1500, 2360]


def render_postdocs_section(
    postdocs: list[PostdocVisiting],
    bib_entries: list[BibEntry],
    paper_index: dict[str, str],
    out: IO[str],
    under_review: Optional[list[UnderReview]] = None,
    under_review_index: Optional[dict[int, str]] = None,
) -> None:
    """C.15 renderer. Empty list → section heading + indented "N/A" so the
    section still appears in the packet (Purdue convention) rather than
    being silently skipped like other empty sections.
    """
    code = SECTION_CODES["Postdocs and Visiting Scholars"]
    heading = SECTION_HEADINGS["Postdocs and Visiting Scholars"]
    _emit_section_heading(out, code, heading)

    if not postdocs:
        emit_styled(out, "na_placeholder", "N/A", indent=720)
        return

    # Cumulative cellx for the 6-column postdoc table.
    cellx: list[int] = []
    cumulative = 0
    for w in _POSTDOC_TABLE_WIDTHS:
        cumulative += w
        cellx.append(cumulative)

    # Header row.
    parts: list[str] = ["\\trowd\\trgaph108\\trleft0"]
    for pos in cellx:
        parts.append(f"{_STUDENT_CELL_BORDER}\\cellx{pos}")
    parts.append("\n")
    for h in (
        "Name", "Last Degree/Date", "Prior Affiliation",
        "Position Title/Dates", "Related Publications",
        "Current Position and Affiliation",
    ):
        parts.append(f"\\pard\\intbl \\b {escape_rtf(h)}\\b0\\cell")
    parts.append("\\row\n")
    out.write("".join(parts))

    for p in sorted(postdocs, key=lambda x: x.year):
        pubs = _student_pub_refs(
            p.name, bib_entries, paper_index,
            under_review=under_review,
            under_review_index=under_review_index,
        )
        pubs_cell = ", ".join(_code_link(r) for r in pubs) if pubs else ""
        cells = [
            escape_rtf(p.name),
            escape_rtf(p.last_degree_date),
            escape_rtf(p.prior_affiliation),
            escape_rtf(p.position_title_dates),
            escape_rtf(pubs_cell),
            escape_rtf(p.current_position),
        ]
        parts = ["\\trowd\\trgaph108\\trleft0"]
        for pos in cellx:
            parts.append(f"{_STUDENT_CELL_BORDER}\\cellx{pos}")
        parts.append("\n")
        for c in cells:
            parts.append(f"\\pard\\intbl {c}\\cell")
        parts.append("\\row\n")
        out.write("".join(parts))
    out.write("\\pard\\par\n")


def _section_intro(section: "Section") -> str:
    """Per-section intro paragraph emitted between the section heading
    and its content. Returns "" when the section has no intro (most
    cases). Hardcoded for now — additions go directly to this dict,
    and the consuming renderer needs to call this helper + emit the
    returned text if non-empty.

    Current consumers: render_service_section (C.23-C.26),
    render_conference_presentations_section (C.9).
    """
    intros: dict[str, str] = {
        "Profession Service": (
            "Davis's primary service to professional societies has been "
            "through peer review. Following is a selected list. See also "
            f"leadership roles in {_code_link('C.7')}."
        ),
        # C.9 Conference Presentations has its own dedicated constant
        # (`_CONF_PRES_NOTE`) emitted directly by its renderer; not
        # routed through this dict.
    }
    return intros.get(section, "")


def render_service_section(
    section: Section,
    entries: list[ServiceEntry],
    out: IO[str],
) -> None:
    """Generic renderer for C.23 / C.24 / C.25 / C.26.

    Hanging-indent numbered list: `C.X.Y\\tab description. year.`
    When year_str is empty (ongoing service with no fixed date — typically
    journal reviewing) the trailing year + period is suppressed.

    Sections may have an opt-in intro paragraph via
    `_section_intro(section)` — emitted between the heading and
    the numbered list. Currently only C.24 (Profession Service) has one.
    """
    if not entries:
        return
    code = SECTION_CODES[section]
    heading = SECTION_HEADINGS[section]
    _emit_section_heading(out, code, heading)
    intro = _section_intro(section)
    if intro:
        # Route through `_emit_intro_note` so the C.24 peer-review
        # framing reads the same as C.9 / C.22 / grant totals.
        _emit_intro_note(out, intro)
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(entries)))
    for idx, entry in enumerate(entries, 1):
        body = escape_rtf(entry.description)
        if entry.year_str:
            body += f". {escape_rtf(entry.year_str)}"
        body += "."
        _emit_list_item(out, f"{code}.{idx}", body, indent=indent)


# Canonical tier order — entries with tier strings outside this tuple sort
# alphabetically AFTER these two. Keep this list tight; new tiers should be
# rare and almost always one of these two.
_STUDENT_AWARDS_TIER_ORDER: tuple[str, ...] = (
    "National and International Awards",
    "Institutional Awards",
)


_SECTION_TO_LEVEL: dict[Section, str] = {
    "Undergraduate Student Awards": "U",
    "Graduate Student Awards": "G",
}


def index_student_awards(
    section_key: Section, awards: list[StudentAward],
) -> list[tuple[str, StudentAward]]:
    """Return [(C.X.Y, award), …] in the order the renderer will emit them.

    Single source of truth for the level + tier + year-DESC sort, so cli's
    ref-index builder and the renderer agree on numbering. Filters by the
    section's expected level via `_SECTION_TO_LEVEL`.
    """
    if section_key not in _SECTION_TO_LEVEL:
        raise ValueError(
            f"index_student_awards called with non-award section "
            f"{section_key!r}; expected one of {list(_SECTION_TO_LEVEL)}"
        )
    expected_level = _SECTION_TO_LEVEL[section_key]
    filtered = [a for a in awards if a.level == expected_level]
    if not filtered:
        return []
    code = SECTION_CODES[section_key]
    by_tier: dict[str, list[StudentAward]] = {}
    for a in filtered:
        by_tier.setdefault(a.tier, []).append(a)

    def _tier_key(t: str) -> tuple[int, str]:
        try:
            return (_STUDENT_AWARDS_TIER_ORDER.index(t), "")
        except ValueError:
            return (len(_STUDENT_AWARDS_TIER_ORDER), t.lower())

    out: list[tuple[str, StudentAward]] = []
    idx = 0
    for tier in sorted(by_tier.keys(), key=_tier_key):
        # Year ASCENDING (oldest first) — matches the chronological-
        # emission convention used by every other dated section in the
        # packet (C.1 / C.2 / C.4 / C.5 / C.10 / C.11 / C.14 / C.15 /
        # C.16.2.3 / C.17). Newest-first read as "career going
        # backwards" to a tenure-packet reader.
        for a in sorted(by_tier[tier], key=lambda x: x.year):
            idx += 1
            out.append((f"{code}.{idx}", a))
    return out


def render_student_awards_section(
    section_key: Section,
    awards: list[StudentAward],
    out: IO[str],
) -> None:
    """C.16.2.4 (undergrad) / C.16.3.3 (grad) student awards / fellowships.

    Filters the shared `awards` list by `level` matching the section's
    expected level (`section_key` → `_SECTION_TO_LEVEL`). Same shape for
    both calls — tier subheadings inside, flat sequential numbering across
    both tiers (`{code}.1`, `{code}.2`, …; tier doesn't contribute to the
    number — adding tier_idx would push to 5 levels deep without info gain).

    Tier render order follows `_STUDENT_AWARDS_TIER_ORDER` (National first),
    then alphabetical for any non-canonical tiers. Within a tier, entries
    sort by year ASCENDING (oldest first) — matching the chronological
    convention used by every other dated section in the packet. The
    numbering counter does NOT reset between tiers — it counts entries
    across the whole section so each `{code}.N` is a unique back-pointer
    target.

    Empty filtered list → emit nothing (no orphan heading).
    """
    indexed = index_student_awards(section_key, awards)
    if not indexed:
        return
    code = SECTION_CODES[section_key]
    heading = SECTION_HEADINGS[section_key]
    _emit_section_heading(out, code, heading)
    indent = _hanging_indent_for_codes([ref for ref, _ in indexed])
    # Emit tier subheadings as we walk the indexed list — tier changes are
    # detected by previous-tier comparison so we don't re-sort here.
    prev_tier: Optional[str] = None
    for ref, a in indexed:
        if a.tier != prev_tier:
            if prev_tier is not None:
                out.write("\\pard\\par\n")
            _emit_inline_heading(
                out, a.tier, _label_position_for_code(f"{code}.1"),
            )
            prev_tier = a.tier
        body = f"{escape_rtf(a.recipient)}, {escape_rtf(a.award)}"
        if a.year_str:
            body += f" ({escape_rtf(a.year_str)})"
        _emit_list_item(out, ref, body, indent=indent)


# C.16.2.2 four-column table widths (twips). Sum = 9360 = 6.5" usable
# on US Letter at standard margins. Dates narrow (years/seasons are
# short); pathway and audience get the most width because they carry
# the descriptive text; participation is short (head counts).
_UNDERGRAD_PATHWAY_TABLE_WIDTHS: list[int] = [1900, 2900, 3200, 1360]


def render_undergrad_pathways_section(
    pathways: list[UndergradPathway], out: IO[str],
) -> None:
    """C.16.2.2: Other Undergraduate Research Pathways — 4-column table.

    Columns: Dates | Pathway / activity | Audience | Participation.
    Rows are emitted in YAML order so the candidate controls the
    narrative arrangement (longer-running pathways vs one-off events).
    Empty list → emit nothing (no orphan heading) per the C.16.2.X
    skip-when-empty pattern.

    Intro prose for this section is auto-emitted by `_emit_section_heading`
    if `section-prose.md` carries a `## C.16.2.2 …` block.
    """
    if not pathways:
        return
    code = SECTION_CODES["Undergraduate Research Pathways"]
    heading = SECTION_HEADINGS["Undergraduate Research Pathways"]
    _emit_section_heading(out, code, heading)
    table = RtfTable(_UNDERGRAD_PATHWAY_TABLE_WIDTHS)
    table.add_header(["Dates", "Pathway / activity", "Audience", "Participation"])
    for p in pathways:
        table.add_row([
            escape_rtf(p.dates),
            escape_rtf(p.activity),
            escape_rtf(p.audience),
            escape_rtf(p.participation),
        ])
    out.write(table.render())
    # Trailing blank for visual spacing before the next sub-section.
    out.write("\\pard\\par\n")


def render_undergrad_products_section(
    products: list[UndergradProduct], out: IO[str],
) -> None:
    """C.16.2.3: research products with undergraduate co-authors.

    Auto-derived from the bib; never authored in YAML. Renders a brief
    intro pointing the reader at the per-author `U` superscript marker
    (the convention used in C.2/C.4/C.5 citations), followed by a
    numbered list of full-sentence entries:

      "Paper C.X.Y has N undergraduate co-author[s][. This paper was led
      by an undergraduate]."

    Empty list (no undergrad coauthors anywhere) → emit nothing (no
    orphan heading; this is the per-section default — distinct from
    C.15 / C.20 / C.21 which emit "N/A").
    """
    if not products:
        return
    code = SECTION_CODES["Undergraduate Research Products"]
    heading = SECTION_HEADINGS["Undergraduate Research Products"]
    _emit_section_heading(out, code, heading)
    # Intro paragraph: explain the `U` superscript convention used
    # elsewhere in the packet, so the reader knows the section is a
    # summary, not a separate authorship claim.
    intro = (
        "Undergraduate authors are marked with a "
        "{\\super U\\nosupersub{}} superscript in the associated "
        "publication sections. This section provides a summary of "
        "undergraduate participation."
    )
    _emit_intro_note(out, intro)
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(products)))
    for idx, p in enumerate(products, 1):
        plural = "co-authors" if p.n_coauthors > 1 else "co-author"
        sentence_a = (
            f"{escape_rtf(p.product_label)} {_code_link(p.ref)} "
            f"has {p.n_coauthors} undergraduate {plural}."
        )
        sentence_b = (
            " This paper was led by an undergraduate."
            if p.lead_is_undergrad else ""
        )
        # Disambiguator for Section V A.1 entries (bare "A.1.N" code would
        # otherwise read like a Section III A.1 reference).
        sentence_c = " (Under review.)" if p.is_under_review else ""
        body = sentence_a + sentence_b + sentence_c
        _emit_list_item(out, f"{code}.{idx}", body, indent=indent)


def _ct_cell(value: object) -> str:
    """Format an optional CIE / responses / enrolled cell. Missing → "—"."""
    if value is None:
        return "\\u8212?"  # em-dash; RTF \\ansicpg1252 needs the Unicode escape
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


# C.17 column widths (twips). Sum = 9360 = 6.5" usable on US Letter.
# Sem/Year | Title | Number | Responsibility | Resp/Enrolled | CIE summary
_COURSES_TAUGHT_WIDTHS: list[int] = [700, 2300, 1300, 2200, 1100, 1760]


def _render_courses_taught_data_row(
    cellx: list[int], cells: list[str], is_header: bool = False,
) -> str:
    """Standard data / header row — same shape as `RtfTable._render_row`
    but hand-rolled so the renderer can interleave a shaded note row in
    the same logical table run."""
    parts: list[str] = [r"\trowd\trgaph108\trleft0"]
    for pos in cellx:
        parts.append(_CELL_BORDER_BLOCK)
        parts.append(rf"\cellx{pos}")
    for cell in cells:
        # Header cells route through the registry's `table_header` style
        # (same as `RtfTable._render_row`). Body cells emit verbatim.
        content = styled_inline("table_header", cell) if is_header else cell
        parts.append(rf"\pard\intbl {content}\cell")
    parts.append("\\row\n")
    return "".join(parts)


def _render_courses_taught_note_row(
    cellx: list[int], semester_str: str, text: str,
) -> str:
    """Grey-shaded "no course taught" row — cells merge horizontally so the
    text reads as one row-wide message ("Sp25: No 3-credit course taught
    – Granted a course release for ABET self-study leadership", etc.).

    The semester label is prepended INLINE to the text ("Sp25: …") so the
    reader sees the temporal anchor without needing the (now-merged)
    Sem/Year column. Without this lead-in the row reads as a stranded
    note disconnected from the semesters above and below it.

    RTF cell-merge convention:
      * First cell of the merged group: `\\clmgf` (merge-first)
      * Every subsequent cell:          `\\clmrg` (merge-continue)
    All cells must still emit `\\cellx{N}` borders, but the renderer
    merges them visually. Cell background uses color 2 (light grey) from
    the document color table.
    """
    parts: list[str] = [r"\trowd\trgaph108\trleft0"]
    for i, pos in enumerate(cellx):
        # \clcbpat2 = cell background = color 2 (light grey, 220,220,220).
        parts.append(r"\clcbpat2")
        parts.append(r"\clmgf" if i == 0 else r"\clmrg")
        parts.append(_CELL_BORDER_BLOCK)
        parts.append(rf"\cellx{pos}")
    # Prefix the body with the semester label so the row reads in context
    # ("Sp25: No 3-credit course taught – ...") rather than as an
    # unanchored note. `semester_str` is rendered inline; the structural
    # Sem/Year column is now part of the merged cell and isn't visible.
    body = f"{escape_rtf(semester_str)}: {text}" if semester_str else text
    # Only the first cell carries the text; subsequent cells are empty
    # placeholders required by the RTF row-arity invariant.
    parts.append(rf"\pard\intbl {body}\cell")
    for _ in range(len(cellx) - 1):
        parts.append(r"\pard\intbl \cell")
    parts.append("\\row\n")
    return "".join(parts)


def render_courses_taught_section(
    rows: list[CourseTaught], out: IO[str],
) -> None:
    """C.17: 6-column table of course-sections taught with teaching scores.

    Sort: year ASC, then semester_order ASC (Sp → Su → F). Asterisk
    prefix on the title cell when `is_new_course` is True. Missing CIE
    cells render as em-dash (distinguishable from a 0.00 score).

    `is_note_row=True` rows render as a grey-shaded merged-cell row that
    spans the table width — used for "no 3-credit course taught" entries
    (e.g., ABET-self-study release semesters). Only `title` is rendered;
    CIE / responsibility / number cells are ignored.

    `cie_partial=True` rows get a "*" suffix on the CIE summary cell and
    drive a footnote below the table ("Computed on the relevant subset
    of questions asked"). Triggered when fewer than 10 core concepts
    backed the per-row aggregate — v657 semesters (Spring 2022) and any
    v737 row where a "where relevant" question was silent.

    Empty list → section heading + indented "N/A" (same C.15/C.18/C.20/
    C.21 pattern). Mandatory section in the Purdue packet.
    """
    code = SECTION_CODES["Courses Taught"]
    heading = SECTION_HEADINGS["Courses Taught"]
    _emit_section_heading(out, code, heading)
    if not rows:
        emit_styled(out, "na_placeholder", "N/A", indent=720)
        return
    sorted_rows = sorted(rows, key=lambda r: (r.year, r.semester_order))
    cellx: list[int] = []
    cumulative = 0
    for w in _COURSES_TAUGHT_WIDTHS:
        cumulative += w
        cellx.append(cumulative)

    parts: list[str] = []
    parts.append(_render_courses_taught_data_row(cellx, [
        "Sem/Year",
        "Course Title",
        "Course Number",
        "Responsibility",
        "# Responses / # Enrolled",
        "CIE Avg (Min, Max)",
    ], is_header=True))

    any_partial = False
    for r in sorted_rows:
        if r.is_note_row:
            parts.append(_render_courses_taught_note_row(
                cellx, r.semester_str, escape_rtf(r.title),
            ))
            continue
        title_cell = escape_rtf(r.title)
        if r.is_new_course:
            title_cell = f"*{title_cell}"
        if r.responses is None and r.enrolled is None:
            resp_cell = "\\u8212?"
        else:
            resp_cell = f"{_ct_cell(r.responses)} / {_ct_cell(r.enrolled)}"
        # "data not available" — applied when ALL three CIE fields are
        # None. Used for course-rows the candidate taught but for which
        # no EvaluationKit responses exist (e.g., F20 / Sp21 VIP, which
        # ran before VIP entered the formal CIE survey). Avoids rendering
        # an em-dash sequence "(— (—, —))" that reads as if scores
        # were captured but lost.
        if (r.cie_average is None and r.cie_min is None
                and r.cie_max is None):
            cie_cell = "data not available"
        else:
            avg = _ct_cell(r.cie_average)
            cie_cell = f"{avg} ({_ct_cell(r.cie_min)}, {_ct_cell(r.cie_max)})"
            if r.cie_partial:
                cie_cell += "*"
                any_partial = True
        parts.append(_render_courses_taught_data_row(cellx, [
            escape_rtf(r.semester_str),
            title_cell,
            escape_rtf(r.course_number),
            escape_rtf(r.responsibility),
            resp_cell,
            cie_cell,
        ]))
    out.write("".join(parts))
    if any_partial:
        # lint-allow: raw-rtf — small italic (fs20 = 10pt) footnote
        # below the C.17 table, one-off style for the CIE-partial
        # disclaimer. Not promoted to the registry because no other
        # site uses this smaller-than-body italic.
        out.write(
            "\\pard\\li720\\fs20\\i *Computed on the relevant subset of "
            "questions asked.\\i0\\par\\par\n"
        )
    else:
        out.write("\\pard\\par\n")


def render_course_development_section(
    activities: list[CourseDevelopment], out: IO[str],
) -> None:
    """C.18: numbered list of "Summary: description" entries — courses
    designed/re-designed + cross-cutting curricular contributions.

    Empty list → section heading + indented "N/A" (same C.15/C.20/C.21
    pattern; the section is mandatory in the Purdue packet).
    """
    code = SECTION_CODES["Course Development"]
    heading = SECTION_HEADINGS["Course Development"]
    _emit_section_heading(out, code, heading)
    if not activities:
        emit_styled(out, "na_placeholder", "N/A", indent=720)
        return
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(activities)))
    for idx, a in enumerate(activities, 1):
        body = f"{styled_inline('entry_summary', escape_rtf(a.summary))}: {escape_rtf(a.description)}"
        _emit_list_item(out, f"{code}.{idx}", body, indent=indent)


def render_entrepreneurial_activities_section(
    activities: list[EntrepreneurialActivity], out: IO[str],
) -> None:
    """C.20: numbered list of "Summary: description" entries.

    Empty list → section heading + indented "N/A" (same C.15-postdocs
    pattern; the section is mandatory in the Purdue packet even pre-
    revenue).
    """
    code = SECTION_CODES["Entrepreneurial Activities"]
    heading = SECTION_HEADINGS["Entrepreneurial Activities"]
    _emit_section_heading(out, code, heading)
    if not activities:
        emit_styled(out, "na_placeholder", "N/A", indent=720)
        return
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(activities)))
    for idx, a in enumerate(activities, 1):
        body = f"{styled_inline('entry_summary', escape_rtf(a.summary))}: {escape_rtf(a.description)}"
        _emit_list_item(out, f"{code}.{idx}", body, indent=indent)


def render_technology_transfer_section(
    rows: list[TechnologyTransfer],
    paper_index: dict[str, str],
    out: IO[str],
) -> None:
    """C.21: 6-column technology-transfer table.

    Columns (Purdue template): Code/Standard | Change Subject | Reason For
    The Change | Research Supporting The Change | Cited Publications |
    Impact. Empty list → section heading + indented "N/A" (same C.15-
    postdocs pattern).

    `cited_publications` cell renders the resolved C.X.Y refs for each
    YAML title via `paper_index` (validation guarantees they all resolve).
    Unresolved at render time = bug — fall back to the raw title.
    """
    code = SECTION_CODES["Technology Transfer"]
    heading = SECTION_HEADINGS["Technology Transfer"]
    _emit_section_heading(out, code, heading)
    if not rows:
        emit_styled(out, "na_placeholder", "N/A", indent=720)
        return
    # Column widths (twips) sum to 9360 = 6.5" usable on US Letter.
    table = RtfTable([1560, 1560, 1560, 1560, 1560, 1560])
    table.add_header([
        "Code/Standard",
        "Change Subject",
        "Reason For The Change",
        "Research Supporting The Change",
        "Cited Publications",
        "Impact",
    ])
    for row in rows:
        refs: list[str] = []
        for title in row.cited_publications:
            ref = paper_index.get(_normalize_title_for_lookup(title))
            refs.append(_code_link(ref) if ref else escape_rtf(title))
        cited_cell = ", ".join(refs)
        table.add_row([
            escape_rtf(row.code_standard),
            escape_rtf(row.change_subject),
            escape_rtf(row.reason),
            escape_rtf(row.research_supporting),
            cited_cell,
            escape_rtf(row.impact),
        ])
    out.write(table.render())
    out.write("\\pard\\par\n")


def _normalize_title_for_lookup(title: str) -> str:
    """Local wrapper to avoid widening the top-level venue import."""
    from .venue import normalize_title
    return normalize_title(title)


def render_software_products_section(
    products: list[SoftwareProduct], out: IO[str],
) -> None:
    """C.22: itemized list of software products.

    Per-entry shape: hanging-indent header line with bolded name + year span,
    then an indented block paragraph with the free-form description (mirrors
    the C.1 Key Works two-paragraph layout — preserves long descriptions
    without crowding the header).
    """
    if not products:
        return
    code = SECTION_CODES["Software Products"]
    heading = SECTION_HEADINGS["Software Products"]
    _emit_section_heading(out, code, heading)
    # Explanatory note: this section only carries Davis-authored software
    # products. Other impact channels (papers that became tools, CVEs,
    # vulnerability disclosures, technical reports, magazine pieces) live
    # in C.5 "Other publications and products". The cross-ref points the
    # reader at the right section without duplicating entries.
    other_pubs_code = SECTION_CODES["Other publications and products"]
    intro = (
        f"For other forms of impact, see {_code_link(other_pubs_code)}."
    )
    _emit_intro_note(out, intro, indent=_body_indent_for_code(code))
    sorted_products = sorted(products, key=lambda x: x.year)
    indent = _hanging_indent_for_codes(
        _section_codes_up_to(code, len(sorted_products))
    )
    for idx, p in enumerate(sorted_products, 1):
        # Header line: "C.22.N.\tab **name** (year_str)".
        # Bold name via the registry. Brace-scope keeps the trailing
        # space before "(year)" literal — bare `\b0 (year)` would
        # consume the space as the control-word delimiter.
        head = f"{{{styled_inline('entry_name', escape_rtf(p.name))}}}"
        if p.year_str:
            head += f" ({escape_rtf(p.year_str)})"
        _emit_list_item_with_body(
            out, f"{code}.{idx}", head, escape_rtf(p.description), indent=indent,
        )


def render_patents_section(patents: list[Patent], out: IO[str]) -> None:
    if not patents:
        return
    code = SECTION_CODES["Patents"]
    heading = SECTION_HEADINGS["Patents"]
    _emit_section_heading(out, code, heading)

    table = RtfTable(column_widths=PATENT_TABLE_WIDTHS)
    table.add_header(["Title", "Co-Inventors", "Issue Date", "Number", "Impact"])
    for idx, p in enumerate(patents, 1):
        # Title cell leads with bookmarked `C.19.N.` (same shape as grant
        # Row 1) so each patent has a parseable cross-ref code and `@id`
        # refs can resolve to a specific patent. Brace-scoped bold keeps
        # the trailing space literal (the `\b0 X` delimiter-eats-space
        # trap from CLAUDE.md).
        entry_code = f"{code}.{idx}"
        # Bold entry code via the registry. Brace-scope keeps the
        # trailing space + period readable.
        title_cell = (
            f"{{{styled_inline('entry_name', _ref_anchor(entry_code))}.}} "
            f"{escape_rtf(p.title)}"
        )
        table.add_row([
            title_cell,
            # lint-allow: raw-rtf — co_inventors comes pre-formatted with `\b` bold-for-me markers from format_inventors; escaping would clobber them.
            p.co_inventors,
            escape_rtf(p.date),
            escape_rtf(p.number),
            escape_rtf(p.impact),
        ])
    out.write(table.render())
    out.write("\\pard\\par\n")


def _ref_anchor(code: str, bookmark_prefix: str = "") -> str:
    """Wrap a section code with RTF bookmark markup. The bookmark name uses
    underscores in place of dots (RTF bookmark-name spec). Paragraph
    renderers emit this around the leading `C.X.Y` text on each entry so
    that `@id` cross-references can hyperlink to the corresponding
    bookmark via the post-write substitution in `_finalize_ref_hyperlinks`.

    `bookmark_prefix` (optional) is prepended to the code BEFORE bookmark
    name generation, so the display text stays as `code` but the bookmark
    target becomes "{prefix}_{code-with-underscores}". Used to namespace
    Section V entries ("V." prefix → "V_A_2_3") so they don't collide
    with the Section III A.2 Degrees bookmarks at the same numeric code.
    """
    bookmark = (bookmark_prefix + code).replace(".", "_")
    return f"{{\\*\\bkmkstart {bookmark}}}{code}{{\\*\\bkmkend {bookmark}}}"


def _code_link(code: str) -> str:
    """Emit a sentinel-wrapped `C.X.Y` code so the post-pass
    `_finalize_ref_hyperlinks` converts it to a clickable RTF hyperlink
    matching `@id`-resolved refs.

    Single source of truth for the "this is a cross-reference code"
    emission. Use this everywhere a paper_index / key_work_index lookup
    or a static C.X.Y reference text would otherwise be plain text —
    `(see {ref})`, `(listed as {ref})`, `Paper {ref}`, comma-joined
    `cited_publications` cells, etc.

    The `builders.REF_LINK_OPEN` / `REF_LINK_CLOSE` sentinel chars
    survive `escape_rtf` and `rtf_escape_unicode` unchanged (both <0x80
    and not in the escape set); the post-pass substitutes them with
    `{\\field{\\*\\fldinst HYPERLINK \\\\l "name"}{\\fldrslt {\\cs1...}}}`.
    """
    from .builders import REF_LINK_CLOSE, REF_LINK_OPEN
    return f"{REF_LINK_OPEN}{code}{REF_LINK_CLOSE}"


_REF_SENTINEL_PATTERN = re.compile(r"\x01([^\x02]+)\x02")


def _finalize_ref_hyperlinks(rtf: str) -> str:
    """Convert `\\x01CODE\\x02` sentinels (planted by
    `builders.resolve_refs(..., link_format=True)`) into RTF HYPERLINK
    fields targeting the bookmark `CODE.replace('.', '_')`.

    The sentinel chars are <0x80 and survive `escape_rtf` /
    `rtf_escape_unicode` untouched, so this single post-pass is enough.

    Display text uses color 1 (blue) + underline so the hyperlink reads
    as a link in Word even before clicking. Color 1 is declared in the
    `\\colortbl` at the top of `write_rtf` and is the same blue used for
    external DOI/URL hyperlinks elsewhere in the doc.

    Pipe-form support: the sentinel may carry `display|bookmark_code`
    (pipe-delimited). When present, the LHS is the user-visible display
    text, the RHS is the bare bookmark target. Used to render
    "Section V, A.1.3" as the visible string while still hyperlinking
    to the bare `A_1_3` bookmark. Without a pipe, display = bookmark
    (existing behavior).
    """
    def _sub(match: "re.Match[str]") -> str:
        text = match.group(1)
        if "|" in text:
            display, code = text.split("|", 1)
        else:
            display, code = text, text
        bookmark = code.replace(".", "_")
        # Use the `\cs1` Hyperlink character style (defined in the
        # stylesheet) — survives Word copy-paste because Word maps the
        # named style across the clipboard. Inline `\cf1\ul` formatting
        # is dropped on copy-paste; `\cs1` is the Word-canonical form.
        # The `\cf1\ul` after `\cs1` is a belt-and-suspenders fallback for
        # viewers that don't honor the character style (TextEdit, basic
        # RTF parsers) — Word ignores them when the named style applies.
        return (
            f'{{\\field{{\\*\\fldinst HYPERLINK \\\\l "{bookmark}"}}'
            f'{{\\fldrslt {{\\cs1\\cf1\\ul {display}}}}}}}'
        )
    return _REF_SENTINEL_PATTERN.sub(_sub, rtf)


def _label_position_for_code(code: str) -> int:
    """Twips column where a numbered entry's label visually starts.

    The label sits one indent step to the RIGHT of its parent heading
    so the entire entry block (label + body) reads as nested inside
    the heading. Without this offset the hanging-indent gutter would
    drop the label into the heading's own column, defeating the
    visual nesting cue.

    Mapping (parent level → label column):
        1 → 360     (A.2.N entries under A.2, etc.)
        2 → 720     (C.16.1.N under C.16.1)
        3 → 1080    (C.16.2.3.N under C.16.2.3)
    """
    parent_code = code.rsplit(".", 1)[0]
    parent_level = _heading_level(parent_code)
    return parent_level * _HEADING_INDENT_PER_LEVEL


def _body_indent_for_code(code: str) -> int:
    """Body indent (twips) for content beneath a section heading at `code`.

    Single source of truth for "where does the content under a heading
    start?" — every prose / placeholder / numbered-list renderer routes
    its base indent through this helper so deeper sub-sections nest
    visually inside their parents.

    Computed as `label_position + 720` so the body wrap column sits
    far enough right of the label (in a hanging-indent layout) for the
    label width to fit. The 720-twip gap fits codes up to ~7 chars.

    Mapping (level → body indent):
        1 → 1080    (A.2 body wraps at 1080 — labels at 360)
        2 → 1440    (C.16.1 — labels at 720)
        3 → 1800    (C.16.2.3 — labels at 1080)
        4 → 2160    (reserved)
    """
    level = _heading_level(code)
    label_pos = level * _HEADING_INDENT_PER_LEVEL
    return label_pos + 720


def _hanging_indent_for_codes(codes: list[str]) -> int:
    """Compute the per-section hanging-indent (in twips) that fits the
    longest entry code without the label overflowing the tab column.

    Two layers combine:
      * Level base from `_body_indent_for_code` — deeper sub-sections
        indent further so the nested structure is visible.
      * Label fit: widens the indent when the longest code label is too
        long for the level base, so the `\\tab` after the label lands
        cleanly past the visible label width.

    Empirically: Times New Roman 12pt renders at ~100 twips per character.
    The visible label is `{code}.` (code + trailing period). At level 1
    the 720-twip base fits up to 7 visible chars; at level 3 the 1440
    base fits substantially more, so deeper sections rarely need the
    label-fit widening at all. Result is rounded up to a multiple of
    360 (½ tab stop) so consecutive sections land on aligned boundaries.
    """
    if not codes:
        return 720
    # Derive the parent section's code from a sample child (strip the
    # last numeric suffix). For codes=["C.16.2.3.1", "C.16.2.3.2"],
    # parent_code="C.16.2.3" (level 3) → base=1440.
    sample = codes[0]
    parent_code = sample.rsplit(".", 1)[0]
    label_pos = _label_position_for_code(sample)
    base = _body_indent_for_code(parent_code)  # label_pos + 720
    max_visible_chars = max(len(c) for c in codes) + 1  # +1 for trailing period
    # The 720-twip gap between label_pos and base fits up to 7 visible
    # chars. For longer labels, widen the body indent so the label still
    # fits in [label_pos, body_indent). 110 twips/char + 80 buffer,
    # rounded up to a 360 mark.
    if max_visible_chars <= 7:
        return base
    label_fit = ((label_pos + max_visible_chars * 110 + 80 + 359) // 360) * 360
    return max(base, label_fit)


def _section_codes_up_to(code_prefix: str, n: int) -> list[str]:
    """Convenience: enumerate `{code_prefix}.1` … `{code_prefix}.n` strings.
    Used by every renderer that emits a contiguous numbered list."""
    return [f"{code_prefix}.{i}" for i in range(1, n + 1)]


def _emit_list_item(
    out: IO[str], code: str, body: str, indent: int = 720,
    *, bookmark_prefix: str = "",
) -> None:
    """Canonical hanging-indent numbered list entry — the C.6 talks shape.

    Renders as:
        \\pard\\li{indent}\\fi-{indent} {bookmarked-code}.\\tab {body}\\par\\par

    The hanging indent puts the entry code in the left gutter; body text
    wraps in the indented column. The default 720 twips (½") fits codes
    up to ~7 visible chars; pass a larger `indent` for sections whose
    longest code overflows (use `_hanging_indent_for_codes` to derive it).
    Within ONE section, every call must use the same `indent` so labels
    align — derive once per section, pass to every entry.

    Trailing `\\par\\par` inserts one blank paragraph between entries
    (the spacing the user ratified on the C.6 talks layout — applies
    uniformly to every paragraph-based numbered section).

    Use this for every "numbered list" section emission:
      * C.6 Invited Talks
      * C.7 Leadership Roles
      * C.8 Media Appearances
      * C.9 Conference Presentations
      * C.16.2.3 Undergrad Research Products
      * C.16.2.4 / C.16.3.3 Student Awards (within each tier sub-block)
      * C.18 Course Development
      * C.20 Entrepreneurial Activities
      * C.23 / C.24 / C.25 / C.26 Service

    Distinct from table-shaped sections (C.14 students, C.17 courses
    taught, C.19 patents, C.21 technology transfer) which use `RtfTable`.
    Distinct from C.22 Software Products which uses a deliberate
    two-paragraph header+body shape.

    The `_ref_anchor` wrap on the code makes the entry a bookmark target
    for `@id` and raw section-code cross-refs.
    """
    # Hanging indent: label_pos is the FIRST-line column for the entry
    # label; indent is the body wrap column. `\\fi` is the first-line
    # indent relative to `\\li`, computed so the label lands at
    # label_pos: `label_pos = li + fi` → `fi = label_pos - li`. With
    # label_pos one step right of the parent heading, the entry visually
    # nests under the heading instead of hanging into the heading's
    # column. `\\tx{indent}` sets an explicit tab stop at the body column
    # so the `\\tab` after the label lands cleanly regardless of label
    # width.
    label_pos = _label_position_for_code(code)
    fi = label_pos - indent  # negative; label starts at li + fi = label_pos
    # `\plain\f0\fs{BODY_FONT_SIZE}` declares TNR 11pt explicitly so body
    # entries don't silently inherit heading character formatting
    # (`\fs28` / `\b`) when Word decides not to honor the blank-line
    # font reset between heading and body — was the cause of the A.1.X
    # identifier list rendering at the wrong size.
    from .styles import BODY_FONT_SIZE
    out.write(
        f"\\pard\\plain\\f0\\fs{BODY_FONT_SIZE}\\li{indent}\\fi{fi}\\tx{indent} "
        f"{_ref_anchor(code, bookmark_prefix=bookmark_prefix)}.\\tab "
        f"{body}\\par\\par\n"
    )


def _emit_list_item_with_body(
    out: IO[str], code: str, header: str, body_paragraph: str,
    indent: int = 720,
) -> None:
    """Two-paragraph variant of `_emit_list_item` — used by C.1 Key Works
    and C.22 Software Products, both of which lead with a short
    hanging-indent citation/name line and then a block-indented
    description paragraph below.

    Same tab-stop discipline as `_emit_list_item`: explicit `\\tx{indent}`
    so the header `\\tab` lands at the indent column regardless of label
    width. Body paragraph uses `\\fi0` (no first-line outdent) so its
    text starts AT the indent column too — visually aligned under the
    header content, not under the label.

    Caller computes `indent` once per section via
    `_hanging_indent_for_codes` and passes it to every call so labels
    align within the section.
    """
    label_pos = _label_position_for_code(code)
    fi = label_pos - indent  # negative; label starts at li + fi = label_pos
    out.write(
        f"\\pard\\li{indent}\\fi{fi}\\tx{indent} "
        f"{_ref_anchor(code)}.\\tab {header}\\par\n"
    )
    out.write(
        f"\\pard\\li{indent}\\fi0 {body_paragraph}\\par\\par\n"
    )


_HEADING_FONT_SIZE_BY_LEVEL: dict[int, int] = {
    1: 28,   # C.1, C.2, ..., A.1 — top-level section
    2: 26,   # C.16.1, C.16.2 — first-tier sub-section
    3: 24,   # C.16.2.1, C.16.2.3 — second-tier sub-section
    4: 22,   # C.16.2.3.1 etc. (rare; reserved)
}
_HEADING_INDENT_PER_LEVEL: int = 240  # twips per level (≈⅙″ ≈ 2-3 spaces)
# RTF font size = half-points. 22 = 11pt — the canonical body size for
# the rendered packet. Used everywhere body text is reset (post-heading,
# post-inline-heading, post-group-heading). Headings stay at their
# fs28 / fs32 values so the heading-vs-body contrast is preserved.
_BODY_FONT_SIZE: int = 22


def _heading_level(code: str) -> int:
    """Sub-section depth derived from dot count: C.16 → 1, C.16.1 → 2,
    C.16.2.1 → 3. Caps at 4 to keep the font size readable for very deep
    sub-sub-sections (none currently in use)."""
    return min(code.count("."), 4)


def _emit_section_heading(out: IO[str], code: str, heading: str) -> None:
    """Section / sub-section header paragraph — emphasized + level-scaled font.

    Visual style scales by dot-depth so the reader can locate their
    place in long lists:
      * Level 1 (C.1 / A.1 / ...): BOLD at fs28 — strong "find your
        place" anchors in long sections.
      * Level 2+ (C.5.1 / C.16.1 / C.16.2.3 / ...): ITALIC at the
        level-scaled font size — softer emphasis so nested sub-sections
        don't compete with their parent for the reader's eye.

    Indent steps by `_HEADING_INDENT_PER_LEVEL` per level so the
    outline nests visually.

    Top-level codes get a leading blank paragraph for visual breathing
    room. Sub-section codes skip the leading blank — they nest directly
    under their parent's flow. Every heading carries a bookmark wrap so
    cross-references resolve (level 1 wraps "{code} {heading}" so the
    full heading text becomes the anchor span; deeper levels wrap just
    the code).
    """
    level = _heading_level(code)
    indent = (level - 1) * _HEADING_INDENT_PER_LEVEL
    # Level-1 codes get a leading blank for breathing room above the
    # heading (the OLD inline emit preserved here pre-migration). Then
    # build the bookmark-wrapped heading block and route through
    # `emit_styled(section_h{level}, …)`. The section-h{level} styles
    # in the registry already carry the level-scaled font + bold-or-
    # italic emphasis + Word "heading 3" / "heading 4" markers.
    if level == 1:
        out.write(f"\\pard\\plain\\f0\\fs{_BODY_FONT_SIZE}\\par\n")
    bookmark_name = code.replace(".", "_")
    if level == 1:
        heading_block = (
            f"{{\\*\\bkmkstart {bookmark_name}}}{code} {heading}"
            f"{{\\*\\bkmkend {bookmark_name}}}"
        )
    else:
        heading_block = f"{_ref_anchor(code)} {heading}"
    emit_styled(out, f"section_h{level}", heading_block, indent=indent)
    # Auto-emit any hand-authored intro prose registered for this code
    # in `section_prose.md`. Sections without an entry are no-op'd. The
    # body indent comes from `_body_indent_for_code` so prose nests at
    # the same column where YAML-driven content under this heading would
    # land — keeps the visual hierarchy uniform whether the body is
    # prose or data. Markdown `**bold**` / `*italic*` inline emphasis
    # is translated to RTF via `_markdown_inline_to_rtf`, available for
    # every section's prose (not just B.X).
    prose = _section_prose.get(code)
    if prose:
        body_indent = _body_indent_for_code(code)
        for p in prose:
            out.write(
                f"\\pard\\li{body_indent} {_markdown_inline_to_rtf(p)}"
                f"\\par\\par\n"
            )


def _emit_placeholder_subsection(
    out: IO[str], code: str, title: str,
) -> None:
    """Emit a sub-section heading whose CONTENT is intentionally empty,
    used for C.16 outline slots that don't have prose yet. The heading
    appears (so the reader sees the planned structure) followed by an
    indented blank paragraph — content for that slot is added later by
    filling in the YAML / editing this call site. The blank paragraph
    matches the heading's indent so future-added content lines up.
    """
    _emit_section_heading(out, code, title)
    # Placeholder body lines up with where REAL content under this same
    # heading would land — routed through `_body_indent_for_code` so when
    # someone fills the slot later, the prose lands at the same indent.
    out.write(f"\\pard\\li{_body_indent_for_code(code)}\\par\\par\n")


# Optional intro prose for any section heading now lives in the
# author-editable `assets/section-prose.md` (loader: `load_section_prose`
# in builders.py). `_emit_section_heading` auto-emits the body for the
# code it just rendered — no caller code needed. To populate a section
# with prose, add a `## <CODE> …` block to that file; to remove prose,
# delete the block.


def write_rtf(
    path: str,
    publications: Publications,
    patents: list[Patent],
    paper_index: Optional[dict[str, str]] = None,
    key_works: Optional[list[KeyWork]] = None,
    key_work_index: Optional[dict[str, str]] = None,
    invited_talks: Optional[list[InvitedTalk]] = None,
    leadership_roles: Optional[list[LeadershipRole]] = None,
    media_appearances: Optional[list[MediaAppearance]] = None,
    conference_presentations: Optional[list[ConferencePresentation]] = None,
    bib_entries: Optional[list[BibEntry]] = None,
    grants_as_pi: Optional[list[Grant]] = None,
    grants_as_co_pi: Optional[list[Grant]] = None,
    gifts: Optional[list[Grant]] = None,
    internal_grants: Optional[list[Grant]] = None,
    graduate_students: Optional[list[Student]] = None,
    postdocs_visiting: Optional[list[PostdocVisiting]] = None,
    undergraduate_students: Optional[list[Student]] = None,
    university_service: Optional[list[ServiceEntry]] = None,
    profession_service: Optional[list[ServiceEntry]] = None,
    national_service: Optional[list[ServiceEntry]] = None,
    other_service: Optional[list[ServiceEntry]] = None,
    under_review: Optional[list[UnderReview]] = None,
    software_products: Optional[list[SoftwareProduct]] = None,
    student_awards: Optional[list[StudentAward]] = None,
    undergrad_pathways: Optional[list[UndergradPathway]] = None,
    undergrad_products: Optional[list[UndergradProduct]] = None,
    entrepreneurial_activities: Optional[list[EntrepreneurialActivity]] = None,
    technology_transfer: Optional[list[TechnologyTransfer]] = None,
    course_development: Optional[list[CourseDevelopment]] = None,
    courses_taught: Optional[list[CourseTaught]] = None,
    candidate_info: Optional[CandidateInformation] = None,
    section_prose: Optional[dict[str, list[str]]] = None,
    pending_proposals: Optional[list[Grant]] = None,
    sections_filter: Optional[set[str]] = None,
) -> None:
    log.info("Generating RTF file: %s", path)
    paper_index = paper_index or {}
    # Stash on the module-level so `_emit_section_heading` (called from
    # ~20 sites) can pick up intro prose for the code it just emitted
    # without every caller threading the dict through. Cleared at the
    # end of write_rtf in a try/finally to keep state from leaking
    # across calls in long-running processes.
    global _section_prose
    _section_prose = section_prose or {}
    key_works = key_works or []
    key_work_index = key_work_index or {}
    invited_talks = invited_talks or []
    leadership_roles = leadership_roles or []
    media_appearances = media_appearances or []
    conference_presentations = conference_presentations or []
    bib_entries = bib_entries or []
    grants_as_pi = grants_as_pi or []
    grants_as_co_pi = grants_as_co_pi or []
    gifts = gifts or []
    internal_grants = internal_grants or []
    graduate_students = graduate_students or []
    # Note: postdocs_visiting defaults to [] BUT the renderer still emits
    # the C.15 section heading + indented "N/A" — the empty list is a
    # signal, not a skip.
    postdocs_visiting = postdocs_visiting or []
    undergraduate_students = undergraduate_students or []
    university_service = university_service or []
    profession_service = profession_service or []
    national_service = national_service or []
    other_service = other_service or []
    under_review = under_review or []
    software_products = software_products or []
    student_awards = student_awards or []
    undergrad_pathways = undergrad_pathways or []
    undergrad_products = undergrad_products or []
    # C.20 and C.21 default to [] but the renderers EMIT "N/A" rather than
    # skipping — empty IS the signal pre-promotion.
    entrepreneurial_activities = entrepreneurial_activities or []
    technology_transfer = technology_transfer or []
    course_development = course_development or []
    courses_taught = courses_taught or []
    # Section V, A.1 index — maps each under_review entry's position in
    # the (already-sorted-upstream) list to its emitted code in the
    # pipe-form "Section V, A.1.{N}|V.A.1.{N}". The pipe-form is honored
    # by `_finalize_ref_hyperlinks` (display=LHS, bookmark target=RHS)
    # so the rendered student-table cell shows "Section V, A.1.3" while
    # hyperlinking to the namespaced bookmark `V_A_1_3` on the under-
    # review entry. The `V.` bookmark prefix prevents collision with
    # Section III A.1 Identifiers entries (also numbered A.1.N).
    ur_code = SECTION_CODES["Under Review"]
    under_review_index: dict[int, str] = {
        i: f"Section V, {ur_code}.{i + 1}|V.{ur_code}.{i + 1}"
        for i in range(len(under_review))
    }
    # Per-section emission filter (set by --sections CLI flag). Returns
    # True for every section when no filter is supplied (default — emit
    # everything). When a filter is set, a section emits iff its code is
    # in the filter OR any code in the filter is a parent of its code
    # (so `--sections C.16` also emits the C.16.2.3 / C.16.2.4 / C.16.3.3
    # sub-sections, useful for "give me all of mentoring" requests).
    def _emit(section_key: Section) -> bool:
        if sections_filter is None:
            return True
        code = SECTION_CODES[section_key]
        if code in sections_filter:
            return True
        return any(code.startswith(f + ".") for f in sections_filter)

    # Buffer the RTF in memory so a final post-pass can convert
    # `\x01CODE\x02` ref sentinels into RTF HYPERLINK fields. The packet
    # is small (~100KB), so buffering is cheap and saves an extra
    # read+rewrite round trip.
    out = io.StringIO()
    if True:  # preserves the original indented body block below
        out.write(
            r"{\rtf1\ansi\ansicpg1252\deff0"
            r"{\fonttbl{\f0\froman\fcharset0 Times New Roman;}}"
        )
        # Document-level body-text default: 11pt (RTF half-points = 22).
        # Word inherits this for any paragraph that doesn't set its own
        # font size; every body emission also resets to \fs{_BODY_FONT_SIZE}
        # explicitly so a copy-paste-into-Word document keeps the body
        # size when the user lifts a paragraph out of context.
        out.write(f"\\fs{_BODY_FONT_SIZE}\n")
        # Color table: 1 = blue (hyperlinks), 2 = light grey (table-row dividers).
        out.write(r"{\colortbl;\red0\green0\blue255;\red220\green220\blue220;}")
        # Stylesheet: \s1 = Heading 1. Word maps the style name "heading 1"
        # to the navigation pane + auto-TOC + the user's Heading 1 theme.
        # Stylesheet:
        #   \s1   — paragraph style "heading 1" (Word navigation pane + TOC)
        #   \cs1  — character style "Hyperlink" (blue + underline)
        # The hyperlink character style is critical for Word copy-paste:
        # inline `\cf1\ul` formatting on each link gets lost on Word →
        # Word clipboard transit (Word strips inline formatting in favor
        # of its named char-style table), but a named `\cs1 Hyperlink`
        # style survives because Word recognizes the canonical name.
        # The `\additive` flag means the style ADDS to existing formatting
        # rather than replacing it (so bold/italic surrounding the link
        # are preserved).
        # `\sN` paragraph styles named "heading N" (lowercased per RTF
        # convention) are what Word's "Insert Table of Contents"
        # command scans for. The block is now DERIVED from the style
        # registry via `styles.format_stylesheet_block()` so a registry
        # edit (e.g. bumping `SECTION_H1_FS`) automatically updates
        # the corresponding stylesheet declaration. No drift.
        #
        # TOC level mapping (see styles._HEADING_STYLE_NAMES):
        #   \s1 → group_heading + roman_section (V. / A. / B. / C.)
        #   \s2 → subgroup_heading (PUBLISHED WORK / ...)
        #   \s3 → section_h1 (C.1, A.1, B.1, ...)
        #   \s4 → section_h2/h3/h4 (C.5.1, C.16.2.3, ...)
        from .styles import format_stylesheet_block
        out.write(format_stylesheet_block())

        # Section III front matter — A. GENERAL INFORMATION (A.1-A.7;
        # A.6 absent by design). Emitted at the TOP per the Purdue
        # tenure-template layout. Skipped silently when no candidate-info
        # YAML was loaded (CLI `--candidate-info` flag).
        if candidate_info is not None:
            sections_in_front = {
                "Identifiers", "Degrees", "Positions at Purdue",
                "Positions at Other Institutions", "Licenses", "Awards",
                "Professional Memberships",
            }
            # The full front matter is emitted when ANY of its sub-sections
            # passes the --sections filter; rendering individual A.X
            # sub-sections in isolation isn't supported (the renderer is
            # one shot covering all of A.1-A.7).
            if any(_emit(s) for s in sections_in_front):  # type: ignore[arg-type]
                render_candidate_information_section(candidate_info, out)

        # Section IV — B.1-B.5 self-evaluation. Emitted between Section
        # III front matter and the C.X scholarly contributions block.
        # Prose for each B.X auto-emits from `_section_prose` (loaded
        # from `assets/section-prose.md`); B-section is skipped silently
        # when no B.X entries are present in the prose dict.
        if any(c in _section_prose for c in ("B.1", "B.2", "B.3", "B.4", "B.5")):
            b_sections = {
                "B1 Summary", "B2 Impact", "B3 Vision",
                "B4 External Events", "B5 COVID Impact",
            }
            if any(_emit(s) for s in b_sections):  # type: ignore[arg-type]
                render_self_evaluation_section(out)

        # C.1 highlight section first (it's a curated promotion list);
        # then the rest of SECTION_ORDER; then Section V, A.1 (Appendix:
        # products under review) is emitted LAST so it reads as an
        # appendix. (The "A.1" code here is reused from Section III's
        # A.1 — see the disambiguation note at the top of write_rtf.)
        # Group heading: "C. SUPPORTING INFORMATION" frames all C.X
        # sections under it, matching the Purdue template's I/II/III
        # outermost layer. Followed immediately by the "PUBLISHED WORK"
        # subgroup that owns C.1-C.5. Only emitted when the section
        # filter would allow ANY of the wrapped sections — the test for
        # "Key Works" is a stand-in for "any C.X section is in scope."
        if _emit("Key Works"):
            _emit_group_heading(out, "C.", "SUPPORTING INFORMATION")
            _emit_subgroup_heading(out, "PUBLISHED WORK")
            render_key_works_section(key_works, paper_index, out)

        # Generic-loop sections: C.2 Journals, C.3 Books and Chapters,
        # C.4 Conferences and Workshops. C.5 ("Other publications") is
        # SKIPPED in this loop and emitted via its own subcategory-aware
        # renderer below.
        for section in SECTION_ORDER:
            # Special-section renderers handle these via their own data structures.
            if section in (
                # Section III front matter (handled by render_candidate_information_section).
                "Identifiers", "Degrees", "Positions at Purdue",
                "Positions at Other Institutions", "Licenses", "Awards",
                "Professional Memberships",
                "Under Review",
                "Other publications and products",
                "Key Works", "Invited Talks", "Leadership Roles",
                "Media Appearances", "Conference Presentations",
                "Grants PI", "Grants Co-PI", "Gifts", "Internal Grants",
                "Graduate Students", "Postdocs and Visiting Scholars",
                "Undergraduate Students",
                "Undergraduate Research Products",
                "Undergraduate Student Awards",
                "Graduate Student Awards",
                "Courses Taught",
                "Course Development",
                "Patents",
                "Entrepreneurial Activities",
                "Technology Transfer",
                "Software Products",
                "University Service", "Profession Service",
                "National Service", "Other Service",
            ):
                continue
            if not _emit(section):
                continue
            citations = publications.get(section, [])
            if not citations:
                continue
            code = SECTION_CODES[section]
            heading = SECTION_HEADINGS[section]
            _emit_section_heading(out, code, heading)

            expansion_done = set()
            indent = _hanging_indent_for_codes(
                _section_codes_up_to(code, len(citations))
            )
            phase = ""
            for idx, cit in enumerate(citations, 1):
                phase = _maybe_emit_career_phase_divider(
                    out, cit.year, phase, indent,
                )
                body = render_citation(cit, expansion_done, paper_index, key_work_index)
                _emit_list_item(out, f"{code}.{idx}", body, indent=indent)

        # C.5 with subcategory subheadings; flat sequential numbering.
        if _emit("Other publications and products"):
            render_other_pubs_section(
                publications.get("Other publications and products", []),
                paper_index, key_work_index, out,
            )
        # Subgroup: EXTERNAL VISIBILITY frames C.6-C.13 (presentations,
        # leadership, media, conference talks, grants, gifts).
        if _emit("Invited Talks"):
            _emit_subgroup_heading(out, "EXTERNAL VISIBILITY")
            render_invited_talks_section(invited_talks, out)
        if _emit("Leadership Roles"):
            render_leadership_section(leadership_roles, out)
        if _emit("Media Appearances"):
            render_media_appearances_section(media_appearances, out)
        if _emit("Conference Presentations"):
            render_conference_presentations_section(
                conference_presentations, bib_entries, paper_index, out,
            )
        # Subgroup: RESEARCH GRANTS AND CONTRACTS AWARDED frames C.10-C.13
        # (PI grants, Co-PI grants, gifts, internal grants).
        if _emit("Grants PI"):
            _emit_subgroup_heading(out, "RESEARCH GRANTS AND CONTRACTS AWARDED")
            render_grants_section("Grants PI", grants_as_pi, out)
        if _emit("Grants Co-PI"):
            render_grants_section("Grants Co-PI", grants_as_co_pi, out)
        if _emit("Gifts"):
            render_grants_section("Gifts", gifts, out)
        if _emit("Internal Grants"):
            render_grants_section("Internal Grants", internal_grants, out)
        # Sections C.14 → C.16 — kept in numeric (C.14, C.15, C.16) order
        # so the bookmark stream emits tuple-monotone. C.16's entire
        # subtree (C.16.1 / C.16.2 / C.16.2.* / C.16.3 / C.16.3.*) emits
        # contiguously inside the C.16 block, before C.17.
        # Subgroup: MENTORING frames C.14-C.16 (graduate students,
        # postdocs, undergraduate research).
        if _emit("Graduate Students"):
            _emit_subgroup_heading(out, "MENTORING")
            render_students_section(
                "Graduate Students", graduate_students, bib_entries, paper_index, out,
                under_review=under_review,
                under_review_index=under_review_index,
            )
        if _emit("Postdocs and Visiting Scholars"):
            render_postdocs_section(
                postdocs_visiting, bib_entries, paper_index, out,
                under_review=under_review,
                under_review_index=under_review_index,
            )
        if _emit("Undergraduate Students"):
            # C.16 base heading ALWAYS emits — the section is the
            # umbrella for mentoring outline + sub-sections, not a
            # student-table-only section like C.14. `render_students_section`
            # below skips its own heading emit when the student list is
            # empty (its return-early branch), so we emit the C.16
            # heading explicitly here.
            _emit_section_heading(
                out, "C.16", SECTION_HEADINGS["Undergraduate Students"],
            )
            render_students_section(
                "Undergraduate Students", undergraduate_students,
                bib_entries, paper_index, out,
                under_review=under_review,
                under_review_index=under_review_index,
                suppress_heading=True,
            )
            # C.16 outline: placeholders for prose subsections + inline
            # emit of the auto-derived data sub-sections (C.16.2.3 / C.16.2.4
            # / C.16.3.3) so the whole mentoring tree reads top-down per
            # the user-supplied 260603 structure.
            # Each `_emit_section_heading` call auto-pulls intro prose
            # from the section_prose dict for the given code. Sections
            # without an entry in `section-prose.md` render heading-only.
            _emit_section_heading(out, "C.16.1", "Overview")
            _emit_section_heading(
                out, "C.16.2", "Undergraduate Student Mentoring",
            )
            _emit_section_heading(
                out, "C.16.2.1", "Vertically Integrated Projects",
            )
            if _emit("Undergraduate Research Pathways"):
                if undergrad_pathways:
                    render_undergrad_pathways_section(undergrad_pathways, out)
                else:
                    # No YAML data yet → still emit the heading so the
                    # C.16 outline reads correctly. Intro prose (if any)
                    # auto-emits from section-prose.md via the heading.
                    _emit_section_heading(
                        out, "C.16.2.2",
                        "Other Undergraduate Research Pathways",
                    )
            if _emit("Undergraduate Research Products"):
                render_undergrad_products_section(undergrad_products, out)
            if _emit("Undergraduate Student Awards"):
                render_student_awards_section(
                    "Undergraduate Student Awards", student_awards, out,
                )
            _emit_section_heading(
                out, "C.16.3", "Graduate Student Mentoring",
            )
            _emit_section_heading(
                out, "C.16.3.1", "Thesis Advising and Research Supervision",
            )
            if _emit("Graduate Student Awards"):
                # Now C.16.3.2 (renumbered from C.16.3.3) — the
                # "Research Leadership and Publications" sub-section
                # was dropped per the 260605 outline revision.
                render_student_awards_section(
                    "Graduate Student Awards", student_awards, out,
                )
        # Subgroup: LEARNING frames C.17-C.18 (courses taught + course
        # development).
        if _emit("Courses Taught"):
            _emit_subgroup_heading(out, "LEARNING")
            render_courses_taught_section(courses_taught, out)
        if _emit("Course Development"):
            render_course_development_section(course_development, out)
        # Subgroup: TECHNOLOGY TRANSFER frames C.19-C.22 (patents,
        # entrepreneurial activities, technology transfer, software
        # products).
        if _emit("Patents"):
            _emit_subgroup_heading(out, "TECHNOLOGY TRANSFER")
            render_patents_section(patents, out)
        if _emit("Entrepreneurial Activities"):
            render_entrepreneurial_activities_section(entrepreneurial_activities, out)
        if _emit("Technology Transfer"):
            render_technology_transfer_section(
                technology_transfer, paper_index, out,
            )
        if _emit("Software Products"):
            render_software_products_section(software_products, out)
        # Subgroup: SERVICE frames C.23-C.26 (Purdue, profession, state/
        # nation, other).
        if _emit("University Service"):
            _emit_subgroup_heading(out, "SERVICE")
            render_service_section("University Service", university_service, out)
        if _emit("Profession Service"):
            render_service_section("Profession Service", profession_service, out)
        if _emit("National Service"):
            render_service_section("National Service", national_service, out)
        if _emit("Other Service"):
            render_service_section("Other Service", other_service, out)
        # Appendix — Section V. Two sub-sections, both emitted at the
        # very end so the C-section numbering above is intact and the
        # appendix reads as a contiguous tail block:
        #   * Section V, A.1 — products under review
        #   * Section V, A.2 — pending proposals (status: pending grants
        #                       routed here from C.10 / C.11 at build time)
        # The Roman-numeral section heading is emitted IF EITHER
        # sub-section will fire — so the appendix is announced as a
        # named region rather than appearing as bare A.1 entries.
        if _emit("Under Review") or _emit("Pending Proposals"):
            _emit_roman_section_heading(
                out, "V",
                "Supporting Documentation for Pending Publications.",
            )
        if _emit("Under Review"):
            render_under_review_section(under_review, out)
        if _emit("Pending Proposals"):
            render_pending_proposals_section(pending_proposals or [], out)
        out.write("}")
    # Finalize: convert ref-link sentinels into RTF HYPERLINK fields, then
    # write to disk.
    final = _finalize_ref_hyperlinks(out.getvalue())
    with open(path, "w", encoding="utf-8") as f:
        f.write(final)
    log.info("Done. Output: %s", path)
