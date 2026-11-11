"""Recall fixes of 2026-11-11 (owner: "fix all").

The run of that day scraped 12,972 ads, found 650 new ones and emailed one.
The losses were inside the pipeline, not in the sources: the English-only
rule dropped 134 of 178, the focus-area rule 197 of 375, JobSpy was cut off
at its 30-minute limit, and no score below the send threshold was recorded
anywhere. These tests pin each fix.
"""

import json
import threading

import pytest

import main
import scrapers
from config import SEARCH_QUERIES

ENGLISH = ("Join our data platform team for twenty hours a week alongside your "
           "studies. You will build evaluation pipelines in Python, query the "
           "warehouse with SQL and present findings to the product team. We are "
           "an international team and our working language is English.")
GERMAN = ("Als Werkstudent unterstützt du unser Data-Team bei der Entwicklung "
          "von Auswertungen und Dashboards. Du arbeitest mit Python und SQL und "
          "wertest Produktdaten aus. Du bist an einer Hochschule immatrikuliert "
          "und hast bis zu 20 Stunden pro Woche Zeit für uns im Team.")


def _j(title, desc="", location="Köln, Germany", **kw):
    d = {"id": kw.pop("id", "t"), "title": title, "company": "Acme",
         "location": location, "url": "https://example.com/j", "source": "s",
         "description": desc, "posted_at": ""}
    d.update(kw)
    return d


# -- German ads for student roles ---------------------------------------------

class TestGermanAdsForStudentRoles:
    @pytest.mark.parametrize("form", ["student", "internship", "part-time"])
    def test_german_student_ad_is_kept_and_marked(self, form, monkeypatch):
        monkeypatch.delenv("JOBHUNTER_GERMAN_ADS", raising=False)
        j = _j("Werkstudent Data (m/w/d)", GERMAN, _form=form)
        assert main._is_english_friendly(j)
        assert j["_german_ad"] is True

    def test_german_full_time_ad_still_drops(self, monkeypatch):
        monkeypatch.delenv("JOBHUNTER_GERMAN_ADS", raising=False)
        j = _j("Junior Data Scientist (m/w/d)", GERMAN, _form="full-time")
        assert not main._is_english_friendly(j)
        assert "_german_ad" not in j

    def test_german_ad_without_a_form_drops(self, monkeypatch):
        monkeypatch.delenv("JOBHUNTER_GERMAN_ADS", raising=False)
        assert not main._is_english_friendly(_j("Werkstudent Data (m/w/d)", GERMAN))

    def test_german_student_ad_demanding_fluent_german_drops(self, monkeypatch):
        monkeypatch.delenv("JOBHUNTER_GERMAN_ADS", raising=False)
        monkeypatch.setattr(main, "_requires_fluent_german", lambda text: True)
        assert not main._is_english_friendly(_j("Werkstudent Data", GERMAN, _form="student"))

    def test_stub_and_two_line_bodies_still_drop(self, monkeypatch):
        monkeypatch.delenv("JOBHUNTER_GERMAN_ADS", raising=False)
        assert not main._is_english_friendly(_j("Werkstudent", "Jetzt bewerben.", _form="student"))
        assert not main._is_english_friendly(_j("Werkstudent", GERMAN, _form="student", _stub=True))

    def test_switch_restores_english_only(self, monkeypatch):
        monkeypatch.setenv("JOBHUNTER_GERMAN_ADS", "off")
        assert not main._is_english_friendly(_j("Werkstudent Data", GERMAN, _form="student"))

    def test_english_ad_is_not_marked_german(self):
        j = _j("Working Student Data", ENGLISH, _form="student")
        assert main._is_english_friendly(j)
        assert "_german_ad" not in j


# -- Focus area: a location that names no place --------------------------------

class TestUnknownLocationReachesTheScorer:
    def _f(self, loc, extra=""):
        return main._tag_where(_j("Working Student Data", ENGLISH + extra, location=loc))

    @pytest.mark.parametrize("loc", ["", "Germany", "Deutschland", "DE", "Germany, DE"])
    def test_bare_country_location_is_kept_and_flagged(self, loc, monkeypatch):
        monkeypatch.delenv("JOBHUNTER_UNKNOWN_LOCATION", raising=False)
        j = self._f(loc)
        assert main._is_in_focus_area(j), loc
        assert j.get("_where_unknown") is True

    @pytest.mark.parametrize("loc", ["Oberpfaffenhofen, Germany", "Munich, Bavaria, Germany",
                                     "Berlin, BE, DE", "Frankfurt am Main"])
    def test_named_place_outside_nrw_still_drops(self, loc, monkeypatch):
        monkeypatch.delenv("JOBHUNTER_UNKNOWN_LOCATION", raising=False)
        assert not main._is_in_focus_area(self._f(loc)), loc

    def test_nrw_named_in_the_ad_is_not_flagged_unknown(self, monkeypatch):
        monkeypatch.delenv("JOBHUNTER_UNKNOWN_LOCATION", raising=False)
        j = self._f("Germany", " The role is based in our Cologne office.")
        assert main._is_in_focus_area(j)
        assert "_where_unknown" not in j

    def test_switch_restores_the_strict_rule(self, monkeypatch):
        monkeypatch.setenv("JOBHUNTER_UNKNOWN_LOCATION", "drop")
        assert not main._is_in_focus_area(self._f("Germany"))
        assert not main._is_in_focus_area(self._f(""))


