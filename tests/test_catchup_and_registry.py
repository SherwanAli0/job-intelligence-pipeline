"""The Sunday 2026-08-16 catch-up window, the absent-source registry, and the
final batch of regional sources (igus, REWE, d.vinci, Uni Bonn, CSB).

The registry exists because THREE sources died silently in one week, and the
median-based alarm was structurally unable to see any of them: it judges
against a rolling window, so a source dead for longer than the window — or
dead since before a state migration, as Arbeitsagentur was — has a median of
zero and is never even eligible for its own alarm.
"""

from datetime import date, datetime, timezone

import config
import main
import scrapers


# ── Catch-up window ──────────────────────────────────────────────────────────

class TestCatchupWindow:
    def test_sunday_is_a_seven_day_sweep(self):
        assert config.max_posting_age_hours(date(2026, 8, 16)) == 168

    def test_monday_reverts_to_24h_without_a_deploy(self):
        assert config.max_posting_age_hours(date(2026, 8, 17)) == 24

    def test_every_later_day_is_24h(self):
        assert config.max_posting_age_hours(date(2026, 9, 1)) == 24

    def test_freshness_filter_honours_the_window(self, monkeypatch):
        from datetime import timedelta
        j = {"title": "Werkstudent Data", "company": "X", "location": "Bonn",
             "url": "u", "source": "linkedin", "description": "d",
             "posted_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()}
        monkeypatch.setattr(config, "max_posting_age_hours", lambda today=None: 168)
        assert main._is_fresh_enough(j)
        monkeypatch.setattr(config, "max_posting_age_hours", lambda today=None: 24)
        assert not main._is_fresh_enough(j)


# ── Absent-source registry ───────────────────────────────────────────────────

class TestAbsentSourceRegistry:
    NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)

    def _reg(self):
        return {
            "Arbeitsagentur": {"typical": 56,
                               "last_nonzero": "2026-08-10T08:00:00+00:00"},
            "linkedin": {"typical": 300,
                         "last_nonzero": "2026-08-16T01:00:00+00:00"},
            "TinySource": {"typical": 2,
                           "last_nonzero": "2026-08-01T00:00:00+00:00"},
        }

    def test_long_dead_source_is_flagged(self):
        """The Arbeitsagentur scenario: dead since before the history window
        begins. The registry remembers; the median check could not."""
        w = main._absent_source_warnings_from(self._reg(), {"linkedin": 250}, self.NOW)
        assert len(w) == 1 and "Arbeitsagentur" in w[0]

    def test_recovered_source_is_not_flagged(self):
        w = main._absent_source_warnings_from(
            self._reg(), {"linkedin": 250, "Arbeitsagentur": 44}, self.NOW)
        assert w == []

    def test_recent_silence_within_grace_is_not_flagged(self):
        reg = {"XING": {"typical": 100,
                        "last_nonzero": "2026-08-16T01:00:00+00:00"}}
        assert main._absent_source_warnings_from(reg, {}, self.NOW) == []

    def test_small_sources_never_alarm(self):
        """A source that typically delivers 2 jobs being at 0 is noise."""
        w = main._absent_source_warnings_from(self._reg(),
                                              {"linkedin": 1, "Arbeitsagentur": 1},
                                              self.NOW)
        assert w == []

    def test_registry_update_is_wired_into_persist(self):
        import inspect
        assert "_update_source_registry" in inspect.getsource(main.node_persist)


# ── Final regional sources ───────────────────────────────────────────────────

class TestIgus:
    def test_data_envelope_is_unwrapped(self, monkeypatch):
        """The API returns {"data": [...]}; iterating the dict itself yields
        zero jobs with no error — the silent-zero class again, caught live."""
        payload = {"data": [{
            "id": "abc-123", "title": "Werkstudent (m/w/d) AI & Workflow Automation",
            "slug": "werkstudent-ai", "location": "Köln",
            "introduction": "<p>igus intro</p>", "tasks": "<ul><li>Python</li></ul>",
            "profile": "<p>Enrolled student</p>", "offer": "<p>Flexible</p>",
            "createdAt": "2026-08-01",
        }, {
            "id": "def-456", "title": "Senior Engineer", "slug": "senior",
            "location": "Köln",
        }]}

        class R:
            status_code = 200
            @staticmethod
            def json():
                return payload
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        out = scrapers.scrape_igus()
        assert len(out) == 1
        j = out[0]
        assert j["source"] == "Igus"
        assert "Python" in j["description"]
        assert j["url"] == "https://karriere.igus.de/offer/werkstudent-ai/abc-123"


