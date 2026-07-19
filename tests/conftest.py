"""Shared fixtures. All tests run fully offline — no API keys, no network."""
import os
import sys
from pathlib import Path

# Repo root importable regardless of where pytest is invoked from
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# scorer.py builds an Anthropic client at import time
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-dummy")

import pytest


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
