"""Unit tests for pubs_emitter.network — the pure helpers (no actual HTTP)."""
from __future__ import annotations

import time

from pubs_emitter.network import (
    RateLimiter,
    is_no_doi_venue,
    titles_similar,
)


class TestRateLimiter:
    def test_first_call_no_wait(self) -> None:
        rl = RateLimiter(min_interval_s=0.05)
        t0 = time.monotonic()
        rl.acquire()
        assert (time.monotonic() - t0) < 0.05  # no prior call → returns immediately

    def test_back_to_back_calls_serialize(self) -> None:
        # Two acquires in quick succession: second must wait ≥ interval.
        rl = RateLimiter(min_interval_s=0.05)
        rl.acquire()
        t0 = time.monotonic()
        rl.acquire()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.04  # allow small slop on slow CI


class TestTitlesSimilar:
    def test_identical(self) -> None:
        assert titles_similar("Hello World Foo Bar", "Hello World Foo Bar")

    def test_clearly_different(self) -> None:
        # Threshold default 0.6; entirely disjoint tokens → fail.
        assert not titles_similar(
            "Apples Oranges Bananas",
            "Quantum Mechanics Lectures",
        )

    def test_short_tokens_ignored(self) -> None:
        # <3 chars discarded; both bags become empty → False (never similar).
        assert not titles_similar("a b c", "x y z")

    def test_partial_overlap_above_threshold(self) -> None:
        assert titles_similar(
            "engineering patterns for trust and safety",
            "engineering patterns trust safety study",
            threshold=0.4,
        )


class TestIsNoDoiVenue:
    def test_usenix_returns_true(self) -> None:
        assert is_no_doi_venue("USENIX-Sec")
        assert is_no_doi_venue("usenix-sec")  # case-insensitive

    def test_other_venues_return_false(self) -> None:
        assert not is_no_doi_venue("ICSE")
        assert not is_no_doi_venue("JSS")

    def test_none_returns_false(self) -> None:
        assert not is_no_doi_venue(None)

    def test_empty_returns_false(self) -> None:
        assert not is_no_doi_venue("")
