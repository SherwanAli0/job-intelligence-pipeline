"""Owner, 2026-10-14: "we will add the full time jobs as well to our pool in nrw
or remote germany or hybrid germany".

Full-time roles join the pool. The location rule is unchanged (on-site only in
NRW or the Bonn belt, hybrid or remote anywhere in Germany), English-only is
unchanged, and the experience / senior-title / completed-Master's filters still
apply, so what reaches the scorer is entry-level and junior. Thesis, Ausbildung
/ dual study, PhD, professorships and freelance work stay out. All offline."""

import inspect
from datetime import datetime, timezone

import pytest

import config
import main
import notifier
import scorer
import scrapers

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


class TestFullTimeIsInThePool:
    @pytest.mark.parametrize("title", [
        "Junior Data Scientist (m/f/d)", "Software Engineer (m/f/d)", "Data Engineer",
        "Machine Learning Engineer", "Data Analyst (m/w/d)", "Business Intelligence Analyst",
        "Graduate AI Engineer", "Trainee Data Science",
    ])
    def test_regular_full_time_roles_are_eligible_and_labelled(self, title):
        j = _j(title)
        assert main._is_eligible_form(j), title
        assert main._job_form(j) == "full-time"

    def test_the_other_forms_keep_their_labels(self):
        assert main._job_form(_j("Working Student Data")) == "student"
        assert main._job_form(_j("Praktikum Data Science")) == "internship"
        assert main._job_form(_j("Internship Machine Learning")) == "internship"
        assert main._job_form(_j("Data Analyst (m/w/d) in Teilzeit")) == "part-time"

    @pytest.mark.parametrize("title", [
        "Ausbildung zum Fachinformatiker (m/w/d)", "Duales Studium Informatik", "Dual Student Data Science",
        "Master Thesis Machine Learning", "Bachelorarbeit Data Science", "Masterarbeit KI",
        "PhD Position in Machine Learning", "Doktorand (m/w/d) Data Science", "Postdoc Computer Vision",
        "Professor für Informatik", "Freelance Data Scientist", "Freiberuflicher Entwickler (m/w/d)",
    ])
    def test_thesis_ausbildung_phd_professor_and_freelance_stay_out(self, title):
        assert not main._is_eligible_form(_j(title)), title

    def test_a_working_student_role_that_also_offers_a_thesis_stays_in(self):
        assert main._is_eligible_form(_j("Working Student AI Engineer / Bachelor's or Master's Thesis"))

    @pytest.mark.parametrize("title,ok", [
        ("Junior Data Analyst", True), ("Software Engineer", True), ("Machine Learning Engineer", True),
        ("Backend Developer (Python)", True), ("BI Analyst", True), ("Research Engineer", True),
        ("Account Executive", False), ("Mechanical Engineer", False), ("Sales Manager DACH", False),
        ("Customer Success Manager", False), ("Office Manager", False), ("Recruiter", False),
    ])
    def test_full_time_rows_need_tech_work_in_the_title(self, title, ok):
        j = _j(title)
        j["_form"] = main._job_form(j)
        assert main._fulltime_title_ok(j) is ok, title

    def test_student_rows_skip_the_title_gate(self):
        j = _j("Werkstudent Marketing")
        j["_form"] = main._job_form(j)
        assert main._fulltime_title_ok(j)


