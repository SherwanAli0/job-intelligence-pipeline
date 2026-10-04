"""Leaks found by the 2026-10-04 audit: jobs the pipeline scraped (or should
have scraped) and then lost before any filter the owner chose could see them.
Each class pins one fix. All offline."""

import inspect
import json

import pytest

import config
import main
import scrapers


def _j(title="Working Student Data", desc="", **kw):
    base = {"id": kw.pop("id", "t"), "title": title, "company": "X", "location": "Köln",
            "url": "https://example.org/job/1", "source": "linkedin",
            "description": desc, "posted_at": ""}
    base.update(kw)
    return base


ENGLISH = ("We are looking for a working student to join our data team. You will build "
           "pipelines in Python and SQL, analyse product data and present the results "
           "to the team. You are enrolled at a university and you enjoy working with "
           "data. Our working language is English and the team is international.")


# ── Step 1a: every query runs on the single daily run ────────────────────────

class TestAllQueriesRunEveryMorning:
    def test_no_query_is_left_out_at_any_hour(self, monkeypatch):
        got = scrapers._jobspy_active_queries()
        assert sorted(got) == sorted(config.SEARCH_QUERIES)
        assert len(got) == len(set(got)) == len(config.SEARCH_QUERIES)

    def test_internship_and_part_time_are_in_the_first_slots(self):
        """JobSpy is time-boxed. A cut must cost depth on every form, never a
        whole form: the old slice put all 18 internship and part-time queries
        in the half that never ran."""
        first = [q.lower() for q in scrapers._jobspy_active_queries()[:9]]
        assert sum("praktik" in q or "intern" in q or "praxissemester" in q for q in first) == 3
        assert sum("teilzeit" in q or "part-time" in q for q in first) == 3

    def test_the_utc_hour_no_longer_selects_a_slice(self):
        src = inspect.getsource(scrapers._jobspy_active_queries)
        assert ".hour" not in src and "SEARCH_QUERIES[:" not in src

    def test_repeated_postings_across_queries_are_not_fetched_twice(self):
        class _DF:
            def __init__(self, rows): self._rows = rows
            def iterrows(self): return iter(enumerate(self._rows))
        rows = [{"job_url": "https://x/1", "title": "A", "company": "C", "location": "Bonn", "site": "indeed"},
                {"job_url": "https://x/2", "title": "B", "company": "C", "location": "Bonn", "site": "indeed"}]
        out, seen = [], set()
        scrapers._jobspy_rows_to_jobs(_DF(rows), out, seen)
        scrapers._jobspy_rows_to_jobs(_DF(rows + [{"job_url": "https://x/3", "title": "D", "company": "C",
                                                   "location": "Bonn", "site": "indeed"}]), out, seen)
        assert [j["url"] for j in out] == ["https://x/1", "https://x/2", "https://x/3"]

    def test_background_grace_covers_the_longer_query_list(self):
        assert scrapers.BACKGROUND_JOIN_SECONDS >= 900


# ── Step 2: a failed description fetch is retried, not buried ────────────────

