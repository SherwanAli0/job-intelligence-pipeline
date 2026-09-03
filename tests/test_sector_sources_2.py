"""Sector sweep batch 2: UKB (WordPress REST), rhenag (embedded JSON),
Deutsche Bahn (db.jobs JSON), plus the d.vinci custom-host support that DZNE
needs. All offline — fixtures mirror the live payloads."""

import json

import scrapers


UKB_ROWS = [
    {"title": {"rendered": "Studentische Hilfskraft (m/w/d)"},
     "link": "https://karriereamukb.de/jobs/studentische-hilfskraft-m-w-d/",
     "date": "2026-09-03T13:53:33"},
    {"title": {"rendered": "Medizinische*r Fachangestellte*r (m/w/d)"},
     "link": "https://karriereamukb.de/jobs/mfa/", "date": "2026-09-03T13:00:00"},
    {"title": {"rendered": "Werkstudent*in in der Sachbearbeitung &amp; IT"},
     "link": "https://karriereamukb.de/jobs/werkstudent-it/", "date": "2026-09-01T09:00:00"},
]

RHENAG_HTML = ('<html><body><div data-pages="' + json.dumps([
    {"title": "Werkstudent KI &amp; Innovation (m/w/d)",
     "uriPathSegment": "werkstudent-ki-innovation-m-w-d",
     "jobDetailPageTags": "Köln,Werkstudierende",
     "date": {"date": "2026-08-10 09:00:00.000000"}},
    {"title": "Netzmonteur (m/w/d)", "uriPathSegment": "netzmonteur",
     "jobDetailPageTags": "Siegburg,Berufserfahrene", "date": {"date": "2026-08-01 09:00:00"}},
]).replace('"', "&quot;") + '"></div></body></html>')

DB_ROWS = [
    {"jobId": "631783", "jobTitleInternational": "Werkstudent:in Projektmanagement",
     "target": "/de-de/Suche/Werkstudent-in-Projektmanagement-631783",
     "locations": "['Koblenz']", "pubExternalDate": "1788127200000"},
    {"jobId": "999", "jobTitleInternational": "Werkstudent:in Zittau",
     "target": "/de-de/Suche/x-999", "locations": "['Zittau']", "pubExternalDate": "1788127200000"},
]


class _R:
    def __init__(self, payload=None, text="", status=200):
        self.status_code = status
        self._p = payload
        self.text = text
        self.headers = {"content-type": "application/json" if payload is not None else "text/html"}

    def json(self):
        if self._p is None:
            raise ValueError("not json")
        return self._p


class TestUKB:
    def test_keeps_student_titles_only_and_decodes_entities(self, monkeypatch):
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _R(UKB_ROWS))
        monkeypatch.setattr(scrapers, "_page_text", lambda url: "ad body")
        out = scrapers.scrape_ukb()
        titles = [j["title"] for j in out]
        assert "Studentische Hilfskraft (m/w/d)" in titles
        assert "Werkstudent*in in der Sachbearbeitung & IT" in titles
        assert not any("Fachangestellte" in t for t in titles)
        j = out[0]
        assert j["source"] == "UKB" and j["location"] == "Bonn"
        assert j["posted_at"].startswith("2026-09-03") and j["description"] == "ad body"


class TestRhenag:
    def test_parses_embedded_json_and_builds_root_urls(self, monkeypatch):
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _R(None, RHENAG_HTML))
        monkeypatch.setattr(scrapers, "_page_text", lambda url: "")
        out = scrapers.scrape_rhenag()
        assert len(out) == 1
        j = out[0]
        assert j["title"] == "Werkstudent KI & Innovation (m/w/d)"
        # Verified live: detail pages live at the site root, not under /jobs/.
        assert j["url"] == "https://karriere.rhenag.de/werkstudent-ki-innovation-m-w-d"
        assert j["location"] == "Köln" and j["posted_at"] == "2026-08-10"
        assert j["source"] == "rhenag"

    def test_missing_attribute_fails_loud_not_silent(self, monkeypatch, capsys):
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _R(None, "<html>new layout</html>"))
        assert scrapers.scrape_rhenag() == []
        assert "data-pages attribute not found" in capsys.readouterr().out


class TestDeutscheBahn:
    def test_belt_filter_url_and_epoch_date(self, monkeypatch):
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _R(DB_ROWS))
        monkeypatch.setattr(scrapers, "_page_text", lambda url: "")
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_DB_QUERIES", ("Werkstudent",))
        out = scrapers.scrape_db_jobs()
        assert [j["location"] for j in out] == ["Koblenz"], "Zittau is not in the belt"
        j = out[0]
        assert j["url"] == "https://db.jobs/de-de/Suche/Werkstudent-in-Projektmanagement-631783"
        assert j["posted_at"] == "2026-08-30"          # 1788127200000 ms, UTC date
        assert j["source"] == "DeutscheBahn"

    def test_one_html_query_does_not_discard_the_others(self, monkeypatch):
        calls = iter([_R(None, "<html>blocked</html>"), _R(DB_ROWS)])
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: next(calls))
        monkeypatch.setattr(scrapers, "_page_text", lambda url: "")
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_DB_QUERIES", ("bad", "Werkstudent"))
        assert len(scrapers.scrape_db_jobs()) == 1


class TestDvinciCustomHost:
    def test_dotted_tenant_is_used_as_full_host(self, monkeypatch):
        seen = []

        def fake_get(url, **kw):
            seen.append(url)
            return _R([{"position": "Student Research Assistant (f/m/x)",
                        "subtitle": "Bonn", "pageDescription": "Neuroscience data analysis",
                        "jobPublicationURL": "https://jobs.dzne.de/de/jobs/1"}])
        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_DVINCI_TENANTS", (("jobs.dzne.de", "DZNE", "Bonn"),))
        out = scrapers.scrape_dvinci()
        assert seen == ["https://jobs.dzne.de/de/jobs"]
        assert len(out) == 1 and out[0]["source"] == "DZNE"

    def test_wiring_and_priorities(self):
        import inspect
        import main
        src = inspect.getsource(scrapers.scrape_all)
        for fn in ("scrape_ukb", "scrape_rhenag", "scrape_db_jobs"):
            assert fn in src, fn
        for src_name in ("UKB", "rhenag", "DeutscheBahn", "DZNE", "Uniper", "1und1", "WDR", "Lufthansa"):
            assert src_name in main._SOURCE_PRIORITY, src_name
            assert src_name in main._LONG_LIVED_SOURCES, src_name
