"""Years that describe the employer are not a requirement, and the LinkedIn
guest lane reads every card of a results list.

Both were found on 17 November 2026 through one ad: a working-student ad that
the pipeline had received and dropped, because its opening sentence speaks of
the employer's "more than 180 years of experience in the fashion industry".
"""
import pytest
from hypothesis import given, settings, strategies as st

import config
import main
import scrapers

ENGLISH = (
    "We are an international team and our working language is English. "
    "In this role the student works with data, builds dashboards and supports the analytics team. "
    "Enrolment at a university and an interest in Python and SQL are what the role needs. "
)


def _j(title="Working Student (m/f/d) Digital Adoption", description="", **kw):
    base = {"id": "x", "title": title, "company": "Acme",
            "location": "Düsseldorf, North Rhine-Westphalia, Germany",
            "url": "https://example.com/jobs/1", "source": "linkedin", "posted_at": "",
            "description": description}
    base.update(kw)
    return base


# -- the experience filter -----------------------------------------------------
class TestEmployerHistoryIsNoRequirement:
    @pytest.mark.parametrize("sentence", [
        # the sentence of the ad, as it stands in every ad of that employer
        "We are building on more than 180 years of experience in the fashion industry "
        "and continuously modernize the company.",
        "Building on more than 180 years of experience, we are continuously modernizing.",
        "With over 30 years of experience, Acme is one of the market leaders.",
        "Our team has 10+ years of experience in building data products.",
        "We have 8 years of experience in e-commerce and serve two million customers.",
        "Founded in 1995, we bring 25+ years of experience to every project.",
        "Wir blicken auf 100+ Jahre Erfahrung zurück.",
        "Dank unserer langjährigen Erfahrung im Maschinenbau sind wir Marktführer.",
        "Wir sind ein Team mit mehrjähriger Erfahrung in der Softwareentwicklung.",
        # the employer names itself only after the number
        "With over 10 years of experience in logistics, our team delivers reliably.",
        "With 12 years of experience in fintech, we know the market.",
    ])
    def test_the_employer_speaking_of_itself_drops_nothing(self, sentence):
        j = _j(description=f"{ENGLISH}{sentence} {ENGLISH}")
        assert main._no_experience_overload(j), sentence
        assert "_drop_note" not in j

    @pytest.mark.parametrize("sentence", [
        "You have 5+ years of experience in data engineering.",
        "We are looking for someone with 5 years of experience in data engineering.",
        "We expect at least 4 years of experience with Python.",
        "Minimum 3 years of experience required.",
        "5 years of professional experience in software development.",
        "Our ideal candidate has 6 years of experience.",
        "We need 4+ years of experience in backend development.",
        "12 years of experience in software architecture.",
        "Du bringst mehrjährige Berufserfahrung mit.",
        "Wir erwarten mehrjährige Berufserfahrung in der Datenanalyse.",
        "Wir suchen Verstärkung mit langjähriger Erfahrung.",
        # the employer speaks, and asks: "we" alone does not make it history
        "Wir wünschen uns 3 Jahre Berufserfahrung im Controlling.",
        "Wir wünschen uns mehrjährige Erfahrung in Python.",
        "Wir setzen 4 Jahre Berufserfahrung voraus.",
        "We want 4 years of experience in Java.",
        "We are a startup and want a developer with 5 years of experience.",
        "We value 3 years of hands-on experience in cloud.",
        "Our team wants 3+ years of experience in React.",
        "Our client wants 5 years of experience in SAP.",
        "We offer flexible hours - 3 years of experience in Python required.",
        "We give 3 years experience on SAP required.",
        "With 5 years of experience, you will lead the data team.",
        # a large number is still a requirement when the sentence says so
        "Minimum 15 years of experience in sales.",
        "You have 20 years of experience in consulting.",
        "Wir erwarten 15+ Jahre Berufserfahrung.",
        # the third person with a small number is not recognised as history
        "Acme has 12 years of experience in the market.",
    ])
    def test_a_requirement_still_drops(self, sentence):
        assert not main._no_experience_overload(_j(description=f"{ENGLISH}{sentence} {ENGLISH}")), sentence

    @pytest.mark.parametrize("sentence,category", [
        ("With over 30 years of experience, we are one of the market leaders.", ""),
        ("We are building on more than 80 years of experience in the fashion industry.", ""),
        ("Our team has 10+ years of experience in building data products.", ""),
        ("You have 5+ years of experience in data engineering.", "experience"),
        ("We want 4 years of experience in Java.", "experience"),
        ("At least 15 years of experience in sales.", "experience"),
    ])
    def test_the_check_before_the_model_reads_it_the_same_way(self, sentence, category):
        import scorer
        j = _j(description=f"{ENGLISH}{sentence} {ENGLISH}")
        hit, _reason, found = scorer._hard_disqualify(j)
        assert (found == "experience") is (category == "experience"), (sentence, found)

    def test_the_ad_that_was_dropped_now_passes(self):
        j = _j(description=(
            "About the job. Acme offers quality fashion at affordable prices. We are building on more "
            "than 180 years of experience in the fashion industry and continuously modernize the company. "
            "Building on more than 180 years of experience, we are modernizing to become a data- and "
            "AI-driven retailer. As a working student you test AI tools against business scenarios. "
            "Hands-on experience with generative AI tools is an advantage. Basic experience with Python "
            "is an advantage; advanced software engineering experience is not required."))
        assert main._no_experience_overload(j)

    def test_history_and_a_real_requirement_in_one_ad_still_drops(self):
        j = _j(description=(
            "We are building on more than 180 years of experience in the fashion industry. "
            "Your profile: at least 4 years of experience in data engineering."))
        assert not main._no_experience_overload(j)
        assert "at least 4 years" in j["_drop_note"]

    def test_fifteen_years_and_more_are_left_to_the_scorer(self):
        assert main._no_experience_overload(_j(description="15 years of experience in retail analytics."))
        assert not main._no_experience_overload(_j(description="14 years of experience in retail analytics."))

    def test_the_words_before_an_earlier_sentence_do_not_count(self):
        # "we" belongs to the sentence before; the requirement stands on its own
        j = _j(description="We offer flexible hours. 5 years of experience in data engineering.")
        assert not main._no_experience_overload(j)

    def test_the_words_of_a_drop_are_noted(self):
        j = _j(description=f"{ENGLISH}You have 5+ years of experience in data engineering. {ENGLISH}")
        assert not main._no_experience_overload(j)
        assert "5+ years" in j["_drop_note"] and len(j["_drop_note"]) < 200


