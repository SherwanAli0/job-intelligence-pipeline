"""Fixes found for the open problems on 19 November 2026.

- Deloitte gave 0 roles from 14 November: the career-site scraper joined a
  relative link as host.split("/jobs")[0] + href, which turned
  https://jobs.deloitte.de + /job/... into https://job/..., and Deloitte's
  /sitemap.xml is an RSS feed without <loc> entries, so neither route gave a
  link it could read.
- The Workday company pages fell from about 1,250 jobs to between 0 and 26 and
  the log said nothing: an answer other than 200 ended a company silently.
- The web search through the duckduckgo_search library went to bing.com/search,
  which robots.txt disallows; it is off, Brave stays.
- LinkedIn: the lists for all of Germany never finished and are off; student
  searches with remote words came in; every read records which search found
  what no other search found.
"""
import inspect

import pytest

import main
import scrapers
from test_full_daily_sweep import FakeLinkedIn, _clean_state, lane  # noqa: F401  (fixtures)


class _Resp:
    def __init__(self, status=200, text="", data=None, headers=None):
        self.status_code, self.text, self._data, self.headers = status, text, data, headers or {}

    def json(self):
        return self._data


RSS = """<?xml version="1.0" encoding="UTF-8" ?><rss version="2.0"><channel><title>Jobs</title>
<item><title>Werkstudent Data Analytics (m/w/d) (K&#246;ln, DE)</title><description><![CDATA[x]]></description>
<link>https://jobs.deloitte.de/job/K%C3%B6ln-Werkstudent-Data-Analytics-%28mwd%29/111/</link><guid>111</guid></item>
<item><title><![CDATA[Praktikant M&amp;A (m/w/d)]]></title>
<link>https://jobs.deloitte.de/job/mehrere-Standorte-Praktikant-M&amp;A-%28mwd%29/222/</link></item>
<item><title>Senior Manager Audit (m/w/d)</title>
<link>https://jobs.deloitte.de/job/Berlin-Senior-Manager-Audit-%28mwd%29/333/</link></item>
</channel></rss>"""
SEARCH = '<a class="jobTitle-link" href="/job/Duesseldorf-Werkstudent-Cyber-%28mwd%29/444/">Werkstudent Cyber</a>'


class TestDeloitte:
    def test_a_sitemap_that_is_an_rss_feed_gives_its_student_links_and_titles(self, monkeypatch):
        monkeypatch.setattr(scrapers, "_guarded_get", lambda url, **kw: _Resp(200, RSS))
        links = scrapers._csb_sitemap_links("https://jobs.deloitte.de")
        assert links == {
            "https://jobs.deloitte.de/job/K%C3%B6ln-Werkstudent-Data-Analytics-%28mwd%29/111/":
                "Werkstudent Data Analytics (m/w/d) (Köln, DE)",
            "https://jobs.deloitte.de/job/mehrere-Standorte-Praktikant-M&A-%28mwd%29/222/": "Praktikant M&A (m/w/d)",
        }

    def test_a_feed_link_in_cdata_is_read_too(self):
        feed = ("<rss><channel><item><title>Werkstudent IT</title>"
                "<link><![CDATA[https://jobs.example.de/job/Koeln-Werkstudent-IT/5/]]></link></item></channel></rss>")
        assert scrapers._csb_rss_items(feed) == [("https://jobs.example.de/job/Koeln-Werkstudent-IT/5/", "Werkstudent IT")]

    def test_an_ordinary_sitemap_reads_as_before(self, monkeypatch):
        xml = ("<urlset><url><loc>https://jobs.rwe.com/job/Essen-Working-Student-IT-Security/9/</loc></url>"
               "<url><loc>https://jobs.rwe.com/job/Essen-Senior-Engineer/8/</loc></url></urlset>")
        monkeypatch.setattr(scrapers, "_guarded_get", lambda url, **kw: _Resp(200, xml))
        assert scrapers._csb_sitemap_links("https://jobs.rwe.com") == {
            "https://jobs.rwe.com/job/Essen-Working-Student-IT-Security/9/": "Essen Working Student IT Security"}

    @pytest.mark.parametrize("host", ["https://jobs.deloitte.de", "https://jobs.bayer.com", "https://career.deutz.com"])
    def test_a_relative_search_link_becomes_a_link_on_the_host(self, monkeypatch, host):
        asked = []

        def get(url, params=None, **kw):
            return _Resp(200, SEARCH) if url.endswith("/search/") else _Resp(404, "")
        monkeypatch.setattr(scrapers, "_guarded_get", get)
        monkeypatch.setattr(scrapers, "_CSB_SITES", ((host, "Deloitte"),))
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)
        monkeypatch.setattr(scrapers, "_rmk_page", lambda u, s: asked.append(u) or [])
        scrapers.scrape_csb()
        assert asked == [host + "/job/Duesseldorf-Werkstudent-Cyber-%28mwd%29/444/"]

    def test_deloitte_gets_its_search_links_and_its_feed_links(self, monkeypatch):
        asked = []

        def get(url, params=None, **kw):
            return _Resp(200, SEARCH if url.endswith("/search/") else RSS)
        monkeypatch.setattr(scrapers, "_guarded_get", get)
        monkeypatch.setattr(scrapers, "_CSB_SITES", (("https://jobs.deloitte.de", "Deloitte"),))
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)
        monkeypatch.setattr(scrapers, "_rmk_page", lambda u, s: asked.append(u) or [])
        scrapers.scrape_csb()
        assert len(asked) == 3 and all(u.startswith("https://jobs.deloitte.de/job/") for u in asked)


