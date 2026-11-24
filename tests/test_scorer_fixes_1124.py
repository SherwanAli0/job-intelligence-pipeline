"""Scorer and location fixes after the check of 20 to 24 November 2026.

A read of every ad the pipeline judged or dropped on those days found good
internships marked down for reasons the ads do not give:

1. "very good German or English" and "good command of German or English" were
   read as German above B1 (an AI internship, a camera-vision internship).
2. "Remote, Anywhere" was dropped as "Germany not confirmed".
3. A "Working Student" title with the location "Remote" was dropped the same way.
4. Two strategy and operations roles were dropped as web analytics for "GTM"
   (go-to-market).
5. Two jobs whose ads read "Heidelberg (Germany) or San Francisco Bay Area
   (USA)", location "Heidelberg, GER", were dropped by the location filter.
6. The scorer invented a US work permit for an internship in Munich, capped two
   internships as theses although the ads also offer an internship, and held an
   AI Master's to be no computer science. Those are prompt rules, checked as text.

Offline, fictional ads only.
"""
import pytest

import evidence
import filters
import main
import scorer


@pytest.fixture
def b1(monkeypatch):
    monkeypatch.setitem(scorer.CANDIDATE_FACTS, "german_level", "B1 (B2 in progress)")


def _job(**kw):
    base = {"title": "Internship - Artificial Intelligence in Technical Development (all genders welcome)",
            "company": "Example Cars AG", "location": "Neckarsulm, Baden-Württemberg, Germany",
            "description": "Your tasks: build AI tools in Python. Your profile: enrolled student. "}
    base.update(kw)
    return base


# -- 1. German offered as an alternative to English --------------------------------
class TestGermanOrEnglish:
    @pytest.mark.parametrize("text", [
        "You have very good German or English skills, written and spoken.",
        "Good command of German or English is required.",
        "Fluent English or German.",
        "Sehr gute Deutsch- oder Englischkenntnisse.",
        "Sehr gute Englisch- oder Deutschkenntnisse in Wort und Schrift.",
        "Very good written and spoken German and/or English.",
        "German (C1) or English (C1).",
    ])
    def test_no_german_demand_when_english_is_enough(self, b1, text):
        assert scorer._hard_disqualify(_job(description="Python, PyTorch. " + text))[0] is False
        assert evidence.required_level(text, "german")[0] == 0, text

    @pytest.mark.parametrize("text", [
        "You have very good German and English skills.",
        "Fluent German is required; English is a plus.",
        "Sehr gute Deutsch- und Englischkenntnisse.",
        "Verhandlungssicheres Deutsch und gutes Englisch.",
        "Fluent German and good English, or French.",
        "You speak fluent German. Or English speakers with German at C1.",
        "Very good German or Spanish and English.",
        "Very good German and French or English.",
        "You speak fluent German\n\nor English speakers apply in German.",
        "You need fluent German to serve German or English-speaking customers.",
        "You need fluent German to serve German or English speaking customers.",
    ])
    def test_german_with_english_is_still_demanded(self, b1, text):
        dropped, _reason, category = scorer._hard_disqualify(_job(description="Python. " + text))
        assert (dropped, category) == (True, "german_required"), text

    def test_the_helper_reads_both_orders(self):
        low = "very good german or english skills"
        start = low.find("german")
        assert evidence.german_or_english(low, start, start + len("german"))
        low = "english or german"
        start = low.find("german")
        assert evidence.german_or_english(low, start, start + len("german"))
        low = "german and english"
        assert not evidence.german_or_english(low, 0, len("german"))


# -- 2. and 3. remote anywhere, working-student titles -----------------------------
class TestRemoteAnywhere:
    def _remote(self, **kw):
        fields = {"title": "Software Engineer - Secure Container Images", "location": "Remote, Anywhere",
                  "description": "Python, Bash and CI/CD. We are a distributed company."}
        return _job(**{**fields, **kw})

    def test_remote_anywhere_includes_germany(self):
        assert scorer._hard_disqualify(self._remote())[0] is False

    @pytest.mark.parametrize("loc", ["Remote, Worldwide", "Remote - Global", "Remote (globally)"])
    def test_other_words_for_the_whole_world(self, loc):
        assert scorer._hard_disqualify(self._remote(location=loc))[0] is False

    @pytest.mark.parametrize("loc", ["Remote, Anywhere in the US", "Remote - anywhere in the United States",
                                     "Remote, anywhere in Canada", "Remote, Anywhere, US",
                                     "Remote, Anywhere in Brazil", "Remote, anywhere in the continental US",
                                     "Remote - Global (Americas)"])
    def test_anywhere_in_another_country_is_not_germany(self, loc):
        dropped, _reason, category = scorer._hard_disqualify(self._remote(location=loc))
        assert (dropped, category) == (True, "location"), loc

    def test_anywhere_in_europe_is(self):
        assert scorer._hard_disqualify(self._remote(location="Remote, Anywhere in Europe"))[0] is False

    @pytest.mark.parametrize("text", ["US citizens only.", "Must have a US work permit.",
                                      "Eligible to work in the UK.", "You must be authorized to work in the U.S.",
                                      "Canadian citizenship required.", "Work authorization for the US is needed.",
                                      "Open to citizens of the United States.", "You must hold US permanent residency.",
                                      "US-based candidates only.", "Applicants must reside in the USA."])
    def test_a_working_student_title_does_not_beat_a_foreign_work_permit(self, text):
        j = _job(title="Working Student Data", location="Remote", description="Python and SQL. " + text)
        dropped, _reason, category = scorer._hard_disqualify(j)
        assert (dropped, category) == (True, "location"), text

    @pytest.mark.parametrize("text", [
        "You will work with us in a small team; join us.",
        "Please let us know your work permit status in your application.",
        "Tell us about your citizenship and your start date.",
        "Eligible to work in Germany or the EU.",
    ])
    def test_us_as_a_word_is_no_country(self, text):
        j = _job(title="Working Student Data", location="Remote", description="Python and SQL. " + text)
        assert scorer._hard_disqualify(j)[0] is False, text

    def test_a_working_student_title_does_not_place_anywhere_in_the_us_in_germany(self):
        j = _job(title="Working Student", location="Remote, Anywhere in the US", description="Python.")
        dropped, _reason, category = scorer._hard_disqualify(j)
        assert (dropped, category) == (True, "location")

    def test_a_working_student_title_places_a_remote_job_in_germany(self):
        j = _job(title="Working Student – Data Engineering & MLOps", location="Remote",
                 description="Build data pipelines with Python and Airflow.")
        assert scorer._hard_disqualify(j)[0] is False

    def test_a_working_student_title_does_not_beat_a_foreign_country_in_the_ad(self):
        # golden case g15: a US-only working-student job
        j = _job(title="Working Student Machine Learning", location="Remote",
                 description="Remote working student role. Must be authorized to work in the United States "
                             "and enrolled at a US university. PyTorch, English.")
        dropped, _reason, category = scorer._hard_disqualify(j)
        assert (dropped, category) == (True, "location")

    def test_a_remote_job_with_no_sign_of_germany_still_goes(self):
        j = _job(title="Backend Engineer", location="Remote", description="Build APIs with Python.")
        dropped, _reason, category = scorer._hard_disqualify(j)
        assert (dropped, category) == (True, "location")


