"""Alarms that respect retired sources, the catch-up (recovery) mechanism, and
the failure handling added after the 2026-10-04 audit: state reads that fail
loudly, a run claim that outlasts a healthy run, a failed send that fails the
run, and a cost meter that prices what was actually requested. All offline."""

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import config
import main
import notifier
import scorer
import scrapers
import storage


def _j(title="Working Student Data", desc="", **kw):
    base = {"id": kw.pop("id", "t"), "title": title, "company": "X", "location": "Köln",
            "url": "https://example.org/job/1", "source": "linkedin",
            "description": desc, "posted_at": ""}
    base.update(kw)
    return base


# ── Step 5: a source switched off on purpose stops raising alarms ────────────

class TestRetiredSourcesAreQuiet:
    def _history(self, src, n=10, value=400):
        return [{"sources": {src: value, "linkedin": 300}} for _ in range(n)] + \
               [{"sources": {"linkedin": 300}} for _ in range(2)]

    def test_median_alarm_skips_a_retired_source(self):
        assert main._dead_source_warnings(self._history("HiringCafe"), {"linkedin": 300}) == []

    def test_median_alarm_still_fires_for_a_live_source(self):
        w = main._dead_source_warnings(self._history("Adzuna"), {"linkedin": 300})
        assert len(w) == 1 and "Adzuna" in w[0]

    def test_registry_alarm_skips_retired_sources_however_long_they_are_silent(self):
        now = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
        reg = {"HiringCafe": {"typical": 474, "last_nonzero": "2026-09-01T08:49:43+00:00"},
               "SAP": {"typical": 13, "last_nonzero": "2026-09-21T08:32:47+00:00"},
               "Stellenwerk": {"typical": 90, "last_nonzero": "2026-09-25T08:00:00+00:00"}}
        w = main._absent_source_warnings_from(reg, {"linkedin": 300}, now)
        assert len(w) == 1 and "Stellenwerk" in w[0]

    def test_the_dead_sap_entries_are_gone_from_config(self):
        assert not any(c["name"] == "SAP" for c in config.COMPANY_PAGES)
        assert not any(t[0] == "sap" for t in scrapers.WORKDAY_TENANTS)
        assert {"HiringCafe", "SAP"} <= main._RETIRED_SOURCES


# ── Recovery: a catch-up day re-reads employer boards, never resends ─────────

