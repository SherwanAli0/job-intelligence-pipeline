"""Four gaps left after the LinkedIn lane and the rule for ads new on a known board
(2026-11-17, owner: "Yes, build them").

1. LinkedIn keyword searches are read five pages deep again: the lane's first
   run from the production address was not refused once.
2. A board read for the first time: what stands on it passes the age rule too,
   within the run's limit, and what is past the limit waits instead of being
   marked as seen.
3. Workable, Teamtailor and Breezy name the boards they read, like the other
   feeds that hand over a company's whole list.
4. DLR: its job pages answer 403 from a bot defence since 17 November. The
   site is not asked in another way. What its sitemap says (place, title,
   link) goes to the digest as a list to check by hand.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import config
import graph
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
    scrapers._RMK_REFUSED.clear()
    sweep.begin(NOW)
    yield
    scrapers._BOARDS_READ.clear()
    scrapers._RMK_REFUSED.clear()
    sweep.begin(NOW)


def _read_before(tmp_path, **boards):
    data = {}
    for key, hours in boards.items():
        when = (NOW - timedelta(hours=hours)).isoformat()
        data[key.replace("__", "|")] = {"covered": when, "asked": when}
    (tmp_path / "search_marks.json").write_text(json.dumps(data), encoding="utf-8")
    sweep.begin(NOW)


def _old(days):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%d")


def _job(id="a", board="board|workable|acme", days=20, **kw):
    j = {"id": id, "title": "Working Student Data Analytics", "company": "Acme",
         "location": "Düsseldorf, North Rhine-Westphalia, Germany",
         "url": f"https://apply.workable.com/j/{id}", "source": "Workable",
         "posted_at": _old(days), "description": ENGLISH * 3}
    if board:
        j["_board"] = board
    j.update(kw)
    return j


def _filter(monkeypatch, jobs, state=None):
    monkeypatch.setattr(main, "load_digested", lambda: {})
    monkeypatch.setattr(main, "_ever_shown_keys", lambda: set())
    monkeypatch.setattr(main, "_ever_shown_url_hashes", lambda: set())
    monkeypatch.setattr(main, "_fill_missing_bodies", lambda js: [])
    monkeypatch.setattr(main, "_skill_radar", lambda js: None)
    monkeypatch.setattr(main, "_body_retry_ids", lambda: set())
    out = main.node_filter({"seen": {}, "all_jobs": jobs, **(state or {})})
    return out, {r["id"]: r for r in main._RUN_AUDIT}


# -- 1. LinkedIn: five pages --------------------------------------------------------
class TestLinkedInReadsFivePagesAgain:
    def test_every_keyword_search_is_planned_five_pages_deep(self):
        plan = scrapers._linkedin_plan()
        assert scrapers._LI_DEPTH == 50
        assert {s["depth"] for s in plan if not s["pool"]} == {50}

    def test_the_lane_has_forty_minutes(self):
        assert scrapers._LI_SECONDS == 2400

    def test_the_depth_is_a_setting(self, monkeypatch):
        monkeypatch.setattr(scrapers, "_LI_DEPTH", 20)
        assert {s["depth"] for s in scrapers._linkedin_plan() if not s["pool"]} == {20}


# -- 2. a board read for the first time ----------------------------------------------
class TestABoardReadForTheFirstTime:
    def test_what_stands_on_a_new_board_passes_the_age_rule(self):
        j = _job()
        assert not main._fresh_by_date(j)
        assert main._board_standing(j) == "first"
        assert main._mark_new_on_read_boards([j]) == (1, 0)
        assert j["_new_on_board"] is True and j["_first_read"] is True
        assert main._is_fresh_enough(j)

    def test_an_ad_new_on_a_known_board_is_not_called_a_first_read(self, tmp_path):
        _read_before(tmp_path, board__workable__acme=24)
        j = _job()
        assert main._board_standing(j) == "new"
        main._mark_new_on_read_boards([j])
        assert j["_new_on_board"] is True and "_first_read" not in j

    @pytest.mark.parametrize("key", ["linkedin|nrw|werkstudent", "linkedin|q|werkstudent data"])
    def test_a_linkedin_list_has_no_such_backlog(self, key):
        # it is asked for a time window, so nothing old stands on it
        j = _job(board=key, source="linkedin")
        assert main._board_standing(j) == ""
        assert main._mark_new_on_read_boards([j]) == (0, 0)

    def test_xing_has_no_such_backlog(self):
        # on a first read XING calls nothing new (it tells new postings by their id)
        j = _job(board="board|xing|all", source="XING", _board_new=False)
        assert main._board_standing(j) == ""

    def test_ads_new_on_known_boards_go_before_a_new_boards_backlog(self, tmp_path, monkeypatch):
        _read_before(tmp_path, board__personio__known=24)
        monkeypatch.setattr(main, "_NEW_ON_BOARD_CAP", 3)
        backlog = [_job(id=f"b{i}") for i in range(4)]
        known = [_job(id=f"k{i}", board="board|personio|known", source="Personio") for i in range(2)]
        assert main._mark_new_on_read_boards(backlog + known) == (3, 3)
        assert [j["id"] for j in backlog + known if j.get("_new_on_board")] == ["b0", "k0", "k1"]
        assert [j["id"] for j in backlog if j.get("_new_on_board_over")] == ["b1", "b2", "b3"]

    def test_the_email_and_the_audit_say_newly_added_board(self, monkeypatch):
        j = _job(score=70, reason="Fits.", _track="DS")
        main._mark_new_on_read_boards([j])
        html = notifier._build_html([j])
        assert html.count("from a newly added board") == 1 and "new on this board" not in html
        monkeypatch.setattr(main, "score_jobs", lambda jobs: jobs)
        monkeypatch.setattr(main, "_apply_ghost_penalty", lambda scored: 0)
        main._RUN_AUDIT.clear()
        main.node_score({"new_jobs": [j]})
        (row,) = [r for r in main._RUN_AUDIT if r.get("stage") == "score"]
        assert row["kept"] == "first read of its board"

    def test_the_log_counts_both_kinds(self, tmp_path, capsys):
        _read_before(tmp_path, board__personio__known=24)
        jobs = [_job(id="b"), _job(id="k", board="board|personio|known", source="Personio")]
        main._mark_new_on_read_boards(jobs)
        out = capsys.readouterr().out
        assert "2 ads kept" in out and "1 new on a board that was read before, 1 on a board read for the first time" in out


class TestAnAdPastTheLimitWaits:
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
        stats = []
        monkeypatch.setattr(main, "_record_run_stats", stats.append)
        import metrics
        monkeypatch.setattr(metrics, "publish", lambda **kw: None)
        main.node_persist({"dry_run": False, "seen": {}, "email_ok": True, "all_jobs": jobs,
                           "scored": [], "top": [], "near": [], **state})
        return saved, stats

    def test_it_is_left_out_of_the_seen_memory_and_passes_in_the_next_run(self, monkeypatch, tmp_path):
        monkeypatch.setattr(main, "_NEW_ON_BOARD_CAP", 2)
        jobs = [_job(id=f"b{i}", title=f"Working Student Data Analytics {i}", company=f"Acme {i}")
                for i in range(5)]
        out, audit = _filter(monkeypatch, jobs)
        assert [j["id"] for j in out["new_jobs"]] == ["b0", "b1"]
        assert out["held_over"] == ["b2", "b3", "b4"]
        assert audit["b4"]["filter"] == "Freshness filter" and "waits for a later run" in audit["b4"]["why"]
        assert "3 older ads that are new to the pipeline wait for the next runs" in out["health_warnings"][-1]

        saved, stats = self._persist(monkeypatch, tmp_path, jobs, held_over=out["held_over"])
        assert sorted(saved) == ["b0", "b1"], "the three that wait are not marked as seen"
        assert stats[0]["held_over"] == 3

        # the next run: the board has been read before, and the three are still unseen
        _read_before(tmp_path, board__workable__acme=7)
        rest = [_job(id=f"b{i}", title=f"Working Student Data Analytics {i}", company=f"Acme {i}")
                for i in (2, 3, 4)]
        out, _ = _filter(monkeypatch, rest)
        assert [j["id"] for j in out["new_jobs"]] == ["b2", "b3"]
        assert out["held_over"] == ["b4"]

    def test_nothing_waits_when_the_limit_is_not_reached(self, monkeypatch):
        out, _ = _filter(monkeypatch, [_job()])
        assert out["held_over"] == [] and "health_warnings" not in out

    def test_the_state_carries_the_two_new_lists(self):
        assert {"by_hand", "held_over"} <= set(graph.RunState.__annotations__)


# -- 3. Workable, Teamtailor, Breezy -------------------------------------------------
class _Resp:
    def __init__(self, payload=None, status=200, content=b""):
        self._payload, self.status_code, self.content = payload, status, content
        self.text = content.decode("utf-8", "replace") if content else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


WORKABLE = {"name": "Acme", "jobs": [
    {"title": "Working Student Data", "url": "https://apply.workable.com/j/AAA", "city": "Köln",
     "country": "Germany", "description": "<p>x</p>", "published_on": "2026-11-01"},
    {"title": "Account Executive", "url": "https://apply.workable.com/j/BBB", "city": "Boston",
     "country": "United States", "description": "<p>x</p>", "published_on": "2026-11-01"}]}
TEAMTAILOR = (b'<rss xmlns:tt="https://teamtailor.com/locations"><channel><item><title>Werkstudent Data</title>'
              b'<link>https://acme.teamtailor.com/jobs/1</link><remoteStatus>hybrid</remoteStatus>'
              b'<tt:locations><tt:location><tt:city>Dortmund</tt:city><tt:country>Germany</tt:country>'
              b'</tt:location></tt:locations><description>x</description>'
              b'<pubDate>Sat, 01 Nov 2026 10:00:00 +0000</pubDate></item></channel></rss>')
BREEZY = [{"name": "Working Student Data", "url": "https://acme.breezy.hr/p/1",
           "locations": [{"country": {"id": "DE"}, "city": "Köln"}], "published_date": "2026-11-01T10:00:00Z",
           "type": {"name": "Part-Time"}, "company": {"name": "Acme"}}]


class TestTheThreeFeedsNameTheirBoards:
    @staticmethod
    def _feeds(monkeypatch, answers):
        monkeypatch.setattr(scrapers, "_WORKABLE_ACCOUNTS", (("acme", "Acme"), ("empty", "Empty")))
        monkeypatch.setattr(scrapers, "_TEAMTAILOR_SITES", (("acme.teamtailor.com", "Acme"),))
        monkeypatch.setattr(scrapers, "_BREEZY_ACCOUNTS", (("acme", "Acme"),))

        def get(url, **kw):
            for part, response in answers.items():
                if part in url:
                    return response
            return _Resp(status=404)
        monkeypatch.setattr(scrapers.requests, "get", get)
        return scrapers.scrape_employer_feeds()

    def test_rows_carry_their_board_and_the_boards_are_named(self, monkeypatch):
        rows = self._feeds(monkeypatch, {
            "accounts/acme": _Resp(WORKABLE), "accounts/empty": _Resp({"name": "Empty", "jobs": []}),
            "teamtailor.com": _Resp(content=TEAMTAILOR), "breezy.hr": _Resp(BREEZY)})
        boards = {r["source"]: r["_board"] for r in rows}
        assert boards == {"Workable": "board|workable|acme", "Teamtailor": "board|teamtailor|acme.teamtailor.com",
                          "Breezy": "board|breezy|acme"}
        named = set(scrapers._BOARDS_READ["scrape_employer_feeds"])
        assert named == set(boards.values()), "the account without a job is not named"

    def test_a_board_with_jobs_but_none_for_germany_was_still_read(self, monkeypatch):
        only_abroad = {"name": "Acme", "jobs": [WORKABLE["jobs"][1]]}
        rows = self._feeds(monkeypatch, {"accounts/acme": _Resp(only_abroad)})
        assert rows == []
        assert list(scrapers._BOARDS_READ["scrape_employer_feeds"]) == ["board|workable|acme"]

    def test_a_failed_answer_names_no_board(self, monkeypatch):
        assert self._feeds(monkeypatch, {"accounts/acme": _Resp(status=500)}) == []
        assert scrapers._BOARDS_READ == {}

    def test_an_old_dated_ad_on_such_a_board_is_kept_once_the_board_was_read(self, tmp_path):
        _read_before(tmp_path, board__teamtailor__acme_teamtailor_com=24)
        key = "board|teamtailor|acme.teamtailor.com"
        (tmp_path / "search_marks.json").write_text(json.dumps(
            {key: {"covered": (NOW - timedelta(hours=24)).isoformat(), "asked": NOW.isoformat()}}), encoding="utf-8")
        sweep.begin(NOW)
        j = _job(board=key, source="Teamtailor")
        assert main._board_standing(j) == "new" and main._mark_new_on_read_boards([j]) == (1, 0)


# -- 4. DLR: a site that refuses its pages --------------------------------------------
DLR = "https://jobs.dlr.de"


def _dlr_urls(*slugs):
    return [f"{DLR}/job/{slug}/{1450000000 + i}/" for i, slug in enumerate(slugs)]


def _sitemap(urls):
    return "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"


class TestASiteThatRefusesItsPages:
    @staticmethod
    def _run(monkeypatch, urls, page):
        calls = []
        monkeypatch.setattr(scrapers, "_RMK_SITES", ((DLR, "DLR"),))

        def get(url, **kw):
            calls.append(url)
            if url.endswith("/sitemap.xml"):
                r = _Resp(content=_sitemap(urls).encode("utf-8"))
                return r
            return page(url)
        monkeypatch.setattr(scrapers.requests, "get", get)
        monkeypatch.setattr(scrapers, "SCRAPE_WORKERS", 1)
        rows = scrapers.scrape_research_institutes()
        return rows, [c for c in calls if not c.endswith("/sitemap.xml")]

    def test_a_refused_page_becomes_a_row_to_check_by_hand(self, monkeypatch, capsys):
        urls = _dlr_urls("K%C3%B6ln-Studentische-Hilfskraft-%28wmd%29-KI-gest%C3%BCtzte-Datenauswertung",
                         "Bonn-Praktikum-%28wmd%29-Software-Engineering")
        refused = _Resp({"error": {"status-code": "403", "message": "Policy ID: cq_botdefence_system_policy_t1_fp"}},
                        status=403)
        rows, asked = self._run(monkeypatch, urls, lambda url: refused)
        assert [r["title"] for r in rows] == ["Studentische Hilfskraft (w/m/d) KI gestützte Datenauswertung",
                                              "Praktikum (w/m/d) Software Engineering"]
        assert [r["location"] for r in rows] == ["Köln, Germany", "Bonn, Germany"]
        assert all(r["_by_hand"] and r["_stub"] and r["source"] == "DLR" and r["url"] in urls for r in rows)
        assert "the site refused its pages (2 not read): 2 roles are known by title only" in capsys.readouterr().out

    def test_after_three_refusals_the_site_is_left_alone(self, monkeypatch):
        urls = _dlr_urls(*[f"K%C3%B6ln-Werkstudent-%28wmd%29-Data-{i}" for i in range(10)])
        rows, asked = self._run(monkeypatch, urls, lambda url: _Resp(status=403))
        assert len(asked) == scrapers._RMK_REFUSALS_BEFORE_STOP == 3
        assert len(rows) == 10, "the ones not asked are known from their address all the same"

    def test_exactly_three_also_with_many_workers_at_once(self, monkeypatch):
        import threading
        asked, lock = [], threading.Lock()
        urls = _dlr_urls(*[f"K%C3%B6ln-Werkstudent-%28wmd%29-Data-{i}" for i in range(40)])
        monkeypatch.setattr(scrapers, "_RMK_SITES", ((DLR, "DLR"),))

        def get(url, **kw):
            if url.endswith("/sitemap.xml"):
                return _Resp(content=_sitemap(urls).encode("utf-8"))
            with lock:
                asked.append(url)
            return _Resp(status=403)
        monkeypatch.setattr(scrapers.requests, "get", get)
        monkeypatch.setattr(scrapers, "SCRAPE_WORKERS", 8)
        rows = scrapers.scrape_research_institutes()
        assert len(asked) == 3 and len(rows) == 40

    def test_a_site_that_answers_is_read_in_parallel_after_its_first_pages(self, monkeypatch):
        urls = _dlr_urls(*[f"K%C3%B6ln-Werkstudent-%28wmd%29-Data-{i}" for i in range(12)])
        html = ('<html><head><meta property="og:title" content="Werkstudent (w/m/d) Data">'
                '</head><body><div class="jobdescription">Python</div></body></html>').encode("utf-8")
        monkeypatch.setattr(scrapers, "_RMK_SITES", ((DLR, "DLR"),))
        monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: _Resp(
            content=_sitemap(urls).encode("utf-8") if url.endswith("/sitemap.xml") else html))
        monkeypatch.setattr(scrapers, "SCRAPE_WORKERS", 8)
        rows = scrapers.scrape_research_institutes()
        assert len(rows) == 12 and not any(r.get("_by_hand") for r in rows)
        assert all(r.get("_student") for r in rows)

    def test_a_site_that_answers_gets_no_such_rows(self, monkeypatch):
        urls = _dlr_urls("K%C3%B6ln-Werkstudent-%28wmd%29-Data")
        html = ('<html><head><meta property="og:title" content="Werkstudent (w/m/d) Data">'
                '<meta name="description" content="Werkstudent Data"></head>'
                '<body><div class="jobdescription">Python and SQL</div></body></html>')
        rows, _ = self._run(monkeypatch, urls, lambda url: _Resp(content=html.encode("utf-8")))
        (row,) = rows
        assert row["title"] == "Werkstudent (w/m/d) Data" and "_by_hand" not in row

    def test_a_page_that_is_merely_gone_is_not_listed(self, monkeypatch):
        urls = _dlr_urls("K%C3%B6ln-Werkstudent-%28wmd%29-Data")
        rows, _ = self._run(monkeypatch, urls, lambda url: _Resp(status=404))
        assert rows == []

    @pytest.mark.parametrize("status", [401, 403, 429])
    def test_these_answers_count_as_a_refusal(self, monkeypatch, status):
        urls = _dlr_urls("Bonn-Werkstudent-%28wmd%29-Data")
        rows, _ = self._run(monkeypatch, urls, lambda url: _Resp(status=status))
        assert len(rows) == 1 and rows[0]["_by_hand"]

    @pytest.mark.parametrize("slug,picked", [
        ("K%C3%B6ln-Studentin-%28wmd%29-Datenanalyse", True),            # DLR's own spelling
        ("Bonn-Student-%28fmx%29-Machine-Learning", True),
        ("Stuttgart-Studentische-Hilfskraft-%28wmd%29-KI", True),
        ("Stuttgart-Masterarbeit-%28wmd%29-Lichtkurven", False),
        ("Stuttgart-Duales-Studium-%28wmd%29-Bachelor-of-Engineering", False),
        ("Bonn-Referentin-%28wmd%29-Abteilung-Innovation", False),
    ])
    def test_which_addresses_are_student_roles(self, slug, picked):
        assert bool(scrapers._RMK_STUDENT.search(f"{DLR}/job/{slug}/1/")) is picked

    @pytest.mark.parametrize("url,title,place", [
        (f"{DLR}/job/Sankt-Augustin-Student-%28fmx%29-Machine-Learning-for-Robotics/77/",
         "Student (f/m/x) Machine Learning for Robotics", "Sankt Augustin, Germany"),
        (f"{DLR}/job/K%C3%B6ln-Werkstudent-%28mwd%29-Data", "Werkstudent (m/w/d) Data", "Köln, Germany"),
        (f"{DLR}/job/Nirgendwo-Werkstudent-Data/5/", "Nirgendwo Werkstudent Data", "Deutschland"),
    ])
    def test_title_and_place_come_from_the_address(self, url, title, place):
        row = scrapers._rmk_stub(url, "DLR")
        assert (row["title"], row["location"]) == (title, place)

    @pytest.mark.parametrize("url", ["", None, f"{DLR}/job/", f"{DLR}/job//12/"])
    def test_an_address_without_a_title_gives_no_row(self, url):
        assert scrapers._rmk_stub(url, "DLR") is None


class TestTheListToCheckByHand:
    @staticmethod
    def _stub(id, title, place, **kw):
        row = scrapers.job(title=title, company="DLR", location=place,
                           url=f"{DLR}/job/x-{id}/{id}/", source="DLR", description=title)
        row.update(id=id, _stub=True, _by_hand=True, _student=True, **kw)   # as scrapers._rmk_stub makes it
        return row

    def test_a_row_made_from_an_address_says_that_it_is_a_student_role(self):
        row = scrapers._rmk_stub(f"{DLR}/job/K%C3%B6ln-Studentin-%28wmd%29-Datenanalyse/9/", "DLR")
        assert row["_student"] is True and row["_by_hand"] is True and row["_stub"] is True
        assert main._job_form(row) == "student", "DLR's own spelling counts, because the source says so"

    def test_only_what_a_title_and_a_place_can_pass_is_listed(self, monkeypatch, capsys):
        fetched = []
        rows = [self._stub("ok", "Studentische Hilfskraft (w/m/d) KI gestützte Datenauswertung", "Köln, Germany"),
                self._stub("far", "Werkstudent (w/m/d) Softwareentwicklung", "Oberpfaffenhofen, Germany"),
                self._stub("nontech", "Studentin (w/m/d) Öffentlichkeitsarbeit", "Köln, Germany"),
                self._stub("thesis", "Masterarbeit (w/m/d) Machine Learning", "Köln, Germany")]
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "_ever_shown_keys", lambda: set())
        monkeypatch.setattr(main, "_ever_shown_url_hashes", lambda: set())
        monkeypatch.setattr(main, "_fill_missing_bodies", lambda js: fetched.extend(js) or [])
        monkeypatch.setattr(main, "_skill_radar", lambda js: None)
        monkeypatch.setattr(main, "_body_retry_ids", lambda: set())
        out = main.node_filter({"seen": {}, "all_jobs": rows})
        audit = {r["id"]: r for r in main._RUN_AUDIT}
        assert [j["id"] for j in out["by_hand"]] == ["ok"]
        assert out["new_jobs"] == []
        assert fetched == [], "the site that refused its pages is not asked for them again"
        assert audit["far"]["filter"] == "Focus-area filter" and audit["far"]["why"] == "known by title only"
        assert audit["nontech"]["filter"] == "Tech-relevance filter"
        assert audit["thesis"]["filter"].startswith("Employment-form")
        assert "the list to check by hand is still sent" in capsys.readouterr().out

    def test_the_run_goes_on_to_the_email_when_there_is_only_such_a_list(self):
        g = graph.build_graph({name: (lambda state: {}) for name in
                               ("scrape", "filter", "score", "rank", "notify", "persist")})
        drawn = g.get_graph()
        targets = {e.target for e in drawn.edges if e.source == "filter"}
        assert targets == {"score", "notify", "persist"}

    def test_route_by_route(self, monkeypatch):
        order = []
        nodes = {"scrape": lambda s: order.append("scrape") or {},
                 "score": lambda s: order.append("score") or {},
                 "rank": lambda s: order.append("rank") or {},
                 "notify": lambda s: order.append("notify") or {},
                 "persist": lambda s: order.append("persist") or {}}
        for result, expected in (({"new_jobs": [], "by_hand": [{"id": "x"}]}, ["scrape", "filter", "notify", "persist"]),
                                 ({"new_jobs": [], "by_hand": []}, ["scrape", "filter", "persist"]),
                                 ({"new_jobs": [{"id": "y"}], "by_hand": []},
                                  ["scrape", "filter", "score", "rank", "notify", "persist"])):
            order.clear()
            nodes["filter"] = lambda s, r=result: order.append("filter") or r
            graph.build_graph(nodes).invoke({"dry_run": True}, {"recursion_limit": 25})
            assert order == expected

    def test_the_email_goes_out_for_such_a_list_alone(self, monkeypatch):
        sent = {}

        def send_email(jobs, warnings=None, notes=None, by_hand=None):
            sent.update(jobs=jobs, by_hand=by_hand)
            return True
        monkeypatch.setattr(main, "send_email", send_email)
        monkeypatch.setattr(main, "add_to_notion", lambda jobs: None)
        row = self._stub("ok", "Studentische Hilfskraft (w/m/d) KI", "Köln, Germany")
        assert main.node_notify({"by_hand": [row]}) == {"email_ok": True}
        assert sent == {"jobs": [], "by_hand": [row]}
        sent.clear()
        assert main.node_notify({}) == {"email_ok": True} and sent == {}

    def test_the_digest_shows_the_list_with_links_and_says_why_there_is_no_score(self):
        rows = [self._stub("ok", "Studentische Hilfskraft (w/m/d) KI & <Daten>", "Köln, Germany")]
        html = notifier._build_html([], by_hand=rows)
        assert "To check by hand" in html and "so they have no score" in html
        assert f'href="{rows[0]["url"]}"' in html
        assert "KI &amp; &lt;Daten&gt;" in html and "<Daten>" not in html
        assert "DLR, Köln, Germany" in html

    def test_more_than_fifteen_are_counted_not_listed(self):
        rows = [self._stub(f"r{i}", f"Werkstudent Data {i}", "Köln, Germany") for i in range(18)]
        html = notifier._build_html([], by_hand=rows)
        assert html.count("Werkstudent Data ") == 15 and "and 3 more" in html

    def test_a_digest_without_such_a_list_is_unchanged(self):
        j = _job(score=70, reason="Fits.")
        assert notifier._build_html([j]) == notifier._build_html([j], by_hand=[]) == notifier._build_html([j], None, None, None)
        assert "To check by hand" not in notifier._build_html([j])

    def test_the_subject_names_the_list(self, monkeypatch):
        import smtplib
        sent = {}

        class Server:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, *a):
                pass

            def sendmail(self, sender, to, text):
                sent["text"] = text
        monkeypatch.setenv("GMAIL_USER", "someone@example.org")
        monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
        monkeypatch.setattr(smtplib, "SMTP_SSL", Server)
        rows = [self._stub("ok", "Studentische Hilfskraft KI", "Köln, Germany")]
        assert notifier.send_email([], by_hand=rows) is True
        from email import message_from_string
        from email.header import decode_header, make_header
        subject = str(make_header(decode_header(message_from_string(sent["text"])["Subject"])))
        assert "0 job matches + 1 to check by hand" in subject

    def test_a_copy_that_can_be_read_beats_the_copy_known_by_title_only(self):
        stub = self._stub("stub", "Werkstudent Data Science", "Köln, Germany", company="DLR")
        readable = {"id": "li", "title": "Werkstudent Data Science", "company": "DLR", "location": "Köln, Germany",
                    "url": "https://www.linkedin.com/jobs/view/880000001", "source": "linkedin",
                    "description": ENGLISH * 3, "posted_at": ""}
        for order in ([stub, readable], [readable, stub]):
            (kept,) = main._dedup_cross_source([dict(j) for j in order])
            assert kept["id"] == "li" and kept["_merged_ids"] == ["stub"]

    def test_the_run_statistics_count_the_list(self, monkeypatch, tmp_path):
        stats = []
        monkeypatch.setattr(main.storage, "read_text", lambda name: None)
        monkeypatch.setattr(main.storage, "write_text", lambda name, text: None)
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
        row = self._stub("ok", "Studentische Hilfskraft KI", "Köln, Germany")
        main.node_persist({"dry_run": False, "seen": {}, "email_ok": True, "all_jobs": [row],
                           "scored": [], "top": [], "near": [], "by_hand": [row]})
        assert stats[0]["by_hand"] == 1 and stats[0]["held_over"] == 0
