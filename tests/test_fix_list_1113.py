"""Fixes from the 13 November 2026 fix list (research rechecked by Opus agents).

Each class covers one item of ongoing/JOB-PIPELINE-FIX-LIST.md in the owner's
portfolio workspace.
"""
import filters
import scrapers


class _Resp:
    def __init__(self, data, status=200, text="", headers=None):
        self._data = data
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {}

    def json(self):
        return self._data


# ---------------------------------------------------------------- item 1
class TestWorkdayGermanyFacet:
    """Tenants that accept the Germany facet but ignore it must not get their
    foreign postings labelled ", Germany"."""

    def _run(self, monkeypatch, reply):
        def fake_post(url, json=None, headers=None, timeout=None):
            return reply(bool(json["appliedFacets"]), json["searchText"], json["offset"])
        monkeypatch.setattr(scrapers.requests, "post", fake_post)
        monkeypatch.setattr(scrapers, "_wd_fetch_description", lambda *a: "An English advert. " * 30)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        return scrapers._workday_cxs_tenant(("astrazeneca", "wd3", "Careers"))

    @staticmethod
    def _jp(title, loc, i=0):
        return {"title": title, "locationsText": loc, "externalPath": f"/job/x/{i}", "postedOn": "Today"}

    IGNORING_FACETS = [{"facetParameter": "locationMainGroup",
                        "values": [{"facetParameter": "locations", "values": []}]},
                       {"facetParameter": "timeType", "values": []}]
    HONOURING_FACETS = [{"facetParameter": "locationMainGroup",
                         "values": [{"facetParameter": "locationCountry", "values": []}]}]

    def test_a_reply_without_a_country_facet_is_not_labelled_germany(self, monkeypatch):
        def reply(facet, search, offset):
            if search == "intern" and offset == 0:
                return _Resp({"facets": self.IGNORING_FACETS, "jobPostings": [
                    self._jp("Facilities Safety Intern", "US - Tarzana - CA", 1),
                    self._jp("Data Science Intern", "2 Locations", 2)]})
            return _Resp({"facets": self.IGNORING_FACETS, "jobPostings": []})
        out = self._run(monkeypatch, reply)
        assert {j["location"] for j in out} == {"US - Tarzana - CA", "2 Locations"}
        assert "astrazeneca" in scrapers._WD_FACET_IGNORED

    def test_a_reply_with_the_country_facet_is_labelled_germany(self, monkeypatch):
        scrapers._WD_FACET_IGNORED.clear()

        def reply(facet, search, offset):
            if facet and search == "intern" and offset == 0:
                return _Resp({"facets": self.HONOURING_FACETS,
                              "jobPostings": [self._jp("Data Science Intern", "2 Locations", 2)]})
            return _Resp({"facets": self.HONOURING_FACETS, "jobPostings": []})
        out = self._run(monkeypatch, reply)
        assert [j["location"] for j in out] == ["2 Locations, Germany"]
        assert "astrazeneca" not in scrapers._WD_FACET_IGNORED

    def test_without_a_facets_list_foreign_postings_decide(self, monkeypatch):
        def reply(facet, search, offset):
            if search == "intern" and offset == 0:
                return _Resp({"jobPostings": [self._jp("AI Intern", "Monza, Italy", 1),
                                              self._jp("ML Intern", "CDR (Amsterdam - Cedar)", 2)]})
            return _Resp({"jobPostings": []})
        out = self._run(monkeypatch, reply)
        assert all(not j["location"].endswith("Germany") for j in out)

    def test_a_foreign_place_is_never_relabelled(self):
        assert scrapers._foreign_place("Monza, Italy")
        assert not scrapers._foreign_place("2 Locations")

    def test_the_facet_check_reads_nested_facets(self):
        assert scrapers._wd_has_facet(self.HONOURING_FACETS, "locationCountry")
        assert not scrapers._wd_has_facet(self.IGNORING_FACETS, "locationCountry")