# -- Whole-word matching -------------------------------------------------------

class TestWholeWordMatching:
    def test_phrase_in_needs_word_boundaries(self):
        assert main._phrase_in("based in lima, peru", "lima")
        assert not main._phrase_in("climate tech startup", "lima")
        assert not main._phrase_in("every woman and man", "oman")
        assert main._phrase_in("remote, us only", "us only")
        assert not main._phrase_in("paid as a bonus only", "us only")

    def test_climate_ad_abroad_is_not_read_as_lima(self):
        j = _j("Data Analyst", "We are a climate analytics company. " + ENGLISH,
               location="Vienna, Austria")
        assert main._is_attendable_from_germany(j)

    def test_bonus_only_is_not_a_us_lock(self):
        j = _j("Data Analyst", ENGLISH + " The variable part is paid as a bonus only.",
               location="Remote")
        assert main._is_attendable_from_germany(j)

    def test_radar_counts_whole_tokens(self, capsys):
        jobs = [_j("Sachbearbeiter", "Der Vertrag ist digital und scalable. We trust you."),
                _j("ML Engineer", "RAG pipelines in Rust, versioned with git.")]
        main._skill_radar(jobs)
        out = capsys.readouterr().out
        for skill in ("rag", "rust", "git"):
            line = next(l for l in out.splitlines() if l.strip().startswith(skill + " "))
            assert " 50%" in line, line
        assert not any(l.strip().startswith("scala ") for l in out.splitlines())

    def test_radar_reads_the_loaded_profile(self, monkeypatch, capsys):
        monkeypatch.setattr(main, "_profile_skill_text", lambda: "built on aws fargate and s3")
        main._skill_radar([_j("Cloud Engineer", "You deploy to AWS every day.")])
        line = next(l for l in capsys.readouterr().out.splitlines() if l.strip().startswith("aws "))
        assert "on CV" in line


# -- Audit trail ---------------------------------------------------------------

class TestAuditTrail:
    def test_scored_jobs_are_printed_and_recorded(self, monkeypatch, capsys):
        main._RUN_AUDIT.clear()
        scored = [dict(_j("Working Student AI", ENGLISH, id="a"), score=28, reason="needs Java",
                       _track="AI"),
                  dict(_j("Intern Data", ENGLISH, id="b"), score=0, reason="on-site Munich",
                       disqualified_category="location")]
        monkeypatch.setattr(main, "score_jobs", lambda jobs: scored)
        main.node_score({"new_jobs": []})
        out = capsys.readouterr().out
        assert "[ 28]" in out and "needs Java" in out
        rows = [r for r in main._RUN_AUDIT if r["stage"] == "score"]
        assert {r["id"]: r["score"] for r in rows} == {"a": 28, "b": 0}
        assert rows[1]["category"] == "location" if rows[1]["id"] == "b" else True
        main._RUN_AUDIT.clear()

    def test_audit_file_is_appended(self, monkeypatch):
        store = {"x": "{\"old\": 1}"}
        monkeypatch.setattr(main.storage, "read_text", lambda name: store.get("x"))
        monkeypatch.setattr(main.storage, "write_text",
                            lambda name, text: store.update(x=text, name=name))
        main._RUN_AUDIT.clear()
        main._RUN_AUDIT.append(main._audit_row(_j("T", id="z"), "filter", filter="English filter"))
        main._write_audit()
        lines = store["x"].strip().splitlines()
        assert json.loads(lines[0]) == {"old": 1}
        row = json.loads(lines[1])
        assert row["id"] == "z" and row["filter"] == "English filter" and row["run"]
        assert store["name"].startswith("audit_") and store["name"].endswith(".jsonl")
        main._RUN_AUDIT.clear()

    def test_audit_write_never_raises(self, monkeypatch):
        def boom(name):
            raise RuntimeError("s3 down")
        monkeypatch.setattr(main.storage, "read_text", boom)
        main._RUN_AUDIT.clear()
        main._RUN_AUDIT.append({"stage": "filter"})
        main._write_audit()
        main._RUN_AUDIT.clear()


