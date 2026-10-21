"""Liveness check, fact gate on drafted answers, and the posting-text archive."""
import json
import sys
import types
from pathlib import Path

import pytest

import application_kit
import liveness
import main
import scrapers

ROOT = Path(__file__).resolve().parent.parent


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


@pytest.fixture(autouse=True)
def _liveness_on(monkeypatch):
    monkeypatch.setenv("JOBHUNTER_LIVENESS", "1")      # these tests mock requests.get


def _job(url, title="Junior Data Scientist", **kw):
    d = {"id": kw.pop("id", url), "title": title, "company": "Acme", "source": "X", "location": "Köln",
         "url": url, "score": 80, "_track": "DS", "description": "Python and SQL."}
    d.update(kw)
    return d


# ── which URL is mapped to which API ─────────────────────────────────────────
class TestProbe:
    def test_greenhouse_both_hosts(self):
        for host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"):
            api, _h = liveness.probe(f"https://{host}/acme/jobs/4567890?gh_src=x")
            assert api == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/4567890"

    def test_lever_and_its_eu_host(self):
        uid = "0a1b2c3d-1111-2222-3333-444455556666"
        assert liveness.probe(f"https://jobs.lever.co/acme/{uid}")[0] == f"https://api.lever.co/v0/postings/acme/{uid}"
        assert liveness.probe(f"https://jobs.eu.lever.co/acme/{uid}/apply")[0] == f"https://api.eu.lever.co/v0/postings/acme/{uid}"

    def test_smartrecruiters(self):
        api, _h = liveness.probe("https://jobs.smartrecruiters.com/BoschGroup/744000152913528-data-scientist")
        assert api == "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings/744000152913528"

    def test_arbeitsagentur_uses_the_key_header(self):
        api, headers = liveness.probe("https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1234567890-S")
        assert api.startswith("https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/")
        assert headers["X-API-Key"] == "jobboerse-jobsuche"

    def test_workday_with_the_site_in_the_url(self):
        api, _h = liveness.probe("https://bmw.wd3.myworkdayjobs.com/en-US/External/job/Munich/Data-Scientist_JR1")
        assert api == "https://bmw.wd3.myworkdayjobs.com/wday/cxs/bmw/External/job/Munich/Data-Scientist_JR1"

    def test_workday_scraper_urls_have_no_site_so_it_comes_from_the_config(self, monkeypatch):
        monkeypatch.setattr(scrapers, "WORKDAY_CXS_TENANTS", [("acme", "wd5", "AcmeCareers")])
        api, _h = liveness.probe("https://acme.wd5.myworkdayjobs.com/job/Berlin/Intern_JR9")
        assert api == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/job/Berlin/Intern_JR9"

    def test_an_ambiguous_or_unknown_workday_tenant_is_never_probed(self, monkeypatch):
        """A wrong site answers 404 for a job that is open: never guess."""
        monkeypatch.setattr(scrapers, "WORKDAY_CXS_TENANTS", [("acme", "wd5", "A"), ("acme", "wd5", "B")])
        assert liveness.probe("https://acme.wd5.myworkdayjobs.com/job/Berlin/Intern_JR9") is None
        monkeypatch.setattr(scrapers, "WORKDAY_CXS_TENANTS", [])
        assert liveness.probe("https://acme.wd5.myworkdayjobs.com/job/Berlin/Intern_JR9") is None

    @pytest.mark.parametrize("url", [
        "https://www.linkedin.com/jobs/view/123", "https://de.indeed.com/viewjob?jk=abc",
        "https://www.xing.com/jobs/berlin-ai-1234", "https://www.adzuna.de/land/ad/123?se=abc",
        "https://careers.example.com/job/1", "", None,
    ])
    def test_aggregators_and_unknown_urls_are_never_probed(self, url):
        assert liveness.probe(url) is None


# ── verdicts ─────────────────────────────────────────────────────────────────
class TestCheck:
    GH = "https://boards.greenhouse.io/acme/jobs/1"

    @pytest.mark.parametrize("status,verdict", [(404, "closed"), (410, "closed"), (200, "open"),
                                                (403, "unknown"), (429, "unknown"), (500, "unknown"), (503, "unknown")])
    def test_only_a_404_or_410_means_closed(self, monkeypatch, status, verdict):
        monkeypatch.setattr(liveness.requests, "get", lambda *a, **k: _Resp(status))
        assert liveness.check(self.GH) == verdict

    def test_a_network_error_means_unknown_not_closed(self, monkeypatch):
        def boom(*a, **k):
            raise liveness.requests.ConnectionError("down")
        monkeypatch.setattr(liveness.requests, "get", boom)
        assert liveness.check(self.GH) == "unknown"


