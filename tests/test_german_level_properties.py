"""Property-based tests for the German-level gate (16 November 2026).

Example tests pin the wordings we have seen. These tests state what must hold
for ANY ad built from list items: the level a list demands for German is the
level of its language item, however the list reached us (line breaks, bullets,
or flattened into one line as LinkedIn descriptions are), in whatever order the
other items stand, in upper case, or inside markdown emphasis.

Hypothesis generates the lists. The runs are derandomized, so a failure is
reproducible and CI does not depend on a random seed.
"""
import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import given, settings, strategies as st   # noqa: E402

import evidence   # noqa: E402
import scorer     # noqa: E402

RUN = settings(max_examples=300, derandomize=True, deadline=None)

# One list item that names German, with the CEFR rank it demands outright
# (0: it states no level, or only as a plus).
LANGUAGE_ITEMS = [
    ("Fluent German", 5),
    ("German: fluent", 5),
    ("Sehr gute Deutschkenntnisse", 5),
    ("Deutsch fließend", 5),
    ("Excellent English and German skills", 5),
    ("Very good knowledge of German", 5),
    ("Verhandlungssicherheit in Deutsch", 5),
    ("Native-level German", 6),
    ("Good German skills", 4),
    ("Deutschkenntnisse (B2)", 4),
    ("German B1", 3),
    ("Grundkenntnisse in Deutsch", 2),
    ("German skills", 0),
    ("English and German skills", 0),
    ("Deutsch und Englisch", 0),
    ("German is a plus", 0),
    ("Deutschkenntnisse von Vorteil", 0),
    ("Fluent German is a plus", 0),
]
# Items that name no language. Many open with a word that is also a language
# level ("strong", "sehr gute", "proficient"): those are the ones a flattened
# list lends to its neighbours.
OTHER_ITEMS = [
    "Strong analytical skills",
    "Excellent communication skills",
    "Good understanding of data pipelines",
    "Sehr gute Kenntnisse in SQL",
    "Sicherer Umgang mit MS Office",
    "Sicher im Umgang mit MS Office",
    "Proficient in MS Office",
    "Fluent in Python",
    "Advanced Excel skills",
    "Solid experience with Python",
    "Outstanding problem-solving skills",
    "Basic knowledge of statistics",
    "Fundierte Kenntnisse in Statistik",
    "Gute Kommunikationsfähigkeiten",
    "Teamfähigkeit und Motivation",
    "Team player with a hands-on mindset",
    "Bachelor's degree in computer science",
    "Erste Erfahrung mit Machine Learning",
]
SEPARATORS = ["\n", "\n* ", "\n- ", " • ", " "]          # the last one is the flattened list


@st.composite
def item_lists(draw):
    language, rank = draw(st.sampled_from(LANGUAGE_ITEMS))
    others = draw(st.lists(st.sampled_from(OTHER_ITEMS), min_size=1, max_size=5, unique=True))
    position = draw(st.integers(min_value=0, max_value=len(others)))
    return others[:position] + [language] + others[position:], rank


class TestTheLevelOfAList:
    @RUN
    @given(item_lists(), st.sampled_from(SEPARATORS))
    def test_the_level_is_that_of_the_language_item_however_the_list_is_joined(self, built, separator):
        items, rank = built
        assert evidence.required_level(separator.join(items), "german")[0] == rank

    @RUN
    @given(item_lists())
    def test_flattening_never_changes_the_level(self, built):
        items, _ = built
        assert (evidence.required_level(" ".join(items), "german")[0]
                == evidence.required_level("\n".join(items), "german")[0])

    @RUN
    @given(item_lists(), st.sampled_from(SEPARATORS))
    def test_markdown_emphasis_does_not_matter(self, built, separator):
        items, rank = built
        assert evidence.required_level(separator.join("**%s**" % i for i in items), "german")[0] == rank

    @RUN
    @given(item_lists(), st.sampled_from(SEPARATORS[:-1]))
    def test_case_does_not_matter_while_the_items_are_separated(self, built, separator):
        # In a flattened list the capital letter of the next item is the one sign
        # that a language item ended, so the one-line form is left out here.
        items, rank = built
        assert evidence.required_level(separator.join(items).upper(), "german")[0] == rank
        assert evidence.required_level(separator.join(items).lower(), "german")[0] == rank

    @RUN
    @given(st.lists(st.sampled_from(OTHER_ITEMS), min_size=1, max_size=6), st.sampled_from(SEPARATORS))
    def test_a_list_that_names_no_language_demands_nothing(self, items, separator):
        assert evidence.language_demands(separator.join(items)) == []


class TestSoftening:
    HARD = [(text, rank) for text, rank in LANGUAGE_ITEMS if rank]

    @RUN
    @given(st.sampled_from(HARD), st.sampled_from([" is a plus", " (nice to have)", " von Vorteil",
                                                   " wünschenswert", " would be an asset"]))
    def test_calling_a_demand_a_plus_removes_it(self, item, softener):
        text, _ = item
        assert evidence.required_level(text + softener, "german") == (0, "")


class TestTheGateFollowsTheProfile:
    LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

    @RUN
    @given(item_lists(), st.sampled_from(SEPARATORS))
    def test_a_higher_candidate_level_never_drops_more(self, built, separator):
        items, rank = built
        text = separator.join(items)
        dropped = []
        saved = scorer.CANDIDATE_FACTS.get("german_level")
        try:
            for level in self.LEVELS:
                scorer.CANDIDATE_FACTS["german_level"] = level
                dropped.append(scorer._requires_fluent_german(text.lower(), text))
        finally:
            scorer.CANDIDATE_FACTS["german_level"] = saved
        assert dropped == sorted(dropped, reverse=True)       # True ... True, False ... False
        if rank == 0:
            assert not any(dropped)


class TestAnyText:
    @RUN
    @given(st.text(max_size=300))
    def test_arbitrary_text_never_breaks_the_reader(self, text):
        for demand in evidence.language_demands(text):
            assert 1 <= demand["rank"] <= 6
            assert demand["lang"] in set(evidence._DEMAND_FORMS.values())
        rank, wording = evidence.required_level(text, "german")
        assert 0 <= rank <= 6 and (bool(wording) == bool(rank))
