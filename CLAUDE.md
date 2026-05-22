# CLAUDE.md — pubs-emitter

A small Python tool that generates a formatted RTF publication list for a
tenure packet from a BibTeX file (`my_papers.bib`, exported from Google
Scholar) and an optional YAML side file for non-Scholar work
(`non-scholar-work.yaml`, e.g. CVE disclosures).

User-facing description lives in `README.md`. This file captures the
**non-derivable** rules, conventions, and pitfalls — read this before
editing.

## Pipeline (5 phases in `cli.main`)

1. **`plan_lookups`** — walks every bib entry + CVE, emits `NetworkTask`s
   only for items missing from the SQLite cache.
2. **`dispatch_parallel`** — runs all tasks concurrently via
   `ThreadPoolExecutor`. Per-host `RateLimiter` serializes workers hitting
   the same API. Retry-with-backoff on `429`/`5xx`/connection errors.
3. **`commit_results`** — persists fetched data into `doi_cache` /
   `patent_cache` / `cve_cache` tables in `lookup_cache.sqlite`.
4. **`build_*`** — converts bib entries → `Citation` / `Patent`;
   converts YAML CVE entries → `Citation` (rank=`"CVE"`, section=C.5).
   By this point the cache is warm; build is local-only.
5. **`write_rtf`** — emits the final document. Sections are
   chrono-sorted, paper-index built for back-pointers, then rendered.

## Modules (src/pubs_emitter/)

| File | What lives there |
|------|------------------|
| `types.py` | `Citation`, `Patent`, `NetworkTask`, type aliases (`Category`, `Rank`, `Section`, `BibEntry`, `Publications`) |
| `config.py` | Loads `assets/config.yaml` (ME, ADVISORS, STUDENTS, RANKS) + code-side constants (SECTION_*, TIER_LABELS, ORG_EXPANSIONS, etc.) |
| `latex.py` | `decode_latex` — wraps pylatexenc |
| `db.py` | SQLite cache schema + read-only accessors + `LOOKUP_STATS` counter dict |
| `network.py` | `RateLimiter`, `polite_get`, the four `try_*` API fetchers, `titles_similar`, `is_no_doi_venue` |
| `authors.py` | Name parsing + `format_author` (citation form) + `format_inventors` (patent-table form) |
| `venue.py` | `parse_venue`, `lookup_rank`, `classify_entry`, `is_patent_entry`, `extract_arxiv_id`, `extract_cve_id`, `normalize_title` |
| `lookup.py` | `NetworkTask` planning, parallel dispatch, result commit, cache-aware fetchers (`fetch_doi_or_url`, `fetch_patent_date`, `fetch_cve_data`); also `extract_patent_number` |
| `builders.py` | `build_citation`, `build_patent`, `build_cve_from_yaml`, `load_non_scholar`, `validate_non_scholar`, plus rendering primitives `escape_rtf` / `parse_year` / `derive_section` |
| `rtf.py` | `RtfTable` class, `render_citation`, `render_patents_section`, `build_paper_index`, `write_rtf` |
| `cli.py` | `parse_args` + `main` (the 5-phase orchestrator) |

Dependency direction: everything depends on `types` + `config`. `db` depends
on `types`. `network` depends on `config` + `types`. Higher-level modules
(`lookup`, `builders`, `rtf`) compose the lower-level ones. **No cycles.**
If you find yourself wanting a circular import, you've put a function in
the wrong module.

## Conventions enforced at runtime (fatal errors)

1. **`journal` / `booktitle` MUST begin with `[ACRONYM'YY]`** — checked in
   `build_citation`. Acronym is looked up in `assets/config.yaml`'s
   `ranks:` block; unknown acronym → fatal. Add to YAML, don't hard-code.
2. **arXiv entries MUST carry an arXiv ID** — checked in
   `builders.resolve_link`. ID can come from `eprint = {...}` field OR
   from `arXiv:NNNN.NNNN` text in the journal field. We build the DOI as
   `https://doi.org/10.48550/arXiv.<id>` deterministically; no API call.
3. **YAML CVE `paper_title`, if set, MUST match a bib entry** — checked in
   `validate_non_scholar`. Match is case-insensitive + whitespace-normalized.
   Unresolved → fatal. The bib stays Scholar-canonical; the YAML is the
   user's hand-curated side data.
4. **YAML CVE entries MUST have `cve_id` + `organization`** and at least
   one of (`paper_title`, `disclosers`). All checked at load time.

## Things that look weird but are intentional

- **The bib is read-only / Scholar-canonical.** Don't add CVEs to it; they
  go in `non-scholar-work.yaml`. Don't add custom fields you'd hate to lose
  on the next Scholar export.
- **`format_inventors` returns RTF-marked-up text; do NOT `escape_rtf` it.**
  The patent table cell's `\b ...\b0` markup must survive to the output.
  `render_patents_section` skips escaping for that cell specifically.