class TestGraduateTitles:
    @pytest.mark.parametrize("title", [
        "Global AI & Digital Postgraduate Program (m/f/d)", "Post-Graduate Programme Data",
        "Traineeprogramm Data Science", "Praktikantin Data Analytics", "Graduates in Software Engineering",
        "Junior Data Analyst", "Entry-Level Data Engineer", "Data Science Intern", "Absolventen (m/w/d) IT",
    ])
    def test_titles_written_for_beginners_skip_the_experience_filter(self, title):
        assert main._is_graduate_designed(title)
        assert main._no_experience_overload(_j(title, "You have 5+ years of experience."))

    @pytest.mark.parametrize("title", [
        "International Sales Manager", "Internal Tools Engineer", "Data Engineer",
        "Working Student Data Science", "Viewer Analytics Engineer",
    ])
    def test_other_titles_do_not(self, title):
        assert not main._is_graduate_designed(title)


class TestTheAuditLineSaysWhy:
    @staticmethod
    def _run(monkeypatch, jobs):
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "_ever_shown_keys", lambda: set())
        monkeypatch.setattr(main, "_fill_missing_bodies", lambda js: [])
        monkeypatch.setattr(main, "_skill_radar", lambda js: None)
        monkeypatch.setattr(main, "_body_retry_ids", lambda: set())
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
        out = main.node_filter({"seen": {}, "all_jobs": jobs})
        return {j["id"] for j in out["new_jobs"]}, {r["id"]: r for r in main._RUN_AUDIT}

    def test_an_experience_drop_carries_the_words_it_tripped_on(self, monkeypatch):
        wanted = _j("Working Student Data Analytics", id="keep",
                    description=f"{ENGLISH}We are building on more than 180 years of experience. {ENGLISH}")
        asks = _j("Working Student Data Engineering", id="asks",
                  description=f"{ENGLISH}You have 5+ years of experience in data engineering. {ENGLISH}")
        sales = _j("Account Executive", id="sales", description=ENGLISH * 2)
        kept, audit = self._run(monkeypatch, [wanted, asks, sales])
        assert "keep" in kept and "asks" not in kept
        assert audit["asks"]["filter"] == "ExperienceFilter"
        assert "5+ years of experience" in audit["asks"]["why"]
        assert "why" not in audit["sales"], "a title rule leaves no note"
        assert "_drop_note" not in wanted and "_drop_note" not in asks


