"""
Tests for seen-jobs state (T1e), run-stats/dead-source alarm (T1d), the
email-failure guard (B2), tracker (B5), and application-kit plumbing (B4).
All offline.
"""
import json

import pytest

import main
import notifier
import track
import application_kit as ak


# ── T1e: seen-jobs state ──────────────────────────────────────────────────────

class TestSeenState:
    @pytest.fixture(autouse=True)
    def _tmp_seen(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "SEEN_FILE", tmp_path / "seen.json")

    def test_missing_file_empty(self):
        assert main.load_seen() == {}

    def test_legacy_list_migrates(self):
        main.SEEN_FILE.write_text(json.dumps(["a", "b"]))
        seen = main.load_seen()
        assert isinstance(seen, dict) and set(seen) == {"a", "b"}

    def test_roundtrip(self):
        main.save_seen({"x": "2099-01-01"})
        assert main.load_seen() == {"x": "2099-01-01"}

    def test_pruning_drops_only_stale(self):
        from datetime import date, timedelta
        fresh = date.today().isoformat()
        stale = (date.today() - timedelta(days=main._SEEN_RETENTION_DAYS + 5)).isoformat()
        main.save_seen({"keep": fresh, "drop": stale})
        kept = main.load_seen()
        assert "keep" in kept and "drop" not in kept

    def test_corrupt_file_recovers(self):
        main.SEEN_FILE.write_text("{not json")
        assert main.load_seen() == {}


# ── T1d: dead-source alarm ────────────────────────────────────────────────────

class TestDeadSourceAlarm:
    HIST = [{"sources": {"XING": 140, "Adzuna": 500}}] * 5 + \
           [{"sources": {"XING": 0, "Adzuna": 480}}] * 2

    def test_dead_source_flagged(self):
        w = main._dead_source_warnings(self.HIST, {"XING": 0, "Adzuna": 490})
        assert len(w) == 1 and "XING" in w[0]

    def test_recovered_source_silent(self):
        assert main._dead_source_warnings(self.HIST, {"XING": 90, "Adzuna": 490}) == []

    def test_small_source_never_alarms(self):
        hist = [{"sources": {"Tiny": 2}}] * 7
        assert main._dead_source_warnings(hist, {"Tiny": 0}) == []

    def test_insufficient_history_silent(self):
        assert main._dead_source_warnings([], {"XING": 0}) == []

    def test_banner_renders_only_with_warnings(self, job):
        h = notifier._build_html([job(score=70, reason="r")], warnings=["Source 'XING' broken"])
        assert "Pipeline health" in h and "XING" in h
        h2 = notifier._build_html([job(score=70, reason="r")])
        assert "Pipeline health" not in h2


# ── B2: email result contract ─────────────────────────────────────────────────

class TestEmailGuard:
    def test_missing_creds_returns_false(self, monkeypatch):
        monkeypatch.delenv("GMAIL_USER", raising=False)
        monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
        assert notifier.send_email([]) is False


# ── B5: tracker ───────────────────────────────────────────────────────────────

class TestTracker:
    @pytest.fixture(autouse=True)
    def _tmp_tracker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(track, "APPLIED_FILE", tmp_path / "applied.json")
        monkeypatch.setattr(track, "_sync_secret", lambda data: None)  # no gh in CI

    def test_apply_and_funnel(self):
        track.mark_applied("https://x.com/j1", "Data Scientist", "Acme")
        f = track.get_funnel()
        assert f["total"] == 1 and f["applied"] == 1

    def test_status_transition(self):
        track.mark_applied("https://x.com/j1", "DS", "Acme")
        track.set_status("https://x.com/j1", "interview")
        assert track.get_funnel()["interview"] == 1

    def test_invalid_status_rejected(self):
        track.mark_applied("https://x.com/j1", "DS", "Acme")
        track.set_status("https://x.com/j1", "hired!!")  # not a valid state
        assert track.get_funnel()["applied"] == 1

    def test_followup_after_quiet_days(self):
        track.mark_applied("https://x.com/j1", "DS", "Acme")
        data = track.load_applied()
        k = next(iter(data))
        data[k]["applied_at"] = data[k]["last_change"] = "2026-01-01T00:00:00+00:00"
        track.save_applied(data)
        fu = track.get_followups()
        assert len(fu) == 1 and fu[0]["stale"] is True
        assert "Acme" in track.followup_draft(fu[0])


# ── B4: application kit plumbing ──────────────────────────────────────────────