class TestDropClosed:
    def test_closed_jobs_are_removed_the_order_of_the_rest_is_kept(self, monkeypatch):
        gone = "https://boards.greenhouse.io/acme/jobs/2"
        calls = []

        def fake_get(url, **k):
            calls.append(url)
            return _Resp(404 if url.endswith("/jobs/2") else 200)
        monkeypatch.setattr(liveness.requests, "get", fake_get)
        jobs = [_job("https://boards.greenhouse.io/acme/jobs/1", id="a"), _job(gone, id="b"),
                _job("https://www.linkedin.com/jobs/view/9", id="c"),
                _job("https://boards.greenhouse.io/acme/jobs/3", id="d")]
        kept, closed = liveness.drop_closed(jobs)
        assert [j["id"] for j in kept] == ["a", "c", "d"] and [j["id"] for j in closed] == ["b"]
        assert len(calls) == 3, "LinkedIn must not be requested"

    def test_the_same_url_is_asked_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(liveness.requests, "get", lambda url, **k: calls.append(url) or _Resp(200))
        u = "https://boards.greenhouse.io/acme/jobs/1"
        liveness.drop_closed([_job(u, id="a"), _job(u, id="b")])
        assert len(calls) == 1

    def test_nothing_is_requested_for_non_employer_urls(self, monkeypatch):
        monkeypatch.setattr(liveness.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("request")))
        jobs = [_job("https://www.linkedin.com/jobs/view/9"), _job("https://de.indeed.com/viewjob?jk=1")]
        kept, closed = liveness.drop_closed(jobs)
        assert kept == jobs and closed == []

    def test_the_kill_switch(self, monkeypatch):
        monkeypatch.setenv("JOBHUNTER_LIVENESS", "0")
        monkeypatch.setattr(liveness.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("request")))
        jobs = [_job("https://boards.greenhouse.io/acme/jobs/1")]
        assert liveness.drop_closed(jobs) == (jobs, [])

    def test_an_empty_digest_is_fine(self):
        assert liveness.drop_closed([]) == ([], [])


def test_node_rank_drops_a_closed_posting_before_the_email(monkeypatch):
    monkeypatch.setattr(liveness.requests, "get", lambda url, **k: _Resp(404 if url.endswith("/jobs/2") else 200))
    monkeypatch.setattr(application_kit, "enrich_with_kits", lambda top: None)
    scored = [_job("https://boards.greenhouse.io/acme/jobs/1", id="open", score=90),
              _job("https://boards.greenhouse.io/acme/jobs/2", id="gone", score=95)]
    out = main.node_rank({"scored": scored})
    assert [j["id"] for j in out["top"]] == ["open"] and out["closed_dropped"] == 1


