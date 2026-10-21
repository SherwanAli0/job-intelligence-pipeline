"""Regression tests for the review of the full-time change (2026-10-15).

Every test here pins something the first version of the change got wrong, or
something no earlier test would have noticed (a mutation that survived)."""
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import application_kit
import config
import main
import scorer
import scrapers

ROOT = Path(__file__).resolve().parent.parent

ENGLISH = ("We are looking for a motivated data scientist to join our international team. You will "
           "build machine learning models in Python, work with SQL and cloud data platforms and "
           "present results to stakeholders. Our working language is English. Hybrid work: two days "
           "per week in the office and the rest from home.")


def _j(title="Junior Data Scientist (m/f/d)", desc=ENGLISH, **kw):
    base = {"id": kw.pop("id", "t"), "title": title, "company": "Acme", "location": "Berlin, Germany",
            "url": "https://example.org/job/1", "source": "Greenhouse", "description": desc,
            "posted_at": datetime.now(timezone.utc).isoformat()}
    base.update(kw)
    return base


class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self._p, self.status_code, self.text = payload, status, text

    def json(self):
        return self._p


# ── the backspace bug ────────────────────────────────────────────────────────
def test_no_source_file_contains_a_control_character():
    """The first version of the change wrote \\b inside a non-raw string: eight
    literal backspace characters ended up in a regex and silently disabled it."""
    bad = {}
    for p in [*ROOT.glob("*.py"), *ROOT.glob("tests/*.py")]:
        text = p.read_text(encoding="utf-8")
        n = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r")
        if n:
            bad[p.name] = n
    assert not bad, bad


@pytest.mark.parametrize("title", [
    "Intern - Master's Thesis Machine Learning (m/f/d)", "AI Intern / Bachelor Thesis",
    "HiWi Data Science - Master Thesis possible", "SHK Informatik - Abschlussarbeit",
    "WHK Data Analytics (Bachelorarbeit moeglich)", "Interns wanted: Master Thesis Data",
    "Machine Learning Intern (PhD)", "Research Intern, PhD", "2027 Summer Intern, PhD, Machine Learning",
])
def test_every_student_work_marker_rescues_a_thesis_or_phd_title(title):
    assert main._is_eligible_form(_j(title)), title


# ── what is NOT a job ────────────────────────────────────────────────────────
@pytest.mark.parametrize("title", [
    "Auszubildende Fachinformatikerin", "Azubi Fachinformatiker", "Dual Studium Informatik",
    "Abschlussarbeit Informatik", "Promotionsstelle Informatik", "Promotionsstudent (m/w/d) KI",
    "Professur (W2) Kuenstliche Intelligenz", "Werkvertrag IT-Berater",
    "Dualer Student Informatik (m/w/d)", "Duale Studentin Wirtschaftsinformatik", "Dualer Studiengang Informatik",
    "Dual Study Program Computer Science", "Dual Degree Programme Informatics", "Bachelor of Science Informatik (dual)",
    "Verbundstudium Informatik", "Masterthesis KI", "Bachelorthesis Informatik", "Diplomarbeit Informatik",
    "Studienarbeit Maschinelles Lernen", "Juniorprofessur Data Science", "Doctoral Researcher in Machine Learning",
    "Post-Doc Position", "Promotion im Bereich Data Science", "Apprentice Software Developer (m/f/d)",
    "Berufsausbildung Fachinformatiker", "Doctoral Candidate (m/f/d) Data Science", "Freelance Data Scientist",
])
def test_non_jobs_are_not_eligible(title):
    assert not main._is_eligible_form(_j(title)), title


@pytest.mark.parametrize("title", [
    "Speech Synthesis Engineer", "Program Synthesis Researcher", "Data Scientist (m/f/d)",
    "Junior Python Developer", "Machine Learning Engineer, Promotion Ranking",
])
def test_real_job_titles_are_not_rejected_by_the_widened_gate(title):
    assert main._is_eligible_form(_j(title)), title


