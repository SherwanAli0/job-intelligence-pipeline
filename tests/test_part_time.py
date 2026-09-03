"""Part-time (Teilzeit) regular employment became an acceptable form on
2026-09-03 ("add the part time jobs"). Two properties must hold: a tech gate
keeps the retail/care/admin Teilzeit flood away from the scorer, and the
language exemption stays student-only."""

import inspect

import main
import scrapers
from config import SEARCH_QUERIES


def _j(title, desc="", loc="Köln, Germany"):
    return {"id": "t", "title": title, "company": "Acme", "location": loc,
            "url": "u", "source": "s", "description": desc, "posted_at": ""}


class TestPartTimeIsAnEligibleForm:
    def test_teilzeit_data_analyst_is_eligible(self):
        assert main._is_eligible_form(_j("Data Analyst (m/w/d) in Teilzeit",
                                         "SQL und Python, 20 Stunden pro Woche."))

    def test_english_part_time_developer_is_eligible(self):
        assert main._is_eligible_form(_j("Part-time Software Developer",
                                         "Python backend, 20 hours per week."))

    def test_teilzeit_in_body_only_still_counts(self):
        assert main._is_eligible_form(_j("Softwareentwickler (m/w/d)",
                                         "Die Stelle ist in Teilzeit zu besetzen."))

    def test_full_time_role_is_not_eligible(self):
        assert not main._is_eligible_form(_j("Data Analyst (m/w/d)",
                                             "Vollzeit, 40 Stunden pro Woche."))

    def test_part_time_NON_tech_is_gated_out(self):
        """Retail and care ads say Teilzeit constantly; without the tech gate
        the scorer would be paid to reject them all."""
        assert not main._is_eligible_form(_j("Verkäufer (m/w/d) Teilzeit",
                                             "Kassieren und Regale auffüllen."))

    def test_werkstudent_keeps_its_gate_free_path(self):
        """Student roles never needed a tech keyword and still do not."""
        assert main._is_eligible_form(_j("Werkstudent Marketing", "Support the team."))


class TestPartTimeLanguageStaysStrict:
    _GERMAN = ("Für unser Team suchen wir eine Entwicklerin oder einen Entwickler "
               "in Teilzeit. Du arbeitest mit Python und SQL und entwickelst "
               "Auswertungen. Wir bieten flexible Arbeitszeiten und ein junges Team "
               "mit kurzen Wegen und viel Gestaltungsspielraum für dich.")

    def test_german_part_time_ad_is_dropped(self):
        """No exemption: a German ad for a regular job signals a German-speaking
        workplace far more strongly than an enrolment-driven student ad does."""
        assert not main._is_english_friendly(
            _j("Softwareentwickler (m/w/d) Teilzeit", self._GERMAN))

    def test_german_werkstudent_ad_is_still_exempt(self):
        assert main._is_english_friendly(
            _j("Werkstudent Softwareentwicklung (m/w/d)",
               self._GERMAN.replace("in Teilzeit", "als Werkstudent")))


class TestPartTimeWiring:
    def test_arbeitsagentur_runs_a_teilzeit_pass(self):
        src = inspect.getsource(scrapers.scrape_arbeitsagentur)
        assert '"arbeitszeit": "tz"' in src
        assert len(scrapers.ARBEITSAGENTUR_PARTTIME_QUERIES) >= 5

    def test_jobspy_queries_include_teilzeit(self):
        assert any("teilzeit" in q.lower() for q in SEARCH_QUERIES)
        assert any("part-time" in q.lower() for q in SEARCH_QUERIES)

    def test_filter_chain_uses_the_form_gate(self):
        src = inspect.getsource(main.node_filter)
        assert "_is_eligible_form" in src