class TestCatchupRecovery:
    def test_is_catchup_follows_the_configured_date(self):
        assert config.is_catchup(config.CATCHUP_UNTIL - timedelta(days=1))
        assert not config.is_catchup(config.CATCHUP_UNTIL)
        assert config.max_posting_age_hours(config.CATCHUP_UNTIL - timedelta(days=1)) == 168

    def _old(self, source, days=40):
        return _j(source=source,
                  posted_at=(datetime.now(timezone.utc) - timedelta(days=days)).isoformat())

    def test_employer_boards_are_uncapped_only_on_a_catchup_day(self, monkeypatch):
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
        monkeypatch.setattr(config, "max_posting_age_hours", lambda today=None: 24)
        assert not main._is_fresh_enough(self._old("Greenhouse"))
        monkeypatch.setattr(config, "is_catchup", lambda today=None: True)
        monkeypatch.setattr(config, "max_posting_age_hours", lambda today=None: 168)
        for src in ("Greenhouse", "Lever", "Personio", "SmartRecruiters", "Workday-CXS", "Ashby"):
            assert main._is_fresh_enough(self._old(src)), src

    def test_aggregators_keep_a_window_even_on_a_catchup_day(self, monkeypatch):
        monkeypatch.setattr(config, "is_catchup", lambda today=None: True)
        monkeypatch.setattr(config, "max_posting_age_hours", lambda today=None: 168)
        assert not main._is_fresh_enough(self._old("linkedin", days=40))
        assert main._is_fresh_enough(self._old("linkedin", days=5))

    def test_ever_shown_keys_reads_the_whole_emailed_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage, "BUCKET", "")
        monkeypatch.chdir(tmp_path)
        lines = [{"key": "x::working student data", "shown_at": "2026-08-02T08:00:00+00:00"},
                 {"key": "y::werkstudent ki", "shown_at": "2026-09-30T08:00:00+00:00"},
                 {"title": "row without a key"}]
        (tmp_path / "shown_jobs.jsonl").write_text(
            "\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")
        assert main._ever_shown_keys() == {"x::working student data", "y::werkstudent ki"}

    def test_a_job_emailed_two_months_ago_is_blocked(self, monkeypatch):
        """The 30-day digest memory has forgotten it; the emailed log has not."""
        job = _j("Working Student Data", id="new-id", company="X GmbH", source="Greenhouse")
        key = main._digest_key(job)
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "_ever_shown_keys", lambda: {key})
        monkeypatch.setattr(main, "_fill_missing_bodies", lambda jobs: [])
        monkeypatch.setattr(main, "_skill_radar", lambda jobs: None)
        monkeypatch.setattr(config, "is_catchup", lambda today=None: True)
        out = main.node_filter({"seen": {}, "all_jobs": [job]})
        assert out["new_jobs"] == []
        assert sum(out["drop_by_filter_track"]["Already-digested filter (company+title)"].values()) == 1

    def test_the_emailed_log_is_consulted_on_a_normal_day_too(self, monkeypatch):
        """The real case of 2026-10-05: a Bayer internship emailed on 09-04
        came back under a new link a month later, on a normal day."""
        job = _j("Internship Analytics Advisory", id="new-link", company="Bayer", source="Bayer")
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "_ever_shown_keys", lambda: {main._digest_key(job)})
        monkeypatch.setattr(main, "_fill_missing_bodies", lambda jobs: [])
        monkeypatch.setattr(main, "_skill_radar", lambda jobs: None)
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
        assert main.node_filter({"seen": {}, "all_jobs": [job]})["new_jobs"] == []

    def test_an_unreadable_emailed_log_stops_the_run(self, monkeypatch):
        """An empty block-list means resending everything."""
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(storage, "read_text",
                            lambda name: (_ for _ in ()).throw(storage.StorageUnavailable("down")))
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
        with pytest.raises(storage.StorageUnavailable):
            main.node_filter({"seen": {}, "all_jobs": [_j(id="a")]})

    def _scored(self, n):
        return [dict(_j(f"Working Student Data {i}", id=str(i), company=f"C{i}"),
                     score=90 - (i % 30), _track=("AI", "DS", "ML", "DA")[i % 4])
                for i in range(n)]

    def test_the_digest_cap_says_what_it_cut(self, monkeypatch, capsys):
        monkeypatch.setattr(main, "enrich_with_kits", lambda top: None, raising=False)
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
        out = main.node_rank({"scored": self._scored(main.MAX_RESULTS + 20)})
        assert len(out["top"]) == main.MAX_RESULTS
        assert "[Digest cap] 20 jobs" in capsys.readouterr().out

    def test_a_catchup_day_delivers_the_backlog_instead_of_cutting_it(self, monkeypatch):
        monkeypatch.setattr(main, "enrich_with_kits", lambda top: None, raising=False)
        monkeypatch.setattr(config, "is_catchup", lambda today=None: True)
        out = main.node_rank({"scored": self._scored(main.MAX_RESULTS + 20)})
        assert len(out["top"]) == main.MAX_RESULTS + 20


# ── Step 7: state reads fail loudly ──────────────────────────────────────────

