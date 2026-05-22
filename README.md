# pubs-emitter

Generates a formatted RTF publication list from a BibTeX file and an optional
YAML side file for non-Scholar work (CVEs, etc.). Output is ready to paste
into Word with formatting preserved: bold for me, superscripts for student /
advisor / last-author roles, italic venue + tier, clickable hyperlinks to
DOIs / NVD / USPTO / publisher pages, and a hanging-indent layout.

## Quick start

```bash
./setup.sh                                       # one-time: creates .venv + installs editable + dev deps
./pubs-emitter.py --bib my_papers.bib            # generates publications.rtf
```

Or once `setup.sh` has run, the shorter form works from anywhere in the venv:

```bash
source .venv/bin/activate
pubs-emitter --bib my_papers.bib --non-scholar non-scholar-work.yaml
```

## Layout

```
publications/
├── pubs-emitter.py            # root driver (delegates to src/pubs_emitter/cli.py)
├── setup.sh                   # one-command bootstrap (venv + editable install)
├── pyproject.toml             # build + pylint + mypy config + project deps
├── README.md
├── .gitignore
├── assets/
│   ├── config.example.yaml    # committed schema + starter venue rankings
│   └── config.yaml            # ME, ADVISORS, STUDENTS, RANKS (gitignored — your data)
└── src/
    └── pubs_emitter/
        ├── __init__.py
        ├── types.py           # Citation, Patent, NetworkTask + type aliases
        ├── config.py          # loads assets/config.yaml + code-side constants
        ├── latex.py           # decode_latex
        ├── db.py              # SQLite cache + LOOKUP_STATS
        ├── network.py         # RateLimiter, polite_get, try_crossref/dblp/nvd/patentsview
        ├── authors.py         # name parsing + format_author / format_inventors
        ├── venue.py           # parse_venue, lookup_rank, classify_entry, etc.
        ├── lookup.py          # plan / dispatch / commit + cache-aware fetchers
        ├── builders.py        # build_citation / build_patent / build_cve_from_yaml
        ├── rtf.py             # RtfTable, render_citation, write_rtf
        └── cli.py             # parse_args + main()
```

The bib (`my_papers.bib`), CVE YAML (`non-scholar-work.yaml`), live user
config (`assets/config.yaml`), cache (`lookup_cache.sqlite`), and output
(`publications.rtf`) are all gitignored. Only `assets/config.example.yaml`
— which carries the schema + starter venue rankings — is committed.

## Pipeline

`main()` runs five phases:

1. **`plan_lookups`** — walks every entry + CVE, emits `NetworkTask`s for
   cache misses only.
2. **`dispatch_parallel`** — runs all tasks concurrently via
   `ThreadPoolExecutor`. Each host has its own `RateLimiter` (Crossref 10/s,
   DBLP 5/s, NVD 5/30s without key or 50/30s with, PatentsView 0.5s). Retries
   transient HTTP failures with exponential backoff (1s, 2s, 4s) and honors
   `Retry-After` headers.
3. **`commit_results`** — persists results into the appropriate cache table
   (`doi_cache`, `patent_cache`, `cve_cache`).
4. **`build_*`** — assembles `Citation` / `Patent` records. By now the
   cache is warm; build is local-only.
5. **`write_rtf`** — emits the final RTF.

## BibTeX conventions

- **Citations** — `journal` / `booktitle` MUST begin with a bracketed
  acronym + year tag. The acronym is looked up in `assets/config.yaml` under
  `ranks:`. Examples:
  ```
  journal   = {[JSS'25] The Journal of Systems and Software}
  booktitle = {[ICSE'25] Proceedings of the International Conference on Software Engineering}
  journal   = {[arXiv'26] arXiv preprint arXiv:2605.10712}
  ```
- **Patents** — `@misc` whose `publisher` or `note` contains `patent`.
  `note = {US Patent 11,176,090}` carries the number; USPTO date lookup is
  attempted via PatentsView when `PATENTSVIEW_API_KEY` is set.
- **CVEs are NOT in the bib.** Bib stays Scholar-canonical. CVEs go in
  `non-scholar-work.yaml` (see below).

## Non-Scholar YAML (`--non-scholar`)

```yaml
cves:
  # CVE attached to a paper. C.5 entry inherits the paper's author list
  # (with student markers) and gets a "(see C.4.N)" back-pointer.
  - cve_id: CVE-2024-38373
    organization: FreeRTOS
    paper_title: "Engineering Patterns for Trust and Safety on Social Media Platforms: A Case Study of Mastodon and Diaspora"

  # Stand-alone CVE — no associated paper. Provide the disclosers explicitly.
  - cve_id: CVE-2025-1675
    organization: Zephyr-RTOS
    disclosers:
      - Davis, James C
      - Amusuo, Paschal C

  # Title override is optional — by default we use NVD's first-sentence description.
  - cve_id: CVE-2026-99999
    organization: FooBar
    title: Memory corruption in libfoobar parser
    disclosers:
      - Davis, James C
```

Required: `cve_id`, `organization`. Either `paper_title` OR `disclosers`
must be present. Unresolved `paper_title` (no exact match in the bib,
case- and whitespace-insensitive) → fatal exit.

## Output sections

| Code | Section | Source |
|------|---------|--------|
| C.2 | Journals | bib `journal` entries |
| C.4 | Conferences and Workshops | bib `booktitle` entries |
| C.5 | Other publications and products | bib magazine + arXiv + YAML CVEs |
| C.19 | Issued U.S. and International Patents | bib `@misc` + `note = US Patent ...` (RTF table) |

Within each section, entries are sorted chronologically (oldest first).
Tier is rendered inline at the end of each citation (italicized): `Tier 1`,
`Tier 2`, `Workshop`, `Magazine`, `Preprint`, `CVE`. No tier sub-headings.

## Environment variables

| Variable | Effect |
|----------|--------|
| `LOG_LEVEL` | `DEBUG` / `INFO` (default) / `WARNING` / `ERROR` |
| `PATENTSVIEW_API_KEY` | Enables USPTO issue-date lookup for patents |
| `NVD_API_KEY` | Raises NVD rate limit ~10x (5/30s → 50/30s) |
| `PUBS_EMITTER_CONFIG` | Override path to `assets/config.yaml` |
| `PUBS_EMITTER_USER_AGENT` | Override the HTTP User-Agent (mailto:) |

## Dev tools

```bash
.venv/bin/pylint src/pubs_emitter    # 10.00/10 baseline; CI-grade
.venv/bin/mypy                       # type-clean
```

Pylint and mypy configs live under `[tool.pylint]` and `[tool.mypy]` in
`pyproject.toml`. The pylint config disables the noisy style/code-org
categories (`missing-docstring`, `too-many-arguments`, etc.) but keeps every
substantive check (unused imports, broad except, dangerous default value,
etc.). Re-enable individual categories as your taste evolves.
