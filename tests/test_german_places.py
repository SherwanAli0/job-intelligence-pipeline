"""German towns were treated as 'outside Germany' by the scorer's pre-screen.

Measured 2026-10-14: 80 of 175 student-role rows from XING were dropped for
location, among them Paderborn, Aachen, Hilden and Schwelm (all NRW) and
Iserlohn, Nürnberg, Karlsruhe. LinkedIn writes "City, Germany" and was immune.

First fix (recognise more towns) was reviewed adversarially (4 reviewers, 20
skeptic votes) and was wrong in ways these tests now pin: a German-named town
next to a foreign country counted as German ("Maastricht, Limburg,
Netherlands", "Hanover, NH"), a finite list cannot hold ~11,000 municipalities,
", DE" matched Delaware, NRW town names beat another state, and a SUBSTRING
test on the ad text ("essential" contains "essen") switched the catch-all off.
The design is now: drop only on POSITIVE evidence that a place is abroad;
require a clean German signal only where one is needed. All offline."""

import re

import pytest

import filters
import main
import scorer
import scrapers


def _j(title="Werkstudent Data Analytics", loc="Paderborn", desc=None, **kw):
    base = {"id": kw.pop("id", "t"), "title": title, "company": "Acme", "location": loc,
            "url": "https://example.org/j/1", "source": "XING",
            "description": desc if desc is not None else
            "Working student in our data team. You build Python and SQL pipelines and "
            "dashboards for the business. Hybrid work, English-speaking team.",
            "posted_at": ""}
    base.update(kw)
    return base


