"""Unit tests for pubs_emitter.lookup."""
from __future__ import annotations

import sqlite3
from typing import Any

from pubs_emitter.lookup import (
    commit_results,
    dispatch_parallel,
    extract_patent_number,
    plan_lookups,
)
from pubs_emitter.types import NetworkTask


class TestExtractPatentNumber:
    def test_canonical_us_patent_form(self) -> None:
        display, clean = extract_patent_number("US Patent 11,176,090")
        assert display == "US Patent 11,176,090"
        assert clean == "11176090"

    def test_numeric_only_falls_back(self) -> None:
        display, clean = extract_patent_number("11,176,090")
        assert clean == "11176090"
        assert display == "11,176,090"

    def test_empty(self) -> None:
        assert extract_patent_number("") == ("", "")

    def test_garbage(self) -> None:
        # No digits in the note → no extractable number.
        assert extract_patent_number("not a patent") == ("", "")


class TestPlanLookups:
    def test_empty_bib(self, conn: sqlite3.Connection) -> None:
        assert plan_lookups(conn, [], None) == []

    def test_arxiv_skipped(self, conn: sqlite3.Connection) -> None:
        # arXiv DOIs are constructed, no lookup needed.
        tasks = plan_lookups(
            conn,
            [{"title": "An arXiv Paper", "journal": "arXiv preprint"}],
            None,
        )
        assert tasks == []

    def test_book_chapter_skipped(self, conn: sqlite3.Connection) -> None:
        tasks = plan_lookups(
            conn,
            [{"ENTRYTYPE": "incollection", "title": "A Book Chapter"}],
            None,
        )
        assert tasks == []

    def test_journal_uncached_emits_doi_task(self, conn: sqlite3.Connection) -> None:
        tasks = plan_lookups(
            conn,
            [
                {
                    "title": "Some Paper",
                    "author": "Davis, James C",
                    "journal": "[ICSE'25] Some Journal",
                }
            ],
            None,
        )
        assert len(tasks) == 1
        assert tasks[0].kind == "doi"
        assert tasks[0].key == "some paper"

    def test_journal_cached_no_task(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO doi_cache VALUES (?, ?)",
            ("some paper", "https://doi.org/10.x"),
        )
        tasks = plan_lookups(
            conn,
            [
                {
                    "title": "Some Paper",
                    "author": "Davis, James C",
                    "journal": "[ICSE'25] Some Journal",
                }
            ],
            None,
        )
        assert tasks == []

    def test_patent_uncached_emits_patent_task(self, conn: sqlite3.Connection) -> None:
        tasks = plan_lookups(
            conn,
            [
                {
                    "ENTRYTYPE": "misc",
                    "publisher": "US patent",
                    "note": "US Patent 11,176,090",
                    "title": "X",
                }
            ],
            None,
        )
        assert len(tasks) == 1
        assert tasks[0].kind == "patent"
        assert tasks[0].key == "11176090"

    def test_cve_uncached_emits_cve_task(self, conn: sqlite3.Connection) -> None:
        tasks = plan_lookups(
            conn,
            [],
            {"cves": [{"cve_id": "CVE-2024-12345"}]},
        )
        assert len(tasks) == 1
        assert tasks[0].kind == "cve"
        assert tasks[0].key == "CVE-2024-12345"

    def test_cve_cached_no_task(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO cve_cache VALUES (?, ?)", ("CVE-2024-12345", "{}"),
        )
        tasks = plan_lookups(
            conn,
            [],
            {"cves": [{"cve_id": "CVE-2024-12345"}]},
        )
        assert tasks == []


class TestDispatchParallel:
    def test_no_tasks_returns_empty(self) -> None:
        assert dispatch_parallel([]) == {}

    def test_runs_tasks(self) -> None:
        results = dispatch_parallel(
            [
                NetworkTask(kind="doi", key="a", fetcher=lambda: "url-a"),
                NetworkTask(kind="doi", key="b", fetcher=lambda: "url-b"),
            ]
        )
        assert results == {("doi", "a"): "url-a", ("doi", "b"): "url-b"}

    def test_task_exception_recorded_as_none(self) -> None:
        def _boom() -> Any:
            raise RuntimeError("nope")

        results = dispatch_parallel(
            [NetworkTask(kind="doi", key="a", fetcher=_boom)]
        )
        assert results == {("doi", "a"): None}


class TestCommitResults:
    def test_commits_each_kind_to_right_table(self, conn: sqlite3.Connection) -> None:
        commit_results(
            conn,
            {
                ("doi", "title-a"): "https://doi.org/10.x",
                ("patent", "11176090"): "2021-11-16",
                ("cve", "CVE-2024-12345"): {"published": "2024-01-15"},
            },
        )
        row = conn.execute(
            "SELECT doi_or_url FROM doi_cache WHERE title=?", ("title-a",),
        ).fetchone()
        assert row[0] == "https://doi.org/10.x"
        row = conn.execute(
            "SELECT issue_date FROM patent_cache WHERE patent_id=?", ("11176090",),
        ).fetchone()
        assert row[0] == "2021-11-16"
        row = conn.execute(
            "SELECT data_json FROM cve_cache WHERE cve_id=?", ("CVE-2024-12345",),
        ).fetchone()
        assert "published" in row[0]
