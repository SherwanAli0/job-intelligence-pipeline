"""Archive of every judged posting, and the second run of the day (2026-11-17).

Up to 2026-11-16 the pipeline had scored 9,266 ads and kept the text of 346,
all of them emailed ones scored 45 or more: nothing showed what a low score
had been given to. Every posting that reaches the scoring stage is now kept
with its text, its score and what decided it.

The same day the owner asked for a second run at 17:00 that looks at what was
published since the morning. That run asks the sources that take a time range
for the hours since the previous completed run started, not for 24 hours.
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import config
import main
import scorer
import scrapers

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _normal_window(monkeypatch):
    """Every test starts and ends with the normal source window."""
    monkeypatch.setattr(config, "_SOURCE_HOURS_OVERRIDE", None)
    monkeypatch.delenv("JOBHUNTER_SOURCE_WINDOW", raising=False)


def _job(id="a", **kw):
    d = {"id": id, "title": "Werkstudent Data Science", "company": "Acme", "location": "Köln",
         "url": f"https://example.org/{id}", "source": "LinkedIn", "posted_at": "2026-11-17",
         "description": "Python and SQL. " * 10, "score": 30, "reason": "Too little overlap.",
         "disqualified_category": "", "_track": "DS", "_form": "student", "_where": "HYBRID",
         "_model_score": 30}
    d.update(kw)
    return d


def _store(monkeypatch):
    files = {}
    monkeypatch.setattr(main.storage, "read_text", lambda name: files.get(name))
    monkeypatch.setattr(main.storage, "write_text", lambda name, text: files.__setitem__(name, text))
    return files


def _rows(files):
    (name, text), = files.items()
    assert name.startswith("scored_") and name.endswith(".jsonl")
    return [json.loads(line) for line in text.strip().splitlines()]


# -- the archive -----------------------------------------------------------------
class TestScoredArchive:
    def test_a_low_score_is_kept_with_its_text(self, monkeypatch):
        files = _store(monkeypatch)
        main._archive_scored([_job(score=12, _model_score=12, description="x" * 20000)])
        (rec,) = _rows(files)
        assert rec["id"] == "a" and rec["score"] == 12 and rec["model_score"] == 12
        assert len(rec["description"]) == 8000
        assert rec["title"] == "Werkstudent Data Science" and rec["company"] == "Acme"
        assert rec["track"] == "DS" and rec["form"] == "student" and rec["where"] == "HYBRID"
        assert rec["reason"] == "Too little overlap." and rec["category"] == ""

    def test_the_record_says_what_decided_and_what_the_model_saw(self, monkeypatch):
        files = _store(monkeypatch)
        main._archive_scored([_job()])
        (rec,) = _rows(files)
        assert rec["model"] == scorer.HAIKU_MODEL
        assert rec["shown_chars"] == scorer.SNIPPET_CHARS
        assert rec["evidence"] is False
        datetime.fromisoformat(rec["scored_at"])               # a real timestamp

    def test_the_penalised_score_and_the_model_score_are_both_kept(self, monkeypatch):
        files = _store(monkeypatch)
        main._archive_scored([_job(score=54, _model_score=62, ghost=True)])
        (rec,) = _rows(files)
        assert (rec["score"], rec["model_score"]) == (54, 62)

    def test_a_posting_dropped_by_a_rule_has_the_rule_and_no_model_score(self, monkeypatch):
        files = _store(monkeypatch)
        j = _job(score=0, reason="[Pre-screened] German above the candidate's level",
                 disqualified_category="german_required")
        j.pop("_model_score")
        main._archive_scored([j])
        (rec,) = _rows(files)
        assert rec["category"] == "german_required"
        assert rec["model_score"] is None and rec["model"] == "" and rec["score"] == 0

    def test_evidence_lines_are_flagged_not_stored(self, monkeypatch):
        files = _store(monkeypatch)
        match = {"requirement": "Python", "status": "met",
                 "evidence": [{"id": "cv:1", "text": "private candidate text"}]}
        main._archive_scored([_job(evidence=[match])])
        (name, text), = files.items()
        assert json.loads(text)["evidence"] is True
        assert "private candidate text" not in text

    def test_a_job_the_model_returned_no_score_for_is_left_out(self, monkeypatch):
        files = _store(monkeypatch)
        failed = _job(id="f", score=0, reason=f"{scorer.SCORER_FAILED_PREFIX} no score returned")
        main._archive_scored([failed, _job(id="ok")])
        assert [r["id"] for r in _rows(files)] == ["ok"]

    def test_a_job_left_unseen_for_another_try_is_left_out(self, monkeypatch):
        files = _store(monkeypatch)
        main._archive_scored([_job(id="stub"), _job(id="ok")], skip_ids={"stub"})
        assert [r["id"] for r in _rows(files)] == ["ok"]

    def test_only_unscored_jobs_writes_nothing(self, monkeypatch):
        files = _store(monkeypatch)
        main._archive_scored([_job(reason=f"{scorer.SCORER_FAILED_PREFIX} no score returned")])
        assert files == {}

    def test_a_second_run_on_the_same_day_appends(self, monkeypatch):
        files = _store(monkeypatch)
        main._archive_scored([_job(id="a")])
        main._archive_scored([_job(id="b")])
        assert [r["id"] for r in _rows(files)] == ["a", "b"]

    def test_an_unreadable_archive_is_not_overwritten(self, monkeypatch):
        written = []

        def unreadable(name):
            raise main.storage.StorageUnavailable("down")
        monkeypatch.setattr(main.storage, "read_text", unreadable)
        monkeypatch.setattr(main.storage, "write_text", lambda *a: written.append(a))
        main._archive_scored([_job()])                         # must not raise
        assert written == []

    def test_nothing_judged_writes_nothing(self, monkeypatch):
        files = _store(monkeypatch)
        main._archive_scored([])
        main._archive_scored(None)
        assert files == {}

    def test_non_ascii_text_is_stored_readable(self, monkeypatch):
        files = _store(monkeypatch)
        main._archive_scored([_job(title="Werkstudent Künstliche Intelligenz (m/w/d)")])
        (text,) = files.values()
        assert "Künstliche" in text

    def test_the_files_are_gitignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "scored_*.jsonl" in ignored
        assert "audit_*.jsonl" in ignored      # a local run writes these next to it


class TestPersistWritesTheArchive:
    def _run(self, monkeypatch, tmp_path, email_ok, dry_run, **state_kw):
        files = _store(monkeypatch)
        stats = []
        monkeypatch.setattr(main, "_BODY_RETRY_FILE", tmp_path / "body_retry.json")
        monkeypatch.setattr(main, "_SCORE_RETRY_FILE", tmp_path / "score_retry.json")
        monkeypatch.setattr(main, "save_seen", lambda s: None)
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "save_digested", lambda d: None)
        monkeypatch.setattr(main, "_record_shown", lambda top, near: None)
        monkeypatch.setattr(main, "_update_source_registry", lambda c: None)
        monkeypatch.setattr(main, "_record_run_stats", stats.append)
        import metrics
        monkeypatch.setattr(metrics, "publish", lambda **kw: None)
        low, high = _job(id="low", score=10, _model_score=10), _job(id="high", score=80, _model_score=80)
        state = {"dry_run": dry_run, "seen": {}, "email_ok": email_ok,
                 "all_jobs": [low, high], "scored": [low, high], "top": [high], "near": []}
        state.update(state_kw)
        main.node_persist(state)
        kept = {n: t for n, t in files.items() if n.startswith("scored_")}
        return kept, stats

    def test_a_delivered_run_keeps_low_and_high_scores(self, monkeypatch, tmp_path):
        kept, _ = self._run(monkeypatch, tmp_path, True, False)
        (text,) = kept.values()
        assert [json.loads(l)["id"] for l in text.strip().splitlines()] == ["low", "high"]

    def test_an_ad_whose_body_is_fetched_again_next_run_is_kept_then_not_now(self, monkeypatch,
                                                                              tmp_path):
        # "low" was judged on a stub body and stays unseen for a retry. Keeping it
        # now would store it twice, the first time with a text nobody should learn from.
        kept, _ = self._run(monkeypatch, tmp_path, True, False, body_unresolved=["low"])
        (text,) = kept.values()
        assert [json.loads(l)["id"] for l in text.strip().splitlines()] == ["high"]

    @pytest.mark.parametrize("email_ok,dry_run", [(False, False), (True, True)])
    def test_a_dry_run_or_an_undelivered_digest_keeps_nothing(self, monkeypatch, tmp_path,
                                                             email_ok, dry_run):
        # An undelivered digest leaves the state untouched, so the same postings
        # are scored again on the next run and kept then: no duplicates.
        kept, _ = self._run(monkeypatch, tmp_path, email_ok, dry_run)
        assert kept == {}

    def test_the_stats_line_names_the_source_window(self, monkeypatch, tmp_path):
        _, stats = self._run(monkeypatch, tmp_path, True, False)
        assert stats[0]["source_hours"] == 24
        config.set_source_window_hours(8)
        _, stats = self._run(monkeypatch, tmp_path, True, False)
        assert stats[0]["source_hours"] == 8


class TestModelScoreIsTakenBeforeThePenalty:
    def test_node_score_remembers_what_the_model_returned(self, monkeypatch):
        old = (datetime.now(timezone.utc) - timedelta(days=main._GHOST_AGE_DAYS + 5)).isoformat()
        stale = _job(id="stale", score=62, posted_at=old)
        fresh = _job(id="fresh", score=40, posted_at=datetime.now(timezone.utc).isoformat())
        ruled = _job(id="ruled", score=0, disqualified_category="location",
                     reason="[Pre-screened] outside Germany")
        for j in (stale, fresh, ruled):
            j.pop("_model_score")
        monkeypatch.setattr(main, "score_jobs", lambda jobs: jobs)
        monkeypatch.setattr(main, "_RUN_AUDIT", [])
        main.node_score({"new_jobs": [stale, fresh, ruled]})
        assert stale["_model_score"] == 62 and stale["score"] == 62 - main._GHOST_PENALTY
        assert fresh["_model_score"] == 40 and fresh["score"] == 40
        assert "_model_score" not in ruled

    def test_the_prompt_shows_exactly_the_recorded_number_of_characters(self):
        block = scorer._format_job_block([{"title": "T", "company": "C", "location": "L",
                                           "description": "x" * 5000}])
        assert block.count("x") == scorer.SNIPPET_CHARS


# -- the second run of the day ---------------------------------------------------
NORMAL_DAY = date(2026, 11, 17)


class TestSourceWindow:
    def test_the_normal_window_is_unchanged(self):
        assert config.max_posting_age_hours(NORMAL_DAY) == 24
        assert not config.source_window_narrowed()

    def test_a_narrowed_window_is_what_the_sources_are_asked_for(self):
        config.set_source_window_hours(8)
        assert config.source_window_narrowed()
        assert config.max_posting_age_hours(NORMAL_DAY) == 8
        assert scrapers._max_age_hours() == 8

    def test_it_is_never_wider_than_the_normal_window_nor_under_an_hour(self):
        config.set_source_window_hours(40)
        assert config.max_posting_age_hours(NORMAL_DAY) == 24
        config.set_source_window_hours(0)
        assert config.max_posting_age_hours(NORMAL_DAY) == 1

    def test_none_restores_the_normal_window(self):
        config.set_source_window_hours(8)
        config.set_source_window_hours(None)
        assert config.max_posting_age_hours(NORMAL_DAY) == 24
        assert not config.source_window_narrowed()

    def test_a_catch_up_day_keeps_its_week(self):
        config.set_source_window_hours(8)
        assert config.max_posting_age_hours(config.CATCHUP_UNTIL - timedelta(days=1)) == 168

    def test_the_filter_cap_is_not_touched(self):
        config.set_source_window_hours(8)
        assert config.max_filter_age_hours(NORMAL_DAY) == 48

    @pytest.mark.parametrize("value,expected", [
        (None, False), ("", False), ("off", False), ("24", False),
        ("since_last_run", True), (" Since_Last_Run ", True)])
    def test_the_switch(self, monkeypatch, value, expected):
        if value is not None:
            monkeypatch.setenv("JOBHUNTER_SOURCE_WINDOW", value)
        assert config.since_last_run_requested() is expected


class TestQueriesFollowTheWindow:
    def test_internship_queries_drop_the_week_long_sweep(self):
        config.set_source_window_hours(8)
        assert scrapers._query_hours_old("Praktikum Data Science") == 8
        assert scrapers._query_hours_old("Machine Learning Intern") == 8

    def test_other_queries_ask_for_the_same_hours(self):
        config.set_source_window_hours(8)
        assert scrapers._query_hours_old("Werkstudent AI") == 8

    def test_without_the_window_internships_still_look_back_a_week(self):
        assert scrapers._query_hours_old("Praktikum Data Science") == config.INTERNSHIP_SOURCE_HOURS

    def test_the_employment_agency_is_asked_for_its_smallest_window(self):
        config.set_source_window_hours(8)
        assert scrapers._ba_window_days() == 1


def _line(end: datetime, seconds=2400.0, **kw):
    """A run-stats line for a run that ended at `end` and took `seconds`."""
    d = {"ts": end.isoformat(timespec="seconds"), "email_ok": True,
         "phases": {"scrape": seconds - 300.0, "filter": 100.0, "score": 190.0, "notify": 10.0}}
    d.update(kw)
    return d


NOW = datetime(2026, 11, 17, 16, 0, 0, tzinfo=timezone.utc)          # 17:00 in Berlin
MORNING_END = datetime(2026, 11, 17, 9, 42, 33, tzinfo=timezone.utc)  # started 09:02:33 UTC


class TestSinceLastRun:
    def test_the_start_is_the_end_minus_the_phase_times(self):
        start = main._previous_run_start([_line(MORNING_END)])
        assert start == MORNING_END - timedelta(seconds=2400)

    def test_the_morning_run_gives_an_eight_hour_window_at_five(self):
        # 09:02:33 to 16:00 UTC is 6 h 57 min: rounded up to 7, plus one of slack
        assert main._since_last_run_hours([_line(MORNING_END)], now=NOW) == 8

    def test_the_newest_delivered_run_decides(self):
        older = _line(MORNING_END - timedelta(days=1))
        assert main._since_last_run_hours([older, _line(MORNING_END)], now=NOW) == 8

    def test_a_run_whose_digest_was_not_delivered_is_skipped(self):
        yesterday = _line(MORNING_END - timedelta(days=1))
        failed = _line(MORNING_END, email_ok=False)
        hours = main._since_last_run_hours([yesterday, failed], now=NOW)
        assert hours == 32                                  # 30 h 57 min, up, plus one
        config.set_source_window_hours(hours)
        assert config.max_posting_age_hours(NORMAL_DAY) == 24   # never wider than normal

    def test_a_line_without_phase_times_counts_from_its_timestamp(self):
        line = {"ts": MORNING_END.isoformat(), "email_ok": True}
        assert main._previous_run_start([line]) == MORNING_END
        assert main._since_last_run_hours([line], now=NOW) == 8     # 6 h 17 min, up, plus one

    def test_a_timestamp_without_a_zone_is_read_as_utc(self):
        line = {"ts": "2026-11-17T09:42:33", "phases": {}}
        assert main._previous_run_start([line]) == MORNING_END

    def test_an_unreadable_line_is_passed_over(self):
        broken = {"ts": "not a time", "email_ok": True}
        assert main._since_last_run_hours([_line(MORNING_END), broken], now=NOW) == 8

    def test_a_run_started_minutes_ago_still_asks_for_two_hours(self):
        just_now = _line(NOW - timedelta(minutes=1), seconds=600.0)
        assert main._since_last_run_hours([just_now], now=NOW) == main._MIN_SOURCE_HOURS == 2

    @pytest.mark.parametrize("history", [[], None, [{"email_ok": False, "ts": "2026-11-17T09:42:33+00:00"}],
                                         [{"ts": "not a time"}]])
    def test_no_usable_history_gives_no_window(self, history):
        assert main._since_last_run_hours(history, now=NOW) is None

    def test_a_run_dated_in_the_future_gives_no_window(self):
        ahead = _line(NOW + timedelta(hours=3))
        assert main._since_last_run_hours([ahead], now=NOW) is None

    @pytest.mark.parametrize("phases", [
        ["scrape", 2400], "2400", 2400, None,
        {"scrape": float("nan")}, {"scrape": float("inf")}, {"scrape": -5.0},
        {"scrape": True}, {"scrape": "2400"}, {"scrape": 10 ** 9}])
    def test_phase_times_that_are_not_seconds_count_as_none(self, phases):
        line = {"ts": MORNING_END.isoformat(), "email_ok": True, "phases": phases}
        assert main._previous_run_start([line]) == MORNING_END          # and no exception

    def test_a_history_entry_that_is_not_a_record_is_passed_over(self):
        assert main._since_last_run_hours([_line(MORNING_END), "junk", 7, None], now=NOW) == 8


class TestScrapeAppliesTheWindow:
    def _scrape(self, monkeypatch, history, env=None):
        asked = []
        if env is not None:
            monkeypatch.setenv("JOBHUNTER_SOURCE_WINDOW", env)
        monkeypatch.setattr(main, "load_seen", lambda: {})
        monkeypatch.setattr(main, "_load_run_history", lambda: history)
        monkeypatch.setattr(main, "_load_source_registry", lambda: {})
        monkeypatch.setattr(main, "_absent_source_warnings_from", lambda registry, counts: [])
        monkeypatch.setattr(main, "_quiet_today", lambda registry, counts: [])

        def fake_scrape_all():
            asked.append((config.max_posting_age_hours(NORMAL_DAY),
                          scrapers._query_hours_old("Praktikum Data Science")))
            return []
        monkeypatch.setattr(main, "scrape_all", fake_scrape_all)
        out = main.node_scrape({})
        return asked[0], out["run_notes"]

    def _morning(self):
        # ended 5 h 50 min ago after a 40-minute run: started 6 h 30 min ago
        return [_line(datetime.now(timezone.utc) - timedelta(hours=5, minutes=50))]

    def test_the_second_run_asks_for_the_hours_since_the_morning(self, monkeypatch):
        (window, internship), notes = self._scrape(monkeypatch, self._morning(), "since_last_run")
        assert (window, internship) == (8, 8)
        assert any("Second run of the day" in n and "8 hours" in n for n in notes)

    def test_without_the_switch_nothing_changes(self, monkeypatch):
        (window, internship), notes = self._scrape(monkeypatch, self._morning())
        assert (window, internship) == (24, config.INTERNSHIP_SOURCE_HOURS)
        assert not any("Second run" in n for n in notes)

    def test_without_a_completed_run_the_normal_window_is_used(self, monkeypatch, capsys):
        (window, internship), notes = self._scrape(monkeypatch, [], "since_last_run")
        assert (window, internship) == (24, config.INTERNSHIP_SOURCE_HOURS)
        assert not any("Second run" in n for n in notes)
        assert "normal window" in capsys.readouterr().out

    def test_a_failure_while_working_out_the_window_does_not_stop_the_run(self, monkeypatch, capsys):
        def broken(history, now=None):
            raise OverflowError("date value out of range")
        monkeypatch.setattr(main, "_since_last_run_hours", broken)
        (window, internship), notes = self._scrape(monkeypatch, self._morning(), "since_last_run")
        assert (window, internship) == (24, config.INTERNSHIP_SOURCE_HOURS)
        assert not any("Second run" in n for n in notes)
        assert "OverflowError" in capsys.readouterr().out

    def test_a_window_left_over_from_an_earlier_run_is_cleared(self, monkeypatch):
        config.set_source_window_hours(3)
        (window, _), _ = self._scrape(monkeypatch, self._morning())
        assert window == 24

    def test_a_catch_up_day_ignores_the_switch(self, monkeypatch):
        monkeypatch.setattr(config, "is_catchup", lambda today=None: True)
        (window, _), notes = self._scrape(monkeypatch, self._morning(), "since_last_run")
        assert window == 168 and not config.source_window_narrowed()
        assert not any("Second run" in n for n in notes)