class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class _S3:
    """In-memory S3 with conditional puts and a switch to break reads."""

    def __init__(self):
        self.objects = {}
        self.read_error = None
        self.deleted = []

    def get_object(self, Bucket, Key):
        if self.read_error:
            raise self.read_error
        if Key not in self.objects:
            raise Exception("NoSuchKey: the specified key does not exist")
        return {"Body": _Body(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        if kw.get("IfNoneMatch") == "*" and Key in self.objects:
            raise Exception("PreconditionFailed (412)")
        self.objects[Key] = Body

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)


@pytest.fixture
def s3(monkeypatch):
    fake = _S3()
    monkeypatch.setattr(storage, "BUCKET", "test-bucket")
    monkeypatch.setattr(storage, "PREFIX", "state")
    monkeypatch.setattr(storage, "_s3", lambda: fake)
    monkeypatch.setattr(storage, "_OWNED_CLAIMS", {})
    return fake


class TestStateReadsFailLoudly:
    def test_missing_object_is_still_none(self, s3):
        assert storage.read_text("seen_jobs.json") is None

    @pytest.mark.parametrize("err", [Exception("AccessDenied: not authorized"),
                                     TimeoutError("Read timeout on endpoint URL"),
                                     Exception("SlowDown: reduce your request rate")])
    def test_any_other_read_failure_raises(self, s3, err):
        s3.objects["state/seen_jobs.json"] = b'{"a": "2026-10-01"}'
        s3.read_error = err
        with pytest.raises(storage.StorageUnavailable):
            storage.read_text("seen_jobs.json")

    def test_an_outage_cannot_make_every_job_look_new(self, s3):
        """Before: load_seen() returned {} and ~10,000 jobs were 'new'."""
        s3.objects["state/seen_jobs.json"] = json.dumps({"a": date.today().isoformat()}).encode()
        s3.read_error = Exception("AccessDenied")
        with pytest.raises(storage.StorageUnavailable):
            main.load_seen()
        with pytest.raises(storage.StorageUnavailable):
            main.load_digested()

    def test_an_outage_cannot_overwrite_history_with_one_line(self, s3):
        s3.objects["state/run_stats.jsonl"] = b'{"n": 1}\n{"n": 2}\n'
        s3.read_error = Exception("AccessDenied")
        with pytest.raises(storage.StorageUnavailable):
            storage.append_line("run_stats.jsonl", '{"n": 3}')
        assert s3.objects["state/run_stats.jsonl"] == b'{"n": 1}\n{"n": 2}\n'

    def test_optional_readers_degrade_instead_of_stopping_the_run(self, s3):
        s3.read_error = Exception("AccessDenied")
        assert main._load_run_history() == []
        assert main._load_source_registry() == {}
        import application_kit
        assert application_kit.load_bank() == {}
        # The retry memory cannot be read either: it starts from zero attempts
        # rather than raising, so the id is simply retried once more.
        assert main._body_retry_update(["a"]) == {"a"}


class TestRunClaim:
    def test_ttl_outlasts_a_healthy_slow_run(self):
        """Scrape (~40-60 min) + the full batch wait must fit inside the TTL;
        at 90 minutes the claim expired while the run was still going."""
        assert storage._CLAIM_TTL_SECONDS >= scorer._BATCH_TIMEOUT_SECONDS + 60 * 60

    def test_a_run_cannot_release_a_claim_it_no_longer_owns(self, s3, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(storage, "_now_epoch", lambda: clock[0])
        assert storage.claim("run-full")                       # run A
        owner_a = dict(storage._OWNED_CLAIMS)
        clock[0] += storage._CLAIM_TTL_SECONDS + 60
        monkeypatch.setattr(storage, "_OWNED_CLAIMS", {})      # run B is another process
        assert storage.claim("run-full")                       # B takes over the expired claim
        monkeypatch.setattr(storage, "_OWNED_CLAIMS", owner_a) # A finishes late
        storage.release("run-full")
        assert "state/run-full.claim" in s3.objects, "A must not delete B's claim"
        assert s3.deleted == []

    def test_the_owner_can_release_its_own_claim(self, s3):
        assert storage.claim("run-full")
        storage.release("run-full")
        assert "state/run-full.claim" not in s3.objects
        assert storage.claim("run-full")

    def test_an_unreadable_live_claim_is_not_stolen(self, s3):
        assert storage.claim("run-full")
        s3.read_error = TimeoutError("Read timeout")
        assert storage.claim("run-full") is False

    def test_a_process_that_never_claimed_deletes_nothing(self, s3):
        s3.objects["state/run-full.claim"] = json.dumps(
            {"claimed_at": 1.0, "ttl": 1, "owner": "someone-else"}).encode()
        storage.release("run-full")
        assert s3.deleted == []


# ── Step 7: a failed send fails the run ──────────────────────────────────────

class TestFailedSendIsVisible:
    def _graph(self, monkeypatch, final):
        import graph
        monkeypatch.setattr(graph, "build_graph",
                            lambda nodes: SimpleNamespace(invoke=lambda state, cfg: final))

    def test_main_raises_when_the_digest_was_not_delivered(self, monkeypatch):
        self._graph(monkeypatch, {"email_ok": False})
        with pytest.raises(RuntimeError, match="could not be delivered"):
            main.main(dry_run=False)

    def test_a_delivered_or_empty_day_does_not_raise(self, monkeypatch):
        self._graph(monkeypatch, {"email_ok": True})
        main.main(dry_run=False)
        self._graph(monkeypatch, {})
        main.main(dry_run=False)

    def test_a_dry_run_never_raises_for_delivery(self, monkeypatch):
        self._graph(monkeypatch, {"email_ok": False})
        main.main(dry_run=True)

    def test_the_handler_reports_error_and_a_nonzero_status(self, monkeypatch):
        import handler
        self._graph(monkeypatch, {"email_ok": False})
        monkeypatch.setattr(storage, "claim", lambda name: True)
        monkeypatch.setattr(storage, "release", lambda name: None)
        out = handler._run(dry_run=False)
        assert out["status"] == "error" and "delivered" in out["error"]

    def _smtp(self, monkeypatch, failures):
        attempts = []

        class _Server:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def login(self, u, p):
                attempts.append(1)
                if len(attempts) <= failures:
                    raise OSError("connection reset by peer")
            def sendmail(self, *a): pass
        monkeypatch.setattr(notifier.smtplib, "SMTP_SSL", lambda host, port: _Server())
        monkeypatch.setattr(notifier, "_SMTP_RETRY_WAITS", (0, 0, 0))
        monkeypatch.setenv("GMAIL_USER", "me@example.org")
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
        return attempts

    def test_a_transient_smtp_error_is_retried(self, monkeypatch):
        attempts = self._smtp(monkeypatch, failures=2)
        assert notifier.send_email([]) is True
        assert len(attempts) == 3

    def test_an_error_after_the_message_was_accepted_is_not_retried(self, monkeypatch):
        """Gmail accepted the digest and only QUIT failed: retrying would
        send the same digest twice."""
        sends = []

        class _Server:
            def __enter__(self): return self
            def __exit__(self, *a): raise OSError("connection closed during QUIT")
            def login(self, u, p): pass
            def sendmail(self, *a): sends.append(1)
        monkeypatch.setattr(notifier.smtplib, "SMTP_SSL", lambda host, port: _Server())
        monkeypatch.setattr(notifier, "_SMTP_RETRY_WAITS", (0, 0, 0))
        monkeypatch.setenv("GMAIL_USER", "me@example.org")
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
        assert notifier.send_email([]) is True
        assert len(sends) == 1

    def test_a_persistent_smtp_error_returns_false_after_three_attempts(self, monkeypatch):
        attempts = self._smtp(monkeypatch, failures=99)
        assert notifier.send_email([]) is False
        assert len(attempts) == 3


# ── Step 7: the cost meter ───────────────────────────────────────────────────

@pytest.fixture
def usage(monkeypatch):
    monkeypatch.setattr(scorer, "TOKEN_USAGE", {
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        "cache_write_1h": 0, "by_model": {}, "batched": False})
    return lambda **kw: SimpleNamespace(**kw)


HAIKU = "claude-haiku-4-5-20251001"


class TestCostMeter:
    def test_batch_cache_writes_are_priced_at_the_one_hour_rate(self, usage):
        """The run of 2026-09-05, all batched: reported $0.0859, real $0.1138."""
        scorer._record_usage(HAIKU, usage(input_tokens=40527, output_tokens=7512,
                                          cache_read_input_tokens=6894,
                                          cache_creation_input_tokens=74446), batched=True)
        assert scorer.estimated_cost_usd() == pytest.approx(0.1138, abs=0.0002)

    def test_sync_cache_writes_keep_the_five_minute_rate(self, usage):
        scorer._record_usage(HAIKU, usage(input_tokens=1_000_000, output_tokens=0,
                                          cache_read_input_tokens=0,
                                          cache_creation_input_tokens=1_000_000))
        assert scorer.estimated_cost_usd() == pytest.approx(1.00 + 1.25)

    def test_sync_fallback_does_not_inherit_the_batch_discount(self, usage):
        u = dict(input_tokens=20000, output_tokens=4000, cache_read_input_tokens=0,
                 cache_creation_input_tokens=0)
        scorer._record_usage(HAIKU, usage(**u), batched=True)
        scorer._record_usage(HAIKU, usage(**u), batched=False)
        # batched: (0.02 + 0.02) * 0.5 = 0.02; sync: 0.04. It used to report 0.04.
        assert scorer.estimated_cost_usd() == pytest.approx(0.06)
        assert set(scorer.TOKEN_USAGE["by_model"]) == {HAIKU, f"{HAIKU}|batch"}

    def test_the_api_ttl_breakdown_wins_when_it_is_reported(self, usage):
        scorer._record_usage(HAIKU, usage(
            input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
            cache_creation_input_tokens=1_000_000,
            cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=400_000,
                                           ephemeral_1h_input_tokens=600_000)), batched=False)
        assert scorer.TOKEN_USAGE["cache_write_1h"] == 600_000
        assert scorer.estimated_cost_usd() == pytest.approx(0.4 * 1.25 + 0.6 * 2.0)

    def test_the_one_hour_split_reaches_the_run_record(self, usage):
        scorer._record_usage(HAIKU, usage(input_tokens=1, output_tokens=1,
                                          cache_read_input_tokens=0,
                                          cache_creation_input_tokens=500), batched=True)
        tokens = {k: v for k, v in scorer.TOKEN_USAGE.items() if k != "by_model"}
        assert tokens["cache_write_1h"] == 500 and tokens["batched"] is True
