"""
Tests for the S3/local storage layer.

The critical property is that setting JOBHUNTER_S3_BUCKET changes WHERE state
goes and nothing else: local runs, the test suite and the GitHub Actions
fallback must behave exactly as before while the AWS migration is half-done.
The S3 path is exercised against a fake client, so these stay offline.
"""

import json
import sys

import pytest

import storage


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "BUCKET", "")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class _FakeS3:
    """Minimal in-memory stand-in for the S3 client."""

    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Exception("NoSuchKey: the specified key does not exist")
        return {"Body": _Body(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Exception("404 Not Found")
        return {}


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


@pytest.fixture
def s3(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(storage, "BUCKET", "test-bucket")
    monkeypatch.setattr(storage, "PREFIX", "state")
    monkeypatch.setattr(storage, "_s3", lambda: fake)
    return fake


class TestLocalModeUnchanged:
    def test_write_then_read_roundtrip(self, local):
        storage.write_text("seen_jobs.json", '{"a": "2026-07-20"}')
        assert json.loads(storage.read_text("seen_jobs.json")) == {"a": "2026-07-20"}

    def test_missing_file_returns_none(self, local):
        assert storage.read_text("nope.json") is None

    def test_append_creates_and_appends(self, local):
        storage.append_line("run_stats.jsonl", '{"n": 1}')
        storage.append_line("run_stats.jsonl", '{"n": 2}')
        lines = storage.read_text("run_stats.jsonl").strip().splitlines()
        assert [json.loads(x)["n"] for x in lines] == [1, 2]

    def test_using_s3_is_false(self, local):
        assert storage.using_s3() is False
        assert storage.describe() == "local files"


class TestS3Mode:
    def test_write_then_read_roundtrip(self, s3):
        storage.write_text("seen_jobs.json", '{"a": "2026-07-20"}')
        assert json.loads(storage.read_text("seen_jobs.json")) == {"a": "2026-07-20"}

    def test_key_is_prefixed(self, s3):
        storage.write_text("seen_jobs.json", "{}")
        assert "state/seen_jobs.json" in s3.objects

    def test_missing_object_returns_none_not_raises(self, s3):
        assert storage.read_text("never_written.json") is None

    def test_append_is_read_modify_write(self, s3):
        storage.append_line("run_stats.jsonl", '{"n": 1}')
        storage.append_line("run_stats.jsonl", '{"n": 2}')
        body = s3.objects["state/run_stats.jsonl"].decode()
        assert [json.loads(x)["n"] for x in body.strip().splitlines()] == [1, 2]

    def test_append_to_missing_object_starts_fresh(self, s3):
        storage.append_line("new.jsonl", '{"n": 1}')
        assert s3.objects["state/new.jsonl"].decode() == '{"n": 1}\n'

    def test_append_repairs_missing_trailing_newline(self, s3):
        s3.objects["state/run_stats.jsonl"] = b'{"n": 1}'  # no trailing \n
        storage.append_line("run_stats.jsonl", '{"n": 2}')
        body = s3.objects["state/run_stats.jsonl"].decode()
        assert body == '{"n": 1}\n{"n": 2}\n'

    def test_exists(self, s3):
        assert storage.exists("x.json") is False
        storage.write_text("x.json", "{}")
        assert storage.exists("x.json") is True

    def test_describe_shows_bucket(self, s3):
        assert storage.describe() == "s3://test-bucket/state/"


class TestMainUsesStorage:
    def test_seen_jobs_roundtrip_through_s3(self, s3, monkeypatch):
        monkeypatch.setitem(sys.modules, "storage", storage)
        import main

        main.save_seen({"job-1": "2026-07-20", "job-2": "2026-07-20"})
        assert "state/seen_jobs.json" in s3.objects
        assert set(main.load_seen()) == {"job-1", "job-2"}

    def test_legacy_flat_list_still_migrates_from_s3(self, s3):
        import main

        s3.objects["state/seen_jobs.json"] = json.dumps(["old-1", "old-2"]).encode()
        loaded = main.load_seen()
        assert set(loaded) == {"old-1", "old-2"}
        assert all(isinstance(v, str) and v for v in loaded.values())

    def test_run_stats_append_through_s3(self, s3):
        import main

        main._record_run_stats({"ts": "2026-07-21T06:00:00+00:00", "sources": {"x": 1}})
        history = main._load_run_history()
        assert len(history) == 1
        assert history[0]["platform"] in ("local", "github-actions", "aws-lambda")


# ── Run claims (mutual exclusion) ─────────────────────────────────────────────
# EventBridge has no equivalent of the GitHub Actions concurrency group, so two
# overlapping runs would each see the same jobs as unseen and both send a
# digest. The same guard covers a future check-and-exit consumer, where the
# tick that finds a finished batch may take minutes to email and the next tick
# must not reprocess it.

class _CondFakeS3(_FakeS3):
    """Adds S3 conditional-write semantics: If-None-Match:* fails if present."""

    def put_object(self, Bucket, Key, Body, **kw):
        if kw.get("IfNoneMatch") == "*" and Key in self.objects:
            raise Exception("PreconditionFailed: At least one of the "
                            "pre-conditions you specified did not hold (412)")
        self.objects[Key] = Body

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


@pytest.fixture
def s3_cond(monkeypatch):
    fake = _CondFakeS3()
    monkeypatch.setattr(storage, "BUCKET", "test-bucket")
    monkeypatch.setattr(storage, "PREFIX", "state")
    monkeypatch.setattr(storage, "_s3", lambda: fake)
    return fake


class TestRunClaims:
    def test_first_caller_wins_second_is_refused(self, s3_cond):
        assert storage.claim("run-full") is True
        assert storage.claim("run-full") is False

    def test_release_lets_the_next_run_start(self, s3_cond):
        assert storage.claim("run-full") is True
        storage.release("run-full")
        assert storage.claim("run-full") is True

    def test_abandoned_claim_is_taken_over_after_ttl(self, s3_cond, monkeypatch):
        assert storage.claim("run-full", ttl_seconds=60) is True
        # Simulate the holder having crashed two hours ago
        later = storage._now_epoch() + 7200
        monkeypatch.setattr(storage, "_now_epoch", lambda: later)
        assert storage.claim("run-full", ttl_seconds=60) is True, \
            "a crashed run must not wedge the pipeline forever"

    def test_unparseable_claim_is_treated_as_abandoned(self, s3_cond):
        s3_cond.objects["state/run-full.claim"] = b"not json"
        assert storage.claim("run-full") is True

    def test_unrelated_s3_error_does_not_block_the_run(self, monkeypatch):
        """A network blip must not look like 'someone else is running'."""
        class _Broken(_CondFakeS3):
            def put_object(self, *a, **kw):
                raise Exception("ConnectionError: transient")

        fake = _Broken()
        monkeypatch.setattr(storage, "BUCKET", "test-bucket")
        monkeypatch.setattr(storage, "_s3", lambda: fake)
        assert storage.claim("run-full") is True

    def test_claim_key_is_namespaced_per_stage(self, s3_cond):
        storage.claim("run-full")
        storage.claim("run-consumer")
        assert "state/run-full.claim" in s3_cond.objects
        assert "state/run-consumer.claim" in s3_cond.objects

    def test_local_mode_claims_do_not_crash(self, local):
        assert storage.claim("run-full") is True
        storage.release("run-full")