class TestUnresolvedBodiesAreRetried:
    def _fake_enrich(self, texts):
        def enrich(jobs, quiet=False, screen_titles=True):
            assert screen_titles is False, "the refill must not be title-screened"
            for j in jobs:
                body = texts.get(j["id"])
                if body:
                    j["description"] = body
        return enrich

    def test_failed_fetch_is_reported_as_unresolved(self, monkeypatch):
        jobs = [_j(id="ok"), _j(id="fail"), _j(id="has-body", desc=ENGLISH)]
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions",
                            self._fake_enrich({"ok": ENGLISH}))
        unresolved = main._fill_missing_bodies(jobs)
        assert unresolved == ["fail"]
        assert jobs[1]["_body_unresolved"] is True
        assert "_body_unresolved" not in jobs[0]

    def test_ads_over_the_cap_are_unresolved_too(self, monkeypatch):
        monkeypatch.setattr(main, "_BODY_FETCH_CAP", 2)
        jobs = [_j(id=f"j{i}") for i in range(4)]
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions",
                            self._fake_enrich({"j0": ENGLISH, "j1": ENGLISH}))
        assert main._fill_missing_bodies(jobs) == ["j2", "j3"]

    def test_seniority_word_in_a_student_title_is_still_fetched(self, monkeypatch):
        """'Intern - Office of the Chief Data Officer' passed the form filter;
        the title screen then skipped the fetch, so it could only fail."""
        calls = []
        monkeypatch.setattr(scrapers, "_fetch_full_description",
                            lambda url: calls.append(url) or ENGLISH)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        j = _j("Intern - Office of the Chief Data Officer", id="chief",
               url="https://example.org/job/chief")
        assert main._fill_missing_bodies([j]) == []
        assert calls == ["https://example.org/job/chief"]
        assert main._is_english_friendly(j)

    def test_retry_state_gives_up_after_three_attempts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "_BODY_RETRY_FILE", tmp_path / "body_retry.json")
        assert main._body_retry_update(["a", "b"]) == {"a", "b"}      # attempt 1
        assert main._body_retry_update(["a"]) == {"a"}                # attempt 2; b resolved
        assert json.loads((tmp_path / "body_retry.json").read_text()) == {"a": 2}
        assert main._body_retry_update(["a"]) == set()                # attempt 3: give up
        assert json.loads((tmp_path / "body_retry.json").read_text()) == {}

    def test_persist_leaves_unresolved_ids_unseen(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "_BODY_RETRY_FILE", tmp_path / "body_retry.json")
        saved = {}
        monkeypatch.setattr(main, "save_seen", lambda s: saved.update(s))
        monkeypatch.setattr(main, "load_digested", lambda: {})
        monkeypatch.setattr(main, "save_digested", lambda d: None)
        monkeypatch.setattr(main, "_record_shown", lambda top, near: None)
        monkeypatch.setattr(main, "_update_source_registry", lambda c: None)
        monkeypatch.setattr(main, "_record_run_stats", lambda s: None)
        import metrics
        monkeypatch.setattr(metrics, "publish", lambda **kw: None)
        state = {"dry_run": False, "seen": {}, "email_ok": True,
                 "all_jobs": [_j(id="fine"), _j(id="stub")], "body_unresolved": ["stub"]}
        main.node_persist(state)
        assert "fine" in saved and "stub" not in saved

    def test_a_metadata_stub_never_passes_the_language_test(self):
        """SmartRecruiters rows carry 'Industry: ... Function: ...' built from
        the listing. That is English for a German ad too."""
        stub = ("Industry: Automotive and mobility solutions for the world of tomorrow "
                "Function: Information Technology and software engineering services "
                "Department: Corporate research and advance engineering unit "
                "Experience level: Internship Employment type: Part-time position")
        j = _j("Werkstudent IT", desc=stub)
        assert main._reads_as_english(stub.lower()), "the stub alone reads as English"
        j["_stub"] = True
        assert not main._is_english_friendly(j)

    def test_a_flagged_stub_is_fetched_even_when_it_is_long(self, monkeypatch):
        long_stub = "Industry: Automotive. " * 30                    # > 400 chars, > 25 words
        j = _j("Working Student IT", desc=long_stub, id="sr")
        j["_stub"] = True
        monkeypatch.setattr(scrapers, "_fetch_full_description", lambda url: ENGLISH * 3)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        assert main._fill_missing_bodies([j]) == []
        assert "_stub" not in j and j["description"].startswith("We are looking")

    def test_filter_hands_unresolved_ids_to_persist(self):
        src = inspect.getsource(main.node_filter)
        assert "body_unresolved = _fill_missing_bodies(new_jobs)" in src
        assert '"body_unresolved": body_unresolved' in src
        from graph import RunState
        assert "body_unresolved" in RunState.__annotations__


# ── Step 3: German cities survive the Greenhouse / Lever scrapers ────────────

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


def _gh(monkeypatch, jobs):
    monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _Resp({"jobs": jobs}))
    return scrapers._greenhouse_board("trivago")


