"""Reading every search as far as it has to be read (2026-11-17).

The owner's point: with a run every day, the 48-hour freshness rule should
lose nothing. It lost 68 wanted ads in a week. Part of the reason: LinkedIn
was asked by two lanes at once, was refused on 48 of 84 queries in one run,
and each query read the first 50 of several hundred results. LinkedIn is now
read by one paced lane that waits after a refusal, reads its lists of every
student or junior posting to the end, and remembers how far each search got
(sweep.py), so a search that was cut short asks for a longer stretch next time.
"""
import json
import zlib
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

import config
import li_inbox
import main
import scrapers
import sweep

NOW = datetime(2026, 11, 18, 9, 0, 0, tzinfo=timezone.utc)
NRW = scrapers._LI_NRW
EMPTY = "<!DOCTYPE html>\n\n<!---->"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    """Search marks in a temp folder, nothing left over between tests."""
    monkeypatch.setattr(sweep, "MARKS_FILE", str(tmp_path / "search_marks.json"))
    monkeypatch.setattr(li_inbox, "INBOX_FILE", str(tmp_path / "linkedin_inbox.jsonl"))
    monkeypatch.setattr(config, "_SOURCE_HOURS_OVERRIDE", None)
    monkeypatch.delenv("JOBHUNTER_LINKEDIN_LANE", raising=False)
    monkeypatch.delenv("JOBHUNTER_SOURCE_WINDOW", raising=False)
    sweep.begin(NOW)
    scrapers._ABANDONED.clear()
    scrapers.LINKEDIN_REPORT.clear()
    yield
    sweep.begin(NOW)
    scrapers._ABANDONED.clear()
    scrapers.LINKEDIN_REPORT.clear()


def _marks(tmp_path, **marks):
    """Write a marks file: key -> hours ago it was last read completely."""
    data = {key.replace("__", "|"): {"covered": (NOW - timedelta(hours=h)).isoformat(),
                                    "asked": NOW.isoformat()}
            for key, h in marks.items()}
    (tmp_path / "search_marks.json").write_text(json.dumps(data), encoding="utf-8")


def _saved(tmp_path):
    return json.loads((tmp_path / "search_marks.json").read_text(encoding="utf-8"))