@pytest.mark.parametrize("title", [
    "Gehalt im Bereich Artificial Intelligence 2026 – jobvector",
    "Intro to Large Language Models Course (LLMs)—Enroll Now – 365 Data Science", "Bootcamp Data Science",
    "Scholarship in Data Science", "Summer School Machine Learning 2027", "Master Data Science Studiengang",
])
def test_web_search_pages_that_are_not_postings_are_dropped(title):
    assert not main._is_eligible_form(_j(title, source="BraveSearch")), title
    assert not main._is_eligible_form(_j(title, source="WebSearch")), title


@pytest.mark.parametrize("title", [
    "Part-time Python Developer, Online Courses Platform", "Part-time Data Analyst - Salary Benchmarking",
    "Teilzeit Entwickler Studiengang-Portal", "Student Data Analyst - Online Courses",
    "Student (m/w/d) Data Analytics Kurs-Plattform",
])
def test_the_same_words_in_a_real_ats_posting_are_fine(title):
    assert main._is_eligible_form(_j(title, source="Greenhouse")), title


def test_dual_and_doctoral_students_are_not_labelled_students():
    for t in ("Dualer Student Informatik", "Doctoral Student Machine Learning", "PhD Student AI"):
        assert not main._is_eligible_form(_j(t))


# ── the full-time title gate ─────────────────────────────────────────────────
def _ft(title, **kw):
    j = _j(title, **kw)
    j["_form"] = main._job_form(j)
    return j


@pytest.mark.parametrize("title", [
    "Data Engineer", "Junior Data Engineer", "Datenanalyst (m/w/d)", "ML Engineer", "AI Engineer",
    "KI Entwickler (m/w/d)", "Cloud Engineer", "Informatiker (m/w/d)", "Full Stack Developer", "NLP Engineer",
    "Computer Vision Engineer", "Prompt Engineer", "Algorithm Engineer", "Agentic Systems Engineer",
    "Reinforcement Learning Engineer", "Applied Researcher", "Site Reliability Engineer",
    "Wissenschaftlicher Mitarbeiter (m/w/d) Maschinelles Lernen", "Recommender Systems Engineer",
    "Sales Data Analyst", "Marketing Data Scientist", "Product Analyst",
])
def test_tech_titles_pass_the_full_time_title_gate(title):
    assert main._fulltime_title_ok(_ft(title)), title


@pytest.mark.parametrize("title", [
    "Account Executive", "Sales Engineer", "Mechanical Engineer", "Data Entry Clerk",
    "Account Manager Data Products", "Software Sales Executive", "Financial Analyst", "Credit Analyst",
    "IT-Administrator (m/w/d)", "Datenschutzbeauftragter (m/w/d)", "Data Protection Officer",
    "Quantity Surveyor", "Data Center Technician", "HR Analyst", "Insurance Agent", "Learning & Development Manager",
])
def test_non_tech_titles_fail_the_full_time_title_gate(title):
    assert not main._fulltime_title_ok(_ft(title)), title


def test_web_search_hits_need_a_posting_url_to_count_as_full_time():
    junk = _ft("Junior Data Scientist guide", source="BraveSearch", url="https://www.jobvector.de/gehalt/ai/")
    root = _ft("Remote AI & Machine Learning Jobs", source="BraveSearch", url="https://airemotelyjobs.com/")
    real = _ft("Junior Data Scientist", source="BraveSearch", url="https://zollsoft.de/jobs/data/junior-1")
    career = _ft("Junior Data Scientist", source="WebSearch", url="https://karriere.example.de/p/123")
    other = _ft("Junior Data Scientist", source="Greenhouse", url="https://boards.greenhouse.io/x/123")
    assert not main._fulltime_title_ok(junk) and not main._fulltime_title_ok(root)
    assert main._fulltime_title_ok(real) and main._fulltime_title_ok(career) and main._fulltime_title_ok(other)


def test_student_rows_from_web_search_keep_their_old_gate():
    j = _j("Werkstudent Data Science", source="BraveSearch", url="https://www.stellenwerk.de/bonn")
    j["_form"] = main._job_form(j)
    assert j["_form"] == "student" and main._fulltime_title_ok(j)


