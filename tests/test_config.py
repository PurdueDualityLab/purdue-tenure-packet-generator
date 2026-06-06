"""Invariants over `pubs_emitter.config` constants.

Lightweight pin tests for the SECTION_CODES / SECTION_HEADINGS dicts —
catches drift in the source-of-truth registries before it hits the
rendered packet.
"""
from __future__ import annotations

from pubs_emitter.config import SECTION_CODES, SECTION_HEADINGS


def test_every_c_x_section_heading_ends_in_period() -> None:
    """Every C.X heading text MUST end in a period (Purdue P&T template
    convention — matches the screenshots the user shared for the
    template's heading style).

    Walks every SECTION_HEADINGS entry whose corresponding SECTION_CODES
    value begins with "C." and asserts the heading text ends with `.`.
    Failing entries are listed in the assertion message so the fixer
    sees the full set in one shot.
    """
    missing = []
    for key, heading in SECTION_HEADINGS.items():
        code = SECTION_CODES.get(key, "")
        if not code.startswith("C."):
            continue
        if not heading.endswith("."):
            missing.append(f"{code} ({key!r}): {heading!r}")
    assert not missing, (
        "C.X section headings missing trailing period:\n  "
        + "\n  ".join(missing)
    )
