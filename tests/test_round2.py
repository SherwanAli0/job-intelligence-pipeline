"""Regression tests for the second review round (2026-10-22)."""
import json
import sys
import types
from datetime import datetime, timezone

import pytest

import application_kit
import graph
import liveness
import main
import scorer


@pytest.fixture(autouse=True)
def _liveness_on(monkeypatch):
    monkeypatch.setenv("JOBHUNTER_LIVENESS", "1")      # these tests mock requests.get


def _j(title="Junior Data Scientist (m/f/d)", desc="Python and SQL. English team.", **kw):
    base = {"id": kw.pop("id", "t"), "title": title, "company": "Acme", "location": "Berlin, Germany",
            "url": "https://example.org/job/1", "source": "Greenhouse", "description": desc,
            "posted_at": datetime.now(timezone.utc).isoformat()}
    base.update(kw)
    return base


class _Resp:
    def __init__(self, status=200):
        self.status_code = status

    def json(self):
        return {}


# ── Master's filter: the Bachelor alternative must sit NEXT to the Master's ──
@pytest.mark.parametrize("text", [
    "Master's degree required; Bachelor's students may apply for internships.",
    "A Master's degree in statistics is required. Experience beyond a bachelor's degree is not considered.",
    "Master's degree required, Bachelor's degree not sufficient.",
    "Master's degree or equivalent practical experience in statistics.",
    "MSc in Machine Learning required. We also offer bachelor thesis topics.",
    "PhD required. Our bachelor interns work on the side.",
])
def test_a_bachelor_word_elsewhere_in_the_window_does_not_excuse_a_masters_requirement(text):
    assert not main._no_masters_required({"title": "Data Scientist", "description": f"Requirements: {text} Python."}), text


@pytest.mark.parametrize("text", [
    "Master's degree in Computer Science (or Bachelor's with 3 years of experience)",
    "Bachelor's or Master's in Computer Science", "BSc/MSc in a quantitative field", "M.Sc. or B.Sc. in statistics",
    "Bachelor und Master Informatik", "Bachelor’s, Master’s or PhD",
])
def test_a_bachelor_next_to_the_masters_keeps_the_job(text):
    assert main._no_masters_required({"title": "Junior Data Scientist", "description": f"We want: {text}. Python."}), text


# ── page-type titles only matter for web search ──────────────────────────────
def test_page_type_words_are_not_applied_to_ats_postings():
    assert main._is_eligible_form(_j("Data Engineer – Salary Platform", source="Lever"))
    assert not main._is_eligible_form(_j("Data Engineer – Salary Guide", source="BraveSearch"))


# ── tech vocabulary, non-tech veto, level veto ───────────────────────────────
def _ft(title, **kw):
    j = _j(title, **kw)
    j["_form"] = main._job_form(j)
    return j


@pytest.mark.parametrize("title", ["Back-end Engineer", "Front-end Developer", "Java Developer (m/w/d)",
                                   "iOS Engineer", "Android Developer", "Programmer", "Python 3 Developer",
                                   "TypeScript Engineer", "Junior Data Scientist"])
def test_more_software_titles_pass(title):
    assert main._fulltime_title_ok(_ft(title)), title


@pytest.mark.parametrize("title", ["Data Labeler", "Data Annotation Specialist", "Data Rater (m/w/d)",
                                   "Automatisierungstechniker (m/w/d)", "Data Privacy Counsel", "Data Collection Agent",
                                   "Transcription Specialist - Data"])
def test_data_gig_and_non_tech_titles_fail(title):
    assert not main._fulltime_title_ok(_ft(title)), title


@pytest.mark.parametrize("title", ["Data Scientist II", "Data Scientist 2", "Software Developer III",
                                   "Data Scientist - Level 3", "Machine Learning Engineer (L4)", "Mid-level Data Scientist",
                                   "Experienced Data Scientist", "Intermediate Data Engineer", "Medior Developer",
                                   "Teamleiter Data Engineering", "Projektleiter Software", "Snr Data Scientist"])
def test_mid_level_titles_are_not_entry_level(title):
    assert not main._fulltime_title_ok(_ft(title)), title


# ── "hybridMultilevel" is Word markup, not hybrid work ───────────────────────
@pytest.mark.parametrize("text,expected", [
    ("mso-list:l0 hybridMultilevel level1", False), ("we run on a hybrid cloud platform", False),
    ("hybrid work model, two days in the office", True), ("hybrides Arbeiten möglich", True),
    ("hybridmultilevel but also hybrid work", True),
])
def test_hybrid_signal(text, expected):
    assert main._has_hybrid_signal(text) is expected


def test_word_markup_does_not_turn_an_onsite_ad_into_hybrid():
    j = main._tag_where(_j("Data Scientist", "Python. <w:lvl hybridMultilevel> Vor Ort im Büro.", location="Hamburg"))
    assert j["_where"] != "hybrid"


