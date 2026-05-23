"""RTF output: table builder + per-citation rendering + section assembly."""
from __future__ import annotations

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
from .types import Citation, InvitedTalk, KeyWork, Patent, Publications
from .venue import normalize_title


log = logging.getLogger(__name__)


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
        for pos in self._cellx:
            parts.append(rf"\cellx{pos}")
        for cell in cells:
            content = rf"\b {cell}\b0" if is_header else cell
            parts.append(rf" {content}\cell")
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

      https://doi.org/X         →  DOI:X
      https://nvd.nist.gov/...  →  CVE-XXXX-NNNN  (no prefix; the ID is self-descriptive)
      anything else             →  URL:<full>
    """
    if not link:
        return ""
    if link.startswith("https://doi.org/"):
        prefix = "DOI:"
        display = link[len("https://doi.org/"):]
    elif "nvd.nist.gov/vuln/detail/" in link:
        prefix = ""
        display = link.rsplit("/", 1)[-1]
    else:
        prefix = "URL:"
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
    body = f"{cit.authors_rtf}, ({cit.year_str}). {escape_rtf(cit.title)}"
    if venue:
        body += f", \\i {escape_rtf(venue)}\\i0"
    body += f"{escape_rtf(cit.details)}."
    # Append DOI (if any) and close it with a period so the regular citation
    # is fully terminated before the tier marker.
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
            body += f" (see {ref})"
    if key_work_index:
        kw_ref = key_work_index.get(normalize_title(cit.title))
        if kw_ref:
            body += f" (listed as {kw_ref})"
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
        body += f" (listed as {canonical})"
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
        out.write(f"\\pard\\li720\\fi-720 {code}.{idx}.\\tab {body}\\par\n")
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
        body = render_invited_talk(talk)
        out.write(f"\\pard\\li720\\fi-720 {code}.{idx}.\\tab {body}\\par\\par\n")


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


def _emit_section_heading(out: IO[str], code: str, heading: str) -> None:
    """Section header paragraph styled as Word `Heading 1` (via stylesheet \\s1).

    `\\pard\\plain` resets paragraph formatting; `\\s1` applies the named
    style from the stylesheet; the explicit `\\b\\fs28` ensures the heading
    renders correctly even if Word's stylesheet inheritance is unusual.
    """
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
) -> None:
    log.info("Generating RTF file: %s", path)
    paper_index = paper_index or {}
    key_works = key_works or []
    key_work_index = key_work_index or {}
    invited_talks = invited_talks or []
    with open(path, "w", encoding="utf-8") as out:
        out.write(
            r"{\rtf1\ansi\ansicpg1252\deff0"
            r"{\fonttbl{\f0\froman\fcharset0 Times New Roman;}}"
        )
        out.write(r"{\colortbl;\red0\green0\blue255;}")
        # Stylesheet: \s1 = Heading 1. Word maps the style name "heading 1"
        # to the navigation pane + auto-TOC + the user's Heading 1 theme.
        out.write(
            r"{\stylesheet"
            r"{\s1\b\fs28\sb240\sa120\keepn"
            r" \sbasedon0\snext0 heading 1;}"
            r"}"
        )

        # C.1 first (highlight section), data-driven from key_works list.
        render_key_works_section(key_works, paper_index, out)

        for section in SECTION_ORDER:
            # Special-section renderers handle these via their own data structures.
            if section in ("Key Works", "Invited Talks", "Patents"):
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
                # Hanging indent: first line flush, continuation indented to 720 twips.
                out.write(
                    f"\\pard\\li720\\fi-720 {code}.{idx}.\\tab {body}\\par\\par\n"
                )

        render_invited_talks_section(invited_talks, out)
        render_patents_section(patents, out)
        out.write("}")
    log.info("Done. Output: %s", path)