class TestIsGermanLocation:
    @pytest.mark.parametrize("loc", [
        "Paderborn", "Aachen", "Iserlohn", "Nürnberg", "Sindelfingen", "Karlsruhe", "Hannover",
        "Darmstadt", "Bremen", "Würzburg", "Ulm", "Weiden", "Schwelm", "Sassenberg", "Halver", "Kamen",
        "Hilden, Nordrhein-Westfalen", "Schwelm, Nordrhein Westfalen", "Berlin, BE, DE",
        "Köln, NW, DE, 50933", "Sassenberg, Germany", "Bayern, Erlangen, Bruckberg",
        "Garching - Bavaria, Germany", "Oberpfaffenhofen", "Frankfurt am Main", "Halle (Saale)",
        "Attendorn", "Gescher", "Ehingen an der Donau", "Coburg", "Oberkochen",
        # ASCII spellings of the biggest NRW cities, and districts main already knew
        "Koeln", "Duesseldorf", "Dusseldorf", "Muenster", "Bad Godesberg", "Beuel", "Porz", "Deutz",
        "Grevenbroich", "Mülheim", "Linz am Rhein", "Unkel", "bundesweit", "Sächsische Schweiz",
        "Fränkische Schweiz",
    ])
    def test_german_places_are_recognised(self, loc):
        assert filters.is_german_location(loc), loc

    @pytest.mark.parametrize("loc", [
        "Hamminkeln, NW", "Paderborn, NW, DE", "Gaimersheim, BY, DE, 85080",
        "Musterstadt, HE", "Unbekanntdorf, SH, DE", "Zzyzx, TH, 07743", "Musterstadt, RP", "Beispielhausen, HH",
    ])
    def test_german_state_codes_are_evidence(self, loc):
        """Indeed and some boards write 'Town, NW' or 'Town, BY, DE'. The code
        alone is enough, for towns no list can know."""
        assert filters.is_german_location(loc), loc

    @pytest.mark.parametrize("loc", ["Bayern", "Niedersachsen", "Thüringen", "Schleswig-Holstein",
                                     "Saarland", "Mecklenburg-Vorpommern", "Sachsen-Anhalt", "Hessen"])
    def test_state_names_alone_are_evidence(self, loc):
        assert filters.is_german_location(loc), loc

    @pytest.mark.parametrize("loc", [
        "Warsaw, Poland", "Wien", "Zürich", "Basel", "Salzburg", "Graz", "Amsterdam", "Copenhagen",
        "Madrid", "Lisbon", "Luxembourg", "Venlo", "Maastricht", "Strasbourg", "Prague", "Budapest",
        "London", "Paris", "Barcelona", "Dublin", "Lima, Peru", "Remote", "",
    ])
    def test_foreign_and_empty_places_are_not(self, loc):
        assert not filters.is_german_location(loc), loc

    @pytest.mark.parametrize("loc", [
        # German-named towns next to an explicit foreign country or region
        "Maastricht, Limburg, Netherlands", "Hasselt, Limburg, Belgium", "Konstanz, Switzerland",
        "Büren, Switzerland", "Feldkirchen in Kärnten, Carinthia, Austria", "Taufkirchen an der Pram, Austria",
        "Soest, Utrecht, Netherlands", "Gronau, Overijssel, Netherlands", "Bocholt, Flemish Region, Belgium",
        "Rees, Netherlands", "Enger, Norway", "Weiden am See, Austria", "Hamm, Luxembourg",
        # US / Australian homonyms
        "Hanover, NH", "Hanover, MD", "Dresden, OH", "Kiel, WI", "Bremen, GA", "Potsdam, NY",
        "Weimar, TX", "Jena, LA", "Fulda, MN", "Worms, Nebraska", "Heidelberg, Victoria",
        "Coburg, Victoria", "Hamburg, NY", "Frankfurt, KY", "Berlin, NH, United States",
    ])
    def test_a_german_named_town_next_to_a_foreign_country_is_not_german(self, loc):
        assert not filters.is_german_location(loc), loc
        assert filters.is_foreign_location(loc), loc

    def test_explicit_germany_beats_a_second_foreign_office(self):
        assert filters.is_german_location("Munich, Germany; London, UK")
        assert not filters.is_foreign_location("Munich, Germany; London, UK")

    @pytest.mark.parametrize("loc", ["Wilmington, DE", "Newark, DE", "Dover (DE)", "Paris/DE",
                                     "Rua de Santa Catarina, de", "Santiago de Compostela, Spain",
                                     "Madrid - de", "Plaza de España, de"])
    def test_a_bare_de_is_not_germany(self, loc):
        """', DE' is Delaware as often as it is Germany; only a German state
        code in front of it (', NW, DE') counts."""
        assert not filters.is_german_location(loc), loc

    def test_whole_words_only(self):
        """Letters are the boundary: Copenhagen is not Hagen, Kielce is not Kiel."""
        assert not filters.is_german_location("Copenhagen")
        assert not filters.is_german_location("Kielce")
        assert not filters.is_german_location("Hagenberg")
        assert not filters.is_german_location("Ulmen, Australia")
        assert filters.is_german_location("Hagen")
        assert filters.is_german_location("Kiel, Schleswig-Holstein")

    def test_austria_and_switzerland_are_deliberately_absent(self):
        for name in ("wien", "zürich", "salzburg", "innsbruck", "bern", "genf", "graz", "linz"):
            assert name not in filters.GERMAN_PLACES and name not in filters.NRW_PLACES

    def test_no_foreign_term_is_also_a_german_place(self):
        """A word in both lists would be decided by order, not by evidence."""
        german = set(filters.GERMAN_PLACES) | set(filters.GERMAN_STATES) | set(filters.GERMANY_TERMS)
        foreign = set(filters.FOREIGN_COUNTRIES) | set(filters.FOREIGN_REGIONS) | set(filters.FOREIGN_CITIES)
        assert sorted(german & foreign) == []


class TestMentionsGermanyIsWholeWord:
    @pytest.mark.parametrize("text", ["An essential role in our team.", "Our dachshund policy: dogs welcome.",
                                      "Responsible for assessing the essence of the brief.",
                                      "Bonnie works with the Bonnet team."])
    def test_substrings_inside_other_words_do_not_count(self, text):
        assert not filters.mentions_germany(text)

    @pytest.mark.parametrize("text", ["Our office in Essen is hybrid.", "Based in Germany.",
                                      "Teams in Bonn, Köln and London.", "DACH region sales."])
    def test_real_mentions_do_count(self, text):
        assert filters.mentions_germany(text)

    def test_the_word_essential_no_longer_switches_the_catch_all_off(self):
        """Pre-existing bug found by the review: 'essential' contains 'essen',
        so most English ads counted as mentioning Germany and a Warsaw
        on-site role was never dropped."""
        d = ("An essential role in our Warsaw team. You build Python and SQL pipelines. "
             "Hybrid work in the Warsaw office, English-speaking team, working student contract.")
        disq, _w, cat = scorer._hard_disqualify(_j(loc="Warsaw", desc=d))
        assert disq and cat == "location"


