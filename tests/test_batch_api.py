"""
Tests for the T3 cost layer: Message Batches API path (50% discount) with its
sync fallback, structured-output parsing, and per-model thinking config.
All offline — fake clients only.
"""
from types import SimpleNamespace as NS

import pytest

import scorer


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(scorer.time, "sleep", lambda s: None)


def _jobs(n=2, prefix="j"):
    return [{"id": f"{prefix}{i}", "title": f"Job {i}", "company": "C",
             "location": "Berlin", "description": "d", "source": "Greenhouse",
             "url": "u", "posted_at": ""} for i in range(n)]


def _batch_result(custom_id, scores_json=None, errored=False):
    if errored:
        return NS(custom_id=custom_id, result=NS(type="errored"))
    return NS(custom_id=custom_id, result=NS(
        type="succeeded",
        message=NS(content=[NS(type="text", text=scores_json)]),
    ))


class FakeClient:
    """messages.create + messages.batches.* with programmable behaviour."""
    def __init__(self, batch_results=None, statuses=None, sync_text=None):
        self.cancelled = False
        self.created_requests = None
        outer = self

        class Batches:
            def create(self, requests):
                outer.created_requests = requests
                return NS(id="batch_1", processing_status="in_progress")

            def retrieve(self, batch_id):
                status = statuses.pop(0) if statuses else "ended"
                return NS(id=batch_id, processing_status=status)

            def results(self, batch_id):
                yield from (batch_results or [])

            def cancel(self, batch_id):
                outer.cancelled = True

        class Messages:
            batches = Batches()

            @staticmethod
            def create(**kw):
                return NS(content=[NS(type="text", text=sync_text or
                          '{"scores": [{"index": 0, "score": 50, "reason": "sync"}]}')])

        self.messages = Messages()


class TestApplyScores:
    def test_structured_object_format(self):
        batch = _jobs(1)
        scorer._apply_scores(batch, '{"scores": [{"index": 0, "score": 77, "reason": "r"}]}')
        assert batch[0]["score"] == 77

    def test_legacy_array_format_still_parses(self):
        batch = _jobs(1)
        scorer._apply_scores(batch, '[{"index": 0, "score": 42, "reason": "r"}]')
        assert batch[0]["score"] == 42


class TestThinkingConfig:
    def test_sonnet5_disables_adaptive_thinking(self):
        # Sonnet 5 runs adaptive thinking when the field is omitted — scoring
        # must disable it explicitly or every call spends thinking tokens.
        kw = scorer._thinking_kwargs(scorer.SONNET_MODEL)
        assert kw == {"thinking": {"type": "disabled"}}

    def test_haiku_sends_no_thinking_field(self):
        assert scorer._thinking_kwargs(scorer.HAIKU_MODEL) == {}


class TestBatchAPIPath:
    def test_successful_batch_scores_all_groups(self, monkeypatch):
        g0, g1 = _jobs(1, "a"), _jobs(1, "b")
        fake = FakeClient(batch_results=[
            _batch_result("g0", '{"scores": [{"index": 0, "score": 80, "reason": "x"}]}'),
            _batch_result("g1", '{"scores": [{"index": 0, "score": 60, "reason": "y"}]}'),
        ])
        monkeypatch.setattr(scorer, "client", fake)
        ran = scorer._score_groups_via_batch_api(
            [(g0, scorer.HAIKU_MODEL, "p"), (g1, scorer.HAIKU_MODEL, "p")])
        assert ran and g0[0]["score"] == 80 and g1[0]["score"] == 60

    def test_errored_entry_falls_back_to_sync(self, monkeypatch):
        g0, g1 = _jobs(1, "a"), _jobs(1, "b")
        fake = FakeClient(batch_results=[
            _batch_result("g0", '{"scores": [{"index": 0, "score": 80, "reason": "x"}]}'),
            _batch_result("g1", errored=True),
        ])
        monkeypatch.setattr(scorer, "client", fake)
        ran = scorer._score_groups_via_batch_api(
            [(g0, scorer.HAIKU_MODEL, "p"), (g1, scorer.HAIKU_MODEL, "p")])
        assert ran and g0[0]["score"] == 80
        assert g1[0]["score"] == 50  # sync fallback scored it

    def test_missing_entry_falls_back_to_sync(self, monkeypatch):
        g0 = _jobs(1, "a")
        fake = FakeClient(batch_results=[])  # batch returned nothing
        monkeypatch.setattr(scorer, "client", fake)
        ran = scorer._score_groups_via_batch_api([(g0, scorer.HAIKU_MODEL, "p")])
        assert ran and g0[0]["score"] == 50

    def test_timeout_cancels_and_reports_failure(self, monkeypatch):
        g0 = _jobs(1, "a")
        # Never ends: enough in_progress statuses to outlast the timeout
        n_polls = scorer._BATCH_TIMEOUT_SECONDS // scorer._BATCH_POLL_SECONDS + 2
        fake = FakeClient(statuses=["in_progress"] * n_polls)
        monkeypatch.setattr(scorer, "client", fake)
        ran = scorer._score_groups_via_batch_api([(g0, scorer.HAIKU_MODEL, "p")])
        assert ran is False and fake.cancelled

    def test_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("DISABLE_BATCH_API", "1")
        assert scorer._score_groups_via_batch_api([]) is False

    def test_batch_requests_carry_cost_controls(self, monkeypatch):
        g0 = _jobs(1, "a")
        fake = FakeClient(batch_results=[
            _batch_result("g0", '{"scores": [{"index": 0, "score": 80, "reason": "x"}]}')])
        monkeypatch.setattr(scorer, "client", fake)
        scorer._score_groups_via_batch_api([(g0, scorer.SONNET_MODEL, "profile")])
        params = fake.created_requests[0]["params"]
        assert params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert params["output_config"]["format"]["type"] == "json_schema"
        assert params["thinking"] == {"type": "disabled"}  # Sonnet 5 in batch too
