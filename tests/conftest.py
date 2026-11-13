"""Shared fixtures. All tests run fully offline - no API keys, no network."""
import os
import sys
from pathlib import Path

# Repo root importable regardless of where pytest is invoked from
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# scorer.py builds an Anthropic client at import time
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-dummy")

# Tests use explicitly fictional profiles, never the owner's S3 object or CV.
os.environ["JOBHUNTER_PROFILE_FILE"] = str(Path(__file__).resolve().parent.parent / "scoring_profiles.example.json")


import pytest


@pytest.fixture(autouse=True)
def _no_liveness_requests(monkeypatch):
    """The liveness check does real GETs to employer APIs; no test may do that
    by accident (an ATS-style URL in a fixture would). tests/test_liveness_and_archive.py
    switches it back on and mocks requests."""
    monkeypatch.setenv("JOBHUNTER_LIVENESS", "0")


@pytest.fixture(autouse=True)
def _no_robots_requests(monkeypatch):
    """robots.py fetches each host's robots.txt; no unit test may touch the
    network. tests/test_fix_list_1113.py switches it on with fake files."""
    monkeypatch.setenv("JOBHUNTER_ROBOTS", "0")


@pytest.fixture
def job():
    """Factory for a minimal valid job dict; override any field per test."""
    def _make(**overrides):
        base = {
            "id": "test-id",
            "title": "Junior Data Scientist",
            "company": "Acme GmbH",
            "location": "Berlin, Germany",
            "url": "https://example.com/job",
            "description": "We are looking for a junior data scientist. Python, SQL. English-speaking team.",
            "source": "Greenhouse",
            "posted_at": "",
        }
        base.update(overrides)
        return base
    return _make