def test_a_body_stated_internship_is_labelled_internship():
    j = _j("Data Analytics Support", ENGLISH + " This is a mandatory internship of 6 months (Pflichtpraktikum).")
    assert main._job_form(j) == "internship"
    j2 = _j("Data Analytics Support", ENGLISH + " This is a Werkstudent position, 20 hours per week.")
    assert main._job_form(j2) == "student"


def test_title_gate_runs_before_the_body_fetch(monkeypatch):
    fetched = []
    monkeypatch.setattr(main, "load_digested", lambda: {})
    monkeypatch.setattr(main, "_ever_shown_keys", lambda: set())
    monkeypatch.setattr(main, "_fill_missing_bodies", lambda js: fetched.extend(j["id"] for j in js) or [])
    monkeypatch.setattr(main, "_skill_radar", lambda js: None)
    monkeypatch.setattr(main, "_body_retry_ids", lambda: set())
    monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
    main.node_filter({"seen": {}, "all_jobs": [_j("Account Executive", id="sales"),
                                                _j("Junior Data Scientist", id="ds"),
                                                _j("Werkstudent Marketing", id="ws")]})
    assert "sales" not in fetched, "a non-tech full-time row must not cost a body fetch (300/run cap)"
    assert "ds" in fetched and "ws" in fetched


# ── Master's filter: a Bachelor alternative is not a Master's requirement ────
@pytest.mark.parametrize("phrase", [
    "Bachelor's degree or Master's degree in Computer Science", "BSc or MSc in Computer Science or related field",
    "B.Sc./M.Sc. in Computer Science", "Bachelor's, Master's or PhD in a quantitative field",
    "University degree (BSc, MSc) in a related field", "a degree: bachelor's, master's or phd in computer science",
])
def test_a_bachelor_alternative_keeps_the_job(phrase):
    assert main._no_masters_required({"title": "Junior Data Scientist", "description": f"We want you. {phrase}. Python."})


@pytest.mark.parametrize("phrase", [
    "Master's degree in Statistics required", "MSc in Computer Science required", "PhD in Machine Learning required",
])
def test_a_real_masters_requirement_still_drops_the_job(phrase):
    assert not main._no_masters_required({"title": "Data Scientist", "description": f"Requirements: {phrase}. Python."})


# ── seniority qualifiers are whole words ─────────────────────────────────────
@pytest.mark.parametrize("title", [
    "Senior Data Scientist - International", "Lead Data Engineer (Internal Platform)",
    "Principal ML Engineer, International Markets", "Staff Software Engineer - Reentry Systems",
    "Director of Data Science, Associate Programs", "Associate Director, Data Science",
])
def test_a_substring_is_not_a_junior_qualifier(title):
    assert not main._not_fulltime_senior({"title": title, "description": "Python"}), title
    disq, _w, cat = scorer._hard_disqualify({"title": title, "description": "Python data role.",
                                             "location": "Remote Germany", "company": "x"})
    assert disq and cat == "senior_title", title


@pytest.mark.parametrize("title", ["Senior Data Scientist (Junior welcome)", "Junior Senior Data Analyst",
                                   "Associate Data Scientist", "Data Scientist - Graduate Programme",
                                   "Senior ML Engineer Intern", "Senior Python Developer (Entry Level)"])
def test_real_qualifiers_still_rescue_a_title(title):
    assert main._not_fulltime_senior({"title": title, "description": "Python"}), title


def test_international_in_the_opening_of_a_senior_ad_does_not_rescue_it():
    body = "Senior Machine Learning Engineer\nJoin our international team of researchers."
    assert not main._not_fulltime_senior({"title": "Machine Learning Engineer", "description": body})
    body2 = "Senior Machine Learning Engineer\nGraduates welcome."
    assert main._not_fulltime_senior({"title": "Machine Learning Engineer", "description": body2})