class TestLeadingCountryCode:
    def test_workday_country_prefixes_are_foreign(self):
        for loc in ("us - gaithersburg - md", "usa - tarzana, ca", "us – tarzana – ca",
                    "in - bangalore", "gb - london"):
            assert filters.is_foreign_location(loc), loc

    def test_german_and_mixed_strings_are_not(self):
        for loc in ("de - berlin", "us - remote - germany", "2 locations", "köln"):
            assert not filters.is_foreign_location(loc), loc


# ---------------------------------------------------------------- items 2 and 13
import pytest  # noqa: E402

import robots  # noqa: E402


@pytest.fixture
def robots_on(monkeypatch):
    """robots.txt checks on, with fake robots.txt files per host."""
    monkeypatch.setenv("JOBHUNTER_ROBOTS", "1")
    robots.reset()
    files: dict = {}

    def fake_fetch(url):
        host = url.split("/")[2]
        code, text = files.get(host, (404, ""))
        if code == "raise":
            raise ConnectionError("down")
        return _Resp({}, code, text)
    monkeypatch.setattr(robots, "_fetch_robots", fake_fetch)
    yield files
    robots.reset()


XING_ROBOTS = """User-agent: CrazyWebCrawler-Spider
Disallow: /
User-agent: *
Disallow: /xing-one/api
Disallow: /jobs/search/
Disallow: /jobs/search?*
Allow: /start/signup
"""


class TestRobotsRules:
    def test_xing_api_is_disallowed_and_job_pages_are_not(self, robots_on):
        robots_on["www.xing.com"] = (200, XING_ROBOTS)
        assert robots.blocked_by("https://www.xing.com/xing-one/api") == "*"
        assert robots.blocked_by("https://www.xing.com/jobs/search?keywords=data") == "*"
        assert robots.blocked_by("https://www.xing.com/jobs/koeln-werkstudent-data-146268547") == ""

    def test_a_block_aimed_at_python_scripts_applies(self, robots_on):
        robots_on["portal.example"] = (200, "User-agent: python-requests\nDisallow: /\n\n"
                                            "User-agent: *\nAllow: /\n")
        assert robots.blocked_by("https://portal.example/vacancies") == "python-requests"

    def test_longest_match_wins_and_allow_wins_a_tie(self, robots_on):
        robots_on["a.example"] = (200, "User-agent: *\nDisallow: /jobs/\nAllow: /jobs/public/\n"
                                       "Disallow: /x\nAllow: /x\n")
        assert robots.blocked_by("https://a.example/jobs/123") == "*"
        assert robots.blocked_by("https://a.example/jobs/public/123") == ""
        assert robots.blocked_by("https://a.example/x") == ""

    def test_wildcards_and_end_anchor(self, robots_on):
        robots_on["b.example"] = (200, "User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?*\n")
        assert robots.blocked_by("https://b.example/files/ad.pdf") == "*"
        assert robots.blocked_by("https://b.example/files/ad.pdf.html") == ""
        assert robots.blocked_by("https://b.example/search?q=x") == "*"

    def test_missing_file_allows_and_unreachable_file_blocks(self, robots_on):
        robots_on["c.example"] = (404, "")
        robots_on["d.example"] = (503, "")
        robots_on["e.example"] = ("raise", "")
        assert robots.blocked_by("https://c.example/anything") == ""
        assert robots.blocked_by("https://d.example/anything") == "unreachable"
        assert robots.blocked_by("https://e.example/anything") == "unreachable"

    def test_owner_exceptions_are_not_checked(self, robots_on):
        robots_on["www.linkedin.com"] = (200, "User-agent: *\nDisallow: /\n")
        assert robots.blocked_by("https://www.linkedin.com/jobs/view/1") == ""

    def test_switch_off(self, robots_on, monkeypatch):
        robots_on["f.example"] = (200, "User-agent: *\nDisallow: /\n")
        monkeypatch.setenv("JOBHUNTER_ROBOTS", "0")
        assert robots.blocked_by("https://f.example/x") == ""


