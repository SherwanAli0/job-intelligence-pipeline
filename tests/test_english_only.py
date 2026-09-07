"""Owner decisions of 2026-09-07, approved as a six-step plan ("go"):
English-only ads for every role type, full-ad fetch for stub bodies before
judging, Germany-wide search with the Bonn belt ordering the digest instead
of dropping, a scorer prompt without the belt cap, and REMOTE / HYBRID /
ON-SITE labels in the digest."""

import inspect

import main
import notifier
import scorer
from config import _CV_SHARED


def _j(title, desc="", location="Köln, Germany", **kw):
    d = {"id": kw.pop("id", "t"), "title": title, "company": "Acme",
         "location": location, "url": "https://example.com/j", "source": "s",
         "description": desc, "posted_at": ""}
    d.update(kw)
    return d


ENGLISH = ("Join our data platform team for twenty hours a week alongside your "
           "studies. You will build evaluation pipelines in Python, query the "
           "warehouse with SQL and present findings to the product team. We are "
           "an international team and our working language is English.")
GERMAN = ("Als Werkstudent unterstützt du unser Data-Team bei der Entwicklung "
          "von Auswertungen und Dashboards. Du arbeitest mit Python und SQL und "
          "wertest Produktdaten aus. Du bist an einer Hochschule immatrikuliert "
          "und hast bis zu 20 Stunden pro Woche Zeit für uns im Team.")


class TestEnglishOnly:
    def test_english_body_survives(self):
        assert main._is_english_friendly(_j("Werkstudent Data (m/w/d)", ENGLISH))

    def test_german_body_drops_for_student_part_time_and_full_time(self):
        for title in ("Werkstudent Data (m/w/d)", "Data Engineer (m/w/d) Teilzeit",
                      "Data Engineer (m/w/d)"):
            assert not main._is_english_friendly(_j(title, GERMAN)), title

    def test_bilingual_ad_with_a_full_english_version_survives(self):
        """German first, English below — the common bilingual layout. The
        whole text is half German, but the English half is complete."""
        english_version = ENGLISH + (
            " You are enrolled at a university in Germany and can work with us "
            "for up to twenty hours a week during the semester and more during "
            "the breaks. Experience with pandas or a cloud platform is a plus but "
            "not required; curiosity and clean code matter more to us.")
        assert main._is_english_friendly(_j("Werkstudent Data (m/w/d)",
                                            GERMAN + " " + english_version))

    def test_mostly_german_ad_with_one_english_sentence_drops(self):
        assert not main._is_english_friendly(_j(
            "Werkstudent Data (m/w/d)", GERMAN + " Good English skills required."))

    def test_stub_body_drops(self):
        assert not main._is_english_friendly(_j("Working Student Data", "Apply now."))

    def test_no_student_exemption_left_in_source(self):
        src = inspect.getsource(main._is_english_friendly)
        assert "_is_eligible_form" not in src
        assert "_requires_fluent_german" not in src


class TestStubBodiesAreFetchedBeforeJudging:
    def test_fetch_fills_stubs_and_leaves_full_bodies_alone(self, monkeypatch):
        import scrapers
        seen = []

        def fake_enrich(jobs, quiet=False):
            for j in jobs:
                seen.append(j["id"])
                j["description"] = ENGLISH
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions", fake_enrich)
        jobs = [_j("Working Student Data", "Apply now.", id="stub"),
                _j("Working Student Data", ENGLISH, id="full")]
        main._fill_missing_bodies(jobs)
        assert seen == ["stub"]
        assert all(main._is_english_friendly(j) for j in jobs)

    def test_cap_is_respected(self, monkeypatch):
        import scrapers
        counted = []
        monkeypatch.setattr(scrapers, "_enrich_jobspy_descriptions",
                            lambda jobs, quiet=False: counted.extend(jobs))
        monkeypatch.setattr(main, "_BODY_FETCH_CAP", 3)
        main._fill_missing_bodies([_j("W", "x", id=str(i)) for i in range(10)])
        assert len(counted) == 3

    def test_chain_fetches_before_the_tech_gate_and_language_test(self):
        src = inspect.getsource(main.node_filter)
        assert src.index("_fill_missing_bodies") < src.index("_is_tech_relevant")
        assert src.index("_fill_missing_bodies") < src.index("_is_english_friendly")