class TestApplicationKit:
    def test_greenhouse_url_recognized(self):
        assert ak._GH_URL_RE.search("https://job-boards.greenhouse.io/gitlab/jobs/850379")
        assert ak._GH_URL_RE.search("https://boards.eu.greenhouse.io/mollie/jobs/123")
        assert not ak._GH_URL_RE.search("https://linkedin.com/jobs/view/1")

    def test_question_normalization(self):
        assert ak._norm_q("  What are your Salary   Expectations?  ") == \
               ak._norm_q("what are your salary expectations")

    def test_no_facts_no_draft(self, monkeypatch, tmp_path, capsys):
        # Without APPKIT_FACTS or personal/facts.md the kit must skip cleanly,
        # never invent answers.
        monkeypatch.delenv("APPKIT_FACTS", raising=False)
        monkeypatch.chdir(tmp_path)  # no personal/ here
        assert ak._load_facts() == ""

    def test_env_facts_win(self, monkeypatch):
        monkeypatch.setenv("APPKIT_FACTS", "- Fact one")
        assert ak._load_facts() == "- Fact one"


class TestShownJobsLoop:
    """O2: the conversion join the tracker's own docstring promises. Shown
    records must carry BOTH the pipeline id and md5(url) — track.py keys
    applications by url hash alone, so without it the join never matches."""

    def _shown(self, **over):
        import main
        j = {"id": "pid1", "url": "https://example.com/Job1", "title": "Junior DS",
             "company": "Acme", "source": "Greenhouse", "_track": "DS",
             "score": 72, "posted_at": "", "location": "", "description": ""}
        j.update(over)
        return main._shown_record(j, "top", "2026-07-31T10:00:00+00:00")

    def test_record_carries_both_join_keys(self):
        import hashlib
        r = self._shown()
        assert r["id"] == "pid1"
        assert r["url_hash"] == hashlib.md5(b"https://example.com/job1").hexdigest()
        assert r["track"] == "DS" and r["band"] == "top" and r["source"] == "Greenhouse"

    def test_conversion_join_by_url_hash(self, tmp_path, monkeypatch):
        import json, hashlib, importlib
        import storage, track, main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(storage, "BUCKET", "")          # local-file mode
        monkeypatch.setattr(track, "_sync_secret", lambda d: None)

        url = "https://example.com/ds-job"
        rec = main._shown_record(
            {"id": "p1", "url": url, "title": "Junior DS", "company": "Acme",
             "source": "Greenhouse", "_track": "DS", "score": 70, "posted_at": ""},
            "top", "2026-07-31T10:00:00+00:00")
        (tmp_path / "shown_jobs.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")

        track.mark_applied(url, "Junior DS", "Acme")
        track.set_status(url, "interview")

        per = track.conversion_stats()
        g = per["source"]["Greenhouse"]
        assert g == {"shown": 1, "applied": 1, "interviews": 1}
        assert per["track"]["DS"]["applied"] == 1

    def test_shown_but_never_applied_counts_shown_only(self, tmp_path, monkeypatch):
        import json
        import storage, track, main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(storage, "BUCKET", "")
        rec = main._shown_record(
            {"id": "p2", "url": "https://example.com/x", "title": "DA", "company": "B",
             "source": "linkedin", "_track": "DA", "score": 55, "posted_at": ""},
            "near", "2026-07-31T10:00:00+00:00")
        (tmp_path / "shown_jobs.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
        per = track.conversion_stats()
        assert per["source"]["linkedin"] == {"shown": 1, "applied": 0, "interviews": 0}

    def test_duplicate_shown_entries_counted_once(self, tmp_path, monkeypatch):
        import json
        import storage, track, main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(storage, "BUCKET", "")
        rec = main._shown_record(
            {"id": "p3", "url": "https://example.com/y", "title": "ML", "company": "C",
             "source": "XING", "_track": "ML", "score": 60, "posted_at": ""},
            "top", "2026-07-31T10:00:00+00:00")
        lines = json.dumps(rec) + "\n" + json.dumps(rec) + "\n"
        (tmp_path / "shown_jobs.jsonl").write_text(lines, encoding="utf-8")
        per = track.conversion_stats()
        assert per["source"]["XING"]["shown"] == 1

    def test_no_history_is_clean(self, tmp_path, monkeypatch):
        import storage, track
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(storage, "BUCKET", "")
        assert track.conversion_stats() == {}
