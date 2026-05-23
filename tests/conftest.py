"""Shared pytest fixtures + import-time bootstrap.

Sets PUBS_EMITTER_CONFIG to point at tests/fixtures/config.yaml BEFORE any
test file imports `pubs_emitter.*` — `pubs_emitter.config` loads the config
at import time, so the env var must be in place first.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys

import pytest


_FIXTURES = pathlib.Path(__file__).parent / "fixtures"
os.environ["PUBS_EMITTER_CONFIG"] = str(_FIXTURES / "config.yaml")

# Make `src/pubs_emitter` importable without `pip install -e .` (CI minimalism).
_SRC = pathlib.Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    return _FIXTURES


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory SQLite with the schema open_db() creates + a few seeded rows."""
    c = sqlite3.connect(":memory:")
    cur = c.cursor()
    cur.executescript(
        """
        CREATE TABLE doi_cache (title TEXT PRIMARY KEY, doi_or_url TEXT);
        CREATE TABLE patent_cache (patent_id TEXT PRIMARY KEY, issue_date TEXT);
        CREATE TABLE cve_cache (cve_id TEXT PRIMARY KEY, data_json TEXT);
        CREATE TABLE students (name TEXT PRIMARY KEY, type TEXT);
        INSERT INTO students VALUES ('Paschal C. Amusuo', 'G');
        INSERT INTO students VALUES ('Amusuo, Paschal C', 'G');
        INSERT INTO students VALUES ('Wenxin Jiang', 'G');
        INSERT INTO students VALUES ('Jiang, Wenxin', 'G');
        INSERT INTO students VALUES ('Test Undergrad', 'U');
        INSERT INTO students VALUES ('Undergrad, Test', 'U');
        """
    )
    c.commit()
    return c