# -- 4. GTM ------------------------------------------------------------------------
class TestGoToMarket:
    def test_gtm_planning_is_not_web_analytics(self):
        j = _job(title="Strategy & Operations Intern", location="Munich, Germany",
                 description="Support GTM planning and go-to-market strategy with data and dashboards.")
        assert scorer._hard_disqualify(j)[0] is False

    def test_google_tag_manager_still_is(self):
        j = _job(title="Web Analytics Intern", location="Munich, Germany",
                 description="Implement tracking with Google Tag Manager and GA4 for our shop.")
        dropped, _reason, category = scorer._hard_disqualify(j)
        assert (dropped, category) == (True, "web_analytics")


# -- 5. the location filter --------------------------------------------------------
class TestLocationFilter:
    def test_a_german_place_with_a_us_office_in_the_ad_is_kept(self):
        j = {"title": "Machine Learning Engineer", "location": "Heidelberg, GER",
             "description": "Turba Labs · Heidelberg (Germany) or San Francisco Bay Area (USA) · "
                            "On-site, hybrid or remote · Candidates must live in Germany or the USA"}
        assert main._is_attendable_from_germany(j)

    def test_ger_is_germany(self):
        assert filters.is_german_location("heidelberg, ger")
        assert filters.is_german_location("Mannheim, GER")
        assert not filters.is_foreign_location("heidelberg, ger")

    def test_ger_inside_a_word_is_nothing(self):
        # Gerlingen is German by the town list, not by "ger"; "Geraldton, Australia" stays foreign
        assert filters.is_foreign_location("geraldton, australia")

    @pytest.mark.parametrize("loc", ["Austin, TX, GER", "New York, GER", "Ger, Spain", "GER, London"])
    def test_ger_counts_only_as_the_last_part_after_nothing_foreign(self, loc):
        assert not filters.is_german_location(loc), loc

    @pytest.mark.parametrize("loc", ["Remote, Anywhere", "Worldwide", "Global"])
    def test_the_whole_world_helper(self, loc):
        assert filters.whole_world_location(loc)

    @pytest.mark.parametrize("loc", ["Anywhere, US", "Global, Remote (US only)", "Anywhere in Mexico",
                                     "Worldwide (APAC)", "Global sales hub in Austin", "Anywhere in the States",
                                     "Anywhere, EST timezone"])
    def test_the_whole_world_with_a_country_beside_it_is_not(self, loc):
        assert not filters.whole_world_location(loc), loc

    def test_the_whole_world_includes_germany(self):
        j = {"title": "ML Engineer", "location": "Anywhere in the World",
             "description": "Our offices in New York and São Paulo work with people everywhere."}
        assert main._is_attendable_from_germany(j)

    def test_a_us_city_in_the_location_still_drops(self):
        # The ad names Germany, but the place of work in the location is New York.
        j = {"title": "Data Scientist", "location": "New York, NY",
             "description": "We have offices in Germany and the US. This role is in New York."}
        assert not main._is_attendable_from_germany(j)

    def test_a_us_city_only_in_the_ad_without_germany_still_drops(self):
        j = {"title": "Data Scientist", "location": "Somewhere",
             "description": "The role is based in San Francisco, on site five days a week."}
        assert not main._is_attendable_from_germany(j)


# -- 6. prompt rules ---------------------------------------------------------------
class TestPromptRules:
    @pytest.fixture
    def prompt(self):
        return scorer._system_prompt()

    def test_a_work_permit_cap_needs_the_ad_to_say_so(self, prompt):
        assert "Apply this ONLY when the AD ITSELF says so" in prompt
        assert "never infer one from the employer's nationality" in prompt

    def test_an_internship_or_thesis_ad_is_an_internship(self, prompt):
        assert "an ad that offers an internship OR a thesis" in prompt
        assert "Only an ad that offers nothing but a thesis gets" in prompt

    def test_a_computer_science_field_includes_him(self, prompt):
        assert "studies are in the computer-science family" in prompt
        assert "never cap or penalise him for his field of study then" in prompt

    def test_the_prompt_still_formats(self, prompt):
        assert "{" not in prompt.split("OUTPUT FORMAT")[0].replace("{{", "")