def _lv(monkeypatch, postings):
    monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _Resp(postings))
    return scrapers._lever_board("acme")


class TestGermanCitiesSurviveTheAtsGate:
    @pytest.mark.parametrize("loc", [
        "Düsseldorf", "Köln", "Cologne", "Bonn", "Bielefeld", "Berlin", "Munich",
        "Aachen", "Cologne, Germany", "Germany", "Remote - Germany", "Remote", "",
    ])
    def test_greenhouse_keeps_german_and_remote_locations(self, monkeypatch, loc):
        out = _gh(monkeypatch, [{"title": "Working Student - Data & Insights",
                                 "location": {"name": loc}, "absolute_url": "u", "content": "x"}])
        assert len(out) == 1, loc

    @pytest.mark.parametrize("loc", ["New York", "Copenhagen", "Nashville, Tennessee",
                                     "London, England", "Amsterdam"])
    def test_greenhouse_still_drops_foreign_cities(self, monkeypatch, loc):
        assert _gh(monkeypatch, [{"title": "T", "location": {"name": loc},
                                  "absolute_url": "u", "content": "x"}]) == []

    def test_greenhouse_secondary_german_office_rescues_a_foreign_primary(self, monkeypatch):
        out = _gh(monkeypatch, [{"title": "T", "location": {"name": "Amsterdam"},
                                 "offices": [{"name": "Cologne",
                                              "location": "Cologne, North Rhine-Westphalia, Germany"}],
                                 "absolute_url": "u", "content": "x"}])
        assert len(out) == 1
        assert out[0]["location"].startswith("Cologne, North Rhine-Westphalia, Germany")
        assert "Amsterdam" in out[0]["location"]

    def test_one_null_location_does_not_abort_the_board(self, monkeypatch):
        out = _gh(monkeypatch, [
            {"title": "A", "location": {"name": "Cologne, Germany"}, "absolute_url": "a", "content": ""},
            {"title": "B", "location": None, "absolute_url": "b", "content": ""},
            {"title": "C", "location": {"name": None}, "absolute_url": "c", "content": None},
            {"title": "D", "location": {"name": "Düsseldorf, Germany"}, "absolute_url": "d", "content": ""},
        ])
        assert [j["title"] for j in out] == ["A", "B", "C", "D"]

    @pytest.mark.parametrize("loc", ["Düsseldorf", "Munich", "Berlin", "Köln", "Remote", ""])
    def test_lever_keeps_german_and_remote_locations(self, monkeypatch, loc):
        out = _lv(monkeypatch, [{"text": "Working Student", "categories": {"location": loc},
                                 "hostedUrl": "u", "descriptionPlain": "x"}])
        assert len(out) == 1, loc

    def test_lever_country_field_and_secondary_locations(self, monkeypatch):
        out = _lv(monkeypatch, [
            {"text": "A", "categories": {"location": "Oberhaching"}, "country": "DE", "hostedUrl": "a"},
            {"text": "B", "categories": {"location": "Paris", "allLocations": ["Paris", "Cologne, Germany"]},
             "hostedUrl": "b"},
            {"text": "C", "categories": {"location": "Copenhagen Office"}, "country": "DK", "hostedUrl": "c"},
        ])
        assert [j["title"] for j in out] == ["A", "B"]
        assert out[0]["location"] == "Oberhaching, Germany"
        assert out[1]["location"].startswith("Cologne, Germany")

    def test_lever_workplace_type_reaches_the_remote_hybrid_tagging(self, monkeypatch):
        out = _lv(monkeypatch, [{"text": "Working Student", "categories": {"location": "Munich"},
                                 "workplaceType": "hybrid", "hostedUrl": "u",
                                 "descriptionPlain": ENGLISH}])
        j = main._tag_where(dict(out[0], id="x"))
        assert j["_where"] == "HYBRID" and main._is_in_focus_area(j)

    def test_a_dusseldorf_working_student_now_reaches_the_scorer_stage(self, monkeypatch):
        """The exact posting that was invisible on 2026-10-04."""
        out = _gh(monkeypatch, [{"title": "Working Student - Data & Insights",
                                 "location": {"name": "Düsseldorf"},
                                 "absolute_url": "https://x/1", "content": ENGLISH}])
        j = dict(out[0], id="x")
        assert main._is_eligible_form(j) and main._is_tech_relevant(j)
        assert main._is_attendable_from_germany(j)
        assert main._is_in_focus_area(main._tag_where(j))
        assert main._is_english_friendly(j)


