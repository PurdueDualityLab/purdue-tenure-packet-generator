"""Directive registry for the markdown-master walker.

Each registered entry maps an uppercase token (e.g., `PI_GRANT_TABLE`,
`PATENTS_TABLE`) to a renderer that emits one atomic RTF chunk for the
walker. The walker calls `DIRECTIVES[name](ctx, out)` when it
encounters `!NAME!` on its own line in the walker outline file.

Two registration paths coexist by design:

  1. **Declarative tabular directives.** Defined in
     `assets/table-schemas.yaml`. Each top-level entry becomes a
     directive whose name matches the schema key. The factory
     `_make_tabular_directive` builds the renderer from the schema —
     no Python edit required for simple-table sections (column-
     mapping + headers + widths + per-column escape policy).
     Brought forward into Phase 2 from Q6 of the design doc.

  2. **Bespoke Python directives.** Defined as functions in this
     module. Used for tables that need computed cells (grant totals,
     tier subheadings, condition-driven rows) or non-table emit
     chunks (intro counters, special list shapes). The schema-driven
     path is INTENTIONALLY narrow — bespoke logic stays in Python.

Per-migration recipe lives at the top of
`docs/design/markdown-master-outline-refactor.md` §"Phase 3".

Failure mode (Q5 of the design doc): an unknown directive aborts the
build via `sys.exit(1)` from the walker. A registered directive that
raises propagates — silent fallback would mask real bugs.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import IO, Callable

from .section_walker import RenderContext


log = logging.getLogger(__name__)


def _directive_hello(ctx: RenderContext, out: IO[str]) -> None:
    """No-op directive used to exercise the walker's dispatch path.

    Emits a single RTF comment-style paragraph that's visible in the
    diff but trivially distinguishable from real content. Kept around
    after Phase 1 as a smoke-test handle for `--use-markdown-master`
    end-to-end runs (e.g., add `!HELLO!` to the outline once,
    confirm it appears in the rendered RTF, remove).
    """
    out.write("\\pard !HELLO! directive fired\\par\n")


# ---------------------------------------------------------------------------
# Tabular-directive factory (declarative-schema path; Q6 brought into Phase 2)
# ---------------------------------------------------------------------------


def _apply_escape_policy(value: object, policy: str) -> str:
    """Convert a row attribute value to an RTF-emittable cell string.

    Two policies today:
      * `escape_rtf` — the default; runs `builders.escape_rtf` over the
        stringified value. Right choice for any field carrying
        author-typed text.
      * `raw` — passes the value through unmodified. Use ONLY for
        fields that arrive pre-formatted with RTF control words
        already in place (e.g., `Patent.co_inventors` carries `\\b`
        bold-for-me markers that escape would clobber).

    Function-local import: keeps `directives` ↔ `builders` cycle off
    the module-load critical path.
    """
    from .builders import escape_rtf
    text = str(value)
    if policy == "escape_rtf":
        return escape_rtf(text)
    if policy == "raw":
        return text
    raise ValueError(f"unknown escape policy: {policy!r}")


def _make_tabular_directive(name: str, schema: dict) -> Callable[[RenderContext, IO[str]], None]:
    """Build a directive renderer from a validated tabular schema.

    Returns a `(ctx, out) → None` callable that:
      1. Reads `ctx.<source>` (a list of row objects).
      2. If empty, emits nothing (matches `render_patents_section`'s
         skip-when-empty behavior).
      3. Builds an `RtfTable` with column widths + headers from the
         schema.
      4. For each row, projects schema fields onto cells with the
         schema's per-column escape policy.
      5. If `bookmark_column` is set, prefixes that column's cell with
         the bookmarked entry-code anchor (`{bold C.X.N.} value`) —
         the canonical "this is row N, click me" shape used by the
         existing patents + grant renderers.
      6. Emits the table + trailing `\\pard\\par`.

    Each step is the same shape regardless of which schema is being
    rendered — that's the load-bearing affordance of the declarative
    approach.
    """
    source_field = schema["source"]
    code_key = schema["code_key"]
    columns: list[dict] = schema["columns"]
    bookmark_column = schema.get("bookmark_column")

    def directive(ctx: RenderContext, out: IO[str]) -> None:
        # Function-local imports: dial in the cycle-free order on EVERY
        # call so module-load doesn't depend on import ordering.
        from .config import SECTION_CODES
        from .rtf import RtfTable, _ref_anchor
        from .styles import styled_inline
        rows = getattr(ctx, source_field, None) or []
        if not rows:
            return
        code_base = SECTION_CODES[code_key]
        widths = [c["width"] for c in columns]
        headers = [c["header"] for c in columns]
        table = RtfTable(column_widths=widths)
        table.add_header(headers)
        for idx, row in enumerate(rows, 1):
            entry_code = f"{code_base}.{idx}"
            cells: list[str] = []
            for col_idx, col in enumerate(columns):
                raw_value = getattr(row, col["field"])
                escape_policy = col.get("escape", "escape_rtf")
                rendered = _apply_escape_policy(raw_value, escape_policy)
                if col_idx == bookmark_column:
                    # Bookmarked entry-code prefix: bold-wrapped anchor +
                    # period + space + the cell value. Brace-scoping
                    # keeps the trailing space literal (the
                    # delimiter-eats-space trap from CLAUDE.md).
                    rendered = (
                        f"{{{styled_inline('entry_name', _ref_anchor(entry_code))}.}} "
                        f"{rendered}"
                    )
                cells.append(rendered)
            table.add_row(cells)
        out.write(table.render())
        out.write("\\pard\\par\n")

    # Decorate the closure for log messages / introspection.
    directive.__name__ = f"_directive_{name.lower()}"
    directive.__doc__ = (
        f"Auto-generated tabular directive for {name} (source={source_field}, "
        f"code_key={code_key}, {len(columns)} columns)."
    )
    return directive


# ---------------------------------------------------------------------------
# Registry assembly
# ---------------------------------------------------------------------------


# Public registry. Keys are the bare token form (no `!` wrap).
# Initialized with the bespoke Python directives; the tabular-schema
# loader extends it at import time below.
DIRECTIVES: dict[str, Callable[[RenderContext, IO[str]], None]] = {
    "HELLO": _directive_hello,
}


# Project-root-relative default path for the tabular schemas file.
# The CLI can override via `--table-schemas`. Set lazily at import
# time so test code can stage a fixture path via the env var (see
# `_DEFAULT_TABLE_SCHEMAS_PATH` resolution below).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_TABLE_SCHEMAS_PATH = str(_REPO_ROOT / "assets" / "table-schemas.yaml")


def register_tabular_directives(schemas_path: str | None = None) -> None:
    """Load `table-schemas.yaml` and register every entry as a directive.

    Idempotent — re-running replaces any existing tabular entries
    with fresh closures from the latest schema. Call sites:

      * Module-load (below) — registers the production schema by
        default so the CLI's default `--use-markdown-master` flow
        finds `!PATENTS_TABLE!` already in the registry.
      * Tests — can rebuild against a fixture schema by passing a
        path; the production registry is mutated in place so test
        teardown should restore it.

    Missing schema file is permissive (logged + skipped) — the walker
    will fail-loud on an unknown directive name at emit time.
    """
    # Function-local import: builders.py imports happen later in the
    # module load graph than directives.py' first-use path; keep the
    # import on the call edge to avoid load-order cycles.
    from .builders import load_table_schemas
    path = schemas_path or os.environ.get(
        "PUBS_EMITTER_TABLE_SCHEMAS", _DEFAULT_TABLE_SCHEMAS_PATH,
    )
    schemas = load_table_schemas(path)
    for name, schema in schemas.items():
        DIRECTIVES[name] = _make_tabular_directive(name, schema)
    log.debug(
        "directives: registered %d tabular directive(s) from %s",
        len(schemas), path,
    )


# Load + register at module import time. Tests that want to swap the
# schema file out can either set `PUBS_EMITTER_TABLE_SCHEMAS` before
# import, or call `register_tabular_directives(<path>)` after import
# to overwrite the entries.
register_tabular_directives()
