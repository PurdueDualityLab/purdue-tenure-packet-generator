# Markdown-master outline refactor — design

## Problem

The Word doc's structure — which sections exist, in what order, with
what headings, with which YAML-driven content populating each — is
currently encoded across three places:

1. **`config.py`** holds the `SECTION_CODES`, `SECTION_HEADINGS`, and
   `SECTION_ORDER` registries that name + order every section in the
   rendered packet.
2. **`rtf.py`** has a ~250-line emit-order block inside `write_rtf`
   that walks `SECTION_ORDER`, conditionally fires per-section
   renderers (`render_grants_section`, `render_patents_section`, …),
   and inlines the C.16 outline + B.X sequence + V. appendix.
3. **`section-prose.md`** holds the hand-authored prose between or
   inside sections.

Two consequences:

**Section ordering / structure is not editor-accessible.** Adding a
new section, reordering, or changing how a section's headings nest
requires touching three files including the 250-line emit-order block
in `write_rtf`. An editor who is otherwise comfortable writing
markdown cannot adapt the packet to a revised Purdue template or to
their own institution's variant without engaging the Python.

**Per-section layout is fixed by the renderer.** Each
`render_*_section` emits heading → intro → table → total in a baked
order. A candidate who wants explanatory prose AFTER a grant table
(rather than before) cannot get there from here — the renderer's
sequence is opaque to markdown. This is a real authoring constraint,
not a hypothetical: catchall sections (C.5 Other Publications, C.16
Student Mentoring, C.26 Other Service) routinely want bespoke
layouts and currently survive only because each is inlined in
`write_rtf` as one-off code.

The candidate's framing crystallizes the goal: **the markdown file
should BE the document outline, with directives marking holes the
Python renderers fill.** Layout authority moves to the editor. The
Python renderers shrink to "emit this one table" — sequencing is
markdown's job.

## Goal

`section-prose.md` becomes the master representation of the rendered
packet:

- **Headings come from markdown.** `# III. …`, `## A. …`, `### A.1 …`,
  `#### A.1.1 …` map by markdown depth to the four levels of section
  heading the renderer already knows (`roman_section`, `group_heading`,
  `section_h1`, `section_h2`).
- **Prose comes from markdown.** Everything between headings is prose;
  the existing `@-ref`, `#macro`, and `**bold**`/`*italic*` pipes apply
  unchanged.
- **Structured content comes from directives.** `!PI_GRANT_TABLE!`,
  `!PATENTS_TABLE!`, `!GRADUATE_STUDENTS_TABLE!`, etc., dispatch to
  registered Python renderers. Each directive emits one atomic chunk
  (a table, a total line, a tier block) — sequencing is in the
  markdown.
- **The 250-line emit-order block in `write_rtf` is replaced by a
  ~30-line markdown walker.** The walker emits headings, resolves
  prose, dispatches directives. That's the whole rendering loop.

`section-prose.md` ships pre-populated with the canonical default
outline + directive placements that match the current rendered packet,
so the migration is non-breaking for the candidate who doesn't want to
customize. Candidates who DO want to customize edit one markdown file.

## Non-goals

- **Not implementing pandoc.** Headings, paragraphs, inline emphasis,
  and our `@-ref` / `#macro` / `!DIRECTIVE!` extensions are the entire
  markdown surface we parse. No tables, no lists, no fenced code, no
  footnotes. (Tables are emitted by directives that wrap the existing
  Python table renderers, which already do the layout. We're not
  asking the candidate to author tables in markdown.)
- **Not converting Python renderers to data-driven config.** Each
  per-section renderer's internal layout (column widths, sort keys,
  tier subheadings, hanging indents, indent maths) stays in Python.
  Directives are dispatch sites, not layout descriptions.
- **Not building HTML / LaTeX / PDF output yet.** The walker's
  intermediate representation will be AST-friendly so a future format
  pass is cheap, but no second format ships in this refactor. (Phase
  5; out of scope here.)