# -- Workday company pages ----------------------------------------------------------
POSTING = {"title": "Werkstudent Data Science (m/w/d)", "externalPath": "/job/Berlin/Werkstudent-Data_R1",
           "locationsText": "Berlin, Germany", "postedOn": "Posted Today"}


class _Workday:
    """A Workday tenant whose list answers come from a script."""

    def __init__(self, monkeypatch, answers, detail=200):
        self.answers, self.detail, self.posts, self.sleeps = list(answers), detail, 0, []
        monkeypatch.setattr(scrapers, "_guarded_post", self.post)
        monkeypatch.setattr(scrapers, "_guarded_get", self.get)
        monkeypatch.setattr(scrapers.time, "sleep", self.sleeps.append)
        scrapers._wd_answers_reset()

    def post(self, url, json=None, **kw):
        self.posts += 1
        status, headers = self.answers.pop(0) if self.answers else (200, {})
        data = {"jobPostings": [POSTING] if status == 200 else [], "facets": []}
        return _Resp(status, "", data, headers)

    def get(self, url, **kw):
        return _Resp(self.detail, "", {"jobPostingInfo": {"jobDescription": "<p>Python and SQL</p>"}})


ENTRY = ("acme", "wd3", "Careers", "Werkstudent")      # one search string: one search pass


class TestWorkday:
    def test_a_refused_answer_is_counted_and_named(self, monkeypatch):
        _Workday(monkeypatch, [(403, {})])
        assert scrapers._workday_cxs_tenant(ENTRY, mode="base") == []
        assert scrapers._WD_ANSWERS == {("list", 403): 1}
        assert scrapers._wd_answers_line("WD-CXS") == "  [WD-CXS] answers other than 200: list 403 x1 (acme)"

    def test_after_a_429_it_waits_once_and_asks_the_same_page_again(self, monkeypatch):
        wd = _Workday(monkeypatch, [(429, {"Retry-After": "5"}), (200, {})])
        rows = scrapers._workday_cxs_tenant(ENTRY, mode="base")
        assert [r["title"] for r in rows] == ["Werkstudent Data Science (m/w/d)"]
        assert 5.0 in wd.sleeps and wd.posts == 2
        assert scrapers._WD_ANSWERS == {("list", 429): 1}

    def test_a_second_429_ends_the_tenant(self, monkeypatch):
        wd = _Workday(monkeypatch, [(429, {}), (429, {})])
        assert scrapers._workday_cxs_tenant(ENTRY, mode="base") == []
        assert wd.sleeps.count(30.0) == 1 and wd.posts == 2
        assert scrapers._WD_ANSWERS == {("list", 429): 2}

    def test_a_tenant_that_keeps_refusing_is_left_alone_for_its_other_searches(self, monkeypatch):
        wd = _Workday(monkeypatch, [(429, {})] * 40)
        assert scrapers._workday_cxs_tenant(("acme", "wd3", "Careers"), mode="base") == []
        assert wd.posts == 2 and wd.sleeps.count(30.0) == 1
        assert scrapers._WD_ANSWERS == {("list", 429): 2}

    def test_a_403_ends_the_tenant_at_once(self, monkeypatch):
        wd = _Workday(monkeypatch, [(403, {})] * 40)
        scrapers._workday_cxs_tenant(("acme", "wd3", "Careers"), mode="base")
        assert wd.posts == 1 and wd.sleeps == []

    def test_another_error_ends_only_that_search(self, monkeypatch):
        wd = _Workday(monkeypatch, [(404, {})] * 40)
        scrapers._workday_cxs_tenant(("acme", "wd3", "Careers"), mode="base")
        assert wd.posts == len(scrapers._WD_STUDENT_SEARCHES) + 1        # every search asked once
        assert scrapers._WD_ANSWERS == {("list", 404): wd.posts}

    def test_a_wait_past_the_time_limit_is_not_started(self, monkeypatch):
        wd = _Workday(monkeypatch, [(429, {"Retry-After": "50"})])
        now = scrapers.time.time()
        assert scrapers._workday_cxs_tenant(ENTRY, deadlines=(now + 10, now + 20), mode="base") == []
        assert 50.0 not in wd.sleeps and wd.posts == 1

    @pytest.mark.parametrize("value,wait", [(None, 30.0), ("5", 5.0), ("120", 60.0), ("soon", 30.0), ("0", 1.0)])
    def test_the_wait_follows_retry_after_within_limits(self, value, wait):
        assert scrapers._wd_retry_wait(_Resp(429, headers={} if value is None else {"Retry-After": value})) == wait

    def test_a_refused_detail_page_is_counted_too(self, monkeypatch):
        _Workday(monkeypatch, [(200, {})], detail=429)
        rows = scrapers._workday_cxs_tenant(ENTRY, mode="base")
        assert rows and rows[0].get("_stub") is True
        assert scrapers._WD_ANSWERS == {("detail", 429): 1}

    def test_nothing_from_any_company_with_errors_is_a_digest_warning(self, monkeypatch, capsys):
        _Workday(monkeypatch, [(403, {})] * 10)
        monkeypatch.setattr(scrapers, "WORKDAY_CXS_TENANTS", [ENTRY, ("other", "wd5", "Jobs", "Student")])
        assert scrapers.scrape_workday_cxs() == []
        out = capsys.readouterr().out
        assert "answers other than 200: list 403 x2 (acme, other)" in out
        (warning,) = scrapers.workday_warnings()
        assert "no jobs from any of the 2 companies" in warning and "2 of them answered with an error" in warning

    def test_jobs_found_mean_no_warning(self, monkeypatch):
        _Workday(monkeypatch, [(200, {})] * 10)
        monkeypatch.setattr(scrapers, "WORKDAY_CXS_TENANTS", [ENTRY])
        assert len(scrapers.scrape_workday_cxs()) == 1
        assert scrapers.workday_warnings() == []

    def test_main_puts_the_warning_in_the_digest(self):
        assert "_scrapers.workday_warnings()" in inspect.getsource(main)


