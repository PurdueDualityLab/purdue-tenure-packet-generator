"""RTF output: table builder + per-citation rendering + section assembly."""
from __future__ import annotations

import io
import logging
import re
from typing import IO, Optional

from .builders import escape_rtf
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
    BibEntry, Citation, ConferencePresentation, CourseDevelopment,
    CourseTaught, EntrepreneurialActivity, Grant, GrantPerson, InvitedTalk,
    KeyWork, LeadershipRole, MediaAppearance, Patent, PostdocVisiting,
    Publications, Section, ServiceEntry, SoftwareProduct, Student,
    StudentAward, TechnologyTransfer, UndergradProduct, UnderReview,
)
from .authors import parse_name_parts
from .venue import parse_venue
from .latex import decode_latex
from .venue import normalize_title


log = logging.getLogger(__name__)


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
            content = rf"\b {cell}\b0" if is_header else cell
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
    When `cit.back_ref_title` is set (CVE → paper linkage), "(see C.4.7)" is appended.

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
            body += f", \\i {escape_rtf(venue)}\\i0"
        body += "."
    else:
        # No comma between the last author and the year — the last-author
        # `*` superscript marker already terminates the author list, and
        # a trailing comma before `(YEAR)` reads as a typo.
        body = f"{cit.authors_rtf} ({cit.year_str}). {escape_rtf(cit.title)}"
        if venue:
            body += f", \\i {escape_rtf(venue)}\\i0"
        body += f"{escape_rtf(cit.details)}."
        # Append DOI (if any) and close it with a period so the regular
        # citation is fully terminated before the tier marker.
        link_field = render_link_field(cit.link)
        if link_field:
            body += link_field + "."
    # Tier marker: underlined, NOT italicized, period after.
    # "Venue rank: " prefix only for peer-reviewed venue categories
    # (Journals + Conferences); bare label for everything else.
    prefix = "Venue rank: " if cit.section in RANKED_SECTIONS else ""
    body += f" \\ul {prefix}{TIER_LABELS[cit.rank]}\\ulnone."
    if cit.back_ref_title and paper_index:
        ref = paper_index.get(normalize_title(cit.back_ref_title))
        if ref:
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


def render_key_works_section(
    key_works: list[KeyWork],
    paper_index: dict[str, str],
    out: IO[str],
) -> None:
    """Emit C.1 entries as TWO paragraphs each:

      1. Hanging-indent paragraph: `C.1.N.\\tab citation text` (matches other sections).
      2. Indented block paragraph: impact text, NOT italic, indented under the citation.

    Two real paragraphs (not `\\line` soft-breaks) so Word paste preserves
    the layout cleanly.
    """
    if not key_works:
        return
    code = SECTION_CODES["Key Works"]
    heading = SECTION_HEADINGS["Key Works"]
    _emit_section_heading(out, code, heading)
    expansion_done: set[str] = set()
    for idx, kw in enumerate(key_works, 1):
        body = render_key_work_citation(kw, expansion_done, paper_index)
        # Citation paragraph (hanging indent: first line at 0, continuation at 720).
        out.write(f"\\pard\\li720\\fi-720 {_ref_anchor(f'{code}.{idx}')}.\\tab {body}\\par\n")
        # Impact paragraph (indented block: both first line and continuation at 720).
        out.write(f"\\pard\\li720\\fi0 {escape_rtf(kw.impact)}\\par\\par\n")


def build_paper_index(publications: Publications) -> dict[str, str]:
    """Map normalized-paper-title → 'C.X.Y' for back-pointer resolution.

    Assumes citations are already chrono-sorted within each section.
    CVEs are excluded as targets (would chain forward).
    """
    index: dict[str, str] = {}
    for section in SECTION_ORDER:
        if section == "Patents":
            continue
        for idx, cit in enumerate(publications.get(section, []), 1):
            if cit.rank == "CVE":
                continue
            if cit.title:
                index[normalize_title(cit.title)] = f"{SECTION_CODES[section]}.{idx}"
    return index