# -- JobSpy lanes --------------------------------------------------------------

class TestJobSpyLanes:
    def test_each_site_runs_every_query_in_its_own_lane(self, monkeypatch):
        import sys
        import types

        import pandas as pd

        calls = []

        def fake_scrape_jobs(**kw):
            site = kw["site_name"][0]
            calls.append((site, kw["search_term"], kw["results_wanted"],
                          threading.current_thread().name))
            return pd.DataFrame([{"job_url": f"https://{site}.example/{kw['search_term']}",
                                  "title": kw["search_term"], "company": "Acme",
                                  "location": "Bonn", "site": site,
                                  "description": "x" * 500}])

        monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=fake_scrape_jobs))
        monkeypatch.setattr(scrapers, "_jobspy_active_queries", lambda: ["q1", "q2", "q3"])
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)
        monkeypatch.setattr(scrapers, "_fetch_full_description", lambda url: "")
        jobs = scrapers.scrape_jobspy()

        assert len(jobs) == 6
        assert sorted(c[1] for c in calls if c[0] == "indeed") == ["q1", "q2", "q3"]
        assert sorted(c[1] for c in calls if c[0] == "linkedin") == ["q1", "q2", "q3"]
        assert {c[3] for c in calls if c[0] == "indeed"} == {"jobspy-indeed"}
        assert {c[3] for c in calls if c[0] == "linkedin"} == {"jobspy-linkedin"}
        assert {c[2] for c in calls if c[0] == "linkedin"} == {scrapers._JOBSPY_LINKEDIN_RESULTS}
        assert scrapers.jobspy_progress_line() == "indeed 3/3, linkedin 3/3"

    def test_a_failing_query_does_not_stop_the_lane(self, monkeypatch):
        import sys
        import types

        import pandas as pd

        def fake_scrape_jobs(**kw):
            if kw["search_term"] == "bad":
                raise RuntimeError("429")
            return pd.DataFrame([{"job_url": f"https://x/{kw['site_name'][0]}/{kw['search_term']}",
                                  "title": "t", "company": "c", "location": "", "site": "s",
                                  "description": "x" * 500}])

        monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=fake_scrape_jobs))
        monkeypatch.setattr(scrapers, "_jobspy_active_queries", lambda: ["bad", "good"])
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)
        assert len(scrapers.scrape_jobspy()) == 2
        assert scrapers.jobspy_progress_line() == "indeed 2/2, linkedin 2/2"

    def test_linkedin_depth_is_below_indeed_depth(self):
        assert scrapers._JOBSPY_LINKEDIN_RESULTS < scrapers._JOBSPY_INDEED_RESULTS


# -- Queries and the Arbeitsagentur title screen --------------------------------

class TestQueriesAndTitleScreen:
    @pytest.mark.parametrize("needle", ["HiWi", "wissenschaftliche Hilfskraft",
                                        "Research Assistant", "Trainee"])
    def test_research_and_trainee_forms_are_searched(self, needle):
        assert any(needle.lower() in q.lower() for q in SEARCH_QUERIES), needle

    def test_no_duplicate_queries(self):
        assert len(SEARCH_QUERIES) == len({q.lower() for q in SEARCH_QUERIES})

    @pytest.mark.parametrize("title", ["Werkstudent AI", "Wissenschaftliche Hilfskraft NLP",
                                       "Werkstudent Forschung", "HiWi LLM Evaluation",
                                       "Student Research Assistant"])
    def test_arbeitsagentur_title_screen_keeps(self, title):
        assert scrapers._BA_RELEVANT_TITLE.search(title), title

    @pytest.mark.parametrize("title", ["Werkstudent Recht", "Verkäufer (m/w/d)",
                                       "Mitarbeiter Lager"])
    def test_arbeitsagentur_title_screen_still_drops(self, title):
        assert not scrapers._BA_RELEVANT_TITLE.search(title), title

    def test_hilfskraft_queries_run_in_the_student_lane(self, capsys):
        order = scrapers._jobspy_active_queries()
        capsys.readouterr()
        assert len(order) == len(SEARCH_QUERIES)


# -- A stopped task releases its claim ------------------------------------------

def test_handler_installs_a_sigterm_handler():
    import inspect

    import handler
    src = inspect.getsource(handler)
    assert "signal.SIGTERM" in src and "SystemExit(143)" in src
