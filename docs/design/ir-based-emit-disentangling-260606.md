# IR-based emit — disentangling the renderer from RTF

## Problem

After the markdown-master refactor (Phases 1–4,
[design](markdown-master-outline-refactor.md)), the rendering
architecture is:

```
outline.md ──▶ walker.parse ──▶ AST [HeadingNode, ParagraphNode, DirectiveNode]
                                    │
                                    ▼
                          walker.walk_section_prose
                                    │
                ┌───────────────────┼───────────────────┐
                ▼                   ▼                   ▼
   _emit_heading_via_legacy   _emit_paragraph     _dispatch_directive
                │                   │                   │
                ▼                   ▼                   ▼
        write RTF to out    write RTF to out    directive writes RTF to out
                                                        │
                                                        ▼
                                               render_X function writes RTF
```

The walker has an AST in its parse step, but **every emit path drops
directly to RTF strings**:

- `_emit_heading_via_legacy` calls `_emit_section_heading` /
  `_emit_group_heading` / `_emit_roman_section_heading` which write
  RTF (`\pard\plain\f0...\par`) directly to the output buffer.
- `_emit_paragraph` resolves `@ref`/`#macro` tokens and runs
  `_markdown_inline_to_rtf` (which emits `\b...\b0`, `\i...\i0`), then
  writes the resulting RTF string.
- Every directive in [`directives.py`](../../src/pubs_emitter/directives.py)
  calls back into a legacy `render_X` function that writes RTF
  directly via `out.write(...)`.
- Tables (`RtfTable`), list items (`_emit_list_item`), images
  (`image_embed.emit_image`) — all emit RTF.

Consequences:

1. **The walker's AST is half-baked.** It exists for the markdown
   *input* layer but doesn't extend through the emit layer. Any
   "render this packet as HTML" or "render this packet as LaTeX"
   need would require parallel paths through every `render_X`
   function — ~25 functions × per-format duplicates.
2. **Bugs entangle across format and logic.** A bug in
   `render_grants_section`'s row construction is also a bug in its
   RTF emission, because they're the same function. Fixing the row
   logic without re-checking the RTF escapements is a class of
   regression. (Q from user 260606: "entangling entails errors.")
3. **Standalone module testing is harder than it should be.** To
   test that `_format_grant_amounts` produces the right amount cell,
   the test asserts on an RTF substring (`"$200,000"`), so
   refactoring the RTF output forces test rewrites. If the renderer
   returned typed IR, the test could assert on the structural shape
   (`Cell(text="$200,000", alignment="right")`) and changes to the
   RTF dialect wouldn't cascade.
4. **Hyperlink finalization is a post-pass over the rendered RTF.**
   `_finalize_ref_hyperlinks` walks the emitted RTF looking for
   sentinel chars (`\x01CODE\x02`) and converts them to
   `{\field{\*\fldinst HYPERLINK "..."}}` blocks. This works but is
   structurally backwards: the renderer knows it's emitting a ref;
   the post-pass has to recover that from sentinel bytes. With IR,
   `RefLink` is a typed Run; the writer translates it directly to
   the HYPERLINK field — no sentinel round-trip.

## Goal

Insert a **typed `Block` IR** between the walker/directive layer and
the format-specific writer. Renderers return `list[Block]` (or a
generator that yields `Block`s); a `Writer` translates blocks to RTF
(or eventually HTML / LaTeX / JSON).

After the refactor:

```
outline.md ──▶ walker.parse ──▶ AST
                                    │
                                    ▼
                          walker.walk(AST, ctx) ──▶ list[Block]
                                                          │
                                                          ▼
                                              RtfWriter.render(blocks) ──▶ RTF str
                                              HtmlWriter.render(blocks) ──▶ HTML str  (future)
                                              LatexWriter.render(blocks) ──▶ LaTeX str (future)
```

Each renderer (`render_grants_section`, `render_patents_section`,
etc.) becomes a **pure function** from data to IR:

```python
def render_patents_section(patents: list[Patent]) -> list[Block]:
    if not patents:
        return []
    return [
        Heading(level=3, code="C.19", title="Issued U.S. ..."),
        Table(
            widths=[2400, 2000, 1600, 1500, 1860],
            header=["Title", "Co-Inventors", "Issue Date", "Number", "Impact"],
            rows=[
                [
                    [Text(p.title)],
                    [RawRun(p.co_inventors)],  # already-formatted bib markers
                    [Text(p.date)],
                    [Text(p.number)],
                    [Text(p.impact)],
                ]
                for p in patents
            ],
        ),
    ]
```

The renderer doesn't know what RTF is; it just declares structure.
The `RtfWriter` knows how to render `Heading` / `Table` /
`Text` / `RawRun` to RTF.

## Non-goals

- **Not implementing HTML / LaTeX writers in this refactor.** This
  refactor delivers the substrate — the typed IR + a faithful
  `RtfWriter` that produces byte-identical output to today's
  renderer. HTML / LaTeX implementations are follow-up work.
- **Not changing rendered packet output.** Every section's RTF emit
  stays byte-identical at every migration step.
- **Not removing existing helpers (`escape_rtf`, `styled_inline`,
  `_emit_list_item`).** They get pushed inside the writer; the
  per-renderer surface stops calling them.
- **Not pursuing an exhaustive IR vocabulary upfront.** The IR
  starts with what the existing renderers need; new Block types
  arrive as new use cases need them. A `RawRtfBlock` escape hatch
  exists during migration so the writer can pass through anything
  the IR doesn't yet model.

## Design

### Block IR

```python
# src/pubs_emitter/ir.py — pure data types, no behavior

@dataclass(frozen=True)
class Block:
    """Base; every concrete block type subclasses."""

@dataclass(frozen=True)
class Heading(Block):
    """A section / sub-section heading.

    `level` mirrors the markdown depth conventions:
        1 = Roman section (III. / V.)
        2 = Group heading (A. / B. / C.)
        3 = Section heading (A.1 / C.19 / B.3)
        4 = Sub-section heading (A.1.1 / C.16.2.3)
    """
    level: int
    code: str
    title: str
    # Optional bookmark — used for ref-link targets. Defaults to
    # code.replace(".", "_") when None.
    bookmark: Optional[str] = None
    # Roman-section-only: suppress the page break before this heading.
    suppress_page_break: bool = False
    # Group-heading-only: restart list numbering at this heading.
    restart_list_numbering: bool = False

@dataclass(frozen=True)
class SubgroupHeading(Block):
    """A subgroup banner: PUBLISHED WORK, EXTERNAL VISIBILITY,
    RESEARCH GRANTS …, MENTORING, LEARNING, TECHNOLOGY TRANSFER,
    SERVICE. These don't have a code prefix; they're cross-cutting
    framers between section groups."""
    title: str

@dataclass(frozen=True)
class Paragraph(Block):
    """Free-form prose at the given indent."""
    runs: list[Run]
    indent_twips: int = 0
    first_line_indent_twips: int = 0
    style: Optional[str] = None  # StyleAttrs key, e.g. "intro_note"

@dataclass(frozen=True)
class ListItem(Block):
    """A hanging-indent numbered list entry.
        `{code}.\\tab body`
    """
    code: str
    body: list[Run]
    indent_twips: int
    bookmark_prefix: str = ""  # e.g. "V." for under-review entries

@dataclass(frozen=True)
class ListItemWithBody(Block):
    """Two-paragraph variant: header line + indented body paragraph.
    Used by C.1 Key Works and C.22 Software Products."""
    code: str
    header: list[Run]
    body_paragraph: list[Run]
    indent_twips: int

@dataclass(frozen=True)
class Table(Block):
    """N-column table. Widths in twips. Each row is a list of cells;
    each cell is a list of Runs."""
    column_widths_twips: list[int]
    header: list[list[Run]]
    rows: list[list[list[Run]]]

@dataclass(frozen=True)
class Image(Block):
    """Embedded picture at the given indent."""
    path: str
    indent_twips: int
    max_width_inches: float = 5.0

@dataclass(frozen=True)
class BlankParagraph(Block):
    """An empty paragraph for vertical spacing."""

@dataclass(frozen=True)
class RawRtfBlock(Block):
    """Escape hatch — preserves a raw RTF string in the block stream.

    Used during migration so the IR doesn't need to model every
    construct the renderer emits today. Each migration wave shrinks
    the population of `RawRtfBlock` calls; once no renderer reaches
    for it, the type can be removed.
    """
    rtf: str
```

