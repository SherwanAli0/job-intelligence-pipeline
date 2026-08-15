"""
Tests for the three sources added from the verified research sweep:
Absolventa (D2), get-in-IT (D1), hiring.cafe (G1). All offline — fixtures
mirror the live payloads the scrapers were built against.

The bug these exist to prevent recurring: on first run all three returned
ZERO because a missing `json` import raised NameError inside per-page
try/excepts, which swallowed it silently. Every parser is therefore tested
directly, outside its exception guard.
"""

import json

import pytest

import scrapers


ABSOLVENTA_HTML = """
<html><head>
<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[]}</script>
<script type="application/ld+json">{
  "@context":"https://schema.org","@type":"JobPosting",
  "title":"Junior Data Analyst (m/w/d)",
  "description":"<p>Wir suchen einen Junior Data Analyst.</p><p>SQL, Python.</p>",
  "datePosted":"2026-07-30",
  "employmentType":"FULL_TIME",
  "hiringOrganization":{"@type":"Organization","name":"Acme Analytics GmbH"},
  "jobLocation":[{"@type":"Place","address":{"addressLocality":"Berlin"}},
                 {"@type":"Place","address":{"addressLocality":"Hamburg"}}]
}</script>
</head><body></body></html>
"""

GETINIT_HTML = """
<html><body>
<script id="__NEXT_DATA__" type="application/json">{"props":{"initialState":{"jobJob":{
  "loading":false,
  "job":{"header":{"title":"Junior Software Engineer (m/w/d)","companyName":"QAware",
                   "locations":["Mainz","München","Rosenheim","Berlin"]},
         "content":"<h2>Aufgaben</h2><p>Python und Cloud.</p>",
         "jobInfo":{"degrees":["Bachelor","Master/Diplom"],"studySubjects":["Informatik"]}}
}}}}</script>
</body></html>
"""

HIRINGCAFE_HIT = {
    "apply_url": "https://careers.example.com/jobs/123",
    "job_information": {"title": "Data Analyst (all genders)"},
    "v5_processed_job_data": {
        "core_job_title": "Data Analyst",
        "company_name": "Deutsche Bahn Group",
        "workplace_cities": ["Munich, Bavaria, DE"],
        "workplace_countries": ["DE"],
        "seniority_level": "Entry Level",
        "language_requirements": ["German"],
        "estimated_publish_date": "2026-07-30T12:00:00.000Z",
        "company_sector_and_industry": "Transportation",
        "company_tagline": "Rail operator",
    },
}


class TestJsonLdParser:
    def test_selects_jobposting_among_other_blocks(self):
        d = scrapers._parse_jsonld_jobposting(ABSOLVENTA_HTML)
        assert d and d["@type"] == "JobPosting"
        assert d["title"] == "Junior Data Analyst (m/w/d)"

    def test_no_jobposting_returns_none(self):
        assert scrapers._parse_jsonld_jobposting("<html>nothing</html>") is None

    def test_multi_city_location(self):
        d = scrapers._parse_jsonld_jobposting(ABSOLVENTA_HTML)
        assert scrapers._jsonld_location(d) == "Berlin, Hamburg"