def render_under_review_section(
    under_review: list[UnderReview], out: IO[str],
) -> None:
    """A.1: numbered list of in-flight submissions.

    Body shape per entry: `Authors. Title. Under review: /italic Venue/,
    NN pages. [Due: YYYY-MM-DD]`. The italic venue cell is prefixed with
    "Under review: " so the in-flight status is explicit on every entry
    (helpful when reviewers scan the appendix). The "Due" suffix only
    appears when the YAML entry carries a known `due_date`; entries
    without one render without a deadline marker. Sorted by `due_date`
    ascending so near-deadline submissions surface first.
    """
    if not under_review:
        return
    code = SECTION_CODES["Under Review"]
    heading = SECTION_HEADINGS["Under Review"]
    _emit_section_heading(out, code, heading)
    for idx, ur in enumerate(under_review, 1):
        body = (
            f"{ur.authors_rtf}. {escape_rtf(ur.title)}. "
            f"Under review: \\i {escape_rtf(ur.venue)}\\i0"
        )
        if ur.pages:
            body += f", {escape_rtf(ur.pages)}"
        body += "."
        # Sentinel "9999-99-99" means no known deadline → suppress the marker.
        if ur.due_date and ur.due_date != "9999-99-99":
            body += f" \\ul Due: {escape_rtf(ur.due_date)}\\ulnone."
        _emit_list_item(out, f"{code}.{idx}", body)


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

_C5_SUBCATEGORY_ORDER: tuple[str, ...] = (
    "Magazine",
    "Technical Reports",
    "Direct computing industry impacts",
)


def render_other_pubs_section(
    citations: list[Citation],
    paper_index: dict[str, str],
    key_work_index: dict[str, str],
    out: IO[str],
) -> None:
    """C.5: subcategory-grouped numbered list.

    Each Citation routes via its `rank` to a subcategory subheading
    (Magazine, Technical Reports, Direct computing industry impacts).
    Unknown ranks fall to an "Other" bucket sorted to the end. Numbering
    is FLAT across all subcategories (`C.5.1`, `C.5.2`, ...) so the
    back-pointer code is stable irrespective of how the visual subheadings
    are arranged.

    Within each subcategory the order is whatever the caller produced
    (cli.py sorts `publications["Other publications and products"]` by
    `(year, venue)` — the global publication sort rule).
    """
    if not citations:
        return
    code = SECTION_CODES["Other publications and products"]
    heading = SECTION_HEADINGS["Other publications and products"]
    _emit_section_heading(out, code, heading)

    by_subcat: dict[str, list[Citation]] = {}
    for cit in citations:
        subcat = _C5_SUBCATEGORY_BY_RANK.get(cit.rank, "Other")
        by_subcat.setdefault(subcat, []).append(cit)

    def _subcat_key(s: str) -> tuple[int, str]:
        try:
            return (_C5_SUBCATEGORY_ORDER.index(s), "")
        except ValueError:
            return (len(_C5_SUBCATEGORY_ORDER), s.lower())

    idx = 0  # Section-wide counter; does NOT reset between subcategories.
    expansion_done: set[str] = set()
    for subcat in sorted(by_subcat.keys(), key=_subcat_key):
        out.write(
            f"\\pard\\b\\fs26\\sb120\\sa60 {escape_rtf(subcat)}"
            f"\\b0\\fs24\\par\n"
        )
        for cit in by_subcat[subcat]:
            idx += 1
            body = render_citation(
                cit, expansion_done, paper_index, key_work_index,
            )
            _emit_list_item(out, f"{code}.{idx}", body)


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
    for idx, talk in enumerate(talks, 1):
        _emit_list_item(out, f"{code}.{idx}", render_invited_talk(talk))