### Run IR

```python
@dataclass(frozen=True)
class Run:
    """Inline content. Every Run is composable; Bold/Italic/Styled
    take sub-Runs so emphasis can nest."""

@dataclass(frozen=True)
class Text(Run):
    """Literal text. The writer escapes it."""
    text: str

@dataclass(frozen=True)
class RawRun(Run):
    """Pre-formatted RTF passed through unchanged. Used for fields
    like Patent.co_inventors that arrive already-marked with bold
    spans. The HtmlWriter would have to refuse this — it's an
    RTF-only escape hatch."""
    rtf: str

@dataclass(frozen=True)
class Bold(Run):
    """Boldface emphasis."""
    runs: list[Run]

@dataclass(frozen=True)
class Italic(Run):
    """Italic emphasis."""
    runs: list[Run]

@dataclass(frozen=True)
class Styled(Run):
    """Apply a named StyleAttrs (see generic-stylesheet-abstraction-260606.md).
    Char-style only — the StyleAttrs `is_character_style` field
    invariant is enforced by the writer."""
    style_name: str
    runs: list[Run]

@dataclass(frozen=True)
class RefLink(Run):
    """A cross-reference to a section code. The writer translates to
    a Word HYPERLINK field targeting the code's bookmark.

    `display` is what the reader sees; `code` is the target. Empty
    `code` (after resolution failure) renders display as plain text."""
    code: str
    display: str

@dataclass(frozen=True)
class Hyperlink(Run):
    """External URL hyperlink."""
    url: str
    display: str

@dataclass(frozen=True)
class Superscript(Run):
    """Author marker (*, G, U, #) rendered as superscript."""
    text: str

@dataclass(frozen=True)
class Bookmark(Run):
    """Bookmark anchor for cross-reference targeting. Wraps inner runs
    inside the bookmark span; the writer emits `\\bkmkstart NAME` +
    inner + `\\bkmkend NAME`."""
    name: str
    runs: list[Run]
```

### Writer protocol

```python
# src/pubs_emitter/writer.py — abstract protocol

class Writer(Protocol):
    def render(self, blocks: list[Block]) -> str: ...

# src/pubs_emitter/writer_rtf.py — RTF implementation

class RtfWriter:
    """Render a list[Block] to a complete RTF document body.

    The writer consults `styles.STYLES` (post-styles-refactor; see
    generic-stylesheet-abstraction-260606.md) to translate
    `style_name` keys to RTF control words.
    """

    def __init__(self, *, ref_index: dict[str, str], ...) -> None:
        ...

    def render(self, blocks: list[Block]) -> str:
        buf = io.StringIO()
        for block in blocks:
            self._render_block(buf, block)
        # Final pass: convert any RefLink sentinel form to HYPERLINK
        # fields. (Eliminated once RefLink is rendered directly.)
        return buf.getvalue()

    def _render_block(self, out: IO[str], block: Block) -> None:
        if isinstance(block, Heading):
            self._render_heading(out, block)
        elif isinstance(block, Paragraph):
            ...
        # one method per block kind
```

A future `HtmlWriter` parallels `RtfWriter` — same `render(blocks)
-> str` signature, different output.

### Walker becomes IR-producing

