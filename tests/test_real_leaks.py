"""
Regression fixtures built from a real digest the owner could not use.

Every job below was actually delivered on 2026-07-21 and was unapplicable for
a concrete reason: German-language body, German C1 required, or multi-year
experience. Each is pinned here so the leak that let it through cannot reopen.

Equally important are the KEEP cases at the bottom. The filter chain has twice
been made too aggressive and silently killed good jobs (an Airbus VIE
graduate programme, a Wolt "(Senior)" role where the parentheses meant senior
was OPTIONAL). Tightening without those guards trades one failure for a worse,
invisible one.
"""

import pytest

import main
import scorer


def _job(title, description, location="Berlin, Germany", source="linkedin"):
    return {
        "id": "t", "title": title, "company": "Acme", "location": location,
        "url": "https://example.com/j", "source": source,
        "description": description, "posted_at": "",
    }


def _survives_pipeline(j):
    """True if the job would reach the digest: passes every pre-scorer filter
    AND is not hard-disqualified by the scorer."""
    if not main._is_attendable_from_germany(j):
        return False
    if not main._is_english_friendly(j):
        return False
    if not main._no_experience_overload(j):
        return False
    if not main._not_fulltime_senior(j):
        return False
    if not main._no_masters_required(j):
        return False
    disqualified, _reason, _cat = scorer._hard_disqualify(j)
    return not disqualified


# ── The real jobs that should NEVER have arrived ─────────────────────────────

class TestGermanLanguageBodies:
    def test_dkb_mlops_german_body_and_c1_german(self):
        """German body, and 'Deutsch- und Englischkenntnisse auf mindestens
        C1-Niveau'. Title was scraped without the (m/w/d) marker, so the
        title-based German check never fired."""
        j = _job("MLOps Engineer", """
            Dein Profil: Studium der (Wirtschafts-)Informatik, Data Science o. ä.
            Mehrjährige Berufserfahrung im Bereich Machine Learning Engineering,
            ML Ops, Platform Engineering, Cloud Engineering oder DevOps.
            Sehr gute Python-Skills sowie Erfahrung mit gängigen ML-Frameworks.
            Deutsch- und Englischkenntnisse auf mindestens C1-Niveau.
            Deine Aufgaben: Aufbau, Weiterentwicklung und Betrieb einer zentralen
            AI-Plattform für bankweit genutzte KI-Dienste.
        """)
        assert not _survives_pipeline(j)

    def test_tqg_ai_engineer_gn_marker(self):
        """'(gn)' is a German gender marker the filter did not know."""
        j = _job("AI Engineer (gn)", """
            Künstliche Intelligenz verändert die Art und Weise, wie wir arbeiten.
            Du übersetzt Anforderungen aus den Fachbereichen in konkrete,
            produktive AI-Lösungen. Du arbeitest hands-on mit unseren AI-Werkzeugen
            und baust Workflows und Use-Cases. Aufgrund organisatorischer und
            regulatorischer Anforderungen ist ein Wohnsitz in Deutschland
            erforderlich. Deine Erfahrung: Erfahrung in der Integration von KI.
        """)
        assert not _survives_pipeline(j)

    def test_paretos_native_german_required(self):
        j = _job("Forward Deployed Engineer Applied AI (f/m/x)", """
            paretos ist die führende KI-basierte Decision Intelligence Plattform.
            Qualifikation: Mehrere Jahre Erfahrung in einer technischen,
            kundennahen Rolle. Deutsch auf muttersprachlichem Niveau oder C1,
            fließendes Englisch. Du führst Discovery-Workshops durch.
        """)
        assert not _survives_pipeline(j)

    def test_amber_german_body_with_fm_star_marker(self):
        j = _job("AI Adoption Engineer (F/M/*)", """
            Aus Europa, für Europa. Wir sind amber, ein wachsendes KI-Startup.
            Deine Aufgaben: Du begleitest unsere Kund:innen bei der Einführung.
            Du führst Workshops, Schulungen und regelmäßige Check-ins durch.
            Deine Qualifikationen: Du kommunizierst sicher auf Deutsch und
            Englisch und fühlst dich in einem internationalen Umfeld wohl.
        """)
        assert not _survives_pipeline(j)

    def test_lidl_german_internship_now_survives(self):
        """This fixture has flipped twice, and both flips were deliberate.

        It was pinned as a leak when the pipeline hunted full-time junior
        roles: a German-language Praktikum was wrong on both counts. Two later
        decisions reversed that — internships became targets (2026-08-16), and
        German-language STUDENT ads stopped being filtered on ad language
        (they are the bulk of the market; only an explicit C1 demand
        disqualifies). Nothing here demands fluent German, so it now belongs
        in the digest and the scorer judges the BWL/VWL study-field mismatch.
        """
        j = _job("Praktikum Data Analytics", """
            Als Teil unseres Lidl Plus international Teams arbeitest du an
            unserem digitalen Vorteilsprogramm. Ab August für 6 Monate.
            Studium im Bereich BWL, VWL, Mathematik / Statistik.
            Pflichtpraktikum: 1.000 € p.M. Erste Erfahrungen mit SQL, Python.
        """)
        assert _survives_pipeline(j)

    def test_a_german_internship_demanding_c1_is_still_dropped(self):
        """The boundary that still holds: ad language is fine, an explicit
        fluent-German requirement is not."""
        j = _job("Praktikum Data Analytics", """
            Als Teil unseres Teams arbeitest du an unserem Programm.
            Voraussetzung: verhandlungssicheres Deutsch auf C1-Niveau.
        """)
        assert not _survives_pipeline(j)


