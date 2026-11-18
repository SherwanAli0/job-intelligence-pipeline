"""LinkedIn read completely, without bursts (2026-11-18).

The run of 18 November 10:00 read 2 of 95 LinkedIn searches as far as
planned. LinkedIn refused seven times; after every refusal the lane's pause
grew by half and it never shrank again, so from minute 22 on the lane asked
once every 12 seconds. And the job texts were fetched two at a time between
the search pages: bursts, which is what LinkedIn refuses.

Now the pause steps back down after 20 answers in a row (never below the
normal pause), and the lane fetches the job texts one at a time with its own
pause. "Werkstudent" and "Praktikum" are read to their end for all of Germany.
And LinkedIn is also read on its own between the email runs; what such a read
finds waits in li_inbox for the next email run.
"""
import json
import math
import re
import threading
from datetime import timedelta

import pytest

import config
import li_inbox
import main
import scrapers
import storage
import sweep
from test_full_daily_sweep import (FakeLinkedIn, NOW, NRW, _clean_state, _marks, _saved,  # noqa: F401
                                   lane)


class TestThePauseComesBackDown:
    def test_twenty_answers_after_a_refusal_bring_the_pause_one_step_down(self, lane):
        fake = FakeLinkedIn(Werkstudent__nrw=300)
        fake.answers[2] = (429, "")
        l = lane(fake=fake)
        assert len(l.run()) == 300
        assert l.sleeps[:2] == [4.0, 60]
        assert l.sleeps[2:21] == [6.0] * 19                    # slower after the refusal ...
        assert l.sleeps[21] == 4.0                              # ... and back after 20 answers
        report = scrapers.LINKEDIN_REPORT
        assert (report["pace_top"], report["pace_end"], report["eased"]) == (6.0, 4.0, 1)

    def test_the_pause_never_goes_below_the_normal_pause(self, lane):
        fake = FakeLinkedIn(Werkstudent__nrw=390)
        fake.answers[2] = (429, "")
        l = lane(fake=fake)
        l.run()
        assert min(l.sleeps) == 4.0
        assert l.sleeps[21:] == [4.0] * len(l.sleeps[21:])
        assert scrapers.LINKEDIN_REPORT["eased"] == 1

    def test_each_refusal_needs_its_own_twenty_answers(self, lane, monkeypatch):
        fake = FakeLinkedIn(Werkstudent__nrw=500)
        fake.answers.update({2: (429, ""), 4: (429, "")})
        l = lane(fake=fake)
        monkeypatch.setattr(scrapers, "_LI_LIST_LIMIT", 1000)
        assert len(l.run()) == 500
        assert l.sleeps[:5] == [4.0, 60, 6.0, 60, 9.0]
        assert l.sleeps[4:23] == [9.0] * 19
        assert l.sleeps[23:43] == [6.0] * 20                   # one step down ...
        assert l.sleeps[43] == 4.0                              # ... and the next one
        report = scrapers.LINKEDIN_REPORT
        assert (report["pace_top"], report["pace_end"], report["eased"]) == (9.0, 4.0, 2)

    def test_a_refusal_before_the_twenty_starts_the_count_again(self, lane, monkeypatch):
        fake = FakeLinkedIn(Werkstudent__nrw=450)
        fake.answers.update({2: (429, ""), 15: (429, "")})
        l = lane(fake=fake)
        monkeypatch.setattr(scrapers, "_LI_LIST_LIMIT", 1000)
        l.run()
        # 12 answers at 6 s, then the second refusal: 9 s, and 20 answers before 6 s again
        assert l.sleeps[1] == 60 and l.sleeps[2:14] == [6.0] * 12
        assert l.sleeps[14] == 60
        assert l.sleeps[15:34] == [9.0] * 19 and l.sleeps[34] == 6.0

    def test_without_a_refusal_nothing_changes(self, lane):
        l = lane(fake=FakeLinkedIn(Werkstudent__nrw=250))
        l.run()
        assert set(l.sleeps) == {4.0}
        report = scrapers.LINKEDIN_REPORT
        assert (report["pace_top"], report["pace_end"], report["eased"]) == (4.0, 4.0, 0)

    def test_the_settings(self):
        assert (scrapers._LI_SLOWER, scrapers._LI_PACE_MAX, scrapers._LI_EASE_AFTER) == (1.5, 12.0, 20)
        assert scrapers._LI_PACE <= scrapers._LI_PACE_MAX

    def test_the_log_line_says_how_the_pause_went(self, lane, capsys):
        fake = FakeLinkedIn(Werkstudent__nrw=300)
        fake.answers[2] = (429, "")
        lane(fake=fake).run()
        out = capsys.readouterr().out
        assert "1 refused (waited 60s; pause up to 6.0s, 4.0s at the end, 1 steps back down)" in out


