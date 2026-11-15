"""German-level gate (15 November 2026).

The runs of 11 to 13 November 2026 scored ads up to 82 although they demand
German far above the candidate's level. Three holes let them through:

1. the phrase list in scorer.py missed inflected, reordered and
   markdown-escaped wordings;
2. the evidence matcher judged long requirement lines by word overlap and
   called them met;
3. nothing compared the level an ad demands with the candidate's own level.

The wordings below come from those ads (company and old score in the comment).
"""
import pytest

import evidence
import main
import scorer


def _german(text):
    return [d for d in evidence.language_demands(text) if d["lang"] == "german"]


@pytest.fixture
def b1(monkeypatch):
    monkeypatch.setitem(scorer.CANDIDATE_FACTS, "german_level", "B1 (B2 in progress)")


REAL_WORDINGS = [
    (r"Du verfügst über fließende Deutsch\- und gute Englischkenntnisse", 5),          # Liebherr, 62
    (r"Sehr gute Deutsch\- und fließende Englischkenntnisse", 5),                       # Jobster, 45
    ("eine gute Kommunikationsfähigkeit sowie fließende Deutschkenntnisse und gute "
     "Englischkenntnisse in Wort und Schrift mit", 5),                                  # REWE, 45
    ("Du sprichst verhandlungssicher Deutsch und gut Englisch", 5),                     # DEMECAN, 22
    ("Sehr gute Ausdrucksweise in Deutsch und Englisch", 5),                            # Porsche Consulting, 78
    ("Sichere Deutschkenntnisse runden dein Profil ab", 5),                             # ROLAND, 45
    ("Sprachen: gutes Deutsch und Englisch in Wort und Schrift", 4),                    # Bosch, 25
    ("Du sprichst und schreibst fließend Deutsch und gutes Englisch", 5),               # T-Systems, 82
    ("Du kommunizierst sicher auf Deutsch und Englisch", 5),                            # Horváth, 55
    (r"Hervorragende Deutsch\- und Englischkenntnisse", 5),                             # FTI Consulting, 55
    ("Du verfügst über sehr gute Deutschkenntnisse", 5),                                # RWW, 50
    ("Language skills: business fluency in German and English", 5),                     # Oliver Wyman, 45
    ("Gute Kommunikationsfähigkeiten in Deutsch und Englisch", 4),                      # Riverty, 62
    ("Good command of German and English", 4),                                          # ABB, 48
    ("Strong written German and English, with the ability to write precise content", 5),  # Utimaco, 48
    ("Du sprichst sehr gut Deutsch", 5),                                                # Gladhome, 55
]
OTHER_FORMS = [
    ("Sehr gute Deutschkenntnisse, gute Englischkenntnisse", 5),
    ("Du überzeugst mit sehr guten Deutschkenntnissen", 5),
    ("Sehr gute Kenntnisse der deutschen Sprache", 5),
    ("Sehr gute schriftliche und mündliche Deutschkenntnisse", 5),
    ("Deutschkenntnisse auf C1-Niveau", 5),
    ("German: C1, English: C1", 5),
    ("Deutsch, verhandlungssicher; Englisch, gut", 5),
    ("Deutsch auf muttersprachlichem Niveau", 6),
    ("Good German skills (B2) required", 4),
    ("Grundkenntnisse in Deutsch", 2),
    ("German B1 or higher", 3),
]