class TestAbsolventaPage:
    def test_parses_fixture_into_job(self, monkeypatch):
        class R:
            status_code = 200
            text = ABSOLVENTA_HTML
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        out = scrapers._absolventa_page("https://www.absolventa.de/stellenangebote/1-b-x")
        assert len(out) == 1
        j = out[0]
        assert j["source"] == "Absolventa"
        assert j["company"] == "Acme Analytics GmbH"
        assert "SQL, Python" in j["description"]
        assert j["posted_at"] == "2026-07-30"

    def test_slug_vocabulary(self):
        """Post-pivot the slug must look like BOTH a data role AND a student
        role. Absolventa mixes graduate-entry and student jobs in one sitemap
        and only the student half is eligible now."""
        keep = "10-s-werkstudent-data-science-m-w-d"
        keep_praktikum = "11-s-praktikum-data-science"   # internships added 2026-08-16
        drop_fulltime = "10-b-junior-data-scientist-m-w-d"
        drop_sales = "12-s-werkstudent-verkaeufer-m-w-d"

        def picked(slug):
            return bool(scrapers._ABSOLVENTA_VOCAB.search(slug)
                        and scrapers._ABSOLVENTA_STUDENT.search(slug)
                        and not scrapers._ABSOLVENTA_EXCLUDE.search(slug))

        assert picked(keep)
        assert picked(keep_praktikum), "internships are targets since 2026-08-16"
        assert not picked(drop_fulltime), "full-time graduate role is off-target now"
        assert not picked(drop_sales), "student role, wrong field"

    def test_studentische_hilfskraft_slug_is_picked(self):
        assert scrapers._ABSOLVENTA_STUDENT.search("13-s-studentische-hilfskraft-informatik")


class TestGetInItPage:
    def test_parses_next_data_into_job(self, monkeypatch):
        class R:
            status_code = 200
            text = GETINIT_HTML
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        out = scrapers._getinit_page("https://www.get-in-it.de/jobsuche/p123")
        assert len(out) == 1
        j = out[0]
        assert j["source"] == "GetInIT"
        assert j["title"].startswith("Junior Software Engineer")
        assert j["location"] == "Mainz, München, Rosenheim"
        # degrees surfaced so the Masters filter can read them
        assert "Master/Diplom" in j["description"]

    def test_page_without_next_data_yields_nothing(self, monkeypatch):
        class R:
            status_code = 200
            text = "<html>redesigned page</html>"
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        assert scrapers._getinit_page("https://www.get-in-it.de/jobsuche/p1") == []


class TestHiringCafe:
    def _resp(self, hits, build_id_page=True):
        class R:
            def __init__(self, text="", payload=None):
                self.status_code = 200
                self.text = text
                self._p = payload
            def json(self):
                return self._p
        home = R(text='x "buildId":"BUILD123" y')
        data = R(payload={"pageProps": {"ssrHits": hits}})
        return home, data

    def test_maps_hit_to_job(self, monkeypatch):
        home, data = self._resp([HIRINGCAFE_HIT])
        calls = iter([home, data, data, data, data])
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: next(calls))
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_HIRINGCAFE_QUERIES", ("data",))
        out = scrapers.scrape_hiringcafe()
        assert len(out) == 1
        j = out[0]
        assert j["source"] == "HiringCafe"
        assert j["company"] == "Deutsche Bahn Group"
        assert j["url"] == "https://careers.example.com/jobs/123"
        assert j["apply_url"] == j["url"]
        assert "Language requirements: German" in j["description"]
        assert j["posted_at"].startswith("2026-07-30")

    def test_non_de_hits_are_dropped(self, monkeypatch):
        hit = json.loads(json.dumps(HIRINGCAFE_HIT))
        hit["v5_processed_job_data"]["workplace_countries"] = ["US"]
        home, data = self._resp([hit])
        calls = iter([home, data])
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: next(calls))
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_HIRINGCAFE_QUERIES", ("data",))
        assert scrapers.scrape_hiringcafe() == []

    def test_missing_build_id_fails_gracefully(self, monkeypatch, capsys):
        class R:
            status_code = 200
            text = "<html>totally new layout</html>"
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        assert scrapers.scrape_hiringcafe() == []
        assert "buildId not found" in capsys.readouterr().out


class TestWiring:
    def test_all_three_in_scrape_all(self):
        import inspect
        src = inspect.getsource(scrapers.scrape_all)
        for name in ("scrape_hiringcafe", "scrape_absolventa", "scrape_getinit"):
            assert name in src, name

    def test_source_priorities_registered(self):
        import main
        for src in ("GetInIT", "Absolventa", "HiringCafe"):
            assert src in main._SOURCE_PRIORITY