class TestRobotsGuardInScrapers:
    def test_a_disallowed_address_is_never_requested(self, robots_on, monkeypatch):
        robots_on["career.example"] = (200, "User-agent: *\nDisallow: /services/\n")
        called = []
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **k: called.append(a) or _Resp({}))
        r = scrapers._guarded_get("https://career.example/services/rss/job/", params={"q": "x"})
        assert r.status_code == 451 and called == []
        assert robots.SKIPPED["career.example"] == 1

    def test_query_parameters_count(self, robots_on, monkeypatch):
        robots_on["www.xing.com"] = (200, XING_ROBOTS)
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **k: _Resp({}))
        assert scrapers._guarded_get("https://www.xing.com/jobs/search", params={"q": "x"}).status_code == 451

    def test_documented_apis_are_exempt(self, robots_on, monkeypatch):
        robots_on["api.smartrecruiters.com"] = (200, "User-agent: *\nDisallow: /\n")
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **k: _Resp({"content": []}))
        assert scrapers._guarded_get("https://api.smartrecruiters.com/v1/companies/X/postings").status_code == 200
        assert scrapers._robots_exempt("https://acme.jobs.personio.de/xml")
        assert scrapers._robots_exempt("https://acme.recruitee.com/api/offers/")
        assert not scrapers._robots_exempt("https://www.xing.com/xing-one/api")

    def test_bund_source_switches_itself_off(self, robots_on, monkeypatch):
        robots_on["www.service.bund.de"] = (200, "User-agent: *\nDisallow: /Content/DE/Stellen/Suche/\n")

        def no_request(*a, **k):
            raise AssertionError("no request expected")
        monkeypatch.setattr(scrapers.requests, "get", no_request)
        assert scrapers.scrape_bund_rss() == []


class TestCsbSitemap:
    def test_student_slugs_from_the_sitemap(self, monkeypatch):
        xml = ("<urlset>"
               "<url><loc>https://job.hdi.group/job/Hilden-Werkstudentin-Robotics-&amp;-KI-Automatisierung/1454405533/</loc></url>"
               "<url><loc>https://job.hdi.group/job/Mainz-Sachbearbeiterin-Schaden/1443846733/</loc></url>"
               "<url><loc>https://job.hdi.group/job/K%C3%B6ln-Praktikum-Data-Analytics/1/</loc></url>"
               "</urlset>")
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **k: _Resp({}, 200, xml))
        links = scrapers._csb_sitemap_links("https://job.hdi.group")
        assert list(links.values()) == ["Hilden Werkstudentin Robotics & KI Automatisierung",
                                        "Köln Praktikum Data Analytics"]

    def test_no_csb_request_uses_the_services_path(self):
        import inspect
        assert "services/rss" not in inspect.getsource(scrapers.scrape_csb)
        assert "https://careers.eon.com" not in {h for h, _ in scrapers._CSB_SITES}


# ---------------------------------------------------------------- item 9
class TestJsonLd:
    AD = '{"@type": "JobPosting", "title": "Werkstudent Data", "description": "x"}'

    def test_single_quotes_and_extra_attributes(self):
        html = f"<script data-x='1' type='application/ld+json' nonce=\"a\">{self.AD}</script>"
        assert scrapers._parse_jsonld_jobposting(html)["title"] == "Werkstudent Data"

    def test_graph_wrapper_and_list_type(self):
        html = ('<script type="application/ld+json">{"@context": "https://schema.org", "@graph": ['
                '{"@type": "Organization"}, {"@type": ["JobPosting"], "title": "ML Intern"}]}</script>')
        assert scrapers._parse_jsonld_jobposting(html)["title"] == "ML Intern"

    def test_the_old_exact_tag_still_works(self):
        assert scrapers._parse_jsonld_jobposting(f'<script type="application/ld+json">{self.AD}</script>')

    def test_body_failures_are_reported_per_host(self):
        scrapers._BODY_OUTCOMES.clear()
        scrapers._body_note("https://a.example/1", "ok")
        scrapers._body_note("https://b.example/1", "HTTP 403")
        scrapers._body_note("https://b.example/2", "HTTP 403")
        assert scrapers.body_fetch_report() == "b.example 2/2 (HTTP 403 2)"


