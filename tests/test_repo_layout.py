"""Where things live in the repository (16 November 2026).

The code moved from the top folder into src/, the two committed state files into
data/, the example profile into examples/ and run-now.ps1 into aws/. No file and no
module was renamed. These tests keep the layout and every reference to it in step:
a wrong path in the Dockerfile, a workflow, the task definition or the README shows
up here and not on AWS.
"""
import json
import os
import re
from pathlib import Path

import pytest

import compare_sources
import eval_retrieval
import main
import storage

ROOT = Path(__file__).resolve().parent.parent
MODULES = set("""scrapers main scorer evidence config filters notifier track application_kit storage
                 robots compare_sources robots_audit health_check liveness calibrate eval_retrieval
                 graph profile_loader handler secrets_loader metrics""".split())
DOCS = ("README.md", "aws/README.md", "aws/private-profiles.md")
_COMMAND = re.compile(r"(?:python3?|py(?: -3\.11)?) (?:%s)\.py\b" % "|".join(sorted(MODULES)))


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# -- the layout itself ---------------------------------------------------------------
def test_the_code_is_in_src_and_not_in_the_top_folder():
    assert sorted(p.name for p in ROOT.glob("*.py")) == []
    assert MODULES <= {p.stem for p in (ROOT / "src").glob("*.py")}


def test_data_example_profile_and_helper_script_are_in_their_folders():
    moved = {"seen_jobs.json": "data", "run_stats.jsonl": "data",
             "scoring_profiles.example.json": "examples", "run-now.ps1": "aws"}
    for name, folder in moved.items():
        assert (ROOT / folder / name).is_file(), name
        assert not (ROOT / name).exists(), name


def test_the_tests_run_on_the_fictional_example_profile():
    assert Path(os.environ["JOBHUNTER_PROFILE_FILE"]).is_file()
    assert Path(main.__file__).resolve().parent == ROOT / "src"


# -- the container image ---------------------------------------------------------------
def test_the_image_takes_its_code_from_src():
    text = _read("Dockerfile")
    assert "COPY src/ ${LAMBDA_TASK_ROOT}/\n" in text.replace("\r\n", "\n")
    assert "COPY golden/ ${LAMBDA_TASK_ROOT}/golden/" in text
    assert "COPY *.py" not in text


def test_the_task_definition_starts_a_file_the_image_has():
    """src/ is copied into the task root, so `python handler.py` there is src/handler.py here."""
    container = json.loads(_read("aws/task-definition.json"))["containerDefinitions"][0]
    assert container["entryPoint"] == ["python"]
    assert container["command"] == ["handler.py"]
    assert (ROOT / "src" / container["command"][0]).is_file()
    assert 'CMD [ "handler.lambda_handler" ]' in _read("Dockerfile")


# -- workflows ---------------------------------------------------------------------------
def test_workflow_commands_name_existing_files():
    named = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            named += [(path.name, p) for p in re.findall(r"\bpython ([\w./-]+\.py)\b", line)]
            added = re.search(r"\bgit add (.+)$", line)
            if added:
                named += [(path.name, p) for p in added.group(1).split()]
            profile = re.match(r"\s*JOBHUNTER_PROFILE_FILE:\s*([\w./-]+)\s*$", line)
            if profile:
                named.append((path.name, profile.group(1)))
    assert len(named) >= 6, named        # calibrate 1, health 2, daily 3
    assert [(name, p) for name, p in named if not (ROOT / p).is_file()] == []


def test_a_change_to_the_state_snapshot_does_not_deploy():
    text = _read(".github/workflows/deploy.yml")
    assert '- "data/**"' in text
    assert '"seen_jobs.json"' not in text and '"run_stats.jsonl"' not in text


# -- documents ---------------------------------------------------------------------------
@pytest.mark.parametrize("doc", DOCS)
def test_relative_links_in_the_documents_point_at_existing_files(doc):
    base = (ROOT / doc).parent
    targets = [t for t in re.findall(r"\]\(([^)\s]+)\)", _read(doc))
               if not re.match(r"[a-z][a-z0-9+.-]*:", t) and not t.startswith("#")]
    if doc == "README.md":
        assert len(targets) >= 30, len(targets)      # the repo tour alone links 17 code files
    assert sorted({t for t in targets if not (base / t.split("#")[0]).exists()}) == []


def test_commands_in_code_and_documents_name_src():
    """`python main.py` no longer exists in the repository. handler.py is left out: its
    text describes the command inside the image, where the code sits in the task root."""
    stale = []
    for path in [*sorted((ROOT / "src").glob("*.py")), *(ROOT / d for d in DOCS)]:
        if path.name == "handler.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _COMMAND.search(line) and "python handler.py" not in line:
                stale.append("%s:%d" % (path.name, number))
    assert stale == []


def test_the_helper_script_finds_the_private_folder_from_aws():
    text = _read("aws/run-now.ps1")
    assert "(Join-Path (Split-Path -Parent $PSScriptRoot) 'personal\\aws-deployment.json')" in text
    assert "(Join-Path $PSScriptRoot 'personal" not in text


# -- state files and golden data ---------------------------------------------------------
def test_the_committed_state_files_are_the_ones_the_code_names():
    assert main.SEEN_FILE == Path("data") / "seen_jobs.json"
    assert main.STATS_FILE == Path("data") / "run_stats.jsonl"
    assert compare_sources.STATS_FILE == main.STATS_FILE
    assert (ROOT / main.SEEN_FILE).is_file() and (ROOT / main.STATS_FILE).is_file()


def test_the_move_does_not_change_the_s3_keys(monkeypatch):
    """Production state lives in S3 under the file name alone. A new local folder must
    not reach the bucket: a new key would read as an empty history and re-send old jobs."""
    monkeypatch.setattr(storage, "PREFIX", "state")
    assert storage._key(str(main.SEEN_FILE)) == "state/seen_jobs.json"
    assert storage._key(str(main.STATS_FILE)) == "state/run_stats.jsonl"
    assert storage._key("data/seen_jobs.json") == "state/seen_jobs.json"


def test_a_local_write_creates_its_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "BUCKET", "")
    monkeypatch.chdir(tmp_path)
    storage.write_text(str(Path("data") / "seen_jobs.json"), "{}")
    storage.append_line(str(Path("data") / "run_stats.jsonl"), '{"n": 1}')
    assert (tmp_path / "data" / "seen_jobs.json").read_text(encoding="utf-8") == "{}"
    assert (tmp_path / "data" / "run_stats.jsonl").read_text(encoding="utf-8") == '{"n": 1}\n'
    assert storage.read_text(str(Path("data") / "seen_jobs.json")) == "{}"


def test_golden_data_is_found_from_src():
    assert eval_retrieval.GOLDEN == ROOT / "golden"
    assert (eval_retrieval.GOLDEN / "evidence_eval_heldout.jsonl").is_file()
    assert (ROOT / "golden" / "golden_set.jsonl").is_file()