class TestMultiYearExperienceWithoutDigits:
    @pytest.mark.parametrize("phrase", [
        "Mehrjährige Berufserfahrung im Bereich Machine Learning",
        "Mehrere Jahre Erfahrung in einer technischen Rolle",
        "Langjährige Erfahrung in der Softwareentwicklung",
        "Fundierte Berufserfahrung im Data-Science-Umfeld",
        "einschlägige Berufserfahrung erforderlich",
    ])
    def test_german_multi_year_phrases_are_caught(self, phrase):
        """These say 'several years' in words. The regex needs a digit, so it
        matched nothing and the job read as junior-friendly."""
        assert not main._no_experience_overload(_job("Engineer", phrase))


class TestRequirementsBelowTheTruncationPoint:
    def test_four_plus_years_deep_in_a_long_description(self):
        """Air Apps: 'Around 4+ years'. The filter drops this correctly when it
        can see it — it sat below the 1,500-char cut applied at scrape time."""
        filler = "About Air Apps. We believe in thinking bigger. " * 60
        j = _job("AI/ML Engineer",
                 filler + " Requirements: Around 4+ years of experience in AI/ML development.")
        assert len(j["description"]) > 1500
        assert not main._no_experience_overload(j)

    def test_five_plus_years_deep_in_a_long_description(self):
        filler = "At bookingkit, Europe's leading booking software. " * 60
        j = _job("Backend Software Engineer",
                 filler + " Requirements: 5+ years of software engineering experience.")
        assert not main._no_experience_overload(j)


class TestSeniorityAndLocationHiddenInTheBody:
    def test_recruiter_generic_title_but_senior_role_in_body(self):
        """Acceler8: title 'Machine Learning Engineer', body advertises
        'Senior AI/ML Engineers'. The senior filter only read the title."""
        j = _job("Machine Learning Engineer", """
            Senior AI/ML Engineers - Autonomous Systems | Germany.
            I'm working with a leading European defence technology company.
            Professional experience in AI, ML, computer vision or robotics.
        """)
        assert not _survives_pipeline(j)

    def test_onsite_elsewhere_despite_german_location_field(self):
        """Air Apps was listed against Germany but is fully onsite in Lisbon."""
        j = _job("AI/ML Engineer", """
            This is a fully onsite position, based at our office in Lisbon,
            where you will collaborate closely with cross-functional teams.
            We are open to support with relocation efforts.
        """, location="Europe")
        assert not _survives_pipeline(j)