class TestPreScreenDropsOnlyOnEvidenceOfAbroad:
    @pytest.mark.parametrize("loc", ["Paderborn", "Aachen", "Iserlohn", "Nürnberg", "Sindelfingen",
                                     "Hilden, Nordrhein Westfalen", "Paderborn, NW, DE", "Hamminkeln, NW"])
    def test_german_town_is_not_outside_germany(self, loc):
        disq, why, cat = scorer._hard_disqualify(_j(loc=loc))
        assert not disq, (loc, why)

    @pytest.mark.parametrize("loc", ["Gescher", "Halver", "Gaimersheim", "Unbekanntdorf"])
    def test_an_unknown_bare_town_goes_to_the_model(self, loc):
        """No list holds every German municipality. A place with no evidence
        either way is kept (recall over precision); the model prompt scores a
        role outside Germany 0-15."""
        disq, why, _c = scorer._hard_disqualify(_j(loc=loc))
        assert not disq, (loc, why)

    @pytest.mark.parametrize("loc", [
        "Warsaw, Poland", "Madrid", "Amsterdam", "Wien", "Lisbon", "Prague", "Zürich",
        "Maastricht, Limburg, Netherlands", "Konstanz, Switzerland", "Hanover, NH",
        "Feldkirchen in Kärnten, Austria", "Heidelberg, Victoria", "Kiel, WI",
        "Paris, FR", "Dublin, IE", "Seattle, WA",
    ])
    def test_foreign_onsite_place_is_dropped(self, loc):
        disq, _why, cat = scorer._hard_disqualify(_j(loc=loc))
        assert disq and cat == "location", loc

    def test_a_foreign_place_named_only_in_the_location_is_dropped_even_with_german_ad_text(self):
        """Mutation 'place list applied to ad text' must not survive: the town
        lists are for LOCATION fields only."""
        d = ("Our partner office is in Aachen. You build Python and SQL pipelines, working student, "
             "hybrid, English-speaking team.")
        disq, _w, cat = scorer._hard_disqualify(_j(loc="Warsaw, Poland", desc=d))
        assert disq and cat == "location"

    def test_remote_abroad_with_a_us_state_is_still_dropped(self):
        d = ("Fully remote. Working student role, Python and SQL pipelines for our team, "
             "English-speaking. Hanover office.")
        disq, _w, cat = scorer._hard_disqualify(_j(loc="Hanover, NH, United States", desc=d))
        assert disq, "the non-EU blocklist hit must not be undone by the town name"


class TestOneListForTheWholePipeline:
    """The review found Bonn-belt and NRW terms that main already treated as
    NRW, but the scorer's list did not know. They must never drift again."""

    @staticmethod
    def _plain(terms):
        return sorted({t.strip().lower() for t in terms
                       if re.fullmatch(r"[a-zäöüß][a-zäöüß .()\-/]*[a-zäöüß.)]", t.strip().lower())
                       and len(t.strip()) >= 3})

    @pytest.mark.parametrize("name", ["_COMMUTABLE_FROM_BONN", "_NRW_SIGNALS", "_NON_COMMUTABLE_DE_CITIES",
                                      "_OUTSIDE_NRW_SIGNALS"])
    def test_every_place_main_knows_is_german_for_the_scorer(self, name):
        missing = [t for t in self._plain(getattr(main, name)) if not filters.is_german_location(t)]
        assert missing == [], f"{name}: {missing}"

    def test_every_place_the_ats_recall_gate_knows_is_german_for_the_scorer(self):
        missing = [t for t in self._plain(scrapers._DE_EXTRA_PLACES) if not filters.is_german_location(t)]
        assert missing == []

    def test_the_ats_recall_gate_uses_the_same_function(self):
        for loc in ("Hanover, NH", "Maastricht, Limburg, Netherlands", "Paderborn", "Aachen"):
            assert scrapers._de_place(loc) == filters.is_german_location(loc), loc