class TestFullTimeThroughTheFilterChain:
    """The location, language, seniority and degree rules still decide."""

    @staticmethod
    def _run(monkeypatch, jobs):
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "_ever_shown_keys", lambda: set())
        monkeypatch.setattr(main, "_fill_missing_bodies", lambda js: [])
        monkeypatch.setattr(main, "_skill_radar", lambda js: None)
        monkeypatch.setattr(main, "_body_retry_ids", lambda: set())
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
        return {j["id"] for j in main.node_filter({"seen": {}, "all_jobs": jobs})["new_jobs"]}

    def test_the_rules_apply_to_full_time_exactly_as_to_the_other_forms(self, monkeypatch):
        jobs = [
            _j("Junior Data Scientist (m/f/d)", id="hybrid-berlin"),                       # hybrid anywhere: in
            _j("Junior Data Scientist (m/f/d)", id="remote", location="Remote, Germany",
               desc=ENGLISH.replace("Hybrid work: two days per week in the office and the rest from home.",
                                    "This role is 100% remote within Germany.")),          # remote: in
            _j("Junior Data Scientist (m/f/d)", id="onsite-koeln", location="Köln, Germany",
               desc=ENGLISH.replace("Hybrid work: two days per week in the office and the rest from home.",
                                    "You work on site in our Cologne office.")),            # on-site NRW: in
            _j("Junior Data Scientist (m/f/d)", id="onsite-munich", location="Munich, Germany",
               desc=ENGLISH.replace("Hybrid work: two days per week in the office and the rest from home.",
                                    "You work on site in our Munich office.")),             # on-site elsewhere: out
            _j("Junior Data Scientist (m/f/d)", id="german", location="Köln, Germany",
               desc="Wir suchen eine Data Scientistin für unser Team in Köln. Du entwickelst Modelle in "
                    "Python und SQL und präsentierst die Ergebnisse. Wir bieten flexible Arbeitszeiten "
                    "und ein junges Team mit kurzen Wegen und viel Gestaltungsspielraum für dich."),
            _j("Senior Data Scientist (m/f/d)", id="senior", location="Köln, Germany"),     # senior title: out
            _j("Data Scientist (m/f/d)", id="years", location="Köln, Germany",
               desc=ENGLISH + " You bring at least 5 years of professional experience."),   # experience: out
            _j("Machine Learning Engineer", id="masters", location="Köln, Germany",
               desc=ENGLISH + " A completed Master's degree in computer science is required."),
            _j("Account Executive", id="sales", location="Köln, Germany"),                   # not tech: out
            _j("Master Thesis Machine Learning", id="thesis", location="Köln, Germany"),     # thesis: out
        ]
        kept = self._run(monkeypatch, jobs)
        assert "hybrid-berlin" in kept and "remote" in kept and "onsite-koeln" in kept
        assert not ({"onsite-munich", "german", "senior", "years", "masters", "sales", "thesis"} & kept), kept

    def test_the_form_is_stamped_on_every_survivor(self, monkeypatch):
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "_ever_shown_keys", lambda: set())
        monkeypatch.setattr(main, "_fill_missing_bodies", lambda js: [])
        monkeypatch.setattr(main, "_skill_radar", lambda js: None)
        monkeypatch.setattr(main, "_body_retry_ids", lambda: set())
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
        out = main.node_filter({"seen": {}, "all_jobs": [_j(id="a"), _j("Working Student Data", id="b")]})
        forms = {j["id"]: j["_form"] for j in out["new_jobs"]}
        assert forms == {"a": "full-time", "b": "student"}

    def test_filter_label_keeps_its_prefix_and_order(self):
        src = inspect.getsource(main.node_filter)
        assert "Employment-form filter" in src and "Full-time title filter" in src
        assert src.index("Employment-form filter") < src.index("_fulltime_title_ok") < src.index("_is_tech_relevant")


class TestScorerKnowsFullTimeIsAcceptable:
    prompt = scorer._system_prompt("profile")

    def test_full_time_is_no_longer_the_wrong_form(self):
        cap = self.prompt.split("CAP AT 15 if the role is the WRONG employment form:")[1].split("CAP AT 20")[0]
        assert "regular" in cap and "full-time" in cap.lower()
        assert "Ausbildung" in cap and "thesis" in cap.lower() and "PhD" in cap
        assert "Full-time (Vollzeit) regular employment, Ausbildung" not in cap

    def test_the_old_anchor_that_scored_a_junior_full_time_role_20_is_gone(self):
        assert "Junior Data Scientist (full-time), Berlin" not in self.prompt
        assert "ANCHOR 20" in self.prompt and "5+ years" in self.prompt.split("ANCHOR 20")[1][:300]

    def test_the_intro_names_all_four_forms(self):
        intro = self.prompt.split("CANDIDATE PROFILE")[0]
        for word in ("Werkstudent", "internships", "part-time", "full-time"):
            assert word in intro, word
        assert "WRONG employment form" in intro

    def test_the_lecture_period_penalty_does_not_apply_to_full_time(self):
        assert "regular full-time roles" in self.prompt

    def test_seniority_still_decides_for_full_time(self):
        assert "the LEVEL decides" in self.prompt
        assert "5+ years" in self.prompt and "Senior/Lead/Manager" in self.prompt

    def test_profile_text_lists_full_time_as_form_d(self):
        assert "(d) a regular FULL-TIME" in config._CV_SHARED
        assert "NOT thesis positions" in config._CV_SHARED
        assert "NOT full-time" not in config._CV_SHARED


class TestDigestShowsTheForm:
    def _html(self, form):
        j = _j(id="x", score=80, reason="fit", _where="HYBRID", _form=form)
        j.update({"title": "Junior Data Scientist", "salary": "", "contact": "", "apply_url": ""})
        return notifier._build_html([j])

    @pytest.mark.parametrize("form,label", [("full-time", "full-time"), ("student", "working student"),
                                            ("internship", "internship"), ("part-time", "part-time")])
    def test_each_form_has_a_badge(self, form, label):
        assert f">{label}</span>" in self._html(form)

    def test_the_shown_log_records_the_form(self):
        rec = main._shown_record(_j(id="x", _form="full-time"), "top", "2026-10-15T00:00:00+00:00")
        assert rec["form"] == "full-time"