class TestTheJobTextsComeOneAtATime:
    def test_the_lane_asks_for_one_at_a_time_with_its_own_pause(self, lane):
        l = lane(fake=FakeLinkedIn(Werkstudent__nrw=45))
        l.run()
        assert l.describe_settings and all(kw == {"linkedin_workers": 1, "linkedin_pause": 4.0}
                                           for kw in l.describe_settings)

    def test_after_a_refusal_the_texts_wait_as_long_as_the_pages(self, lane, monkeypatch):
        monkeypatch.setattr(scrapers, "_LI_PUBLISH_EVERY", 15)
        fake = FakeLinkedIn(Werkstudent__nrw=45)
        fake.answers[2] = (429, "")
        l = lane(fake=fake)
        l.run()
        pauses = [kw["linkedin_pause"] for kw in l.describe_settings]
        assert pauses[0] == 6.0                                 # the first batch came after the refusal

    def test_the_texts_stop_when_the_lanes_time_is_up(self, lane, monkeypatch):
        l = lane(fake=FakeLinkedIn(Werkstudent__nrw=45))
        monkeypatch.setattr(scrapers, "_LI_SECONDS", 250)
        asked = []

        def slow_text(jobs, **kw):                     # each text takes 100 s of the lane's time
            asked.extend(j["url"] for j in jobs)
            l.clock[0] += 100
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions", slow_text)
        jobs = l.run()
        report = scrapers.LINKEDIN_REPORT
        assert len(asked) == 3                          # 12 s of pages, then 112, 212, 312 > 250
        assert report["texts_left"] == 37 and report["halt"] == "time"
        assert len(jobs) == 40                          # the postings are kept, without a text

    def test_the_real_helper_fetches_one_after_the_other_with_that_pause(self, monkeypatch):
        sleeps, threads = [], []
        monkeypatch.setattr(scrapers.time, "sleep", sleeps.append)

        def fetch(url):
            threads.append(threading.current_thread().name)
            return "text " * 200
        monkeypatch.setattr(scrapers, "_fetch_full_description", fetch)
        jobs = [scrapers.job(f"Werkstudent Data {i}", "Acme", "Bonn",
                             f"https://www.linkedin.com/jobs/view/83000000{i}", "linkedin") for i in range(3)]
        scrapers._enrich_jobspy_descriptions(jobs, quiet=True, stats=[0, 0, 0],
                                             linkedin_workers=1, linkedin_pause=7.5)
        assert sleeps == [7.5, 7.5, 7.5]
        assert set(threads) == {threading.current_thread().name}     # no thread pool
        assert all(len(j["description"]) > 400 for j in jobs)

    def test_other_callers_keep_the_two_worker_slow_lane(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(scrapers.time, "sleep", sleeps.append)
        monkeypatch.setattr(scrapers, "_fetch_full_description", lambda url: "text " * 200)
        jobs = [scrapers.job(f"Werkstudent Data {i}", "Acme", "Bonn",
                             f"https://www.linkedin.com/jobs/view/84000000{i}", "linkedin") for i in range(2)]
        seen = {}
        real = scrapers._parallel_collect

        def spy(items, fn, label="", workers=None):
            seen[label] = workers
            return real(items, fn, label, workers=workers)
        monkeypatch.setattr(scrapers, "_parallel_collect", spy)
        scrapers._enrich_jobspy_descriptions(jobs, quiet=True, stats=[0, 0, 0])
        assert seen["desc-linkedin"] == scrapers._LINKEDIN_SLOW_LANE_WORKERS == 2
        assert sleeps == [0.5, 0.5]


# -- the lists for all of Germany --------------------------------------------------
class TestTheListsForAllOfGermany:
    def test_a_list_for_all_of_germany_runs_to_linkedins_limit_and_counts_as_read(self, lane, tmp_path):
        l = lane(fake=FakeLinkedIn(Werkstudent=1300))
        assert len(l.run(save=True)) == 1000
        report = scrapers.LINKEDIN_REPORT
        assert report["pools"]["de|werkstudent"] == {"read": 1000, "hours": 3, "status": "limit"}
        assert report["cut_short"] == 0
        assert _saved(tmp_path)["linkedin|de|werkstudent"]["covered"] == NOW.isoformat(timespec="seconds")
        # ... and the digest says what was not read
        assert any("'werkstudent' reached LinkedIn's limit of 1000 postings for 3 hours" in w
                   for w in scrapers.linkedin_lane_warnings())

    def test_a_list_for_nrw_still_stops_at_its_own_limit(self, lane):
        lane(fake=FakeLinkedIn(Werkstudent__nrw=1200)).run()
        assert scrapers.LINKEDIN_REPORT["pools"]["nrw|werkstudent"]["status"] == "long"
        assert not [w for w in scrapers.linkedin_lane_warnings() if "limit of" in w]

    def test_the_first_read_asks_for_three_hours_then_for_the_time_since(self, lane):
        l = lane(fake=FakeLinkedIn(default=3))
        l.run(now=NOW, save=True)
        assert {c[:2]: c[3] for c in l.fake.calls}[("Werkstudent", "Germany")] == 3
        l.run(now=NOW + timedelta(hours=4), save=True)
        assert {c[:2]: c[3] for c in l.fake.calls}[("Werkstudent", "Germany")] == 4 + sweep.SLACK_HOURS


# -- part-time (2026-11-18) -----------------------------------------------------------
class TestPartTime:
    def test_no_list_for_teilzeit_but_searches_that_pair_it_with_tech_work(self):
        # "Teilzeit" in NRW: more than 118 postings in one hour, 5 with tech work in
        # the title, none with both: a list would spend LinkedIn's allowance on nothing.
        plan = scrapers._linkedin_plan()
        assert not [s for s in plan if s["pool"] and ("teilzeit" in s["key"] or "part" in s["key"])]
        nrw = {s["words"]: s for s in plan if s["key"].startswith("linkedin|nrwq|")}
        assert set(nrw) == set(scrapers._LI_NRW_QUERIES) and len(nrw) == 7
        assert all(s["location"] == NRW and s["stage"] == 2 and s["depth"] == scrapers._LI_DEPTH
                   for s in nrw.values())
        germany = {s["words"]: s["stage"] for s in plan if s["key"].startswith("linkedin|q|")}
        for words in scrapers._LI_PART_TIME_QUERIES + ("Teilzeit Data", "Part-time Data Analyst"):
            assert germany[words] == 2, words

    def test_every_part_time_search_names_part_time_and_tech_work(self):
        for words in scrapers._LI_NRW_QUERIES + scrapers._LI_PART_TIME_QUERIES:
            assert re.search(r"teilzeit|part-time", words, re.IGNORECASE), words
            assert main._RE_PART_TIME_TECH.search(words), words

    def test_an_nrw_search_is_asked_for_nrw_and_read_five_pages_deep(self, lane, monkeypatch, tmp_path):
        fake = FakeLinkedIn(default=0)
        fake.set("Teilzeit Data", NRW, 80)
        l = lane(fake=fake)
        monkeypatch.setattr(scrapers, "_LI_NRW_QUERIES", ("Teilzeit Data",))
        jobs = l.run(save=True)
        assert fake.starts("Teilzeit Data", NRW) == [0, 10, 20, 30, 40]
        assert ("Teilzeit Data", "Germany") not in {c[:2] for c in fake.calls}
        assert len([j for j in jobs if j["title"].startswith("Teilzeit Data")]) == 50
        assert "linkedin|nrwq|teilzeit data" in _saved(tmp_path)


# -- the postings that wait between the reads --------------------------------------
def _row(n, text="", **extra):
    row = scrapers.job(f"Werkstudent Data {n}", "Acme GmbH", "Berlin, Germany",
                       f"https://www.linkedin.com/jobs/view/9100000{n:02d}", "linkedin", description=text)
    row.update(extra)
    return row


class TestTheInbox:
    def test_what_is_kept_comes_back(self):
        rows = [_row(1, "x" * 500), _row(2)]
        assert li_inbox.save([], rows, now=NOW) == 2
        back = li_inbox.load(now=NOW + timedelta(hours=1))
        assert [r["id"] for r in back] == [r["id"] for r in rows]
        assert back[0]["description"] == "x" * 500
        assert {r["_collected"] for r in back} == {NOW.isoformat(timespec="seconds")}

    def test_seen_postings_are_left_out_when_kept_and_when_read(self):
        a, b = _row(1), _row(2)
        li_inbox.save([], [a, b], skip_ids={a["id"]}, now=NOW)
        assert [r["id"] for r in li_inbox.load(now=NOW)] == [b["id"]]
        assert li_inbox.load(skip_ids={b["id"]}, now=NOW) == []

    def test_a_posting_first_found_more_than_three_days_ago_is_dropped(self):
        li_inbox.save([], [_row(1)], now=NOW)
        assert li_inbox.load(now=NOW + timedelta(hours=71)) != []
        assert li_inbox.load(now=NOW + timedelta(hours=73)) == []
        assert li_inbox.save(li_inbox.load(now=NOW), [], now=NOW + timedelta(hours=73)) == 0

    def test_a_posting_found_again_keeps_its_first_time_and_the_longer_text(self):
        li_inbox.save([], [_row(1, "short")], now=NOW)
        li_inbox.save(li_inbox.load(now=NOW), [_row(1, "y" * 800)], now=NOW + timedelta(hours=5))
        (row,) = li_inbox.load(now=NOW + timedelta(hours=5))
        assert row["_collected"] == NOW.isoformat(timespec="seconds")
        assert row["description"] == "y" * 800

    def test_the_board_of_a_list_card_is_kept(self):
        li_inbox.save([], [_row(1, _board="linkedin|de|werkstudent")], now=NOW)
        assert li_inbox.load(now=NOW)[0]["_board"] == "linkedin|de|werkstudent"

    def test_broken_lines_and_rows_without_a_time_are_skipped(self):
        good = dict(_row(1), _collected=NOW.isoformat())
        text = "\n".join(["not json", json.dumps({"id": "", "url": ""}), json.dumps(good), "[1, 2]",
                          json.dumps(_row(2))]) + "\n"
        storage.write_text(li_inbox.INBOX_FILE, text)
        assert [r["id"] for r in li_inbox.load(now=NOW)] == [good["id"]]

    def test_a_row_whose_id_or_link_is_not_text_is_skipped(self):
        good = dict(_row(1), _collected=NOW.isoformat())
        rows = [dict(good, id=["x"]), dict(good, id=7), dict(good, url={"a": 1}), good]
        storage.write_text(li_inbox.INBOX_FILE, "".join(json.dumps(r) + "\n" for r in rows))
        assert [r["id"] for r in li_inbox.load(now=NOW)] == [good["id"]]
        assert li_inbox.save(rows, [dict(good, id=None)], now=NOW) == 1

    def test_fields_that_are_not_text_are_made_text(self):
        odd = dict(_row(1), _collected=NOW.isoformat(), description=5, title=None, source=3)
        storage.write_text(li_inbox.INBOX_FILE, json.dumps(odd) + "\n")
        (row,) = li_inbox.load(now=NOW)
        assert (row["description"], row["title"], row["source"]) == ("", "", "linkedin")
        # found again with a text: no error, and the text is kept
        assert li_inbox.save([dict(odd)], [_row(1, "t" * 500)], now=NOW) == 1
        assert li_inbox.load(now=NOW)[0]["description"] == "t" * 500

    def test_a_file_that_cannot_be_read_gives_nothing_and_does_not_stop_the_run(self, monkeypatch, capsys):
        def broken(name):
            raise storage.StorageUnavailable("down")
        monkeypatch.setattr(storage, "read_text", broken)
        assert li_inbox.load() == []
        assert li_inbox.LOAD_FAILED is True
        assert "could not be read (StorageUnavailable)" in capsys.readouterr().out

    def test_the_email_says_so_and_the_postings_follow_later(self, lane, monkeypatch):
        l = lane(fake=FakeLinkedIn())
        real = storage.read_text

        def broken(name):
            raise storage.StorageUnavailable("down")
        monkeypatch.setattr(storage, "read_text", broken)
        l.run()
        assert scrapers.LINKEDIN_REPORT["inbox_unreadable"] is True
        assert any("could not be read in this run" in w for w in scrapers.linkedin_lane_warnings())
        monkeypatch.setattr(storage, "read_text", real)
        li_inbox.load()
        assert li_inbox.LOAD_FAILED is False

    def test_a_write_that_fails_says_none(self, monkeypatch, capsys):
        def broken(name, text):
            raise OSError("full")
        monkeypatch.setattr(storage, "write_text", broken)
        assert li_inbox.save([], [_row(1)], now=NOW) is None
        assert "could not be kept" in capsys.readouterr().out


class TestAnEmailRunTakesThemIn:
    @pytest.fixture(autouse=True)
    def _clock(self, monkeypatch):
        monkeypatch.setattr(li_inbox, "_now", lambda: NOW)

    def test_the_waiting_postings_come_first_and_count_in_the_note(self, lane):
        waiting = [_row(1, "x" * 500), _row(2, "x" * 500)]
        li_inbox.save([], waiting, now=NOW)
        jobs = lane(fake=FakeLinkedIn(Werkstudent__nrw=3)).run()
        assert [j["id"] for j in jobs[:2]] == [w["id"] for w in waiting]
        assert len(jobs) == 5
        report = scrapers.LINKEDIN_REPORT
        assert (report["waiting"], report["kept"]) == (2, 3)
        assert "2 postings came from the LinkedIn reads in between" in scrapers.linkedin_lane_note()

    def test_a_waiting_posting_found_again_is_not_taken_twice(self, lane):
        fake = FakeLinkedIn()
        fake.set("Werkstudent", NRW, [(910000001, "Werkstudent Data 1"), (910000002, "Werkstudent Data 2")])
        li_inbox.save([], [_row(1, "x" * 500)], now=NOW)
        jobs = lane(fake=fake).run()
        urls = [j["url"] for j in jobs]
        assert len(urls) == len(set(urls)) == 2
        assert jobs[0]["description"] == "x" * 500

    def test_seen_postings_are_not_taken_in(self, lane, monkeypatch):
        a, b = _row(1), _row(2)
        li_inbox.save([], [a, b], now=NOW)
        l = lane(fake=FakeLinkedIn())
        monkeypatch.setattr(scrapers, "KNOWN_JOB_IDS", {a["id"]})
        assert [j["id"] for j in l.run()] == [b["id"]]

    def test_a_linkedin_only_read_does_not_take_them_in(self, lane):
        li_inbox.save([], [_row(1)], now=NOW)
        lane(fake=FakeLinkedIn())
        sweep.begin(NOW)
        assert scrapers.scrape_linkedin(with_inbox=False) == []
        assert scrapers.LINKEDIN_REPORT["waiting"] == 0

    def test_the_partial_result_holds_them_from_the_start(self, lane, monkeypatch):
        li_inbox.save([], [_row(1)], now=NOW)
        sizes = []
        l = lane(fake=FakeLinkedIn(Werkstudent__nrw=45))
        original = l._describe

        def describe(jobs, **kw):
            sizes.append(len(scrapers._LI_SWEEP_PARTIAL))
            original(jobs, **kw)
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions", describe)
        l.run()
        assert sizes[0] == 1                 # handed over before the first page was


# -- a read of LinkedIn alone, between the email runs -------------------------------
class TestALinkedInOnlyRead:
    @pytest.fixture
    def read(self, monkeypatch, tmp_path):
        calls = {}
        monkeypatch.setattr(sweep, "_now", lambda: NOW)
        monkeypatch.setattr(li_inbox, "_now", lambda: NOW)
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
        monkeypatch.setattr(main, "_LINKEDIN_READS_FILE", str(tmp_path / "linkedin_reads.jsonl"))
        monkeypatch.setattr(main, "load_seen", lambda: {"seen-1": "2026-11-17"})
        found = [_row(5), _row(6, "z" * 600)]

        def fake_lane(with_inbox=True):
            calls["with_inbox"] = with_inbox
            calls["window"] = config.max_posting_age_hours()
            calls["known"] = set(scrapers.KNOWN_JOB_IDS)
            calls["title_gate"] = scrapers.TITLE_IS_TECH
            sweep.record("linkedin|nrw|werkstudent", True, 7)
            scrapers.LINKEDIN_REPORT.update({"recorded": True, "searches": 1})
            return list(found)
        monkeypatch.setattr(scrapers, "scrape_linkedin", fake_lane)
        return calls, found

    def test_it_reads_linkedin_alone_and_keeps_what_it_found(self, read):
        calls, found = read
        assert main.read_linkedin_only() == {"found": 2, "kept": 2}
        assert calls["with_inbox"] is False and calls["title_gate"] is not None
        assert [r["id"] for r in li_inbox.load(now=NOW)] == [r["id"] for r in found]

    def test_the_keyword_searches_ask_for_the_time_since_linkedin_was_last_read(self, read, tmp_path):
        calls, _ = read
        _marks(tmp_path, linkedin__nrw__werkstudent=4.5)
        main.read_linkedin_only()
        assert calls["window"] == math.ceil(4.5) + sweep.SLACK_HOURS == 7

    def test_without_an_earlier_read_the_normal_window(self, read):
        calls, _ = read
        main.read_linkedin_only()
        assert calls["window"] == 24

    def test_no_text_is_fetched_again_for_seen_or_waiting_postings(self, read):
        calls, _ = read
        li_inbox.save([], [_row(1, "x" * 500), _row(2, "short")], now=NOW)
        main.read_linkedin_only()
        assert calls["known"] == {"seen-1", _row(1)["id"]}

    def test_postings_seen_since_leave_the_file(self, read, monkeypatch):
        _, found = read
        li_inbox.save([], [_row(1)], now=NOW)
        monkeypatch.setattr(main, "load_seen", lambda: {_row(1)["id"]: "2026-11-18"})
        assert main.read_linkedin_only()["kept"] == 2
        assert [r["id"] for r in li_inbox.load(now=NOW)] == [r["id"] for r in found]

    def test_the_marks_move_and_the_read_is_recorded(self, read, tmp_path):
        main.read_linkedin_only()
        assert _saved(tmp_path)["linkedin|nrw|werkstudent"]["covered"] == NOW.isoformat(timespec="seconds")
        (line,) = (tmp_path / "linkedin_reads.jsonl").read_text(encoding="utf-8").splitlines()
        assert (json.loads(line)["found"], json.loads(line)["waiting"]) == (2, 2)

    def test_when_the_postings_cannot_be_kept_the_marks_stay(self, read, tmp_path, monkeypatch):
        monkeypatch.setattr(li_inbox, "save", lambda *a, **k: None)
        assert main.read_linkedin_only()["kept"] is None
        assert not (tmp_path / "search_marks.json").exists()

    def test_when_the_waiting_postings_cannot_be_read_it_stops_and_overwrites_nothing(
            self, read, tmp_path, monkeypatch, capsys):
        calls, _ = read
        li_inbox.save([], [_row(1)], now=NOW)
        before = (tmp_path / "linkedin_inbox.jsonl").read_text(encoding="utf-8")
        real = storage.read_text

        def flaky(name):
            if name == li_inbox.INBOX_FILE:
                raise storage.StorageUnavailable("down")
            return real(name)
        monkeypatch.setattr(storage, "read_text", flaky)
        assert main.read_linkedin_only() == {"found": 0, "kept": None}
        assert "with_inbox" not in calls                       # LinkedIn was not asked
        assert (tmp_path / "linkedin_inbox.jsonl").read_text(encoding="utf-8") == before
        assert not (tmp_path / "search_marks.json").exists()
        assert "this read stops before asking LinkedIn" in capsys.readouterr().out

    def test_a_dry_run_keeps_nothing_and_moves_no_mark(self, read, tmp_path):
        assert main.read_linkedin_only(dry_run=True) == {"found": 2, "kept": None}
        assert li_inbox.load(now=NOW) == []
        assert not (tmp_path / "search_marks.json").exists()
        assert not (tmp_path / "linkedin_reads.jsonl").exists()


class TestTheModeSwitch:
    @pytest.fixture
    def handler(self, monkeypatch):
        import handler as _handler
        calls, claims = [], []
        self.held = {}                                   # claim name -> seconds since it was taken
        self.sleeps = []
        monkeypatch.setattr(main, "read_linkedin_only", lambda dry_run=False: calls.append(("linkedin", dry_run)))
        monkeypatch.setattr(main, "main", lambda dry_run=False: calls.append(("full", dry_run)))
        monkeypatch.setattr(storage, "claim", lambda name, *a, **k: claims.append(name) or True)
        monkeypatch.setattr(storage, "release", lambda name: None)

        def read_text(name):
            age = self.held.get(name.removesuffix(".claim"))
            return None if age is None else json.dumps({"claimed_at": _handler.time.time() - age, "ttl": 14400})
        monkeypatch.setattr(storage, "read_text", read_text)
        monkeypatch.setattr(_handler.time, "sleep", self.sleeps.append)
        return _handler, calls, claims

    def test_linkedin_mode_reads_linkedin_alone(self, handler):
        h, calls, claims = handler
        assert h._run(mode="linkedin")["status"] == "ok"
        assert calls == [("linkedin", False)] and claims == ["run-linkedin"]

    def test_the_default_is_the_email_run(self, handler):
        h, calls, claims = handler
        assert h._run()["status"] == "ok"
        assert calls == [("full", False)] and claims == ["run-full"]

    def test_it_skips_itself_while_an_email_run_reads(self, handler):
        h, calls, claims = handler
        self.held["run-full"] = 600
        result = h._run(mode="linkedin")
        assert result["status"] == "skipped" and calls == [] and claims == []

    def test_a_claim_left_over_from_a_crash_does_not_stop_it(self, handler):
        h, calls, claims = handler
        self.held["run-full"] = 5 * 3600
        assert h._run(mode="linkedin")["status"] == "ok" and calls == [("linkedin", False)]

    def test_an_email_run_waits_for_a_linkedin_only_read_to_finish(self, handler, monkeypatch):
        h, calls, claims = handler
        self.held["run-linkedin"] = 300
        checks = []

        def sleep(seconds):
            self.sleeps.append(seconds)
            checks.append(seconds)
            if len(checks) == 2:
                del self.held["run-linkedin"]              # the read has finished
        monkeypatch.setattr(h.time, "sleep", sleep)
        assert h._run()["status"] == "ok"
        assert self.sleeps == [30, 30] and calls == [("full", False)]

    def test_an_email_run_waits_twenty_minutes_at_most(self, handler, capsys):
        h, calls, claims = handler
        self.held["run-linkedin"] = 300
        assert h._run()["status"] == "ok"
        assert sum(self.sleeps) == 20 * 60 and calls == [("full", False)]
        assert "still running: this run goes on anyway" in capsys.readouterr().out

    def test_an_email_run_does_not_wait_for_a_claim_left_over_from_a_crash(self, handler):
        h, calls, claims = handler
        self.held["run-linkedin"] = 2 * 3600
        assert h._run()["status"] == "ok"
        assert self.sleeps == [] and calls == [("full", False)]

    def test_a_dry_run_neither_waits_nor_skips(self, handler):
        h, calls, claims = handler
        self.held.update({"run-full": 60, "run-linkedin": 60})
        h._run(mode="linkedin", dry_run=True)
        h._run(dry_run=True)
        assert self.sleeps == [] and calls == [("linkedin", True), ("full", True)]

    def test_an_unknown_mode_starts_nothing(self, handler):
        h, calls, claims = handler
        result = h._run(mode="linkdin")
        assert result["status"] == "error" and calls == [] and claims == []

    def test_the_container_takes_the_mode_from_its_environment(self, handler, monkeypatch):
        import runpy
        import signal
        import sys
        h, calls, _ = handler
        monkeypatch.setenv("JOBHUNTER_MODE", "LinkedIn ")
        monkeypatch.delenv("DRY_RUN", raising=False)
        monkeypatch.setattr(sys, "argv", ["handler.py"])
        before = signal.getsignal(signal.SIGTERM)
        try:
            with pytest.raises(SystemExit) as end:
                runpy.run_path(h.__file__, run_name="__main__")
        finally:
            signal.signal(signal.SIGTERM, before)
        assert end.value.code == 0 and calls == [("linkedin", False)]
