"""Print a per-paper breakdown of the stats-module classifications so
the user can audit the macro outputs against an external database.

Mirrors the cli.main build phase up to publications, then iterates and
emits one row per Citation with: section, rank, year, first-author,
student-led? bool, peer-reviewed? bool, undergrad-coauthor? bool,
title. Concludes with the macro totals so the receipts add up to the
numbers the build emits.

Run:
    .venv/bin/python tools/stats_receipts.py \\
        --bib assets/my_papers_full.bib \\
        --non-scholar assets/non-scholar-work.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

# Allow importing the package without install.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bib", default="assets/my_papers_full.bib")
    ap.add_argument("--non-scholar", default="assets/non-scholar-work.yaml")
    ap.add_argument("--cache", default="lookup_cache.sqlite")
    args = ap.parse_args()

    os.environ.setdefault("PUBS_EMITTER_CONFIG", "assets/config.yaml")

    import bibtexparser
    from pubs_emitter.authors import lookup_student_type
    from pubs_emitter.builders import (
        build_book_chapter,
        build_citation,
        build_cve_from_yaml,
        build_disclosure_from_yaml,
        build_patent,
        build_thesis,
        load_non_scholar,
    )
    from pubs_emitter.config import MANUAL_LINKS, STUDENTS
    from pubs_emitter.db import open_db, populate_students, seed_manual_links
    from pubs_emitter.statistics import (
        _PEER_REVIEWED_RANKS,
        StatsContext,
        compute_all,
    )
    from pubs_emitter.types import Citation, Patent
    from pubs_emitter.venue import (
        is_book_chapter_entry,
        is_patent_entry,
        is_thesis_entry,
        normalize_title,
    )

    conn = open_db(args.cache)
    seed_manual_links(conn, MANUAL_LINKS)

    with open(args.bib) as f:
        bibdb = bibtexparser.load(f)
    entries = bibdb.entries

    non_scholar = load_non_scholar(args.non_scholar)

    # Chair/Co-Chair filter (per user request 2026-06-06): "student-led"
    # counts only students whose role on the YAML graduate_students list
    # is `Chair` or `Co-Chair`. Committee-member roles are excluded.
    # Names from the static `STUDENTS["G"]` config.yaml list are KEPT
    # (they're spelling-variant baselines for known chair/co-chair students),
    # but any name in `STUDENTS["G"]` whose YAML entry has role
    # `Committee member` is REMOVED.
    committee_only = {
        s.get("name", "").strip()
        for s in (non_scholar.get("graduate_students") or [])
        if s.get("role") == "Committee member"
    }
    chair_cochair_yaml = [
        s.get("name", "") for s in (non_scholar.get("graduate_students") or [])
        if s.get("name")
        and s.get("role") in ("Chair", "Co-Chair")
    ]
    base_g = [n for n in STUDENTS.get("G", []) if n.strip() not in committee_only]
    merged_students = {k: list(v) for k, v in STUDENTS.items()}
    merged_students["G"] = list({*base_g, *chair_cochair_yaml})
    populate_students(conn, merged_students)

    publications: dict = defaultdict(list)
    patents: list = []
    patent_impacts = dict(non_scholar.get("patent_impacts") or {})
    for entry in entries:
        if is_patent_entry(entry):
            patents.append(build_patent(conn, entry, patent_impacts))
        elif is_thesis_entry(entry):
            # held internally; not part of publications
            pass
        elif is_book_chapter_entry(entry):
            cit = build_book_chapter(conn, entry)
            publications[cit.section].append(cit)
        else:
            try:
                cit = build_citation(conn, entry)
                publications[cit.section].append(cit)
            except Exception as e:
                print(f"# skipped: {entry.get('ID')} → {e}", file=sys.stderr)
                continue

    for cve in non_scholar.get("cves") or []:
        cit = build_cve_from_yaml(conn, cve, entries)
        publications[cit.section].append(cit)
    for disc in non_scholar.get("security_disclosures") or []:
        cit = build_disclosure_from_yaml(conn, disc, entries)
        publications[cit.section].append(cit)

    # StatsContext derives title_to_bib internally from bib_entries.
    ctx = StatsContext(
        publications=publications,
        bib_entries=entries,
        patents=patents,
        conn=conn,
    )
    title_to_bib = ctx.title_to_bib

    PURDUE_JOIN_YEAR = 2020

    def first_author(bib: dict) -> str:
        raw = bib.get("author", "") or ""
        return raw.split(" and ", 1)[0].strip()

    def first_author_role(bib: dict) -> str:
        if not bib:
            return "?(no-bib)"
        fa = first_author(bib)
        if not fa:
            return "?(empty)"
        kind = lookup_student_type(conn, fa)
        if kind == "G":
            return f"G:{fa}"
        if kind == "U":
            return f"U:{fa}"
        # Senior co-author? Check advisors. Not strictly needed here.
        return f"-:{fa}"

    def has_undergrad_coauthor(bib: dict) -> bool:
        raw = (bib.get("author", "") or "") if bib else ""
        if not raw:
            return False
        for name in raw.split(" and "):
            if lookup_student_type(conn, name.strip()) == "U":
                return True
        return False

    # ----- Print receipts ------------------------------------------------
    print("# Per-paper receipts (1 row per Citation).")
    print("# Columns: rank | section | year | atPurdue | studentLed | "
          "undergradCoauthor | firstAuthor | title")
    print()

    all_cits: list[Citation] = []
    for sec_cits in publications.values():
        all_cits.extend(sec_cits)
    # Stable sort: by section then year
    all_cits.sort(key=lambda c: (c.section, c.year, c.title))

    counters: dict[str, int] = defaultdict(int)

    for c in all_cits:
        bib = title_to_bib.get(normalize_title(c.title)) or {}
        at_purdue = c.year >= PURDUE_JOIN_YEAR
        fa_role = first_author_role(bib)
        student_led = fa_role.startswith(("G:", "U:"))
        undergrad_co = has_undergrad_coauthor(bib)
        peer = c.rank in _PEER_REVIEWED_RANKS

        marker_sl = "SL" if student_led else "  "
        marker_pr = "PR" if peer else "  "
        marker_p = "P" if at_purdue else " "
        marker_uc = "UC" if undergrad_co else "  "

        counters["all"] += 1
        if peer:
            counters["peer_reviewed"] += 1
            if at_purdue:
                counters["peer_reviewed_at_purdue"] += 1
            if student_led:
                counters["student_led_peer_reviewed"] += 1
            if undergrad_co:
                counters["papers_with_ugrad_coauthor"] += 1
        if c.rank == "Rank 1":
            counters["tier_1"] += 1
            if at_purdue:
                counters["tier_1_at_purdue"] += 1
            if student_led:
                counters["student_led_tier_1"] += 1
        if c.rank == "Rank 2":
            counters["tier_2"] += 1
        if c.rank == "Rank 3":
            counters["tier_3"] += 1
        if c.rank == "Workshop":
            counters["workshop"] += 1
        if c.rank == "Magazine":
            counters["magazine"] += 1

        print(f"{c.rank:14s} | {c.section[:14]:14s} | {c.year} | "
              f"{marker_p} | {marker_sl} | {marker_uc} | "
              f"{fa_role[:30]:30s} | {c.title[:80]}")

    print()
    print("# Counts derived from the receipts above:")
    print(f"#   total papers (incl Magazine/Preprint/CVE) = {counters['all']}")
    print(f"#   peer-reviewed (Tier1+Tier2+Tier3+Workshop+BookChapter) = {counters['peer_reviewed']}")
    print(f"#     ... at Purdue (year >= {PURDUE_JOIN_YEAR}) = {counters['peer_reviewed_at_purdue']}")
    print(f"#     ... student-led = {counters['student_led_peer_reviewed']}")
    print(f"#     ... with undergrad co-author = {counters['papers_with_ugrad_coauthor']}")
    print(f"#   Tier-1 = {counters['tier_1']}")
    print(f"#     ... at Purdue = {counters['tier_1_at_purdue']}")
    print(f"#     ... student-led = {counters['student_led_tier_1']}")
    print(f"#   Tier-2 = {counters['tier_2']}")
    print(f"#   Tier-3 = {counters['tier_3']}")
    print(f"#   Workshop = {counters['workshop']}")
    print(f"#   Magazine = {counters['magazine']}")
    print()
    print("# Stats-module macro outputs (the numbers the build emits):")
    macros = compute_all(ctx)
    for k in (
        "NUM_PEER_REVIEWED_WORKS",
        "NUM_WORKS_AT_PURDUE",
        "NUM_TIER_1",
        "NUM_TIER_2",
        "NUM_LED_BY_ADVISEES",
        "NUM_LED_BY_ADVISEES_TIER_1",
        "NUM_PAPERS_WITH_UNDERGRADUATE_COAUTHORS",
        "NUM_UNDERGRADUATE_COAUTHORS",
    ):
        if k in macros:
            print(f"#   #{k} = {macros[k]}")


if __name__ == "__main__":
    main()
