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


class TestWorkersArgument:
    """`workers=` limits ONE call; it must never touch the module global."""

    @staticmethod
    def _peak_concurrency(n_items, **kwargs):
        import threading

        lock, state = threading.Lock(), {"now": 0, "peak": 0}

        def hold(i):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            threading.Event().wait(0.05)      # long enough for the pool to fill
            with lock:
                state["now"] -= 1
            return [{"id": i}]

        scrapers._parallel_collect(list(range(n_items)), hold, **kwargs)
        return state["peak"]

    def test_workers_caps_one_call_below_the_global(self, workers):
        workers(6)
        assert self._peak_concurrency(12, workers=2) == 2

    def test_without_workers_the_global_is_used(self, workers):
        workers(4)
        assert self._peak_concurrency(12) == 4

    def test_workers_1_is_sequential(self, workers):
        workers(6)
        assert self._peak_concurrency(6, workers=1) == 1

    def test_workers_does_not_change_the_global(self, workers):
        workers(6)
        scrapers._parallel_collect(list(range(8)), _fetch, workers=2)
        assert scrapers.SCRAPE_WORKERS == 6

    def test_results_and_order_are_the_same_with_workers(self, workers):
        workers(6)
        items = [f"board{i}" for i in range(25)]
        assert (scrapers._parallel_collect(items, _fetch, workers=2)
                == scrapers._parallel_collect(items, _fetch))


class TestLinkedInSlowLane:
    """_enrich_jobspy_descriptions slows LinkedIn down to 2 workers. It used to
    do that by saving SCRAPE_WORKERS, setting it to 2 and restoring it in a
    finally. JobSpy calls it once per query while scrape_linkedin_guest and
    main._fill_missing_bodies call it from other threads, so two calls
    overlapped: the later one saved the already-lowered 2, finished last, and
    'restored' it — leaving every later _parallel_collect at 2 workers for the
    rest of the run."""

    @staticmethod
    def _jobs(prefix, n):
        return [{"title": f"Working Student {i}", "description": "",
                 "url": f"https://www.linkedin.com/jobs/view/{prefix}{i}"}
                for i in range(n)]

    @pytest.fixture(autouse=True)
    def _no_pacing(self, monkeypatch):
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)

    def test_overlapping_slow_lane_calls_leave_the_global_alone(self, monkeypatch, workers):
        import threading

        workers(6)
        gate = {k: threading.Event() for k in ("a_in", "b_in", "release_a", "release_b")}

        def fake_fetch(url):
            if "/view/A" in url:
                gate["a_in"].set()
                assert gate["release_a"].wait(5)
            else:
                gate["b_in"].set()
                assert gate["release_b"].wait(5)
            return "full description " * 5

        monkeypatch.setattr(scrapers, "_fetch_full_description", fake_fetch)
        errors = []

        def call(jobs):
            try:
                scrapers._enrich_jobspy_descriptions(jobs, screen_titles=False)
            except BaseException as e:           # surface thread failures in the test
                errors.append(e)

        short = threading.Thread(target=call, args=(self._jobs("A", 1),))
        long_ = threading.Thread(target=call, args=(self._jobs("B", 4),))
        short.start()
        assert gate["a_in"].wait(5)              # the short call is inside its slow lane
        long_.start()
        assert gate["b_in"].wait(5)              # the long call entered while it was active
        assert scrapers.SCRAPE_WORKERS == 6      # not lowered while either runs
        gate["release_a"].set()
        short.join(5)                            # the short one finishes first ...
        assert scrapers.SCRAPE_WORKERS == 6
        gate["release_b"].set()
        long_.join(5)                            # ... the long one last: where it got stuck at 2

        assert not errors and not short.is_alive() and not long_.is_alive()
        assert scrapers.SCRAPE_WORKERS == 6

    def test_the_slow_lane_still_uses_two_workers(self, monkeypatch, workers):
        import threading

        workers(6)
        lock, state = threading.Lock(), {"now": 0, "peak": 0}

        def fake_fetch(url):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            threading.Event().wait(0.05)
            with lock:
                state["now"] -= 1
            return "full description " * 5

        monkeypatch.setattr(scrapers, "_fetch_full_description", fake_fetch)
        jobs = self._jobs("C", 12)
        scrapers._enrich_jobspy_descriptions(jobs, screen_titles=False)

        assert state["peak"] == 2
        assert all(j["description"] for j in jobs)
        assert scrapers.SCRAPE_WORKERS == 6

    def test_other_hosts_keep_the_full_pool(self, monkeypatch, workers):
        import threading

        workers(4)
        lock, state = threading.Lock(), {"now": 0, "peak": 0}

        def fake_fetch(url):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            threading.Event().wait(0.05)
            with lock:
                state["now"] -= 1
            return "full description " * 5

        monkeypatch.setattr(scrapers, "_fetch_full_description", fake_fetch)
        jobs = [{"title": f"Working Student {i}", "description": "",
                 "url": f"https://careers.example.com/job/{i}"} for i in range(12)]
        scrapers._enrich_jobspy_descriptions(jobs, screen_titles=False)

        assert state["peak"] == 4


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
