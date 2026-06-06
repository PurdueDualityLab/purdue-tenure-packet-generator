/*
OUTLINE — markdown-master walker source

Phase 2+ of the markdown-master outline refactor. This file is
consumed by the walker
(`pubs_emitter.section_walker.walk_section_prose`) when
`--use-markdown-master` is set. Headings here become the rendered
packet's outline; !DIRECTIVE! lines dispatch to Python renderers
registered in `pubs_emitter.directives.DIRECTIVES`.

In Phase 2 this file contains only the C.19 Patents stanza — the
walker emits THAT chunk while the legacy emit-order block in
`rtf.write_rtf` handles every other section. The legacy emit-order
block skips C.19 when `--use-markdown-master` is set so the section
isn't double-emitted.

Phase 3 migrates more sections (each adds a stanza here + a
directive registration + a skip in the legacy emit-order block).
Phase 4 turns this file into the document's master outline —
`section-prose.md` then becomes a free-form-prose sidecar merged
into here.

Heading-depth → renderer mapping:
  #     Roman section heading      (III. / V.)
  ##    Group heading              (A. / B. / C.)
  ###   Section-level heading      (A.1 / C.19 / B.3)
  ####  Sub-section heading        (A.1.1 / C.16.2.3)

Directives accept token form [A-Z_][A-Z0-9_]* on their own line.
Tokens mid-paragraph render as literal text.

This block is editor-only — stripped before parsing along with any
other comment block in the file. Use the convention to leave
authoring guidance / template prompts / reminders alongside the
real content. Avoid nested close-comment markers inside a block —
the strip is non-greedy and would close at the first one.
*/

### C.19 Issued U.S. and International Patents.

!PATENTS_TABLE!
