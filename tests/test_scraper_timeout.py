"""
Tests for the per-scraper timeout.

Observed live: after several full scrapes in one evening, LinkedIn/Indeed began
rate-limiting and JobSpy retried silently. A local run and a Fargate task both
sat at the same log line for 20+ minutes with no output and no error. On
Fargate that is a container being paid for that will never deliver a digest.
The guarantee pinned here is that one hung source costs its own jobs and
nothing else.
"""

import time

import pytest

import scrapers


@pytest.fixture
def short_timeout(monkeypatch):
    monkeypatch.setattr(scrapers, "SCRAPER_TIMEOUT_SECONDS", 1)


class TestTimeout:
    def test_hanging_scraper_is_abandoned(self, short_timeout):
        def hangs():
            time.sleep(30)
            return [{"id": "never"}]

        started = time.time()
        jobs, timed_out = scrapers._run_scraper_guarded(hangs)
        elapsed = time.time() - started

        assert timed_out is True
        assert jobs == []
        assert elapsed < 5, "must not wait for the hung scraper to finish"

    def test_fast_scraper_is_unaffected(self, short_timeout):
        jobs, timed_out = scrapers._run_scraper_guarded(lambda: [{"id": "a"}, {"id": "b"}])
        assert timed_out is False
        assert len(jobs) == 2

    def test_failing_scraper_returns_empty_not_raises(self, short_timeout):
        def boom():
            raise RuntimeError("source is down")

        jobs, timed_out = scrapers._run_scraper_guarded(boom)
        assert jobs == [] and timed_out is False

    def test_scraper_returning_none_is_tolerated(self, short_timeout):
        jobs, timed_out = scrapers._run_scraper_guarded(lambda: None)
        assert jobs == [] and timed_out is False

    def test_default_timeout_is_generous_but_bounded(self):
        # Long enough for a slow-but-working source, short enough that a hung
        # one cannot consume the whole run.
        assert 300 <= scrapers.SCRAPER_TIMEOUT_SECONDS <= 1200

    def test_timeout_is_configurable(self, monkeypatch):
        import importlib
        monkeypatch.setenv("JOBHUNTER_SCRAPER_TIMEOUT", "42")
        importlib.reload(scrapers)
        assert scrapers.SCRAPER_TIMEOUT_SECONDS == 42
        monkeypatch.delenv("JOBHUNTER_SCRAPER_TIMEOUT", raising=False)
        importlib.reload(scrapers)


class TestLateRecovery:
    """A timed-out scraper often finishes while the remaining sources run.
    Observed live: a throttled JobSpy timed out at 420s, then completed with
    696 real jobs during the 20 minutes of scraping that followed, and every
    one of them was discarded."""

    def test_late_finisher_is_recovered(self, monkeypatch):
        monkeypatch.setattr(scrapers, "SCRAPER_TIMEOUT_SECONDS", 1)
        scrapers._PENDING_SCRAPERS.clear()

        def slow_but_finishes():
            time.sleep(2)
            return [{"id": "late-1"}, {"id": "late-2"}]

        jobs, timed_out = scrapers._run_scraper_guarded(slow_but_finishes)
        assert timed_out is True and jobs == []

        recovered = scrapers._recover_late_scrapers(grace_seconds=5)
        assert [j["id"] for j in recovered] == ["late-1", "late-2"]

    def test_still_hung_scraper_is_skipped_not_waited_on(self, monkeypatch):
        monkeypatch.setattr(scrapers, "SCRAPER_TIMEOUT_SECONDS", 1)
        scrapers._PENDING_SCRAPERS.clear()

        scrapers._run_scraper_guarded(lambda: (time.sleep(60), [{"id": "never"}])[1])
        started = time.time()
        recovered = scrapers._recover_late_scrapers(grace_seconds=1)
        assert recovered == []
        assert time.time() - started < 4, "must not block on a genuinely hung source"

    def test_pending_list_is_cleared_between_runs(self, monkeypatch):
        monkeypatch.setattr(scrapers, "SCRAPER_TIMEOUT_SECONDS", 1)
        scrapers._PENDING_SCRAPERS.clear()
        scrapers._run_scraper_guarded(lambda: (time.sleep(2), [{"id": "x"}])[1])
        scrapers._recover_late_scrapers(grace_seconds=3)
        assert scrapers._PENDING_SCRAPERS == []


class TestBackgroundScrapers:
    """LinkedIn/Indeed and the web search self-throttle and cannot be made
    faster. Measured on Fargate they were 600s+ and 428s of a 27-minute
    scrape, for ~5% of the jobs. Running them alongside the ATS scraping that
    has to happen anyway removes that wait without losing anything."""

    def test_slow_sources_are_marked_background(self):
        assert "scrape_jobspy" in scrapers._BACKGROUND_SCRAPERS
        assert "scrape_web_search" in scrapers._BACKGROUND_SCRAPERS

    def test_started_scraper_runs_concurrently(self):
        import time as _t

        def slow():
            _t.sleep(1.5)
            return [{"id": "bg"}]

        started = _t.time()
        name, box, thread = scrapers._start_scraper(slow)
        # Control returns immediately; the work happens on the thread
        assert _t.time() - started < 0.5
        thread.join(5)
        assert box["jobs"] == [{"id": "bg"}]

    def test_background_failure_does_not_propagate(self):
        name, box, thread = scrapers._start_scraper(
            lambda: (_ for _ in ()).throw(RuntimeError("linkedin blocked")))
        thread.join(5)
        assert box["jobs"] == []

    def test_google_jobs_loop_is_gone(self):
        """It returned 0 jobs on every run for months while costing 10
        requests and 30s of sleeping."""
        import inspect
        src = inspect.getsource(scrapers.scrape_jobspy)
        assert "google_search_term" not in src
        assert 'site_name=["google"]' not in src