# ── scorer prompt: no leftovers of the old rule ──────────────────────────────
class TestPromptHasNoLeftoversOfTheStudentOnlyRule:
    prompt = scorer._system_prompt(config.CV_PROFILE_DS)

    def test_the_old_intro_and_examples_are_gone(self):
        assert "Full-time (Vollzeit) permanent roles" not in self.prompt
        assert "Capped at 15: full-time Junior" not in self.prompt
        assert "since 2026-10-14" in self.prompt.split("CANDIDATE PROFILE")[0]

    def test_the_top_band_and_tier_one_name_full_time(self):
        line = [l for l in self.prompt.splitlines() if l.startswith("- 85-100")][0]
        assert "entry-level full-time" in line
        assert '"Junior ML Engineer"' in self.prompt

    def test_off_target_and_tiers_are_not_student_only(self):
        assert "A role of ANY form with NO data/AI/software content" in self.prompt
        assert "A student role with NO data/AI/software content" not in self.prompt
        assert "FULL-TIME role that is" in self.prompt and "helpdesk" in self.prompt
        assert "in any of the four forms" in self.prompt.split("TIER 2")[1].split("OFF-TARGET")[0]

    def test_profiles_name_all_forms(self):
        for p in (config.CV_PROFILE_DS, config.CV_PROFILE_ML, config.CV_PROFILE_AI):
            assert "entry-level full-time" in p.split("Profile:")[0]

    def test_golden_has_a_full_time_junior_high_case_and_a_senior_control(self):
        import json
        rows = [json.loads(l) for l in (ROOT / "golden" / "golden_set.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        by = {r["id"]: r for r in rows}
        assert by["g11"]["expect_band"] == "high"
        assert by["g21"].get("expect_dq") and by["g21"]["dq_category"] == "senior_title"
        assert len(by) == len(rows), "golden ids must be unique"


# ── scrapers: query lists, caps ──────────────────────────────────────────────
def test_every_source_has_all_its_full_time_queries():
    for q in ("junior", "graduate", "entry level", "berufseinsteiger", "data scientist", "machine learning", "ai engineer"):
        assert q in scrapers._SR_QUERIES, q
    assert len(scrapers.ARBEITSAGENTUR_FULLTIME_QUERIES) >= 8
    assert scrapers._BA_ENRICH_CAP >= 350
    for q in ("Junior Data Scientist", "Junior Data Analyst", "Junior Machine Learning Engineer",
              "Junior Softwareentwickler Python", "Berufseinsteiger Data", "Data Engineer"):
        assert q in scrapers._XING_QUERIES, q
    ft = [q for q in config.SEARCH_QUERIES
          if not re.search(r"werkstudent|working student|student|praktik|intern|teilzeit|part-time", q.lower())]
    assert len(ft) >= 20


def _ba_item(ref, title, ho):
    d = {"referenznummer": ref, "stellenangebotsTitel": title, "firma": "Acme GmbH",
         "stellenlokationen": [{"adresse": {"ort": "Hamburg", "region": "HAMBURG", "land": "DEUTSCHLAND"}}],
         "veroeffentlichungszeitraum": {"von": "2026-10-14"}}
    if ho is not None:
        d["homeofficemoeglich"] = ho
    return d


def test_germany_wide_pass_skips_only_items_that_say_no_home_office(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        # only the Germany-wide pass (no wo, no arbeitszeit) sees these rows
        if params.get("was") == "Data Scientist" and "wo" not in params and "arbeitszeit" not in params:
            return _Resp({"ergebnisliste": [_ba_item("1", "Data Scientist (m/w/d)", True),
                                            _ba_item("2", "Data Scientist Pricing (m/w/d)", False),
                                            _ba_item("3", "Junior Data Scientist (m/w/d)", None)]})
        return _Resp({"ergebnisliste": []})
    monkeypatch.setattr(scrapers.requests, "get", fake_get)
    monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
    monkeypatch.setattr(scrapers, "_ba_enrich", lambda ref: {"description": "Python and SQL."})
    rows = {r["url"].rsplit("/", 1)[-1]: r for r in scrapers.scrape_arbeitsagentur()}
    assert set(rows) == {"1", "3"}, "False = skipped; True and missing = kept"
    assert rows["1"]["description"].startswith("[Homeoffice möglich]")
    assert not rows["3"]["description"].startswith("[Homeoffice möglich]")


def test_absolventa_takes_the_newest_postings_first(monkeypatch):
    urls = [f"https://www.absolventa.de/stellenangebote/{i}-werkstudent-data-analyst" for i in range(1000, 1400)]
    xml = "".join(f"<loc>{u}</loc>" for u in urls)
    monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _Resp(None, 200, xml))
    got = []
    monkeypatch.setattr(scrapers, "_absolventa_page", lambda u: got.append(u) or [])
    scrapers.scrape_absolventa()
    ids = sorted(int(re.search(r"/(\d+)-", u).group(1)) for u in got)
    assert len(ids) == scrapers._ABSOLVENTA_CAP and ids[-1] == 1399
    assert ids[0] == 1400 - scrapers._ABSOLVENTA_CAP, "the cap must keep the NEWEST ids"


def test_xing_pages_are_read_through_their_json_ld(monkeypatch):
    calls = []
    monkeypatch.setattr(scrapers, "_xing_fetch_description", lambda u: calls.append(u) or "The real ad text.")
    monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("generic fetch")))
    assert scrapers._fetch_full_description("https://www.xing.com/jobs/berlin-ai-1234") == "The real ad text."
    assert calls == ["https://www.xing.com/jobs/berlin-ai-1234"]


