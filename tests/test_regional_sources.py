"""
Tests for the two sources added by the 2026-08-15 Bonn-region sweep:
Stellenwerk (the university boards of Bonn/H-BRS, Köln and Düsseldorf) and
the Fraunhofer + DLR research institutes.

Offline; fixtures mirror payloads verified live on 2026-08-15.
"""

import scrapers


STELLENWERK_HTML = """
<html><head>
<script type="application/ld+json">{
  "@context":"https://schema.org","@type":"JobPosting",
  "title":"Werkstudent (m/w/d) Development \\u2013 AI & Cloud",
  "description":"<p>Du arbeitest mit Python und Azure.</p>",
  "datePosted":"2026-08-11T15:00:39.507Z",
  "hiringOrganization":{"@type":"Organization","name":"synalis GmbH & Co. KG"},
  "jobLocation":{"@type":"Place","address":{"addressLocality":"Bonn-Rhein-Sieg"}}
}</script>
</head><body></body></html>
"""

RMK_HTML = """
<html><head>
<meta name="description" content="Sankt Augustin Studentische Hilfskr&auml;fte - Agentic AI (all genders), 53757" />
<meta property="og:title" content="Studentische Hilfskr&auml;fte - Agentic AI (all genders)" />
</head><body>
<div class="jobdescription">Das Fraunhofer IAIS in Sankt Augustin sucht Studierende.</div>
</body></html>
"""


class TestStellenwerk:
    def test_parses_jsonld_into_a_job(self, monkeypatch):
        class R:
            status_code = 200
            content = STELLENWERK_HTML.encode("utf-8")
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        out = scrapers._stellenwerk_page(
            "https://www.stellenwerk.de/bonn-rhein-sieg/werkstudent-x-260811-274000")
        assert len(out) == 1
        j = out[0]
        assert j["source"] == "Stellenwerk"
        assert j["company"] == "synalis GmbH & Co. KG"
        assert "Bonn-Rhein-Sieg" in j["location"]
        assert j["posted_at"].startswith("2026-08-11")

    def test_umlauts_survive_the_missing_charset_header(self, monkeypatch):
        """The server sends no charset, so requests would decode as
        ISO-8859-1 and turn Köln into KÃ¶ln — which also corrupts the
        company name used as the cross-source dedup key."""
        html = STELLENWERK_HTML.replace("synalis GmbH & Co. KG", "Kölner Träger gGmbH")

        class R:
            status_code = 200
            content = html.encode("utf-8")
            text = html.encode("utf-8").decode("iso-8859-1")   # the wrong guess
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        out = scrapers._stellenwerk_page("https://www.stellenwerk.de/koeln/x-260811-1")
        assert out[0]["company"] == "Kölner Träger gGmbH"

    def test_url_pattern_matches_only_the_three_cities(self):
        ok = "https://www.stellenwerk.de/bonn-rhein-sieg/werkstudent-data-260811-274000"
        other = "https://www.stellenwerk.de/hamburg/werkstudent-data-260811-274001"
        assert scrapers._STELLENWERK_URL_RE.search(ok)
        assert not scrapers._STELLENWERK_URL_RE.search(other)

    def test_dedup_key_is_the_trailing_id(self):
        """Roughly a quarter of postings are cross-listed under several
        cities; the trailing id is what makes them one job."""
        a = scrapers._STELLENWERK_URL_RE.search(
            "https://www.stellenwerk.de/koeln/werkstudent-data-260811-274000")
        b = scrapers._STELLENWERK_URL_RE.search(
            "https://www.stellenwerk.de/duesseldorf/werkstudent-data-260811-274000")
        assert a.group(2) == b.group(2) == "274000"

    def test_slug_screen_requires_student_and_tech(self):
        keep = "werkstudent-development-ai-cloud-260811-1"
        no_tech = "werkstudent-kellner-gastronomie-260811-2"
        no_student = "senior-data-engineer-260811-3"
        assert scrapers._STELLENWERK_STUDENT.search(keep) and scrapers._STELLENWERK_VOCAB.search(keep)
        assert not scrapers._STELLENWERK_VOCAB.search(no_tech)
        assert not scrapers._STELLENWERK_STUDENT.search(no_student)

    def test_robots_disallowed_feed_is_not_used(self):
        """stellenwerk.de/robots.txt disallows /jobs-feed even though that
        endpoint returns clean JSON. The sitemap path is the permitted one.

        Only executable lines are checked: the comment explaining WHY the feed
        is avoided naturally mentions it by name."""
        import inspect
        code = [l for l in inspect.getsource(scrapers).splitlines()
                if not l.strip().startswith("#")]
        assert not any("jobs-feed" in l for l in code), \
            "the robots-disallowed feed must not be called"