# ── Guardrails: these MUST still get through ─────────────────────────────────

class TestGoodJobsStillSurvive:
    def test_english_junior_role_in_germany(self):
        j = _job("Junior Machine Learning Engineer", """
            We are looking for a junior ML engineer to join our Berlin team.
            You will work with Python, PyTorch and SQL. Our working language is
            English. No prior professional experience required, 0-2 years welcome.
        """)
        assert _survives_pipeline(j)

    def test_graduate_programme_keeps_its_immunity(self):
        """An Airbus VIE graduate programme was once killed by boilerplate
        'years of experience' text in the body. Title intent must win."""
        j = _job("VIE - Data Analyst", """
            Graduate programme for recent graduates. Our team language is English.
            The ideal candidate has 3 years of experience in a similar role.
        """)
        assert _survives_pipeline(j)

    def test_parenthesised_senior_still_means_optional(self):
        """'(Senior) Applied Scientist' is German-ad convention for
        'senior OPTIONAL' — juniors are explicitly considered."""
        j = _job("(Senior) Applied Scientist", """
            We welcome candidates at all levels. The team language is English.
            Python and machine learning experience is valued.
        """)
        assert _survives_pipeline(j)

    def test_german_word_in_a_company_name_is_not_a_german_job(self):
        j = _job("Machine Learning Engineer", """
            Join Deutsche Bank Technology Centre. The working language is English
            across all engineering teams. We welcome junior applicants.
        """)
        assert _survives_pipeline(j)

    def test_english_role_mentioning_german_as_a_nice_to_have(self):
        """German named only as a bonus must not trigger the German filters.
        No year requirement here: '2+ years' is now a drop (see
        TestTwoYearsIsNowExcluded), which is a separate rule."""
        j = _job("AI Product Engineer", """
            Berlin-based team, our working language is English. We welcome
            junior engineers building production systems. Working knowledge of
            German a bonus, not required.
        """)
        assert _survives_pipeline(j)


class TestTwoYearsIsNowExcluded:
    """The owner reviewed a real digest and named 'wants 2 years experience'
    as a reason he could not apply, so 2 years is now treated like 3: dropped
    unless the text softens it."""

    def test_two_plus_years_is_dropped(self):
        assert not main._no_experience_overload(
            _job("Engineer", "You bring 2+ years of engineering experience."))

    def test_plain_two_years_is_dropped(self):
        assert not main._no_experience_overload(
            _job("Engineer", "We require 2 years of experience in Python."))

    def test_softened_two_years_still_survives(self):
        assert main._no_experience_overload(
            _job("Engineer", "Ideally 2 years of experience, but not required."))

    def test_one_year_still_survives(self):
        assert main._no_experience_overload(
            _job("Engineer", "1 year of experience with Python is enough."))


class TestDigestRepeats:
    """The owner received 'AI & Data Engineer @ RWE AG' on consecutive days.
    seen_jobs.json remembers URLs, but Adzuna mints a new tracking URL for the
    same posting on every scrape, so URL memory cannot stop the repeat. Digest
    memory is therefore keyed on normalized (company, title) as well."""

    def test_same_job_different_url_produces_the_same_digest_key(self):
        a = _job("AI & Data Engineer d/f/m", "x")
        a["company"] = "RWE AG"
        b = dict(a, url="https://adzuna.de/land/ad/999?tracking=DIFFERENT")
        assert main._digest_key(a) == main._digest_key(b)

    def test_gender_marker_and_legal_form_do_not_change_the_key(self):
        a = _job("AI & Data Engineer (m/w/d)", "x"); a["company"] = "RWE AG"
        b = _job("AI & Data Engineer", "x"); b["company"] = "RWE"
        assert main._digest_key(a) == main._digest_key(b)

    def test_different_roles_at_the_same_company_keep_distinct_keys(self):
        a = _job("Data Engineer", "x"); a["company"] = "RWE AG"
        b = _job("Data Scientist", "x"); b["company"] = "RWE AG"
        assert main._digest_key(a) != main._digest_key(b)


