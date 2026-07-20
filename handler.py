"""
handler.py — AWS entrypoint for the job hunter.

The same image serves both compute options, because the choice between them is
still open pending the scrape-timing measurement:

  * AWS Lambda  — invoked as `handler.lambda_handler`
  * AWS Fargate — the container is run directly, `python handler.py`

Secrets and state are already environment-driven (see secrets_loader.py and
storage.py), so this file only has to translate an invocation into a run and
report the outcome in a form CloudWatch can index.
"""

import json
import os
import sys
import time


def _run(mode: str = "full", dry_run: bool = False) -> dict:
    # Imported inside the function so an import error surfaces as a handled
    # failure with context, rather than killing the Lambda during init where
    # the traceback is far less legible.
    import main
    import storage

    # Mutual exclusion: EventBridge has no equivalent of the GitHub Actions
    # concurrency group, and two overlapping runs would each see the same jobs
    # as unseen (seen_jobs is written only at the end) and both send a digest.
    # Skipping is a success, not a failure — the run that holds the claim is
    # doing the work.
    claim_name = f"run-{mode}"
    if not dry_run and not storage.claim(claim_name):
        result = {"status": "skipped", "mode": mode,
                  "reason": "another run holds the claim"}
        print(json.dumps({"jobhunter_run": result}))
        return result

    started = time.time()
    try:
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
    outcome = _run(dry_run=_dry)
    sys.exit(0 if outcome["status"] == "ok" else 1)