```python
# src/pubs_emitter/section_walker.py — POST-refactor

def walk_to_blocks(
    text: str, ctx: RenderContext,
) -> list[Block]:
    """Parse outline markdown, dispatch directives, produce IR blocks."""
    nodes = parse_section_prose(text)
    _augment_ref_index(ctx, _collect_declared_codes(nodes))
    blocks: list[Block] = []
    current_code: Optional[str] = None
    for node in nodes:
        if isinstance(node, HeadingNode):
            current_code = node.code
            blocks.append(Heading(level=node.depth, code=node.code, title=node.title))
        elif isinstance(node, ParagraphNode):
            runs = _resolve_paragraph_to_runs(ctx, node.text)
            indent = _body_indent_for_code(current_code) if current_code else 0
            blocks.append(Paragraph(runs=runs, indent_twips=indent))
        elif isinstance(node, DirectiveNode):
            blocks.extend(DIRECTIVES[node.name](ctx))
    return blocks
```

### Directive contract changes

```python
# src/pubs_emitter/directives.py — POST-refactor

# Old shape: (ctx, out) -> None  (writes RTF to out)
# New shape: (ctx) -> list[Block]

DIRECTIVES: dict[str, Callable[[RenderContext], list[Block]]] = {
    "PATENTS_TABLE": directive_patents_table,
    "INVITED_TALKS": directive_invited_talks,
    ...
}

def directive_patents_table(ctx: RenderContext) -> list[Block]:
    """Return the IR for the C.19 Patents table."""
    from .renderers import render_patents_section_blocks
    return render_patents_section_blocks(ctx.patents or [])
```

### Renderer contract changes

Each `render_X_section` returns `list[Block]` instead of writing to
`out`:

```python
# Before
def render_patents_section(patents: list[Patent], out: IO[str], *, suppress_heading=False) -> None:
    if not patents:
        return
    if not suppress_heading:
        _emit_section_heading(out, "C.19", ...)
    table = RtfTable(...)
    table.add_header([...])
    for p in patents:
        table.add_row([...])
    out.write(table.render())
    out.write("\\pard\\par\n")

# After
def render_patents_section_blocks(patents: list[Patent]) -> list[Block]:
    if not patents:
        return []
    return [
        Table(
            column_widths_twips=[2400, 2000, 1600, 1500, 1860],
            header=[
                [Text("Title")], [Text("Co-Inventors")],
                [Text("Issue Date")], [Text("Number")], [Text("Impact")],
            ],
            rows=[
                [
                    [Bookmark(f"C_19_{i}", [Text(f"C.19.{i}.")]), Text(" "), Text(p.title)],
                    [RawRun(p.co_inventors)],
                    [Text(p.date)],
                    [Text(p.number)],
                    [Text(p.impact)],
                ]
                for i, p in enumerate(patents, 1)
            ],
        ),
        BlankParagraph(),
    ]
```

Note that `suppress_heading` is gone — the renderer doesn't emit the
heading; the walker emits it from the markdown `### C.19 …` line.
This is cleaner than the current "every render_X has a
`suppress_heading` parameter" workaround.

### Hyperlink finalization

Today's sentinel-then-post-pass approach goes away:

```python
# Today: writer emits "\x01C.19.3\x02"; _finalize_ref_hyperlinks
# walks the emitted RTF looking for sentinels, converts to HYPERLINK.

# After: RtfWriter._render_run for RefLink emits the HYPERLINK
# field directly. No sentinel, no post-pass.

def _render_run(self, out: IO[str], run: Run) -> None:
    if isinstance(run, RefLink):
        bookmark = run.code.replace(".", "_")
        out.write(
            f"{{\\field{{\\*\\fldinst HYPERLINK \\l \"{bookmark}\"}}"
            f"{{\\fldrslt {{\\cs1\\cf1\\ul {escape_rtf(run.display)}}}}}}}"
        )
```

This is the load-bearing structural win — the renderer expresses
intent (`RefLink(code, display)`) and the writer expresses the
RTF-specific encoding. No string-substitution post-pass.

### Module layout

