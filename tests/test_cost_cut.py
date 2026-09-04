"""Owner decisions of 2026-09-04 to cut the per-run bill (~$0.20 -> ~$0.07):
no Sonnet re-score, no near misses, and a free CV-keyword gate so obviously
non-technical student ads never reach Claude."""

import inspect

import pytest

import main
import scorer


def _j(title, desc=""):
    return {"id": "t", "title": title, "company": "X", "location": "Bonn",
            "url": "u", "source": "s", "description": desc, "posted_at": ""}


class TestTechRelevanceGate:
    # Real titles from the 2026-09-04 run that were scored near zero.
    @pytest.mark.parametrize("title", [
        "Werkstudent Kundenservice Privatkunden (m/w/d)",
        "Referendar*in für die CONNEX Support Unit",
        "Studentische Hilfskraft (m/w/d) Klinik für Psychiatrie",
        "Anerkennungsjahr Soziale Arbeit / Sozialpädagogik",
        "Werkstudent Marketing & Kommunikation (m/w/d)",
        "Praktikum Online Marketing (m/w/d)",
        "Werkstudent (m/w/d) Lager",
    ])
    def test_non_technical_student_ads_are_dropped_for_free(self, title):
        assert not main._is_tech_relevant(_j(title, "Wir suchen dich für unser Team."))

    @pytest.mark.parametrize("title", [
        "Praktikum KI-Entwicklung (m/w/d)",
        "Werkstudent Mathematik (w/m/d)",
        "Working Student (w/m/d) - Mobile Network Capacity Management",
        "Werkstudent Infrastrukturplanung & Data Analytics",
        "Werkstudent IT (w/m/d)",
        "Werkstudent Softwareentwicklung",
        "Studentische Hilfskraft Datenpflege in IT-Systemen",
        "Intern Global EDA Platforms (m/f/x)",
        "Werkstudent SAP (m/w/d)",
        "Werkstudent:in Digitalisierung & Prozessmanagement",
    ])
    def test_technical_titles_pass(self, title):
        assert main._is_tech_relevant(_j(title))

    def test_tech_content_in_the_body_rescues_a_vague_title(self):
        assert main._is_tech_relevant(_j(
            "Werkstudent Digitale Projekte",
            "Du entwickelst Auswertungen in Python und SQL für unser Dashboard."))

    def test_the_english_word_it_is_not_a_tech_signal(self):
        """'it' is one of the most common English words; only capitalised IT
        counts, on raw text."""
        assert not main._is_tech_relevant(_j(
            "Working Student Office Management",
            "We are a great team and it is a friendly place to work in."))

    def test_gate_is_wired_after_the_employment_form_filter(self):
        src = inspect.getsource(main.node_filter)
        assert "_is_tech_relevant" in src
        assert src.index("Employment-form filter") < src.index("_is_tech_relevant")


class TestNoNearMisses:
    def test_rank_never_returns_near_misses(self, monkeypatch):
        scored = [dict(_j(f"Werkstudent Data {i}"), score=s, _track="AI", id=str(i))
                  for i, s in enumerate((88, 60, 44, 40, 36, 12))]
        monkeypatch.setattr(main, "enrich_with_kits", lambda top: None, raising=False)
        out = main.node_rank({"scored": scored})
        assert out["near"] == []
        assert [j["score"] for j in out["top"]] == [88, 60]


class TestNoSonnet:
    def test_score_jobs_only_ever_calls_haiku(self, monkeypatch):
        models = []

        def fake_batch(batch, model=scorer.HAIKU_MODEL, cv_profile=""):
            models.append(model)
            for j in batch:
                j["score"], j["reason"] = 90, "fake"
            return batch
        monkeypatch.setattr(scorer, "_score_batch", fake_batch)
        monkeypatch.setattr(scorer, "_score_groups_via_batch_api", lambda groups: False)
        monkeypatch.setattr(scorer.time, "sleep", lambda *_: None)
        jobs = [dict(_j(f"Werkstudent Data Science {i}",
                        "Python and SQL, enrolled student, English team."), id=str(i))
                for i in range(3)]
        scorer.score_jobs(jobs)
        assert models and set(models) == {scorer.HAIKU_MODEL}
        assert scorer.SONNET_MODEL not in models

    def test_stage_three_is_gone_from_source(self):
        src = inspect.getsource(scorer.score_jobs)
        assert "re-scoring" not in src and "SONNET_MODEL)" not in src

