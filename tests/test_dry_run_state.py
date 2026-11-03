"""A dry run must not write the screening-answer bank (answers.json)."""
import json
import sys
import types

import application_kit
import main

FACTS = "Available from October 2026, up to 20 hours per week."


def _gh_job(**kw):
    j = {"id": "g1", "title": "Working Student Machine Learning", "company": "Acme GmbH",
         "location": "Bonn, Germany", "url": "https://boards.greenhouse.io/acme/jobs/111",
         "source": "Greenhouse", "description": "Python", "_form": "student", "_track": "ML",
         "score": 80, "reason": "r"}
    j.update(kw)
    return j


def _fake_anthropic(monkeypatch, answers):
    class FakeMessages:
        def create(self, **kw):
            payload = {"answers": [{"index": i, "answer": a} for i, a in enumerate(answers)]}
            return types.SimpleNamespace(content=[types.SimpleNamespace(text=json.dumps(payload))])
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda api_key=None: types.SimpleNamespace(messages=FakeMessages())
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


def _kit_env(monkeypatch):
    saved = []
    monkeypatch.setattr(application_kit, "_load_facts", lambda: FACTS)
    monkeypatch.setattr(application_kit, "_greenhouse_questions", lambda url: ["When can you start?"])
    monkeypatch.setattr(application_kit, "load_bank", lambda: {})
    monkeypatch.setattr(application_kit, "save_bank", lambda b: saved.append(dict(b)))
    _fake_anthropic(monkeypatch, ["I can start in October 2026."])
    return saved


class TestEnrichWithKits:
    def test_a_normal_run_saves_new_answers(self, monkeypatch):
        saved = _kit_env(monkeypatch)
        job = _gh_job()
        application_kit.enrich_with_kits([job])
        assert len(saved) == 1 and list(saved[0].values()) == ["I can start in October 2026."]

    def test_persist_false_attaches_the_kit_but_never_saves(self, monkeypatch):
        saved = _kit_env(monkeypatch)
        job = _gh_job()
        application_kit.enrich_with_kits([job], persist=False)
        assert saved == []
        assert [x["a"] for x in job["app_kit"]] == ["I can start in October 2026."]


class TestNodeRankPassesTheDryRunFlag:
    def _rank(self, monkeypatch, dry_run):
        calls = []
        monkeypatch.setattr(application_kit, "enrich_with_kits",
                            lambda top, **kw: calls.append(kw))
        state = {"scored": [_gh_job()]}
        if dry_run is not None:
            state["dry_run"] = dry_run
        main.node_rank(state)
        return calls

    def test_dry_run_disables_persistence(self, monkeypatch):
        assert self._rank(monkeypatch, True) == [{"persist": False}]

    def test_real_run_keeps_the_default(self, monkeypatch):
        assert self._rank(monkeypatch, False) == [{}]
        assert self._rank(monkeypatch, None) == [{}]


def test_dry_run_through_node_rank_writes_no_answer_bank(monkeypatch, tmp_path):
    """End to end through the real storage layer: no answers.json appears."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(application_kit, "_load_facts", lambda: FACTS)
    monkeypatch.setattr(application_kit, "_greenhouse_questions", lambda url: ["When can you start?"])
    _fake_anthropic(monkeypatch, ["I can start in October 2026."])
    main.node_rank({"scored": [_gh_job()], "dry_run": True})
    assert not (tmp_path / "answers.json").exists()
    main.node_rank({"scored": [_gh_job()], "dry_run": False})
    assert (tmp_path / "answers.json").exists(), "control: a real run does write the bank"
