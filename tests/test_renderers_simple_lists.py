"""Tests for `pubs_emitter.renderers.simple_lists` — Phase 5 Wave A
of the IR refactor (C.6, C.7, C.8, C.9 simple-list-section migrations).

Two test layers per section:
  1. **IR shape** — assert the renderer returns `list[ListItem]` (plus
     intro RawRtfBlock for C.9).
  2. **Byte-identity** — assert the IR path produces the same RTF as
     the legacy `render_X_section(out)`.
"""
from __future__ import annotations

import io

import pytest

from pubs_emitter import ir
from pubs_emitter.renderers.simple_lists import (
    render_invited_talks_blocks,
    render_leadership_roles_blocks,
    render_media_appearances_blocks,
)
from pubs_emitter.rtf import (
    render_invited_talks_section,
    render_leadership_section,
    render_media_appearances_section,
)
from pubs_emitter.types import InvitedTalk, LeadershipRole, MediaAppearance
from pubs_emitter.writer_rtf import RtfWriter


def _talk(**ov: object) -> InvitedTalk:
    return InvitedTalk(
        year=2024, year_str="2024",
        topic="software engineering", subtitle="reproducibility",
        venue="MIT CSAIL",
    )._replace(**ov)  # type: ignore[arg-type]


def _role(**ov: object) -> LeadershipRole:
    return LeadershipRole(
        year=2024, year_str="2024",
        role="Steering Committee Member",
        description="Sets venue policy",
        society="ACM SIGSOFT",
    )._replace(**ov)  # type: ignore[arg-type]


def _media(**ov: object) -> MediaAppearance:
    return MediaAppearance(
        year=2024, year_str="2024",
        title="Episode title", venue="The Secure Developer",
        url="https://example.com/x",
    )._replace(**ov)  # type: ignore[arg-type]


class TestInvitedTalksIR:
    def test_empty_returns_empty(self) -> None:
        assert render_invited_talks_blocks([]) == []

    def test_returns_list_items(self) -> None:
        blocks = render_invited_talks_blocks([_talk(), _talk()])
        assert all(isinstance(b, ir.ListItem) for b in blocks)
        assert len(blocks) == 2

    def test_list_item_codes_are_c6_n(self) -> None:
        blocks = render_invited_talks_blocks([_talk(), _talk(), _talk()])
        codes = [b.code for b in blocks if isinstance(b, ir.ListItem)]
        assert codes == ["C.6.1", "C.6.2", "C.6.3"]

    def test_byte_identical_to_legacy(self) -> None:
        talks = [_talk(venue="A"), _talk(venue="B"), _talk(venue="C")]
        ir_buf = io.StringIO()
        ir_buf.write(RtfWriter().render(render_invited_talks_blocks(talks)))
        legacy_buf = io.StringIO()
        render_invited_talks_section(talks, legacy_buf, suppress_heading=True)
        assert ir_buf.getvalue() == legacy_buf.getvalue()


class TestLeadershipRolesIR:
    def test_empty_returns_empty(self) -> None:
        assert render_leadership_roles_blocks([]) == []

    def test_byte_identical_to_legacy(self) -> None:
        roles = [_role(year_str="2022"), _role(year_str="2024")]
        ir_buf = io.StringIO()
        ir_buf.write(RtfWriter().render(render_leadership_roles_blocks(roles)))
        legacy_buf = io.StringIO()
        render_leadership_section(roles, legacy_buf, suppress_heading=True)
        assert ir_buf.getvalue() == legacy_buf.getvalue()


class TestMediaAppearancesIR:
    def test_empty_returns_empty(self) -> None:
        assert render_media_appearances_blocks([]) == []

    def test_byte_identical_to_legacy(self) -> None:
        media = [_media(title="A"), _media(title="B", venue="")]
        ir_buf = io.StringIO()
        ir_buf.write(RtfWriter().render(render_media_appearances_blocks(media)))
        legacy_buf = io.StringIO()
        render_media_appearances_section(media, legacy_buf, suppress_heading=True)
        assert ir_buf.getvalue() == legacy_buf.getvalue()