def test_xing_falls_back_to_the_generic_fetcher_when_json_ld_is_empty(monkeypatch):
    monkeypatch.setattr(scrapers, "_xing_fetch_description", lambda u: "")
    monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _Resp(None, 404))
    assert scrapers._fetch_full_description("https://www.xing.com/jobs/berlin-ai-1234") == ""


# ── Workday: two passes inside a time budget ─────────────────────────────────
class TestWorkdayTwoPasses:
    def _jp(self, title, i=0, loc="Germany, Munich"):
        return {"title": title, "locationsText": loc, "externalPath": f"/job/x/{i}", "postedOn": "Posted Today"}

    def _setup(self, monkeypatch, answer):
        calls, details = [], []

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append((json["searchText"], json["offset"], bool(json["appliedFacets"])))
            if json["appliedFacets"]:
                return _Resp({}, 400)           # NVIDIA-style: facet rejected
            return _Resp({"jobPostings": answer(json["searchText"], json["offset"])}, 200)
        monkeypatch.setattr(scrapers.requests, "post", fake_post)
        monkeypatch.setattr(scrapers, "_wd_fetch_description", lambda *a: details.append(a) or ENGLISH)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        return calls, details

    def _answer(self, search, offset):
        return [self._jp(f"Working Student Data {search} {offset}-{i}", i + offset) for i in range(20)] if offset < 40 else []

    def test_the_plans_are_ordered_and_leave_room_under_the_scraper_timeout(self):
        assert scrapers._wd_plan(1000.0, 400.0)["base"] == (1320.0, 1380.0)
        assert scrapers._wd_ft_plan(1000.0, 200.0)["fulltime"] == (1140.0, 1200.0)
        assert scrapers._WD_BUDGET_SECONDS <= 0.85 * scrapers.SCRAPER_TIMEOUT_SECONDS
        assert scrapers._WD_FT_BUDGET_SECONDS <= 0.85 * scrapers.SCRAPER_TIMEOUT_SECONDS

    def test_the_base_pass_never_runs_a_full_time_search(self, monkeypatch):
        calls, _ = self._setup(monkeypatch, self._answer)
        scrapers._workday_cxs_tenant(("nvidia", "wd5", "Site"), None, "base")
        searched = {c[0] for c in calls}
        assert searched == {*scrapers._WD_STUDENT_SEARCHES, ""}

    def test_the_full_time_pass_runs_only_the_full_time_searches(self, monkeypatch):
        calls, _ = self._setup(monkeypatch, self._answer)
        scrapers._workday_cxs_tenant(("nvidia", "wd5", "Site"), None, "fulltime")
        assert {c[0] for c in calls} == set(scrapers._WD_FULLTIME_SEARCHES)

    def test_a_tenant_with_an_explicit_search_has_no_full_time_pass(self, monkeypatch):
        calls, _ = self._setup(monkeypatch, self._answer)
        assert scrapers._workday_cxs_tenant(("stryker", "wd1", "S", "working student"), None, "fulltime") == []
        assert calls == []

    def test_the_budget_split_keeps_ten_slots_for_full_time(self, monkeypatch):
        self._setup(monkeypatch, self._answer)
        base = scrapers._workday_cxs_tenant(("nvidia", "wd5", "Site"), None, "base")
        assert len(base) == scrapers._WD_KEEP_PER_TENANT - scrapers._WD_KEEP_FULLTIME
        ft = scrapers._workday_cxs_tenant(("nvidia", "wd5", "Site"), None, "fulltime")
        assert len(ft) == scrapers._WD_KEEP_FULLTIME

    def test_full_time_pass_skips_what_the_base_pass_already_returned(self, monkeypatch):
        def answer(search, offset):
            return [self._jp("Junior Data Scientist", 1), self._jp("Data Scientist", 2)] if (search == "junior" and offset == 0) else []
        self._setup(monkeypatch, answer)
        known = {"https://nvidia.wd5.myworkdayjobs.com/job/x/1"}
        out = scrapers._workday_cxs_tenant(("nvidia", "wd5", "Site"), None, "fulltime", known)
        assert [r["title"] for r in out] == ["Data Scientist"]

    def test_full_time_candidates_rank_tech_titles_before_junior_non_tech(self, monkeypatch):
        def answer(search, offset):
            if search == "junior" and offset == 0:
                return [self._jp("Junior Accountant", 1), self._jp("Graduate Program - Finance", 2),
                        self._jp("Data Scientist", 3), self._jp("Junior Data Scientist", 4)]
            return []
        self._setup(monkeypatch, answer)
        monkeypatch.setattr(scrapers, "_WD_KEEP_FULLTIME", 2)
        out = scrapers._workday_cxs_tenant(("nvidia", "wd5", "Site"), None, "fulltime")
        assert [r["title"] for r in out] == ["Junior Data Scientist", "Data Scientist"]

    def test_junior_titles_take_detail_slots_after_student_ones_and_before_others(self, monkeypatch):
        def answer(search, offset):
            if offset:
                return []
            return {"working student": [self._jp("Working Student AI", 1)],
                    "": [self._jp("Senior Machine Learning Engineer", 2), self._jp("Principal Data Scientist", 3)],
                    "junior": [self._jp("Junior Data Scientist", 4)]}.get(search, [])
        self._setup(monkeypatch, answer)
        monkeypatch.setattr(scrapers, "_WD_KEEP_PER_TENANT", 2)
        out = scrapers._workday_cxs_tenant(("nvidia", "wd5", "Site"))
        assert [r["title"] for r in out] == ["Working Student AI", "Junior Data Scientist"]

    def test_no_list_request_after_the_list_deadline_and_the_skips_are_counted(self, monkeypatch):
        calls, _ = self._setup(monkeypatch, self._answer)
        before = scrapers._WD_STATS["searches_skipped"]
        now = scrapers.time.time()
        out = scrapers._workday_cxs_tenant(("nvidia", "wd5", "Site"), (now - 1, now + 999), "base")
        assert calls == [] and out == []
        assert scrapers._WD_STATS["searches_skipped"] - before == len(scrapers._WD_STUDENT_SEARCHES) + 1

    def test_no_detail_fetch_after_the_detail_deadline(self, monkeypatch):
        calls, details = self._setup(monkeypatch, self._answer)
        before = scrapers._WD_STATS["details_skipped"]
        now = scrapers.time.time()
        out = scrapers._workday_cxs_tenant(("nvidia", "wd5", "Site"), (now + 999, now - 1), "base")
        assert calls and details == [] and out == []
        assert scrapers._WD_STATS["details_skipped"] - before > 0

    def _tenants(self, monkeypatch, record):
        monkeypatch.setattr(scrapers, "SCRAPE_WORKERS", 1)
        monkeypatch.setattr(scrapers, "WORKDAY_CXS_TENANTS", [("a", "wd1", "S"), ("b", "wd1", "S", "working student")])
        monkeypatch.setattr(scrapers, "_workday_cxs_tenant", record)

    def test_the_base_scraper_runs_explicit_search_tenants_first_with_the_base_deadlines(self, monkeypatch):
        seen = []
        monkeypatch.setattr(scrapers, "_wd_plan", lambda *a, **k: {"base": (1.0, 2.0)})
        self._tenants(monkeypatch, lambda e, d=None, mode="all", known=frozenset():
                      seen.append((e[0], mode, d)) or [{"url": f"https://{e[0]}/job/1"}])
        scrapers.scrape_workday_cxs()
        assert seen == [("b", "base", (1.0, 2.0)), ("a", "base", (1.0, 2.0))]
        assert scrapers._WD_BASE_URLS == {"https://a/job/1", "https://b/job/1"}

    def test_the_full_time_scraper_has_its_own_deadlines_and_skips_what_the_base_found(self, monkeypatch):
        seen = []
        monkeypatch.setattr(scrapers, "_wd_ft_plan", lambda *a, **k: {"fulltime": (3.0, 4.0)})
        monkeypatch.setattr(scrapers, "_WD_BASE_URLS", {"https://a/job/1"})
        self._tenants(monkeypatch, lambda e, d=None, mode="all", known=frozenset():
                      seen.append((e[0], mode, d, set(known))) or [])
        scrapers.scrape_workday_cxs_fulltime()
        assert seen == [("b", "fulltime", (3.0, 4.0), {"https://a/job/1"}),
                        ("a", "fulltime", (3.0, 4.0), {"https://a/job/1"})]

    def test_both_workday_scrapers_are_registered_base_first(self):
        import inspect
        src = inspect.getsource(scrapers.scrape_all)
        assert "scrape_workday_cxs," in src and "scrape_workday_cxs_fulltime," in src
        assert src.index("scrape_workday_cxs,") < src.index("scrape_workday_cxs_fulltime,")

    def test_an_empty_budget_returns_at_once_without_exception(self, monkeypatch):
        calls, _ = self._setup(monkeypatch, self._answer)
        monkeypatch.setattr(scrapers, "WORKDAY_CXS_TENANTS", [("a", "wd1", "S"), ("b", "wd1", "S")])
        monkeypatch.setattr(scrapers, "_WD_BUDGET_SECONDS", 0)
        monkeypatch.setattr(scrapers, "_WD_FT_BUDGET_SECONDS", 0)
        assert scrapers.scrape_workday_cxs() == [] and scrapers.scrape_workday_cxs_fulltime() == [] and calls == []

    def test_the_two_scrapers_return_different_rows(self, monkeypatch):
        self._setup(monkeypatch, self._answer)
        monkeypatch.setattr(scrapers, "WORKDAY_CXS_TENANTS", [("a", "wd1", "S"), ("b", "wd1", "S")])
        base = scrapers.scrape_workday_cxs()
        ft = scrapers.scrape_workday_cxs_fulltime()
        assert base and ft
        assert not ({r["url"] for r in base} & {r["url"] for r in ft}), "the full-time pass must skip the base rows"


# ── application kit: no Werkstudent answers for full-time jobs ───────────────
class TestApplicationKitAndFullTime:
    def _run(self, monkeypatch, facts):
        asked = []
        monkeypatch.setattr(application_kit, "_load_facts", lambda: facts)
        monkeypatch.setattr(application_kit, "_greenhouse_questions", lambda url: asked.append(url) or [])
        monkeypatch.setattr(application_kit, "load_bank", lambda: {})
        jobs = [dict(_j("Working Student Data", id="s", url="https://boards.greenhouse.io/acme/jobs/111"), _form="student"),
                dict(_j("Junior Data Scientist", id="f", url="https://boards.greenhouse.io/acme/jobs/222"), _form="full-time")]
        application_kit.enrich_with_kits(jobs)
        return asked

    def test_full_time_jobs_get_no_kit_until_the_fact_sheet_says_so(self, monkeypatch):
        assert self._run(monkeypatch, "TARGET: Werkstudent roles only.") == ["https://boards.greenhouse.io/acme/jobs/111"]

    def test_the_marker_line_switches_it_on(self, monkeypatch):
        asked = self._run(monkeypatch, "TARGET: all forms.\nFULLTIME_KIT: yes\n")
        assert len(asked) == 2