class TestFreshnessCap:
    """13 of 14 jobs in a real digest were more than a day old; one was six
    days old. Running twice daily exists to apply FAST, so known-stale
    postings never reach the digest."""

    def _aged(self, hours):
        from datetime import datetime, timedelta, timezone
        ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        j = _job("Data Scientist", "x")
        j["posted_at"] = ts
        return j

    def test_six_day_old_posting_is_dropped(self):
        assert not main._is_fresh_enough(self._aged(6 * 24))

    def test_thirty_hour_old_posting_is_dropped(self):
        assert not main._is_fresh_enough(self._aged(30))

    def test_recent_posting_survives(self):
        assert main._is_fresh_enough(self._aged(5))

    def test_unknown_age_is_kept_not_guessed(self):
        j = _job("Data Scientist", "x")
        j["posted_at"] = ""
        assert main._is_fresh_enough(j)


class TestWerkstudentIsNowTheTarget:
    """The exact inversion of this file's oldest rule. 'Working Student
    (f/m/d) Python & AI Automation @ Innomotics' was once pinned here as a
    leak to kill; from October 2026 the owner is enrolled at Uni Bonn and
    that posting is the single best kind of job the pipeline can find."""

    def test_english_working_student_title_now_survives(self):
        j = _job("Working Student (f/m/d) Python & AI Automation", """
            Support our Market Intelligence team. Our working language is
            English. You are enrolled at a university.
        """, location="Köln, Germany")
        assert main._is_student_role(j)
        assert _survives_pipeline(j)

    def test_german_werkstudent_title_is_recognised(self):
        j = _job("Werkstudent Data Analytics (m/w/d)", "Support our team.",
                 location="Bonn, Germany")
        assert main._is_student_role(j)

    def test_studentische_hilfskraft_is_recognised(self):
        assert main._is_student_role(
            _job("Studentische Hilfskraft Informatik", "x", location="Bonn"))

    def test_full_time_role_is_not_a_student_role(self):
        """The mirror image: a perfect topical match that is full-time is now
        the thing being filtered out."""
        j = _job("Junior Machine Learning Engineer", """
            Full-time position, 40h/week. Python, PyTorch. English team.
        """, location="Köln, Germany")
        assert not main._is_student_role(j)

    def test_werkstudent_mentioned_only_in_the_body_still_counts(self):
        j = _job("Data Analytics Support (m/w/d)", """
            This is a Werkstudent position, 20 hours per week alongside your
            studies. English-speaking team.
        """, location="Bonn, Germany")
        assert main._is_student_role(j)