# ---------------------------------------------------------------- item 3
class TestLinkedInGuest:
    PAGE = "".join(
        f'<li><a class="base-card__full-link" href="https://de.linkedin.com/jobs/view/werkstudent-{i}-{i}"></a>'
        f'<h3 class="base-search-card__title">Werkstudent Data {i}</h3>'
        f'<h4 class="base-search-card__subtitle">Acme {i}</h4>'
        f'<span class="job-search-card__location">Köln</span></li>' for i in range(6))

    def test_already_seen_postings_get_no_description_fetch(self, monkeypatch):
        calls = {"n": 0}

        def fake_get(url, params=None, headers=None, timeout=None):
            calls["n"] += 1
            return _Resp({}, 200 if calls["n"] == 1 else 404, self.PAGE if calls["n"] == 1 else "")
        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        monkeypatch.setattr(scrapers, "_LI_GUEST_QUERIES", ("werkstudent data",))
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        enriched = []
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions",
                            lambda jobs, **kw: enriched.extend(jobs))
        first = scrapers.job("Werkstudent Data 0", "Acme 0", "Köln",
                             scrapers._li_canonical_url("https://de.linkedin.com/jobs/view/werkstudent-0-0"),
                             "linkedin")
        monkeypatch.setattr(scrapers, "KNOWN_JOB_IDS", {first["id"]})
        rows = scrapers.scrape_linkedin_guest()
        assert len(rows) == 6
        assert first["id"] not in {j["id"] for j in enriched} and len(enriched) == 5

    def test_a_timeout_keeps_what_the_guest_search_collected(self, monkeypatch):
        import threading
        release = threading.Event()

        def scrape_linkedin_guest():
            scrapers._LI_GUEST_PARTIAL.clear()
            scrapers._LI_GUEST_PARTIAL.append({"id": "a", "title": "Werkstudent AI"})
            release.wait(5)
            return list(scrapers._LI_GUEST_PARTIAL)
        monkeypatch.setattr(scrapers, "SCRAPER_TIMEOUT_SECONDS", 0.2)
        scrapers._PENDING_SCRAPERS.clear()
        jobs, timed_out = scrapers._run_scraper_guarded(scrape_linkedin_guest)
        assert timed_out and [j["id"] for j in jobs] == ["a"]
        release.set()
        assert [j["id"] for j in scrapers._recover_late_scrapers(grace_seconds=5)] == ["a"]

    def test_a_scraper_that_dies_after_the_timeout_still_gives_its_partial(self, monkeypatch):
        import threading
        release = threading.Event()

        def scrape_linkedin_guest():
            scrapers._LI_GUEST_PARTIAL.clear()
            scrapers._LI_GUEST_PARTIAL.extend([{"id": "a"}, {"id": "b"}])
            release.wait(5)
            raise RuntimeError("enrichment failed")
        monkeypatch.setattr(scrapers, "SCRAPER_TIMEOUT_SECONDS", 0.2)
        monkeypatch.setattr(scrapers.traceback, "print_exc", lambda: None)
        scrapers._PENDING_SCRAPERS.clear()
        scrapers._run_scraper_guarded(scrape_linkedin_guest)
        release.set()
        assert [j["id"] for j in scrapers._recover_late_scrapers(grace_seconds=5)] == ["a", "b"]