- **Not migrating C.5, C.16, C.26, or other catchall sections.**
  Catchalls are inherently free-form; their markdown body is
  hand-authored prose with optional inline directives, and the
  walker treats them like any other section. No special Python
  emit-order block for them.

## Design

### Heading-level mapping

| Markdown | Semantic | Style key (existing) | Word style ID |
|---|---|---|---|
| `# III. MATERIAL …` | Roman section | `roman_section` | `\s1` (heading 1) |
| `## A. GENERAL INFORMATION` | Group | `group_heading` | `\s2` (heading 2) |
| `### A.1 Name and …` | Section level 1 | `section_h1` | `\s3` (heading 3) |
| `#### A.1.1 Name` | Section level 2 | `section_h2` | `\s4` (heading 4) |

The heading's code (`III` / `A` / `A.1` / `A.1.1`) is extracted from
the heading text via a regex; the rest of the line is the title. The
walker emits `_emit_roman_section_heading` / `_emit_group_heading` /
`_emit_section_heading` per markdown depth — none of these change
shape, just their caller does.

Heading-code regex (loose enough to cover the cases we know about):

```python
_HEADING_CODE_RE = re.compile(
    r"^(?P<code>[IVX]+|[A-Z](?:\.\d+)*)\s+(?P<title>.+?)\s*$"
)
```

Matches: `III. MATERIAL …`, `A. GENERAL …`, `A.1 Name`, `C.16.2.3
Products`. Roman numeral and letter-only codes have no dot; section
codes have dots. The walker distinguishes by markdown depth, not by
code shape.

### Directive syntax + registry

Directive token form: `!NAME!` on its own line (paragraph-equivalent
position). Tokens elsewhere (mid-paragraph) are passed through as
literal text. The token alphabet is `[A-Z_][A-Z0-9_]*` so directives
are visually distinct from normal prose.

Registry shape:

```python
DIRECTIVES: dict[str, Callable[[RenderContext, IO], None]] = {
    "PI_GRANT_TABLE":           lambda ctx, out: render_grant_table(ctx.grants_as_pi, out),
    "PI_GRANT_TOTAL":           lambda ctx, out: render_grant_total(ctx.grants_as_pi, out),
    "CO_PI_GRANT_TABLE":        lambda ctx, out: render_grant_table(ctx.grants_as_co_pi, out),
    "PATENTS_TABLE":            lambda ctx, out: render_patents_table(ctx.patents, out),
    "GRADUATE_STUDENTS_TABLE":  lambda ctx, out: render_students_table(ctx.graduate_students, ...),
    # ~40-50 entries total
}
```

`RenderContext` is a dataclass bundling everything a directive might
need: publications, patents, grants, students, software, ref_index,
paper_index, etc. One `ctx` argument passed to every directive
removes per-directive plumbing.

### Renderer decomposition

Today's monolithic renderers (`render_grants_section`,
`render_undergrad_pathways_section`) emit heading + intro + table +
total as one call. The refactor splits each into atomic directives so
the candidate controls sequencing:

| Today's renderer | Decomposes into |
|---|---|
| `render_grants_section` | `!*_GRANT_TABLE!`, `!*_GRANT_TOTAL!` (intro auto-emits from prose dict via heading) |
| `render_patents_section` | `!PATENTS_TABLE!` (single-table; no decomp) |
| `render_students_section` | `!*_STUDENTS_TABLE!` |
| `render_courses_taught_section` | `!COURSES_TABLE!` |
| `render_undergrad_products_section` | `!UNDERGRAD_PRODUCTS_LIST!` |
| `render_student_awards_section` | `!*_STUDENT_AWARDS_BY_TIER!` (could decompose further if per-tier control becomes a need) |
| Service sections (C.23-C.26) | `!*_SERVICE_LIST!` |

Most sections need 1-2 directives. The decomposition unit is "the
smallest chunk a candidate might want to re-sequence around."

