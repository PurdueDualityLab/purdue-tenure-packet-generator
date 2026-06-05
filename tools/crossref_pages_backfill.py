"""Crossref `pages` backfill for the bib database.

Iterates every bib entry missing a `pages` field and asks Crossref for it.
Writes verified hits to a YAML side cache (`assets/page-cache.yaml`).
The cache is merged into bib entries at render time by
`cli.merge_pages_cache` so the renderer transparently picks up new
backfill without anyone editing the bib.

Safety rails:
  * **Year cutoff** — only entries with `year <= current_year - 1`
    are queried. Crossref's index lags ~6-12 months for newer conference
    proceedings, and the false-match rate spikes on too-fresh papers
    (a title-similar OLDER paper gets matched against a new submission).
    Today (2026): cutoff is `year <= 2025`.
  * **Strict validation** — an entry is only accepted when BOTH the
    title bag-of-words overlap ≥ 0.8 AND the Crossref-returned year is
    within ±1 of the bib year. Either alone has produced false matches
    in pilot runs; both together filter them out.
  * **Idempotent cache** — if `page-cache.yaml` already has a key, the
    backfill skips Crossref and keeps the cached value. Re-running is
    free (zero API calls) and safe.

Usage:
    python3 tools/crossref_pages_backfill.py \\
        --bib assets/my_papers_full.bib \\
        --cache assets/page-cache.yaml \\
        [--limit 5]   # try N entries then stop (pilot mode)
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import bibtexparser
import yaml

# Re-use the production network plumbing — same polite-pool rate limit,
# same backoff/retry policy. The backfill is a sibling tool, not a
# competing one.
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "src"),
)
from pubs_emitter.config import CROSSREF_API_URL, DEFAULT_HEADERS  # noqa: E402
from pubs_emitter.network import polite_get  # noqa: E402


log = logging.getLogger("crossref-backfill")


# --- Validation knobs (changeable but proven in pilot) -----------------

TITLE_OVERLAP_THRESHOLD = 0.8
"""Bag-of-significant-words overlap. 0.5 was the pilot default and let
through 2 false matches (newer papers matched to older similar-title
work). 0.8 caught both in retrospective."""

YEAR_TOLERANCE = 1
"""Max |crossref_year - bib_year|. 0 would reject Crossref entries
whose `issued` date is the online-first year while the bib uses the
proceedings year; 1 covers that case without admitting older papers
that happen to share words."""


def _significant_words(s: str) -> set[str]:
    """Lowercase 3+ letter words. Filters out 'a', 'of', 'the', etc.,
    which dominate short titles and inflate overlap scores."""
    return {w.lower() for w in re.findall(r"[A-Za-z]{3,}", s)}


def title_overlap(a: str, b: str) -> float:
    """Word-overlap ratio (intersection / smaller-set size). The smaller
    side denominator handles "Long Subtitle vs Short Title" cases — if
    every word of the short title appears in the long one, that's a
    match worth keeping."""
    A, B = _significant_words(a), _significant_words(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def extract_crossref_year(item: dict) -> Optional[int]:
    """Crossref puts the year in `issued.date-parts[0][0]` (or
    `published-print.date-parts[0][0]` for journals). Return None when
    we can't find one — a missing year is a soft rejection signal."""
    for field in ("issued", "published-print", "published-online", "created"):
        parts = (item.get(field) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def crossref_lookup(title: str, authors: str) -> Optional[dict]:
    """Query Crossref's `/works` endpoint for the best title+author hit.
    Returns the raw `items[0]` dict (caller validates) or None on miss /
    HTTP failure. Routes through `polite_get` so we share the
    per-host rate limiter with the main renderer."""
    first_author = authors.split(" and ")[0] if authors else ""
    query = urllib.parse.quote(f"{title} {first_author}")
    url = (
        f"{CROSSREF_API_URL}"
        f"?query={query}&select=DOI,title,page,issued,published-print&rows=1"
    )
    resp = polite_get("api.crossref.org", url, headers=DEFAULT_HEADERS)
    if resp is None or resp.status_code != 200:
        return None
    try:
        items = resp.json().get("message", {}).get("items", [])
    except ValueError:
        return None
    return items[0] if items else None


def validate(bib_title: str, bib_year: int, item: dict) -> tuple[bool, str]:
    """Strict accept/reject. Returns (accepted, reason). Reason is a
    short string for the report so the user can audit at a glance."""
    returned_title = (item.get("title") or [""])[0]
    overlap = title_overlap(bib_title, returned_title)
    cr_year = extract_crossref_year(item)

    if overlap < TITLE_OVERLAP_THRESHOLD:
        return False, f"title overlap {overlap:.2f} < {TITLE_OVERLAP_THRESHOLD}"
    if cr_year is None:
        return False, "no year in Crossref response"
    if abs(cr_year - bib_year) > YEAR_TOLERANCE:
        return False, f"year mismatch (bib {bib_year}, crossref {cr_year})"
    return True, f"overlap {overlap:.2f}, year {cr_year}"


# --- Cache I/O ---------------------------------------------------------


def load_cache(path: Path) -> dict[str, dict]:
    """YAML file: top-level mapping `bib_key -> {pages, doi, source,
    fetched}`. Missing file → empty cache (first run is fine)."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        log.warning("%s: root is not a mapping; treating as empty", path)
        return {}
    return data


def save_cache(path: Path, cache: dict[str, dict]) -> None:
    """Atomic-ish write: temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cache, f, sort_keys=True, allow_unicode=True)
    tmp.replace(path)


