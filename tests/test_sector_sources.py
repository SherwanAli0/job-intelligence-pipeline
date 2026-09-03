"""Sector sweep sources (2026-09): BeeSite JSON, service.bund.de RSS, the
SuccessFactors RSS pass, and the Bonn-belt Workday/Recruitee additions.
All offline — fixtures mirror the live payloads the code was built against."""

import json

import scrapers


BEESITE_PAYLOAD = {"SearchResult": {"SearchResultCountAll": 3, "SearchResultItems": [
    {"MatchedObjectDescriptor": {
        "ID": "1", "PositionTitle": "Studentische Hilfskraft (m/w/d) Klinik für Psychiatrie",
        "PositionURI": "https://lvr-beesite-gjb.app.beesite.de/index.php?ac=jobad&id=1",
        "PositionLocation": [{"CityName": "Köln"}], "PublicationStartDate": "2026-08-20",
        "CareerLevel": {"Name": "Studierende"}}},
    {"MatchedObjectDescriptor": {
        "ID": "2", "PositionTitle": "Intern (m/f/d) Digital Transformation",
        "PositionURI": "https://giz-beesite-production-gjb.app.beesite.de/index.php?ac=jobad&id=2",
        "PositionLocation": [{"CityName": "Bonn"}], "PublicationStartDate": "2026-08-25",
        "CareerLevel": {"Name": "Praktikum"}}},
    {"MatchedObjectDescriptor": {
        "ID": "3", "PositionTitle": "Sachbearbeiter (m/w/d) Vollzeit",
        "PositionURI": "https://x/3", "PositionLocation": [{"CityName": "Bonn"}],
        "CareerLevel": {"Name": "Berufserfahrene"}}},
]}}

BUND_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Werkstudentinnen / Werkstudenten (w/m/d) - befristet - BSI-2026-092</title>
<link>https://www.service.bund.de/IMPORTE/Stellenangebote/editor/BVA-BSI/2026/08/1.html</link>
<description>&lt;p&gt;Arbeitgeber: Bundesamt f&amp;uuml;r Sicherheit in der Informationstechnik  Ort: Bonn  Bewerbungsfrist: 30.09.2026&lt;/p&gt;</description></item>
<item><title>Werkstudentin/Werkstudent (w/m/d) im Bereich Fachkonzepte</title>
<link>https://www.service.bund.de/IMPORTE/Stellenangebote/interamt/2026/06/2.html</link>
<description>Arbeitgeber: Bundesnetzagentur  Ort: Bonn</description></item>
</channel></rss>"""


class TestBeeSite:
    def test_student_filter_keeps_hiwi_and_intern_drops_fulltime(self, monkeypatch):
        class R:
            status_code = 200
            def json(self):
                return BEESITE_PAYLOAD
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        monkeypatch.setattr(scrapers, "_page_text", lambda url: "body text")
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_BEESITE_HOSTS",
                            (("lvr-beesite-gjb.app.beesite.de", "LVR", "Köln"),))
        out = scrapers.scrape_beesite()
        titles = [j["title"] for j in out]
        assert any("Studentische Hilfskraft" in t for t in titles)
        assert any("Intern (m/f/d)" in t for t in titles)
        assert not any("Sachbearbeiter" in t for t in titles)
        j = next(x for x in out if "Hilfskraft" in x["title"])
        assert j["location"] == "Köln" and j["source"] == "LVR"
        assert "Career level: Studierende" in j["description"]
        assert j["posted_at"] == "2026-08-20"

    def test_student_regex_covers_part_time_and_english_intern(self):
        for s in ("Werkstudent", "Intern (m/f/d)", "Teilzeit", "Studentische Hilfskraft",
                  "Duales Studium"):
            assert scrapers._BEESITE_STUDENT.search(s), s
        assert not scrapers._BEESITE_STUDENT.search("International Sales Manager")


class TestBundRSS:
    def test_parser_extracts_title_link_and_unescaped_description(self):
        items = scrapers._bund_items(BUND_RSS)
        assert len(items) == 2
        assert items[0]["title"].startswith("Werkstudentinnen / Werkstudenten")
        assert items[0]["link"].endswith("/BVA-BSI/2026/08/1.html")
        assert "Bundesamt für Sicherheit" in items[0]["desc"]
        assert "<p>" not in items[0]["desc"]

    def test_scrape_maps_employer_and_city_from_description(self, monkeypatch):
        class R:
            status_code = 200
            text = BUND_RSS
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        monkeypatch.setattr(scrapers, "_page_text", lambda url: "full ad body")
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_BUND_QUERIES", ("Werkstudent",))
        out = scrapers.scrape_bund_rss()
        assert len(out) == 2
        bsi = out[0]
        assert bsi["source"] == "BundDE"
        assert "Sicherheit in der Informationstechnik" in bsi["company"]
        assert bsi["location"] == "Bonn"
        assert "full ad body" in bsi["description"]

    def test_duplicate_links_across_queries_collapse(self, monkeypatch):
        class R:
            status_code = 200
            text = BUND_RSS
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        monkeypatch.setattr(scrapers, "_page_text", lambda url: "")
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_BUND_QUERIES", ("Werkstudent", "Data"))
        assert len(scrapers.scrape_bund_rss()) == 2


class TestSectorWiring:
    def test_new_scrapers_run_in_scrape_all(self):
        import inspect
        src = inspect.getsource(scrapers.scrape_all)
        assert "scrape_beesite" in src and "scrape_bund_rss" in src

    def test_csb_sites_include_the_sector_hosts(self):
        names = {s for _, s in scrapers._CSB_SITES}
        assert {"HDI", "NRWBANK", "StadtKoeln"} <= names

    def test_workday_keywords_now_admit_student_titles(self):
        """Debeka's 'Werkstudent IT' has no AI keyword; the pre-filter must
        not throw it away before the real filters see it."""
        low = "werkstudent it (w/m/d)"
        assert any(k in low for k in scrapers._WD_AI_KEYWORDS)

    def test_bonn_belt_workday_and_recruitee_configured(self):
        from config import WORKDAY_CXS_TENANTS, RECRUITEE_SLUGS
        assert ("debeka", "wd3", "Karriere") in WORKDAY_CXS_TENANTS
        assert "unu" in RECRUITEE_SLUGS

    def test_priorities_and_long_lived_registered(self):
        import main
        for src in ("GIZ", "LVR", "BaFin", "BARMER", "BundDE", "HDI", "NRWBANK", "StadtKoeln"):
            assert src in main._SOURCE_PRIORITY, src
            assert src in main._LONG_LIVED_SOURCES, src