class TestTheLevelAnAdDemands:
    @pytest.mark.parametrize("text,rank", REAL_WORDINGS + OTHER_FORMS)
    def test_wordings(self, text, rank):
        got, wording = evidence.required_level(text, "german")
        assert got == rank
        assert wording

    @pytest.mark.parametrize("text,rank", [
        ("Sehr gute Englisch- und Deutschkenntnisse", 5),      # level in front of both
        ("Fluent in English and German", 5),
        ("Deutsch und Englisch fließend", 5),                  # level behind both
        ("Deutsch- und Englischkenntnisse auf C1-Niveau", 5),
        ("Deutsch, Englisch und Französisch fließend", 5),
    ])
    def test_coordinated_languages_share_one_level(self, text, rank):
        assert evidence.required_level(text, "german")[0] == rank

    def test_a_level_stated_for_the_neighbour_alone_is_not_shared(self):
        assert evidence.required_level("English C1 and German", "german") == (0, "")
        assert evidence.required_level("Fließende Deutsch- und gute Englischkenntnisse", "english")[0] == 4

    def test_every_mention_is_read_not_only_the_first(self):
        text = ("Wir sind im deutschsprachigen Raum aktiv. Deutsch ist unsere Teamsprache.\n"
                "Dein Profil: Python, SQL. Sehr gute Deutschkenntnisse.")
        assert evidence.required_level(text, "german")[0] == 5


class TestSoftenedDemands:
    @pytest.mark.parametrize("text", [
        "Sehr gute Deutsch- und Englischkenntnisse sind von Vorteil",
        "Gute Deutschkenntnisse sind wünschenswert, aber kein Muss",
        "Idealerweise sehr gute Deutschkenntnisse",
        "Python und idealerweise gute Deutschkenntnisse",
        "Fluent German is a plus",
        "Nice to have: fluent German",
        "Fluent English, German is a plus",
        "Fluent in English, German language is a big plus",
        "Sehr gute Englischkenntnisse, Deutschkenntnisse sind von Vorteil",
        "A good knowledge of French or German would be an asset",       # WHH, 65
        "Fluent German preferred",
        "Keine sehr guten Deutschkenntnisse erforderlich",
    ])
    def test_a_plus_is_not_a_demand(self, text):
        assert evidence.required_level(text, "german") == (0, "")

    def test_the_softened_demand_is_still_reported_as_soft(self):
        (d,) = _german("Fluent German is a plus")
        assert d["rank"] == 5 and d["soft"] is True

    @pytest.mark.parametrize("text", [
        "Sehr gute Deutschkenntnisse, Englischkenntnisse von Vorteil",      # the plus is the English
        "Fließende Deutschkenntnisse und idealerweise erste Erfahrung mit SAP",
        "Englisch von Vorteil und sehr gute Deutschkenntnisse",
        "Fließendes Deutsch ist ein Muss, Englisch von Vorteil",
    ])
    def test_a_softener_for_something_else_does_not_soften_german(self, text):
        assert evidence.required_level(text, "german")[0] == 5


class TestNotADemand:
    @pytest.mark.parametrize("text", [
        "We are a solid German company with a cloud-native stack",
        "Excellent German engineering and advanced German manufacturing",
        "Die Deutsche Bahn bietet deutschlandweit über 500 Berufe",       # Deutsche Bahn, 65: no demand
        "Gutachten auf Deutsch erstellen",
        "Our German clients run Microsoft Dynamics",
        "Our team is German-speaking",
        "Fluent English; German not required",
        "No German required",
        "Kenntnisse in Cloud-Native-Architekturen sowie Deutsch und Englisch",
        "IT-Sicherheit, Deutsch und Englisch",
        "Strong Python skills and German",
        "Deutsch- und Englischkenntnisse",
    ])
    def test_no_level_is_read_into_it(self, text):
        assert _german(text) == []

    def test_markdown_and_blanks_do_not_matter(self):
        plain = evidence.required_level("fließende Deutsch- und gute Englischkenntnisse", "german")
        escaped = evidence.required_level("**fließende** Deutsch\\- und   gute Englischkenntnisse", "german")
        assert plain[0] == escaped[0] == 5