# -- the search marks ------------------------------------------------------------
class TestSearchMarks:
    def test_a_search_without_a_mark_is_asked_for_the_normal_window(self):
        assert sweep.window_hours("linkedin|de|werkstudent", 24) == 24
        assert sweep.window_hours("linkedin|de|werkstudent", 9) == 9

    def test_a_search_read_to_its_end_is_asked_for_the_time_since(self, tmp_path):
        sweep.record("a", True, 24, "end")
        sweep.save()
        sweep.begin(NOW + timedelta(hours=7))
        assert sweep.window_hours("a", 24) == 7 + sweep.SLACK_HOURS

    def test_part_hours_round_up(self, tmp_path):
        sweep.record("a", True, 24, "end")
        sweep.save()
        sweep.begin(NOW + timedelta(hours=7, minutes=5))
        assert sweep.window_hours("a", 24) == 8 + sweep.SLACK_HOURS

    def test_a_first_try_that_was_cut_short_keeps_the_whole_stretch(self, tmp_path):
        sweep.record("a", False, 24, "refused")
        sweep.save()
        sweep.begin(NOW + timedelta(hours=7))
        assert sweep.window_hours("a", 24) == 24 + 7 + sweep.SLACK_HOURS

    def test_a_cut_short_search_keeps_its_older_mark(self, tmp_path):
        sweep.record("a", True, 24, "end")
        sweep.save()                                              # read completely at NOW
        sweep.begin(NOW + timedelta(hours=7))
        sweep.record("a", False, 9, "time")
        sweep.save()                                              # cut short 7 h later
        sweep.begin(NOW + timedelta(hours=24))
        assert sweep.window_hours("a", 24) == 24 + sweep.SLACK_HOURS
        sweep.record("a", True, 26, "end")
        sweep.save()                                              # and caught up
        sweep.begin(NOW + timedelta(hours=31))
        assert sweep.window_hours("a", 24) == 7 + sweep.SLACK_HOURS

    def test_the_window_is_never_wider_than_a_week(self, tmp_path):
        _marks(tmp_path, a=24 * 30)
        sweep.begin(NOW)
        assert sweep.window_hours("a", 24) == sweep.MAX_HOURS

    def test_a_mark_in_the_future_asks_for_the_slack_only(self, tmp_path):
        _marks(tmp_path, a=-5)
        sweep.begin(NOW)
        assert sweep.window_hours("a", 24) == sweep.SLACK_HOURS

    def test_a_time_without_a_zone_is_taken_as_utc(self, tmp_path):
        _marks(tmp_path, a=7)
        plain = NOW.replace(tzinfo=None)
        sweep.begin(plain)
        assert sweep.window_hours("a", 24) == 7 + sweep.SLACK_HOURS
        assert sweep.window_hours("a", 24, now=plain + timedelta(hours=3)) == 10 + sweep.SLACK_HOURS
        assert sweep.hours_unread("a", now=plain) == 7.0
        sweep.record("a", False, 9, "time")
        assert sweep.overdue(5, now=plain) == [("a", 7.0)]

    def test_nothing_recorded_means_nothing_written(self, tmp_path):
        sweep.save()
        assert not (tmp_path / "search_marks.json").exists()

    def test_a_run_is_saved_once(self, tmp_path):
        sweep.record("a", True, 24, "end")
        sweep.save()
        (tmp_path / "search_marks.json").unlink()
        sweep.save()
        assert not (tmp_path / "search_marks.json").exists()

    def test_an_unreadable_file_gives_normal_windows(self, monkeypatch, capsys):
        def boom(name):
            raise sweep.storage.StorageUnavailable("s3 down")
        monkeypatch.setattr(sweep.storage, "read_text", boom)
        sweep.begin(NOW)
        assert sweep.window_hours("a", 24) == 24
        assert "normal windows" in capsys.readouterr().out

    @pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '{"a": 5}', '{"a": {"covered": "soon"}}',
                                      '{"a": {"covered": null}}', "null"])
    def test_a_broken_file_gives_normal_windows(self, tmp_path, text):
        (tmp_path / "search_marks.json").write_text(text, encoding="utf-8")
        sweep.begin(NOW)
        assert sweep.window_hours("a", 24) == 24

    def test_a_failed_save_does_not_stop_the_run(self, monkeypatch, capsys):
        def boom(name, text):
            raise OSError("disk full")
        monkeypatch.setattr(sweep.storage, "write_text", boom)
        sweep.record("a", True, 24, "end")
        sweep.save()
        assert "could not save" in capsys.readouterr().out

    def test_a_search_that_is_no_longer_asked_leaves_the_file(self, tmp_path):
        old = (NOW - timedelta(days=sweep.FORGET_DAYS + 1)).isoformat()
        (tmp_path / "search_marks.json").write_text(json.dumps({
            "gone": {"covered": old, "asked": old},
            "kept": {"covered": old, "asked": (NOW - timedelta(days=2)).isoformat()}}), encoding="utf-8")
        sweep.begin(NOW)
        sweep.record("new", True, 24, "end")
        sweep.save()
        assert sorted(_saved(tmp_path)) == ["kept", "new"]

    def test_overdue_names_the_cut_short_searches_past_the_limit(self, tmp_path):
        _marks(tmp_path, old=60, recent=20, fine=60)
        sweep.begin(NOW)
        sweep.record("old", False, 62, "refused")
        sweep.record("recent", False, 22, "refused")
        sweep.record("fine", True, 62, "end")
        sweep.record("first try", False, 72, "time")
        assert sweep.overdue(48) == [("first try", 72.0), ("old", 60.0)]
        assert sweep.overdue(48, prefix="old") == [("old", 60.0)]

    def test_the_summary_counts_by_reason(self):
        sweep.record("x|a", True, 24, "end")
        sweep.record("x|b", False, 24, "refused")
        sweep.record("x|c", False, 24, "refused")
        sweep.record("y|d", False, 24, "time")
        assert sweep.summary("x|") == {"searches": 3, "complete": 1, "cut_short": 2,
                                       "reasons": {"refused": 2}}

    @settings(max_examples=200, deadline=None)
    @given(st.one_of(st.none(), st.text(max_size=30), st.integers(), st.floats(allow_nan=True),
                     st.datetimes().map(lambda d: d.isoformat())),
           st.integers(min_value=-5, max_value=400))
    def test_any_stored_value_gives_a_window_in_range(self, covered, default):
        sweep._marks.clear()
        sweep._marks["k"] = {"covered": covered}
        hours = sweep.window_hours("k", default, now=NOW)
        assert isinstance(hours, int) and 1 <= hours <= sweep.MAX_HOURS

    @pytest.mark.parametrize("default,expected", [(500, 168), (168, 168), (0, 1), (-3, 1), (24.9, 24)])
    def test_the_normal_window_is_kept_in_range_too(self, default, expected):
        assert sweep.window_hours("no mark", default) == expected

    @pytest.mark.parametrize("hours,kept", [(10 ** 9, 168), (-4, 1), ("many", 24), (None, 24),
                                             (float("inf"), 24), (float("nan"), 24), (9, 9)])
    def test_any_number_of_hours_can_be_recorded_and_saved(self, tmp_path, capsys, hours, kept):
        sweep.record("a", False, hours, "refused")
        assert sweep.outcomes()["a"]["hours"] == kept
        sweep.save()                                              # must not raise
        assert "could not save" not in capsys.readouterr().out
        assert _saved(tmp_path)["a"]["covered"] == (NOW - timedelta(hours=kept)).isoformat(timespec="seconds")

    def test_a_save_that_fails_inside_never_raises(self, monkeypatch, capsys):
        sweep.record("a", True, 24, "end")
        monkeypatch.setattr(sweep.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
        sweep.save()
        assert "could not save the search marks (ValueError)" in capsys.readouterr().out


# -- a LinkedIn that answers like the real one ------------------------------------
class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


def _card(number, title, place):
    return (f'<li><div class="base-card"><a class="base-card__full-link" '
            f'href="https://de.linkedin.com/jobs/view/some-title-{number}?position=3">x</a>'
            f'<h3 class="base-search-card__title">{title}</h3>'
            f'<h4 class="base-search-card__subtitle">Acme GmbH</h4>'
            f'<span class="job-search-card__location">{place}</span>'
            f'<time datetime="2026-11-17">2 hours ago</time></div></li>')


class FakeLinkedIn:
    """Ten cards per request, from one list per search (words and location)."""

    def __init__(self, default=0, **sizes):
        self.default = default
        self.lists = {}
        self.calls = []            # (words, location, start, hours)
        self.answers = {}          # request number -> (status, body)
        self.broken = set()        # (words, location) that always answer 500
        self.refuse_from = None    # every request from this number on is refused
        self.fail = set()          # request numbers that raise
        for key, value in sizes.items():
            words, _, where = key.partition("__")
            self.set(words.replace("_", " "), {"nrw": NRW, "": "Germany"}[where], value)

    def set(self, words, location, value):
        if isinstance(value, int):
            base = (zlib.crc32(f"{words}|{location}".encode()) % 9000 + 1000) * 100000
            value = [(base + i, f"{words} {i}") for i in range(value)]
        self.lists[(words, location)] = list(value)

    def get(self, url, params=None, **kw):
        number = len(self.calls) + 1
        words, location, start = params["keywords"], params["location"], params["start"]
        self.calls.append((words, location, start, int(params["f_TPR"][1:]) // 3600))
        if number in self.fail:
            raise ConnectionError("down")
        if number in self.answers:
            return _Resp(*self.answers[number])
        if self.refuse_from is not None and number >= self.refuse_from:
            return _Resp(429, "")
        if (words, location) in self.broken:
            return _Resp(500, "")
        if (words, location) not in self.lists:
            self.set(words, location, self.default)
        place = "Bonn, North Rhine-Westphalia, Germany" if location == NRW else "Berlin, Germany"
        rows = self.lists[(words, location)][start:start + 10]
        return _Resp(200, "".join(_card(n, t, place) for n, t in rows) or EMPTY)

    def starts(self, words, location="Germany"):
        return [c[2] for c in self.calls if c[:2] == (words, location)]


class Lane:
    """The lane with a small plan, a fake LinkedIn and a clock that only moves
    when the lane sleeps. `pools` are list words asked for NRW and for all of
    Germany, `nrw_only` for NRW alone."""

    def __init__(self, monkeypatch, fake=None, pools=("Werkstudent",), nrw_only=(), queries=()):
        self.fake = fake or FakeLinkedIn()
        self.sleeps, self.clock, self.described, self.describe_calls = [], [0.0], [], 0
        monkeypatch.setattr(scrapers, "_LI_POOLS_NRW", tuple(pools) + tuple(nrw_only))
        monkeypatch.setattr(scrapers, "_LI_POOLS_GERMANY", tuple(pools))
        # fixed numbers, so these tests do not move when the settings are tuned
        monkeypatch.setattr(scrapers, "_LI_PACE", 4.0)
        monkeypatch.setattr(scrapers, "_LI_DEPTH", 50)
        monkeypatch.setattr(scrapers, "_LI_SECONDS", 1200)
        monkeypatch.setattr(scrapers, "_LI_LIST_LIMIT", 400)
        monkeypatch.setattr(scrapers, "_queries_by_form", lambda: list(queries))
        monkeypatch.setattr(scrapers, "_LI_GUEST_QUERIES", ())
        monkeypatch.setattr(scrapers, "_LI_PART_TIME_QUERIES", ())
        monkeypatch.setattr(scrapers, "_LI_NRW_QUERIES", ())
        monkeypatch.setattr(scrapers.requests, "get", self.fake.get)
        monkeypatch.setattr(scrapers, "_li_clock", lambda: self.clock[0])
        monkeypatch.setattr(scrapers.time, "sleep", self._sleep)
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions", self._describe)
        monkeypatch.setattr(scrapers, "KNOWN_JOB_IDS", set())
        monkeypatch.setattr(scrapers, "TITLE_IS_TECH", None)
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock[0] += seconds

    def _describe(self, jobs, quiet=False, screen_titles=True, stats=None, **kw):
        self.describe_calls += 1
        self.describe_settings = getattr(self, "describe_settings", []) + [kw]
        self.described.extend(j["url"] for j in jobs)
        if stats is not None:
            stats[0] += len(jobs)
            stats[1] += len(jobs)

    def run(self, now=NOW, save=False):
        sweep.begin(now)
        self.fake.calls.clear()
        self.sleeps.clear()
        self.clock[0] = 0.0
        jobs = scrapers.scrape_linkedin()
        if save:
            sweep.save()
        return jobs


@pytest.fixture
def lane(monkeypatch):
    def make(**kw):
        return Lane(monkeypatch, **kw)
    return make


# -- the order in which the requests are spent -----------------------------------
class TestTheOrderOfTheSearches:
    def test_nrw_lists_first_then_first_pages_then_germany_lists_then_the_rest(self, lane):
        l = lane(fake=FakeLinkedIn(default=12), nrw_only=("Junior",),
                 queries=("Werkstudent Data Science", "Junior Data Scientist Germany"))
        jobs = l.run()
        assert [c[:3] for c in l.fake.calls] == [
            ("Werkstudent", NRW, 0), ("Junior", NRW, 0),                 # 1: NRW lists, to the end
            ("Werkstudent", NRW, 10), ("Junior", NRW, 10),
            ("Junior Data Scientist Germany", "Germany", 0),            # 2: first page, no list covers it
            ("Werkstudent", "Germany", 0), ("Werkstudent", "Germany", 10),   # 3: Germany lists
            ("Werkstudent Data Science", "Germany", 0),                 # 4: the keyword searches
            ("Junior Data Scientist Germany", "Germany", 10),
            ("Werkstudent Data Science", "Germany", 10),
        ]
        assert len(jobs) == 5 * 12

    def test_a_list_only_for_nrw_is_not_asked_for_germany(self, lane):
        l = lane(fake=FakeLinkedIn(default=3), nrw_only=("Junior",))
        l.run()
        assert ("Junior", NRW) in {c[:2] for c in l.fake.calls}
        assert ("Junior", "Germany") not in {c[:2] for c in l.fake.calls}

    def test_a_search_that_was_cut_short_goes_first_in_its_stage(self, lane, tmp_path):
        _marks(tmp_path, linkedin__nrw__werkstudent=7, linkedin__nrw__praktikum=30)
        l = lane(fake=FakeLinkedIn(default=3), pools=("Werkstudent", "Praktikum"))
        l.run()
        assert [c[0] for c in l.fake.calls[:2]] == ["Praktikum", "Werkstudent"]

    def test_an_internship_search_does_not_stand_first_because_it_asks_for_a_week(self, lane, tmp_path):
        # It asks for 168 hours in every run. Ordered by the hours asked, it
        # would always be read before the rest, and the rest cut short.
        _marks(tmp_path, **{"linkedin__q__junior data scientist germany": 17,
                            "linkedin__q__praktikum data science": 17,
                            "linkedin__q__teilzeit data": 41})
        l = lane(fake=FakeLinkedIn(default=3), pools=(),
                 queries=("Junior Data Scientist Germany", "Praktikum Data Science", "Teilzeit Data"))
        l.run()
        assert [c[0] for c in l.fake.calls] == ["Teilzeit Data",                 # cut short last time
                                                 "Junior Data Scientist Germany",  # then the plan's order
                                                 "Praktikum Data Science"]
        assert {c[0]: c[3] for c in l.fake.calls}["Praktikum Data Science"] == 168

    def test_the_real_plan_asks_each_search_once_and_every_query(self):
        plan = scrapers._linkedin_plan()
        keys = [s["key"] for s in plan]
        assert len(keys) == len(set(keys))
        names = {s["key"] for s in plan}
        for query in config.SEARCH_QUERIES:
            assert "linkedin|q|" + " ".join(query.lower().split()) in names
        for query in scrapers._LI_GUEST_QUERIES:
            assert "linkedin|q|" + query.lower() in names
        # Measured on LinkedIn: the English words bring the same postings, "Trainee"
        # brings apprenticeships and "Junior" hardly any junior title.
        assert scrapers._LI_POOLS_NRW == ("Werkstudent", "Praktikum")
        assert {"linkedin|nrw|werkstudent", "linkedin|nrw|praktikum"} <= names
        # and since 2026-11-18 the same two words for all of Germany
        assert {"linkedin|de|werkstudent", "linkedin|de|praktikum"} <= names
        assert sum(1 for s in plan if s["pool"]) == 4
        assert all(s["location"] == NRW for s in plan if s["stage"] == 1)
        assert all(s["pool"] == (s["stage"] in (1, 3)) for s in plan)
        assert all(s["depth"] == scrapers._LI_DEPTH for s in plan if not s["pool"])

    def test_the_settings_fit_the_lanes_time(self):
        # The lane's first run from the production address (17 November 2026):
        # 333 requests in 1,303 seconds, none refused, so 3.9 seconds a request
        # with the description fetches in between. Five pages for every keyword
        # search that no list covers, about 40 requests for the two NRW lists
        # and about 100 for the two lists for all of Germany (three and a half
        # hours at the busiest time, 2026-11-18) fit.
        plan = scrapers._linkedin_plan()
        keyword_requests = sum(s["depth"] // 10 for s in plan if s["stage"] == 2)
        assert (scrapers._LI_PACE, scrapers._LI_DEPTH, scrapers._LI_SECONDS) == (3.0, 50, 2400)
        assert (keyword_requests + 40 + 100) * 3.9 < scrapers._LI_SECONDS
        assert scrapers._LI_LIST_LIMIT == 800 and scrapers._LI_GIVE_UP_AFTER == 4

    def test_two_lists_are_read_for_all_of_germany(self):
        # 2026-11-18: "Werkstudent" for all of Germany had 135 postings in the
        # last hour at noon, read to the end in 15 requests. With LinkedIn read
        # five times a day a list covers three to eight hours.
        assert scrapers._LI_POOLS_GERMANY == ("Werkstudent", "Praktikum")
        plan = scrapers._linkedin_plan()
        assert [s["key"] for s in plan if s["stage"] == 3] == ["linkedin|de|werkstudent", "linkedin|de|praktikum"]
        stage = {s["words"]: s["stage"] for s in plan if not s["pool"]}
        # covered by a list, or by a word LinkedIn answers with the same postings
        for words in ("Werkstudent Data Science", "Working Student Machine Learning", "Praktikum Informatik",
                      "Internship Data Science", "Summer Internship 2027", "Pflichtpraktikum Informatik"):
            assert stage[words] == 4, words
        for words in ("Junior Data Scientist Germany", "HiWi Data Science", "Data Science Intern",
                      "Praktikant Data Science", "studentische Hilfskraft KI", "Teilzeit Data"):
            assert stage[words] == 2, words

    def test_a_keyword_search_that_a_germany_list_covers_waits_for_the_last_stage(self, monkeypatch):
        monkeypatch.setattr(scrapers, "_LI_POOLS_GERMANY", ("Werkstudent", "Praktikum"))
        stage = {s["words"]: s["stage"] for s in scrapers._linkedin_plan() if not s["pool"]}
        assert stage["Werkstudent Data Science"] == 4
        assert stage["Praktikum Machine Learning"] == 4
        assert stage["Junior Data Scientist Germany"] == 2
        assert stage["Teilzeit Data"] == 2
        assert stage["HiWi Data Science"] == 2

    def test_keyword_searches_are_read_page_by_page_across_all_of_them(self, lane):
        l = lane(fake=FakeLinkedIn(default=25), pools=(), queries=("Junior Data Scientist Germany",
                                                                    "Teilzeit Data", "HiWi Data Science"))
        l.run()
        assert [(c[0], c[2]) for c in l.fake.calls] == [
            ("Junior Data Scientist Germany", 0), ("Teilzeit Data", 0), ("HiWi Data Science", 0),
            ("Junior Data Scientist Germany", 10), ("Teilzeit Data", 10), ("HiWi Data Science", 10),
            ("Junior Data Scientist Germany", 20), ("Teilzeit Data", 20), ("HiWi Data Science", 20),
        ]


# -- every card of a list is read -------------------------------------------------
class TestAListIsReadToItsEnd:
    @pytest.mark.parametrize("total,starts", [
        (37, [0, 10, 20, 30]),          # a short last page ends it
        (30, [0, 10, 20, 30]),          # a full last page needs one more request
        (4, [0]),
        (0, [0]),
    ])
    def test_no_card_is_skipped_and_none_is_read_twice(self, lane, total, starts):
        l = lane(fake=FakeLinkedIn(Werkstudent__nrw=total))
        jobs = l.run()
        assert l.fake.starts("Werkstudent", NRW) == starts
        assert len(jobs) == total
        assert len({j["url"] for j in jobs}) == total
        assert scrapers.LINKEDIN_REPORT["pools"]["nrw|werkstudent"] == {
            "read": total, "hours": 24, "status": "end"}

    def test_a_long_list_is_read_far_past_the_old_fifty(self, lane):
        l = lane(fake=FakeLinkedIn(Werkstudent=362))
        jobs = l.run()
        assert len(jobs) == 362
        assert l.fake.starts("Werkstudent")[-1] == 360

    def test_a_list_far_longer_than_planned_is_stopped_and_counts_as_cut_short(self, lane, tmp_path):
        # "Trainee" with NRW as location: 600 results in six hours, nearly all apprenticeships
        l = lane(fake=FakeLinkedIn(Werkstudent__nrw=1200), queries=("Junior Data Scientist Germany",))
        jobs = l.run(save=True)
        assert len(jobs) == scrapers._LI_LIST_LIMIT == 400
        report = scrapers.LINKEDIN_REPORT
        assert report["pools"]["nrw|werkstudent"] == {"read": 400, "hours": 24, "status": "long"}
        assert (report["done"], report["cut_short"], report["halt"]) == (2, 1, "")
        assert report["reasons"] == {"long": 1}
        assert "a list was longer than planned" in scrapers.linkedin_lane_note()
        # the searches after it were still asked
        assert l.fake.starts("Junior Data Scientist Germany") == [0]
        # and the list does not count as read
        saved = _saved(tmp_path)
        assert saved["linkedin|nrw|werkstudent"]["covered"] < saved["linkedin|de|werkstudent"]["covered"]

    def test_a_page_of_repeats_ends_the_search(self, lane):
        fake = FakeLinkedIn()
        rows = [(700000000 + i, f"Werkstudent {i}") for i in range(10)]
        fake.set("Werkstudent", NRW, rows + rows)
        l = lane(fake=fake)
        assert len(l.run()) == 10
        assert fake.starts("Werkstudent", NRW) == [0, 10]

    def test_linkedin_serves_no_more_than_a_thousand(self, lane, monkeypatch):
        l = lane(fake=FakeLinkedIn(Werkstudent__nrw=1200))
        monkeypatch.setattr(scrapers, "_LI_LIST_LIMIT", 5000)
        jobs = l.run()
        assert len(jobs) == 1000
        assert scrapers.LINKEDIN_REPORT["pools"]["nrw|werkstudent"]["status"] == "limit"
        assert scrapers.LINKEDIN_REPORT["cut_short"] == 0

    def test_a_keyword_search_stops_at_its_depth(self, lane):
        l = lane(fake=FakeLinkedIn(default=0, Junior_Data_Scientist_Germany=200), pools=(),
                 queries=("Junior Data Scientist Germany",))
        jobs = l.run()
        assert l.fake.starts("Junior Data Scientist Germany") == [0, 10, 20, 30, 40]
        assert len(jobs) == scrapers._LI_DEPTH == 50

    def test_the_card_fields_arrive(self, lane):
        l = lane(fake=FakeLinkedIn(Werkstudent__nrw=1))
        (job,) = l.run()
        assert job["url"].startswith("https://www.linkedin.com/jobs/view/")
        assert job["url"].rsplit("/", 1)[1].isdigit()
        assert (job["title"], job["company"], job["source"]) == ("Werkstudent 0", "Acme GmbH", "linkedin")
        assert job["location"] == "Bonn, North Rhine-Westphalia, Germany"
        assert job["posted_at"] == "2026-11-17" and job["description"] == ""


# -- lists keep the tech titles ---------------------------------------------------
class TestAListKeepsTechTitles:
    @staticmethod
    def _fake():
        fake = FakeLinkedIn()
        fake.set("Werkstudent", NRW, [(800000001, "Werkstudent Data Science"),
                                      (800000002, "Werkstudent Marketing"),
                                      (800000003, "Werkstudent Vertrieb")])
        fake.set("Werkstudent AI", "Germany", [(800000002, "Werkstudent Marketing")])
        return fake

    def test_a_card_without_tech_work_in_the_title_is_left_out(self, lane, monkeypatch):
        l = lane(fake=self._fake())
        monkeypatch.setattr(scrapers, "TITLE_IS_TECH", lambda title: "data" in title.lower())
        assert [j["title"] for j in l.run()] == ["Werkstudent Data Science"]
        assert scrapers.LINKEDIN_REPORT["pool_cards_left_out"] == 2

    def test_a_keyword_search_still_brings_the_same_card(self, lane, monkeypatch):
        l = lane(fake=self._fake(), queries=("Werkstudent AI",))
        monkeypatch.setattr(scrapers, "TITLE_IS_TECH", lambda title: "data" in title.lower())
        assert sorted(j["title"] for j in l.run()) == ["Werkstudent Data Science", "Werkstudent Marketing"]

    def test_without_a_title_test_everything_is_kept(self, lane):
        assert len(lane(fake=self._fake()).run()) == 3

    def test_a_title_test_that_fails_keeps_the_card(self, lane, monkeypatch):
        def broken(title):
            raise ValueError("bad pattern")
        l = lane(fake=self._fake())
        monkeypatch.setattr(scrapers, "TITLE_IS_TECH", broken)
        assert len(l.run()) == 3

    def test_main_hands_over_its_own_tech_gate_for_titles(self, monkeypatch):
        monkeypatch.setattr(main, "load_seen", lambda: {})
        monkeypatch.setattr(main, "_load_run_history", lambda: [])
        monkeypatch.setattr(main, "_load_source_registry", lambda: {})
        monkeypatch.setattr(main, "_absent_source_warnings_from", lambda registry, counts: [])
        monkeypatch.setattr(main, "_quiet_today", lambda registry, counts: [])
        monkeypatch.setattr(main, "scrape_all", lambda: [])
        monkeypatch.setattr(scrapers, "TITLE_IS_TECH", None)
        main.node_scrape({})
        assert scrapers.TITLE_IS_TECH("Werkstudent Data Science") is True
        assert scrapers.TITLE_IS_TECH("Werkstudent (m/w/d) Softwareentwicklung") is True
        assert scrapers.TITLE_IS_TECH("Werkstudent Marketing") is False
        assert scrapers.TITLE_IS_TECH("") is False


# -- a refusal is waited out, the search is not dropped ---------------------------
class TestARefusal:
    def test_the_lane_waits_and_asks_the_same_page_again(self, lane):
        fake = FakeLinkedIn(Werkstudent__nrw=25)
        fake.answers[2] = (429, "")
        l = lane(fake=fake)
        jobs = l.run()
        assert fake.starts("Werkstudent", NRW) == [0, 10, 10, 20]
        assert len(jobs) == 25
        assert l.sleeps == [4.0, 60, 6.0, 6.0, 6.0]            # slower after the refusal
        report = scrapers.LINKEDIN_REPORT
        assert (report["refusals"], report["waited_s"], report["cut_short"]) == (1, 60, 0)

    def test_each_further_refusal_is_waited_out_longer(self, lane):
        fake = FakeLinkedIn(Werkstudent__nrw=5)
        fake.answers.update({n: (429, "") for n in (1, 2, 3, 4)})
        l = lane(fake=fake)
        assert len(l.run()) == 5
        assert l.sleeps[:4] == [60, 120, 240, 240]
        assert l.sleeps[4] == 12.0                              # never slower than this

    @pytest.mark.parametrize("answer", [(403, ""), (999, ""), (200, "<html><body>" + "sign in " * 200 + "</body></html>")])
    def test_a_wall_counts_as_a_refusal(self, lane, answer):
        fake = FakeLinkedIn(Werkstudent__nrw=5)
        fake.answers[1] = answer
        l = lane(fake=fake)
        assert len(l.run()) == 5
        assert l.sleeps[0] == 60 and scrapers.LINKEDIN_REPORT["refusals"] == 1

    def test_the_bare_empty_page_is_an_empty_result_not_a_wall(self, lane):
        l = lane(fake=FakeLinkedIn())
        assert l.run() == []
        assert scrapers.LINKEDIN_REPORT["refusals"] == 0
        assert scrapers.LINKEDIN_REPORT["cut_short"] == 0

    def test_after_a_refusal_and_an_answer_the_next_wait_is_short_again(self, lane):
        fake = FakeLinkedIn(Werkstudent__nrw=45)
        fake.answers.update({2: (429, ""), 4: (429, "")})
        l = lane(fake=fake)
        assert len(l.run()) == 45
        assert l.sleeps[:5] == [4.0, 60, 6.0, 60, 9.0]

    def test_a_lane_that_is_refused_again_and_again_gives_up(self, lane, tmp_path):
        fake = FakeLinkedIn(default=30)
        fake.refuse_from = 1
        l = lane(fake=fake, queries=("Junior Data Scientist Germany",))
        assert l.run(save=True) == []
        assert l.sleeps == [60, 120, 240, 240]                  # eleven minutes, then it is over
        assert len(fake.calls) == 5
        report = scrapers.LINKEDIN_REPORT
        assert (report["halt"], report["done"], report["cut_short"]) == ("refused", 0, 3)
        assert {v["status"] for v in report["pools"].values()} == {"refused"}
        # nothing counts as read: each search keeps the stretch it was asked for
        # (a list for all of Germany read for the first time asks for 3 hours)
        saved = _saved(tmp_path)
        assert {k: v["covered"] for k, v in saved.items()} == {
            k: (NOW - timedelta(hours=3 if k.startswith("linkedin|de|") else 24)).isoformat(timespec="seconds")
            for k in saved}

    def test_a_wait_that_would_run_past_the_lanes_time_is_not_started(self, lane, monkeypatch):
        fake = FakeLinkedIn(default=30)
        fake.refuse_from = 1
        l = lane(fake=fake)
        monkeypatch.setattr(scrapers, "_LI_SECONDS", 100)
        assert l.run() == []
        assert l.sleeps == [60]                                 # 60 + 120 would be past 100 s
        assert scrapers.LINKEDIN_REPORT["halt"] == "refused"

    def test_what_was_found_before_a_final_refusal_is_returned(self, lane):
        fake = FakeLinkedIn(Werkstudent__nrw=100)
        fake.refuse_from = 4
        l = lane(fake=fake)
        assert len(l.run()) == 30
        assert scrapers.LINKEDIN_REPORT["pools"]["nrw|werkstudent"] == {
            "read": 30, "hours": 24, "status": "refused"}


# -- failed requests --------------------------------------------------------------
class TestFailedRequests:
    def test_two_failures_close_one_search_and_the_others_go_on(self, lane):
        fake = FakeLinkedIn(default=12)
        fake.broken.add(("Werkstudent", NRW))
        l = lane(fake=fake, nrw_only=("Junior",))
        jobs = l.run()
        assert fake.starts("Werkstudent", NRW) == [0, 0]
        assert len(jobs) == 24                                  # Junior in NRW, Werkstudent in Germany
        report = scrapers.LINKEDIN_REPORT
        assert report["pools"]["nrw|werkstudent"]["status"] == "error"
        assert (report["done"], report["cut_short"]) == (2, 1)

    def test_one_failure_is_asked_again(self, lane):
        fake = FakeLinkedIn(Werkstudent__nrw=12)
        fake.fail.add(1)
        l = lane(fake=fake)
        assert len(l.run()) == 12
        assert fake.starts("Werkstudent", NRW) == [0, 0, 10]

    def test_cards_that_cannot_be_read_are_not_the_end_of_a_search(self, lane, tmp_path):
        # LinkedIn changes its page: ten cards, no title, no link. Counted as
        # "ten read, then an empty page: done", every search would move its
        # mark on while nothing at all was read.
        strange = "".join(f"<li><div class='new-card-{i}'>something</div></li>" for i in range(10))
        fake = FakeLinkedIn(default=12)
        fake.answers.update({1: (200, strange), 3: (200, strange)})
        l = lane(fake=fake, nrw_only=("Junior",))
        jobs = l.run(save=True)
        report = scrapers.LINKEDIN_REPORT
        assert fake.starts("Werkstudent", NRW) == [0, 0]            # asked again, then given up
        assert report["pools"]["nrw|werkstudent"] == {"read": 0, "hours": 24, "status": "error"}
        assert (report["unreadable_pages"], report["cut_short"]) == (2, 1)
        assert len(jobs) == 24                                      # the other searches went on
        saved = _saved(tmp_path)
        assert saved["linkedin|nrw|werkstudent"]["covered"] < saved["linkedin|nrw|junior"]["covered"]

    def test_a_page_layout_that_changed_everywhere_stops_the_lane_and_is_a_warning(self, lane):
        strange = "".join("<li><div>something new</div></li>" for _ in range(10))
        fake = FakeLinkedIn()
        fake.answers.update({n: (200, strange) for n in range(1, 400)})
        l = lane(fake=fake, pools=("A", "B", "C"), nrw_only=("D", "E", "F"))
        assert l.run() == []
        report = scrapers.LINKEDIN_REPORT
        assert (len(fake.calls), report["halt"], report["done"]) == (10, "error", 0)
        (warning,) = scrapers.linkedin_lane_warnings()
        assert "10 answers held job cards that could not be read" in warning
        assert "layout has probably changed" in warning

    def test_single_cards_that_cannot_be_read_are_counted(self, lane):
        fake = FakeLinkedIn()
        fake.answers[1] = (200, _card(850000001, "Werkstudent A", "Bonn, North Rhine-Westphalia, Germany")
                           + "<li><div>an advert</div></li>")
        l = lane(fake=fake)
        assert len(l.run()) == 1
        assert scrapers.LINKEDIN_REPORT["cards_unread"] == 1
        assert scrapers.LINKEDIN_REPORT["unreadable_pages"] == 0
        assert scrapers.linkedin_lane_warnings() == []

    def test_a_failed_request_between_refusals_is_no_answer(self, lane):
        # Only an answer shows that LinkedIn lets the lane through again.
        fake = FakeLinkedIn(default=30)
        fake.answers.update({1: (429, ""), 2: (429, ""), 3: (500, ""), 4: (429, ""), 5: (429, ""), 6: (429, "")})
        l = lane(fake=fake, nrw_only=("Junior",))
        assert l.run() == []
        assert len(fake.calls) == 6 and scrapers.LINKEDIN_REPORT["halt"] == "refused"

    def test_when_nothing_answers_the_lane_stops_after_ten(self, lane):
        fake = FakeLinkedIn()
        fake.fail.update(range(1, 500))
        l = lane(fake=fake, pools=("A", "B", "C"), nrw_only=("D", "E", "F"))
        assert l.run() == []
        assert len(fake.calls) == 10
        assert scrapers.LINKEDIN_REPORT["halt"] == "error"
        assert scrapers.LINKEDIN_REPORT["done"] == 0

    @pytest.mark.filterwarnings("ignore::bs4.MarkupResemblesLocatorWarning")
    @settings(max_examples=60, deadline=None)
    @given(st.text(max_size=300))
    def test_any_answer_text_is_read_without_an_error(self, text):
        number, cards = scrapers._li_cards(text)
        assert number >= len(cards) >= 0


# -- the lane's time ---------------------------------------------------------------
class TestTheLanesTime:
    def test_the_lane_stops_when_its_time_is_up_and_keeps_what_it_found(self, lane, monkeypatch):
        l = lane(fake=FakeLinkedIn(default=200), queries=("Junior Data Scientist Germany",))
        monkeypatch.setattr(scrapers, "_LI_SECONDS", 20)
        jobs = l.run()
        assert len(l.fake.calls) == 6                           # 4 s apart: the seventh is past 20 s
        assert len(jobs) == 60
        report = scrapers.LINKEDIN_REPORT
        assert report["halt"] == "time"
        assert report["pools"]["nrw|werkstudent"]["status"] == "time"
        assert report["pools"]["de|werkstudent"]["status"] == "time"     # never reached
        assert sweep.outcomes()["linkedin|q|junior data scientist germany"]["complete"] is False


# -- how many hours each search is asked for --------------------------------------
class TestTheWindows:
    def test_a_list_is_asked_for_the_time_since_it_was_read_to_its_end(self, lane, tmp_path):
        _marks(tmp_path, linkedin__nrw__werkstudent=17)
        l = lane(fake=FakeLinkedIn(default=3))
        l.run()
        hours = {c[:2]: c[3] for c in l.fake.calls}
        assert hours[("Werkstudent", NRW)] == 17 + sweep.SLACK_HOURS
        # no mark yet: a list for all of Germany starts with the last few hours (2026-11-18)
        assert hours[("Werkstudent", "Germany")] == scrapers._LI_POOL_FIRST_HOURS == 3

    def test_a_list_is_never_asked_for_more_than_two_days(self, lane, tmp_path):
        _marks(tmp_path, linkedin__nrw__werkstudent=100)
        l = lane(fake=FakeLinkedIn(default=3))
        l.run()
        assert {c[:2]: c[3] for c in l.fake.calls}[("Werkstudent", NRW)] == scrapers._LI_POOL_HOURS

    def test_a_keyword_search_is_never_asked_for_less_than_before(self, lane, tmp_path):
        _marks(tmp_path, **{"linkedin__q__junior data scientist germany": 7,
                            "linkedin__q__praktikum data science": 7})
        l = lane(fake=FakeLinkedIn(default=3), pools=(),
                 queries=("Junior Data Scientist Germany", "Praktikum Data Science"))
        l.run()
        hours = {c[0]: c[3] for c in l.fake.calls}
        assert hours["Junior Data Scientist Germany"] == 24
        assert hours["Praktikum Data Science"] == config.INTERNSHIP_SOURCE_HOURS   # still a week

    def test_the_second_run_of_the_day_asks_for_the_hours_since_the_morning(self, lane, tmp_path):
        _marks(tmp_path, **{"linkedin__q__junior data scientist germany": 7,
                            "linkedin__nrw__werkstudent": 7})
        config.set_source_window_hours(9)
        l = lane(fake=FakeLinkedIn(default=3), queries=("Junior Data Scientist Germany",
                                                         "Praktikum Data Science"))
        l.run()
        hours = {c[:2]: c[3] for c in l.fake.calls}
        assert hours[("Junior Data Scientist Germany", "Germany")] == 9
        assert hours[("Praktikum Data Science", "Germany")] == 9
        assert hours[("Werkstudent", NRW)] == 9
        assert hours[("Werkstudent", "Germany")] == 3           # first read of that list (2026-11-18)

    def test_a_search_cut_short_today_asks_for_a_longer_stretch_tomorrow(self, lane):
        fake = FakeLinkedIn(default=3)
        fake.refuse_from = 1
        l = lane(fake=fake, queries=("Junior Data Scientist Germany",))
        l.run(now=NOW, save=True)
        fake.refuse_from = None
        l.run(now=NOW + timedelta(hours=24), save=True)
        hours = {c[:2]: c[3] for c in fake.calls}
        assert hours[("Junior Data Scientist Germany", "Germany")] == 48 + sweep.SLACK_HOURS
        assert hours[("Werkstudent", NRW)] == scrapers._LI_POOL_HOURS
        l.run(now=NOW + timedelta(hours=31), save=True)         # read to the end: back to normal
        hours = {c[:2]: c[3] for c in fake.calls}
        assert hours[("Werkstudent", NRW)] == 7 + sweep.SLACK_HOURS
        assert hours[("Junior Data Scientist Germany", "Germany")] == 24

    def test_a_catch_up_day_asks_every_search_for_the_week(self, lane, monkeypatch, tmp_path):
        _marks(tmp_path, linkedin__nrw__werkstudent=7)
        l = lane(fake=FakeLinkedIn(default=3), queries=("Junior Data Scientist Germany",))
        monkeypatch.setattr(config, "is_catchup", lambda today=None: True)
        l.run()
        assert {c[3] for c in l.fake.calls} == {168}


# -- what counts as read ----------------------------------------------------------
class TestWhatCountsAsRead:
    def test_a_finished_lane_records_every_search(self, lane, tmp_path):
        l = lane(fake=FakeLinkedIn(default=60), queries=("Junior Data Scientist Germany",))
        l.run(save=True)
        rows = sweep.outcomes() or {}
        assert rows == {}                                       # saved: the run is closed
        saved = _saved(tmp_path)
        assert sorted(saved) == ["linkedin|de|werkstudent", "linkedin|nrw|werkstudent",
                                 "linkedin|q|junior data scientist germany"]
        assert {v["covered"] for v in saved.values()} == {NOW.isoformat(timespec="seconds")}

    def test_reaching_the_depth_counts_and_so_does_the_end(self, lane):
        l = lane(fake=FakeLinkedIn(default=60), queries=("Junior Data Scientist Germany",))
        l.run()
        rows = sweep.outcomes("linkedin|")
        assert rows["linkedin|q|junior data scientist germany"]["why"] == "depth"
        assert rows["linkedin|nrw|werkstudent"]["why"] == "end"
        assert all(r["complete"] for r in rows.values())
        assert rows["linkedin|nrw|werkstudent"]["read"] == 60

    def test_a_lane_whose_result_was_taken_early_records_nothing(self, lane, capsys):
        l = lane(fake=FakeLinkedIn(default=12))
        sweep.begin(NOW)
        scrapers._salvage_background("scrape_linkedin")          # the run takes what there is
        jobs = scrapers.scrape_linkedin()
        assert len(jobs) == 24
        assert sweep.outcomes() == {}
        assert scrapers.LINKEDIN_REPORT["recorded"] is False
        assert "nothing is recorded as read" in capsys.readouterr().out

    def test_taking_a_result_early_notes_the_scraper(self):
        scrapers._LI_SWEEP_PARTIAL[:] = [{"id": "x"}]
        assert scrapers._salvage_background("scrape_linkedin") == [{"id": "x"}]
        assert "scrape_linkedin" in scrapers._ABANDONED
        scrapers._LI_SWEEP_PARTIAL.clear()

    def test_the_partial_result_grows_while_the_lane_runs(self, lane, monkeypatch):
        monkeypatch.setattr(scrapers, "_LI_PUBLISH_EVERY", 10)
        seen_sizes = []
        l = lane(fake=FakeLinkedIn(Werkstudent__nrw=35))
        original = l._describe

        def describe(jobs, **kw):
            seen_sizes.append(len(scrapers._LI_SWEEP_PARTIAL))
            original(jobs, **kw)
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions", describe)
        l.run()
        # handed over page by page (the texts are asked one at a time since 2026-11-18)
        assert sorted(set(seen_sizes)) == [0, 10, 20, 30] and seen_sizes == sorted(seen_sizes)
        assert scrapers._PARTIAL_BUFFERS["scrape_linkedin"] is scrapers._LI_SWEEP_PARTIAL


# -- descriptions -----------------------------------------------------------------
class TestDescriptions:
    def test_only_postings_not_seen_before_get_a_description(self, lane, monkeypatch):
        fake = FakeLinkedIn()
        fake.set("Werkstudent", NRW, [(810000001, "Werkstudent A"), (810000002, "Werkstudent B")])
        l = lane(fake=fake)
        known = scrapers.make_id("https://www.linkedin.com/jobs/view/810000001", "Werkstudent A", "Acme GmbH")
        monkeypatch.setattr(scrapers, "KNOWN_JOB_IDS", {known})
        jobs = l.run()
        assert len(jobs) == 2                                    # a seen posting is still returned
        assert l.described == ["https://www.linkedin.com/jobs/view/810000002"]
        assert scrapers.LINKEDIN_REPORT["known"] == 1
        assert scrapers.LINKEDIN_REPORT["described"] == 1

    def test_the_real_helper_counts_into_the_lanes_own_tally(self, monkeypatch):
        monkeypatch.setattr(scrapers, "_fetch_full_description", lambda url: "text " * 200)
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)
        before = list(scrapers._JOBSPY_DESC_STATS)
        tally = [0, 0, 0]
        jobs = [scrapers.job("Werkstudent Data", "Acme", "Bonn", "https://www.linkedin.com/jobs/view/820000001",
                             "linkedin"),
                scrapers.job("Senior Director Data", "Acme", "Bonn",
                             "https://www.linkedin.com/jobs/view/820000002", "linkedin")]
        scrapers._enrich_jobspy_descriptions(jobs, quiet=True, stats=tally)
        assert tally == [1, 1, 1]
        assert scrapers._JOBSPY_DESC_STATS == before
        assert len(jobs[0]["description"]) > 400 and jobs[1]["description"] == ""


# -- what the digest says ----------------------------------------------------------
class TestTheNoteAndTheWarning:
    def test_a_full_read_is_one_plain_sentence(self, lane):
        lane(fake=FakeLinkedIn(default=3), queries=("Junior Data Scientist Germany",)).run()
        assert scrapers.linkedin_lane_note() == "LinkedIn: 3 of 3 searches were read as far as planned."
        assert scrapers.linkedin_lane_warnings() == []

    def test_a_cut_short_read_says_why_and_what_happens_next(self, lane):
        fake = FakeLinkedIn(default=3)
        fake.refuse_from = 2
        lane(fake=fake, queries=("Junior Data Scientist Germany",)).run()
        note = scrapers.linkedin_lane_note()
        assert note.startswith("LinkedIn: 1 of 3 searches were read as far as planned; 2 were cut short")
        assert "LinkedIn refused further requests" in note and "asked first in the next run" in note

    def test_no_lane_no_note(self):
        assert scrapers.linkedin_lane_note() == ""

    def test_a_search_unread_for_longer_than_the_freshness_rule_is_a_warning(self, lane, tmp_path):
        _marks(tmp_path, linkedin__nrw__werkstudent=60, linkedin__de__werkstudent=20)
        fake = FakeLinkedIn(default=3)
        fake.refuse_from = 1
        lane(fake=fake).run()
        (warning,) = scrapers.linkedin_lane_warnings()
        assert "1 searches (1 of them full lists)" in warning
        assert "more than 48 hours (longest: 60 h)" in warning
        assert "too old for the freshness rule" in warning

    def test_one_bad_run_is_no_warning(self, lane):
        fake = FakeLinkedIn(default=3)
        fake.refuse_from = 1
        lane(fake=fake).run()
        assert scrapers.linkedin_lane_warnings() == []


# -- one lane asks LinkedIn ---------------------------------------------------------
def _stub_every_scraper(monkeypatch):
    ran = []
    for name in dir(scrapers):
        if not name.startswith("scrape_") or name in ("scrape_all", "scrape_company_page"):
            continue
        if not callable(getattr(scrapers, name)):
            continue

        def stub(_name=name):
            ran.append(_name)
            return []
        stub.__name__ = name
        monkeypatch.setattr(scrapers, name, stub)
    return ran


class TestOneLaneAsksLinkedIn:
    def test_the_paced_lane_runs_and_the_two_old_lanes_do_not(self, monkeypatch):
        ran = _stub_every_scraper(monkeypatch)
        scrapers.scrape_all()
        assert "scrape_linkedin" in ran and "scrape_indeed" in ran
        assert "scrape_jobspy" not in ran and "scrape_linkedin_guest" not in ran
        assert len(ran) == len(set(ran))

    def test_the_switch_brings_the_old_lanes_back(self, monkeypatch):
        monkeypatch.setenv("JOBHUNTER_LINKEDIN_LANE", "legacy")
        ran = _stub_every_scraper(monkeypatch)
        scrapers.scrape_all()
        assert "scrape_jobspy" in ran and "scrape_linkedin_guest" in ran
        assert "scrape_linkedin" not in ran and "scrape_indeed" not in ran

    @pytest.mark.parametrize("value,on", [("", True), ("sweep", True), ("legacy", False),
                                          (" Legacy ", False), ("off", True)])
    def test_only_the_word_legacy_switches_it_off(self, monkeypatch, value, on):
        monkeypatch.setenv("JOBHUNTER_LINKEDIN_LANE", value)
        assert scrapers.linkedin_lane_on() is on

    def test_both_slow_lanes_start_in_the_background(self):
        assert {"scrape_indeed", "scrape_linkedin"} <= scrapers._BACKGROUND_SCRAPERS
        assert scrapers._PARTIAL_BUFFERS["scrape_indeed"] is scrapers._JOBSPY_PARTIAL

    def test_a_lane_that_raises_still_delivers_what_it_had_handed_over(self, monkeypatch, capsys):
        _stub_every_scraper(monkeypatch)

        def scrape_linkedin():
            scrapers._LI_SWEEP_PARTIAL[:] = [scrapers.job("Werkstudent Data", "Acme", "Bonn",
                                                           "https://www.linkedin.com/jobs/view/840000001",
                                                           "linkedin")]
            raise RuntimeError("a surprise")
        monkeypatch.setattr(scrapers, "scrape_linkedin", scrape_linkedin)
        jobs = scrapers.scrape_all()
        scrapers._LI_SWEEP_PARTIAL.clear()
        assert [j["title"] for j in jobs] == ["Werkstudent Data"]
        assert "ended with an error: keeping the 1 jobs" in capsys.readouterr().out
        assert sweep.outcomes() == {}

    def test_the_lane_gets_its_own_time_however_fast_the_other_sources_are(self, monkeypatch):
        # The run waits for a background source for BACKGROUND_JOIN_SECONDS
        # after the others are done. The lane's time counts from its start.
        import time as real_time
        _stub_every_scraper(monkeypatch)
        monkeypatch.setattr(scrapers, "BACKGROUND_JOIN_SECONDS", 0.01)

        def scrape_linkedin():
            real_time.sleep(0.4)
            return [scrapers.job("Werkstudent Data", "Acme", "Bonn",
                                 "https://www.linkedin.com/jobs/view/860000001", "linkedin")]
        monkeypatch.setattr(scrapers, "scrape_linkedin", scrape_linkedin)
        jobs = scrapers.scrape_all()
        assert [j["title"] for j in jobs] == ["Werkstudent Data"]
        assert "scrape_linkedin" not in scrapers._ABANDONED

    def test_a_scraper_that_returns_nothing_gets_no_leftovers(self, monkeypatch):
        _stub_every_scraper(monkeypatch)
        scrapers._LI_SWEEP_PARTIAL[:] = [{"id": "left over from another run"}]
        jobs = scrapers.scrape_all()
        scrapers._LI_SWEEP_PARTIAL.clear()
        assert jobs == []

    def test_a_new_run_forgets_which_results_were_taken_early(self, monkeypatch):
        scrapers._ABANDONED.add("scrape_linkedin")
        _stub_every_scraper(monkeypatch)
        scrapers.scrape_all()
        assert scrapers._ABANDONED == set()

    def _jobspy(self, monkeypatch, rows_for):
        import sys
        import types

        import pandas as pd
        calls = []

        def fake_scrape_jobs(**kw):
            site = kw["site_name"][0]
            calls.append((site, kw["search_term"], kw["results_wanted"]))
            return pd.DataFrame([{"job_url": f"https://{site}.example/{kw['search_term']}/{i}",
                                  "title": kw["search_term"], "company": "Acme", "location": "Bonn",
                                  "site": site, "description": "x" * 500}
                                 for i in range(rows_for(kw))])
        monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=fake_scrape_jobs))
        monkeypatch.setattr(scrapers, "_jobspy_active_queries", lambda: ["q1", "q2", "q3"])
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)
        monkeypatch.setattr(scrapers, "_fetch_full_description", lambda url: "")
        return calls

    def test_python_jobspy_runs_for_indeed_alone(self, monkeypatch):
        calls = self._jobspy(monkeypatch, lambda kw: 1)
        jobs = scrapers.scrape_indeed()
        assert len(jobs) == 3
        assert {c[0] for c in calls} == {"indeed"}
        assert scrapers.jobspy_progress_line() == "indeed 3/3"

    def test_a_query_that_comes_back_full_is_counted_and_said(self, monkeypatch, capsys):
        limit = scrapers._JOBSPY_INDEED_RESULTS
        self._jobspy(monkeypatch, lambda kw: limit if kw["search_term"] == "q2" else 2)
        scrapers.scrape_indeed()
        assert scrapers._JOBSPY_LANE_REPORT["indeed"]["full"] == 1
        assert f"1 queries reached the limit of {limit} results and have more" in capsys.readouterr().out

    def test_no_full_query_no_such_words(self, monkeypatch, capsys):
        self._jobspy(monkeypatch, lambda kw: 2)
        scrapers.scrape_indeed()
        assert "reached the limit" not in capsys.readouterr().out

    def test_indeed_is_asked_for_three_pages(self):
        assert scrapers._JOBSPY_INDEED_RESULTS == 300