class TestGermanMarketIsNotGermanLanguage:
    """German employers put (m/w/d) and the word 'Werkstudent' on nearly every
    student ad, including ones written entirely in English. Treating either as
    proof of a German-language posting deleted most of the target market."""

    def test_german_titled_werkstudent_with_english_body_survives(self):
        j = _job("Werkstudent Data Science (m/w/d)", """
            You will join our analytics team for 20 hours per week alongside
            your studies. You will build reporting pipelines, analyse product
            usage data and present findings to the team. We expect solid Python
            and SQL knowledge and curiosity about machine learning methods.
        """, location="Köln, Germany")
        assert main._is_english_friendly(j), \
            "(m/w/d) plus an English body must not read as a German posting"

    def test_studentische_hilfskraft_title_with_english_body_survives(self):
        j = _job("Studentische Hilfskraft NLP", """
            Join our research group for ten to nineteen hours per week. You
            will run experiments with transformer models, prepare datasets and
            help our doctoral researchers evaluate results. Python and a
            genuine interest in language technology are what matter here.
        """, location="Bonn, Germany")
        assert main._is_english_friendly(j)

    def test_german_bodied_student_ad_now_survives_too(self):
        """Superseded on 2026-08-15. This case used to assert a drop: a German
        BODY was disqualifying however the ad was titled. The first live run
        of Werkstudent mode showed that rule killed 62 of 76 reachable student
        roles and produced an empty digest, so for student roles the ad's
        language is no longer evidence of anything. See
        TestGermanLanguageStudentAdsAreAllowed for the replacement rule."""
        j = _job("Werkstudent Data Science (m/w/d)", """
            Du unterstützt unser Team bei der Entwicklung von Datenprodukten
            und arbeitest mit uns an der Auswertung von Kundendaten. Du bist
            eingeschrieben an einer Hochschule und hast bereits erste
            Kenntnisse in Python sowie Freude an der Arbeit mit Daten.
        """, location="Köln, Germany")
        assert main._is_english_friendly(j)

    def test_german_bodied_FULL_TIME_ad_is_still_dropped(self):
        """The rule above is scoped to student roles. For everything else a
        German body is still disqualifying, exactly as before."""
        j = _job("Data Scientist (m/w/d)", """
            Du unterstützt unser Team bei der Entwicklung von Datenprodukten
            und arbeitest mit uns an der Auswertung von Kundendaten. Du hast
            ein abgeschlossenes Studium und bereits erste Kenntnisse in
            Python sowie Freude an der Arbeit mit Daten.
        """, location="Köln, Germany")
        assert not main._is_english_friendly(j)

    def test_short_stub_student_ad_is_kept_for_the_scorer_to_judge(self):
        """A two-line stub carries no language evidence either way. Under the
        student rule that is not grounds for a silent drop; the scorer sees it
        and the explicit-C1 check still applies."""
        j = _job("Werkstudent Data Science (m/w/d)", "Werkstudent gesucht.",
                 location="Köln, Germany")
        assert main._is_english_friendly(j)

    def test_short_stub_NON_student_ad_still_cannot_claim_english(self):
        """_reads_as_english demands positive evidence, so a two-line stub
        with a gender marker is still dropped for non-student roles."""
        j = _job("Data Scientist (m/w/d)", "Data Scientist gesucht.",
                 location="Köln, Germany")
        assert not main._is_english_friendly(j)


class TestGermanLanguageStudentAdsAreAllowed:
    """The 2026-08-15 run produced an EMPTY digest: the ad-language filter
    killed 62 of the 76 student roles within reach of Bonn. German Werkstudent
    ads are written in German as a matter of course, so filtering on the
    language of the advertisement deleted the market. Only an explicit demand
    for fluent German is disqualifying now."""

    _GERMAN_BODY = """
        Als Werkstudent unterstützt du unser Data-Team bei der Entwicklung
        von Auswertungen und Dashboards. Du arbeitest mit Python und SQL und
        wertest Produktdaten aus. Du bist an einer Hochschule immatrikuliert
        und hast bis zu 20 Stunden pro Woche Zeit. Wir bieten flexible
        Arbeitszeiten passend zu deinem Stundenplan und ein junges Team.
    """

    def test_german_language_werkstudent_ad_survives(self):
        j = _job("Werkstudent Data Analytics (m/w/d)", self._GERMAN_BODY,
                 location="Köln, Germany")
        assert main._is_english_friendly(j)
        assert _survives_pipeline(j)

    def test_german_werkstudent_ad_demanding_c1_still_drops(self):
        j = _job("Werkstudent Data Analytics (m/w/d)", self._GERMAN_BODY + """
            Voraussetzung: verhandlungssicheres Deutsch auf C1-Niveau.
        """, location="Köln, Germany")
        assert not _survives_pipeline(j)

    def test_german_language_FULL_TIME_ad_still_drops(self):
        """The exemption is scoped to student roles. A German-language
        full-time ad must still be filtered exactly as before."""
        j = _job("Data Analyst (m/w/d)", """
            Für unser Data-Team suchen wir eine Datenanalystin oder einen
            Datenanalysten. Du entwickelst Auswertungen und Dashboards mit
            Python und SQL und arbeitest eng mit den Fachbereichen zusammen.
            Wir bieten flexible Arbeitszeiten und ein junges Team.
        """, location="Köln, Germany")
        assert not main._is_english_friendly(j)

    def test_english_student_ad_is_unaffected(self):
        j = _job("Working Student Data Science", """
            Support our data team 20 hours per week alongside your studies.
            You are enrolled at a university. Python and SQL. Our working
            language is English and hours are flexible around lectures.
        """, location="Bonn, Germany")
        assert _survives_pipeline(j)