# ---------------------------------------------------------------- item 4
class TestEon:
    SITEMAP = ("<urlset><url><loc>https://jobs.eon.com/de/job/Werkstudent_in-Energieanlagen-Essen/1064</loc></url>"
               "<url><loc>https://jobs.eon.com/de/job/Projektleiter-Netzbau-Essen/2000</loc></url>"
               "<url><loc>https://jobs.eon.com/de/job/Praktikum-Robotik-Data-Analytics-Kabelsketal/10793</loc></url>"
               "</urlset>")
    PAGE = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Werkstudent:in Energieanlagen",'
            ' "hiringOrganization": {"name": "E.ON Business Solutions"}, "datePosted": "2026-11-12",'
            ' "description": "<p>Ad text</p>", "jobLocation": [{"address": {"addressLocality": "Essen",'
            ' "addressCountry": "DE"}}]}</script>')

    def test_student_slugs_are_fetched_and_parsed(self, monkeypatch):
        fetched = []

        def fake_get(url, headers=None, timeout=None):
            fetched.append(url)
            if url.endswith("sitemap.xml"):
                return _Resp({}, 200, self.SITEMAP, {"content-type": "application/xml"})
            return _Resp({}, 200, self.PAGE, {"content-type": "text/html"})
        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        rows = scrapers.scrape_eon()
        assert len(fetched) == 3                    # sitemap + the two student pages
        assert all("Projektleiter" not in u for u in fetched)
        assert rows[0]["location"] == "Essen, Germany" and rows[0]["source"] == "E.ON"
        assert rows[0]["company"] == "E.ON Business Solutions"

    def test_eon_runs_in_scrape_all(self):
        import inspect
        assert "scrape_eon" in inspect.getsource(scrapers.scrape_all)
        assert "E.ON" in __import__("main")._LONG_LIVED_SOURCES


# ---------------------------------------------------------------- items 5, 6, 12
class TestResearchFeeds:
    HELMHOLTZ = ("<jobs>"
                 "<job><position>Studentische Hilfskräfte</position><title>Studentische Hilfskraft Data Science (m/w/d)</title>"
                 "<institute>Forschungszentrum Jülich</institute><location>Jülich</location>"
                 "<url>https://www.fz-juelich.de/de/karriere/stellenangebote/2026S-1</url></job>"
                 "<job><position>Führungskräfte</position><title>Bereichsleiter Umwelt</title>"
                 "<institute>KIT</institute><location>Karlsruhe</location><url>http://x/a.pdf</url></job>"
                 "<job><position>Praktikum</position><title>Schülerpraktikum Geosysteme</title>"
                 "<institute>GFZ</institute><location>Potsdam</location><url>http://x/b</url></job>"
                 "</jobs>").encode("utf-8")
    MPG = ("<rss><channel>"
           "<item><title>Studentische Hilfskraft (m/w/d) Maschinelles Lernen</title>"
           "<link>https://www.mpg.de/job-1</link><pubDate>Fri, 13 Nov 2026 00:00:00 +0100</pubDate>"
           "<description>Das Max-Planck-Institut für Mathematik in Bonn sucht ...</description></item>"
           "<item><title>Promotionsstellen Quantengravitation</title><link>https://www.mpg.de/2</link>"
           "<description>in Potsdam</description></item>"
           "</channel></rss>").encode("utf-8")
    FZJ_RSS = ("<rss><channel><item><title>Werkstudent KI (w/m/d)</title>"
               "<link>https://www.fz-juelich.de/de/karriere/stellenangebote/2026W-1</link>"
               "<description>https://recruiting.fz-juelich.de/jobposting/abc1</description>"
               "<pubDate>Thu, 12 Nov 2026 10:00:00 +0000</pubDate></item></channel></rss>").encode("utf-8")

    def test_helmholtz_keeps_student_roles_only(self):
        rows, total = scrapers._helmholtz_rows(self.HELMHOLTZ)
        assert total == 3 and [r["title"] for r in rows] == ["Studentische Hilfskraft Data Science (m/w/d)"]
        assert rows[0]["company"] == "Forschungszentrum Jülich" and rows[0]["location"] == "Jülich, Germany"
        assert rows[0]["_stub"] and rows[0]["source"] == "Helmholtz"

    def test_max_planck_gets_the_city_from_the_text(self):
        rows, total = scrapers._mpg_rows(self.MPG)
        assert total == 2 and len(rows) == 1
        assert rows[0]["location"] == "Bonn, Germany" and rows[0]["posted_at"] == "2026-11-13"

    def test_juelich_rss_uses_the_recruiting_links(self):
        rows = scrapers._wl_fzj_rss_rows(self.FZJ_RSS)
        assert rows == [{"title": "Werkstudent KI (w/m/d)", "url": "https://recruiting.fz-juelich.de/jobposting/abc1",
                         "location": "Jülich", "posted": "2026-11-12"}]

    def test_pdf_ads_are_not_read_as_text(self, monkeypatch):
        monkeypatch.setattr(scrapers.requests, "get",
                            lambda *a, **k: _Resp({}, 200, "%PDF-1.7 binary", {"content-type": "application/pdf"}))
        assert scrapers._fetch_full_description("https://example.org/ad.pdf") == ""

    def test_new_sources_are_wired_and_long_lived(self):
        import inspect
        import main
        assert "scrape_research_feeds" in inspect.getsource(scrapers.scrape_all)
        assert {"jobs.pse.kit.edu", "jobs.dkfz.de"} <= {t[0] for t in scrapers._DVINCI_TENANTS}
        assert {"Helmholtz", "MaxPlanck", "KIT", "DKFZ"} <= main._LONG_LIVED_SOURCES