class TestDedupKeepsTheReachableCopy:
    def _pair(self, munich_src="Greenhouse", koeln_src="Greenhouse", munich_desc="x", koeln_desc="x"):
        a = _j("Working Student Data (m/f/d)", desc=munich_desc, id="muc", company="X GmbH",
               location="Munich, Bavaria, Germany", source=munich_src)
        b = _j("Working Student Data (m/f/d)", desc=koeln_desc, id="cgn", company="X GmbH",
               location="Köln, Germany", source=koeln_src)
        return a, b

    def test_koeln_beats_munich_whatever_the_order(self):
        a, b = self._pair()
        assert [j["id"] for j in main._dedup_cross_source([a, b])] == ["cgn"]
        assert [j["id"] for j in main._dedup_cross_source([b, a])] == ["cgn"]

    def test_focus_beats_source_priority_and_description_length(self):
        a, b = self._pair(munich_src="Greenhouse", koeln_src="WebSearch", munich_desc="long " * 200)
        assert main._SOURCE_PRIORITY.get("Greenhouse", 0) > main._SOURCE_PRIORITY.get("WebSearch", 0)
        assert [j["id"] for j in main._dedup_cross_source([a, b])] == ["cgn"]

    def test_two_reachable_copies_still_resolve_by_priority(self):
        a = _j("Working Student Data", id="lo", company="X", location="Köln", source="WebSearch")
        b = _j("Working Student Data", id="hi", company="X", location="Bonn", source="Greenhouse")
        assert [j["id"] for j in main._dedup_cross_source([a, b])] == ["hi"]

    def test_the_survivor_then_passes_the_filter_chain_geography(self):
        a, b = self._pair()
        kept = main._dedup_cross_source([a, b])[0]
        assert main._is_in_focus_area(main._tag_where(kept))

    def test_probing_does_not_tag_the_job(self):
        a, b = self._pair()
        main._dedup_cross_source([a, b])
        assert "_where" not in a and "_where" not in b


# ── Step 4: caps and keyword gates that hid student roles ────────────────────