**Default packet:** `section-prose.md` ships with the decomposed
directives placed in the canonical order so the rendered packet
matches today's output byte-for-byte modulo whitespace. Customization
is opt-in.

### Walker architecture

The walker is a single pass over the markdown that emits RTF. It
maintains no AST today (but the design keeps an AST node-emit
factoring in mind so a future format pass is mechanical — see
"Phase 5" below).

Pseudocode:

```python
def walk_section_prose(text: str, ctx: RenderContext, out: IO[str]) -> None:
    for node in _parse_section_prose(text):
        if node.kind == "heading":
            _emit_heading(out, node.depth, node.code, node.title)
        elif node.kind == "paragraph":
            resolved, _ = resolve_refs(node.text, ctx.ref_index, link_format=True)
            resolved, _ = substitute(resolved, ctx.macros)
            out.write(f"\\pard\\li{_body_indent_for_code(_current_code)} "
                      f"{_markdown_inline_to_rtf(resolved)}\\par\\par\n")
        elif node.kind == "directive":
            DIRECTIVES[node.name](ctx, out)
```

The parser is a small regex-driven tokenizer: lines starting with `#`
become heading nodes (depth = `#` count); lines that match
`^!([A-Z_][A-Z0-9_]*)!$` become directive nodes; everything else
collects into paragraph nodes split on blank lines.

### Bookmark generation + ref_index integration

Every heading the walker emits gets a bookmark named
`{code.replace(".", "_")}` — same convention as today's
`_emit_section_heading`. The walker collects every heading code into
a "declared codes" set during its first pass and merges it into
`ref_index` before prose resolution begins:

```python
declared_codes = {node.code for node in nodes if node.kind == "heading"}
for code in declared_codes:
    if code not in ref_index:
        # Self-resolving: @C.16.2 → C.16.2 (the code is its own value).
        ref_index[code] = code
```

This is the only "new path" for cross-ref machinery. `@C.X.Y` refs
that previously relied on `_RAW_CODE_PATTERN` (which sidesteps
ref_index entirely) continue to work; refs that target a markdown-
declared but renderer-empty section heading now resolve correctly.

### Catchall sections

C.5, C.16, C.26 (and any future catchall) live in `section-prose.md`
as free-form markdown: heading + prose + optional inline directives,
in whatever order the candidate wants. There is no special Python
emit-order entry for them. From the walker's perspective, they're
just sections with a longer prose body and zero or more directive
holes.

This is exactly what `section-prose.md` already supports for C.16;
the refactor just removes the "everything not in C.16 emits via the
Python emit-order block" carve-out.

### `--sections` filter migration

Current behavior: `--sections C.10,C.16` is a Section-name allowlist;
sections whose registered name (or that of a parent code) appears in
the set get emitted.

New behavior: code-prefix allowlist. The walker skips any heading
whose code doesn't begin with a member of the allowlist (with the
parent-tree convention: `--sections C.16` emits `C.16.1`, `C.16.2.1`,
…). Same UX from the user's perspective; mechanical reimplementation
in the walker.

## Migration plan

Five phases, each independently shippable + revertible. The two
systems coexist during Phase 3.

### Phase 1 — Walker + directive infrastructure (no migration)

**Scope:** Parser, walker, directive registry, `RenderContext`
dataclass, walker-side ref_index extension, missing-directive lint.
None of the existing renderers are touched; no section is migrated.

**Deliverables:**

- `src/pubs_emitter/section_walker.py` (new): `parse_section_prose`,
  `walk_section_prose`, `RenderContext`.
- `src/pubs_emitter/directives.py` (new): `DIRECTIVES` registry,
  initially empty (or with one no-op `!HELLO!` directive for testing).
- `_section_prose` module global in `rtf.py` stays as today's source
  of prose — no behavior change yet.
- New `--use-markdown-master` CLI flag (default OFF). When set,
  invokes the walker after the existing emit-order block runs; the
  walker is a no-op until directives + section migrations land in
  Phase 2+.

