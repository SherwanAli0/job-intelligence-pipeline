"""German-level gate: flattened lists and levels in nouns (16 November 2026).

The first production run with the level gate (16 November 2026, task definition
revision 56) dropped seven ads for German and all seven did demand it. It also
showed two weak spots:

1. An internship ad that asks for "Kommunikations- und Präsentationssicherheit
   in Deutsch und Englisch" was sent (score 48). The level sits in a noun.
2. LinkedIn descriptions arrive as one line, without list breaks. For
   "... Excellent English and German skills Strong analytical skills ..." the
   gate quoted "german skills strong analytical skills": it had read the
   "strong" of the NEXT list item as the level of German. The ad does ask for
   excellent German, so the outcome was right, but the same reading drops an ad
   that only says "English and German skills".
"""
import pytest

import evidence
import scorer


@pytest.fixture
def b1(monkeypatch):
    monkeypatch.setitem(scorer.CANDIDATE_FACTS, "german_level", "B1 (B2 in progress)")


ACCENTURE = ("Erste Erfahrung in Frontend- und Backendentwicklung Kommunikations- und "
             "Präsentationssicherheit in Deutsch und Englisch Spaß an der Arbeit in einem "
             "internationalen Projektteam")
ANDERCORE = ("Currently enrolled in a Bachelor's or Master's degree program Excellent English and "
             "German skills Strong analytical skills with the ability to interpret financial data")


class TestLevelsInNouns:
    @pytest.mark.parametrize("text", [
        ACCENTURE,
        "Verhandlungssicherheit in Deutsch",
        "Sprachsicherheit in Deutsch und Englisch",
        "Stilsicherheit in der deutschen Sprache",
        "Sicherheit im Umgang mit der deutschen Sprache",
        "Ausdrucks- und Formulierungssicherheit in Deutsch",
    ])
    def test_a_noun_about_speaking_or_writing_states_the_level(self, text):
        rank, wording = evidence.required_level(text, "german")
        assert rank == 5
        assert "sicherheit" in wording

    def test_the_noun_level_is_shared_with_the_coordinated_language(self):
        ranks = {d["lang"]: d["rank"] for d in evidence.language_demands(ACCENTURE)}
        assert ranks == {"german": 5, "english": 5}

    @pytest.mark.parametrize("text", [
        "Erfahrung mit IT-Sicherheit in deutschen Unternehmen",
        "Kenntnisse in Informationssicherheit, Deutsch von Vorteil",
        "Arbeitssicherheit in Deutsch und Englisch dokumentieren",
        "Datensicherheit in Deutsch erklären",
    ])
    def test_other_kinds_of_sicherheit_are_no_language_level(self, text):
        assert evidence.required_level(text, "german") == (0, "")

    def test_a_softened_noun_demand_stays_soft(self):
        assert evidence.required_level("Verhandlungssicherheit in Deutsch ist von Vorteil", "german") == (0, "")

    def test_the_internship_ad_is_dropped_for_a_b1_candidate(self, job, b1):
        assert scorer._requires_fluent_german(ACCENTURE.lower())
        dq, reason, category = scorer._hard_disqualify(
            job(title="Praktikum Salesforce Consulting (all genders)", location="Düsseldorf, Germany",
                description=ACCENTURE))
        assert dq and category == "german_required"
        assert "above the candidate's level" in reason