```
src/pubs_emitter/
├── ir.py                # NEW — Block + Run types
├── writer.py            # NEW — Writer protocol
├── writer_rtf.py        # NEW — RtfWriter implementation
├── renderers/           # NEW — per-section block builders
│   ├── __init__.py
│   ├── patents.py       # render_patents_section_blocks
│   ├── grants.py        # render_grants_section_blocks
│   ├── students.py
│   ├── candidate_info.py
│   ├── citations.py     # C.1-C.5 generic-loop body
│   └── ...
├── rtf.py               # SHRINKS — high-level write_rtf orchestration
│                        # only; per-section emit moves to renderers/
├── styles.py            # POST-styles-refactor (companion doc)
└── styles_rtf.py        # POST-styles-refactor
```

After the refactor, `rtf.py` is much smaller (a few hundred lines,
mostly the document header / footer scaffolding + the
`write_rtf` orchestration). Each renderer is a standalone file with
its own unit tests against IR — no RTF strings in the test
assertions.

## Migration plan

Eight phases. The first three build substrate; phases 4–7 migrate
renderers one cluster at a time; phase 8 cleans up.

**Prerequisite:** Generic stylesheet abstraction
([`generic-stylesheet-abstraction-260606.md`](generic-stylesheet-abstraction-260606.md))
must land first. The IR's `Styled` run type references style names
that need to translate to RTF; without `StyleAttrs`, the writer
would have to import raw RTF strings from `STYLES`, defeating the
disentangling goal.

### Phase 1 — IR vocabulary + RawRtfBlock escape hatch

**Scope:** Author `ir.py` with all Block + Run types listed above.
Add `RawRtfBlock` and `RawRun` as escape hatches that let the writer
pass through raw RTF for things not yet IR-modeled.

**Deliverables:**

- `src/pubs_emitter/ir.py` with the type vocabulary.
- 25+ unit tests covering construction + equality for each Block /
  Run type.
- No production code change.

**Exit criteria:** IR types defined; no callers yet.

### Phase 2 — `RtfWriter` skeleton with RawRtfBlock-only support

**Scope:** Author `writer.py` (Writer protocol) and `writer_rtf.py`
(RtfWriter class). Initially it supports ONLY `RawRtfBlock` and
`RawRun` — every block it sees gets passed through verbatim.

**Deliverables:**

- `src/pubs_emitter/writer.py` (protocol).
- `src/pubs_emitter/writer_rtf.py` (RtfWriter, single-block support).
- Test: `RtfWriter().render([RawRtfBlock("\\par hello\\par\n")])`
  produces `"\\par hello\\par\n"`.

**Exit criteria:** A no-op writer exists. No production change yet.

### Phase 3 — Walker IR-bridge

**Scope:** Add `walk_to_blocks` alongside `walk_section_prose`. The
old function stays for the legacy emit path; the new one produces
IR. Initially every paragraph / directive returns a single
`RawRtfBlock` containing the same RTF the legacy path would have
written. This is a zero-behavioral-change wrapping.

**Deliverables:**

- `walk_to_blocks(text, ctx) -> list[Block]` in `section_walker.py`.
- For each existing directive, also register an
  `<NAME>_BLOCKS_DIRECTIVES` entry that returns `[RawRtfBlock(...)]`.
- Test: `walk_to_blocks(outline_text, ctx)` followed by
  `RtfWriter().render(blocks)` produces byte-identical output to
  the current `walk_section_prose` path.

**Exit criteria:** Two parallel paths (legacy vs IR-bridge) produce
identical RTF. CLI / `write_rtf` still uses the legacy path.

### Phase 4 — Pilot migration: C.19 Patents

**Scope:** Migrate the Patents directive + renderer to return real
Block IR (not `RawRtfBlock`). The `RtfWriter` gains support for
`Table`, `Text`, `RawRun`, `Bookmark`.

**Deliverables:**

- `renderers/patents.py` with `render_patents_section_blocks`.
- `directives.py`: `_directive_patents_table` returns
  `render_patents_section_blocks(ctx.patents)`.
- `writer_rtf.py`: adds `_render_table`, `_render_text`, `_render_bookmark`.
- Pin test: walker output for C.19 byte-identical to pre-refactor.

