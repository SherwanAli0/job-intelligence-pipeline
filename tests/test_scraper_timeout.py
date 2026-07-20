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
