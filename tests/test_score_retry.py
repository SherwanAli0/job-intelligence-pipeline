"""A job the model returned no score for must not be marked seen forever:
it stays unseen for a bounded number of runs (same pattern as the body retry)."""
import json

import pytest

import main
import scorer


def _job(id_, **kw):
    j = {"id": id_, "title": "Working Student ML", "company": "Acme GmbH", "location": "Bonn",
         "url": f"https://example.org/{id_}", "description": "Python", "source": "t"}
    j.update(kw)
    return j


@pytest.fixture
def persist_env(tmp_path, monkeypatch):
    """node_persist with every side effect except the retry files redirected."""
    monkeypatch.setattr(main, "_BODY_RETRY_FILE", tmp_path / "body_retry.json")
    monkeypatch.setattr(main, "_SCORE_RETRY_FILE", tmp_path / "score_retry.json")
    saved = {}
    monkeypatch.setattr(main, "save_seen", lambda s: saved.update(s))
    monkeypatch.setattr(main, "load_digested", lambda: {})
    monkeypatch.setattr(main, "save_digested", lambda d: None)
    monkeypatch.setattr(main, "_record_shown", lambda top, near: None)
    monkeypatch.setattr(main, "_archive_postings", lambda top, near: None)
    monkeypatch.setattr(main, "_update_source_registry", lambda c: None)
    monkeypatch.setattr(main, "_record_run_stats", lambda s: None)
    import metrics
    monkeypatch.setattr(metrics, "publish", lambda **kw: None)

    def persist(scored, **state_kw):
        saved.clear()
        state = {"dry_run": False, "seen": {}, "email_ok": True,
                 "all_jobs": scored, "scored": scored}
        if state_kw.pop("no_scored", False):      # the "nothing new today" path
            del state["scored"]
        state.update(state_kw)
        main.node_persist(state)
        return dict(saved)

    persist.score_file = tmp_path / "score_retry.json"
    return persist


def _failed(id_):
    return _job(id_, score=0, reason=f"{scorer.SCORER_FAILED_PREFIX} no score returned")


def _scored(id_, score):
    return _job(id_, score=score, reason="model said so")


class TestRetryCounter:
    def test_gives_up_after_three_attempts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "_SCORE_RETRY_FILE", tmp_path / "score_retry.json")
        assert main._score_retry_update(["a", "b"]) == {"a", "b"}      # attempt 1
        assert main._score_retry_update(["a"]) == {"a"}                # attempt 2; b scored fine
        assert json.loads((tmp_path / "score_retry.json").read_text()) == {"a": 2}
        assert main._score_retry_update(["a"]) == set()                # attempt 3: give up
        assert json.loads((tmp_path / "score_retry.json").read_text()) == {}

    def test_no_failures_and_no_file_creates_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "_SCORE_RETRY_FILE", tmp_path / "score_retry.json")
        assert main._score_retry_update([]) == set()
        assert not (tmp_path / "score_retry.json").exists()

    def test_unwritable_state_falls_back_to_marking_seen(self, monkeypatch):
        def boom(name, text):
            raise OSError("no space")
        monkeypatch.setattr(main.storage, "read_text", lambda name: None)
        monkeypatch.setattr(main.storage, "write_text", boom)
        assert main._score_retry_update(["a"]) == set()


class TestPersist:
    def test_a_failed_job_stays_unseen_and_the_rest_are_seen(self, persist_env):
        seen = persist_env([_failed("fail"), _scored("low", 5), _scored("good", 80)])
        assert "fail" not in seen
        assert "low" in seen and "good" in seen

    def test_a_prescreened_job_is_seen_even_though_its_score_is_zero(self, persist_env):
        pre = _job("pre", score=0, reason="[Pre-screened] needs German")
        assert "pre" in persist_env([pre])

    def test_a_job_that_keeps_failing_is_marked_seen_on_the_third_run(self, persist_env):
        assert "x" not in persist_env([_failed("x")])      # run 1
        assert "x" not in persist_env([_failed("x")])      # run 2
        assert "x" in persist_env([_failed("x")])          # run 3: give up, stop retrying
        assert json.loads(persist_env.score_file.read_text()) == {}

    def test_a_job_that_scores_on_the_retry_is_seen_and_forgotten(self, persist_env):
        persist_env([_failed("x")])
        assert "x" in persist_env([_scored("x", 70)])
        assert json.loads(persist_env.score_file.read_text()) == {}

    def test_body_retry_and_score_retry_combine(self, persist_env):
        seen = persist_env([_failed("s"), _job("b", score=0, reason="r"), _scored("ok", 60)],
                           body_unresolved=["b"])
        assert "s" not in seen and "b" not in seen and "ok" in seen

    def test_a_dry_run_records_nothing(self, persist_env):
        persist_env([_failed("x")], dry_run=True)
        assert not persist_env.score_file.exists()

    def test_an_undelivered_digest_records_nothing(self, persist_env):
        seen = persist_env([_failed("x"), _scored("ok", 80)], email_ok=False)
        assert seen == {}, "nothing is marked seen when the digest was not sent"
        assert not persist_env.score_file.exists()

    def test_a_quiet_day_without_scored_state_still_persists(self, persist_env):
        assert "a" in persist_env([_job("a")], no_scored=True)


def test_the_real_scorer_failure_marker_is_what_persist_looks_for(persist_env, monkeypatch):
    """Couples the two modules: an API outage in the scorer must leave the job
    unseen through node_persist."""
    class Boom:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("simulated API outage")
    monkeypatch.setattr(scorer, "_client", lambda: Boom)
    monkeypatch.setattr(scorer.time, "sleep", lambda *_: None)
    j = _job("outage")
    scorer._score_batch([j])
    assert j["score"] == 0 and j["reason"].startswith(scorer.SCORER_FAILED_PREFIX)
    assert "outage" not in persist_env([j])