class TestSourcesCarryTheCountry:
    def test_xing_location_has_the_country(self):
        assert scrapers._xing_location({"city": "Paderborn", "country": {"countryCode": "DE"}}) == "Paderborn, Germany"
        assert scrapers._xing_location({"city": "Wien", "country": {"countryCode": "AT"}}) == "Wien, Austria"
        assert scrapers._xing_location({"city": "Zürich", "country": {"countryCode": "CH"}}) == "Zürich, Switzerland"
        assert scrapers._xing_location({"city": "Köln"}) == "Köln"
        assert scrapers._xing_location(None) == "Germany"

    def test_a_xing_row_without_a_city_keeps_its_foreign_country(self):
        """Mutation 'cityless foreign row becomes Germany' must not survive."""
        assert scrapers._xing_location({"country": {"countryCode": "AT"}}) == "Austria"
        assert scrapers._xing_location({"city": "", "country": {"countryCode": "CH"}}) == "Switzerland"

    def test_a_xing_page_row_gets_the_country(self, monkeypatch):
        """Since 2026-11-13 XING rows come from the ad page's JSON-LD (its
        GraphQL search is disallowed by robots.txt)."""
        page = ('<script data-ch type="application/ld+json">{"@type": "JobPosting", '
                '"title": "Werkstudent Data", "hiringOrganization": {"name": "KIRCHHOFF"}, '
                '"datePosted": "2026-11-13T04:00:00Z", "description": "<p>Ad</p>", '
                '"jobLocation": [{"address": {"addressLocality": "Iserlohn", "addressCountry": "DE"}}]}'
                '</script>')

        class _R:
            status_code = 200
            headers = {"content-type": "text/html"}
            content = page.encode("utf-8")
            text = page
        monkeypatch.setattr(scrapers.requests, "get", lambda *a, **kw: _R())
        rows = scrapers._xing_page("https://www.xing.com/jobs/iserlohn-werkstudent-data-1")
        assert rows and rows[0]["location"] == "Iserlohn, Germany"
        assert rows[0]["company"] == "KIRCHHOFF" and rows[0]["source"] == "XING"

    def test_the_xing_slug_filter_wants_student_or_junior_tech_roles(self):
        want = scrapers._xing_slug_wanted
        assert want("https://www.xing.com/jobs/koeln-werkstudent-data-analytics-146268547")
        assert want("https://www.xing.com/jobs/berlin-junior-data-scientist-1")
        assert not want("https://www.xing.com/jobs/koeln-projektleiter-tga-koeln-146268547")
        assert not want("https://www.xing.com/jobs/leipheim-praktikant-handwerk-146437662")

    @pytest.mark.parametrize("addr,expected", [
        ({"ort": "Hilden", "region": "NORDRHEIN_WESTFALEN", "land": "DEUTSCHLAND"},
         "Hilden, Nordrhein-Westfalen, Germany"),
        ({"ort": "Stuttgart", "region": "BADEN_WUERTTEMBERG", "land": "DEUTSCHLAND"},
         "Stuttgart, Baden-Württemberg, Germany"),
        ({"ort": "Langenlonsheim", "region": "RHEINLAND_PFALZ", "land": "DEUTSCHLAND"},
         "Langenlonsheim, Rheinland-Pfalz, Germany"),
        ({"ort": "Wien", "region": "WIEN", "land": "OESTERREICH"}, "Wien, Wien, Oesterreich"),
    ])
    def test_arbeitsagentur_location_has_state_and_country(self, addr, expected):
        assert scrapers._ba_location({"stellenlokationen": [{"adresse": addr}]}) == expected

    def test_arbeitsagentur_without_a_location_is_deutschland(self):
        assert scrapers._ba_location({"stellenlokationen": []}) == "Deutschland"

    def test_a_ba_hilden_job_is_nrw_on_site_end_to_end(self):
        loc = scrapers._ba_location({"stellenlokationen": [{"adresse": {
            "ort": "Hilden", "region": "NORDRHEIN_WESTFALEN", "land": "DEUTSCHLAND"}}]})
        j = main._tag_where(_j(loc=loc, desc="Vor Ort in Hilden."))
        assert j["_where"] == "ON-SITE" and j["_nrw"] and main._is_in_focus_area(j)
        disq, why, _c = scorer._hard_disqualify(_j(loc=loc))
        assert not disq, why


