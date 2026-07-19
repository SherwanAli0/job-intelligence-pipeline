"""
Scorer tests: the hard pre-screen, track routing, and — most importantly —
the JSON parse paths of _score_batch, which crashed the whole pipeline when a
Haiku response was partial or the API errored (bug B1, fixed 2026-07-19).
"""
import json

import pytest

import scorer


# ── Fake Anthropic clients ────────────────────────────────────────────────────

class _Resp:
    def __init__(self, text):
        self.content = [type("T", (), {"text": text})()]


def make_client(text=None, exc=None, texts=None):
    """Client returning fixed text, raising, or yielding a sequence."""
    seq = list(texts) if texts else None

    class C:
        class messages:
            @staticmethod
            def create(**kw):
                if seq is not None:
                    item = seq.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    return _Resp(item)
                if exc:
                    raise exc
                return _Resp(text)
    return C


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(scorer.time, "sleep", lambda s: None)


def _jobs(n=2):
    return [{"id": str(i), "title": f"Job {i}", "company": "C", "location": "Berlin",
             "description": "desc", "source": "Greenhouse", "url": "u", "posted_at": ""}
            for i in range(n)]


# ── _score_batch parse paths (bug B1 regressions) ─────────────────────────────

class TestScoreBatch:
    def test_clean_json(self, monkeypatch):
        monkeypatch.setattr(scorer, "client", make_client(
            '[{"index": 0, "score": 80, "reason": "fit"}, {"index": 1, "score": 30, "reason": "meh"}]'))
        out = scorer._score_batch(_jobs())
        assert out[0]["score"] == 80 and out[1]["score"] == 30

    def test_fenced_json(self, monkeypatch):
        monkeypatch.setattr(scorer, "client", make_client(
            '```json\n[{"index": 0, "score": 75, "reason": "r"}]\n```'))
        out = scorer._score_batch(_jobs(1))
        assert out[0]["score"] == 75

    def test_api_error_yields_zero_not_crash(self, monkeypatch):
        # B1 regression: this used to return jobs with NO score key at all,
        # and main.py's j['score'] subscript killed the run before the email.
        monkeypatch.setattr(scorer, "client", make_client(exc=RuntimeError("api down")))
        out = scorer._score_batch(_jobs())
        assert all(j["score"] == 0 for j in out)
        assert "[scorer-failed]" in out[0]["reason"]

    def test_partial_response_fills_missing_with_zero(self, monkeypatch):
        monkeypatch.setattr(scorer, "client", make_client(
            '[{"index": 0, "score": 80, "reason": "fit"}]'))
        out = scorer._score_batch(_jobs(2))
        assert out[0]["score"] == 80 and out[1]["score"] == 0

    def test_garbage_response_survives(self, monkeypatch):
        monkeypatch.setattr(scorer, "client", make_client("I cannot score these jobs, sorry!"))
        out = scorer._score_batch(_jobs(1))
        assert out[0]["score"] == 0

    def test_retry_recovers_from_transient_error(self, monkeypatch):
        monkeypatch.setattr(scorer, "client", make_client(texts=[
            RuntimeError("blip"),
            '[{"index": 0, "score": 66, "reason": "second try"}]',
        ]))
        out = scorer._score_batch(_jobs(1))
        assert out[0]["score"] == 66

    def test_sonnet_stage_failure_keeps_haiku_scores(self, monkeypatch):
        monkeypatch.setattr(scorer, "client", make_client(exc=RuntimeError("down")))
        pre = _jobs(1)
        pre[0]["score"], pre[0]["reason"] = 61, "haiku verdict"
        out = scorer._score_batch(pre)
        assert out[0]["score"] == 61 and out[0]["reason"] == "haiku verdict"

    def test_out_of_range_index_ignored(self, monkeypatch):
        monkeypatch.setattr(scorer, "client", make_client(
            '[{"index": 99, "score": 80, "reason": "?"}]'))
        out = scorer._score_batch(_jobs(1))
        assert out[0]["score"] == 0


# ── Hard pre-screen ───────────────────────────────────────────────────────────

class TestHardDisqualify:
    def _j(self, **kw):
        base = {"id": "x", "title": "Junior Data Scientist", "company": "C",
                "location": "Berlin, Germany", "url": "u", "posted_at": "",
                "description": "Junior role, Python and SQL, English-speaking team.",
                "source": "Greenhouse"}
        base.update(kw)
        return base

    def test_clean_junior_passes(self):
        dq, _, _ = scorer._hard_disqualify(self._j())
        assert not dq

    def test_fluent_german_dropped(self):
        dq, _, cat = scorer._hard_disqualify(self._j(
            description="You have verhandlungssicheres Deutsch (C1) and English."))
        assert dq

    def test_werkstudent_dropped(self):
        dq, _, _ = scorer._hard_disqualify(self._j(title="Werkstudent Data Science"))
        assert dq

    def test_unpaid_dropped(self):
        dq, _, _ = scorer._hard_disqualify(self._j(
            description="This is an unpaid internship for 6 months."))
        assert dq


# ── Track routing ─────────────────────────────────────────────────────────────

class TestClassifyTrack:
    def _j(self, title, desc=""):
        return {"title": title, "description": desc or title, "company": "C"}

    def test_data_analyst(self):
        assert scorer._classify_track(self._j("Junior Data Analyst")) == "DA"

    def test_data_scientist(self):
        assert scorer._classify_track(self._j("Data Scientist")) == "DS"

    def test_ml_engineer(self):
        assert scorer._classify_track(self._j("Machine Learning Engineer")) == "ML"

    def test_ai_engineer(self):
        assert scorer._classify_track(self._j("AI Engineer (LLM)")) == "AI"

    def test_every_track_has_profile(self):
        for track in ("AI", "ML", "DS", "DA"):
            assert track in scorer._TRACK_PROFILES