# ── liveness: circuit breaker, ordering, run stats ───────────────────────────
GH = "https://boards.greenhouse.io/acme/jobs/{}"


def _gh(n, **kw):
    return _j("Junior Data Scientist", id=f"gh{n}", url=GH.format(n), **kw)


class TestBreaker:
    def test_when_nearly_everything_of_one_api_answers_closed_nothing_is_dropped(self, monkeypatch):
        monkeypatch.setattr(liveness.requests, "get", lambda *a, **k: _Resp(404))
        jobs = [_gh(n) for n in range(6)]
        kept, closed = liveness.drop_closed(jobs)
        assert len(kept) == 6 and closed == []
        assert len(liveness.LAST_WARNINGS) == 1 and "boards-api.greenhouse.io" in liveness.LAST_WARNINGS[0]

    def test_a_normal_share_of_closed_postings_is_dropped(self, monkeypatch):
        monkeypatch.setattr(liveness.requests, "get",
                            lambda url, **k: _Resp(404 if url.rsplit("/", 1)[-1] in ("0", "1") else 200))
        kept, closed = liveness.drop_closed([_gh(n) for n in range(6)])
        assert len(closed) == 2 and len(kept) == 4 and liveness.LAST_WARNINGS == []

    def test_a_handful_of_probes_never_trips_it(self, monkeypatch):
        monkeypatch.setattr(liveness.requests, "get", lambda *a, **k: _Resp(404))
        kept, closed = liveness.drop_closed([_gh(n) for n in range(4)])
        assert len(closed) == 4 and kept == []

    def test_families_are_judged_separately(self, monkeypatch):
        uid = "0a1b2c3d-1111-2222-3333-444455556666"
        lever = [_j("Junior Data Scientist", id=f"lv{n}", url=f"https://jobs.lever.co/acme/{uid[:-1]}{n}") for n in range(6)]

        def get(url, **k):
            return _Resp(404 if "lever.co" in url else 200)
        monkeypatch.setattr(liveness.requests, "get", get)
        kept, closed = liveness.drop_closed(lever + [_gh(n) for n in range(6)])
        assert len(kept) == 12 and closed == [] and any("lever.co" in w for w in liveness.LAST_WARNINGS)


class TestRankOrdering:
    def test_a_dead_posting_does_not_take_a_digest_slot(self, monkeypatch):
        monkeypatch.setattr(liveness.requests, "get", lambda url, **k: _Resp(404 if url.endswith("/jobs/1") else 200))
        monkeypatch.setattr(application_kit, "enrich_with_kits", lambda top: None)
        monkeypatch.setattr(main, "MAX_RESULTS", 1)
        scored = [_gh(1, score=95), _gh(2, score=90)]
        for j in scored:
            j["_track"] = "DS"
        out = main.node_rank({"scored": scored})
        assert [j["id"] for j in out["top"]] == ["gh2"], "the live second-best must not be cut by the cap"

    def test_breaker_warnings_reach_the_health_warnings(self, monkeypatch):
        monkeypatch.setattr(liveness.requests, "get", lambda *a, **k: _Resp(404))
        monkeypatch.setattr(application_kit, "enrich_with_kits", lambda top: None)
        scored = [_gh(n, score=90 - n) for n in range(6)]
        for j in scored:
            j["_track"] = "DS"
        out = main.node_rank({"scored": scored, "health_warnings": ["earlier warning"]})
        assert len(out["top"]) == 6 and out["health_warnings"][0] == "earlier warning"
        assert any("Liveness" in w for w in out["health_warnings"])


def test_closed_dropped_is_part_of_the_graph_state():
    """LangGraph silently discards keys the state schema does not declare."""
    assert "closed_dropped" in graph.RunState.__annotations__


# ── fact gate ────────────────────────────────────────────────────────────────
FACTS = "B.Sc. grade 1.8. Up to 20 hours per week. Reach me: me@example.com.au, site https://x.org/ab. Salary 51,234."


