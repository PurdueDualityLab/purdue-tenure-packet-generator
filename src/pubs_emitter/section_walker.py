"""Markdown-master walker — Phase 1 of the section-prose outline refactor.

Parses `section-prose.md` into a sequence of typed nodes (heading /
paragraph / directive) and walks the sequence to emit RTF. Designed to
eventually replace the ~250-line emit-order block in `rtf.write_rtf`;
in Phase 1 the walker is wired through `--use-markdown-master` but is
a no-op on the legacy emit path — its behavior is exercised only by
its own tests.

Markdown surface accepted:
    # III. MATERIAL …          → roman heading       (depth 1, `\\s1`)
    ## A. GENERAL INFORMATION  → group heading       (depth 2, `\\s2`)
    ### A.1 Name …             → section h1          (depth 3, `\\s3`)
    #### A.1.1 Subentry        → section h2          (depth 4, `\\s4`)
    !DIRECTIVE!                → directive dispatch  (own line only)
    everything else            → paragraph (blank-line separated)

Directives mid-paragraph are LITERAL text — only `^!NAME!$` lines
dispatch. Token alphabet: `[A-Z_][A-Z0-9_]*` so directives stay
visually distinct from prose.

Design doc: `docs/design/markdown-master-outline-refactor.md`.
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from typing import IO, Any, Callable, NamedTuple, Optional


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------
#
# Three discriminated kinds keyed by `.kind`. NamedTuple keeps the surface
# light + immutable; `kind` is the discriminator for the walker's dispatch.


class HeadingNode(NamedTuple):
    """Markdown heading: `#`+ code + title.

    `depth` is the `#`-count (1-4). `code` is the structural code
    extracted from the heading line (`III`, `A`, `A.1`, `C.16.2.3`).
    `title` is the rest of the heading line after the code.
    """
    kind: str  # "heading"
    depth: int
    code: str
    title: str


class ParagraphNode(NamedTuple):
    """Free-form prose paragraph between headings / directives.

    `text` is the paragraph body with internal soft-wraps collapsed to
    spaces (markdown convention; matches `load_section_prose`).
    """
    kind: str  # "paragraph"
    text: str


class DirectiveNode(NamedTuple):
    """Standalone `!NAME!` line that dispatches to a registered renderer.

    `name` is the directive identifier (uppercase + underscores + digits).
    The walker looks up `name` in the directive registry and invokes the
    renderer with the active `RenderContext` + output sink.
    """
    kind: str  # "directive"
    name: str


# ---------------------------------------------------------------------------
# Render context
# ---------------------------------------------------------------------------


@dataclass
class RenderContext:
    """Bundle of state every directive may consult.

    A single `ctx` argument threads through the walker so directives
    don't grow per-directive plumbing. Most fields default to empty so
    Phase-1 tests can construct a minimal context; downstream phases
    populate the data fields as sections migrate to directives.

    `ref_index` is the canonical `@id → C.X.Y` map. The walker AUGMENTS
    this with every heading code it sees (self-resolving entries) so
    `@C.16.2` resolves whether the target is YAML-backed or
    markdown-only.

    `macros` is the canonical `#NAME → value` map computed from
    `statistics.compute_all`.

    `extra` is an escape hatch for directives that need a payload the
    canonical fields don't carry — used sparingly during the migration
    so new directive shapes don't force a context-field churn.
    """
    ref_index: dict[str, str] = field(default_factory=dict)
    macros: dict[str, str] = field(default_factory=dict)
    # `@bibkey^SECTION` override resolution map — see builders.resolve_refs.
    # Outer key = bibkey; inner key = section prefix (e.g. "C.1", "C.4");
    # value = full C.X.Y. Populated by cli.py during ref registration so
    # papers that appear in BOTH a main section AND Key Works (C.1) can
    # be cross-referenced unambiguously from prose. Empty default keeps
    # tests minimal.
    section_bibkey_index: dict[str, dict[str, str]] = field(default_factory=dict)
    # Populated by the production pipeline as more directives land in
    # Phases 2+. Optional everywhere so Phase-1 tests stay terse.
    publications: Optional[Any] = None
    patents: Optional[list] = None
    grants_as_pi: Optional[list] = None
    grants_as_co_pi: Optional[list] = None
    gifts: Optional[list] = None
    internal_grants: Optional[list] = None
    pending_proposals: Optional[list] = None
    graduate_students: Optional[list] = None
    postdocs_visiting: Optional[list] = None
    undergraduate_students: Optional[list] = None
    student_awards: Optional[list] = None
    invited_talks: Optional[list] = None
    leadership_roles: Optional[list] = None
    media_appearances: Optional[list] = None
    conference_presentations: Optional[list] = None
    software_products: Optional[list] = None
    undergrad_pathways: Optional[list] = None
    undergrad_products: Optional[list] = None
    entrepreneurial_activities: Optional[list] = None
    technology_transfer: Optional[list] = None
    course_development: Optional[list] = None
    courses_taught: Optional[list] = None
    university_service: Optional[list] = None
    profession_service: Optional[list] = None
    national_service: Optional[list] = None
    other_service: Optional[list] = None
    under_review: Optional[list] = None
    candidate_info: Optional[Any] = None
    paper_index: dict[str, str] = field(default_factory=dict)
    # Escape hatch — see class docstring.
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
#
# Single-pass regex-driven tokenizer. Lines starting with `#` become
# heading nodes; lines that match `^!([A-Z_][A-Z0-9_]*)!$` become
# directive nodes; everything else collects into paragraph nodes split
# on blank lines.


# Heading: 1-4 `#`s + space + heading text. Capture `#`s for depth and
# the trailing text for code+title extraction.
_HEADING_LINE_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")

# Heading code (Roman OR dotted letter form). Roman = uppercase IVX
# letters; letter form = single capital letter, optionally followed by
# one or more `.N` segments. Matches: `III`, `IV`, `A`, `A.1`, `C.16.2.3`.
# Trailing period on the code is allowed (`III.`, `A.`) and stripped.
_HEADING_CODE_RE = re.compile(
    r"^(?P<code>[IVX]+|[A-Z](?:\.\d+)*)\.?\s+(?P<title>.+?)\s*$"
)

# Directive line: `!NAME!` filling the whole non-blank line. Token
# alphabet starts with uppercase or `_` and contains uppercase, digits,
# and `_`. Matches `!HELLO!`, `!PI_GRANT_TABLE!`, `!CO_PI_GRANT_TOTAL!`.
_DIRECTIVE_LINE_RE = re.compile(r"^\s*!([A-Z_][A-Z0-9_]*)!\s*$")

# C-style `/* … */` editor-only comment blocks. Stripped at parse time
# so the existing authoring convention (template prompts, reminders)
# continues to work under the walker. Mirrors `_PROSE_COMMENT_RE` in
# builders.py.
_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(text: str) -> str:
    """Remove `/* … */` blocks from the source before parsing."""
    return _COMMENT_BLOCK_RE.sub("", text)


def _split_heading_line(line: str) -> tuple[int, str, str]:
    """Parse a single `#`-line. Returns `(depth, code, title)`.

    Raises `ValueError` if the heading body doesn't carry a recognizable
    structural code (e.g., `## Random Title` with no `A`/`III`/`A.1`
    leader). Phase-1 strictness — loosen only if a real case appears.
    """
    m = _HEADING_LINE_RE.match(line)
    if not m:
        raise ValueError(f"not a heading line: {line!r}")
    depth = len(m.group(1))
    body = m.group(2).strip()
    cm = _HEADING_CODE_RE.match(body)
    if not cm:
        raise ValueError(
            f"heading body missing structural code (expected leading "
            f"Roman or dotted code like 'III' or 'A.1'): {body!r}"
        )
    return depth, cm.group("code"), cm.group("title").strip()


def parse_section_prose(text: str) -> list[NamedTuple]:
    """Parse markdown source into a flat node sequence.

    Tokenization is line-oriented + then paragraph-grouped:
      1. Strip `/* … */` editor comments.
      2. Walk lines: heading lines become HeadingNode; directive lines
         become DirectiveNode; runs of non-blank non-heading non-
         directive lines collect into a buffer that flushes to a
         ParagraphNode on each blank-line / heading / directive
         boundary. Internal soft-wraps in a paragraph collapse to
         spaces.
    """
    text = _strip_comments(text)
    nodes: list[NamedTuple] = []
    para_buf: list[str] = []

    def _flush_paragraph() -> None:
        if not para_buf:
            return
        # Collapse internal whitespace runs (incl. embedded newlines)
        # to single spaces — markdown soft-wrap convention.
        joined = " ".join(line.strip() for line in para_buf if line.strip())
        if joined:
            nodes.append(ParagraphNode(kind="paragraph", text=joined))
        para_buf.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            _flush_paragraph()
            continue
        if _HEADING_LINE_RE.match(line):
            _flush_paragraph()
            depth, code, title = _split_heading_line(line)
            nodes.append(
                HeadingNode(kind="heading", depth=depth, code=code, title=title)
            )
            continue
        dm = _DIRECTIVE_LINE_RE.match(line)
        if dm:
            _flush_paragraph()
            nodes.append(DirectiveNode(kind="directive", name=dm.group(1)))
            continue
        para_buf.append(line)
    _flush_paragraph()
    return nodes


# ---------------------------------------------------------------------------
# Walker
# ---------------------------------------------------------------------------


def _collect_declared_codes(nodes: list[NamedTuple]) -> set[str]:
    """Return every structural code declared by a heading node."""
    return {n.code for n in nodes if isinstance(n, HeadingNode)}


def _augment_ref_index(ctx: RenderContext, declared_codes: set[str]) -> None:
    """Self-resolve every declared heading code into `ctx.ref_index`.

    `@C.16.2 → C.16.2` for any code the markdown declares that
    isn't already in `ref_index` (YAML-backed entries take precedence).
    Mutates `ctx.ref_index` in place.
    """
    for code in declared_codes:
        ctx.ref_index.setdefault(code, code)


def _emit_heading_via_legacy(
    out: IO[str], depth: int, code: str, title: str,
) -> None:
    """Dispatch to the existing heading emitters in `rtf.py` by depth.

    Phase 1 keeps the heading shape identical to the legacy emit path
    so Phase-2's parity test (walker vs legacy) reduces to whitespace
    diffing. Imports are local to break the circular `rtf ↔ walker`
    risk.
    """
    # Local import: walker is invoked from rtf.write_rtf, but the
    # walker also imports the heading emitters. Function-local import
    # keeps module-load ordering clean.
    from .rtf import (
        _emit_group_heading,
        _emit_roman_section_heading,
        _emit_section_heading,
    )
    if depth == 1:
        _emit_roman_section_heading(out, code, title)
    elif depth == 2:
        _emit_group_heading(out, code, title)
    elif depth in (3, 4):
        # `_emit_section_heading` self-scales by dot-depth; depth-3
        # codes (`A.1`) render fs28-bold, depth-4 (`A.1.1`) render
        # smaller italic. The walker passes the code verbatim — the
        # existing helper's internal `_heading_level(code)` already
        # picks the right level.
        _emit_section_heading(out, code, title)
    else:
        raise ValueError(f"unsupported heading depth: {depth}")


def _resolve_paragraph(ctx: RenderContext, text: str) -> str:
    """Run the canonical `#macro` + `@ref` pipes over a paragraph.

    Mirrors the cli.py resolution loop so prose authored in
    `section-prose.md` resolves identically whether it flows through
    the legacy dict-based path or the walker.

    Unresolved tokens are LOGGED at warning level (the walker is a
    Phase-1 test surface; production fail-loud belongs upstream in
    cli.py once the walker is the production path).
    """
    from .builders import resolve_refs
    from .statistics import substitute
    resolved, unresolved_macros = substitute(text, ctx.macros)
    if unresolved_macros:
        log.warning(
            "walker: unresolved #macro(s) %s in paragraph %r",
            sorted(unresolved_macros), text[:80],
        )
    resolved, unresolved_refs = resolve_refs(
        resolved, ctx.ref_index, link_format=True,
        section_bibkey_index=ctx.section_bibkey_index,
    )
    if unresolved_refs:
        log.warning(
            "walker: unresolved @-ref(s) %s in paragraph %r",
            sorted(unresolved_refs), text[:80],
        )
    return resolved


def _emit_paragraph(
    out: IO[str], ctx: RenderContext, text: str, current_code: Optional[str],
) -> None:
    """Emit a paragraph node at the body-indent of the surrounding code."""
    from .rtf import _body_indent_for_code, _markdown_inline_to_rtf
    resolved = _resolve_paragraph(ctx, text)
    rendered = _markdown_inline_to_rtf(resolved)
    # Roman / group / pre-first-heading paragraphs have no dotted code
    # to derive an indent from; emit flush-left in that case.
    if current_code and "." in current_code:
        indent = _body_indent_for_code(current_code)
    else:
        indent = 0
    out.write(f"\\pard\\li{indent} {rendered}\\par\\par\n")


def _dispatch_directive(
    out: IO[str], ctx: RenderContext, name: str,
) -> None:
    """Look up `name` in the directive registry and invoke it.

    Missing directive is a build-fatal error (Q5 of the design doc:
    fail-loud). The walker logs the error context (registered names)
    and exits 1 so the build doesn't ship an outline with silent
    gaps.
    """
    # Function-local import: `directives` will eventually import from
    # this module's RenderContext, so module-load ordering matters.
    from .directives import DIRECTIVES
    if name not in DIRECTIVES:
        log.error(
            "walker: unknown directive %r at emit time; registered: %s",
            name, sorted(DIRECTIVES.keys()) or "(none)",
        )
        sys.exit(1)
    DIRECTIVES[name](ctx, out)


def walk_section_prose(
    text: str, ctx: RenderContext, out: IO[str],
) -> None:
    """Emit RTF for the given markdown source.

    Phase-1 contract:
      * Headings dispatch to `_emit_*_heading` by depth.
      * Paragraphs flow through `#macro` + `@ref` resolution + markdown
        inline emphasis, then emit at the body-indent of the current
        section code.
      * Directives dispatch to `DIRECTIVES`; missing directive aborts.
      * `ref_index` is augmented with every declared heading code so
        `@C.X.Y` cross-refs that target markdown-only sections resolve.

    Single pass. Maintains `current_code` so paragraph indent tracks
    the nearest preceding heading.
    """
    nodes = parse_section_prose(text)
    _augment_ref_index(ctx, _collect_declared_codes(nodes))
    current_code: Optional[str] = None
    for node in nodes:
        if isinstance(node, HeadingNode):
            current_code = node.code
            _emit_heading_via_legacy(out, node.depth, node.code, node.title)
        elif isinstance(node, ParagraphNode):
            _emit_paragraph(out, ctx, node.text, current_code)
        elif isinstance(node, DirectiveNode):
            _dispatch_directive(out, ctx, node.name)
        else:
            raise TypeError(f"unknown node type: {type(node).__name__}")