# -- the run around the lane --------------------------------------------------------
class TestTheRunAroundTheLane:
    def _scrape(self, monkeypatch, fake_scrape_all):
        monkeypatch.setattr(main, "load_seen", lambda: {})
        monkeypatch.setattr(main, "_load_run_history", lambda: [])
        monkeypatch.setattr(main, "_load_source_registry", lambda: {})
        monkeypatch.setattr(main, "_absent_source_warnings_from", lambda registry, counts: [])
        monkeypatch.setattr(main, "_quiet_today", lambda registry, counts: [])
        monkeypatch.setattr(main, "scrape_all", fake_scrape_all)
        return main.node_scrape({})

    def test_the_lanes_sentence_reaches_the_digest_notes(self, monkeypatch):
        def fake_scrape_all():
            scrapers.LINKEDIN_REPORT.update({"searches": 126, "done": 120, "cut_short": 6, "halt": "time"})
            return []
        out = self._scrape(monkeypatch, fake_scrape_all)
        (note,) = [n for n in out["run_notes"] if n.startswith("LinkedIn:")]
        assert "120 of 126" in note and "6 were cut short (the time for LinkedIn was up)" in note

    def test_a_report_left_from_an_earlier_run_is_not_repeated(self, monkeypatch):
        scrapers.LINKEDIN_REPORT.update({"searches": 5, "done": 5, "cut_short": 0, "halt": ""})
        out = self._scrape(monkeypatch, lambda: [])
        assert not [n for n in out["run_notes"] if n.startswith("LinkedIn:")]

    def test_an_overdue_search_reaches_the_digest_warnings(self, monkeypatch, tmp_path):
        # node_scrape starts the record at the real time of day
        read = (datetime.now(timezone.utc) - timedelta(hours=70)).isoformat()
        (tmp_path / "search_marks.json").write_text(
            json.dumps({"linkedin|de|werkstudent": {"covered": read, "asked": read}}), encoding="utf-8")

        def fake_scrape_all():
            sweep.record("linkedin|de|werkstudent", False, 48, "refused")
            return []
        out = self._scrape(monkeypatch, fake_scrape_all)
        assert any("LinkedIn: 1 searches" in w and "70 h" in w for w in out["health_warnings"])

    def test_the_scrape_starts_a_fresh_record(self, monkeypatch):
        sweep.record("left over", True, 24, "end")
        self._scrape(monkeypatch, lambda: [])
        assert sweep.outcomes() == {}

    def _persist(self, monkeypatch, tmp_path, email_ok, dry_run):
        files, stats = {}, []
        monkeypatch.setattr(main.storage, "read_text", lambda name: files.get(name))
        monkeypatch.setattr(main.storage, "write_text", lambda name, text: files.__setitem__(name, text))
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
        sweep.record("linkedin|de|werkstudent", True, 24, "end", read=412)
        scrapers.LINKEDIN_REPORT.update({"searches": 1, "done": 1, "cut_short": 0, "requests": 43})
        main.node_persist({"dry_run": dry_run, "seen": {}, "email_ok": email_ok, "all_jobs": [],
                           "scored": [], "top": [], "near": []})
        marks = [text for name, text in files.items() if name.endswith("search_marks.json")]
        return marks, stats

    def test_a_delivered_run_saves_how_far_each_search_was_read(self, monkeypatch, tmp_path):
        (text,), stats = self._persist(monkeypatch, tmp_path, True, False)
        assert json.loads(text)["linkedin|de|werkstudent"]["covered"] == NOW.isoformat(timespec="seconds")
        assert stats[0]["linkedin_lane"]["requests"] == 43

    @pytest.mark.parametrize("email_ok,dry_run", [(False, False), (True, True)])
    def test_a_dry_run_or_an_undelivered_digest_saves_nothing(self, monkeypatch, tmp_path,
                                                             email_ok, dry_run):
        # The state is left untouched, the same ads are unseen next time, so
        # the searches must ask for the same stretch again.
        marks, _ = self._persist(monkeypatch, tmp_path, email_ok, dry_run)
        assert marks == []

    def test_the_marks_file_is_not_committed(self):
        from pathlib import Path
        ignored = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
        assert "search_marks.json" in ignored