def render_leadership_role(role: LeadershipRole) -> str:
    """C.7 format: 'Role, Description. Society. Year.'

    The society is the affiliated professional org (ACM SIGSOFT, IEEE, ASEE)
    and is rendered with the underlined `Society:` prefix to mirror the
    tier-marker styling on citations.
    """
    body = f"{escape_rtf(role.role)}, {escape_rtf(role.description)}, {escape_rtf(role.year_str)}."
    body += f" \\ul Society: {escape_rtf(role.society)}\\ulnone."
    return body


def render_leadership_section(roles: list[LeadershipRole], out: IO[str]) -> None:
    if not roles:
        return
    code = SECTION_CODES["Leadership Roles"]
    heading = SECTION_HEADINGS["Leadership Roles"]
    _emit_section_heading(out, code, heading)
    for idx, role in enumerate(roles, 1):
        _emit_list_item(out, f"{code}.{idx}", render_leadership_role(role))


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
        f"Talk at {{\\i {escape_rtf(venue_clean)}}} in {escape_rtf(year)}. "
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
    # Explanatory note (italic, full-width paragraph before the numbered list)
    out.write(f"\\pard \\i {escape_rtf(_CONF_PRES_NOTE)}\\i0\\par\\par\n")
    # Sort by linked-paper year for chronological order
    from .venue import normalize_title
    def _year(p: ConferencePresentation) -> int:
        from .builders import parse_year
        bib = _bib_entry_by_title_local(bib_entries, p.paper_title)
        return parse_year(bib.get("year", "") if bib else "")
    sorted_pres = sorted(presentations, key=_year)
    for idx, p in enumerate(sorted_pres, 1):
        _emit_list_item(
            out, f"{code}.{idx}",
            render_conference_presentation(p, bib_entries, paper_index),
        )


def render_media_appearance(media: MediaAppearance) -> str:
    """C.8 format: 'Title. Venue. Year. URL:...'"""
    body = f"{escape_rtf(media.title)}. \\i {escape_rtf(media.venue)}\\i0, {escape_rtf(media.year_str)}."
    body += render_link_field(media.url)
    return body


def render_media_appearances_section(media: list[MediaAppearance], out: IO[str]) -> None:
    if not media:
        return
    code = SECTION_CODES["Media Appearances"]
    heading = SECTION_HEADINGS["Media Appearances"]
    _emit_section_heading(out, code, heading)
    for idx, m in enumerate(media, 1):
        _emit_list_item(out, f"{code}.{idx}", render_media_appearance(m))


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
    """Row 3: "{role} - {pct}%, {responsibility prose}".

    Format rules:
      * `{role}` alone if no pct and no responsibility text.
      * `{role}, {responsibility}` for gifts (no computable pct).
      * `{role} - {pct}%, {responsibility}` for grants with a credited
        share. When pct == 100 (sole PI single-institution), the
        `- 100%` is suppressed since 100% is the implicit default and
        the line reads better as `PI, {responsibility}`.

    Lead-institution annotation (`(Purdue is lead)` / `(Columbia is
    lead)`) is NOT included — the C.X section the grant lands in
    already conveys whether Davis is the PI or Co-PI / lead or follower.
    The `Grant.lead_institution` field stays in the schema for downstream
    consumers and is just elided from this rendering.
    """
    role = grant.role
    pct = _compute_responsibility_pct(grant)
    description = grant.responsibility or grant.activities or ""
    bits = [role]
    if pct is not None and pct != 100:
        bits.append(f" - {pct}%")
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