**Tests (Phase 1):**

| Test | Asserts |
|---|---|
| `test_parse_headings_by_depth` | `#`/`##`/`###`/`####` map to depths 1/2/3/4 |
| `test_parse_extracts_code_and_title` | `## A.1 Name and identifiers` → code="A.1", title="Name and identifiers" |
| `test_parse_directive_isolated_line` | `!FOO!` on its own line → directive node |
| `test_parse_directive_inline_is_literal` | `text !FOO! more text` → literal text in a paragraph (not a directive) |
| `test_parse_paragraphs_split_on_blank_lines` | Multiple blank-line-separated paragraphs become separate nodes |
| `test_parse_strips_c_style_comments` | Existing `/* … */` strip behavior preserved |
| `test_walker_emits_bookmark_per_heading` | Walker output contains `\bkmkstart C_16_2` for `## C.16.2 …` |
| `test_walker_dispatches_directive` | `!HELLO!` invokes the registered renderer |
| `test_walker_missing_directive_exits` | `!NOT_REGISTERED!` → log.error + sys.exit(1) |
| `test_walker_resolves_at_refs_in_paragraphs` | `@davis2018impact` in a paragraph becomes a hyperlink |
| `test_walker_substitutes_macros_in_paragraphs` | `#NUM_TIER_1` in a paragraph becomes the computed value |
| `test_walker_collects_declared_codes_into_ref_index` | `@C.16.2.1` resolves to the C_16_2_1 bookmark when only the markdown declares it |