- **NVD `published` date is the CVE's chrono-sort key** — not the bib year.
  CVE C.5 entries land at their disclosure date, which is what reviewers want
  to see.
- **Crossref skipped entirely for USENIX venues.** USENIX doesn't register
  DOIs with Crossref; asking returns a fuzzy-wrong match. DBLP is still tried
  and usually returns a `usenix.org` URL. See `NO_DOI_ACRONYM_PREFIXES`.
- **Title-similarity check on Crossref + DBLP results.** Jaccard overlap of
  significant tokens; below `TITLE_MATCH_THRESHOLD` (0.6) the match is
  rejected and we fall through. Logged as "likely-wrong match" — those
  warnings are signal for bib refinement.
- **Per-host rate limiters live in `network.py` module-level dict.**
  `_HOST_LIMITERS` is shared across all `polite_get` callers and threads.
  NVD's interval is set based on `NVD_API_KEY` env var at import time.
- **Lookup planning uses `functools.partial`, not lambdas.** Lambdas with
  default args (`lambda x=foo: ...`) trip mypy and are easy to mis-bind in
  loops. `partial(fn, arg)` is mypy-clean and binds cleanly.

## Adding new things

- **New venue?** Add the acronym under the appropriate rank in
  `assets/config.yaml`. No code change.
- **New student?** Add to `students.G` or `students.U` in
  `assets/config.yaml`. The loader auto-generates the "Last, First" reverse
  form for BibTeX matching.
- **New section type** (e.g., datasets, talks)? Steps:
  1. Add to `Section` Literal in `types.py`
  2. Add to `SECTION_ORDER`, `SECTION_CODES`, `SECTION_HEADINGS` in `config.py`
  3. Decide where its source data lives (probably a new top-level key in
     `non-scholar-work.yaml`, like the existing `cves:`)
  4. Add a `build_X_from_yaml(...)` in `builders.py`
  5. Wire into `plan_lookups` (if it needs API calls) and `cli.main`
  6. Either render through `render_citation` (if it fits citation shape) or
     add a `render_X_section` mirroring `render_patents_section`
- **New API source?** Add a `try_X(...)` in `network.py`. Add the host to
  `_HOST_LIMITERS`. Add a new `kind` and cache table if needed, then update
  `plan_lookups` + `commit_results` + a `fetch_X` in `lookup.py`.

## Quality gates

```bash
.venv/bin/pylint src/pubs_emitter   # 10.00/10 baseline
.venv/bin/mypy                      # 0 errors baseline
./pubs-emitter.py --bib my_papers_sample.bib   # smoke
```

Keep both at green. The pylint config in `pyproject.toml` disables the
noisy-style nags but keeps every substantive check; new code is held to the
same bar. If you need to suppress an unavoidable warning, prefer an inline
`# pylint: disable=<name>` with a same-line comment explaining *why*.

## Setup / env

- **`./setup.sh`** is the one-command bootstrap: creates `.venv`, installs
  the package editable with dev extras (`pylint`, `mypy`, type stubs).
  Idempotent — safe to re-run.
- **`pyproject.toml`** is the source of truth for deps + lint configs. No
  `requirements.txt`.
- **`PUBS_EMITTER_CONFIG`** env var overrides `assets/config.yaml` path
  (useful if you ever want per-bib config).
- **`NVD_API_KEY` / `PATENTSVIEW_API_KEY`** are optional — both are
  rate-limit boosters; the tool degrades gracefully without them
  (NVD goes from 50/30s to 5/30s; PatentsView lookup is skipped entirely
  and patents fall back to the bib date).

## Pitfalls

- **`assets/config.yaml` not found** → import-time crash from `config.py`.
  Either the file is missing or `PUBS_EMITTER_CONFIG` points somewhere
  invalid. Don't catch — fail loud.
- **DBLP can connection-reset under load.** Already retried 3× with
  backoff. If you see persistent `Connection reset by peer`, slow the
  `dblp.org` `RateLimiter` interval from `0.2` to `0.5` (or higher).
- **Sequential-fallback fetch** (in `lookup.fetch_*`) is a defensive
  backstop, not the primary path. If you see "sequential fallback" in
  the log, planning missed something — investigate `plan_lookups` rather
  than relying on the fallback.
- **arXiv DOI is constructed, not fetched.** The arXiv DOI scheme
  `10.48550/arXiv.<id>` has been canonical since 2022. Don't add a Crossref
  query path for arXiv entries — it'll either fail or return a fuzzy match.
- **Modifying `Citation` is a cross-cutting change.** Every callsite must
  pass the new field. The NamedTuple gives default values for backwards
  compat (`back_ref_title: Optional[str] = None`), but that's a comfort,
  not a contract.
