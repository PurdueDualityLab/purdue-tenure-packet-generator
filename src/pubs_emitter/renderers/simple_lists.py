"""Wave A — simple list sections as IR.

Each section is a hanging-indent numbered list: a `ListItem` per entry,
where the body is whatever the legacy per-item formatter
(`render_invited_talk`, `render_leadership_role`, etc.) emits.

Covered sections (Wave A subset — 4 sections with standalone item
formatters):
  * C.6 Invited Talks         → `render_invited_talks_blocks`
  * C.7 Leadership Roles      → `render_leadership_roles_blocks`
  * C.8 Media Appearances     → `render_media_appearances_blocks`
  * C.9 Conference Presentations → `render_conference_presentations_blocks`

The remaining list-shaped sections (C.18 / C.20 / C.23-C.26) have body
formatters inlined into their section emitters and an N/A-placeholder
empty path; they'll migrate in a follow-up batch once Paragraph +
na_placeholder writer arms ship.

Body content stays as `RawRun(legacy_body_str)` during this wave —
the body formatters (`render_invited_talk(item)`) already produce
escaped + styled RTF, so wrapping them in RawRun preserves
byte-identity. Wave D (citations) will migrate the per-item body
builders to return `list[Run]` directly, eliminating the RawRun
escape hatch.
"""
from __future__ import annotations

from typing import Callable, Sequence, TYPE_CHECKING

from ..config import SECTION_CODES
from ..ir import Block, ListItem, RawRun, Run
from ..types import (
    BibEntry,
    ConferencePresentation,
    InvitedTalk,
    LeadershipRole,
    MediaAppearance,
    Section,
)


def _list_section_blocks(
    items: Sequence[object],
    section_name: Section,
    body_formatter: Callable[[object], str],
) -> list[Block]:
    """Generic builder for a numbered-list section.

    Returns one `ListItem` block per entry. `body_formatter(item)` is
    called for each item; the result is wrapped in `RawRun` so the
    writer passes it through unchanged. Empty input → empty list
    (caller decides whether to emit a heading + N/A placeholder for
    sections that need one).
    """
    if not items:
        return []
    from ..rtf import _hanging_indent_for_codes, _section_codes_up_to
    code = SECTION_CODES[section_name]
    indent = _hanging_indent_for_codes(_section_codes_up_to(code, len(items)))
    blocks: list[Block] = []
    for idx, item in enumerate(items, 1):
        body_str = body_formatter(item)
        body_runs: list[Run] = [RawRun(body_str)]
        blocks.append(ListItem(
            code=f"{code}.{idx}",
            body=body_runs,
            indent_twips=indent,
        ))
    return blocks


def render_invited_talks_blocks(talks: list[InvitedTalk]) -> list[Block]:
    """C.6 Invited Talks — IR shape. Body via legacy `render_invited_talk`."""
    from ..rtf import render_invited_talk
    return _list_section_blocks(
        talks, "Invited Talks",
        lambda t: render_invited_talk(t),  # type: ignore[arg-type]
    )


def render_leadership_roles_blocks(
    roles: list[LeadershipRole],
) -> list[Block]:
    """C.7 Leadership Roles — IR shape. Body via legacy `render_leadership_role`."""
    from ..rtf import render_leadership_role
    return _list_section_blocks(
        roles, "Leadership Roles",
        lambda r: render_leadership_role(r),  # type: ignore[arg-type]
    )


def render_media_appearances_blocks(
    media: list[MediaAppearance],
) -> list[Block]:
    """C.8 Media Appearances — IR shape. Body via legacy
    `render_media_appearance` (which handles the empty-venue
    freeform-prose escape hatch added 2026-06-06)."""
    from ..rtf import render_media_appearance
    return _list_section_blocks(
        media, "Media Appearances",
        lambda m: render_media_appearance(m),  # type: ignore[arg-type]
    )


def render_conference_presentations_blocks(
    presentations: list[ConferencePresentation],
    bib_entries: list[BibEntry],
    paper_index: dict[str, str],
) -> list[Block]:
    """C.9 Conference Presentations — IR shape.

    Two extras beyond the generic list:
      * Italic intro note explaining the lead-author-talks convention
        (legacy `_CONF_PRES_NOTE` + `_emit_intro_note`).
      * Sort by linked-paper year (chronological).

    Both ride through `RawRtfBlock` for the intro and inline the sort.
    Body uses RawRun via the generic helper. Migrating the intro to a
    `Paragraph(style="intro_note")` IR block is a follow-up; doesn't
    affect byte-identity.
    """
    if not presentations:
        return []
    import io
    from ..rtf import (
        _CONF_PRES_NOTE,
        _bib_entry_by_title_local,
        _emit_intro_note,
        render_conference_presentation,
    )
    from ..builders import escape_rtf, parse_year
    from ..ir import RawRtfBlock

    # Capture the intro-note legacy emit into a RawRtfBlock prefix.
    intro_buf = io.StringIO()
    _emit_intro_note(intro_buf, escape_rtf(_CONF_PRES_NOTE))
    intro_block = RawRtfBlock(rtf=intro_buf.getvalue())

    def _year(p: ConferencePresentation) -> int:
        bib = _bib_entry_by_title_local(bib_entries, p.paper_title)
        return parse_year(bib.get("year", "") if bib else "")

    sorted_pres = sorted(presentations, key=_year)
    item_blocks = _list_section_blocks(
        sorted_pres, "Conference Presentations",
        lambda p: render_conference_presentation(p, bib_entries, paper_index),  # type: ignore[arg-type]
    )
    return [intro_block, *item_blocks]