class TestNrwTownsAreNrwOnlyWhenNothingContradicts:
    @pytest.mark.parametrize("loc", ["Sassenberg", "Schwelm", "Warendorf", "Lippstadt", "Rheda-Wiedenbrück",
                                     "Wermelskirchen", "Bad Salzuflen", "Erftstadt", "Ibbenbüren",
                                     "Halver", "Kamen", "Werne", "Hamminkeln", "Warburg", "Sundern",
                                     "Hamminkeln, NW", "Paderborn, NW, DE", "Koeln", "Duesseldorf"])
    def test_on_site_in_an_nrw_town_is_in_focus(self, loc):
        j = main._tag_where(_j(loc=loc, desc="Vor Ort im Büro. Werkstudent Datenanalyse mit Python."))
        assert j["_where"] == "ON-SITE" and j["_nrw"] and main._is_in_focus_area(j), loc

    @pytest.mark.parametrize("loc", [
        "Borken, Hessen", "Gronau (Leine), Niedersachsen", "Schwalmtal, Hessen", "Brüggen (Leine), Niedersachsen",
        "Herten, Baden-Württemberg", "Greven, Mecklenburg-Vorpommern", "Haan, Hessen",
        "Soest, Utrecht, Netherlands", "Gronau, Overijssel, Netherlands", "Büren, Bern, Switzerland",
        "Rees, Netherlands", "Enger, Norway", "Hamm, Luxembourg", "Hamm, RP",
    ])
    def test_an_nrw_name_in_another_state_or_country_is_not_nrw(self, loc):
        j = main._tag_where(_j(loc=loc, desc="On-site in our office."))
        assert not j["_nrw"] and not main._is_in_focus_area(j), loc

    def test_an_explicit_nrw_mention_wins_over_a_second_office(self):
        j = main._tag_where(_j(loc="Borken, Nordrhein-Westfalen; Frankfurt, Hessen", desc="On-site."))
        assert j["_nrw"]

    @pytest.mark.parametrize("loc", ["Karlsruhe", "Nürnberg", "Stuttgart", "Sindelfingen", "Hannover"])
    def test_on_site_outside_nrw_is_still_dropped(self, loc):
        j = main._tag_where(_j(loc=loc, desc="Vor Ort im Büro."))
        assert not j["_nrw"] and not main._is_in_focus_area(j), loc

    def test_hybrid_in_any_german_town_stays_in_focus(self):
        j = main._tag_where(_j(loc="Sindelfingen", desc="Hybrid: zwei Tage im Büro."))
        assert j["_where"] == "HYBRID" and main._is_in_focus_area(j)

    def test_the_long_nrw_list_is_not_matched_against_ad_text(self):
        """Soest, Olpe, Verl ... would match German prose. Location field only."""
        j = main._tag_where(_j(loc="Hamburg", desc="Wir sitzen nah bei Soest und Olpe, vor Ort im Büro."))
        assert not j["_nrw"] and not main._is_in_focus_area(j)

    def test_names_that_only_the_older_nrw_list_knows_still_work(self):
        """Mutation 'drop _NRW_SIGNALS from _tag_where' must not survive."""
        for loc in ("Dortmund", "Nordrhein-Westfalen, Germany", "Unbekannt, Nordrhein Westfalen", "Essen"):
            assert main._tag_where(_j(loc=loc, desc="On-site."))["_nrw"], loc

    def test_main_uses_the_shared_nrw_list(self):
        assert main._NRW_PLACES is filters.NRW_PLACES

    def test_no_nrw_place_is_a_town_in_another_state_by_name_alone(self):
        """The ambiguous words were left out of the list on purpose."""
        for name in ("lage", "much", "alpen", "senden", "horn", "hille", "steinheim", "freudenberg"):
            assert name not in filters.NRW_PLACES, name


# -- v3: found by the second review (wf_7c774b8d-79b) ------------------------