# ---------------------------------------------------------------- item 7
class TestEmployerFeeds:
    def test_workable_keeps_german_and_remote_jobs(self):
        data = {"name": "Circonomit ", "jobs": [
            {"title": "Working Student AI", "url": "https://apply.workable.com/j/A", "telecommuting": False,
             "published_on": "2026-11-12", "description": "<p>Ad</p>",
             "locations": [{"city": "Cologne", "country": "Germany", "countryCode": "DE"}]},
            {"title": "Engineer", "url": "https://apply.workable.com/j/B", "telecommuting": False,
             "locations": [{"city": "Lisbon", "country": "Portugal", "countryCode": "PT"}]},
            {"title": "Data Analyst", "url": "https://apply.workable.com/j/C", "telecommuting": True,
             "locations": [{"city": "Lisbon", "country": "Portugal", "countryCode": "PT"}]}]}
        rows = scrapers._workable_rows(data, "Circonomit")
        assert [(r["title"], r["location"]) for r in rows] == [
            ("Working Student AI", "Cologne, Germany"), ("Data Analyst", "Remote, Lisbon, Portugal")]
        assert rows[0]["company"] == "Circonomit" and rows[0]["description"] == "Ad"

    def test_teamtailor_reads_its_location_elements(self):
        rss = ('<rss xmlns:tt="https://teamtailor.com/locations"><channel><item>'
               '<title>Werkstudent Data</title><link>https://coac.teamtailor.com/jobs/1-werkstudent</link>'
               '<description>&lt;p&gt;Ad&lt;/p&gt;</description><pubDate>Tue, 03 Nov 2026 10:44:52 +0100</pubDate>'
               '<remoteStatus>hybrid</remoteStatus><tt:locations><tt:location><tt:city>Köln</tt:city>'
               '<tt:country>Germany</tt:country></tt:location></tt:locations></item></channel></rss>').encode("utf-8")
        (row,) = scrapers._teamtailor_rows(rss, "coac")
        assert row["location"] == "Hybrid, Köln, Germany" and row["posted_at"] == "2026-11-03"
        assert row["source"] == "Teamtailor" and row["description"] == "Ad"

    def test_breezy_rows_are_stubs_for_the_body_fetch(self):
        data = [{"name": "(Junior) Backend Engineer - AI Agents", "url": "https://eigenblue.breezy.hr/p/1",
                 "published_date": "2026-11-10T09:39:16Z", "type": {"name": "Vollzeit"},
                 "company": {"name": "eigenblue "},
                 "locations": [{"country": {"id": "DE"}, "city": "Munich", "is_remote": False}]},
                {"name": "Sales US", "url": "https://eigenblue.breezy.hr/p/2",
                 "locations": [{"country": {"id": "US"}, "city": "Austin", "is_remote": False}]}]
        (row,) = scrapers._breezy_rows(data, "eigenblue")
        assert row["location"] == "Munich, Germany" and row["_stub"] and row["company"] == "eigenblue"

    def test_feeds_are_wired(self):
        import inspect
        import main
        assert "scrape_employer_feeds" in inspect.getsource(scrapers.scrape_all)
        assert len(scrapers._WORKABLE_ACCOUNTS) >= 18 and len(scrapers._TEAMTAILOR_SITES) >= 7
        assert {"Workable", "Teamtailor", "Breezy"} <= main._EMPLOYER_ATS_SOURCES


