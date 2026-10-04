"""Leaks found by the 2026-10-04 audit: jobs the pipeline scraped (or should
have scraped) and then lost before any filter the owner chose could see them.
Each class pins one fix. All offline."""

import inspect
import json

import pytest

import config
import main
import scrapers


def _j(title="Working Student Data", desc="", **kw):
    base = {"id": kw.pop("id", "t"), "title": title, "company": "X", "location": "Köln",
            "url": "https://example.org/job/1", "source": "linkedin",
            "description": desc, "posted_at": ""}
    base.update(kw)
    return base


ENGLISH = ("We are looking for a working student to join our data team. You will build "
           "pipelines in Python and SQL, analyse product data and present the results "
           "to the team. You are enrolled at a university and you enjoy working with "
           "data. Our working language is English and the team is international.")


# ── Step 1a: every query runs on the single daily run ────────────────────────

class TestAllQueriesRunEveryMorning:
    def test_no_query_is_left_out_at_any_hour(self, monkeypatch):
        got = scrapers._jobspy_active_queries()
        assert sorted(got) == sorted(config.SEARCH_QUERIES)
        assert len(got) == len(set(got)) == len(config.SEARCH_QUERIES)

    def test_internship_and_part_time_are_in_the_first_slots(self):
        """JobSpy is time-boxed. A cut must cost depth on every form, never a
        whole form: the old slice put all 18 internship and part-time queries
        in the half that never ran."""
        first = [q.lower() for q in scrapers._jobspy_active_queries()[:9]]
        assert sum("praktik" in q or "intern" in q or "praxissemester" in q for q in first) == 3
        assert sum("teilzeit" in q or "part-time" in q for q in first) == 3

    def test_the_utc_hour_no_longer_selects_a_slice(self):
        src = inspect.getsource(scrapers._jobspy_active_queries)
        assert ".hour" not in src and "SEARCH_QUERIES[:" not in src

    def test_repeated_postings_across_queries_are_not_fetched_twice(self):
        class _DF:
            def __init__(self, rows): self._rows = rows
            def iterrows(self): return iter(enumerate(self._rows))
        rows = [{"job_url": "https://x/1", "title": "A", "company": "C", "location": "Bonn", "site": "indeed"},
                {"job_url": "https://x/2", "title": "B", "company": "C", "location": "Bonn", "site": "indeed"}]
        out, seen = [], set()
        scrapers._jobspy_rows_to_jobs(_DF(rows), out, seen)
        scrapers._jobspy_rows_to_jobs(_DF(rows + [{"job_url": "https://x/3", "title": "D", "company": "C",
                                                   "location": "Bonn", "site": "indeed"}]), out, seen)
        assert [j["url"] for j in out] == ["https://x/1", "https://x/2", "https://x/3"]

    def test_background_grace_covers_the_longer_query_list(self):
        assert scrapers.BACKGROUND_JOIN_SECONDS >= 900
