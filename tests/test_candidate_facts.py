"""Candidate facts live only in the private profile; source holds neutral templates.

Offline, fictional data only. The source scan builds its forbidden-word list from a
private file at test time and is skipped when no such file is configured, so no
forbidden word is ever committed.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import scorer
from profile_loader import FACT_NAMES, LoadedProfiles, ProfilesUnavailable, load_profiles

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "scoring_profiles.example.json"
TRACKS = ("AI", "ML", "DS", "DA")


def _profiles():
    return {name: "\n" + name + " profile\n" for name in ("AI", "ML", "DS", "shared")}


def _facts(prefix="F"):
    return {name: "%s-%s" % (prefix, name) for name in sorted(FACT_NAMES)}


def _context():
    return {"intro": "intro text", "work_match": "work match text"}


def _raw(facts="absent", **extra):
    data = {"schema_version": 1, "profiles": _profiles(), "prompt_context": _context(), **extra}
    if facts != "absent":
        data["candidate_facts"] = facts
    return json.dumps(data).encode("utf-8")


class _S3:
    def __init__(self, raw):
        self.raw = raw

    def get_object(self, **kwargs):
        import io
        return {"Body": io.BytesIO(self.raw)}


def _load(raw, fallback=None, strict=""):
    return load_profiles(fallback, environ={"JOBHUNTER_S3_BUCKET": "test",
                                            "JOBHUNTER_REQUIRE_PRIVATE_PROFILES": strict},
                         s3_client=_S3(raw))


# -- the example file is the documented schema --------------------------------
def test_example_profile_defines_every_fact_with_fictional_text():
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert set(data["candidate_facts"]) == FACT_NAMES
    assert all(isinstance(v, str) and v.strip() for v in data["candidate_facts"].values())
    assert data["schema_version"] == 1


# -- loader ---------------------------------------------------------------------
def test_loader_returns_facts_exactly_and_hides_them_from_repr():
    facts = _facts()
    facts["candidate_name"] = "DO_NOT_LOG name"
    loaded = _load(_raw(facts))
    assert loaded.candidate_facts == facts
    assert "DO_NOT_LOG" not in repr(loaded)


def test_loader_ignores_unrelated_extra_top_level_keys():
    assert _load(_raw(_facts(), future_key="x")).candidate_facts == _facts()


@pytest.mark.parametrize("facts", [
    "DO_NOT_LOG",
    ["DO_NOT_LOG"],
    {name: "DO_NOT_LOG" for name in list(FACT_NAMES)[:-1]},          # one key missing
    {**_facts(), "unexpected": "DO_NOT_LOG"},                          # unknown key
    {**_facts(), "german_level": ""},                                  # empty text
    {**_facts(), "german_level": "   "},
    {**_facts(), "german_level": 1},
])
def test_malformed_facts_stop_startup_with_a_sanitized_error(facts):
    with pytest.raises(ProfilesUnavailable) as error:
        _load(_raw(facts), strict="1")
    assert "DO_NOT_LOG" not in str(error.value)
    assert str(error.value) == "Private scoring profiles unavailable from s3"
    assert error.value.__suppress_context__


def test_malformed_facts_do_not_fall_back_silently_in_final_stage():
    with pytest.raises(ProfilesUnavailable):
        _load(_raw({"candidate_name": "DO_NOT_LOG"}), fallback=None)


def _run_config(profile_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("JOBHUNTER_")}
    env["JOBHUNTER_PROFILE_FILE"] = str(profile_path)
    code = "import sys; sys.path.insert(0, %r); import config" % str(ROOT)
    return subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          env=env, cwd=str(ROOT), timeout=60)


def test_config_requires_the_facts_and_names_no_private_text(tmp_path):
    path = tmp_path / "without-facts.json"
    path.write_bytes(_raw(facts="absent"))
    result = _run_config(path)
    assert result.returncode != 0
    assert "ProfilesUnavailable: Private candidate facts are required" in result.stderr
    assert "profile\n" not in result.stderr


def test_config_accepts_a_complete_object(tmp_path):
    path = tmp_path / "complete.json"
    path.write_bytes(_raw(_facts()))
    result = _run_config(path)
    assert result.returncode == 0, result.stderr[-300:]


def test_there_is_no_embedded_fallback_for_facts():
    import config
    assert config.CANDIDATE_FACTS == json.loads(EXAMPLE.read_text(encoding="utf-8"))["candidate_facts"]
    with pytest.raises(ProfilesUnavailable):
        load_profiles(environ={})


# -- rendering --------------------------------------------------------------------
@pytest.fixture
def tokens(monkeypatch):
    facts = _facts("TOK")
    monkeypatch.setattr(scorer, "CANDIDATE_FACTS", facts)
    return facts


def test_prompt_substitutes_every_fact_in_place(tokens):
    prompt = scorer._system_prompt("profile text")
    t = tokens
    assert "The candidate is %s. Score 0 to 15" % t["work_scope"] in prompt
    assert "the candidate studies in %s. His rule" % t["study_city"] in prompt
    assert '"German native". %s is %s.' % (t["candidate_name"], t["german_level"]) in prompt
    assert "experience. %s has %s." % (t["candidate_name"], t["professional_experience"]) in prompt
    assert "Master's or PhD. %s %s " % (t["candidate_name"], t["degree_status"]) in prompt
    assert "a domain %s has %s to AND requires" % (t["candidate_name"], t["domain_exposure"]) in prompt
    assert "\n    %s\n" % t["ml_match_note"] in prompt
    assert "\n    %s\n" % t["ds_match_note"] in prompt
    assert "\n    %s\n" % t["da_match_note"] in prompt
    assert "(%s has %s)" % (t["candidate_name"], t["work_auth_short"]) in prompt
    assert '(e.g. "%s")' % t["cv_hint_example"] in prompt
    assert "so %s can audit your decisions" % t["candidate_name"] in prompt
    assert "{F[" not in prompt and "CANDIDATE_FACTS" not in prompt


def test_prompt_is_unchanged_apart_from_the_substituted_facts(tokens):
    with_tokens = scorer._system_prompt("p")
    other = {name: "OTHER-" + name for name in tokens}
    scorer.CANDIDATE_FACTS = other  # monkeypatch restores the original afterwards
    other_render = scorer._system_prompt("p")
    for name in tokens:
        with_tokens = with_tokens.replace(tokens[name], "@")
        other_render = other_render.replace(other[name], "@")
    assert with_tokens == other_render


def test_every_track_prompt_uses_its_own_profile_and_the_shared_facts(tokens):
    for track in TRACKS:
        prompt = scorer._system_prompt(scorer._TRACK_PROFILES[track])
        assert scorer._TRACK_PROFILES[track] in prompt
        assert tokens["study_city"] in prompt


def test_disqualify_reasons_are_rendered_from_facts(tokens):
    base = {"id": "1", "title": "Data Analyst", "company": "X", "location": "Berlin, Germany"}
    german = scorer._hard_disqualify({**base, "description": "We need fluent German (C1). Python."})
    assert german[0] and german[1].endswith("candidate is " + tokens["german_level"])
    masters = scorer._hard_disqualify({**base, "description": "Master degree is required. Python."})
    assert masters[0] and masters[1].endswith("candidate has " + tokens["completed_degree"])


# -- the public tree carries no candidate-specific text ----------------------------
SCANNED_CODE = ("scorer.py", "main.py", "filters.py", "config.py", "profile_loader.py",
                "application_kit.py", "aws/private-profiles.md")
SCANNED_DOCS = ("README.md", "aws/README.md", "aws/private-profiles.md", "scoring_profiles.example.json")


def _private_scan_profile():
    candidates = [os.environ.get("JOBHUNTER_PRIVATE_PROFILE_FOR_SCAN", ""),
                  str(ROOT / "personal" / "scoring-profiles.json")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return json.loads(Path(candidate).read_text(encoding="utf-8"))
    return None


def _extra_terms():
    """Optional private word list: JOBHUNTER_FORBIDDEN_TERMS_FILE or personal/forbidden-terms.txt."""
    for candidate in (os.environ.get("JOBHUNTER_FORBIDDEN_TERMS_FILE", ""),
                      str(ROOT / "personal" / "forbidden-terms.txt")):
        if candidate and Path(candidate).is_file():
            lines = Path(candidate).read_text(encoding="utf-8").splitlines()
            return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    return []


def _find_problems(files, facts, extra):
    """Return 'file: kind #index' strings; the matched text is never included."""
    fragments = [line.strip() for value in facts.values() for line in value.splitlines()
                 if len(line.strip()) >= 15]
    name = facts["candidate_name"]
    problems = []
    for rel, text in sorted(files.items()):
        if rel in SCANNED_CODE and name in text:
            problems.append("%s: candidate name" % rel)
        for index, fragment in enumerate(fragments):
            if fragment in text:
                problems.append("%s: private fact fragment #%d" % (rel, index))
        for index, term in enumerate(extra):
            if term in text:
                problems.append("%s: forbidden term #%d" % (rel, index))
    return problems


def test_source_and_docs_contain_no_candidate_specific_text():
    private = _private_scan_profile()
    if not private or "candidate_facts" not in private:
        pytest.skip("no private profile configured for the source scan")
    files = {rel: (ROOT / rel).read_text(encoding="utf-8")
             for rel in sorted(set(SCANNED_CODE + SCANNED_DOCS))}
    problems = _find_problems(files, private["candidate_facts"], _extra_terms())
    assert not problems, problems  # indexes only; the terms themselves are never printed


def test_scanner_reports_planted_fictional_facts_without_echoing_them():
    facts = {"candidate_name": "Zed Fictional",
             "ml_match_note": "(Matches a fictional record-linkage project line one,\n    line two of it.)"}
    files = {"scorer.py": "x = 1  # Zed Fictional wrote this\n",
             "main.py": "# (Matches a fictional record-linkage project line one,\n",
             "README.md": "Zed Fictional appears in docs but the name is only checked in code\n",
             "filters.py": "# a Fictionalville mention\n",
             "config.py": "clean = True\n"}
    problems = _find_problems(files, facts, ["Fictionalville"])
    assert problems == ["filters.py: forbidden term #0",
                        "main.py: private fact fragment #0",
                        "scorer.py: candidate name"]
    assert not any("Zed" in p or "Fictionalville" in p for p in problems)
    assert _find_problems({"scorer.py": "clean\n"}, facts, ["Fictionalville"]) == []