class TestGermanyWideIsRankingNotFiltering:
    def test_commute_no_longer_drops(self):
        src = inspect.getsource(main.node_filter)
        assert "Commute filter" not in src
        assert "_tag_where" in src

    def test_berlin_hybrid_english_working_student_reaches_the_scorer(self):
        j = _j("Working Student Data Engineering", ENGLISH + " Hybrid, two office days.",
               location="Berlin, Germany")
        assert main._is_attendable_from_germany(j)
        assert main._is_english_friendly(j)
        assert main._is_in_focus_area(main._tag_where(j))
        disq, _r, _c = scorer._hard_disqualify(j)
        assert not disq

    def test_labels_and_ranks(self):
        assert main._tag_where(_j("W", ENGLISH, location="Remote, Germany"))["_where"] == "REMOTE"
        belt = main._tag_where(_j("W", ENGLISH, location="Köln, Germany"))
        assert belt["_where"] == "ON-SITE" and belt["_belt"] and belt["_where_rank"] == 1
        hyb = main._tag_where(_j("W", ENGLISH + " Hybrid: two days in the office.",
                                 location="Berlin, Germany"))
        assert hyb["_where"] == "HYBRID" and not hyb["_belt"] and hyb["_where_rank"] == 3
        nrw = main._tag_where(_j("W", ENGLISH, location="Dortmund, North Rhine-Westphalia, Germany"))
        assert nrw["_where"] == "ON-SITE" and nrw["_nrw"] and not nrw["_belt"] and nrw["_where_rank"] == 2
        # Ashby-style "Berlin (Remote)": an office exists, so it is hybrid,
        # not remote — the city still matters.
        assert main._tag_where(_j("W", ENGLISH, location="Berlin (Remote)"))["_where"] == "HYBRID"
        full = main._tag_where(_j("W", ENGLISH + " This role is 100% remote within Germany.",
                                  location="München, Germany"))
        assert full["_where"] == "REMOTE" and full["_where_rank"] == 0

    def test_digest_order_is_remote_then_belt_then_rest(self, monkeypatch):
        monkeypatch.setattr(main, "enrich_with_kits", lambda top: None, raising=False)
        scored = [
            dict(_j("Berlin", ENGLISH, location="Berlin", id="b"), score=95, _track="AI",
                 _where="ON-SITE", _belt=False, _where_rank=2),
            dict(_j("Köln", ENGLISH, location="Köln", id="k"), score=60, _track="AI",
                 _where="ON-SITE", _belt=True, _where_rank=1),
            dict(_j("Remote", ENGLISH, location="Remote, Germany", id="r"), score=50,
                 _track="AI", _where="REMOTE", _belt=False, _where_rank=0),
        ]
        out = main.node_rank({"scored": scored})
        assert [j["id"] for j in out["top"]] == ["r", "k", "b"]


class TestFocusArea:
    """Second decision of 2026-09-07, after the first Germany-wide digest:
    "from now on we focus on hybrid, remote and NRW and Bonn only"."""

    def _f(self, loc, extra=""):
        return main._is_in_focus_area(main._tag_where(_j("W", ENGLISH + extra, location=loc)))

    def test_remote_anywhere_is_kept(self):
        assert self._f("Munich, Bavaria, Germany", " This role is 100% remote within Germany.")
        assert self._f("Remote, Germany")

    def test_hybrid_anywhere_is_kept(self):
        assert self._f("Berlin, Germany", " Hybrid: two days in the office.")
        assert self._f("Hamburg, Germany", " Homeoffice möglich.")
        assert self._f("Berlin (Remote)")

    def test_onsite_outside_nrw_is_dropped(self):
        for loc in ("Munich, Bavaria, Germany", "Berlin, BE, DE", "Frankfurt am Main",
                    "Stuttgart, Baden-Württemberg, Germany", "Parsdorf, Bavaria, Germany",
                    "Garching - Bavaria, Germany", "Karlsruhe, Baden-Württemberg, Germany"):
            assert not self._f(loc), loc

    def test_onsite_in_nrw_and_the_belt_is_kept(self):
        for loc in ("Bonn, NW, DE", "Cologne, North Rhine-Westphalia, Germany",
                    "Dortmund, Germany", "Aachen, Germany", "Essen, NW, DE",
                    "Nordrhein-Westfalen, Düsseldorf", "Koblenz, Germany",
                    "Remagen", "Sankt Augustin, Germany", "Bielefeld"):
            assert self._f(loc), loc

    def test_unknown_location_is_kept(self):
        assert self._f("")
        assert self._f("Deutschland")
        assert self._f("Germany")

    def test_filter_is_wired_after_tagging_and_before_the_language_test(self):
        src = inspect.getsource(main.node_filter)
        assert src.index("_tag_where") < src.index("_is_in_focus_area") < src.index("_is_english_friendly")

    def test_digest_order_is_remote_belt_nrw_then_hybrid_elsewhere(self, monkeypatch):
        monkeypatch.setattr(main, "enrich_with_kits", lambda top: None, raising=False)
        mk = lambda i, loc, extra="", score=50: dict(main._tag_where(
            _j(i, ENGLISH + extra, location=loc, id=i)), score=score, _track="AI")
        scored = [mk("hyb-berlin", "Berlin", " Hybrid role.", 99),
                  mk("nrw", "Dortmund, Germany", "", 70),
                  mk("belt", "Köln, Germany", "", 60),
                  mk("remote", "Remote, Germany", "", 45)]
        out = main.node_rank({"scored": scored})
        assert [j["id"] for j in out["top"]] == ["remote", "belt", "nrw", "hyb-berlin"]

    def test_prompt_and_profile_say_nrw(self):
        prompt = scorer._system_prompt("profile")
        assert "on-site roles only in North Rhine-" in prompt
        assert "ON-SITE only in" in _CV_SHARED


