"""
handler.py - AWS entrypoint for the job hunter.

The same image serves both compute options, because the choice between them is
still open pending the scrape-timing measurement:

  * AWS Lambda  - invoked as `handler.lambda_handler`
  * AWS Fargate - the container is run directly, `python handler.py`

Secrets and state are already environment-driven (see secrets_loader.py and
storage.py), so this file only has to translate an invocation into a run and
report the outcome in a form CloudWatch can index.
"""

import json
import os
import sys
import time


# "full": the pipeline with its email. "linkedin": LinkedIn read on its own
# between the email runs (main.read_linkedin_only), set by the schedules
# job-hunter-linkedin-* as JOBHUNTER_MODE=linkedin.
MODES = ("full", "linkedin")
# Both read LinkedIn, and two reads at once ask LinkedIn twice as fast, which
# is what it refuses. A LinkedIn-only read skips itself while an email run
# holds its claim; an email run waits up to _WAIT_FOR_LINKEDIN_SECONDS for a
# LinkedIn-only read to finish (its lane has 40 minutes). A claim older than
# what such a run can take is left over from a crash and counts for nothing.
_LINKEDIN_READ_MAX_SECONDS = 90 * 60
_WAIT_FOR_LINKEDIN_SECONDS = 20 * 60
_WAIT_STEP_SECONDS = 30


def _claim_age(storage, name: str):
    """Seconds since the claim `name` was taken, or None (no claim, or unreadable)."""
    try:
        raw = storage.read_text(f"{name}.claim")
        if not raw:
            return None
        return max(0.0, time.time() - float(json.loads(raw).get("claimed_at", 0)))
    except Exception:
        return None


def _wait_for_linkedin_read(storage) -> None:
    waited = 0
    while waited < _WAIT_FOR_LINKEDIN_SECONDS:
        age = _claim_age(storage, "run-linkedin")
        if age is None or age >= _LINKEDIN_READ_MAX_SECONDS:
            return
        if not waited:
            print("A LinkedIn-only read is running: waiting for it to finish (up to "
                  f"{_WAIT_FOR_LINKEDIN_SECONDS // 60} minutes)")
        time.sleep(_WAIT_STEP_SECONDS)
        waited += _WAIT_STEP_SECONDS
    print("The LinkedIn-only read is still running: this run goes on anyway")


def _run(mode: str = "full", dry_run: bool = False) -> dict:
    # Imported inside the function so an import error surfaces as a handled
    # failure with context, rather than killing the Lambda during init where
    # the traceback is far less legible.
    import main
    import storage

    if mode not in MODES:
        # A mistyped mode must not start a run with an email nobody expects.
        result = {"status": "error", "mode": mode, "error": f"unknown mode {mode!r}"}
        print(json.dumps({"jobhunter_run": result}))
        return result

    if mode == "linkedin" and not dry_run:
        age = _claim_age(storage, "run-full")
        if age is not None and age < getattr(storage, "_CLAIM_TTL_SECONDS", 4 * 3600):
            result = {"status": "skipped", "mode": mode, "reason": "an email run is reading now"}
            print(json.dumps({"jobhunter_run": result}))
            return result
    if mode == "full" and not dry_run:
        _wait_for_linkedin_read(storage)

    # Mutual exclusion: EventBridge has no equivalent of the GitHub Actions
    # concurrency group, and two overlapping runs would each see the same jobs
    # as unseen (seen_jobs is written only at the end) and both send a digest.
    # Skipping is a success, not a failure - the run that holds the claim is
    # doing the work.
    claim_name = f"run-{mode}"
    if not dry_run and not storage.claim(claim_name):
        result = {"status": "skipped", "mode": mode,
                  "reason": "another run holds the claim"}
        print(json.dumps({"jobhunter_run": result}))
        return result

    started = time.time()
    try:
        if mode == "linkedin":
            main.read_linkedin_only(dry_run=dry_run)
        else:
            main.main(dry_run=dry_run)
        status, error = "ok", None
    except SystemExit as e:
        # main() calls sys.exit(0) on the "nothing new today" path.
        status, error = ("ok" if not e.code else "error"), (None if not e.code else str(e.code))
    except Exception as e:
        status, error = "error", f"{type(e).__name__}: {e}"
    finally:
        # Always release, including on failure: a crashed run must not lock the
        # pipeline until the TTL expires.
        if not dry_run:
            storage.release(claim_name)

    result = {
        "status": status,
        "mode": mode,
        "dry_run": dry_run,
        "duration_seconds": round(time.time() - started, 1),
    }
    if error:
        result["error"] = error
    # One JSON line per run: CloudWatch Logs Insights can query these fields
    # directly, which plain print() output does not allow.
    print(json.dumps({"jobhunter_run": result}))
    return result


def lambda_handler(event, context):
    event = event or {}
    result = _run(
        mode=event.get("mode", "full"),
        dry_run=bool(event.get("dry_run", os.environ.get("DRY_RUN"))),
    )
    if result["status"] != "ok":
        # Raising marks the invocation as failed so EventBridge retries and
        # CloudWatch alarms actually fire; returning a 500-ish dict would look
        # like success to every AWS-side signal.
        raise RuntimeError(result.get("error", "run failed"))
    return result


if __name__ == "__main__":
    # DRY_RUN must be honoured here too, not only in lambda_handler. On Fargate
    # the container is started as `python handler.py`, so an earlier version
    # that only inspected sys.argv silently ignored a DRY_RUN=1 container
    # override and performed a REAL run: it emailed a digest and wrote state
    # while the operator believed nothing would be sent. A dry-run switch that
    # quietly does not apply is worse than having none.
    _dry = "--dry-run" in sys.argv or bool(os.environ.get("DRY_RUN"))

    # A stopped task must give its claim back. `aws ecs stop-task` sends
    # SIGTERM, and Python's default action ends the process without running
    # the `finally` in _run: on 2026-11-11 a manual stop left run-full.claim
    # behind and the next run skipped itself ("another run holds the claim")
    # until the claim was deleted by hand; unfixed it blocks for the 4h TTL.
    # Raising SystemExit from the handler unwinds through that `finally`.
    def _on_sigterm(signum, frame):
        print("SIGTERM received - releasing the run claim and stopping")
        raise SystemExit(143)

    try:
        import signal
        signal.signal(signal.SIGTERM, _on_sigterm)
    except Exception:
        pass
    outcome = _run(mode=os.environ.get("JOBHUNTER_MODE", "").strip().lower() or "full", dry_run=_dry)
    # "skipped" means the overlap guard correctly declined to start a second
    # concurrent hunt (a manual click while the scheduled run was mid-flight).
    # That is the system working, not a failure - exiting 1 for it made GitHub
    # send a red failure email for correct behaviour, which teaches the owner
    # to ignore red emails. Red must mean genuinely broken.
    sys.exit(0 if outcome["status"] in ("ok", "skipped") else 1)