class TestInternshipsAreTargetsToo:
    """Added 2026-08-16 on request. Internships sit alongside Werkstudent as a
    valid employment form; only full-time permanent roles, Ausbildung and
    thesis positions are excluded now."""

    def test_german_praktikum_counts(self):
        assert main._is_student_role(
            _job("Praktikum Data Science (m/w/d)", "x", location="Köln"))

    def test_praktikant_counts(self):
        assert main._is_student_role(
            _job("Praktikant (m/w/d) Machine Learning", "x", location="Bonn"))

    def test_english_internship_counts(self):
        assert main._is_student_role(
            _job("Data Science Internship", "x", location="Bonn"))

    def test_praxissemester_counts(self):
        assert main._is_student_role(
            _job("Praxissemester Data Analytics", "x", location="Bonn"))

    def test_internship_named_only_in_the_body_counts(self):
        assert main._is_student_role(_job("Data Analytics Support", """
            This is a 6-month internship for enrolled students, starting in
            October. You will work with Python and SQL.
        """, location="Bonn"))

    def test_the_word_international_is_not_an_internship(self):
        """'intern' needs a word boundary: 'international', 'internal' and
        'Internet' appear in a large share of tech postings and would
        otherwise drag every full-time role back into scope."""
        assert not main._is_student_role(_job("Data Analyst International", """
            Full-time permanent role reporting to our internal analytics team.
            You will use our internal tooling and the Internet of Things stack.
        """, location="Köln"))

    def test_full_time_role_still_excluded(self):
        assert not main._is_student_role(
            _job("Junior Data Scientist", "Full-time, 40h per week, permanent.",
                 location="Köln"))

    def test_thesis_positions_remain_out_of_scope(self):
        """Not asked for, and a different kind of search."""
        from filters import title_is_worth_fetching
        assert not title_is_worth_fetching("Masterarbeit Machine Learning")
        assert not title_is_worth_fetching("Abschlussarbeit Data Science")

    def test_ausbildung_remains_out_of_scope(self):
        from filters import title_is_worth_fetching
        assert not title_is_worth_fetching("Ausbildung Fachinformatiker")

    def test_unpaid_internships_are_still_disqualified(self):
        """Including internships must not open the door to unpaid ones."""
        dq, _r, cat = scorer._hard_disqualify(_job("Praktikum AI Research", """
            This is an unpaid internship for six months. You are enrolled at a
            university.
        """, location="Bonn"))
        assert dq and cat == "unpaid"

    def test_hiwi_alternative_actually_matches(self):
        """Regression: this alternative was written into the file as a literal
        backspace byte (a generator script emitted \\b inside a non-raw Python
        string), so it read as 'hiwi<BS>' and could never match anything."""
        assert main._is_student_role(_job("HiWi Stelle Informatik", "x",
                                          location="Bonn"))