class TestDigestShowsWhere:
    def test_badge_and_order_in_html(self):
        jobs = [
            dict(_j("Berlin role", ENGLISH, location="Berlin", id="b"), score=95,
                 reason="r", _where="ON-SITE", _belt=False, _where_rank=2),
            dict(_j("Remote role", ENGLISH, location="Remote, Germany", id="r"),
                 score=50, reason="r", _where="REMOTE", _belt=False, _where_rank=0),
            dict(_j("Köln role", ENGLISH, location="Köln", id="k"), score=70,
                 reason="r", _where="HYBRID", _belt=True, _where_rank=1),
        ]
        html = notifier._build_html(jobs)
        assert "REMOTE" in html and "HYBRID · near Bonn" in html and "ON-SITE" in html
        assert html.index("Remote role") < html.index("Köln role") < html.index("Berlin role")
        assert "remote first" in html


class TestScorerPromptIsGermanyWide:
    def test_no_belt_cap_left(self):
        prompt = scorer._system_prompt("profile")
        assert "0 to 15 for on-site/hybrid roles anywhere else" not in prompt
        assert "ANYWHERE in Germany" in prompt
        assert "already passed an English-language" in prompt

    def test_profile_no_longer_scores_far_cities_down(self):
        assert "score them 0-15" not in _CV_SHARED
        assert "must NOT be scored down for its location" in _CV_SHARED


class TestGermanyWideSources:
    """Step 5 of the plan: boards verified live on 2026-09-07 for English
    student roles outside the belt, plus the belt gates removed from the
    sources that had them."""

    def test_new_boards_configured(self):
        from config import (ASHBY_SLUGS, PERSONIO_SLUGS, SMARTRECRUITERS_SLUGS,
                            WORKDAY_CXS_TENANTS)
        import scrapers
        assert "bettermile" in ASHBY_SLUGS
        assert "knime" in PERSONIO_SLUGS
        assert "Vattenfall" in SMARTRECRUITERS_SLUGS
        tenants = {t[:3] for t in WORKDAY_CXS_TENANTS}
        assert ("stryker", "wd1", "StrykerCareers") in tenants
        assert ("harman", "wd3", "HARMAN") in tenants
        assert ("ag", "wd3", "Airbus") in tenants
        assert {"RWE", "E.ON"} <= {s for _, s in scrapers._CSB_SITES}
        for src in ("RWE", "E.ON"):
            assert src in main._SOURCE_PRIORITY and src in main._LONG_LIVED_SOURCES

    def test_workday_search_string_is_optional_and_used(self, monkeypatch):
        import scrapers
        payloads = []

        class R:
            status_code = 200

            def json(self):
                return {"jobPostings": []}

        def fake_post(url, json=None, headers=None, timeout=0):
            payloads.append(json)
            return R()
        monkeypatch.setattr(scrapers.requests, "post", fake_post)
        scrapers._workday_cxs_tenant(("ag", "wd3", "Airbus", "working student"))
        scrapers._workday_cxs_tenant(("nvidia", "wd5", "NVIDIAExternalCareerSite"))
        assert payloads[0]["searchText"] == "working student"
        assert payloads[1]["searchText"] == ""
        assert payloads[0]["appliedFacets"] == {} == payloads[1]["appliedFacets"]

    def test_germany_facet_is_opt_in_and_falls_back_on_400(self, monkeypatch):
        import scrapers
        calls = []

        class R:
            def __init__(self, code):
                self.status_code = code

            def json(self):
                return {"jobPostings": []}

        def fake_post(url, json=None, headers=None, timeout=0):
            calls.append(dict(json["appliedFacets"]))
            return R(400 if json["appliedFacets"] else 200)
        monkeypatch.setattr(scrapers.requests, "post", fake_post)
        scrapers._workday_cxs_tenant(("stryker", "wd1", "StrykerCareers", "working student", True))
        assert calls[0] == {"locationCountry": [scrapers._WD_COUNTRY_DE]}
        assert calls[1] == {}, "a 400 on the facet must retry without it"

    def test_belt_gates_are_gone_from_csb_and_institutes(self):
        import scrapers
        assert "_RMK_REGION" not in inspect.getsource(scrapers.scrape_csb)
        assert "_RMK_REGION" not in inspect.getsource(scrapers.scrape_research_institutes)
        assert scrapers._CSB_CAP_PER_SITE >= 40

    def test_arbeitsagentur_runs_a_nationwide_english_pass_first(self):
        import scrapers
        src = inspect.getsource(scrapers.scrape_arbeitsagentur)
        assert '"nationwide"' in src
        assert src.index("ARBEITSAGENTUR_NATIONWIDE_QUERIES") < src.index("ARBEITSAGENTUR_QUERIES:")
        assert "Working Student" in scrapers.ARBEITSAGENTUR_NATIONWIDE_QUERIES
