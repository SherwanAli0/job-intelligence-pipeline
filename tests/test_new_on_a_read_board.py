"""An ad that is new on a board the pipeline has read before is new (2026-11-17).

The freshness rule reads the date an ad prints. That date is not always the
day the ad appeared: a re-opened posting keeps its first date, some boards
print the day the requisition was created. Such an ad was dropped as old on
the day it showed up. The owner on these ads: "keep them".

Where the pipeline reads a board as a whole (Greenhouse, Lever, Ashby,
Personio, Recruitee, the LinkedIn lists for NRW) or can tell a new posting by
its id (XING), an ad it has not seen is new there, and the printed date no
longer decides. A board read for the first time, and every source the
pipeline sees only a part of, keep the date rule.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

import config
import main
import notifier
import scrapers
import sweep

NOW = datetime.now(timezone.utc).replace(microsecond=0)
ENGLISH = ("We are an international team and our working language is English. "
           "In this role the student works with data, builds dashboards and supports the analytics team. "
           "Enrolment at a university and an interest in Python and SQL are what the role needs. ")


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "MARKS_FILE", str(tmp_path / "search_marks.json"))
    monkeypatch.setattr(config, "is_catchup", lambda today=None: False)
    monkeypatch.setattr(config, "_SOURCE_HOURS_OVERRIDE", None)
    scrapers._BOARDS_READ.clear()
    sweep.begin(NOW)
    yield
    scrapers._BOARDS_READ.clear()
    sweep.begin(NOW)


def _read_before(tmp_path, **boards):
    """Marks of an earlier delivered run: board key -> hours ago (or a dict)."""
    data = {}
    for key, value in boards.items():
        extra = value if isinstance(value, dict) else {"hours": value}
        when = (NOW - timedelta(hours=extra.pop("hours"))).isoformat()
        data[key.replace("__", "|")] = {"covered": when, "asked": when, **extra}
    (tmp_path / "search_marks.json").write_text(json.dumps(data), encoding="utf-8")
    sweep.begin(NOW)


def _old(days):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%d")


def _job(id="a", board="board|personio|acme", days=9, **kw):
    j = {"id": id, "title": "Working Student Data Analytics", "company": "Acme",
         "location": "Düsseldorf, North Rhine-Westphalia, Germany",
         "url": f"https://acme.jobs.personio.de/job/{id}", "source": "Personio",
         "posted_at": _old(days), "description": ENGLISH * 3}
    if board:
        j["_board"] = board
    j.update(kw)
    return j


# -- the rule ---------------------------------------------------------------------
class TestNewOnABoardReadBefore:
    def test_an_old_date_no_longer_drops_an_ad_that_is_new_on_a_known_board(self, tmp_path):
        _read_before(tmp_path, board__personio__acme=24)
        j = _job()
        assert not main._fresh_by_date(j)
        assert main._mark_new_on_read_boards([j]) == (1, 0)
        assert j["_new_on_board"] is True and main._is_fresh_enough(j)

    def test_a_board_read_for_the_first_time_keeps_the_date_rule(self, tmp_path):
        # what already stands on a new board is still judged by its date
        j = _job()
        assert main._mark_new_on_read_boards([j]) == (0, 0)
        assert not main._is_fresh_enough(j) and "_new_on_board" not in j

    def test_the_read_of_this_very_run_does_not_count(self, tmp_path):
        sweep.record("board|personio|acme", True, 24, "read")      # this run read it
        j = _job()
        assert main._mark_new_on_read_boards([j]) == (0, 0)
        assert not main._is_fresh_enough(j)

    @pytest.mark.parametrize("hours,kept", [(1, True), (47, True), (48, True), (49, False), (200, False)])
    def test_the_earlier_read_must_lie_within_the_freshness_rule(self, tmp_path, hours, kept):
        # read longer ago than that, the ad may have stood there for days
        _read_before(tmp_path, board__personio__acme=hours)
        j = _job()
        main._mark_new_on_read_boards([j])
        assert main._is_fresh_enough(j) is kept

    def test_a_catch_up_day_widens_it_with_the_rule(self, tmp_path, monkeypatch):
        _read_before(tmp_path, board__personio__acme=100)
        monkeypatch.setattr(config, "is_catchup", lambda today=None: True)
        j = _job(source="XING", url="https://www.xing.com/jobs/x-1")
        main._mark_new_on_read_boards([j])
        assert main._is_fresh_enough(j)

    def test_an_ad_without_a_board_keeps_the_date_rule(self, tmp_path):
        _read_before(tmp_path, board__personio__acme=24)
        j = _job(board="", source="linkedin")
        assert main._mark_new_on_read_boards([j]) == (0, 0)
        assert not main._is_fresh_enough(j)

    def test_an_ad_that_is_fresh_by_its_date_is_left_alone(self, tmp_path):
        _read_before(tmp_path, board__personio__acme=24)
        j = _job(days=0)
        assert main._mark_new_on_read_boards([j]) == (0, 0)
        assert main._is_fresh_enough(j) and "_new_on_board" not in j

    def test_filler_on_xing_is_not_new_only_a_new_id_is(self, tmp_path):
        _read_before(tmp_path, board__xing__all=24)
        filler = _job(id="f", board="board|xing|all", source="XING", _board_new=False)
        fresh_id = _job(id="n", board="board|xing|all", source="XING", _board_new=True)
        assert main._mark_new_on_read_boards([filler, fresh_id]) == (1, 0)
        assert not main._is_fresh_enough(filler) and main._is_fresh_enough(fresh_id)

    def test_an_ad_waiting_for_its_text_is_not_touched(self, tmp_path):
        _read_before(tmp_path, board__personio__acme=24)
        j = _job(id="retry")
        assert main._mark_new_on_read_boards([j], skip_ids={"retry"}) == (0, 0)

    def test_an_internship_within_its_own_longer_limit_needs_no_help(self, tmp_path):
        _read_before(tmp_path, board__personio__acme=24)
        j = _job(title="Internship Data Science", days=9)
        assert main._fresh_by_date(j)
        assert main._mark_new_on_read_boards([j]) == (0, 0)

    def test_marks_that_cannot_be_read_keep_the_date_rule(self, monkeypatch):
        def boom(key):
            raise RuntimeError("no marks")
        monkeypatch.setattr(sweep, "covered_until", boom)
        j = _job()
        assert main._new_on_a_read_board(j) is False

    @settings(max_examples=150, deadline=None)
    @given(st.one_of(st.none(), st.text(max_size=40), st.integers(), st.lists(st.text(max_size=5), max_size=3)),
           st.one_of(st.none(), st.booleans(), st.text(max_size=5)),
           st.one_of(st.none(), st.text(max_size=30), st.datetimes().map(lambda d: d.isoformat())))
    def test_any_board_value_gives_a_verdict(self, board, new, covered):
        sweep._marks.clear()
        if isinstance(board, str):
            sweep._marks[board] = {"covered": covered}
        j = {"id": "x", "_board": board, "_board_new": new}
        try:
            verdict = main._new_on_a_read_board(j)
        finally:
            sweep._marks.clear()
        assert isinstance(verdict, bool)


# -- a limit per run ---------------------------------------------------------------
class TestALimitPerRun:
    def test_past_the_limit_the_printed_date_decides_again(self, tmp_path):
        _read_before(tmp_path, board__personio__acme=24)
        jobs = [_job(id=f"j{i}") for i in range(main._NEW_ON_BOARD_CAP + 10)]
        assert main._mark_new_on_read_boards(jobs) == (main._NEW_ON_BOARD_CAP, 10)
        assert sum(1 for j in jobs if main._is_fresh_enough(j)) == main._NEW_ON_BOARD_CAP
        last = jobs[-1]
        assert not main._fresh_or_noted(last)
        assert last["_drop_note"].endswith("new on a board read before, but past the run's limit")

    def test_the_limit_is_sixty(self):
        assert main._NEW_ON_BOARD_CAP == 60


# -- through the filter step -------------------------------------------------------
class TestThroughTheFilterStep:
    @staticmethod
    def _run(monkeypatch, jobs, state=None):
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "_ever_shown_keys", lambda: set())
        monkeypatch.setattr(main, "_ever_shown_url_hashes", lambda: set())
        monkeypatch.setattr(main, "_fill_missing_bodies", lambda js: [])
        monkeypatch.setattr(main, "_skill_radar", lambda js: None)
        monkeypatch.setattr(main, "_body_retry_ids", lambda: set())
        out = main.node_filter({"seen": {}, "all_jobs": jobs, **(state or {})})
        return out, {r["id"]: r for r in main._RUN_AUDIT}

    def test_the_ad_reaches_the_model_and_the_old_copy_of_the_rule_would_have_dropped_it(self, tmp_path,
                                                                                         monkeypatch, capsys):
        _read_before(tmp_path, board__personio__acme=24)
        known = _job(id="known")
        first_time = _job(id="first", board="board|personio|other", company="Other",
                          title="Working Student Data Engineering")
        out, audit = self._run(monkeypatch, [known, first_time])
        assert [j["id"] for j in out["new_jobs"]] == ["known"]
        assert audit["first"]["filter"] == "Freshness filter"
        assert audit["first"]["why"].startswith(f"dated {_old(9)}, ")
        assert "health_warnings" not in out
        assert "1 ads kept although the date they print is older" in capsys.readouterr().out

    def test_far_too_many_new_ads_is_a_warning_in_the_digest(self, tmp_path, monkeypatch):
        _read_before(tmp_path, board__personio__acme=24)
        jobs = [_job(id=f"j{i}", title=f"Working Student Data Analytics {i}", company=f"Acme {i}")
                for i in range(main._NEW_ON_BOARD_CAP + 5)]
        out, _ = self._run(monkeypatch, jobs, {"health_warnings": ["an earlier warning"]})
        assert out["health_warnings"][0] == "an earlier warning"
        assert "65 ads looked new on job boards that were read before" in out["health_warnings"][1]
        assert len(out["new_jobs"]) == main._NEW_ON_BOARD_CAP

    def test_the_audit_line_of_the_judged_ad_says_why_it_was_kept(self, tmp_path, monkeypatch):
        _read_before(tmp_path, board__personio__acme=24)
        j = _job()
        main._mark_new_on_read_boards([j])
        j.update(score=62, reason="Fits.", _track="DS")
        plain = _job(id="plain", days=0, score=50, reason="Fits.", _track="DS")
        monkeypatch.setattr(main, "score_jobs", lambda jobs: jobs)
        monkeypatch.setattr(main, "_apply_ghost_penalty", lambda scored: 0)
        main._RUN_AUDIT.clear()
        main.node_score({"new_jobs": [j, plain]})
        rows = {r["id"]: r for r in main._RUN_AUDIT if r.get("stage") == "score"}
        assert rows["a"]["kept"] == "new on a board read before"
        assert "kept" not in rows["plain"]

    def test_the_email_says_new_on_this_board(self):
        j = _job(score=70, reason="Fits.", _new_on_board=True)
        plain = _job(id="b", score=70, reason="Fits.", title="Working Student Data Engineering")
        html = notifier._build_html([j, plain])
        assert html.count("new on this board") == 1
        assert "new on this board" not in notifier._build_html([plain])


# -- the scrapers name the boards they read ---------------------------------------
class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class TestTheScrapersNameTheirBoards:
    def test_greenhouse_tags_the_rows_and_names_the_board(self, monkeypatch):
        payload = {"jobs": [{"title": "Working Student Data", "location": {"name": "Berlin, Germany"},
                             "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "content": "x",
                             "updated_at": "2026-11-01T10:00:00Z"},
                            {"title": "Account Executive", "location": {"name": "New York"},
                             "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "content": "x"}]}
        monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: _Resp(payload))
        rows = scrapers._greenhouse_board("acme")
        assert [r["_board"] for r in rows] == ["board|greenhouse|acme"]
        assert "board|greenhouse|acme" in scrapers._BOARDS_READ["scrape_greenhouse"]

    def test_a_board_with_jobs_but_none_for_germany_was_still_read(self, monkeypatch):
        payload = {"jobs": [{"title": "Account Executive", "location": {"name": "New York"},
                             "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "content": "x"}]}
        monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: _Resp(payload))
        assert scrapers._greenhouse_board("acme") == []
        assert "board|greenhouse|acme" in scrapers._BOARDS_READ["scrape_greenhouse"]

    @pytest.mark.parametrize("response", [_Resp({"jobs": []}), _Resp({"status": 404}, status=404),
                                          _Resp(None)])
    def test_an_empty_or_failed_answer_names_no_board(self, monkeypatch, response):
        # an answer with nothing in it does not show that the board was read
        monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: response)
        assert scrapers._greenhouse_board("acme") == []
        assert scrapers._BOARDS_READ == {}

    def test_lever(self, monkeypatch):
        payload = [{"text": "Working Student Data", "categories": {"location": "Berlin, Germany"},
                    "hostedUrl": "https://jobs.lever.co/acme/1", "descriptionPlain": "x",
                    "createdAt": 1762000000000}]
        monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: _Resp(payload))
        (row,) = scrapers._lever_board("acme")
        assert row["_board"] == "board|lever|acme"
        assert "board|lever|acme" in scrapers._BOARDS_READ["scrape_lever"]

    def test_personio(self, monkeypatch):
        xml = ("<workzag-jobs><position><id>7</id><name>Werkstudent Data</name><office>Bonn</office>"
               "<createdAt>2026-11-01T10:00:00+00:00</createdAt></position></workzag-jobs>")
        monkeypatch.setattr(scrapers, "PERSONIO_SLUGS", ["acme", "empty"])
        monkeypatch.setattr(scrapers.requests, "get",
                            lambda url, **kw: _Resp(text=xml if "acme" in url else "<workzag-jobs></workzag-jobs>"))
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)
        (row,) = scrapers.scrape_personio()
        assert row["_board"] == "board|personio|acme"
        assert list(scrapers._BOARDS_READ["scrape_personio"]) == ["board|personio|acme"]

    def test_ashby(self, monkeypatch):
        payload = {"jobs": [{"title": "Working Student Data", "location": "Berlin, Germany",
                             "jobUrl": "https://jobs.ashbyhq.com/acme/1", "descriptionPlain": "x",
                             "publishedAt": "2026-11-01T10:00:00Z"}]}
        monkeypatch.setattr(scrapers, "ASHBY_SLUGS", ["acme"])
        monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: _Resp(payload))
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)
        (row,) = scrapers.scrape_ashby()
        assert row["_board"] == "board|ashby|acme"
        assert "board|ashby|acme" in scrapers._BOARDS_READ["scrape_ashby"]

    def test_recruitee(self, monkeypatch):
        payload = {"offers": [{"title": "Working Student Data", "city": "Köln", "country": "Germany",
                               "careers_url": "https://acme.recruitee.com/o/1", "description": "x",
                               "created_at": "2026-11-01 10:00:00 UTC"}]}
        monkeypatch.setattr(scrapers, "RECRUITEE_SLUGS", ["acme"])
        monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: _Resp(payload))
        monkeypatch.setattr(scrapers.time, "sleep", lambda s: None)
        (row,) = scrapers.scrape_recruitee()
        assert row["_board"] == "board|recruitee|acme"
        assert "board|recruitee|acme" in scrapers._BOARDS_READ["scrape_recruitee"]

    def test_the_boards_count_as_read_once_the_result_is_taken(self):
        scrapers._board_read("scrape_ashby", "Ashby", "acme", [])
        assert sweep.outcomes() == {}
        scrapers._boards_done("scrape_ashby")
        assert sweep.outcomes()["board|ashby|acme"]["complete"] is True
        assert scrapers._BOARDS_READ == {}
        scrapers._boards_done("scrape_ashby")                    # nothing left: no error

    def test_a_scraper_that_timed_out_names_no_board(self, monkeypatch):
        def fake(scraper):
            scrapers._board_read(scraper.__name__, "Ashby", "acme", [])
            return [], scraper.__name__ == "scrape_ashby"        # ashby timed out
        for name in dir(scrapers):
            if name.startswith("scrape_") and name not in ("scrape_all", "scrape_company_page") \
                    and callable(getattr(scrapers, name)):
                def stub(_name=name):
                    return []
                stub.__name__ = name
                monkeypatch.setattr(scrapers, name, stub)
        monkeypatch.setattr(scrapers, "_run_scraper_guarded", fake)
        scrapers.scrape_all()
        rows = sweep.outcomes("board|")
        assert rows                                               # the others were recorded
        assert "scrape_ashby" in scrapers._BOARDS_READ            # the timed-out one was not
        scrapers._BOARDS_READ.clear()

    def test_a_new_run_forgets_boards_left_over(self, monkeypatch):
        scrapers._board_read("scrape_ashby", "Ashby", "left over", [])
        for name in dir(scrapers):
            if name.startswith("scrape_") and name not in ("scrape_all", "scrape_company_page") \
                    and callable(getattr(scrapers, name)):
                def stub(_name=name):
                    return []
                stub.__name__ = name
                monkeypatch.setattr(scrapers, name, stub)
        scrapers.scrape_all()
        assert sweep.outcomes("board|") == {}


# -- XING tells new postings by their id --------------------------------------------
class TestXingKnowsWhatIsNew:
    @staticmethod
    def _xing(monkeypatch, ids):
        urls = [f"https://www.xing.com/jobs/koeln-werkstudent-data-{i}" for i in sorted(ids, reverse=True)]
        fetched = []

        def page(url):
            fetched.append(url)
            return [scrapers.job("Werkstudent Data", "Acme", "Köln, Germany", url, "XING", "x",
                                 posted_at="2026-11-01")]
        monkeypatch.setattr(scrapers, "_xing_candidates", lambda: urls)
        monkeypatch.setattr(scrapers, "_xing_page", page)
        return fetched

    def test_without_an_earlier_run_nothing_is_called_new(self, monkeypatch):
        fetched = self._xing(monkeypatch, range(1000, 1100))
        rows = scrapers.scrape_xing()
        assert len(fetched) == scrapers._XING_DESC_CAP == 60
        assert all(r["_board"] == "board|xing|all" and r["_board_new"] is False for r in rows)
        assert scrapers._BOARDS_READ["scrape_xing"]["board|xing|all"] == {"top_id": 1099}

    def test_every_posting_above_the_last_newest_id_is_read_and_called_new(self, tmp_path, monkeypatch):
        _read_before(tmp_path, board__xing__all={"hours": 24, "top_id": 1009})
        fetched = self._xing(monkeypatch, range(1000, 1100))             # 90 newer than 1009
        rows = scrapers.scrape_xing()
        assert len(fetched) == 90, "all of the new ones, although more than the usual sixty"
        assert sorted(scrapers._xing_id(u) for u in fetched) == list(range(1010, 1100))
        assert all(r["_board_new"] for r in rows)

    def test_the_rest_is_filled_up_as_before_and_is_not_new(self, tmp_path, monkeypatch):
        _read_before(tmp_path, board__xing__all={"hours": 24, "top_id": 1094})
        fetched = self._xing(monkeypatch, range(1000, 1100))             # 5 newer than 1094
        rows = scrapers.scrape_xing()
        assert len(fetched) == 60
        new = [r for r in rows if r["_board_new"]]
        assert sorted(scrapers._xing_id(r["url"]) for r in new) == [1095, 1096, 1097, 1098, 1099]

    def test_a_flood_of_new_ids_is_capped(self, tmp_path, monkeypatch):
        _read_before(tmp_path, board__xing__all={"hours": 24, "top_id": 10})
        fetched = self._xing(monkeypatch, range(1000, 1400))
        scrapers.scrape_xing()
        assert len(fetched) == scrapers._XING_NEW_CAP == 200

    def test_the_newest_id_is_kept_by_a_delivered_run_only(self, tmp_path, monkeypatch):
        self._xing(monkeypatch, range(1000, 1010))
        scrapers.scrape_xing()
        assert sweep.mark_value("board|xing|all", "top_id") is None       # not taken, not saved
        scrapers._boards_done("scrape_xing")
        sweep.save()
        sweep.begin(NOW + timedelta(hours=7))
        assert sweep.mark_value("board|xing|all", "top_id") == 1009

    def test_a_failed_sitemap_read_names_no_board(self, monkeypatch):
        def boom():
            raise RuntimeError("down")
        monkeypatch.setattr(scrapers, "_xing_candidates", boom)
        assert scrapers.scrape_xing() == []
        assert scrapers._BOARDS_READ == {}

    @pytest.mark.parametrize("url,expected", [
        ("https://www.xing.com/jobs/koeln-werkstudent-data-159212314", 159212314),
        ("https://www.xing.com/jobs/no-number", 0), ("", 0), (None, 0)])
    def test_the_id_is_the_number_at_the_end(self, url, expected):
        assert scrapers._xing_id(url) == expected

    @pytest.mark.parametrize("value", ["many", None, 3.5, True, [1]])
    def test_only_a_whole_number_is_kept_with_a_mark(self, tmp_path, value):
        sweep.record("board|xing|all", True, 24, "read", top_id=value)
        sweep.save()
        assert "top_id" not in json.loads((tmp_path / "search_marks.json").read_text())["board|xing|all"]


# -- the LinkedIn lists ---------------------------------------------------------------
class TestTheLinkedInLists:
    def test_a_card_from_a_list_names_its_list_and_a_keyword_card_does_not(self, monkeypatch):
        from test_full_daily_sweep import FakeLinkedIn, Lane
        lane = Lane(monkeypatch, fake=FakeLinkedIn(default=3), queries=("Junior Data Scientist Germany",))
        jobs = lane.run(now=NOW)
        boards = {j["title"].rsplit(" ", 1)[0]: j.get("_board") for j in jobs}
        assert boards["Werkstudent"] in ("linkedin|nrw|werkstudent", "linkedin|de|werkstudent")
        assert boards["Junior Data Scientist Germany"] is None
        assert {j.get("_board") for j in jobs} == {"linkedin|nrw|werkstudent", "linkedin|de|werkstudent", None}

    def test_a_list_read_as_planned_before_makes_its_new_cards_new(self, tmp_path):
        _read_before(tmp_path, linkedin__nrw__werkstudent=7)
        j = _job(board="linkedin|nrw|werkstudent", source="linkedin", days=5,
                 url="https://www.linkedin.com/jobs/view/870000001")
        assert main._mark_new_on_read_boards([j]) == (1, 0)


# -- every copy of a job counts as read -------------------------------------------------
class TestEveryCopyOfAJobCountsAsRead:
    @staticmethod
    def _copy(id, source, description="x" * 50, **kw):
        j = {"id": id, "title": "Working Student Data Analytics", "company": "Acme GmbH",
             "location": "Köln, Germany", "url": f"https://example.org/{id}", "source": source,
             "description": description, "posted_at": ""}
        j.update(kw)
        return j

    def test_the_kept_copy_carries_the_ids_of_the_copies_merged_into_it(self):
        a, b, c = self._copy("a", "linkedin"), self._copy("b", "Personio"), self._copy("c", "indeed")
        (kept,) = main._dedup_cross_source([a, b, c])
        assert sorted([kept["id"]] + kept["_merged_ids"]) == ["a", "b", "c"]
        assert kept["id"] not in kept["_merged_ids"]

    def test_a_job_without_copies_carries_none(self):
        (kept,) = main._dedup_cross_source([self._copy("a", "linkedin")])
        assert "_merged_ids" not in kept

    def _persist(self, monkeypatch, tmp_path, jobs, **state):
        saved = {}
        monkeypatch.setattr(main.storage, "read_text", lambda name: None)
        monkeypatch.setattr(main.storage, "write_text", lambda name, text: None)
        monkeypatch.setattr(main, "_BODY_RETRY_FILE", tmp_path / "body_retry.json")
        monkeypatch.setattr(main, "_SCORE_RETRY_FILE", tmp_path / "score_retry.json")
        monkeypatch.setattr(main, "save_seen", saved.update)
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "save_digested", lambda d: None)
        monkeypatch.setattr(main, "_record_shown", lambda top, near: None)
        monkeypatch.setattr(main, "_update_source_registry", lambda c: None)
        monkeypatch.setattr(main, "_record_run_stats", lambda s: None)
        import metrics
        monkeypatch.setattr(metrics, "publish", lambda **kw: None)
        main.node_persist({"dry_run": False, "seen": {}, "email_ok": True, "all_jobs": jobs,
                           "scored": [], "top": [], "near": [], **state})
        return saved

    def test_a_delivered_run_marks_every_copy_as_seen(self, monkeypatch, tmp_path):
        (kept,) = main._dedup_cross_source([self._copy("a", "linkedin"), self._copy("b", "Personio")])
        saved = self._persist(monkeypatch, tmp_path, [kept])
        assert sorted(saved) == ["a", "b"]

    def test_copies_of_an_ad_that_waits_for_its_text_stay_unseen_with_it(self, monkeypatch, tmp_path):
        (kept,) = main._dedup_cross_source([self._copy("a", "linkedin"), self._copy("b", "Personio")])
        saved = self._persist(monkeypatch, tmp_path, [kept], body_unresolved=[kept["id"]])
        assert saved == {}