def test_a_broken_liveness_module_never_stops_the_digest(monkeypatch):
    monkeypatch.setattr(liveness, "drop_closed", lambda jobs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(application_kit, "enrich_with_kits", lambda top: None)
    out = main.node_rank({"scored": [_job("https://boards.greenhouse.io/acme/jobs/1", id="a", score=80)]})
    assert [j["id"] for j in out["top"]] == ["a"] and out["closed_dropped"] == 0


# ── fact gate ────────────────────────────────────────────────────────────────
FACTS = "Available from October 2026, up to 20 hours per week. B.Sc. grade 1.8. Contact: me@example.com. Gross salary 51,234 EUR."


class TestFactGate:
    @pytest.mark.parametrize("answer", [
        "I can start in October 2026 and work 20 hours per week.",
        "My B.Sc. grade is 1.8.", "You can reach me at me@example.com.", "I expect 51.234 EUR gross.",
        "I enjoy data work.",                      # no number at all
    ])
    def test_grounded_answers_pass(self, answer):
        assert application_kit.ungrounded_claims(answer, FACTS) == []

    def test_a_number_glued_to_a_unit_in_the_facts_still_counts(self):
        assert application_kit.ungrounded_claims("I can work 20 hours a week.", "Hours: up to 20h/week") == []
        assert application_kit.ungrounded_claims("I can work 25 hours a week.", "Hours: up to 20h/week") == ["25"]

    @pytest.mark.parametrize("answer,bad", [
        ("I have 5 years of experience.", "5"), ("I can work 40 hours per week.", "40"),
        ("Write to other@example.org.", "other@example.org"), ("I expect 60,000 EUR.", "60000"),
        ("I graduated in 2019.", "2019"),
    ])
    def test_an_invented_number_or_address_is_caught(self, answer, bad):
        found = application_kit.ungrounded_claims(answer, FACTS)
        assert found and any(bad in f.replace(",", "").replace(".", "") or bad in f for f in found), found

    def test_enrich_banks_only_the_grounded_answers(self, monkeypatch):
        qs = ["How many years of Python experience do you have?", "When can you start?"]
        monkeypatch.setattr(application_kit, "_load_facts", lambda: FACTS)
        monkeypatch.setattr(application_kit, "_greenhouse_questions", lambda url: list(qs))
        banks = {}
        monkeypatch.setattr(application_kit, "load_bank", lambda: {})
        monkeypatch.setattr(application_kit, "save_bank", lambda b: banks.update(b))

        class FakeMessages:
            def create(self, **kw):
                payload = {"answers": [{"index": 0, "answer": "I have 5 years of Python experience."},
                                       {"index": 1, "answer": "I can start in October 2026."}]}
                return types.SimpleNamespace(content=[types.SimpleNamespace(text=json.dumps(payload))])
        fake = types.ModuleType("anthropic")
        fake.Anthropic = lambda api_key=None: types.SimpleNamespace(messages=FakeMessages())
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        job = _job("https://boards.greenhouse.io/acme/jobs/111", id="j1")
        job["_form"] = "student"
        application_kit.enrich_with_kits([job])
        answers = list(banks.values())
        assert answers == ["I can start in October 2026."], banks
        assert [x["a"] for x in job["app_kit"]] == ["I can start in October 2026."]


# ── posting archive ──────────────────────────────────────────────────────────
class TestArchive:
    def _store(self, monkeypatch):
        files = {}
        monkeypatch.setattr(main.storage, "read_text", lambda name: files.get(name))
        monkeypatch.setattr(main.storage, "write_text", lambda name, text: files.__setitem__(name, text))
        return files

    def test_the_text_of_emailed_postings_is_kept(self, monkeypatch):
        files = self._store(monkeypatch)
        j = _job("https://boards.greenhouse.io/acme/jobs/1", id="a", description="x" * 20000)
        j["_form"] = "full-time"
        main._archive_postings([j], [])
        (name, text), = files.items()
        assert name.startswith("postings_") and name.endswith(".jsonl")
        rec = json.loads(text.strip())
        assert rec["id"] == "a" and rec["form"] == "full-time" and len(rec["description"]) == 8000

    def test_a_second_send_on_the_same_day_appends(self, monkeypatch):
        files = self._store(monkeypatch)
        main._archive_postings([_job("https://x/1", id="a")], [])
        main._archive_postings([_job("https://x/2", id="b")], [])
        (text,) = files.values()
        assert [json.loads(l)["id"] for l in text.strip().splitlines()] == ["a", "b"]

    def test_an_unreadable_archive_is_not_overwritten(self, monkeypatch):
        written = []

        def unreadable(name):
            raise main.storage.StorageUnavailable("down")
        monkeypatch.setattr(main.storage, "read_text", unreadable)
        monkeypatch.setattr(main.storage, "write_text", lambda *a: written.append(a))
        main._archive_postings([_job("https://x/1", id="a")], [])      # must not raise
        assert written == []

    def test_nothing_to_archive_writes_nothing(self, monkeypatch):
        files = self._store(monkeypatch)
        main._archive_postings([], [])
        assert files == {}

    def test_persist_archives_after_the_shown_log_and_the_files_are_gitignored(self):
        import inspect
        src = inspect.getsource(main.node_persist)
        assert src.index("_record_shown(") < src.index("_archive_postings(")
        assert "postings_*.jsonl" in (ROOT / ".gitignore").read_text(encoding="utf-8")