class TestBonnRegionIsRecognisedAsGermany:
    """The pipeline could not recognise its own home region.

    GERMANY_TERMS listed Berlin, Munich, Köln and the big states, but not
    Bonn. A posting whose location field reads just "Bonn", "Bonn-Rhein-Sieg",
    "Sankt Augustin" or "Koblenz" — exactly how Stellenwerk, Fraunhofer, BWI
    and Debeka write it — matched nothing and was hard-disqualified by the
    scorer as being outside Germany, after surviving every other filter.
    """

    def _dq(self, location):
        return scorer._hard_disqualify({
            "title": "Werkstudent Data Science", "company": "X",
            "location": location, "url": "u",
            "description": "Enrolled student, English-speaking team.",
        })

    @pytest.mark.parametrize("location", [
        "Bonn", "Bonn-Rhein-Sieg", "Sankt Augustin", "Siegburg", "Troisdorf",
        "Wachtberg", "Koblenz", "Leverkusen", "Hürth", "Neuss",
    ])
    def test_commute_belt_cities_are_germany(self, location):
        dq, _reason, cat = self._dq(location)
        assert not dq, f"{location} disqualified as {cat}"

    @pytest.mark.parametrize("location", ["Warsaw, Poland", "Austin, Texas"])
    def test_genuinely_foreign_locations_still_dropped(self, location):
        """The fix must not turn the location check into a no-op."""
        dq, _reason, cat = self._dq(location)
        assert dq and cat == "location"


class TestCommutableFromBonn:
    """He studies in Bonn, so an on-site role is only real if he can get
    there and back around lectures. The rule is an explicit ~1h-by-train
    list, NOT 'anywhere in NRW': Bielefeld and Münster are in NRW and are
    over two hours away."""

    def _at(self, location, description="Werkstudent role, English team."):
        return _job("Werkstudent Data Science", description, location=location)

    def test_bonn_is_kept(self):
        assert main._is_commutable_or_remote(self._at("Bonn, Germany"))

    def test_cologne_is_kept(self):
        assert main._is_commutable_or_remote(self._at("Köln, Germany"))

    def test_dusseldorf_is_kept(self):
        assert main._is_commutable_or_remote(self._at("Düsseldorf, Germany"))

    def test_siegburg_is_kept(self):
        assert main._is_commutable_or_remote(self._at("Siegburg, Germany"))

    def test_koblenz_is_kept(self):
        assert main._is_commutable_or_remote(self._at("Koblenz, Germany"))

    def test_berlin_onsite_is_dropped(self):
        assert not main._is_commutable_or_remote(self._at("Berlin, Germany"))

    def test_munich_onsite_is_dropped(self):
        assert not main._is_commutable_or_remote(self._at("München, Germany"))

    def test_aachen_is_dropped_even_though_it_is_nrw(self):
        """Aachen is NRW but 1h11+ from Bonn Hbf — the reason this filter is
        an explicit city list and not a state match."""
        assert not main._is_commutable_or_remote(self._at("Aachen, Germany"))

    def test_dortmund_is_dropped_even_though_it_is_nrw(self):
        assert not main._is_commutable_or_remote(self._at("Dortmund, Germany"))

    def test_remote_germany_is_kept_wherever_the_office_is(self):
        assert main._is_commutable_or_remote(self._at(
            "Berlin, Germany",
            "This role is 100% remote within Germany, work from anywhere."))

    def test_location_field_saying_remote_is_kept(self):
        assert main._is_commutable_or_remote(self._at("Remote, Germany"))

    def test_unknown_location_is_kept_not_guessed(self):
        """Absence of evidence is not evidence of Berlin. Let the scorer
        judge rather than dropping silently."""
        assert main._is_commutable_or_remote(self._at(""))


class TestEnrolledStudentPhrasingSurvives:
    """Werkstudent ads describe enrolment, which the Master's filter used to
    read as a completed-degree requirement. That would now drop the entire
    target market."""

    def test_bachelor_or_master_enrolment_is_not_a_masters_requirement(self):
        j = _job("Werkstudent Data Science", """
            You are enrolled in a Bachelor's or Master's programme in computer
            science, data science or a related field. English-speaking team.
        """, location="Bonn, Germany")
        assert main._no_masters_required(j)
        assert _survives_pipeline(j)

    def test_completed_masters_requirement_still_drops(self):
        j = _job("Werkstudent Data Science", """
            An abgeschlossenes Masterstudium is required for this position.
            English-speaking team.
        """, location="Bonn, Germany")
        assert not _survives_pipeline(j)