class TestSmartRecruitersAsksForStudentForms:
    def _run(self, monkeypatch, pages):
        """pages: {(slug, q, offset): [postings]}"""
        calls = []

        def fake_get(url, params=None, timeout=None, **kw):
            slug = url.rstrip("/").split("/")[-2]
            calls.append((slug, params["q"], params["offset"], params["country"]))
            return _Resp({"content": pages.get((slug, params["q"], params["offset"]), [])})
        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        monkeypatch.setattr(scrapers, "SMARTRECRUITERS_SLUGS", ["BoschGroup"])
        return scrapers.scrape_smartrecruiters(), calls

    def _p(self, pid, name, **kw):
        return {"id": pid, "name": name, "location": {"fullLocation": "Stuttgart, Germany"},
                "releasedDate": "2026-10-04T07:00:00.000Z", **kw}

    def test_titles_without_an_ai_keyword_are_no_longer_dropped(self, monkeypatch):
        """The exact examples config.py gives for adding these companies."""
        titles = ["Werkstudent IT", "Working Student Cloud Infrastructure",
                  "Werkstudent CRM-Datenanalyse", "Werkstudent (m/w/d) KI-Automatisierung"]
        out, _ = self._run(monkeypatch, {("BoschGroup", "werkstudent", 0):
                                         [self._p(str(i), t) for i, t in enumerate(titles)]})
        assert [j["title"] for j in out] == titles

    def test_every_student_and_part_time_form_is_queried_for_germany(self, monkeypatch):
        _, calls = self._run(monkeypatch, {})
        assert {c[1] for c in calls} == set(scrapers._SR_QUERIES)
        assert {"working student", "werkstudent", "intern", "praktikum", "teilzeit"} <= {c[1] for c in calls}
        assert {c[3] for c in calls} == {"de"}

    def test_a_query_is_paged_to_its_end_not_capped_at_500(self, monkeypatch):
        pages = {("BoschGroup", "praktikum", off): [self._p(f"{off}-{i}", f"Praktikum {off}-{i}")
                                                    for i in range(100)]
                 for off in range(0, 700, 100)}
        pages[("BoschGroup", "praktikum", 700)] = [self._p("last", "Praktikum last")]
        out, _ = self._run(monkeypatch, pages)
        assert len(out) == 701

    def test_the_same_posting_from_two_queries_is_emitted_once(self, monkeypatch):
        p = self._p("42", "Working Student / Werkstudent Data")
        out, _ = self._run(monkeypatch, {("BoschGroup", "working student", 0): [p],
                                         ("BoschGroup", "werkstudent", 0): [p]})
        assert len(out) == 1 and out[0]["url"] == "https://jobs.smartrecruiters.com/BoschGroup/42"

    def test_rows_are_flagged_as_stubs_so_the_real_ad_is_fetched(self, monkeypatch):
        out, _ = self._run(monkeypatch, {("BoschGroup", "intern", 0): [self._p(
            "7", "Intern Data Science", typeOfEmployment={"label": "Part-time"})]})
        assert out[0]["_stub"] is True
        assert "Employment type: Part-time" in out[0]["description"]
        assert not main._is_english_friendly(out[0])

    def test_the_body_fetcher_uses_the_posting_detail_api(self, monkeypatch):
        seen = []

        def fake_get(url, **kw):
            seen.append(url)
            return _Resp({"jobAd": {"sections": {
                "companyDescription": {"text": "<p>About us</p>"},
                "jobDescription": {"text": "<p>You build data pipelines in Python.</p>"},
                "qualifications": {"text": "<ul><li>Enrolled student</li></ul>"}}}})
        monkeypatch.setattr(scrapers.requests, "get", fake_get)
        text = scrapers._fetch_full_description("https://jobs.smartrecruiters.com/BoschGroup/744000152913528")
        assert seen == ["https://api.smartrecruiters.com/v1/companies/BoschGroup/postings/744000152913528"]
        assert text.startswith("You build data pipelines in Python.")
        assert "Enrolled student" in text and text.rstrip().endswith("About us")