# --- Main loop ---------------------------------------------------------


def run(bib_path: Path, cache_path: Path, limit: Optional[int]) -> None:
    """One pass over the bib; one Crossref call per missing-pages entry
    (cached entries are skipped). Reports counts at the end."""
    with bib_path.open("r", encoding="utf-8") as f:
        db = bibtexparser.load(f)
    cache = load_cache(cache_path)

    cutoff = datetime.now().year - 1  # safety: only year <= last year
    log.info("Year cutoff: <= %d", cutoff)

    candidates: list[dict] = []
    for entry in db.entries:
        key = entry.get("ID", "")
        if not key:
            continue
        if entry.get("pages"):
            continue
        if key in cache:
            continue
        year = entry.get("year", "")
        if not year.isdigit() or int(year) > cutoff:
            continue
        # Skip patents / arxiv-only: misc entries rarely have Crossref data
        # and consume the rate-limit budget for nothing.
        if entry.get("ENTRYTYPE", "").lower() not in ("inproceedings", "article", "incollection"):
            continue
        candidates.append(entry)

    log.info("Candidates: %d entries to query", len(candidates))
    if limit is not None:
        candidates = candidates[:limit]
        log.info("Limit: querying only first %d", len(candidates))

    accepted: list[tuple[str, str, str, str]] = []  # (key, pages, doi, reason)
    rejected: list[tuple[str, str, str]] = []  # (key, doi, reason)
    no_hit: list[str] = []

    today = datetime.now().strftime("%Y-%m-%d")
    for i, entry in enumerate(candidates, 1):
        key = entry["ID"]
        title = (entry.get("title") or "").replace("\n", " ").strip()
        authors = entry.get("author") or ""
        year = int(entry["year"])
        item = crossref_lookup(title, authors)
        if item is None:
            no_hit.append(key)
            log.info("[%d/%d] %s: no hit", i, len(candidates), key)
            continue
        ok, reason = validate(title, year, item)
        doi = item.get("DOI", "")
        page = item.get("page", "") or ""
        if ok and page:
            cache[key] = {
                "pages": page,
                "doi": doi,
                "source": "crossref",
                "fetched": today,
                "bib_year": year,
            }
            accepted.append((key, page, doi, reason))
            log.info("[%d/%d] %s: ACCEPT (%s) — %s", i, len(candidates), key, reason, page)
            save_cache(cache_path, cache)  # incremental save so a crash doesn't lose progress
        elif ok and not page:
            rejected.append((key, doi, "passed strict check but Crossref has no page field"))
            log.info("[%d/%d] %s: no-page (%s)", i, len(candidates), key, reason)
        else:
            rejected.append((key, doi, reason))
            log.info("[%d/%d] %s: REJECT (%s)", i, len(candidates), key, reason)

    # Final report
    print()
    print("=" * 70)
    print(f"Year cutoff: <= {cutoff}")
    print(f"Candidates queried: {len(candidates)}")
    print(f"Accepted (cached + persisted): {len(accepted)}")
    print(f"Rejected (Crossref returned something but failed strict check): {len(rejected)}")
    print(f"No hit (Crossref returned nothing): {len(no_hit)}")
    print(f"Cache file: {cache_path} ({len(cache)} total entries)")
    print("=" * 70)
    if accepted:
        print("\nAccepted (first 20):")
        for key, page, doi, reason in accepted[:20]:
            print(f"  {key}: pages={page}  ({reason})")
    if rejected:
        print("\nRejected (first 20) — review manually if any look promising:")
        for key, doi, reason in rejected[:20]:
            print(f"  {key}: {reason}")
    if no_hit:
        print(f"\nNo Crossref hit at all: {len(no_hit)} entries — likely arXiv-only / patents / unindexed")


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bib", required=True, type=Path, help="Path to the bib file")
    p.add_argument(
        "--cache", default=Path("assets/page-cache.yaml"), type=Path,
        help="Path to the YAML page-cache (default: assets/page-cache.yaml)",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Query at most N entries this run (pilot mode)",
    )
    args = p.parse_args(argv)
    run(args.bib, args.cache, args.limit)


if __name__ == "__main__":
    main()