# ---------------------------------------------------------------- items 10 and 11
class TestDedupSourcesAndGoRadar:
    def test_the_kept_copy_records_every_source(self, monkeypatch):
        import main
        monkeypatch.setattr(main, "_in_focus", lambda j: True)
        rows = [{"title": "Werkstudent Data", "company": "Acme GmbH", "source": "linkedin", "description": "a"},
                {"title": "Werkstudent Data", "company": "Acme GmbH", "source": "Greenhouse", "description": "ab"},
                {"title": "Werkstudent Data", "company": "Acme GmbH", "source": "indeed", "description": "a"}]
        (kept,) = main._dedup_cross_source(rows)
        assert kept["source"] == "Greenhouse"
        assert kept["_seen_on"] == ["Greenhouse", "indeed", "linkedin"]
        assert main._audit_row(kept, "score")["seen_on"] == "Greenhouse,indeed,linkedin"

    def test_a_single_copy_reports_its_own_source(self):
        import main
        assert main._audit_row({"source": "XING"}, "filter")["seen_on"] == "XING"

    def test_go_means_the_language(self, capsys):
        import main
        jobs = [{"title": "Werkstudent", "description": "We go the extra mile on go-live and go-to-market."},
                {"title": "Backend", "description": "Python, Go or Rust"},
                {"title": "Golang Developer", "description": ""}]
        main._skill_radar(jobs)
        out = capsys.readouterr().out
        line = next(l for l in out.splitlines() if l.strip().startswith("go "))
        assert "67%" in line          # 2 of 3, the English "go" sentence does not count


# ---------------------------------------------------------------- item 14
class TestScoreVersusOutcome:
    def test_auc(self):
        import track
        assert track.auc([80, 90], [40, 50]) == 1.0
        assert track.auc([50], [50]) == 0.5
        assert track.auc([40], [80]) == 0.0
        assert track.auc([], [1]) is None

    def test_outcomes_are_joined_to_the_shown_scores(self, monkeypatch):
        import json
        import track
        shown = "\n".join(json.dumps(r) for r in (
            {"url_hash": "h1", "score": 82, "company": "A", "title": "T1"},
            {"url_hash": "h2", "score": 48, "company": "B", "title": "T2"},
            {"url_hash": "zz", "score": 70, "company": "C", "title": "T3"}))
        monkeypatch.setattr(track, "load_applied", lambda: {
            "h1": {"status": "interview", "company": "A", "title": "T1"},
            "h2": {"status": "rejected", "company": "B", "title": "T2"},
            "other": {"status": "applied", "company": "C", "title": "T3", "last_change": track._now()}})
        import storage
        monkeypatch.setattr(storage, "read_text", lambda name: shown if name == "shown_jobs.jsonl" else None)
        assert sorted(track.score_outcomes()) == [(48.0, "bad"), (70.0, "open"), (82.0, "good")]
        report = "\n".join(track.score_report())
        assert "Not enough outcomes" in report and "80+" in report

    def test_saving_writes_through_storage_and_skips_the_secret(self, monkeypatch, tmp_path):
        import track
        import storage
        written = {}
        monkeypatch.setattr(storage, "write_text", lambda name, text: written.update({name: text}))
        monkeypatch.setattr(track, "APPLIED_FILE", tmp_path / "applied_jobs.json")
        monkeypatch.setattr(track, "_sync_secret", lambda d: (_ for _ in ()).throw(AssertionError("no gh")))
        monkeypatch.delenv("JOBHUNTER_TRACK_SECRET_SYNC", raising=False)
        track.save_applied({"k": {"status": "applied"}})
        assert "applied_jobs.json" in written and (tmp_path / "applied_jobs.json").exists()