class TestWorkdayPutsStudentRolesFirst:
    def _tenant(self, monkeypatch, answer):
        """answer(search, facet, offset) -> (status, [postings])"""
        calls = []

        def fake_post(url, json=None, headers=None, timeout=None):
            facet = bool(json["appliedFacets"])
            calls.append((json["searchText"], facet, json["offset"]))
            status, postings = answer(json["searchText"], facet, json["offset"])
            r = _Resp({"jobPostings": postings}, status)
            return r
        monkeypatch.setattr(scrapers.requests, "post", fake_post)
        monkeypatch.setattr(scrapers, "_wd_fetch_description", lambda *a: ENGLISH)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        return calls

    def _jp(self, title, loc="Germany, Munich", i=0):
        return {"title": title, "locationsText": loc, "externalPath": f"/job/x/{title}-{i}",
                "postedOn": "Posted Today"}

    def test_a_tenant_without_a_search_string_gets_the_student_searches(self, monkeypatch):
        calls = self._tenant(monkeypatch, lambda s, f, o: (200, []))
        scrapers._workday_cxs_tenant(("nvidia", "wd5", "NVIDIAExternalCareerSite"))
        assert {c[0] for c in calls} == {"working student", "intern", "werkstudent", ""}

    def test_student_titles_take_the_detail_slots_before_senior_ones(self, monkeypatch):
        def answer(search, facet, offset):
            if facet:
                return 400, []                       # NVIDIA-style: facet rejected
            if search == "" and offset < 200:
                return 200, [self._jp(f"Senior Software Engineer {offset}-{i}", "US, CA, Santa Clara", i)
                             for i in range(20)]
            if search == "working student" and offset == 0:
                return 200, [self._jp("Working Student AI Research", "Germany, Munich")]
            return 200, []
        self._tenant(monkeypatch, answer)
        out = scrapers._workday_cxs_tenant(("nvidia", "wd5", "NVIDIAExternalCareerSite"))
        assert len(out) == scrapers._WD_KEEP_PER_TENANT
        assert out[0]["title"] == "Working Student AI Research"

    def test_facet_is_tried_everywhere_and_a_400_falls_back(self, monkeypatch):
        calls = self._tenant(monkeypatch, lambda s, f, o: (400, []) if f else (200, []))
        scrapers._workday_cxs_tenant(("intel", "wd1", "External"))
        assert ("intern", True, 0) in calls and ("intern", False, 0) in calls

    def test_german_postings_rank_above_foreign_ones_within_student_titles(self, monkeypatch):
        def answer(search, facet, offset):
            if facet:
                return 400, []
            if search == "intern" and offset == 0:
                return 200, ([self._jp(f"Intern Hardware {i}", "US, CA, Santa Clara", i) for i in range(19)]
                             + [self._jp("Intern Deep Learning", "Germany, Munich", 99)])
            return 200, []
        self._tenant(monkeypatch, answer)
        monkeypatch.setattr(scrapers, "_WD_KEEP_PER_TENANT", 5)
        out = scrapers._workday_cxs_tenant(("nvidia", "wd5", "NVIDIAExternalCareerSite"))
        assert out[0]["title"] == "Intern Deep Learning"

    def test_a_facet_hit_is_labelled_germany_for_the_geography_filters(self, monkeypatch):
        self._tenant(monkeypatch, lambda s, f, o: (200, [self._jp("Working Student Data", "2 Locations")])
                     if (f and s == "working student" and o == 0) else (200, []))
        out = scrapers._workday_cxs_tenant(("sanofi", "wd3", "SanofiCareers"))
        assert out and out[0]["location"] == "2 Locations, Germany"

    def test_an_explicit_search_string_is_still_the_only_pass(self, monkeypatch):
        calls = self._tenant(monkeypatch, lambda s, f, o: (200, []))
        scrapers._workday_cxs_tenant(("stryker", "wd1", "StrykerCareers", "working student"))
        assert {c[0] for c in calls} == {"working student"}


class TestSlicesKeepTheHomeRegion:
    def test_belt_and_nrw_institutes_are_never_cut_by_the_cap(self, monkeypatch):
        far = [f"https://jobs.fraunhofer.de/job/Dresden-Studentische-Hilfskraft-{i}/" for i in range(300)]
        belt = ["https://jobs.fraunhofer.de/job/Sankt-Augustin-Studentische-Hilfskraft-KI/1/",
                "https://jobs.fraunhofer.de/job/Wachtberg-Student-Assistant-Radar/2/"]
        nrw = ["https://jobs.fraunhofer.de/job/Aachen-Werkstudent-Lasertechnik/3/"]
        sitemap = "".join(f"<loc>{u}</loc>" for u in far + belt + nrw)

        class _R:
            status_code = 200
            text = sitemap
        picked = []
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _R())
        monkeypatch.setattr(scrapers, "_RMK_SITES", (("https://jobs.fraunhofer.de", "Fraunhofer"),))
        monkeypatch.setattr(scrapers, "_parallel_collect",
                            lambda items, fn, label: picked.extend(items) or [])
        scrapers.scrape_research_institutes()
        assert picked[:2] == belt and picked[2] == nrw[0]
        assert len(picked) == scrapers._RMK_CAP

    def test_english_student_form_is_queried_first_on_csb_sites(self):
        assert scrapers._CSB_QUERIES[0] == "Student"
        assert scrapers._CSB_CAP_PER_SITE >= 60 and scrapers._BEESITE_CAP >= 60

    def test_adzuna_asks_for_the_newest_ads(self):
        src = inspect.getsource(scrapers.scrape_adzuna)
        assert '"sort_by":' in src and '"date"' in src
        assert '"max_days_old":     14' not in src