class TestMultiOfficeStringsAreJudgedPerOffice:
    """A foreign CITY alone never overrides a German place; a foreign country,
    region or code still does, but only when no office is German on its own."""

    @pytest.mark.parametrize("loc", [
        "Köln; Amsterdam", "Berlin / Zürich", "Köln, Wien", "Münster, Utrecht", "Aachen, Maastricht",
        "Essen, Rotterdam", "Remote, Berlin or Vienna", "Düsseldorf & Paris", "Bonn | Brussels",
        "Munich; London, UK", "Berlin; Paris, France", "Hamburg / Singapore",
    ])
    def test_a_german_office_next_to_a_foreign_one_is_german(self, loc):
        assert filters.is_german_location(loc), loc
        assert not filters.is_foreign_location(loc), loc

    @pytest.mark.parametrize("loc", [
        "Maastricht, Limburg, Netherlands", "Hanover, NH", "Konstanz, Switzerland",
        "Soest, Utrecht, Netherlands", "Trier, Luxemburg", "Frankfurt, Singapore", "Dresden, OH",
    ])
    def test_a_hierarchical_place_with_a_foreign_country_or_code_is_still_foreign(self, loc):
        assert filters.is_foreign_location(loc), loc

    @pytest.mark.parametrize("loc", ["Wien", "Amsterdam", "Zürich", "Madrid", "Lisbon", "Prague"])
    def test_a_bare_foreign_city_is_still_foreign(self, loc):
        assert filters.is_foreign_location(loc), loc

    def test_the_pre_screen_keeps_the_multi_office_jobs_it_used_to_drop(self):
        for loc in ("Köln; Amsterdam", "Berlin / Zürich", "Köln, Wien"):
            disq, why, _c = scorer._hard_disqualify(_j(loc=loc))
            assert not disq, (loc, why)

    def test_the_recall_gate_of_the_ats_scrapers_agrees(self):
        for loc in ("Köln; Amsterdam", "Berlin / Zürich"):
            assert scrapers._de_place(loc), loc

    @pytest.mark.parametrize("loc", ["Münster, Utrecht", "Aachen, Maastricht", "Essen, Rotterdam", "Gronau / Enschede"])
    def test_an_nrw_town_next_to_a_foreign_city_is_still_nrw(self, loc):
        j = main._tag_where(_j(loc=loc, desc="Vor Ort im Büro."))
        assert j["_nrw"] and main._is_in_focus_area(j), loc

    def test_the_location_is_capped_so_garbage_cannot_make_a_regex_crawl(self):
        import time
        t0 = time.time()
        filters.is_german_location("x" + " " * 20000 + ", ca")
        filters.is_foreign_location("x" + " " * 20000 + ", nw")
        assert time.time() - t0 < 0.5


class TestRemoteRolesThatNameGermany:
    """The v2 guard `and not has_blocked` dropped these; production kept them."""
    R = "Fully remote working student role. Python and SQL pipelines, English-speaking team."

    @pytest.mark.parametrize("loc", [
        "Remote - Germany or United Kingdom", "Remote (Germany, London)", "Remote - Germany, United States",
        "Munich, Germany; London, UK", "Remote - Germany or LATAM", "Berlin, Germany / Singapore (remote)",
    ])
    def test_a_remote_role_that_names_germany_is_kept(self, loc):
        disq, why, _c = scorer._hard_disqualify(_j(loc=loc, desc=self.R))
        assert not disq, (loc, why)

    @pytest.mark.parametrize("loc", ["Remote - Paderborn", "Iserlohn (remote)", "Karlsruhe, Remote", "Remote, NW"])
    def test_a_remote_role_in_a_german_town_is_german_evidence(self, loc):
        """The location leg of the German evidence: the ad never says Germany."""
        disq, why, _c = scorer._hard_disqualify(_j(loc=loc, desc=self.R))
        assert not disq, (loc, why)

    def test_the_ad_text_leg_of_the_german_evidence(self):
        d = ("Fully remote working student role, Python and SQL pipelines. Our headquarters are in "
             "Berlin and the team works in CET. English-speaking.")
        disq, why, _c = scorer._hard_disqualify(_j(loc="Remote", desc=d))
        assert not disq, why

    def test_ad_text_town_names_do_not_count_as_german_evidence(self):
        assert not filters.mentions_germany("Our partner office is in Aachen and Paderborn.")
        assert not filters.mentions_germany("We also have staff in Nürnberg and Karlsruhe.")

    def test_a_remote_role_abroad_is_still_dropped(self):
        disq, _w, cat = scorer._hard_disqualify(_j(loc="Hanover, NH, United States", desc=self.R))
        assert disq and cat == "location"


