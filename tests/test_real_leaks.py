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

    def test_lidl_german_internship(self):
        j = _job("Praktikum Data Analytics", """
            Als Teil unseres Lidl Plus international Teams arbeitest du an
            unserem digitalen Vorteilsprogramm. Ab August für 6 Monate.
            Studium im Bereich BWL, VWL, Mathematik / Statistik.
            Pflichtpraktikum: 1.000 € p.M. Erste Erfahrungen mit SQL, Python.
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


class TestWorkingStudentEnglishPhrasing:
    def test_english_working_student_title_is_disqualified(self):
        """Real digest: 'Working Student (f/m/d) Python & AI Automation
        @ Innomotics'. The filter knew 'Werkstudent' but not its English
        translation, and the owner cannot take these roles at all."""
        j = _job("Working Student (f/m/d) Python & AI Automation", """
            Support our Market Intelligence team. Our working language is
            English. You are enrolled at a university.
        """)
        dq, _r, cat = scorer._hard_disqualify(j)
        assert dq and cat == "werkstudent"

    def test_german_werkstudent_still_disqualified(self):
        j = _job("Werkstudent Data Analytics", "Unterstütze unser Team.")
        dq, _r, cat = scorer._hard_disqualify(j)
        assert dq and cat == "werkstudent"