class TestTheSearchesReachFullTimeRoles:
    def test_jobspy_has_a_fourth_lane_and_runs_every_query(self):
        got = scrapers._jobspy_active_queries()
        assert sorted(got) == sorted(config.SEARCH_QUERIES)
        full = [q for q in config.SEARCH_QUERIES if "junior" in q.lower() or "entry level" in q.lower()]
        assert len(full) >= 6
        first = [q.lower() for q in got[:12]]
        assert sum("junior" in q or "entry level" in q or "graduate" in q or "remote" in q or "nrw" in q
                   or "applied" in q or "llm" in q or "köln" in q or "düsseldorf" in q for q in first) >= 3

    def test_background_grace_covers_the_longer_list(self):
        assert scrapers.BACKGROUND_JOIN_SECONDS >= 1800

    @pytest.mark.parametrize("name,needle", [
        ("_LI_GUEST_QUERIES", "junior data scientist"), ("_ADZUNA_QUERIES", "Junior Data Scientist"),
        ("_XING_QUERIES", "Junior Data Scientist"), ("_SR_QUERIES", "junior"),
        ("_WD_FULLTIME_SEARCHES", "junior"), ("ARBEITSAGENTUR_FULLTIME_QUERIES", "Data Scientist"),
    ])
    def test_every_query_driven_source_asks_for_full_time_roles(self, name, needle):
        assert needle in getattr(scrapers, name)

    def test_web_search_has_full_time_queries(self):
        assert any("junior data scientist" in q for q in scrapers._WEB_QUERIES)

    def test_absolventa_accepts_entry_level_slugs(self):
        for slug in ("junior-data-analyst-m-w-d", "trainee-data-science", "berufseinsteiger-ki-entwickler"):
            assert scrapers._ABSOLVENTA_STUDENT.search(slug), slug

    def test_workday_ranks_junior_titles_after_student_ones(self):
        assert scrapers._WD_JUNIOR_TITLE.search("Junior Data Scientist")
        assert scrapers._WD_JUNIOR_TITLE.search("Graduate Software Engineer")
        assert not scrapers._WD_JUNIOR_TITLE.search("Principal Engineer")
        assert scrapers._WD_KEEP_PER_TENANT >= 40


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class TestArbeitsagenturFullTimePasses:
    @staticmethod
    def _items():
        def it(ref, title, ho):
            return {"referenznummer": ref, "stellenangebotsTitel": title, "firma": "Acme GmbH",
                    "homeofficemoeglich": ho,
                    "stellenlokationen": [{"adresse": {"ort": "Hamburg", "region": "HAMBURG", "land": "DEUTSCHLAND"}}],
                    "veroeffentlichungszeitraum": {"von": "2026-10-14"}}
        return [it("1", "Data Scientist (m/w/d)", True), it("2", "Data Scientist Pricing (m/w/d)", False)]

    def _run(self, monkeypatch):
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            if "jobdetails" in url:
                return _Resp({"stellenangebotsBeschreibung": "Python und SQL"})
            calls.append(dict(params or {}))
            return _Resp({"ergebnisliste": self._items() if params.get("was") == "Data Scientist" else []})
        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "_ba_enrich", lambda ref: {"description": "Python and SQL."})
        return scrapers.scrape_arbeitsagentur(), calls

    def test_an_nrw_wide_pass_and_a_germany_wide_pass_exist(self, monkeypatch):
        _rows, calls = self._run(monkeypatch)
        full = [c for c in calls if c.get("was") == "Data Scientist"]
        assert any(c.get("wo") == "Nordrhein-Westfalen" for c in full)
        assert any("wo" not in c and "arbeitszeit" not in c for c in full)

    def test_the_germany_wide_pass_keeps_only_home_office_jobs_and_marks_them(self, monkeypatch):
        rows, _calls = self._run(monkeypatch)
        by_url = {r["url"].rsplit("/", 1)[-1]: r for r in rows}
        assert "1" in by_url and "[Homeoffice möglich]" in by_url["1"]["description"]
        # ref 2 has no home office: it can only come from the NRW pass, never the Germany-wide one
        nrw_only = [r for r in rows if r["url"].endswith("/2")]
        assert len(nrw_only) <= 1

    def test_a_home_office_job_reads_as_hybrid_to_the_location_rule(self):
        j = main._tag_where(_j("Data Scientist (m/w/d)", location="Hamburg, Hamburg, Germany",
                               desc="[Homeoffice möglich]\nPython und SQL."))
        assert j["_where"] == "HYBRID" and main._is_in_focus_area(j)
