"""
Tests for the Brave Search scraper's failure reporting.

Brave silently returned 0 jobs for two days because the code never checked the
HTTP status: a 401, a 429 and a quota error all decode as valid JSON with no
"web" key, so every one of them printed "[Brave] 0 jobs". These tests pin the
distinction between the failure modes, since "returns nothing" is the one
behaviour all of them share and the only thing that told us apart was luck.
"""

import pytest

import scrapers


class _Resp:
    def __init__(self, status=200, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def _ok_payload(*urls):
    return {"web": {"results": [
        {"url": u, "title": "Junior Data Scientist job", "description": "A role"}
        for u in urls
    ]}}


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """No real sleeping, no real page fetches."""
    monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
    monkeypatch.setattr(scrapers, "_fetch_full_description", lambda url: "")
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")


class TestAuthFailure:
    def test_401_fails_fast_without_hammering_the_api(self, monkeypatch, capsys):
        calls = []

        def fake_get(*a, **kw):
            calls.append(1)
            return _Resp(status=401, text='{"error":"invalid token"}')

        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        out = scrapers.scrape_brave_search()

        assert out == []
        # One doomed call, not one per query
        assert len(calls) == 1
        assert "AUTH FAILED" in capsys.readouterr().out

    def test_403_also_fails_fast(self, monkeypatch, capsys):
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _Resp(status=403))
        assert scrapers.scrape_brave_search() == []
        assert "AUTH FAILED" in capsys.readouterr().out


class TestRateLimit:
    def test_429_is_retried_then_reported(self, monkeypatch, capsys):
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _Resp(status=429))
        scrapers.scrape_brave_search()
        out = capsys.readouterr().out
        assert "HTTP 429" in out
        assert "rate limit" in out.lower()

    def test_429_then_success_recovers(self, monkeypatch):
        state = {"n": 0}

        def fake_get(*a, **kw):
            state["n"] += 1
            if state["n"] == 1:
                return _Resp(status=429)
            return _Resp(payload=_ok_payload("https://acme.de/careers/data-job"))

        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        assert len(scrapers.scrape_brave_search()) >= 1


class TestFilterFunnelIsDistinguishable:
    def test_results_all_filtered_by_domain_says_so(self, monkeypatch, capsys):
        # Brave healthy, but every hit is a job board we deliberately exclude
        monkeypatch.setattr(scrapers.requests, "get",
                            lambda *a, **kw: _Resp(payload=_ok_payload(
                                "https://www.linkedin.com/jobs/view/1",
                                "https://indeed.com/viewjob?jk=2")))
        out = scrapers.scrape_brave_search()
        printed = capsys.readouterr().out
        assert out == []
        assert "filtered out" in printed
        assert "job-board domain" in printed
        assert "API is healthy" in printed

    def test_genuinely_empty_search_is_not_reported_as_a_failure(self, monkeypatch, capsys):
        monkeypatch.setattr(scrapers.requests, "get",
                            lambda *a, **kw: _Resp(payload={"web": {"results": []}}))
        out = scrapers.scrape_brave_search()
        printed = capsys.readouterr().out
        assert out == []
        assert "no web results at all" in printed
        assert "AUTH FAILED" not in printed

    def test_healthy_run_reports_quota_remaining(self, monkeypatch, capsys):
        monkeypatch.setattr(scrapers.requests, "get",
                            lambda *a, **kw: _Resp(
                                payload=_ok_payload("https://acme.de/careers/data-job"),
                                headers={"X-RateLimit-Remaining": "1234"}))
        scrapers.scrape_brave_search()
        assert "quota remaining: 1234" in capsys.readouterr().out


class TestUnchangedHappyPath:
    def test_valid_career_page_still_becomes_a_job(self, monkeypatch):
        monkeypatch.setattr(scrapers.requests, "get",
                            lambda *a, **kw: _Resp(payload=_ok_payload(
                                "https://acme.de/careers/data-scientist")))
        out = scrapers.scrape_brave_search()
        assert out and out[0]["source"] == "BraveSearch"
        assert out[0]["company"] == "Acme"

    def test_missing_key_skips_without_error(self, monkeypatch, capsys):
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        assert scrapers.scrape_brave_search() == []
        assert "not set" in capsys.readouterr().out


class TestQuotaHandling:
    """HTTP 402 is Brave's 'monthly free-tier allowance spent' response. It is
    account-level, so every remaining query would fail too — the real incident
    burned three calls before giving up, and the log said nothing about quota."""

    def test_402_fails_fast_with_quota_message(self, monkeypatch, capsys):
        calls = []

        def fake_get(*a, **kw):
            calls.append(1)
            return _Resp(status=402, headers={"X-RateLimit-Remaining": "49, 0"})

        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        out = scrapers.scrape_brave_search()
        printed = capsys.readouterr().out

        assert out == []
        assert len(calls) == 1, "402 is account-level; must not retry every query"
        assert "QUOTA EXHAUSTED" in printed
        assert "resets automatically" in printed


class TestQueryBudget:
    def test_per_run_budget_is_capped(self):
        assert len(scrapers._brave_query_slice()) == scrapers._BRAVE_MAX_QUERIES

    def test_budget_keeps_monthly_usage_inside_the_included_credit(self):
        # Plan: $5.00/1,000 requests with $5 included monthly = ~1,000 requests.
        # 2 scheduled runs/day over ~31 days must stay well inside that, with
        # room left for manual runs and local testing.
        monthly = scrapers._BRAVE_MAX_QUERIES * 2 * 31
        assert monthly < 1000, f"{monthly} requests/month would exhaust the $5 credit"

    def test_slice_rotates_so_every_query_is_used_over_time(self, monkeypatch):
        seen = set()
        import datetime as _dt

        class _FakeDate(_dt.date):
            _day = 1

            @classmethod
            def today(cls):
                return _dt.date(2026, 1, 1) + _dt.timedelta(days=cls._day - 1)

        monkeypatch.setattr(_dt, "date", _FakeDate)
        for day in range(1, 40):
            _FakeDate._day = day
            seen.update(scrapers._brave_query_slice())
        assert seen == set(scrapers._WEB_QUERIES), "rotation must eventually cover every query"

    def test_slice_never_exceeds_available_queries(self, monkeypatch):
        monkeypatch.setattr(scrapers, "_WEB_QUERIES", ["a", "b"])
        assert scrapers._brave_query_slice() == ["a", "b"]