_WORDS = ["we", "our", "you", "wir", "unsere", "5", "12", "180", "years", "year", "of", "experience",
          "+", ".", ":", "\n", "minimum", "at least", "mehrjährige", "langjährigen", "erfahrung",
          "jahre", "berufserfahrung", "building on", "more than", "ideally"]


class TestNeverRaises:
    @settings(max_examples=300, deadline=None)
    @given(st.lists(st.sampled_from(_WORDS), max_size=40), st.text(max_size=40))
    def test_any_mix_of_the_trigger_words_gives_a_verdict(self, words, title):
        verdict = main._no_experience_overload({"title": title, "description": " ".join(words)})
        assert isinstance(verdict, bool)

    @settings(max_examples=200, deadline=None)
    @given(st.text(max_size=300), st.text(max_size=60))
    def test_any_text_gives_a_verdict(self, description, title):
        assert isinstance(main._no_experience_overload({"title": title, "description": description}), bool)

    @settings(max_examples=200, deadline=None)
    @given(st.text(max_size=200), st.integers(0, 220), st.integers(0, 220), st.none() | st.integers(0, 400))
    def test_the_history_test_takes_any_position(self, text, start, end, years):
        assert isinstance(main._employer_history(text, min(start, end), max(start, end), years), bool)


# -- the LinkedIn guest lane ---------------------------------------------------
class TestLinkedInGuestReadsEveryCard:
    """One request returns 10 cards. Asking for start = 0, 25, 50 read the
    cards 1-10, 26-35, 51-60 and never the ones between."""

    @staticmethod
    def _run(monkeypatch, total, per_page=10):
        calls = []
        queries = list(scrapers._LI_GUEST_QUERIES)

        class Resp:
            status_code = 200

            def __init__(self, query, start):
                number = queries.index(query)
                self.text = "".join(
                    f'<li><a class="base-card__full-link" '
                    f'href="https://de.linkedin.com/jobs/view/werkstudent-{1000000 + number * 1000 + i}?pos={i}">a</a>'
                    f'<span class="base-search-card__title">Werkstudent {number}-{i}</span></li>'
                    for i in range(start, min(start + per_page, total))
                )

        def fake_get(url, params=None, **kw):
            calls.append((params["keywords"], params["start"]))
            return Resp(params["keywords"], params["start"])

        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions", lambda jobs: None)
        return scrapers.scrape_linkedin_guest(), calls, queries

    def test_no_card_between_the_pages_is_skipped(self, monkeypatch):
        jobs, calls, queries = self._run(monkeypatch, total=60)
        assert len(jobs) == 60 * len(queries)
        first = queries[0]
        assert [start for q, start in calls if q == first] == [0, 10, 20, 30, 40, 50, 60]
        numbers = sorted(int(j["url"].rsplit("/", 1)[1]) - 1000000 for j in jobs
                         if j["title"].startswith("Werkstudent 0-"))
        assert numbers == list(range(60)), "every card of the first query, in one unbroken run"

    def test_a_short_last_page_is_still_read(self, monkeypatch):
        jobs, calls, queries = self._run(monkeypatch, total=23)
        assert len(jobs) == 23 * len(queries)
        assert [start for q, start in calls if q == queries[0]] == [0, 10, 20]

    def test_the_request_budget_per_query_is_unchanged(self, monkeypatch):
        jobs, calls, queries = self._run(monkeypatch, total=500)
        per_query = [start for q, start in calls if q == queries[0]]
        assert len(per_query) == scrapers._LI_GUEST_PAGES
        assert per_query == [10 * n for n in range(scrapers._LI_GUEST_PAGES)]

    def test_a_page_of_twenty_five_moves_on_by_twenty_five(self, monkeypatch):
        jobs, calls, queries = self._run(monkeypatch, total=60, per_page=25)
        assert [start for q, start in calls if q == queries[0]] == [0, 25, 50, 60]
        assert len(jobs) == 60 * len(queries)

    def test_page_one_of_every_query_still_comes_first(self, monkeypatch):
        jobs, calls, queries = self._run(monkeypatch, total=60)
        assert [q for q, _ in calls[:len(queries)]] == queries
        assert all(start == 0 for _, start in calls[:len(queries)])
