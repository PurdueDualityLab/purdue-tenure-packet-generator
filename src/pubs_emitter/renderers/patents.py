"""C.19 Patents — IR-shaped renderer.

`render_patents_section_blocks(patents)` returns a `list[Block]`
representing the C.19 table. The legacy `render_patents_section(out)`
function (in `rtf.py`) writes the same RTF directly; the IR variant is
byte-identical when fed through `RtfWriter`.

Phase 4 of the IR refactor (per
`docs/design/ir-based-emit-disentangling-260606.md`). Pin tests in
`tests/test_renderers_patents.py` cover IR shape + byte-identity.
"""
from __future__ import annotations

from typing import List

from ..config import PATENT_TABLE_WIDTHS, SECTION_CODES
from ..ir import (
    BlankParagraph,
    Block,
    Bookmark,
    BraceScope,
    RawRun,
    Run,
    Styled,
    Table,
    Text,
)
from ..types import Patent


def render_patents_section_blocks(patents: List[Patent]) -> List[Block]:
    """C.19: Patents table as IR.

    Empty input → empty list (no heading emit; the heading lands from
    the markdown outline's `### C.19 ...` line via the walker).

    Each row's first cell carries a bookmark on the entry code so
    `@id` cross-refs to the patent resolve. The bookmark name comes
    from the code with `.` → `_` (matching `_ref_anchor` legacy
    behavior). The brace-scope around the styled code keeps the
    period from getting consumed by the `\\b0` close delimiter (the
    canonical CLAUDE.md trap).
    """
    if not patents:
        return []
    code_base = SECTION_CODES["Patents"]  # "C.19"
    header: list[list[Run]] = [
        [Text("Title")],
        [Text("Co-Inventors")],
        [Text("Issue Date")],
        [Text("Number")],
        [Text("Impact")],
    ]
    rows: list[list[list[Run]]] = []
    for idx, p in enumerate(patents, 1):
        entry_code = f"{code_base}.{idx}"
        bookmark_name = entry_code.replace(".", "_")
        title_cell: list[Run] = [
            BraceScope([
                Styled("entry_name", [
                    Bookmark(bookmark_name, [Text(entry_code)]),
                ]),
                Text("."),
            ]),
            Text(" "),
            Text(p.title),
        ]
        # co_inventors arrives pre-formatted with `\b`...`\b0` bold-for-me
        # markers from format_inventors — `RawRun` passes through unchanged.
        coinventors_cell: list[Run] = [RawRun(p.co_inventors)]
        rows.append([
            title_cell,
            coinventors_cell,
            [Text(p.date)],
            [Text(p.number)],
            [Text(p.impact)],
        ])
    return [
        Table(
            column_widths_twips=PATENT_TABLE_WIDTHS,
            header=header,
            rows=rows,
        ),
        BlankParagraph(),
    ]
