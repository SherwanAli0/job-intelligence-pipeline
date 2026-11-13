"""Fixes from the 13 November 2026 fix list (research rechecked by Opus agents).

Each class covers one item of ongoing/JOB-PIPELINE-FIX-LIST.md in the owner's
portfolio workspace.
"""
import filters
import scrapers


class _Resp:
    def __init__(self, data, status=200, text="", headers=None):
        self._data = data
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {}

    def json(self):
        return self._data


# ---------------------------------------------------------------- item 1
class TestWorkdayGermanyFacet:
    """Tenants that accept the Germany facet but ignore it must not get their
    foreign postings labelled ", Germany"."""

    def _run(self, monkeypatch, reply):
        def fake_post(url, json=None, headers=None, timeout=None):
            return reply(bool(json["appliedFacets"]), json["searchText"], json["offset"])
        monkeypatch.setattr(scrapers.requests, "post", fake_post)
        monkeypatch.setattr(scrapers, "_wd_fetch_description", lambda *a: "An English advert. " * 30)
        monkeypatch.setattr(scrapers.time, "sleep", lambda *_: None)
        return scrapers._workday_cxs_tenant(("astrazeneca", "wd3", "Careers"))

    @staticmethod
    def _jp(title, loc, i=0):
        return {"title": title, "locationsText": loc, "externalPath": f"/job/x/{i}", "postedOn": "Today"}

    IGNORING_FACETS = [{"facetParameter": "locationMainGroup",
                        "values": [{"facetParameter": "locations", "values": []}]},
                       {"facetParameter": "timeType", "values": []}]
    HONOURING_FACETS = [{"facetParameter": "locationMainGroup",
                         "values": [{"facetParameter": "locationCountry", "values": []}]}]

    def test_a_reply_without_a_country_facet_is_not_labelled_germany(self, monkeypatch):
        def reply(facet, search, offset):
            if search == "intern" and offset == 0:
                return _Resp({"facets": self.IGNORING_FACETS, "jobPostings": [
                    self._jp("Facilities Safety Intern", "US - Tarzana - CA", 1),
                    self._jp("Data Science Intern", "2 Locations", 2)]})
            return _Resp({"facets": self.IGNORING_FACETS, "jobPostings": []})
        out = self._run(monkeypatch, reply)
        assert {j["location"] for j in out} == {"US - Tarzana - CA", "2 Locations"}
        assert "astrazeneca" in scrapers._WD_FACET_IGNORED

    def test_a_reply_with_the_country_facet_is_labelled_germany(self, monkeypatch):
        scrapers._WD_FACET_IGNORED.clear()

        def reply(facet, search, offset):
            if facet and search == "intern" and offset == 0:
                return _Resp({"facets": self.HONOURING_FACETS,
                              "jobPostings": [self._jp("Data Science Intern", "2 Locations", 2)]})
            return _Resp({"facets": self.HONOURING_FACETS, "jobPostings": []})
        out = self._run(monkeypatch, reply)
        assert [j["location"] for j in out] == ["2 Locations, Germany"]
        assert "astrazeneca" not in scrapers._WD_FACET_IGNORED

    def test_without_a_facets_list_foreign_postings_decide(self, monkeypatch):
        def reply(facet, search, offset):
            if search == "intern" and offset == 0:
                return _Resp({"jobPostings": [self._jp("AI Intern", "Monza, Italy", 1),
                                              self._jp("ML Intern", "CDR (Amsterdam - Cedar)", 2)]})
            return _Resp({"jobPostings": []})
        out = self._run(monkeypatch, reply)
        assert all(not j["location"].endswith("Germany") for j in out)

    def test_a_foreign_place_is_never_relabelled(self):
        assert scrapers._foreign_place("Monza, Italy")
        assert not scrapers._foreign_place("2 Locations")

    def test_the_facet_check_reads_nested_facets(self):
        assert scrapers._wd_has_facet(self.HONOURING_FACETS, "locationCountry")
        assert not scrapers._wd_has_facet(self.IGNORING_FACETS, "locationCountry")


class TestLeadingCountryCode:
    def test_workday_country_prefixes_are_foreign(self):
        for loc in ("us - gaithersburg - md", "usa - tarzana, ca", "us – tarzana – ca",
                    "in - bangalore", "gb - london"):
            assert filters.is_foreign_location(loc), loc

    def test_german_and_mixed_strings_are_not(self):
        for loc in ("de - berlin", "us - remote - germany", "2 locations", "köln"):
            assert not filters.is_foreign_location(loc), loc