class TestCodesAndRegionsOnTownsNoListKnows:
    """Unlisted towns, so only the CODE or REGION can say where they are."""

    @pytest.mark.parametrize("loc", ["Hengelo, NL", "Bolzano, IT", "Zug, CH", "Gdynia, PL", "Nyon, CH"])
    def test_a_foreign_country_code_is_abroad(self, loc):
        assert filters.is_foreign_location(loc) and not filters.is_german_location(loc), loc
        disq, _w, cat = scorer._hard_disqualify(_j(loc=loc))
        assert disq and cat == "location", loc

    @pytest.mark.parametrize("loc", ["Kitchener, ON", "Halifax, NS", "Hobart, TAS", "Newcastle, NSW", "Laval, QC"])
    def test_canadian_and_australian_codes_are_abroad(self, loc):
        assert filters.is_foreign_location(loc), loc
        disq, _w, cat = scorer._hard_disqualify(_j(loc=loc))
        assert disq and cat == "location", loc

    @pytest.mark.parametrize("loc", ["Hanover, NH 03755", "Springfield, IL 62701-1234", "Hanover, NH, USA",
                                     "Hanover, NH, US", "Kiel, WI 53042"])
    def test_us_state_codes_with_a_zip_or_country_tail_are_abroad(self, loc):
        assert filters.is_foreign_location(loc) and not filters.is_german_location(loc), loc

    @pytest.mark.parametrize("loc", ["Palo Alto, California", "Madison, Wisconsin", "Springfield, Illinois",
                                     "Leeds, Yorkshire, England", "Zagreb, Croatia"])
    def test_foreign_region_and_country_names_are_abroad(self, loc):
        assert filters.is_foreign_location(loc) and not filters.is_german_location(loc), loc

    def test_an_unlisted_town_with_the_nrw_code_is_nrw_on_site(self):
        for loc in ("Musterstadt, NW", "Unbekanntdorf, NW, DE", "Zzyzx, NW, DE, 50933"):
            assert not main._place_in(loc.lower(), main._NRW_PLACES), loc
            j = main._tag_where(_j(loc=loc, desc="Vor Ort im Büro."))
            assert j["_nrw"], loc

    @pytest.mark.parametrize("loc", ["Deutschlandsberg, Austria", "Deutschlandsberg, Steiermark"])
    def test_the_germany_word_is_matched_as_a_whole_word(self, loc):
        """A real Austrian town starts with 'deutschland'."""
        assert not filters.is_german_location(loc), loc

    @pytest.mark.parametrize("loc", ["Zulm", "Qhagen", "Xkiel", "Mbonn"])
    def test_a_german_town_at_the_end_of_another_word_is_not_german(self, loc):
        assert not filters.is_german_location(loc), loc


class TestTheBonnBeltInsideRhinelandPalatinate:
    @pytest.mark.parametrize("loc", ["Koblenz, Rheinland-Pfalz", "Remagen, RP", "Andernach, Rheinland-Pfalz",
                                     "Linz am Rhein, Rheinland-Pfalz", "Unkel, RP"])
    def test_on_site_in_the_belt_stays_in_focus(self, loc):
        """main's own comment: the belt reaches into RLP. The 'another state'
        veto applies to the NRW town list only, never to the belt."""
        j = main._tag_where(_j(loc=loc, desc="Vor Ort im Büro."))
        assert j["_nrw"] and main._is_in_focus_area(j), loc


