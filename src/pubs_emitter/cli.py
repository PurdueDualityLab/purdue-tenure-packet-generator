"""CLI entry point: parse args + orchestrate the 5-phase pipeline.

Pipeline (see module docstrings for each phase):
  1. plan_lookups()     → list[NetworkTask] for items not in cache
  2. dispatch_parallel() → run concurrently via ThreadPoolExecutor
  3. commit_results()   → persist into the appropriate cache table
  4. build_*()          → assemble Citation / Patent records (warm cache)
  5. write_rtf()        → emit final RTF (citations + patent table)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from typing import Optional, cast

import bibtexparser

from .builders import (
    build_citation,
    build_cve_from_yaml,
    build_patent,
    load_non_scholar,
    validate_non_scholar,
)
from .config import (
    DEFAULT_DB_FILE,
    DEFAULT_MAX_WORKERS,
    DEFAULT_OUT_FILE,
    SECTION_CODES,
    SECTION_ORDER,
    STUDENTS,
)
from .db import LOOKUP_STATS, open_db, populate_students
from .lookup import commit_results, dispatch_parallel, plan_lookups
from .rtf import build_paper_index, write_rtf
from .types import BibEntry, Patent, Publications
from .venue import is_patent_entry


log = logging.getLogger("pubs-emitter")


# ----- I/O helpers --------------------------------------------------------


def load_bib(path: str) -> list[BibEntry]:
    log.info("Loading BibTeX from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        db = bibtexparser.load(f)
    log.info("Loaded %d entries", len(db.entries))
    return cast(list[BibEntry], db.entries)


def log_section_summary(publications: Publications, patents: list[Patent]) -> None:
    for section in SECTION_ORDER:
        if section == "Patents":
            if patents:
                log.info(
                    "Section %s '%s': %d patents",
                    SECTION_CODES[section], section, len(patents),
                )
            continue
        cits = publications.get(section, [])
        if cits:
            log.info(
                "Section %s '%s': %d papers",
                SECTION_CODES[section], section, len(cits),
            )


def log_lookup_stats() -> None:
    """Summarize lookup-cache effectiveness across DOI, patent, CVE caches."""
    def fmt(label: str, hits: int, misses: int) -> None:
        total = hits + misses
        if total:
            rate = 100.0 * hits / total
            log.info(
                "%s cache: %d/%d hits (%.0f%%); %d sequential fallback fetch(es)",
                label, hits, total, rate, misses,
            )

    fmt("DOI", LOOKUP_STATS["doi_cache_hits"], LOOKUP_STATS["doi_cache_misses"])
    fmt("Patent", LOOKUP_STATS["patent_cache_hits"], LOOKUP_STATS["patent_cache_misses"])
    fmt("CVE", LOOKUP_STATS["cve_cache_hits"], LOOKUP_STATS["cve_cache_misses"])
    arxiv = LOOKUP_STATS["arxiv_constructed"]
    if arxiv:
        log.info("arXiv: %d DOIs constructed (cache bypassed)", arxiv)


# ----- CLI ----------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pubs-emitter",
        description=(
            "Generate a formatted RTF publication list from a BibTeX file. "
            "Output is suitable for pasting into Word with formatting preserved "
            "(bold for me, superscripts for student/advisor roles, hyperlinks, "
            "patent table, CVE entries)."
        ),
        epilog=(
            "Environment:\n"
            "  LOG_LEVEL              Logging verbosity (DEBUG/INFO/WARNING/ERROR). Default INFO.\n"
            "  PATENTSVIEW_API_KEY    Optional; enables USPTO issue-date lookup for patents.\n"
            "  NVD_API_KEY            Optional; raises NVD rate limit ~10x.\n"
            "  PUBS_EMITTER_CONFIG    Override path to assets/config.yaml.\n"
            "  PUBS_EMITTER_USER_AGENT Override the HTTP User-Agent (mailto:).\n\n"
            "BibTeX conventions:\n"
            "  - Citations: journal / booktitle must begin with a bracketed tag,\n"
            "    e.g. `[ICSE'25]`. Acronym must appear in assets/config.yaml.\n"
            "  - Patents: @misc whose `publisher` or `note` contains 'patent';\n"
            "    `note = {US Patent 11,176,090}` carries the number.\n"
            "  - CVEs live in the YAML side file, NOT the bib. See --non-scholar.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--bib", required=True, metavar="PATH",
        help="Path to the input BibTeX file (required).",
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT_FILE, metavar="PATH",
        help=f"Path to write the output RTF file. Default: {DEFAULT_OUT_FILE}",
    )
    parser.add_argument(
        "--cache", default=DEFAULT_DB_FILE, metavar="PATH",
        help=(
            f"Path to the SQLite lookup cache. Created if missing; safe to delete "
            f"to force re-lookup. Default: {DEFAULT_DB_FILE}"
        ),
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_MAX_WORKERS, metavar="N",
        help=f"Parallel network workers. Default: {DEFAULT_MAX_WORKERS}",
    )
    parser.add_argument(
        "--non-scholar", metavar="PATH", default=None,
        help=(
            "Path to YAML side file with non-Scholar work (CVEs etc.). "
            "Bib stays Scholar-canonical; this file fills the gaps. Optional."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    args = parse_args(argv)
    conn = open_db(args.cache)
    try:
        populate_students(conn, STUDENTS)
        entries = load_bib(args.bib)

        # Load + validate YAML side file (CVEs etc.) before any network work.
        non_scholar = load_non_scholar(args.non_scholar)
        validate_non_scholar(non_scholar, entries)

        # Phases 1-3: plan / dispatch / commit.
        tasks = plan_lookups(conn, entries, non_scholar)
        if tasks:
            results = dispatch_parallel(tasks, max_workers=args.workers)
            commit_results(conn, results)
        else:
            log.info("Cache is fully warm; no network lookups needed.")

        # Phase 4: build citations / patents from bib (warm cache).
        publications: Publications = defaultdict(list)
        patents: list[Patent] = []
        for entry in entries:
            if is_patent_entry(entry):
                patents.append(build_patent(conn, entry))
            else:
                cit = build_citation(conn, entry)
                publications[cit.section].append(cit)

        # Phase 4b: build CVE citations from YAML, route to C.5.
        for cve in non_scholar.get("cves") or []:
            cit = build_cve_from_yaml(conn, cve, entries)
            publications[cit.section].append(cit)

        # Chronological order (oldest first) within each section.
        for section in publications:
            publications[section].sort(key=lambda c: c.year)
        patents.sort(key=lambda p: p.year)

        # Back-pointer index — must run AFTER chrono-sort so C.X.Y is stable.
        paper_index = build_paper_index(publications)

        log_section_summary(publications, patents)
        log_lookup_stats()

        # Phase 5: render.
        write_rtf(args.out, publications, patents, paper_index)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
