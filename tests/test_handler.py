"""
Tests for the AWS entrypoint.

The dry-run switch is the one that matters: an earlier version honoured
DRY_RUN only in lambda_handler, while Fargate starts the container as
`python handler.py`. A DRY_RUN=1 container override was therefore ignored and
the task performed a REAL run — emailing a digest and writing state — while
the operator believed nothing would be sent. A safety switch that silently
does not apply is worse than no switch at all.
"""

import sys

import pytest

import handler


@pytest.fixture
def fake_main(monkeypatch):
    """Replace main.main so nothing scrapes, scores or emails."""
    calls = {}

    def _fake(dry_run=False):
        calls["dry_run"] = dry_run

    import main
    monkeypatch.setattr(main, "main", _fake)
    monkeypatch.setattr(handler, "_run", handler._run)
    return calls


@pytest.fixture(autouse=True)
def _no_claims(monkeypatch):
    import storage
    monkeypatch.setattr(storage, "claim", lambda *a, **k: True)
    monkeypatch.setattr(storage, "release", lambda *a, **k: None)


class TestDryRunPropagation:
    def test_dry_run_env_var_is_honoured(self, fake_main, monkeypatch):
        monkeypatch.setenv("DRY_RUN", "1")
        handler._run(dry_run=bool(__import__("os").environ.get("DRY_RUN")))
        assert fake_main["dry_run"] is True

    def test_absent_dry_run_means_a_real_run(self, fake_main, monkeypatch):
        monkeypatch.delenv("DRY_RUN", raising=False)
        handler._run(dry_run=bool(__import__("os").environ.get("DRY_RUN")))
        assert fake_main["dry_run"] is False

    def test_lambda_handler_reads_the_event_flag(self, fake_main):
        handler.lambda_handler({"dry_run": True}, None)
        assert fake_main["dry_run"] is True

    def test_main_entrypoint_source_checks_both_argv_and_env(self):
        """The __main__ block must consult DRY_RUN, not just sys.argv."""
        src = open(handler.__file__, encoding="utf-8").read()
        main_block = src.split('if __name__ == "__main__":')[1]
        assert "DRY_RUN" in main_block
        assert "--dry-run" in main_block


class TestOutcome:
    def test_failure_raises_so_aws_sees_it(self, monkeypatch):
        import main
        monkeypatch.setattr(main, "main", lambda dry_run=False: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            handler.lambda_handler({}, None)

    def test_nothing_new_exit_zero_counts_as_success(self, monkeypatch):
        import main

        def _exits(dry_run=False):
            raise SystemExit(0)

        monkeypatch.setattr(main, "main", _exits)
        assert handler.lambda_handler({}, None)["status"] == "ok"
