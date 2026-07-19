"""
Filter-chain tests. Seeded with the two REAL regressions that once killed
good jobs (the 'VIE - Data Analyst' Airbus posting and the '(Senior) Applied
Scientist' Wolt posting), plus the location-drift case fixed by filters.py.
Every case here is a job the pipeline once handled wrong or plausibly could.
"""
import main
import filters


# ── Location: Germany-attendable only ─────────────────────────────────────────

class TestLocationFilter:
    def test_berlin_kept(self, job):
        assert main._is_attendable_from_germany(job(location="Berlin, Germany"))

    def test_nrw_city_kept(self, job):
        assert main._is_attendable_from_germany(job(location="Düsseldorf"))

    def test_poland_onsite_dropped_by_pipeline(self, job):
        # The original complaint: a Poland F2F job reached the digest. The
        # two-stage contract: main's filter is recall-friendly (EU city →
        # pass through), the scorer's hard disqualifier is the precision
        # stage that must drop it. Assert the CONTRACT, not one stage.
        j = job(title="Data Scientist", location="Warsaw, Poland",
                description="Hybrid role in our Warsaw office. Python, SQL. English team.")
        assert main._is_attendable_from_germany(j)  # recall stage: keep
        import scorer
        dq, _, cat = scorer._hard_disqualify(j)
        assert dq and cat == "location"             # precision stage: drop

    def test_brazil_dropped(self, job):
        assert not main._is_attendable_from_germany(
            job(location="São Paulo, Brazil", description="Onsite in São Paulo."))

    def test_eu_remote_covering_germany_kept(self, job):
        assert main._is_attendable_from_germany(
            job(location="Madrid, Spain",
                description="Spain HQ but fully remote within Europe — work from anywhere in the EU."))

    def test_drift_case_hiring_across_europe(self, job):
        # Regression for the filters.py merge: this phrasing passed main's
        # filter but was killed by scorer's copy before the merge.
        j = job(location="Remote", description="Fully remote — hiring across Europe. English team.")
        assert main._is_attendable_from_germany(j)
        import scorer
        dq, _, _ = scorer._hard_disqualify(j)
        assert not dq

    def test_us_only_remote_dropped(self, job):
        assert not main._is_attendable_from_germany(
            job(location="Remote", description="Fully remote, but US residents only."))

    def test_unknown_location_kept_for_scorer(self, job):
        # Policy: unknown → KEEP, let the scorer decide from the description.
        assert main._is_attendable_from_germany(job(location="", description="Great junior role."))

    def test_shared_constants_are_single_source(self):
        import scorer
        assert main._GERMANY_TERMS is filters.GERMANY_TERMS
        assert scorer._GERMANY_TERMS is filters.GERMANY_TERMS
        assert scorer._REMOTE_COVERS_GERMANY_SIGNALS is filters.REMOTE_COVERS_GERMANY_SIGNALS


# ── Experience: ≤2 years target, graduate-title immunity ─────────────────────

class TestExperienceFilter:
    def test_four_plus_years_dropped(self, job):
        # Neutral title — a 'Junior'/'Graduate' title is deliberately immune.
        assert not main._no_experience_overload(
            job(title="Data Scientist",
                description="Requirements: 4+ years of professional experience with Python."))

    def test_minimum_five_years_dropped(self, job):
        assert not main._no_experience_overload(
            job(title="Data Scientist",
                description="Minimum 5 years experience in data science."))

    def test_junior_title_immune_to_experience(self, job):
        # The flip side of the two tests above: same killer description, but a
        # junior-designed title wins (this is how the Airbus VIE fix works).
        assert main._no_experience_overload(
            job(title="Junior Data Scientist",
                description="Requirements: 4+ years of professional experience with Python."))

    def test_bare_two_years_kept(self, job):
        assert main._no_experience_overload(
            job(description="Ideally 2 years of experience with SQL."))

    def test_no_experience_mention_kept(self, job):
        assert main._no_experience_overload(job(description="Junior role. Python and SQL."))

    def test_airbus_vie_regression(self, job):
        # REAL regression: 'VIE - Data Analyst' (Airbus graduate programme)
        # was killed by the experience filter before title immunity existed.
        assert main._no_experience_overload(
            job(title="VIE - Data Analyst",
                description="The VIE programme requires up to 3 years experience in analytics."))

    def test_graduate_title_immune(self, job):
        assert main._no_experience_overload(
            job(title="Graduate Data Scientist",
                description="You have 5+ years of curiosity and 3 years of Python."))


# ── Senior titles ─────────────────────────────────────────────────────────────

