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
