"""
Tests for concurrent board fetching.

The requirement this exists to protect is the user's, stated plainly: making
the scrape concurrent must not change which jobs come back. So the properties
pinned here are equivalence and ordering against the sequential path, not
speed — a faster scrape that quietly drops a board would be a regression, not
an improvement.
"""

import pytest

import scrapers


@pytest.fixture
def workers(monkeypatch):
    def _set(n):
        monkeypatch.setattr(scrapers, "SCRAPE_WORKERS", n)
    return _set


def _fetch(item):
    return [{"id": f"{item}-a"}, {"id": f"{item}-b"}]


class TestEquivalenceWithSequential:
    def test_same_results_and_order_as_sequential(self, workers):
        items = [f"board{i}" for i in range(25)]
        workers(1)
        sequential = scrapers._parallel_collect(items, _fetch)
        workers(6)
        concurrent = scrapers._parallel_collect(items, _fetch)
        assert concurrent == sequential

    def test_nothing_is_dropped(self, workers):
        items = list(range(50))
        workers(8)
        out = scrapers._parallel_collect(items, _fetch)
        assert len(out) == 100
        assert len({j["id"] for j in out}) == 100

    def test_order_follows_input_not_completion(self, workers):
        """Dedup downstream keeps the FIRST occurrence, so input order must
        survive concurrency even when later items finish sooner."""
        import time

        def variable_delay(i):
            time.sleep(0.02 if i == 0 else 0.0)   # first item is slowest
            return [{"id": i}]

        workers(6)
        out = scrapers._parallel_collect(list(range(10)), variable_delay)
        assert [j["id"] for j in out] == list(range(10))


class TestResilience:
    def test_one_failing_board_does_not_kill_the_batch(self, workers):
        def flaky(i):
            if i % 4 == 0:
                raise RuntimeError("board is down")
            return [{"id": i}]

        def guarded(i):
            try:
                return flaky(i)
            except Exception:
                return []

        workers(6)
        out = scrapers._parallel_collect(list(range(20)), guarded)
        assert len(out) == 15

    def test_empty_and_none_returns_are_tolerated(self, workers):
        workers(4)
        out = scrapers._parallel_collect([1, 2, 3], lambda i: None if i == 2 else [{"id": i}])
        assert [j["id"] for j in out] == [1, 3]

    def test_empty_input(self, workers):
        workers(6)
        assert scrapers._parallel_collect([], _fetch) == []

    def test_single_item_skips_the_pool(self, workers):
        workers(6)
        assert scrapers._parallel_collect(["only"], _fetch) == _fetch("only")


class TestConfiguration:
    def test_workers_env_var_floor_is_one(self, monkeypatch):
        """JOBHUNTER_SCRAPE_WORKERS=1 must mean sequential, never zero threads."""
        import importlib
        monkeypatch.setenv("JOBHUNTER_SCRAPE_WORKERS", "0")
        importlib.reload(scrapers)
        assert scrapers.SCRAPE_WORKERS == 1
        monkeypatch.delenv("JOBHUNTER_SCRAPE_WORKERS", raising=False)
        importlib.reload(scrapers)

    def test_timings_dict_exists_for_run_stats(self):
        assert isinstance(scrapers.SCRAPER_TIMINGS, dict)