class TestSeniorFilter:
    def test_plain_senior_dropped(self, job):
        assert not main._not_fulltime_senior(job(title="Senior Data Scientist"))

    def test_underscored_senior_dropped(self, job):
        # LinkedIn slug formatting once slipped through
        assert not main._not_fulltime_senior(job(title="Senior_Machine_Learning_Engineer"))

    def test_wolt_parenthesized_senior_regression(self, job):
        # REAL regression: '(Senior) Applied Scientist' is German-ad convention
        # for 'senior optional' — it must SURVIVE.
        assert main._not_fulltime_senior(job(title="(Senior) Applied Scientist"))

    def test_junior_kept(self, job):
        assert main._not_fulltime_senior(job(title="Junior Data Scientist"))

    def test_engineer_iii_dropped(self, job):
        assert not main._not_fulltime_senior(job(title="Data Engineer III"))

    def test_principal_dropped(self, job):
        assert not main._not_fulltime_senior(job(title="Principal ML Engineer"))

    def test_plain_title_kept(self, job):
        assert main._not_fulltime_senior(job(title="Machine Learning Engineer"))


# ── Masters/PhD requirement ───────────────────────────────────────────────────

class TestMastersFilter:
    def test_masters_required_dropped(self, job):
        assert not main._no_masters_required(
            job(description="A Master's degree in Computer Science is required."))

    def test_masters_preferred_kept(self, job):
        assert main._no_masters_required(
            job(description="A Master's degree is preferred but not required."))

    def test_scrum_master_kept(self, job):
        assert main._no_masters_required(
            job(description="You will work with our Scrum Master on agile delivery."))

    def test_master_data_kept(self, job):
        assert main._no_masters_required(
            job(description="Experience with master data management is a plus."))

    def test_phd_required_dropped(self, job):
        assert not main._no_masters_required(
            job(description="PhD in Machine Learning required."))

    def test_no_mention_kept(self, job):
        assert main._no_masters_required(job(description="Bachelor's degree welcome."))


# ── Cross-source dedup ────────────────────────────────────────────────────────

class TestDedup:
    def test_ats_beats_search(self, job):
        a = job(id="1", source="Greenhouse", company="Acme GmbH", title="Data Scientist (m/w/d)")
        b = job(id="2", source="BraveSearch", company="Acme", title="Data Scientist")
        out = main._dedup_cross_source([b, a])
        assert len(out) == 1 and out[0]["source"] == "Greenhouse"

    def test_legal_form_stripped(self):
        assert main._normalize_company("Acme GmbH") == main._normalize_company("acme")
        assert main._normalize_company("Foo AG") == main._normalize_company("Foo")

    def test_distinct_companies_not_merged(self):
        # Over-aggressive stripping once caused false merges — 'Acme AI' and
        # 'Acme' may be unrelated companies.
        assert main._normalize_company("Acme AI") != main._normalize_company("Acme")

    def test_different_jobs_both_kept(self, job):
        a = job(id="1", title="Data Scientist", company="Acme")
        b = job(id="2", title="Data Engineer", company="Acme")
        assert len(main._dedup_cross_source([a, b])) == 2


# ── A1 diversity quotas ───────────────────────────────────────────────────────

class TestDiversify:
    def test_quota_guarantees_minority_tracks(self, job):
        ai = [job(id=f"ai{i}", _track="AI", score=90 - i) for i in range(30)]
        ds = [job(id=f"ds{i}", _track="DS", score=50 - i) for i in range(6)]
        pool = sorted(ai + ds, key=lambda x: -x["score"])
        picked = main._diversify(pool, 20)
        ds_picked = [j for j in picked if j["_track"] == "DS"]
        assert len(ds_picked) == 6, "DS quota slots must survive an AI score wall"
        assert len(picked) == 20

    def test_sorted_desc(self, job):
        pool = [job(id=str(i), _track="AI", score=s) for i, s in enumerate([50, 90, 70])]
        picked = main._diversify(pool, 3)
        assert [j["score"] for j in picked] == sorted([j["score"] for j in picked], reverse=True)


# ── B6 ghost detection ────────────────────────────────────────────────────────

class TestGhosts:
    def test_old_posting_penalized_and_tagged(self, job):
        from datetime import date, timedelta
        old = (date.today() - timedelta(days=90)).isoformat()
        j = job(posted_at=old, score=60)
        n = main._apply_ghost_penalty([j])
        assert n == 1 and j["ghost"] is True and j["score"] == 60 - main._GHOST_PENALTY

    def test_fresh_posting_untouched(self, job):
        from datetime import date
        j = job(posted_at=date.today().isoformat(), score=60)
        assert main._apply_ghost_penalty([j]) == 0 and "ghost" not in j

    def test_unknown_age_untouched(self, job):
        j = job(posted_at="", score=60)
        assert main._apply_ghost_penalty([j]) == 0