class TestFindingsOfTheIndependentCheck:
    """Edge wordings an independent review of the gate got wrong outputs for."""

    @pytest.mark.parametrize("text", [
        "Don't worry if you don't speak fluent German",
        "You do not need fluent German for this role",
        "Sehr gutes Deutsch wäre schön",
        "Du bist deutschsprachig",
    ])
    def test_no_demand(self, text):
        assert evidence.required_level(text, "german") == (0, "")

    @pytest.mark.parametrize("text,rank", [
        ("Sprachen\nDeutsch\nfließend\nEnglisch\ngut", 5),          # one item per line
        ("Deutsch   \n  fließend", 5),
        ("Deutschsprachig auf C1-Niveau", 5),
        ("Perfect German", 5),
    ])
    def test_demand(self, text, rank):
        assert evidence.required_level(text, "german")[0] == rank

    def test_a_level_below_belongs_to_the_language_above_it_only(self):
        assert evidence.required_level("Sprachen\nDeutsch\nfließend\nEnglisch\ngut", "english")[0] == 4
        assert evidence.required_level("Wir arbeiten auf Deutsch\nFließend Englisch erwarten wir", "german") == (0, "")

    def test_an_ad_that_never_names_german_costs_nothing(self):
        assert evidence.required_level("Python, SQL and Docker. Fluent English required.", "german") == (0, "")


class TestPreScreen:
    @pytest.mark.parametrize("text,rank", REAL_WORDINGS)
    def test_real_wordings_block_a_b1_candidate(self, text, rank, b1):
        assert scorer._candidate_german_rank() == 3
        assert scorer._requires_fluent_german(text.lower())

    @pytest.mark.parametrize("text", [
        "Grundkenntnisse in Deutsch",
        "German B1 or higher",
        "Fluent German is a plus",
        "Sehr gute Englischkenntnisse, Deutschkenntnisse sind von Vorteil",
        "Our German clients run Microsoft Dynamics",
        "Die Deutsche Bahn bietet deutschlandweit über 500 Berufe",
    ])
    def test_what_a_b1_candidate_may_still_see(self, text, b1):
        assert not scorer._requires_fluent_german(text.lower())

    def test_the_gate_follows_the_profile(self, monkeypatch):
        good = ["Gute Deutschkenntnisse erforderlich", "Good command of German and English",
                "Gutes Deutsch und Englisch"]
        fluent = ["Sehr gute Deutschkenntnisse", "Fließende Deutsch- und gute Englischkenntnisse",
                  "Fluent German required"]
        monkeypatch.setitem(scorer.CANDIDATE_FACTS, "german_level", "B1")
        assert all(scorer._requires_fluent_german(t.lower()) for t in good + fluent)
        monkeypatch.setitem(scorer.CANDIDATE_FACTS, "german_level", "B2")
        assert not any(scorer._requires_fluent_german(t.lower()) for t in good)
        assert all(scorer._requires_fluent_german(t.lower()) for t in fluent)
        monkeypatch.setitem(scorer.CANDIDATE_FACTS, "german_level", "C1")
        assert not scorer._requires_fluent_german("fließende deutschkenntnisse und gute englischkenntnisse")

    def test_an_unreadable_level_blocks_only_c1_and_above(self, monkeypatch):
        monkeypatch.setitem(scorer.CANDIDATE_FACTS, "german_level", "some German")
        assert scorer._candidate_german_rank() == 4
        assert not scorer._requires_fluent_german("gutes deutsch und englisch")
        assert scorer._requires_fluent_german("du sprichst verhandlungssicher deutsch")

    def test_hard_disqualify_names_the_wording(self, job, b1):
        j = job(title="Praktikum Digital Analytics (m/w/d)", location="Köln, Germany",
                description=("Du unterstützt unser Team bei Auswertungen mit Python und SQL.\n"
                             "* Du verfügst über fließende Deutsch\\- und gute Englischkenntnisse\n"
                             "* Du bist immatrikuliert"))
        dq, reason, category = scorer._hard_disqualify(j)
        assert dq and category == "german_required"
        assert "fliessende deutsch- und gute" in reason and "B1" in reason

    def test_phrase_list_hits_keep_their_old_reason(self, job, b1):
        j = job(title="Working Student Data", location="Köln, Germany",
                description="We need a student with Python. Mandatory: English and German.")
        dq, reason, category = scorer._hard_disqualify(j)
        assert dq and category == "german_required" and reason.startswith("Requires fluent/native German")