class TestReweGroup:
    def test_terms_are_deduped_and_urls_https(self, monkeypatch):
        item = {"id": "976345-de_DE",
                "title": "Werkstudent Business Insights (m/w/d)",
                "location": "Köln, NW, DE, 50933",
                "created_at": "2026-08-14T02:00:00+02:00",
                "url": "http://jobs.rewe-group.com/jobs/rewe/976345",
                "details": "SQL und Python."}

        class R:
            status_code = 200
            @staticmethod
            def json():
                return [item]          # same item for both terms
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        out = scrapers.scrape_rewe()
        assert len(out) == 1, "same id from both terms must not duplicate"
        assert out[0]["url"].startswith("https://")


class TestDvinci:
    def test_student_titles_only_and_publication_url(self, monkeypatch):
        rows = [
            {"position": "Praktikant / Werkstudent (m/w/d) Controlling",
             "subtitle": "Köln", "pageDescription": "Data Quality team",
             "jobPublicationURL": "https://generali-gruppe.dvinci-hr.com/de/jobs/1"},
            {"position": "(Senior) Risk Engineer (m/w/d)",
             "subtitle": "Köln", "pageDescription": "x",
             "jobPublicationURL": "https://generali-gruppe.dvinci-hr.com/de/jobs/2"},
        ]

        class R:
            status_code = 200
            headers = {"content-type": "application/json;charset=UTF-8"}
            @staticmethod
            def json():
                return rows
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        out = scrapers.scrape_dvinci()
        # 4 tenants x 1 student row each (same fixture served to all)
        assert len(out) == 4
        assert all("erkstudent" in j["title"] or "raktik" in j["title"] for j in out)

    def test_non_json_response_is_survivable(self, monkeypatch):
        """d.vinci serves HTML to browser-like UAs; if that ever happens to
        the plain UA too, the scraper must report and continue, not crash."""
        class R:
            status_code = 200
            headers = {"content-type": "text/html;charset=UTF-8"}
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        assert scrapers.scrape_dvinci() == []


class TestUniBonn:
    HTML = """
    <html><body>
      <a href="/sitewide/pdf/shk-data-analytics.pdf">Student Assistant (SHK/WHF)
        (m/w/d), International Office, iStart (Data Analytics)</a>
      <a href="/nav">kurz</a>
      <a href="https://ext.example.com/x.pdf">Studentische Hilfskraft (SHK/WHF)
        (m/w/d), Healthy Campus (10 Std./Wo.)</a>
    </body></html>
    """

    def test_pdf_anchors_become_jobs(self, monkeypatch):
        class R:
            status_code = 200
            text = self_html = None
        R.text = self.HTML
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        out = scrapers.scrape_unibonn()
        assert len(out) == 2
        assert out[0]["company"] == "Universität Bonn"
        assert out[0]["url"].startswith("https://www.uni-bonn.de/")
        assert out[1]["url"].startswith("https://ext.example.com/")
        assert all(main._is_student_role(j) for j in out)


class TestCsbRegionFilter:
    def test_only_commute_belt_hrefs_are_fetched(self, monkeypatch):
        html = """
        <html><body>
          <a class="jobTitle-link" href="/job/K%C3%B6ln-Werkstudent-Steuern-NW">K</a>
          <a class="jobTitle-link" href="/job/Hamburg-Werkstudent-Data-HH">H</a>
          <a class="jobTitle-link" href="/job/K%C3%B6ln-Werkstudent-Steuern-NW">dup</a>
        </body></html>
        """

        class R:
            status_code = 200
            text = html
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: R())
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        picked_urls = []

        def fake_collect(urls, fn, label):
            picked_urls.extend(urls)
            return []
        monkeypatch.setattr(scrapers, "_parallel_collect", fake_collect)
        scrapers.scrape_csb()
        assert picked_urls, "nothing was picked at all"
        assert all("K%C3%B6ln" in u for u in picked_urls), picked_urls
        # one Köln href per site (deduped), three sites configured
        assert len(picked_urls) == len(scrapers._CSB_SITES)


class TestWiringAndFreshness:
    def test_all_five_run_in_scrape_all(self):
        import inspect
        src = inspect.getsource(scrapers.scrape_all)
        for name in ("scrape_igus", "scrape_rewe", "scrape_dvinci",
                     "scrape_unibonn", "scrape_csb"):
            assert name in src, name

    def test_priorities_registered(self):
        for src in ("Igus", "REWE", "UniBonn", "Zurich", "Bayer", "DEUTZ",
                    "Generali", "RheinEnergie"):
            assert src in main._SOURCE_PRIORITY, src

    def test_employer_own_boards_are_freshness_exempt(self):
        for src in ("Igus", "REWE", "UniBonn", "Zurich", "DEUTZ", "Generali"):
            assert src in main._LONG_LIVED_SOURCES, src