class TestFlattenedLists:
    def test_the_level_comes_from_the_item_itself_not_from_the_next_one(self):
        rank, wording = evidence.required_level(ANDERCORE, "german")
        assert rank == 5
        assert "excellent english and german skills" in wording
        german = [d for d in evidence.language_demands(ANDERCORE) if d["lang"] == "german"][0]
        assert (german["label"], german["side"]) == ("excellent", "before")     # not "strong", "after"

    @pytest.mark.parametrize("text", [
        # the level word opens the NEXT item
        "Bachelor in Finance English and German skills Strong analytical skills with the ability to",
        "German skills Excellent communication skills",
        "Deutsch Sehr gute Kenntnisse in SQL und Python",
        "Deutsch Gute Kommunikationsfähigkeiten",
        "Deutsch Sicher im Umgang mit MS Office",
        "German Proficient in MS Office and Excel",
        "German, Strong analytical skills",
        "Languages\nGerman\nStrong analytical skills",
        "• German • Strong analytical skills • Excel",
        # the level word belongs to the item BEFORE the language
        "Strong communication skills German and English language documents",
        "You are a strong team player German speaking colleagues will support you",
        "Good understanding of data pipelines German language skills",
        "Sehr gute Kenntnisse in SQL Deutsch und Englisch",
    ])
    def test_a_neighbouring_item_does_not_lend_its_level(self, text):
        assert evidence.required_level(text, "german") == (0, "")

    @pytest.mark.parametrize("text,rank", [
        ("Deutsch fließend Teamfähigkeit und Motivation", 5),      # uninflected: belongs to the language
        ("Deutsch: sehr gute Kenntnisse", 5),
        ("German skills: excellent", 5),
        ("German (fluent)", 5),
        ("German native speaker", 6),
        ("German fluent in speaking and writing", 5),
        ("Deutsch verhandlungssicher erforderlich", 5),
        ("Deutsch sehr gut Englisch gut", 5),
        ("German - very good", 5),
        ("Sehr gute Sprachkenntnisse Deutsch und Englisch", 5),
        ("Excellent language skills German and English", 5),
        ("Excellent communication skills in both German and English", 5),
        ("Sicherer Umgang mit der deutschen Sprache", 5),
        ("Gute bis sehr gute Kenntnisse in Deutsch", 5),
    ])
    def test_genuine_demands_are_still_read(self, text, rank):
        assert evidence.required_level(text, "german")[0] == rank

    def test_a_bullet_is_an_item_break(self):
        text = ("• Practical experience in database modelling • Fluency in German and English "
                "• Strong team spirit")
        rank, wording = evidence.required_level(text, "german")
        assert rank == 5
        assert wording.startswith("fluency in german")

    def test_an_ad_without_a_stated_level_passes_the_gate(self, b1):
        text = ("Bachelor in Finance English and German skills Strong analytical skills with the "
                "ability to interpret financial data")
        assert not scorer._requires_fluent_german(text.lower())

    def test_the_ad_of_16_november_is_still_dropped(self, b1):
        assert scorer._requires_fluent_german(ANDERCORE.lower())


class TestCasesFromTheIndependentCheck:
    """An independent check ran 334 inputs against this change before it was merged. These
    are the ones it got wrong at first."""

    @pytest.mark.parametrize("text", [
        # only a capital letter says that an item ended behind "German": a general adjective
        # right in front of the word is then not enough
        "Strong German Presence in the DACH region.",
        "Strong German Customers base",
        "Excellent German Clients",
        "Strong German Offices and teams",
        "Our strong German Entity",
        "Strong German Industry contacts",
        "Excellent German Brands portfolio",
        "We are a strong German Family business",
        "Excellent German Engineering reputation",
        "Good German Regulations knowledge",
        "Solid German Tax Law background",
        "A good German Employer",
        # the level word belongs to the language name BEHIND it
        "Sie bringen mit Deutsch fließende Englischkenntnisse",
        "You bring German Fluent English",
        "German-speaking team, fluent English",
        "Your profile German Business-level English",
        # no level crosses a bare blank between two items
        "Sehr gute Englischkenntnisse Deutschkenntnisse Sehr gute Excel-Kenntnisse",
        "Dein Profil Deutsch Sichere Beherrschung von Excel",
        # a softener in front still softens
        "Idealerweise sehr gute Deutschkenntnisse",
    ])
    def test_no_demand(self, text):
        assert evidence.required_level(text, "german") == (0, "")

    @pytest.mark.parametrize("text,rank", [
        ("Fluent German Team player with a hands-on mindset", 5),
        ("Very good knowledge of German Experience with SQL", 5),
        ("Excellent written German Strong analytical skills", 5),
        ("Native-level German Driving licence", 6),
        ("Deutsch sehr gut Englisch gut", 5),
        ("German: fluent, English: basic", 5),
        ("Sehr gute Deutschkenntnisse Idealerweise Auslandserfahrung", 5),   # the softener opens the next item
        ("Präsentationssicherheit auf Deutsch", 5),
        ("Sehr gute Englisch- und Deutschkenntnisse", 5),
        ("Englisch/Deutsch fließend", 5),
    ])
    def test_demand(self, text, rank):
        assert evidence.required_level(text, "german")[0] == rank