# -- web search ------------------------------------------------------------------------
class TestWebSearch:
    def test_the_bing_backed_search_is_not_run_and_brave_is(self):
        source = inspect.getsource(scrapers.scrape_all)
        lines = [ln.strip() for ln in source.splitlines()]
        assert "scrape_brave_search,       # Brave web search API" in lines
        assert not [ln for ln in lines if ln.startswith("scrape_web_search,")]

    def test_its_source_name_is_retired_so_the_alarm_stays_quiet(self):
        assert "WebSearch" in main._RETIRED_SOURCES
        history = [{"sources": {"WebSearch": 250, "Brave": 70}}] * 5
        assert not [w for w in main._dead_source_warnings(history, {"Brave": 70}) if "WebSearch" in w]


# -- LinkedIn: which search found what -----------------------------------------------
class TestWhichSearchFoundWhat:
    def test_postings_only_one_search_found_are_counted_per_search(self, lane, monkeypatch):
        fake = FakeLinkedIn()
        fake.set("Search A", "Germany", [(950000000 + i, f"Data job {i}") for i in range(1, 11)])
        fake.set("Search B", "Germany", [(950000000 + i, f"Data job {i}") for i in range(6, 16)])
        l = lane(fake=fake, pools=(), queries=("Search A", "Search B"))
        known = scrapers.make_id("https://www.linkedin.com/jobs/view/950000001", "Data job 1", "Acme GmbH")
        monkeypatch.setattr(scrapers, "KNOWN_JOB_IDS", {known})
        l.run()
        per = scrapers.LINKEDIN_REPORT["per_search"]
        # pages, postings, only this search, of those not seen before
        assert per == {"q|search a": [2, 10, 5, 4], "q|search b": [2, 10, 5, 5]}

    def test_a_search_that_only_repeats_another_shows_zero(self, lane, capsys):
        fake = FakeLinkedIn()
        rows = [(960000000 + i, f"Data job {i}") for i in range(10)]
        fake.set("Search A", "Germany", rows)
        fake.set("Search B", "Germany", rows[:5])
        lane(fake=fake, pools=(), queries=("Search A", "Search B")).run()
        per = scrapers.LINKEDIN_REPORT["per_search"]
        assert per["q|search b"][2] == 0 and per["q|search a"][2] == 5
        assert "of 2 searches read, 1 found nothing no other search found" in capsys.readouterr().out

    def test_a_search_that_was_not_reached_is_not_listed(self, lane, monkeypatch):
        fake = FakeLinkedIn(default=3)
        fake.refuse_from = 2
        lane(fake=fake, pools=(), queries=("Search A", "Search B")).run()
        assert list(scrapers.LINKEDIN_REPORT["per_search"]) == ["q|search a"]

    def test_the_remote_student_searches_are_in_the_plan(self):
        plan = {s["words"]: s for s in scrapers._linkedin_plan()}
        for words in scrapers._LI_REMOTE_QUERIES:
            assert plan[words]["stage"] == 2 and plan[words]["location"] == "Germany"
        assert len(scrapers._LI_REMOTE_QUERIES) == 5
