"""Internship lane and watchlist (2026-11-11, owner: "Yes, all Germany").

Internships get a 14-day freshness cap, a one-week search window at the job
boards, no NRW rule inside Germany, and four employer career pages are read
directly every run with a same-day note when one cannot be read.
"""
from datetime import datetime, timedelta, timezone

import pytest

import config
import main
import notifier
import scrapers


def _posted(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _j(**kw):
    base = {"id": "x", "title": "Machine Learning Intern", "company": "ACME", "location": "",
            "source": "LinkedIn", "description": "", "url": "https://example.org/1"}
    base.update(kw)
    return base


class TestFreshness:
    def test_internship_ten_days_old_is_kept(self):
        assert main._is_fresh_enough(_j(posted_at=_posted(10)))

    def test_internship_older_than_the_cap_is_dropped(self):
        assert not main._is_fresh_enough(_j(posted_at=_posted(config.INTERNSHIP_MAX_AGE_DAYS + 3)))

    def test_other_forms_keep_the_48h_cap(self):
        assert not main._is_fresh_enough(_j(title="Werkstudent Data Science", posted_at=_posted(5)))
        assert main._is_fresh_enough(_j(title="Werkstudent Data Science", posted_at=_posted(1)))

    def test_switch_off_restores_48h_for_internships(self, monkeypatch):
        monkeypatch.setenv("JOBHUNTER_INTERNSHIP_LANE", "off")
        assert not main._is_fresh_enough(_j(posted_at=_posted(10)))


class TestFocusArea:
    def _onsite(self, **kw):
        return _j(_where="ON-SITE", **kw)

    def test_onsite_internship_in_munich_is_kept_and_marked(self):
        j = self._onsite(_form="internship", location="München, Bayern")
        assert main._is_in_focus_area(j)
        assert j["_relocate"] is True

    def test_onsite_working_student_in_munich_is_still_dropped(self):
        j = self._onsite(_form="student", title="Werkstudent AI", location="München, Bayern")
        assert not main._is_in_focus_area(j)
        assert "_relocate" not in j

    def test_onsite_internship_abroad_is_not_covered(self):
        j = self._onsite(_form="internship", location="Vienna, Austria")
        assert not main._is_in_focus_area(j)

    def test_nrw_internship_is_not_marked_as_relocation(self):
        j = self._onsite(_form="internship", location="Köln", _nrw=True)
        assert main._is_in_focus_area(j)
        assert "_relocate" not in j

    def test_switch_off_restores_the_nrw_rule(self, monkeypatch):
        monkeypatch.setenv("JOBHUNTER_INTERNSHIP_LANE", "off")
        assert not main._is_in_focus_area(self._onsite(_form="internship", location="München"))

    def test_digest_shows_the_relocation_badge(self):
        j = _j(_where="ON-SITE", _relocate=True, score=80, reason="r", location="München")
        assert "RELOCATION" in notifier._build_html([j])


class TestQueries:
    def test_internship_queries_look_back_a_week(self):
        assert scrapers._query_hours_old("Praktikum Data Science") == config.INTERNSHIP_SOURCE_HOURS
        assert scrapers._query_hours_old("Summer Internship 2027") == config.INTERNSHIP_SOURCE_HOURS
        assert scrapers._query_hours_old("Machine Learning Intern") == config.INTERNSHIP_SOURCE_HOURS

    def test_other_queries_keep_the_normal_window(self):
        assert scrapers._query_hours_old("Werkstudent AI") == config.max_posting_age_hours()
        assert scrapers._query_hours_old("Working Student International Sales") \
            == config.max_posting_age_hours()

    def test_programme_wording_is_searched(self):
        qs = {q.lower() for q in config.SEARCH_QUERIES}
        assert {"summer internship 2027", "ai research intern", "student researcher"} <= qs


GOOGLE = ('<h3>Sort by</h3><h3>Research Scientist PhD Intern, 2027</h3>'
          '<a href="jobs/results/111285665542349510-research-scientist-phd-intern-2027?x=1">'
          '<a href="jobs/results/118991686201156294-product-deployment-engineer-bsms-intern-2027-english">')
APPLE = ('<h3><a class="l" aria-label="x" href="/de-de/details/200692236-5487/internship-ml?team=STDNT" '
         'data-discover="true">Internship - Machine Learning Research</a></h3>'
         '<span class="job-posted-date" id="search-job-posted-date-1">29. Okt. 2026</span>'
         '<span id="search-store-name-container-1">Aachen</span>')
SAP = ('<table><tr class="data-row"> <td> <a href="/job/Berlin-PhD-Internship/1/" class="jobTitle-link">'
       'PhD Internship (f/m/d) - AI Research</a> <span class="jobLocation"> Berlin, DE, 10557 </span></td></tr>'
       '<tr class="data-row"> <a href="/job/Walldorf-Senior/2/" class="jobTitle-link">Senior Specialist</a>'
       '<span class="jobLocation"> Walldorf, DE, 69190 </span></tr></table>')
FZJ = ('{"description":"https:\\u002F\\u002Frecruiting.fz-juelich.de\\u002Fjobposting\\u002Fabc1",'
       '"effective":"1969","title":"Praktikant:in Data Science (w\\u002Fm\\u002Fd)","type_title":"S"},'
       '{"description":"https:\\u002F\\u002Frecruiting.fz-juelich.de\\u002Fjobposting\\u002Fabc1",'
       '"effective":"1969","title":"Praktikant:in Data Science (w\\u002Fm\\u002Fd)","type_title":"S"}')


class TestWatchlistParsers:
    def test_google(self):
        rows = scrapers._wl_google_rows(GOOGLE)
        assert [r["title"] for r in rows] == ["Research Scientist PhD Intern, 2027",
                                              "Product Deployment Engineer Bsms Intern 2027 English"]
        assert rows[0]["url"].endswith("111285665542349510-research-scientist-phd-intern-2027")

    def test_apple(self):
        (r,) = scrapers._wl_apple_rows(APPLE)
        assert r == {"title": "Internship - Machine Learning Research",
                     "url": "https://jobs.apple.com/de-de/details/200692236-5487/internship-ml",
                     "location": "Aachen, Germany", "posted": "2026-10-29"}

    def test_sap(self):
        rows = scrapers._wl_sap_rows(SAP)
        assert [(r["title"], r["location"]) for r in rows] == [
            ("PhD Internship (f/m/d) - AI Research", "Berlin, DE, 10557"),
            ("Senior Specialist", "Walldorf, DE, 69190")]

    def test_juelich_drops_the_duplicate_row(self):
        (r,) = scrapers._wl_fzj_rows(FZJ)
        assert r["title"] == "Praktikant:in Data Science (w/m/d)"
        assert r["url"] == "https://recruiting.fz-juelich.de/jobposting/abc1"

    def test_apple_body_is_read_from_the_embedded_json(self, monkeypatch):
        page = ('x\\"jobSummary\\":\\"We explore speech translation.\\\\n\\",\\"description\\":'
                '\\"Strong Python skills.\\",\\"internalDetails\\":null}')
        monkeypatch.setattr(scrapers, "_wl_get", lambda url: page)
        body = scrapers._wl_apple_body("https://jobs.apple.com/x")
        assert "We explore speech translation." in body and "Strong Python skills." in body
        assert "\\" not in body and "internalDetails" not in body


class TestWatchlistRun:
    def _run(self, monkeypatch, table):
        monkeypatch.setattr(scrapers, "_WATCHLIST", table)
        monkeypatch.setattr(scrapers, "_page_text", lambda url: "A full advertisement text.")
        monkeypatch.setattr(scrapers, "KNOWN_JOB_IDS", set())
        return scrapers.scrape_internship_watchlist()

    def test_student_rows_become_jobs_and_status_is_recorded(self, monkeypatch):
        rows = [{"title": "AI Research Intern", "url": "https://e.org/1", "location": "Berlin", "posted": ""},
                {"title": "Senior Specialist", "url": "https://e.org/2", "location": "Berlin", "posted": ""}]
        jobs = self._run(monkeypatch, (("ACME", "https://e.org", lambda: rows, None),))
        assert [(j["title"], j["company"], j["source"]) for j in jobs] == [
            ("AI Research Intern", "ACME", "Watchlist")]
        assert scrapers.WATCHLIST_STATUS["ACME"] == {"ok": True, "listed": 2, "kept": 1,
                                                     "url": "https://e.org"}

    def test_a_failing_page_is_recorded_and_the_others_still_run(self, monkeypatch):
        def boom():
            raise RuntimeError("HTTP 403")
        ok_rows = [{"title": "Praktikum KI", "url": "https://e.org/3", "location": "Jülich", "posted": ""}]
        jobs = self._run(monkeypatch, (("Broken", "https://b.org", boom, None),
                                       ("Fine", "https://f.org", lambda: ok_rows, None)))
        assert len(jobs) == 1
        assert scrapers.WATCHLIST_STATUS["Broken"]["ok"] is False
        assert scrapers.WATCHLIST_STATUS["Broken"]["error"] == "HTTP 403"
        assert scrapers.WATCHLIST_STATUS["Fine"]["ok"] is True

    def test_a_page_with_no_rows_counts_as_a_failure(self, monkeypatch):
        self._run(monkeypatch, (("Empty", "https://e.org", lambda: [], None),))
        assert scrapers.WATCHLIST_STATUS["Empty"]["ok"] is False

    def test_watchlist_is_wired_in_and_long_lived(self):
        import inspect
        assert "scrape_internship_watchlist" in inspect.getsource(scrapers.scrape_all)
        assert "Watchlist" in main._LONG_LIVED_SOURCES


class TestRunNotes:
    def test_quiet_today_names_usually_active_sources_only(self):
        reg = {"Big": {"typical": 200}, "Small": {"typical": 3}, "Alive": {"typical": 50},
               "HiringCafe": {"typical": 400}}
        assert main._quiet_today(reg, {"Alive": 12}) == ["Big"]

    def test_notes_render_in_the_email(self):
        j = _j(score=80, reason="r")
        html = notifier._build_html([j], None, ["Internship watchlist read today: Google 5 of 5."])
        assert "Internship watchlist read today: Google 5 of 5." in html

    def test_email_without_notes_is_unchanged(self):
        j = _j(score=80, reason="r")
        assert notifier._build_html([j]) == notifier._build_html([j], None, None)