class TestFactGateBoundaries:
    def test_the_leading_digit_of_a_decimal_is_not_a_claim_that_matches(self):
        assert application_kit.ungrounded_claims("I have 1 year of experience.", FACTS) == ["1"]

    @pytest.mark.parametrize("answer", ["I have 5 years of experience.", "I can do 4 hours a week.", "I know 8 tools."])
    def test_a_digit_hidden_inside_a_longer_number_is_still_a_claim(self, answer):
        assert application_kit.ungrounded_claims(answer, "up to 45 hours per week, grade 1.5, 28 days") != []

    def test_a_truncated_address_or_url_is_a_claim(self):
        assert application_kit.ungrounded_claims("Write to me@example.com.", FACTS) == ["me@example.com"]
        assert application_kit.ungrounded_claims("See https://x.org/a for details", FACTS) == ["https://x.org/a"]

    def test_the_full_address_and_url_pass(self):
        assert application_kit.ungrounded_claims("Write to me@example.com.au or see https://x.org/ab.", FACTS) == []

    def test_case_and_trailing_punctuation_do_not_matter(self):
        assert application_kit.ungrounded_claims("Grade: 1.8. Salary: 51.234!", FACTS) == []
        assert application_kit.ungrounded_claims("ME@EXAMPLE.COM.AU", FACTS) == []

    def test_no_catastrophic_backtracking_on_long_input(self):
        import time
        t0 = time.time()
        application_kit.ungrounded_claims("1." * 5000 + "x" * 20000 + "9" * 5000, FACTS)
        assert time.time() - t0 < 2


class TestKitFlow:
    def _fake_anthropic(self, monkeypatch, answers):
        class FakeMessages:
            def create(self, **kw):
                payload = {"answers": [{"index": i, "answer": a} for i, a in enumerate(answers)]}
                return types.SimpleNamespace(content=[types.SimpleNamespace(text=json.dumps(payload))])
        fake = types.ModuleType("anthropic")
        fake.Anthropic = lambda api_key=None: types.SimpleNamespace(messages=FakeMessages())
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    def test_the_bank_is_not_written_back_when_every_draft_was_rejected(self, monkeypatch):
        saved = []
        monkeypatch.setattr(application_kit, "_load_facts", lambda: "Available from October 2026.")
        monkeypatch.setattr(application_kit, "_greenhouse_questions", lambda url: ["Years of experience?"])
        monkeypatch.setattr(application_kit, "load_bank", lambda: {})
        monkeypatch.setattr(application_kit, "save_bank", lambda b: saved.append(dict(b)))
        self._fake_anthropic(monkeypatch, ["7 years"])
        job = _j(url="https://boards.greenhouse.io/acme/jobs/111")
        job["_form"] = "student"
        application_kit.enrich_with_kits([job])
        assert saved == [], "writing the bank back would wipe it after a transient read error"

    @pytest.mark.parametrize("facts,expected", [("FULLTIME_KIT: no", 0), ("fulltime_kit: NO\nTARGET: any", 0),
                                                ("  FULLTIME_KIT:   yes  ", 1), ("note FULLTIME_KIT: yes", 0)])
    def test_only_an_explicit_yes_line_enables_full_time_kits(self, monkeypatch, facts, expected):
        asked = []
        monkeypatch.setattr(application_kit, "_load_facts", lambda: facts)
        monkeypatch.setattr(application_kit, "_greenhouse_questions", lambda url: asked.append(url) or [])
        monkeypatch.setattr(application_kit, "load_bank", lambda: {})
        job = _j(url="https://boards.greenhouse.io/acme/jobs/222")
        job["_form"] = "full-time"
        application_kit.enrich_with_kits([job])
        assert len(asked) == expected, facts


# ── the posting archive is written by node_persist, and only for a sent digest ──
class TestPersistArchives:
    def _run(self, monkeypatch, tmp_path, email_ok, dry_run):
        files = {}
        monkeypatch.setattr(main.storage, "read_text", lambda name: files.get(name))
        monkeypatch.setattr(main.storage, "write_text", lambda name, text: files.__setitem__(name, text))
        monkeypatch.setattr(main, "_BODY_RETRY_FILE", tmp_path / "body_retry.json")
        monkeypatch.setattr(main, "save_seen", lambda s: None)
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "save_digested", lambda d: None)
        monkeypatch.setattr(main, "_record_shown", lambda top, near: None)
        monkeypatch.setattr(main, "_update_source_registry", lambda c: None)
        monkeypatch.setattr(main, "_record_run_stats", lambda s: None)
        import metrics
        monkeypatch.setattr(metrics, "publish", lambda **kw: None)
        job = _j(id="j1")
        main.node_persist({"dry_run": dry_run, "seen": {}, "email_ok": email_ok, "all_jobs": [job],
                           "top": [job], "near": []})
        return {n: t for n, t in files.items() if n.startswith("postings_")}

    def test_a_sent_digest_is_archived(self, monkeypatch, tmp_path):
        got = self._run(monkeypatch, tmp_path, True, False)
        (text,) = got.values()
        assert [json.loads(l)["id"] for l in text.strip().splitlines()] == ["j1"]

    @pytest.mark.parametrize("email_ok,dry_run", [(False, False), (True, True)])
    def test_a_failed_send_and_a_dry_run_archive_nothing(self, monkeypatch, tmp_path, email_ok, dry_run):
        assert self._run(monkeypatch, tmp_path, email_ok, dry_run) == {}
