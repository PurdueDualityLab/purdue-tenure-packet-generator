"""Per-section block builders for the IR refactor.

Each module in this package exports a `render_<section>_blocks` (or
similar) function that takes typed input + returns `list[Block]` (the
IR vocabulary in `pubs_emitter.ir`). The `RtfWriter` translates the
blocks to RTF.

The architectural goal is bug-class partitioning: a row-construction
bug is here; an RTF-emit bug is in `writer_rtf.py`; a style choice is
in `StyleAttrs` (in `styles.py`). See
`docs/design/ir-based-emit-disentangling-260606.md` for the full
motivation + migration plan.

Currently migrated (Phase 4):
  * `patents` — C.19 Issued U.S. and International Patents.
"""
from __future__ import annotations