class TestStateCodesThatAreAlsoCountryCodes:
    """BE Belgium, BY Belarus, TH Thailand, SN Senegal, NI Nicaragua, SL Sierra
    Leone, BW Botswana, BB Barbados, MV Maldives, SH Saint Helena, ST Sao Tome are
    also German state codes. They are evidence only with ', DE' or a 5-digit
    German postcode; NW, HB, HH, HE and RP are not country codes and stand alone."""

    @pytest.mark.parametrize("loc", ["Brussels, BE", "Minsk, BY", "Bangkok, TH", "Dakar, SN", "Managua, NI",
                                     "Gaborone, BW", "Bridgetown, BB"])
    def test_a_bare_ambiguous_code_is_not_german(self, loc):
        assert not filters.is_german_location(loc), loc

    @pytest.mark.parametrize("loc", ["Berlin, BE, DE", "Gaimersheim, BY, DE", "Unbekanntdorf, TH, 07743",
                                     "Musterstadt, ST, DE, 06108"])
    def test_with_de_or_a_german_postcode_it_is(self, loc):
        assert filters.is_german_location(loc), loc

    def test_a_bare_ambiguous_code_on_an_unknown_town_goes_to_the_model_not_the_bin(self):
        """No evidence either way: not German, not foreign, so the pre-screen keeps it."""
        for loc in ("Unbekanntdorf, BY", "Unbekanntdorf, TH"):
            assert not filters.is_german_location(loc) and not filters.is_foreign_location(loc), loc
            disq, why, _c = scorer._hard_disqualify(_j(loc=loc))
            assert not disq, (loc, why)


class TestRemoteWordOnlyInTheAd:
    """The remote half of the original bug: an unlisted German town plus the word
    'remote' somewhere in the ad was dropped as 'Germany not confirmed'."""
    D = ("Working student in our data team. Python and SQL pipelines for the business. "
         "Remote-friendly culture with an office day each week. English-speaking team.")

    def test_an_unlisted_town_with_remote_only_in_the_ad_goes_to_the_model(self):
        for loc in ("Gescher", "Gaimersheim", "Unbekanntdorf"):
            disq, why, _c = scorer._hard_disqualify(_j(loc=loc, desc=self.D))
            assert not disq, (loc, why)

    @pytest.mark.parametrize("loc", ["Remote", "Remote - EMEA", ""])
    def test_remote_in_the_location_with_no_germany_signal_still_drops(self, loc):
        disq, _w, cat = scorer._hard_disqualify(_j(loc=loc, desc="Fully remote working student role. "
                                                   "Python and SQL pipelines for the business, English-speaking."))
        assert (disq and cat == "location") or loc == "", loc

    def test_remote_with_a_positively_foreign_location_still_drops(self):
        disq, _w, cat = scorer._hard_disqualify(_j(loc="Warsaw, Poland", desc=self.D))
        assert disq and cat == "location"

    @pytest.mark.parametrize("phrase", ["This role is remote worldwide.", "You can work from anywhere in the world.",
                                        "Location independent, fully remote."])
    def test_worldwide_remote_phrasing_covers_germany(self, phrase):
        d = f"Fully remote working student role. Python and SQL pipelines, English-speaking team. {phrase}"
        disq, why, _c = scorer._hard_disqualify(_j(loc="Remote", desc=d))
        assert not disq, (phrase, why)


class TestSmallFixes:
    def test_bare_limburg_is_not_german_evidence(self):
        assert not filters.is_german_location("Limburg")
        assert filters.is_german_location("Limburg an der Lahn")

    @pytest.mark.parametrize("loc", ["Oberbayern", "Süddeutschland", "Nordhessen", "Metropolregion Rhein-Neckar",
                                     "Großraum München", "Ostwestfalen-Lippe"])
    def test_german_compounds_and_regions_are_recognised(self, loc):
        assert filters.is_german_location(loc), loc

    @pytest.mark.parametrize("loc", ["Luedenscheid", "Guetersloh", "Ludenscheid", "Gummersbach", "Hückelhoven",
                                     "Hueckelhoven"])
    def test_ascii_spellings_of_nrw_towns_are_nrw_on_site(self, loc):
        j = main._tag_where(_j(loc=loc, desc="Vor Ort im Büro."))
        assert j["_nrw"] and main._is_in_focus_area(j), loc

    def test_the_answers_are_cached(self):
        filters.is_german_location.cache_clear()
        for _ in range(5):
            filters.is_german_location("Paderborn")
        info = filters.is_german_location.cache_info()
        assert info.hits >= 4 and info.misses == 1