**Exit criteria:** C.19 emits via the IR path with no
`RawRtfBlock`. Other sections still go through `RawRtfBlock`.

### Phase 5 — Bulk renderer migration

**Scope:** Migrate the remaining ~24 renderers one at a time. Each
migration:

1. Write `renderers/<section>.py` with the pure-data renderer.
2. Update the directive to call it.
3. Extend `RtfWriter` if new Block / Run types are needed.
4. Pin test: byte-identical output for this section.

Migration order (lowest risk → highest):

- **Wave A — simple list sections** (C.6-C.9, C.20, C.18, C.15,
  C.23-C.26): each ~30 min.
- **Wave B — table sections** (C.17, C.21, C.22, C.14): ~45 min
  each.
- **Wave C — grants** (C.10-C.13): need `IntroNote` (italic intro
  line) + 4-row grant-table shape; ~60 min each.
- **Wave D — citation sections** (C.1, C.2, C.3, C.4, C.5): involve
  `render_citation` which is the most complex run — author markers,
  venue italic, year, ref-links. This is the biggest single
  conversion (~2-4 hours).
- **Wave E — front matter + appendix** (A.1-A.7, B.1-B.5, V.A.1,
  V.A.2): bundled directives convert to bundled renderers.

**Deliverables per wave:** new renderer file + writer extensions +
pin tests.

**Exit criteria:** every directive returns real IR; no
`RawRtfBlock` remains anywhere.

### Phase 6 — RefLink direct rendering

**Scope:** Remove the sentinel-then-post-pass hyperlink machinery.
`RtfWriter._render_run` for `RefLink` emits the HYPERLINK field
directly. `resolve_refs(..., link_format=True)` stops emitting
sentinel chars; instead it returns structured `RefLink` runs.

**Deliverables:**

- `resolve_refs` produces `list[Run]` instead of a sentinel-laden
  string.
- `_finalize_ref_hyperlinks` deleted.
- `REF_LINK_OPEN` / `REF_LINK_CLOSE` sentinel constants deleted.

**Exit criteria:** No sentinel bytes anywhere in the emit pipeline.
Byte-identical RTF output.

### Phase 7 — Cleanup

**Scope:**

- Delete `RawRtfBlock` and `RawRun` (no remaining callers).
- Delete legacy `walk_section_prose` (replaced by `walk_to_blocks +
  RtfWriter`).
- Delete legacy `render_X_section` functions (replaced by
  `renderers/<section>.render_X_blocks`).
- `write_rtf` body collapses to: build header → walker.walk →
  RtfWriter.render → write to disk.

**Exit criteria:** the codebase has ONE emit path: data →
renderer → IR → writer → RTF.

### Phase 8 — (FUTURE; out of scope) HTML writer

Author `writer_html.py` with the same shape as `writer_rtf.py`. The
IR is unchanged; only the per-Block-type translation is new. The CLI
gains a `--format` flag.

## Test strategy

Four layers:

1. **IR construction tests** (Phase 1) — typed Block / Run equality,
   nesting validity.
2. **Writer pin tests** (Phase 2-5) — per-Block-type RTF output. For
   each Block type, a focused test: `RtfWriter().render([block]) ==
   expected_rtf`.
3. **Renderer pin tests** (Phase 4-5) — for each `render_X_blocks`,
   assert the IR shape matches expectations. E.g.,
   `render_patents_section_blocks([sample_patent])[0] ==
   Table(widths=[...], header=[...], rows=[[...]])`. **These tests
   no longer mention RTF.**
4. **E2E byte-identity** — full build via the IR path produces
   byte-identical RTF to pre-refactor.

The biggest test-quality win is layer 3: renderer tests stop being
RTF-shaped. Today's `test_rtf.py` has thousands of lines of "this
RTF substring should appear"; after the refactor, those become
"this IR shape should be produced" assertions, decoupled from RTF
dialect. RTF-shape changes only affect writer tests.

## Risks