# -- an age drop says what date the ad carried --------------------------------------
class TestAnAgeDropSaysTheDate:
    @staticmethod
    def _old(days):
        return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    def test_a_drop_carries_the_date_and_the_age(self):
        j = {"title": "Werkstudent Data Science", "source": "linkedin", "posted_at": self._old(6)}
        assert main._fresh_or_noted(j) is False
        assert j["_drop_note"].startswith(f"dated {self._old(6)}, ")
        assert j["_drop_note"].endswith("days old when first seen")
        assert 5.0 <= float(j["_drop_note"].split(", ")[1].split()[0]) <= 6.0

    def test_a_fresh_ad_and_an_undated_ad_pass_without_a_note(self):
        fresh = {"title": "Werkstudent Data Science", "source": "linkedin", "posted_at": self._old(0)}
        undated = {"title": "Werkstudent Data Science", "source": "linkedin", "posted_at": ""}
        assert main._fresh_or_noted(fresh) and main._fresh_or_noted(undated)
        assert "_drop_note" not in fresh and "_drop_note" not in undated

    def test_the_audit_line_of_an_age_drop_holds_it(self, monkeypatch):
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "_ever_shown_keys", lambda: set())
        monkeypatch.setattr(main, "_ever_shown_url_hashes", lambda: set())
        monkeypatch.setattr(main, "_fill_missing_bodies", lambda js: [])
        monkeypatch.setattr(main, "_skill_radar", lambda js: None)
        monkeypatch.setattr(main, "_body_retry_ids", lambda: set())
        monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
        old = {"id": "old", "title": "Werkstudent Data Science", "company": "Acme", "location": "Bonn",
               "url": "https://www.linkedin.com/jobs/view/830000001", "source": "linkedin",
               "posted_at": self._old(9), "description": "Python and SQL. " * 30}
        main.node_filter({"seen": {}, "all_jobs": [old]})
        (row,) = [r for r in main._RUN_AUDIT if r["id"] == "old"]
        assert row["filter"] == "Freshness filter"
        assert row["why"].startswith(f"dated {self._old(9)}, ") and "days old when first seen" in row["why"]
        assert "_drop_note" not in old
