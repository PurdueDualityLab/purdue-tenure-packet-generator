"""RTF `Writer` — concrete IR → RTF translator.

Consumes `list[Block]` (from a renderer) and produces RTF identical to
what the legacy `render_X_section(out=...)` functions wrote directly.
The writer is the ONLY place outside `styles_rtf.py` that knows about
RTF control words.

The skeleton uses isinstance dispatch with `NotImplementedError`
fallback so adding a new IR type without writer support surfaces at
runtime (not silent fallthrough). As migration phases progress (Phase
4+), additional Block / Run arms come online.

Phase 2 (LANDED) — `RawRtfBlock`, `RawRun`, `Text`.
Phase 4 (LANDED) — `Table`, `BlankParagraph`, `Bookmark`, `Styled`,
`BraceScope` (Patents pilot).
"""
from __future__ import annotations

import io
from typing import IO

from .ir import (
    BlankParagraph,
    Block,
    Bold,
    Bookmark,
    BraceScope,
    Heading,
    Hyperlink,
    Image,
    Italic,
    ListItem,
    ListItemWithBody,
    Paragraph,
    RawRtfBlock,
    RawRun,
    RefLink,
    Run,
    Styled,
    SubgroupHeading,
    Superscript,
    Table,
    Text,
)


class RtfWriter:
    """Translate IR blocks to a complete RTF document body.

    The writer's job: walk `blocks`, dispatch on each block type,
    emit RTF control words + escaped text. Phase-by-phase arms come
    online via the IR refactor migration plan; an unmigrated block
    type raises `NotImplementedError`, never silently falls through.
    """

    def render(self, blocks: list[Block]) -> str:
        buf = io.StringIO()
        for block in blocks:
            self._render_block(buf, block)
        return buf.getvalue()

    def _render_block(self, out: IO[str], block: Block) -> None:
        """Dispatch on block type."""
        if isinstance(block, RawRtfBlock):
            out.write(block.rtf)
            return
        if isinstance(block, BlankParagraph):
            out.write("\\pard\\par\n")
            return
        if isinstance(block, Table):
            self._render_table(out, block)
            return
        if isinstance(block, ListItem):
            self._render_list_item(out, block)
            return
        if isinstance(block, Paragraph):
            self._render_paragraph(out, block)
            return
        if isinstance(block, Heading):
            self._render_heading(out, block)
            return
        if isinstance(block, SubgroupHeading):
            self._render_subgroup_heading(out, block)
            return
        if isinstance(block, Image):
            self._render_image(out, block)
            return
        # Phase 5+ adds the rest: ListItemWithBody. Until each
        # migration ships, the unmigrated block type raises loudly.
        raise NotImplementedError(
            f"RtfWriter does not yet support {type(block).__name__}; "
            "wrap the legacy renderer's output in `RawRtfBlock(...)` "
            "until the per-renderer migration lands "
            "(see docs/design/ir-based-emit-disentangling-260606.md)."
        )

    # ------ Block arms ------------------------------------------------

    def _render_table(self, out: IO[str], block: Table) -> None:
        """Render a Table block via the legacy `RtfTable` to preserve
        byte-identity with existing tables. `RtfTable` produces the
        \\trowd...\\row scaffolding + per-cell borders. We render each
        cell's `list[Run]` into a string and hand it to `RtfTable`.
        """
        from .rtf import RtfTable
        t = RtfTable(column_widths=block.column_widths_twips)
        if block.header:
            t.add_header([self._render_runs_to_str(cell) for cell in block.header])
        for row in block.rows:
            t.add_row([self._render_runs_to_str(cell) for cell in row])
        out.write(t.render())

    def _render_paragraph(self, out: IO[str], block: Paragraph) -> None:
        """Render a Paragraph block — delegates to the legacy
        `emit_styled` path when a style is set (`na_placeholder`,
        `intro_note`, etc.) for byte-identity with existing emit.

        When no style is set, emits a plain body-style paragraph.
        """
        from .styles import emit_styled
        body = self._render_runs_to_str(block.runs)
        if block.style:
            emit_styled(out, block.style, body, indent=block.indent_twips)
        else:
            from .styles import BODY_FONT_SIZE
            out.write(
                f"\\pard\\plain\\f0\\fs{BODY_FONT_SIZE}"
                f"\\li{block.indent_twips} {body}\\par\n"
            )

    def _render_heading(self, out: IO[str], block: Heading) -> None:
        """Render a Heading block.

        Level → renderer mapping mirrors the legacy walker:
          1 = roman_section (III. / V.)
          2 = group_heading (A. / B. / C.)
          3 = section_h1 (A.1 / C.1 / B.1)
          4 = section_h2 (A.1.1 / C.5.1 / C.16.2)

        For level 3+ the bookmark wraps the code. The legacy heading
        emitters in `rtf.py` carry per-level branching that this
        method delegates to via `_emit_*_heading`.
        """
        from .rtf import (
            _emit_group_heading,
            _emit_roman_section_heading,
            _emit_section_heading,
            _emit_subsection_heading,
        )
        if block.level == 1:
            _emit_roman_section_heading(
                out, block.code, block.title,
                suppress_page_break=block.suppress_page_break,
            )
        elif block.level == 2:
            _emit_group_heading(
                out, block.code, block.title,
                restart_numbering=block.restart_list_numbering,
            )
        elif block.level == 3:
            _emit_section_heading(out, block.code, block.title)
        elif block.level == 4:
            _emit_subsection_heading(out, block.code, block.title)
        else:
            raise ValueError(f"Heading level {block.level} not supported (1-4 only)")

    def _render_subgroup_heading(
        self, out: IO[str], block: SubgroupHeading,
    ) -> None:
        """Subgroup banner (PUBLISHED WORK, EXTERNAL VISIBILITY, ...)."""
        from .styles import emit_styled
        emit_styled(out, "subgroup_heading", block.title)

    def _render_image(self, out: IO[str], block: Image) -> None:
        from .image_embed import emit_image
        emit_image(
            out, block.path,
            indent_twips=block.indent_twips,
            max_width_inches=block.max_width_inches,
        )

    def _render_list_item(self, out: IO[str], block: ListItem) -> None:
        """Render a ListItem block — the canonical hanging-indent numbered
        list entry shape used by C.6/C.7/C.8/C.9/C.18/C.20/C.23-C.26.

        Byte-identical to `rtf._emit_list_item`: emits
        `\\pard\\plain\\f0\\fs{body}\\li{indent}\\fi{fi}\\tx{indent}
         {bookmark-anchor}{code}{end-bookmark}.\\tab {body}\\par\\par\\n`.
        The bookmark name is `{prefix}_{code-with-underscores}` if
        prefix is set, else `code-with-underscores`.
        """
        from .rtf import _label_position_for_code
        from .styles import BODY_FONT_SIZE
        label_pos = _label_position_for_code(block.code)
        fi = label_pos - block.indent_twips
        bookmark = (block.bookmark_prefix + block.code).replace(".", "_")
        out.write(
            f"\\pard\\plain\\f0\\fs{BODY_FONT_SIZE}"
            f"\\li{block.indent_twips}\\fi{fi}\\tx{block.indent_twips} "
            f"{{\\*\\bkmkstart {bookmark}}}{block.code}"
            f"{{\\*\\bkmkend {bookmark}}}.\\tab "
        )
        self._render_runs(out, block.body)
        out.write("\\par\\par\n")

    # ------ Run-level helpers ------------------------------------------

    def _render_runs_to_str(self, runs: list[Run]) -> str:
        """Render a run list to a string (for use in table cells)."""
        buf = io.StringIO()
        self._render_runs(buf, runs)
        return buf.getvalue()

    def _render_runs(self, out: IO[str], runs: list[Run]) -> None:
        for r in runs:
            self._render_run(out, r)

    def _render_run(self, out: IO[str], run: Run) -> None:
        """Dispatch on run type."""
        if isinstance(run, Text):
            from .builders import escape_rtf
            out.write(escape_rtf(run.text))
            return
        if isinstance(run, RawRun):
            out.write(run.rtf)
            return
        if isinstance(run, Bookmark):
            out.write(f"{{\\*\\bkmkstart {run.name}}}")
            self._render_runs(out, run.runs)
            out.write(f"{{\\*\\bkmkend {run.name}}}")
            return
        if isinstance(run, BraceScope):
            out.write("{")
            self._render_runs(out, run.runs)
            out.write("}")
            return
        if isinstance(run, Styled):
            self._render_styled(out, run)
            return
        # Other run types arrive in Phase 5+; keep the dispatch loud.
        raise NotImplementedError(
            f"RtfWriter does not yet support run type "
            f"{type(run).__name__}; see Phase 5+ of the IR refactor."
        )

    def _render_styled(self, out: IO[str], run: Styled) -> None:
        """Render a Styled run as `<prefix> <inner><close>`.

        Translates the style name to RTF via `styles_rtf`. The
        character-style-only invariant is enforced at the writer side:
        a paragraph style passed to `Styled` is a programmer error.
        """
        from . import styles, styles_rtf
        attrs = styles._STYLES_ATTRS[run.style_name]
        assert attrs.is_character_style, (
            f"Styled({run.style_name!r}) must be a character style; "
            f"got paragraph style — use Paragraph(style=...) instead."
        )
        prefix = styles_rtf.to_rtf_open(attrs)
        close = styles_rtf.to_rtf_close(attrs)
        # Match `styled_inline`'s exact shape: `{prefix} {text}{close}`.
        out.write(f"{prefix} ")
        self._render_runs(out, run.runs)
        out.write(close)