**R1: `render_citation` is the hardest conversion.** It produces a
fully-formatted citation string with author markers, venue italic,
year, page count, ref-links, link fields — all entangled in one
output string today. Mitigation: model it as a function
`render_citation_runs(cit, …) -> list[Run]` that returns a
composable run sequence. The `ListItem` block then carries those
runs. Risk: subtle whitespace / punctuation differences.

**R2: `Patent.co_inventors` and similar "pre-baked RTF" fields.**
These arrive already-marked with `\b ...\b0` from `format_inventors`
because they need the bold-for-me convention. The clean fix:
upstream `format_inventors` to return `list[Run]` instead of an RTF
string. The pragmatic fix during migration: use `RawRun` so the
writer passes them through. Long-term: convert the upstream
formatter too.

**R3: Cell border / table-row layout details.** Table rows today
involve `\trowd\trgaph108\trleft0` + per-cell border block +
`\cellx` + `\pard\intbl` + cell content + `\cell` + row close. The
`RtfWriter._render_table` needs to faithfully replicate this. The
border block specifically — `_CELL_BORDER_BLOCK` from
[rtf.py](../../src/pubs_emitter/rtf.py) — needs a clear home (maybe
a `border_style` field on `Table`, maybe a separate `CellBorder`
attribute). Decide as part of Phase 4.

**R4: Tests against current RTF outputs lock in dialectal choices.**
Today's pin tests assert on exact RTF strings. Migrating those
tests to IR shape is a chunky one-time cost. Mitigation: pin tests
against IR at the renderer surface; pin tests against RTF at the
writer surface. Existing RTF pin tests stay but move to the writer
test file. ~1 day of test reorganization.

**R5: Lateral changes to `_emit_*` helpers.** Tests / external uses
of `_emit_list_item`, `_emit_section_heading`, etc. will break when
those helpers move inside `RtfWriter`. Audit before deletion;
provide a deprecation shim if needed during the migration.

**R6: This is a big refactor.** Honest scope: 4-7 days of focused
work to land Phases 1-7. Compresses to 3-4 days if the work is
purely mechanical; extends to 7-10 days if `render_citation`'s
complexity turns out to be deeper than expected.

## Rollback story

Phases 1-3 are pure additions — fully revertible.

Phases 4-5 migrate one renderer at a time. Each migration is an
independent commit; a problematic migration can be reverted without
affecting other migrations. The `RawRtfBlock` escape hatch means a
partial-migration state (some renderers via IR, others via
`RawRtfBlock`) is always shippable.

Phase 6 (sentinel removal) is the only risky cleanup. Land it with
a feature flag (`use_sentinel_hyperlinks: bool = False` default)
for one release if there's anxiety; flip the default after a
verification cycle.

Phase 7 (cleanup) is large but mechanical — delete dead code.
Revertible commit-by-commit.

## Type discipline (for ALL new code in this refactor)

The IR refactor introduces ~10+ new modules — `ir.py`, `writer.py`,
`writer_rtf.py`, `renderers/*.py`. This is the **largest typed
surface the codebase has ever added at once**, and the right moment
to ship the new surface under strict typing AND a test-suite gate.
Current mypy state (audited 2026-06-06): gradual mode, no
pytest/CI gate, 22 unrepaired errors in production code.

New modules from this refactor ship under strict mode:

1. **`mypy --strict` per-module override.** `pyproject.toml` extends
   the strict block introduced by the styles refactor:

   ```toml
   [[tool.mypy.overrides]]
   module = [
       "pubs_emitter.styles",
       "pubs_emitter.styles_rtf",
       "pubs_emitter.ir",
       "pubs_emitter.writer",
       "pubs_emitter.writer_rtf",
       "pubs_emitter.renderers.*",
   ]
   disallow_untyped_defs = true
   disallow_untyped_calls = true
   disallow_any_explicit = true
   warn_return_any = true
   no_implicit_reexport = true
   ```

2. **mypy gated by `tests/test_strict_mypy.py`** (lands with the
   styles refactor; extends to cover IR modules here). Build-fail on
   any type error in the strict-mode surface.