**Exit criteria:** All Phase 1 tests pass. `--use-markdown-master`
runs without effect on the rendered RTF (the walker is engaged but
nothing's migrated to it). Existing 571-test suite still green.

### Phase 2 — Pilot section migration (C.19 Patents)

**Scope:** Migrate C.19 Patents — the cleanest single-table section
with no decomposition needed. Verify the round trip end-to-end.

**Deliverables:**

- Add `!PATENTS_TABLE!` to `DIRECTIVES`, calling the existing
  `render_patents_section` body (refactored to take a list of patents
  and emit just the table, no heading).
- Add `### C.19 List issued U.S. international patents …` + the
  `!PATENTS_TABLE!` directive to `section-prose.md`.
- Remove C.19's emit entry from `write_rtf`'s emit-order block (gated
  on `--use-markdown-master`).
- The C.19 emit now flows through the walker.

**Tests (Phase 2):**

| Test | Asserts |
|---|---|
| `test_patents_emit_via_walker` | With `--use-markdown-master`, the patent table appears in the same place as before |
| `test_patents_byte_identical_modulo_whitespace` | Walker output ≈ legacy output for C.19 (whitespace-normalized diff is empty) |
| `test_patents_bookmark_present` | `\bkmkstart C_19` and per-row `\bkmkstart C_19_N` bookmarks still emit |
| `test_patents_cross_ref_resolves` | An `@C.19.3` ref elsewhere in the doc still resolves to the C.19.3 patent row |

**Exit criteria:** Walker-emitted C.19 is functionally identical to
legacy emit. `--use-markdown-master` becomes the recommended flag for
candidates piloting the new behavior.

### Phase 3 — Bulk section migrations

**Scope:** Migrate the remaining ~20 non-catchall sections one by one.
Each migration is its own self-contained change: decompose the
renderer if it needs >1 directive, register the directive(s) in
`DIRECTIVES`, add the markdown stanza to `section-prose.md`, remove
the legacy entry from `write_rtf`'s emit-order block.

**Migration order** (lowest risk first):

1. Simple single-table tail sections: C.6, C.7, C.8, C.9, C.15, C.17,
   C.18, C.20, C.22 (~30 min each).
2. Multi-table grants: C.10, C.11, C.12, C.13 (~60 min each — they
   need decomposition: intro counter + table + total).
3. Student tables: C.14 (graduate students) (~45 min — has tier
   subheadings).
4. Service sections: C.23, C.24, C.25 (~30 min each).
5. Awards sections: C.16.2.4, C.16.3.2 student-awards (~45 min — tier
   subheadings).
6. A.X front matter: A.1 through A.7 (~20 min each — short directives).

Each migration follows the same recipe (codified in a checklist that
lives at the top of `directives.py`):

1. Identify the directive granularity (single-table = 1 directive;
   intro+table+total = 3 directives).
2. Refactor the existing `render_*_section` to expose the per-piece
   emit functions; the section-level function becomes a thin wrapper
   for backwards compatibility during Phase 3 only.
3. Register directive(s) in `DIRECTIVES`.
4. Add the markdown stanza to `section-prose.md`.
5. Add the legacy section name to a `_MIGRATED_SECTIONS` set in
   `write_rtf`; the emit-order block skips migrated sections under
   `--use-markdown-master`.
6. Run the existing test suite + add a per-section test asserting
   walker output matches legacy output (whitespace-normalized).

**Tests (Phase 3, per migrated section):**

| Test | Asserts |
|---|---|
| `test_<section>_walker_matches_legacy` | Walker-emitted RTF for the section ≈ legacy emit (whitespace-normalized diff is empty) |
| `test_<section>_directives_registered` | Each `!FOO!` referenced in section-prose.md is in DIRECTIVES |
| `test_<section>_skip_when_empty` | Sections with no underlying data still emit nothing (walker respects skip-when-empty) |
| `test_<section>_cross_refs_resolve` | Per-row bookmarks (`C.10.3`, `C.14.5`, …) are still emitted + still target-able |

**Exit criteria:** All sections except C.5 / C.16 / C.26 (catchalls)
emit via the walker. `--use-markdown-master` produces byte-identical
output to legacy modulo whitespace for every non-catchall section.

### Phase 4 — Cleanup + flag flip

**Scope:** Make `--use-markdown-master` the default behavior. Delete
the legacy emit-order block in `write_rtf`. Delete `SECTION_ORDER`.
Shrink `SECTION_HEADINGS` (markdown is now the source of truth for
headings; only the keys directives still look up remain). Shrink
`SECTION_CODES` similarly.

**Deliverables:**

- `write_rtf` body collapses to: validate inputs → build ref_index →
  load section-prose → walk. The 250-line emit-order block is gone.
- `--use-markdown-master` is renamed `--use-legacy-emit` (the inverse
  default) for one release as an escape hatch; then deleted.
- C.16 outline that's currently inlined in `write_rtf` moves to its
  free-form markdown body in `section-prose.md`. This is the largest
  individual file change in Phase 4 (~80 lines of inlined emit
  becomes ~80 lines of markdown).

**Tests (Phase 4):**

| Test | Asserts |
|---|---|
| `test_legacy_emit_block_deleted` | No `_emit("…")` calls remain in `write_rtf`'s body |
| `test_section_order_constant_deleted` | `SECTION_ORDER` import fails at module load |
| `test_section_headings_only_has_directive_keys` | Every key in `SECTION_HEADINGS` is referenced by an entry in `DIRECTIVES`; orphan keys flag as dead |
| `test_no_legacy_flag_in_cli` | `--use-legacy-emit` is no longer in `cli.py`'s argparse |
| `test_e2e_byte_identical_to_pre_refactor` | Whole-document emit matches a pinned-pre-refactor RTF byte-for-byte modulo whitespace |

**Exit criteria:** All 4 phases shipped. Walker is the only render
path. Legacy emit-order block deleted.

### Phase 5 — Output-format extensibility (FUTURE; out of scope for this doc)

The walker's emit step is currently `out.write(rtf_string)`. To unlock
HTML / LaTeX / JSON outputs, refactor to:

1. Parser produces an AST (list of typed nodes).
2. A format-specific renderer walks the AST and emits its format.
3. Each `DIRECTIVES` entry takes a `Format` enum and emits the right
   string for that format (or skips, e.g., bookmarks in HTML become
   `<a name="…">`).

This is a clean follow-up refactor once Phase 4 lands; the markdown-
master design teers it up but doesn't require it. NOT part of this
design doc.

## Test strategy summary

Every phase ships with three test categories:

1. **Unit:** Parser, walker, directive dispatch, registry lookups —
   isolated from the data layer.
2. **Migration parity:** Per-section "walker matches legacy"
   whitespace-normalized RTF diff. This is the safety net during
   Phase 3 — if a section's walker emit differs from legacy emit by
   anything except whitespace, the migration is broken.
3. **E2E:** Full-document emit matches a pinned reference RTF after
   each phase. Phase 4 ends with the reference RTF re-baselined; from
   then on, the e2e test is the regression guard.

Migration parity is the load-bearing test layer for Phase 3. It's
easy to get wrong (a directive could emit slightly different
whitespace, indent, or order) and the parity test catches it
mechanically.

## Open questions

**Q1: Heading code regex strictness.** The proposed regex tolerates
`III` (Roman) and `A` (letter only) and `A.1.2.3` (dotted). Does the
parser need to also handle `IV.A.2` or other compound forms? My read:
no — none currently exist. Lock the regex tight; loosen later if a
new section format appears.

**Q2: Directive arguments.** Should directives support arguments
(`!GRANT_TABLE:PI!` collapsing `!PI_GRANT_TABLE!` and
`!CO_PI_GRANT_TABLE!` into one directive)? Lean: NO for v1. The
directive-name space is small (~40-50 entries), the args layer adds
a parser surface, and there's no current case where the arg form is
clearly nicer than two named directives. Add args only if a real case
appears in Phase 3.

**Q3: Markdown header strictness.** Do we enforce exactly `## A.1
Name…` or also tolerate `##A.1 Name…` (no space)? Lean: enforce
strict GitHub-Flavored Markdown — `## ` (with the space). Easy to
diagnose if violated; matches what every markdown editor produces.

**Q4: Walker-emitted bookmarks for headings with no body content.**
Today's `_emit_placeholder_subsection` writes `\bkmkstart` then an
empty `\par\par`. The walker behavior should match: emit the bookmark
unconditionally, blank body if no prose / no directive follows. Is
there any case where an "intentionally empty" heading should NOT get
a bookmark? Lean: no — bookmarks are always emitted, so `@C.16.2`
always resolves whether or not C.16.2 has body content.

**Q5: Directive failure mode.** A directive that raises mid-emit:
should the walker abort the whole render, log + skip the directive,
or fall through to a placeholder? Lean: abort — failures here are
programming errors (directive bug or missing data) and silent
fallback would mask them. Same fail-loud semantics as the existing
@-ref / #macro pipeline.

**Q6: Declarative table structure (FUTURE; design space to ponder).**
The directive model proposed above leaves each table's
**column-mapping** in Python: `add_header([...])` and `add_row([
escape_rtf(p.dates), escape_rtf(p.activity), ...])` are hard-coded
inside per-section renderers. That means a candidate who wants a
genuinely new section ("Books with audiobook companions" with custom
columns) still has to write Python — register a new directive AND
implement the renderer.

The natural next step: expose **table structure declaratively** in a
sidecar config file. Sketch:

```yaml
# assets/table-schemas.yaml — example
undergrad_pathways:
  yaml_field: undergrad_pathways          # → list[UndergradPathway]
  columns:
    - {field: dates,        header: Dates,         width: 1900}
    - {field: activity,     header: Pathway,       width: 2900}
    - {field: audience,     header: Audience,      width: 3200}
    - {field: participation, header: Participation, width: 1360}
  sort: dates                              # or a tuple/expression
```

A new "tabular" directive class reads the schema, walks the YAML data,
and emits a generic `RtfTable`. The user adds rows to the YAML +
columns to the schema file + a `!UNDERGRAD_PATHWAYS_TABLE!` directive
to `section-prose.md` — zero Python.

Scope this carries:
- Simple tables (column-mapping + maybe a sort key) go declarative.
- Bespoke tables with computed cells (grant totals, tier subheadings,
  per-row cross-refs, condition-driven row formatting) stay in Python.
  The directive registry already accommodates both shapes.
- Schema format is YAML-or-markdown-frontmatter; either fine.
- Migration: pilot on one or two simple tables (undergrad_pathways,
  service entries) in a later phase; the bulk of renderers stay in
  Python through Phase 4 because their per-section logic isn't
  schema-expressible.

Trade-off: declarative descriptors are more verbose than Python for
gnarly cases. Lean: **defer to a follow-up phase (Phase 5 or 6),** but
pin the design space here so the directive registry's API doesn't
paint into a Python-only corner.

The candidate's framing for this: "the user should be able to DIY
their own custom sections for the 'custom' parts" — same instinct as
markdown-mastering the outline, applied to the table layer.

**Q7: Embedded images for supporting documentation (V.A.1 under-review
papers + general catchall use).** Section V.A.1 lists papers currently
under review; the Purdue template expects "supporting documentation" —
typically a screenshot of the submission-confirmation email — embedded
directly beneath each paper entry, sub-indented under the paper's
citation. The current renderer has no image-emit path.

Two notations are natural under the directive/walker model:

1. **YAML-attached image (preferred for the V.A.1 case).** Each
   under-review `Publication` gains an optional `supporting_image`
   field; the under-review renderer emits the image after the citation
   at the paper's body-indent. This is the cleanest fit because the
   supporting evidence is semantically tied to the paper, not to a
   free-floating markdown position.

   ```yaml
   # bibtex sidecar or YAML db entry
   - cite_key: lugo2026dgov
     status: under_review
     supporting_image: assets/supporting-docs/dgov-2026-0087.png
     supporting_image_caption: "Submission confirmation, 2026-04-20"
   ```

2. **Markdown image directive (general-purpose).** A directive of the
   form `!IMAGE:path/to/file.png!` (or, with options, `!IMAGE
   path=... width=... indent=A.1.1!`) emits a free-standing image at
   the walker's current indent. Useful for catchall sections where the
   image isn't tied to a YAML row — e.g., a screenshot inside C.16
   prose, or a figure inside C.26 blog evidence.

   The directive resolver reads the file at emit time, hex-encodes per
   the PNG/JPG kind, and emits an RTF `{\pict\pngblip\picwgoal<w>\
   pichgoal<h> <hex>}` block inside a `\pard\li<indent>` paragraph. The
   indent comes from the current section code (same `_body_indent_for_
   code` machinery the walker already uses for paragraphs).

Both should land — they're complementary, not alternatives. The YAML
form handles the structured cases (every under-review paper, every
patent that wants a filed-stamp screenshot, etc.) without forcing the
candidate to keep a parallel notation in markdown. The directive form
handles the bespoke cases the YAML can't reach.

RTF mechanics worth pinning now so the directive registry's image
contract is right the first time:

- **Encoding:** `{\pict\<blip>\picwgoal<w_twips>\pichgoal<h_twips> <hex>}`.
  `\pngblip` for PNG, `\jpegblip` for JPEG. Width + height in twips
  (1 twip = 1/20 point). Read the image's intrinsic px dims at emit
  time, convert via DPI (default 96 DPI → twips = px × 15).
- **Sizing policy:** clamp the embedded width to a sane max
  (e.g., 5 inches = 7200 twips) so a tall screenshot doesn't blow past
  the page width. Preserve aspect ratio.
- **Indent:** wrap the `\pict` in `\pard\li<indent>\par <pict> \par`
  so the picture flows at the same left-edge as the paper body. For
  V.A.1, that's the body-indent for the `A.1.1` heading code.
- **File-not-found:** fail loud (same policy as `@-ref` / `#macro`
  unresolved). Builds with missing supporting docs should not ship.
- **Path resolution:** image paths are repo-relative (treated as
  resolved against the project root, same as `assets/`-prefixed paths
  elsewhere).
- **Caption (optional):** if `supporting_image_caption` (YAML) or
  `caption=...` (directive) is set, emit a `\fs18\i Caption\i0` line
  below the image at the same indent. Out of scope for v1 if not
  needed.

Scope this carries:
- One new helper module (`src/pubs_emitter/image_embed.py`) that loads
  the file, decodes dims, hex-encodes, builds the `\pict` block. ~80
  LoC + tests.
- The `Publication` data model gains an optional `supporting_image`
  field (renderer-side change, no schema break).
- The directive registry gains an `IMAGE` entry that parses
  `!IMAGE:path!` (or the verbose form) and dispatches to
  `image_embed.emit_image_at_indent`.
- Two tests, minimum: (a) round-trip emit of a known small PNG produces
  a `\pict\pngblip ...` block with the expected dims; (b) V.A.1
  papers with `supporting_image` set emit the image at the right
  indent below the citation.

Trade-off: hex-encoding large screenshots bloats the RTF dramatically
(2× the source PNG bytes, since each byte becomes two hex chars).
Mitigation: downsample / convert to JPEG for screenshots before
committing them under `assets/supporting-docs/`. Document this in the
authoring guidance at the top of `section-prose.md`. (`pillow` is
already a project dep; a future tool could auto-downsample on build.)

Lean: **build in Phase 3 alongside the V.A.1 / V appendix migrations.**
The YAML field is small enough to land without ceremony; the
`!IMAGE!` directive is straightforward registry work. Pin the design
here so the directive contract accommodates both forms from day one.

The candidate's framing: "we should have a notation that says 'insert
this image here'" — both notations described above honor that, with
the YAML form being the more ergonomic of the two for the routine
under-review case.

## Risks

**R1: Whitespace divergence between walker and legacy emit.** Likely
the most common Phase 3 bug. Mitigation: whitespace-normalized RTF
diff in the parity test; the diff is empty or the migration is
broken. Detection is mechanical.

**R2: Bookmark collisions when markdown declares a code the
renderer also emits.** E.g., walker emits `C_16_2` bookmark from the
markdown heading; a directive somewhere also tries to emit it.
Mitigation: walker emits headings first, registers the code in
ref_index; directives that try to emit a duplicate bookmark name
should be detectable at write time (RTF doesn't error on duplicate
bookmarks but Word's link-target lookup picks the first
occurrence — same as today's behavior for any code that gets
mentioned twice).

**R3: Markdown-declared code shape drifts from the existing
`SECTION_CODES` shape.** E.g., editor types `### C.5.4.1 Foo` but
the existing `_section_codes_up_to(code, N)` machinery uses a
different layout. Mitigation: the walker validates that every
heading code parses cleanly per the regex; downstream code that
takes codes from the walker continues to work because the codes ARE
the same shape (the walker doesn't synthesize new codes).

**R4: Migration parity test passes but the visual output differs in
Word.** RTF byte-equivalence is not visual-equivalence. Mitigation:
manual visual check on the Phase 2 pilot + Phase 4 final baseline.
The migration parity test is a strong filter, not a sufficient one.

**R5: The catchall sections want to use directives mid-prose (which
the walker supports) but with arguments the current syntax can't
express.** Mitigation: keep an open issue for "if a catchall wants
parameterized directives, revisit Q2." Don't block on this in v1.

## Rollback story

Phases 1, 2, 3 are independently revertible: drop the relevant
section's directive from `DIRECTIVES`, remove its markdown stanza,
restore its `_emit("…")` call in `write_rtf`. The legacy emit-order
block stays intact during Phases 1-3 (it just selectively skips
migrated sections), so a partial rollback is one commit.

Phase 4 deletes the legacy emit block; rollback at that point means
reverting the deletion commit. Not destructive, just larger.
