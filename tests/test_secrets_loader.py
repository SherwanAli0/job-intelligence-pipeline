"""
Tests for the SSM secrets loader.

Two properties matter. First, it must be completely inert when
JOBHUNTER_SSM_PREFIX is unset, otherwise a laptop or GitHub Actions run could
start behaving differently mid-migration. Second, a value already in the
environment must never be overwritten by a stored one, so a local override or
a CI secret always wins over a stale copy in Parameter Store.
"""

import pytest

import secrets_loader


class _FakeSSM:
    def __init__(self, params):
        self._params = params

    def get_paginator(self, _name):
        params = self._params

        class _P:
            def paginate(self, **kw):
                yield {"Parameters": [{"Name": k, "Value": v} for k, v in params.items()]}

        return _P()


@pytest.fixture
def fake_ssm(monkeypatch):
    def _install(params):
        fake = _FakeSSM(params)
        fake_boto3 = type("boto3", (), {"client": staticmethod(lambda svc: fake)})
        monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)
        return fake
    return _install


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GMAIL_APP_PASSWORD", "BRAVE_API_KEY",
              "JOBHUNTER_SSM_PREFIX"):
        monkeypatch.delenv(k, raising=False)


class TestDisabledByDefault:
    def test_no_prefix_means_no_op(self, monkeypatch):
        # Would explode if it tried to reach AWS
        monkeypatch.setitem(__import__("sys").modules, "boto3", None)
        assert secrets_loader.load() == 0

    def test_empty_prefix_is_no_op(self):
        assert secrets_loader.load(prefix="   ") == 0


class TestLoading:
    def test_loads_allowed_parameters(self, fake_ssm, monkeypatch):
        fake_ssm({"/job-hunter/ANTHROPIC_API_KEY": "sk-from-ssm",
                  "/job-hunter/GMAIL_APP_PASSWORD": "app-pass"})
        n = secrets_loader.load(prefix="/job-hunter", quiet=True)
        import os
        assert n == 2
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-ssm"
        assert os.environ["GMAIL_APP_PASSWORD"] == "app-pass"

    def test_existing_environment_value_wins(self, fake_ssm, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-local-override")
        fake_ssm({"/job-hunter/ANTHROPIC_API_KEY": "sk-from-ssm"})
        secrets_loader.load(prefix="/job-hunter", quiet=True)
        import os
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-local-override"

    def test_unexpected_parameter_names_are_ignored(self, fake_ssm):
        fake_ssm({"/job-hunter/PATH": "/evil", "/job-hunter/AWS_SECRET_ACCESS_KEY": "x",
                  "/job-hunter/BRAVE_API_KEY": "brave-key"})
        import os
        before_path = os.environ.get("PATH")
        secrets_loader.load(prefix="/job-hunter", quiet=True)
        assert os.environ.get("PATH") == before_path
        assert os.environ.get("AWS_SECRET_ACCESS_KEY") != "x"
        assert os.environ["BRAVE_API_KEY"] == "brave-key"

    def test_trailing_slash_in_prefix_is_tolerated(self, fake_ssm):
        fake_ssm({"/job-hunter/BRAVE_API_KEY": "k"})
        assert secrets_loader.load(prefix="/job-hunter/", quiet=True) == 1


class TestFailureIsSoft:
    def test_aws_error_does_not_raise(self, monkeypatch):
        class _Boom:
            @staticmethod
            def client(_svc):
                raise RuntimeError("no credentials")

        monkeypatch.setitem(__import__("sys").modules, "boto3", _Boom)
        assert secrets_loader.load(prefix="/job-hunter", quiet=True) == 0


class TestScorerClientIsLazy:
    def test_importing_scorer_without_key_does_not_raise(self, monkeypatch):
        """The whole reason the client is lazy: on Lambda the key arrives after
        imports, and an import-time client would crash the init phase."""
        import importlib
        import os
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import scorer
        importlib.reload(scorer)          # must not raise
        assert scorer.client is None
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            scorer._client()
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-dummy"
        importlib.reload(scorer)