class TestResearchInstitutes:
    def test_parses_meta_tags_not_css_classes(self, monkeypatch):
        """.jobTitle on these pages resolves to the 'Jetzt bewerben' button,
        so og:title and the meta description are the trustworthy fields."""
        class R:
            status_code = 200
            text = RMK_HTML
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        out = scrapers._rmk_page(
            "https://jobs.fraunhofer.de/job/Sankt-Augustin-Studentische-"
            "Hilfskr%C3%A4fte-Agentic-AI-%28all-genders%29-53757/123/", "Fraunhofer")
        assert len(out) == 1
        j = out[0]
        assert "Agentic AI" in j["title"]
        assert j["company"] == "Fraunhofer"
        assert j["source"] == "Fraunhofer"
        assert "Sankt Augustin" in j["location"]
        assert "Fraunhofer IAIS" in j["description"]

    def test_city_is_read_by_name_not_by_splitting_on_dashes(self, monkeypatch):
        """Splitting the path produced locations like 'Wachtberg STUDENTISCHE'
        because the title follows the city with no separator."""
        class R:
            status_code = 200
            text = RMK_HTML
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        out = scrapers._rmk_page(
            "https://jobs.dlr.de/job/K%C3%B6ln-Studentische-Hilfskraft-"
            "Datenpflege-in-IT-Systemen-%28wmd%29/14259/", "DLR")
        assert out[0]["location"] == "Köln, Germany"

    def test_region_and_student_filters_run_on_the_url(self):
        near = ("https://jobs.fraunhofer.de/job/Sankt-Augustin-Studentische-"
                "Hilfskraft-KI/1/")
        far = "https://jobs.fraunhofer.de/job/Dresden-Studentische-Hilfskraft-KI/2/"
        senior = ("https://jobs.fraunhofer.de/job/Sankt-Augustin-Abteilungsleiter"
                  "-Institut/3/")
        assert scrapers._RMK_REGION.search(near) and scrapers._RMK_STUDENT.search(near)
        assert not scrapers._RMK_REGION.search(far)
        assert not scrapers._RMK_STUDENT.search(senior)

    def test_both_institutes_are_configured(self):
        hosts = [h for h, _ in scrapers._RMK_SITES]
        assert "https://jobs.fraunhofer.de" in hosts
        assert "https://jobs.dlr.de" in hosts


class TestWiring:
    def test_new_scrapers_run_in_scrape_all(self):
        import inspect
        src = inspect.getsource(scrapers.scrape_all)
        assert "scrape_stellenwerk" in src
        assert "scrape_research_institutes" in src

    def test_source_priorities_registered(self):
        import main
        for src in ("Stellenwerk", "Fraunhofer", "DLR"):
            assert src in main._SOURCE_PRIORITY, src

    def test_wachtberg_counts_as_commutable(self):
        """Fraunhofer FHR and FKIE are in Wachtberg, 15-25 min from Bonn."""
        import main
        assert main._is_commutable_or_remote(
            {"location": "Wachtberg, Germany", "description": ""})

    def test_regional_workday_tenants_present(self):
        tenants = {t for t, _s, _n in scrapers.WORKDAY_TENANTS}
        assert "debeka" in tenants and "cgm" in tenants

    def test_the_greenhouse_metro_trap_is_not_configured(self):
        """boards-api.greenhouse.io/v1/boards/metro returns 200 with real
        jobs — for Unity One, a security firm in Idaho, not METRO Düsseldorf."""
        from config import GREENHOUSE_SLUGS
        assert "metro" not in GREENHOUSE_SLUGS
