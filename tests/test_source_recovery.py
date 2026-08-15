"""
Regression tests for two sources that failed SILENTLY in production.

1. Arbeitsagentur returned zero for five days (2026-08-10 to 08-15) without
   any alarm. The search API moved to pc/v6 and renamed every field; the old
   v4 URL now answers 403. A version bump alone would not have been enough,
   because parsing "stellenangebote"/"titel"/"arbeitgeber" against a v6 body
   yields an empty list rather than an error.

2. The LinkedIn guest scraper spent its whole per-IP page budget on the first
   query, so a 429 on query 2 of 7 meant five queries never ran.

Both are offline: fixtures mirror the live payloads verified on 2026-08-15.
"""

import scrapers


# Trimmed from a real pc/v6 response for "Werkstudent Informatik" near Bonn.
BA_V6_PAGE = {
    "ergebnisliste": [
        {
            "stellenangebotsart": "PRAKTIKUM_TRAINEE",
            "stellenangebotsTitel": "Werkstudent DevClient-Management (m/w/d)",
            "stellenlokationen": [{
                "adresse": {"strasse": "Karl-Legien-Str. 192", "plz": "53117",
                            "ort": "Bonn", "region": "NORDRHEIN_WESTFALEN",
                            "land": "DEUTSCHLAND"}
            }],
            "veroeffentlichungszeitraum": {"von": "2026-08-10"},
            "datumErsteVeroeffentlichung": "2026-04-15",
            "aenderungsdatum": "2026-08-10T07:07:16.572",
            "firma": "BWI GmbH",
            "referenznummer": "13509-00002110056002-S",
            "entfernung": 6,
        },
        {   # must be dropped: the API matches loosely and returns non-tech roles
            "stellenangebotsTitel": "Werkstudent/-in (m/w/d) Rechtsanwaltskanzlei",
            "stellenlokationen": [{"adresse": {"ort": "Köln",
                                               "region": "NORDRHEIN_WESTFALEN"}}],
            "veroeffentlichungszeitraum": {"von": "2026-08-11"},
            "firma": "Ghendler Ruvinskij",
            "referenznummer": "99999-00000000000001-S",
            "entfernung": 28,
        },
    ],
    "maxErgebnisse": 21,
    "page": 1,
    "size": 100,
}


class TestArbeitsagenturV6:
    def _patch(self, monkeypatch, payload=BA_V6_PAGE):
        class R:
            status_code = 200
            headers = {"content-type": "application/json"}
            def json(self):
                return payload
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_ba_enrich", lambda ref: {"description": "Voll text"})
        monkeypatch.setattr(scrapers, "ARBEITSAGENTUR_QUERIES", ["Werkstudent Informatik"])
        monkeypatch.setattr(scrapers, "ARBEITSAGENTUR_REMOTE_QUERIES", [])
        monkeypatch.setattr(scrapers, "_BA_MAX_PAGES", 1)

    def test_parses_the_v6_field_names(self, monkeypatch):
        self._patch(monkeypatch)
        out = scrapers.scrape_arbeitsagentur()
        assert len(out) == 1, "the law-firm row should have been screened out"
        j = out[0]
        assert j["title"] == "Werkstudent DevClient-Management (m/w/d)"
        assert j["company"] == "BWI GmbH"
        assert j["source"] == "Arbeitsagentur"
        assert "Bonn" in j["location"]
        assert j["url"].endswith("13509-00002110056002-S")

    def test_posted_at_uses_this_listing_not_first_ever_publication(self, monkeypatch):
        """datumErsteVeroeffentlichung is 2026-04-15 for this ad; using it
        would make every re-posted job look four months stale and the 24h
        freshness cap would delete the source all over again."""
        self._patch(monkeypatch)
        assert scrapers.scrape_arbeitsagentur()[0]["posted_at"] == "2026-08-10"

    def test_distance_from_bonn_is_surfaced(self, monkeypatch):
        self._patch(monkeypatch)
        assert "6 km von Bonn" in scrapers.scrape_arbeitsagentur()[0]["description"]

    def test_search_url_is_v6_and_details_url_is_v4(self):
        """The split is real and counter-intuitive: search 403s on v4, details
        403 on v6. Pinning both stops a well-meaning 'consistency' fix."""
        assert "/pc/v6/jobs" in scrapers._BA_SEARCH_URL
        assert "/pc/v4/jobdetails" in scrapers._BA_DETAIL_URL

    def test_irrelevant_titles_never_cost_a_detail_request(self, monkeypatch):
        calls = []
        self._patch(monkeypatch)
        monkeypatch.setattr(scrapers, "_ba_enrich",
                            lambda ref: calls.append(ref) or {"description": "x"})
        scrapers.scrape_arbeitsagentur()
        assert len(calls) == 1, "only the technical role should be enriched"


class TestLinkedInGuestBreadthFirst:
    class _Resp:
        status_code = 200
        def __init__(self, n=25):
            self.text = "".join(
                f'<li><a class="base-card__full-link" '
                f'href="https://de.linkedin.com/jobs/view/job-{i}">a</a>'
                f'<span class="base-search-card__title">Werkstudent {i}</span></li>'
                for i in range(n)
            )

    def _run(self, monkeypatch, throttle_after):
        calls = []

        def fake_get(url, params=None, **kw):
            calls.append((params["keywords"], params["start"] // 25))
            if len(calls) > throttle_after:
                r = self._Resp()
                r.status_code = 429
                return r
            return self._Resp()

        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions", lambda jobs: None)
        scrapers.scrape_linkedin_guest()
        return calls

    def test_page_one_is_swept_across_queries_before_any_page_two(self, monkeypatch):
        calls = self._run(monkeypatch, throttle_after=999)
        n_queries = len(scrapers._LI_GUEST_QUERIES)
        first = calls[:n_queries]
        assert all(page == 0 for _q, page in first), \
            "the first pass must be page 1 of every query, not 10 pages of one"
        assert len({q for q, _ in first}) == n_queries

    def test_an_early_throttle_no_longer_starves_later_queries(self, monkeypatch):
        """The production failure: 429 on query 2 left five queries unrun."""
        calls = self._run(monkeypatch, throttle_after=4)
        assert len({q for q, _ in calls}) >= 4, \
            "a throttle must cost depth, not whole queries"

    def test_every_query_runs_when_not_throttled(self, monkeypatch):
        calls = self._run(monkeypatch, throttle_after=999)
        assert {q for q, _ in calls} == set(scrapers._LI_GUEST_QUERIES)