GERMAN_AD = ("Als Werkstudent unterstützt du unser Data-Team bei der Entwicklung "
             "von Auswertungen und Dashboards. Du arbeitest mit Python und SQL und "
             "wertest Produktdaten aus. Du bist an einer Hochschule immatrikuliert "
             "und hast bis zu 20 Stunden pro Woche Zeit für uns im Team.")


def _ad(desc, **kw):
    d = {"id": "t", "title": "Werkstudent Data (m/w/d)", "company": "Acme", "location": "Köln, Germany",
         "url": "https://example.com/j", "source": "s", "description": desc, "posted_at": "", "_form": "student"}
    d.update(kw)
    return d


class TestGermanStudentAds:
    def test_a_german_student_ad_without_a_level_is_still_shown(self, monkeypatch, b1):
        monkeypatch.delenv("JOBHUNTER_GERMAN_ADS", raising=False)
        j = _ad(GERMAN_AD)
        assert main._is_english_friendly(j) and j["_german_ad"] is True

    @pytest.mark.parametrize("line", [
        r"Du verfügst über fließende Deutsch\- und gute Englischkenntnisse.",
        "Du sprichst verhandlungssicher Deutsch und gut Englisch.",
        "Sehr gute Ausdrucksweise in Deutsch und Englisch.",
    ])
    def test_a_german_student_ad_demanding_more_than_b1_is_dropped(self, line, monkeypatch, b1):
        monkeypatch.delenv("JOBHUNTER_GERMAN_ADS", raising=False)
        j = _ad(GERMAN_AD + " " + line)
        assert not main._is_english_friendly(j)
        assert "_german_ad" not in j

    def test_a_plus_keeps_the_ad(self, monkeypatch, b1):
        monkeypatch.delenv("JOBHUNTER_GERMAN_ADS", raising=False)
        j = _ad(GERMAN_AD + " Sehr gute Englischkenntnisse, Deutschkenntnisse sind von Vorteil.")
        assert main._is_english_friendly(j)


class TestEvidenceMatcher:
    CORPUS = evidence.build_corpus(
        facts={"german_level": "B1", "english_level": "C1"},
        documents={"cv": "Built Python and SQL pipelines with FastAPI and Docker in two university projects."})

    def _match(self, requirement):
        return evidence.match_requirement(requirement, evidence.BM25Retriever(self.CORPUS), self.CORPUS)

    @pytest.mark.parametrize("line", [
        "Sehr gute Deutsch- und gute Englischkenntnisse in Wort und Schrift runden dein Profil ab",   # adesso, 82
        "Du bringst fließende Deutschkenntnisse und gute Englischkenntnisse sowie Teamgeist mit",
        "Excellent communication skills with business fluency in German and English are required",
    ])
    def test_a_long_line_with_a_missed_level_is_a_gap(self, line):
        m = self._match(line)
        assert m["status"] == "gap"
        assert [e["id"] for e in m["evidence"]] == ["fact:german_level"]

    def test_a_level_the_candidate_has_is_not_a_gap(self):
        assert self._match("Very good English skills")["status"] == "met"
        assert self._match("Grundkenntnisse in Deutsch")["status"] == "met"

    def test_a_softened_level_is_left_to_retrieval(self):
        line = "Sehr gute Englischkenntnisse, Deutschkenntnisse auf C1-Niveau sind von Vorteil"
        hard = [d for d in evidence.language_demands(line) if not d["soft"]]
        assert {d["lang"] for d in hard} == {"english"}
        m = self._match(line)
        assert not (m["status"] == "gap" and [e["id"] for e in m["evidence"]] == ["fact:german_level"])

    def test_prompt_line_says_gap(self):
        j = {"description": "Dein Profil\n- Sehr gute Deutsch- und gute Englischkenntnisse in Wort und Schrift\n- Python"}
        evidence.attach_evidence([j], self.CORPUS)
        lines = evidence.evidence_prompt_lines(j)
        assert "Deutsch- und gute Englischkenntnisse" in lines
        german_line = next(ln for ln in lines.splitlines() if "Deutsch" in ln)
        assert "=> GAP" in german_line and "MET" not in german_line