def _format_grant_table(grant: Grant, idx: int, section_code: str) -> str:
    """Render one grant as a 4-row RTF table matching the Purdue CV format.

    Layout (each row in its own `\\trowd`, borders on all sides):
      Row 1 (full width):  "{N}. [{grant_number-link}] {agency} / {title}"
      Row 2 (split):       "{start}-{end}."     |     "${amount}"  (right-aligned)
      Row 3 (full width):  "{role}[ (lead-institution annotation)][, {responsibility|activities}]"
      Row 4 (full width):  "{personnel}"   ("Sole PI" when grant.personnel is empty)
    """
    # --- Row 1: numbered head ---
    # Brace-scope the bold so the close-brace ends the bold AND emits a
    # literal space — bare `\b0 ` consumes its trailing space as the
    # control-word delimiter, producing "C.10.18.2526621" with no gap.
    # Also: emit the full `{section_code}.{idx}` (e.g. "C.10.1") instead
    # of the bare "1." — gives every grant row a parseable cross-ref
    # code and lets `@id` refs into grants resolve to the same form as
    # the rest of the document.
    code = f"{section_code}.{idx}"
    head_bits: list[str] = [f"{{\\b {_ref_anchor(code)}.}} "]
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
        total_amount = sum(g.my_amount for g in grants)
        # Brace-scoped bold so the close-brace terminates the formatting
        # AND the space after it renders as literal whitespace. The bare
        # form `\b0 $X` drops the space (RTF consumes it as the \b0
        # control-word delimiter), producing "...:$X" without a gap.
        out.write(
            f"\\pard {{\\b {escape_rtf(total_label)}:}} "
            f"{escape_rtf(_format_usd(total_amount))}\\par\\par\n"
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
    """Find all C.X.Y / A.1.N refs for papers this student co-authored.

    Scans the bib (C.2/C.3/C.4/C.5 sourced) AND the A.1 under-review list
    (when supplied). Match semantics: structural — last-name equality +
    canonical-initials.startswith(bib-initials), as in
    `authors.lookup_student_type`. Aliases extend the candidate set so
    students whose bib forms use different last names (accent fold,
    short form) are matched once per alias.

    Output is sorted: C.* refs first (by section + idx), then A.1 refs.
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
        prefix = 0 if r.startswith("C.") else 1
        body = r.replace("C.", "").replace("A.", "")
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
    pubs_cell = ", ".join(pubs) if pubs else ""
    role_cell = (
        f"{s.role} (with {s.co_advisor})" if s.co_advisor else s.role
    )
    cells = [
        escape_rtf(s.name),
        escape_rtf(s.degree),
        escape_rtf(s.grad_display),
        escape_rtf(role_cell),
        escape_rtf(pubs_cell),
        escape_rtf(s.position),
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
) -> None:
    """C.14 / C.16 student-table renderer.

    For C.14 specifically: sorts by (Purdue tier, grad_year) so the table
    matches the mandated subsection order (PhD Chair → Co-Chair → D.Eng
    → MS Thesis Chair → Co-Chair → MS Non-Thesis → committee), and emits
    a short grey divider row at each tier transition. C.16 has no tier
    concept; the sort still applies but typically collapses to one tier.
    """
    if not students:
        return
    code = SECTION_CODES[section]
    heading = SECTION_HEADINGS[section]
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
        out.write("\\pard\\li720 N/A\\par\\par\n")
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
        pubs_cell = ", ".join(pubs) if pubs else ""
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


def render_service_section(
    section: Section,
    entries: list[ServiceEntry],
    out: IO[str],
) -> None:
    """Generic renderer for C.23 / C.24 / C.25 / C.26.

    Hanging-indent numbered list: `C.X.Y\\tab description. year.`
    When year_str is empty (ongoing service with no fixed date — typically
    journal reviewing) the trailing year + period is suppressed.
    """
    if not entries:
        return
    code = SECTION_CODES[section]
    heading = SECTION_HEADINGS[section]
    _emit_section_heading(out, code, heading)
    for idx, entry in enumerate(entries, 1):
        body = escape_rtf(entry.description)
        if entry.year_str:
            body += f". {escape_rtf(entry.year_str)}"
        body += "."
        _emit_list_item(out, f"{code}.{idx}", body)


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
        for a in sorted(by_tier[tier], key=lambda x: -x.year):
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
    sort by year DESCENDING (newest first). The numbering counter does NOT
    reset between tiers — it counts entries across the whole section so each
    `{code}.N` is a unique back-pointer target.

    Empty filtered list → emit nothing (no orphan heading).
    """
    indexed = index_student_awards(section_key, awards)
    if not indexed:
        return
    code = SECTION_CODES[section_key]
    heading = SECTION_HEADINGS[section_key]
    _emit_section_heading(out, code, heading)
    # Emit tier subheadings as we walk the indexed list — tier changes are
    # detected by previous-tier comparison so we don't re-sort here.
    prev_tier: Optional[str] = None
    for ref, a in indexed:
        if a.tier != prev_tier:
            if prev_tier is not None:
                out.write("\\pard\\par\n")
            out.write(
                f"\\pard\\b\\fs26\\sb120\\sa60 {escape_rtf(a.tier)}"
                f"\\b0\\fs24\\par\n"
            )
            prev_tier = a.tier
        body = f"{escape_rtf(a.recipient)}, {escape_rtf(a.award)}"
        if a.year_str:
            body += f" ({escape_rtf(a.year_str)})"
        _emit_list_item(out, ref, body)


def render_undergrad_products_section(
    products: list[UndergradProduct], out: IO[str],
) -> None:
    """C.16.2.3: research products with undergraduate co-authors.

    Auto-derived from the bib; never authored in YAML. Each entry renders as
    a numbered line: "{label} {ref} ({n} undergraduate co-author[s] [undergraduate
    is lead author])". Empty list (no undergrad coauthors anywhere) → emit
    nothing (no orphan heading; this is the per-section default — distinct
    from C.15/C.20/C.21 which emit "N/A").
    """
    if not products:
        return
    code = SECTION_CODES["Undergraduate Research Products"]
    heading = SECTION_HEADINGS["Undergraduate Research Products"]
    _emit_section_heading(out, code, heading)
    for idx, p in enumerate(products, 1):
        plural = "co-authors" if p.n_coauthors > 1 else "co-author"
        lead_tag = " [undergraduate is lead author]" if p.lead_is_undergrad else ""
        body = (
            f"{escape_rtf(p.product_label)} {_code_link(p.ref)} "
            f"({p.n_coauthors} undergraduate {plural}{lead_tag})"
        )
        _emit_list_item(out, f"{code}.{idx}", body)


def _ct_cell(value: object) -> str:
    """Format an optional CIE / responses / enrolled cell. Missing → "—"."""
    if value is None:
        return "\\u8212?"  # em-dash; RTF \\ansicpg1252 needs the Unicode escape
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_courses_taught_section(
    rows: list[CourseTaught], out: IO[str],
) -> None:
    """C.17: 6-column table of course-sections taught with teaching scores.

    Sort: year ASC, then semester_order ASC (Sp → Su → F). Asterisk
    prefix on the title cell when `is_new_course` is True. Missing CIE
    cells render as em-dash (distinguishable from a 0.00 score).

    Empty list → section heading + indented "N/A" (same C.15/C.18/C.20/
    C.21 pattern). Mandatory section in the Purdue packet.
    """
    code = SECTION_CODES["Courses Taught"]
    heading = SECTION_HEADINGS["Courses Taught"]
    _emit_section_heading(out, code, heading)
    if not rows:
        out.write("\\pard\\li720 N/A\\par\\par\n")
        return
    sorted_rows = sorted(rows, key=lambda r: (r.year, r.semester_order))
    # Column widths (twips) sum to 9360 = 6.5" usable on US Letter.
    # Sem/Year | Title | Number | Responsibility | Resp/Enrolled | CIE summary
    table = RtfTable([700, 2300, 1300, 2200, 1100, 1760])
    table.add_header([
        "Sem/Year",
        "Course Title",
        "Course Number",
        "Responsibility",
        "# Responses / # Enrolled",
        "CIE Avg (Min, Max)",
    ])
    for r in sorted_rows:
        title_cell = escape_rtf(r.title)
        if r.is_new_course:
            title_cell = f"*{title_cell}"
        if r.responses is None and r.enrolled is None:
            resp_cell = "\\u8212?"
        else:
            resp_cell = f"{_ct_cell(r.responses)} / {_ct_cell(r.enrolled)}"
        # CIE summary: "4.13 (3.76, 4.34)" with em-dash for any missing piece.
        avg = _ct_cell(r.cie_average)
        cie_cell = f"{avg} ({_ct_cell(r.cie_min)}, {_ct_cell(r.cie_max)})"
        table.add_row([
            escape_rtf(r.semester_str),
            title_cell,
            escape_rtf(r.course_number),
            escape_rtf(r.responsibility),
            resp_cell,
            cie_cell,
        ])
    out.write(table.render())
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
        out.write("\\pard\\li720 N/A\\par\\par\n")
        return
    for idx, a in enumerate(activities, 1):
        body = f"\\b {escape_rtf(a.summary)}\\b0: {escape_rtf(a.description)}"
        _emit_list_item(out, f"{code}.{idx}", body)


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
        out.write("\\pard\\li720 N/A\\par\\par\n")
        return
    for idx, a in enumerate(activities, 1):
        body = f"\\b {escape_rtf(a.summary)}\\b0: {escape_rtf(a.description)}"
        _emit_list_item(out, f"{code}.{idx}", body)


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
        out.write("\\pard\\li720 N/A\\par\\par\n")
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
    for idx, p in enumerate(sorted(products, key=lambda x: x.year), 1):
        # Header line: "C.22.N.\tab **name** (year_str)".
        # Brace-scope the bold so the close-brace ends `\b` AND emits a
        # literal space — bare `\b0 (year)` consumes its trailing space
        # as the control-word delimiter, producing "safe-regex(2018-...)"
        # with no gap. Same trap as the Grant Row 1 / Total: $X cases.
        head = f"{{\\b {escape_rtf(p.name)}}}"
        if p.year_str:
            head += f" ({escape_rtf(p.year_str)})"
        out.write(f"\\pard\\li720\\fi-720 {_ref_anchor(f'{code}.{idx}')}.\\tab {head}\\par\n")
        # Body line: indented block paragraph for the description.
        out.write(f"\\pard\\li720\\fi0 {escape_rtf(p.description)}\\par\\par\n")


def render_patents_section(patents: list[Patent], out: IO[str]) -> None:
    if not patents:
        return
    code = SECTION_CODES["Patents"]
    heading = SECTION_HEADINGS["Patents"]
    _emit_section_heading(out, code, heading)

    table = RtfTable(column_widths=PATENT_TABLE_WIDTHS)
    table.add_header(["Title", "Co-Inventors", "Issue Date", "Number", "Impact"])
    for p in patents:
        table.add_row([
            escape_rtf(p.title),
            p.co_inventors,  # already RTF-marked (\b for me); escaping would clobber it
            escape_rtf(p.date),
            escape_rtf(p.number),
            escape_rtf(p.impact),
        ])
    out.write(table.render())
    out.write("\\pard\\par\n")


def _ref_anchor(code: str) -> str:
    """Wrap a section code with RTF bookmark markup. The bookmark name uses
    underscores in place of dots (RTF bookmark-name spec). Paragraph
    renderers emit this around the leading `C.X.Y` text on each entry so
    that `@id` cross-references can hyperlink to the corresponding
    bookmark via the post-write substitution in `_finalize_ref_hyperlinks`.
    """
    bookmark = code.replace(".", "_")
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
    """
    def _sub(match: "re.Match[str]") -> str:
        code = match.group(1)
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
            f'{{\\fldrslt {{\\cs1\\cf1\\ul {code}}}}}}}'
        )
    return _REF_SENTINEL_PATTERN.sub(_sub, rtf)


def _emit_list_item(out: IO[str], code: str, body: str) -> None:
    """Canonical hanging-indent numbered list entry — the C.6 talks shape.

    Renders as:
        \\pard\\li720\\fi-720 {bookmarked-code}.\\tab {body}\\par\\par

    The 720-twip (½") hanging indent puts the entry code in the left
    gutter; body text wraps in the indented column. Trailing `\\par\\par`
    inserts one blank paragraph between entries (the spacing the user
    ratified on the C.6 talks layout — applies uniformly to every
    paragraph-based numbered section).

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
    out.write(
        f"\\pard\\li720\\fi-720 {_ref_anchor(code)}.\\tab {body}\\par\\par\n"
    )


def _emit_section_heading(out: IO[str], code: str, heading: str) -> None:
    """Section header paragraph styled as Word `Heading 1` (via stylesheet \\s1).

    `\\pard\\plain` resets paragraph formatting; `\\s1` applies the named
    style from the stylesheet; the explicit `\\b\\fs28` ensures the heading
    renders correctly even if Word's stylesheet inheritance is unusual.

    Top-level codes (single dot: `C.1`, `C.2`, ..., `A.1`) get a leading
    blank paragraph for visual breathing room before the next major
    section. Sub-section codes (multi-dot: `C.16.2.3` etc.) skip the
    leading blank — they nest directly under their parent's flow.
    """
    is_top_level = code.count(".") == 1
    if is_top_level:
        out.write("\\pard\\plain\\fs24\\par\n")
    out.write(
        f"\\pard\\plain\\s1\\b\\fs28 {code} {heading}\\par\n"
        f"\\pard\\plain\\fs24\\par\n"
    )


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
    undergrad_products: Optional[list[UndergradProduct]] = None,
    entrepreneurial_activities: Optional[list[EntrepreneurialActivity]] = None,
    technology_transfer: Optional[list[TechnologyTransfer]] = None,
    course_development: Optional[list[CourseDevelopment]] = None,
    courses_taught: Optional[list[CourseTaught]] = None,
    sections_filter: Optional[set[str]] = None,
) -> None:
    log.info("Generating RTF file: %s", path)
    paper_index = paper_index or {}
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
    undergrad_products = undergrad_products or []
    # C.20 and C.21 default to [] but the renderers EMIT "N/A" rather than
    # skipping — empty IS the signal pre-promotion.
    entrepreneurial_activities = entrepreneurial_activities or []
    technology_transfer = technology_transfer or []
    course_development = course_development or []
    courses_taught = courses_taught or []
    # A.1 index — maps each under_review entry's position in the
    # (already-sorted-upstream) list to its emitted "A.1.{N}" code.
    # The student-table renderer scans this to thread A.1 refs into
    # each student's "Related Publications" cell alongside C.X.Y refs.
    under_review_index: dict[int, str] = {
        i: f"A.1.{i + 1}" for i in range(len(under_review))
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
        out.write(
            r"{\stylesheet"
            r"{\s1\b\fs28\sb240\sa120\keepn"
            r" \sbasedon0\snext0 heading 1;}"
            r"{\*\cs1\additive\cf1\ul"
            r" \sbasedon10 Hyperlink;}"
            r"}"
        )

        # C.1 highlight section first (it's a curated promotion list);
        # then the rest of SECTION_ORDER; then A.1 (Appendix: products
        # under review) is emitted LAST so it reads as an appendix.
        if _emit("Key Works"):
            render_key_works_section(key_works, paper_index, out)

        # Generic-loop sections: C.2 Journals, C.3 Books and Chapters,
        # C.4 Conferences and Workshops. C.5 ("Other publications") is
        # SKIPPED in this loop and emitted via its own subcategory-aware
        # renderer below.
        for section in SECTION_ORDER:
            # Special-section renderers handle these via their own data structures.
            if section in (
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

            expansion_done: set[str] = set()
            for idx, cit in enumerate(citations, 1):
                body = render_citation(cit, expansion_done, paper_index, key_work_index)
                _emit_list_item(out, f"{code}.{idx}", body)

        # C.5 with subcategory subheadings; flat sequential numbering.
        if _emit("Other publications and products"):
            render_other_pubs_section(
                publications.get("Other publications and products", []),
                paper_index, key_work_index, out,
            )
        if _emit("Invited Talks"):
            render_invited_talks_section(invited_talks, out)
        if _emit("Leadership Roles"):
            render_leadership_section(leadership_roles, out)
        if _emit("Media Appearances"):
            render_media_appearances_section(media_appearances, out)
        if _emit("Conference Presentations"):
            render_conference_presentations_section(
                conference_presentations, bib_entries, paper_index, out,
            )
        if _emit("Grants PI"):
            render_grants_section("Grants PI", grants_as_pi, out)
        if _emit("Grants Co-PI"):
            render_grants_section("Grants Co-PI", grants_as_co_pi, out)
        if _emit("Gifts"):
            render_grants_section("Gifts", gifts, out)
        if _emit("Internal Grants"):
            render_grants_section("Internal Grants", internal_grants, out)
        if _emit("Graduate Students"):
            render_students_section(
                "Graduate Students", graduate_students, bib_entries, paper_index, out,
                under_review=under_review,
                under_review_index=under_review_index,
            )
        if _emit("Undergraduate Students"):
            render_students_section(
                "Undergraduate Students", undergraduate_students,
                bib_entries, paper_index, out,
                under_review=under_review,
                under_review_index=under_review_index,
            )
        if _emit("Postdocs and Visiting Scholars"):
            render_postdocs_section(
                postdocs_visiting, bib_entries, paper_index, out,
                under_review=under_review,
                under_review_index=under_review_index,
            )
        # C.17 sits BEFORE C.18 — render the courses-taught table first.
        if _emit("Courses Taught"):
            render_courses_taught_section(courses_taught, out)
        if _emit("Course Development"):
            render_course_development_section(course_development, out)
        if _emit("Patents"):
            render_patents_section(patents, out)
        if _emit("Entrepreneurial Activities"):
            render_entrepreneurial_activities_section(entrepreneurial_activities, out)
        if _emit("Technology Transfer"):
            render_technology_transfer_section(
                technology_transfer, paper_index, out,
            )
        # C.16.2.3 sits in the mentoring flow with the other C.16-cluster
        # subsections; emit it just before the C.16.2.4 + C.16.3.3 awards.
        if _emit("Undergraduate Research Products"):
            render_undergrad_products_section(undergrad_products, out)
        if _emit("Undergraduate Student Awards"):
            render_student_awards_section(
                "Undergraduate Student Awards", student_awards, out,
            )
        if _emit("Graduate Student Awards"):
            render_student_awards_section(
                "Graduate Student Awards", student_awards, out,
            )
        if _emit("Software Products"):
            render_software_products_section(software_products, out)
        if _emit("University Service"):
            render_service_section("University Service", university_service, out)
        if _emit("Profession Service"):
            render_service_section("Profession Service", profession_service, out)
        if _emit("National Service"):
            render_service_section("National Service", national_service, out)
        if _emit("Other Service"):
            render_service_section("Other Service", other_service, out)
        # Appendix — products under review. Emitted LAST so the C-section
        # numbering is intact + the appendix reads as a tail block.
        if _emit("Under Review"):
            render_under_review_section(under_review, out)
        out.write("}")
    # Finalize: convert ref-link sentinels into RTF HYPERLINK fields, then
    # write to disk.
    final = _finalize_ref_hyperlinks(out.getvalue())
    with open(path, "w", encoding="utf-8") as f:
        f.write(final)
    log.info("Done. Output: %s", path)
