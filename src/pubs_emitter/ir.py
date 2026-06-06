"""Typed IR (Block + Run) for the renderer → writer pipeline.

After the IR refactor, every per-section renderer produces a
`list[Block]` instead of writing RTF directly. The `Writer` protocol
(in `writer.py`) translates blocks to a format-specific string;
`RtfWriter` (in `writer_rtf.py`) produces RTF, byte-identical to the
legacy renderer. A future `HtmlWriter` parallels the RTF writer.

Design: `docs/design/ir-based-emit-disentangling-260606.md`.

The IR is split into two hierarchies:

* **`Block`** — top-level document content: headings, paragraphs, list
  items, tables, images. Each block carries inline content as `Run`s.
* **`Run`** — inline content: literal text, bold/italic emphasis,
  ref-links to section codes, external hyperlinks, superscript
  markers. Runs nest via the `runs: list[Run]` field on Bold / Italic
  / Styled.

Escape hatches for the in-progress migration:

* `RawRtfBlock` — pass-through raw RTF at the block level. Used while
  a section's renderer is still emitting RTF strings directly; the
  walker wraps that output in a single `RawRtfBlock` so the IR path
  can run end-to-end without per-renderer migration.
* `RawRun` — pass-through raw RTF at the run level. Used for fields
  like `Patent.co_inventors` that arrive pre-formatted with bold
  markers. Eventually upstreamed into `list[Run]`; for now the writer
  treats them as RTF strings.

Both escape hatches are deleted at Phase 7 (post-migration cleanup).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ----- Run hierarchy ---------------------------------------------------


@dataclass(frozen=True)
class Run:
    """Base — every concrete run type subclasses. Frozen so the runtime
    can hash + cache subtrees during writer dispatch."""


@dataclass(frozen=True)
class Text(Run):
    """Literal text. The writer escapes RTF special characters."""
    text: str


@dataclass(frozen=True)
class RawRun(Run):
    """Pre-formatted RTF passed through unchanged.

    Used for fields that arrive already-marked (e.g.
    `Patent.co_inventors`, `Citation.authors_rtf` — both contain bold
    markers from `format_inventors` / `format_author`). An `HtmlWriter`
    would have to refuse this — it's an RTF-only escape hatch.
    """
    rtf: str


@dataclass(frozen=True)
class Bold(Run):
    """Boldface emphasis wrapping nested runs."""
    runs: list[Run]


@dataclass(frozen=True)
class Italic(Run):
    """Italic emphasis wrapping nested runs."""
    runs: list[Run]


@dataclass(frozen=True)
class Styled(Run):
    """Apply a named `StyleAttrs` to nested runs.

    Character-style only — the StyleAttrs `is_character_style` field
    invariant is enforced by the writer (raises if the named style is
    a paragraph style).
    """
    style_name: str
    runs: list[Run]


@dataclass(frozen=True)
class RefLink(Run):
    """A cross-reference to a section code.

    `display` is what the reader sees (typically the same as `code`,
    e.g. `"C.4.7"`); `code` is the bookmark target. Empty `code` (after
    resolution failure) renders display as plain text — the writer
    handles the fallback.
    """
    code: str
    display: str


@dataclass(frozen=True)
class Hyperlink(Run):
    """External URL hyperlink wrapping a display label."""
    url: str
    display: str


@dataclass(frozen=True)
class Superscript(Run):
    """Author marker (`*`, `G`, `U`, `#`) rendered as superscript."""
    text: str


@dataclass(frozen=True)
class Bookmark(Run):
    """Bookmark anchor for cross-reference targeting.

    The writer emits `{\\*\\bkmkstart NAME}` before the inner runs and
    `{\\*\\bkmkend NAME}` after. Used by list-item codes (e.g. wrapping
    `"C.19.3"` so `RefLink(code="C.19.3", display="...")` can target
    it).
    """
    name: str
    runs: list[Run]


@dataclass(frozen=True)
class BraceScope(Run):
    """Brace-scope a run sequence (`{` + inner + `}`).

    Used for direct-format-override safety: when an inner Styled run
    has a trailing `\\b0` close marker, the brace-scope prevents the
    delimiter-eats-space trap (the `\\b0 X` pattern where Word swallows
    the space after `\\b0`). The canonical site is the Patents /
    Grants leading entry-code cell: `{<bold>C.X.N</bold>.}` — the
    period stays inside the brace-scope so it doesn't get consumed.
    """
    runs: list[Run]


# ----- Block hierarchy --------------------------------------------------


@dataclass(frozen=True)
class Block:
    """Base — every concrete block type subclasses."""


@dataclass(frozen=True)
class Heading(Block):
    """A section / sub-section heading.

    `level` mirrors the markdown depth conventions used by the walker:
        1 = Roman section (III. / V.)
        2 = Group heading (A. / B. / C.)
        3 = Section heading (A.1 / C.19 / B.3)
        4 = Sub-section heading (A.1.1 / C.16.2.3 / C.5.1)
    """
    level: int
    code: str
    title: str
    bookmark: Optional[str] = None
    suppress_page_break: bool = False
    restart_list_numbering: bool = False


@dataclass(frozen=True)
class SubgroupHeading(Block):
    """A subgroup banner (PUBLISHED WORK, EXTERNAL VISIBILITY, RESEARCH
    GRANTS AND CONTRACTS AWARDED, MENTORING, LEARNING, TECHNOLOGY
    TRANSFER, SERVICE).

    These don't have a `code` prefix — they're cross-cutting framers
    between section groups, rendered with the `subgroup_heading`
    style.
    """
    title: str


@dataclass(frozen=True)
class Paragraph(Block):
    """Free-form prose paragraph at the given indent.

    `style` references a named `StyleAttrs` key (e.g., `"intro_note"`,
    `"na_placeholder"`) — `None` means use the default body style.
    """
    runs: list[Run]
    indent_twips: int = 0
    first_line_indent_twips: int = 0
    style: Optional[str] = None


@dataclass(frozen=True)
class ListItem(Block):
    """A hanging-indent numbered list entry.

    Renders as `{code}.\\tab body` at `indent_twips`. The code is
    wrapped in a bookmark so cross-refs can target it.
    """
    code: str
    body: list[Run]
    indent_twips: int
    bookmark_prefix: str = ""


@dataclass(frozen=True)
class ListItemWithBody(Block):
    """Two-paragraph variant: header line + indented body paragraph.

    Used by C.1 Key Works (header = bib citation, body = explanation)
    and C.22 Software Products (header = product line, body =
    description).
    """
    code: str
    header: list[Run]
    body_paragraph: list[Run]
    indent_twips: int


@dataclass(frozen=True)
class Table(Block):
    """N-column table. Widths in twips.

    `header` is a single row of header cells (each cell is `list[Run]`,
    typically a single `Text` wrapped in `Bold` by the writer).
    `rows` is `list[row]` where each `row` is `list[cell]` where each
    `cell` is `list[Run]`.
    """
    column_widths_twips: list[int]
    header: list[list[Run]]
    rows: list[list[list[Run]]]


@dataclass(frozen=True)
class Image(Block):
    """Embedded picture (PNG/JPEG) at the given indent.

    `path` is repo-relative (resolves via `image_embed.emit_image`'s
    `project_root` parameter at writer time).
    """
    path: str
    indent_twips: int
    max_width_inches: float = 5.0


@dataclass(frozen=True)
class BlankParagraph(Block):
    """An empty `\\par` for vertical spacing."""


@dataclass(frozen=True)
class RawRtfBlock(Block):
    """Escape hatch — preserves a raw RTF string in the block stream.

    Used during migration (Phase 3 onwards) so the IR can run
    end-to-end without per-renderer migration: the walker wraps each
    unmigrated section's RTF output in a single `RawRtfBlock`, and the
    `RtfWriter` writes it through unchanged.

    Goal: shrink the population of `RawRtfBlock` to zero across waves
    A-E (Phase 5). The type is deleted in Phase 7 cleanup once no
    renderer reaches for it.
    """
    rtf: str


# ----- Helpers used by tests and renderers ----------------------------


def _runs(*items: object) -> list[Run]:
    """Tiny convenience: `_runs("hello", Bold([Text("world")]))` →
    `[Text("hello"), Bold([Text("world")])]`. Strings become `Text`;
    everything else passes through.

    Kept off the dataclasses to avoid mutation surface. Use directly
    in test setups where the verbosity tax of explicit `Text(...)`
    wrapping isn't paying for itself.
    """
    out: list[Run] = []
    for it in items:
        if isinstance(it, str):
            out.append(Text(it))
        elif isinstance(it, Run):
            out.append(it)
        else:
            raise TypeError(f"_runs() argument {it!r} is not str or Run")
    return out
