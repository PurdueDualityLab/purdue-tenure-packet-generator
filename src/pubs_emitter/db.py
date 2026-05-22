"""SQLite lookup cache: schema setup + read-only accessors + stats counters."""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional, cast

from .types import StudentType


log = logging.getLogger(__name__)


# Cache + lookup counters. Mutated by lookup.fetch_* helpers, summarized at
# the end of a run by cli.log_lookup_stats.
LOOKUP_STATS: dict[str, int] = {
    "doi_cache_hits": 0,
    "doi_cache_misses": 0,
    "arxiv_constructed": 0,
    "patent_cache_hits": 0,
    "patent_cache_misses": 0,
    "cve_cache_hits": 0,
    "cve_cache_misses": 0,
}


def open_db(path: str) -> sqlite3.Connection:
    """Open the lookup cache; create tables on first run; fresh students per run."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS doi_cache "
        "(title TEXT PRIMARY KEY, doi_or_url TEXT)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS patent_cache "
        "(patent_id TEXT PRIMARY KEY, issue_date TEXT)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS cve_cache "
        "(cve_id TEXT PRIMARY KEY, data_json TEXT)"
    )
    # Students table is rebuilt per run from the YAML config.
    cur.execute("DROP TABLE IF EXISTS students")
    cur.execute("CREATE TABLE students (name TEXT PRIMARY KEY, type TEXT)")
    conn.commit()
    return conn


def populate_students(
    conn: sqlite3.Connection,
    students: dict[StudentType, list[str]],
) -> None:
    """Insert every student name + auto-generated 'Last, First' reverse form."""
    cur = conn.cursor()
    for s_type, names in students.items():
        for name in names:
            cur.execute(
                "INSERT OR IGNORE INTO students (name, type) VALUES (?, ?)",
                (name, s_type),
            )
            if " " in name:
                parts = name.split()
                reversed_name = f"{parts[-1]}, {' '.join(parts[:-1])}"
                cur.execute(
                    "INSERT OR IGNORE INTO students (name, type) VALUES (?, ?)",
                    (reversed_name, s_type),
                )
    conn.commit()


# ----- Read-only cache accessors ------------------------------------------


def cache_read_doi(conn: sqlite3.Connection, title: str) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT doi_or_url FROM doi_cache WHERE title=?", (title.lower(),))
    row = cur.fetchone()
    return row[0] if row else None


def cache_read_patent(conn: sqlite3.Connection, number_clean: str) -> Optional[str]:
    cur = conn.cursor()
    cur.execute(
        "SELECT issue_date FROM patent_cache WHERE patent_id=?", (number_clean,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def cache_read_cve(conn: sqlite3.Connection, cve_id: str) -> Optional[dict]:
    """Return cached NVD record for cve_id (or None if uncached / no NVD data)."""
    cur = conn.cursor()
    cur.execute("SELECT data_json FROM cve_cache WHERE cve_id=?", (cve_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        return cast(dict, json.loads(row[0]))
    except (ValueError, TypeError):
        return None