3. **Block / Run hierarchy is `@dataclass(frozen=True, kw_only=True)`.**
   Frozen so the runtime can hash + cache them (useful for the future
   sub-tree memoization); kw_only so adding fields to a base class
   doesn't break existing callers' positional argument ordering. No
   raw `dict[str, object]` payloads. No `Any` in field annotations.

4. **`Writer` is a `typing.Protocol`.** Not an ABC — structural
   typing means a class becomes a `Writer` by having a `render`
   method with the right shape; no inheritance ceremony. The IR
   doesn't care what concrete writer it's handed to.

5. **Directive signature is fully typed.** Today's
   `Callable[[RenderContext, IO[str]], None]` becomes
   `Callable[[RenderContext], list[Block]]`. The registry is
   `DIRECTIVES: dict[str, Callable[[RenderContext], list[Block]]]` —
   typed both directions; mypy catches any directive that forgets to
   return a list or returns the wrong block subtype.

6. **`RenderContext` tightens.** Today's `Optional[list]` field per
   data type becomes `Sequence[X]` defaulting to `()` (frozen empty
   tuple). Every callsite already treats `None` as empty; removing
   `Optional` means downstream code doesn't need defensive
   `or []` patterns. This is a small but real type-safety win
   that cascades through every renderer.

7. **`Run` polymorphism via `match` statements + exhaustiveness
   checking.** The writer's per-run dispatch uses
   `match run: case Text(): ... case Bold(): ...` with a final
   `case _: assert_never(run)` that mypy checks for exhaustiveness.
   Adding a new `Run` subclass without updating the writer is a
   build-fail, not a silent runtime fallthrough.

8. **No `Any` escape hatches without justification.** Same rule as
   the styles refactor. The one expected legitimate `Any` is in
   `RenderContext.extra: dict[str, Any]` — the caller-owned bag for
   ctx fields not modeled top-level yet (today: `bib_entries`,
   `under_review_index`, `key_works`, `key_work_index`). Each access
   site narrows the type at point of use.

The cost: each migrating renderer (Wave A-E) carries a ~20% time
overhead from "make mypy happy" work. The win: every Block / Run /
Writer interaction is compiler-checked. The disentangling goal — bugs
partition by responsibility — gets its strongest enforcement here.

## How this composes with the styles refactor

[`generic-stylesheet-abstraction-260606.md`](generic-stylesheet-abstraction-260606.md)
is the prerequisite. With `StyleAttrs` in place, `Styled` runs can
reference style names; `RtfWriter._render_run_styled` translates the
style name to RTF control words via `styles_rtf.to_rtf_open` /
`to_rtf_close`. A future `HtmlWriter._render_run_styled` translates
the same style name to CSS via `styles_html.to_css`. Same IR, two
writers.

Without the styles refactor, the IR's `Styled` runs would either
carry RTF strings inline (defeating the goal) or duplicate the
translation logic per writer (worse — every new writer
re-implements style translation). The two refactors together
deliver the full disentangling: data → IR → writer + writer →
styles_<format> → format-specific output.

## Net wins after both refactors

1. **Per-section renderers are testable in isolation** against IR
   shape, not RTF strings. Adding a column to a table is one IR
   edit + one test update; the RTF dialect is the writer's
   problem.
2. **Bugs partition cleanly.** A row-construction bug is in the
   renderer; an RTF emission bug is in the writer; a style choice
   is in `StyleAttrs`. Today all three categories cohabit in the
   same `render_X` function.
3. **New formats are tractable.** HTML / LaTeX / JSON outputs only
   need a new `Writer` implementation; no renderer changes.
4. **Standalone module testing.** Renderers, writer, styles all
   live in separate modules with their own unit test files. No
   per-file "import the world to test one function."

Trade-off: more files, more types, more module boundaries. ~2000+
lines of net new code (offset by ~1500 lines deleted from
`rtf.py`). The complexity is moved, not added — but the new shape
is more composable.
