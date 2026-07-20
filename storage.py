"""
storage.py — where the pipeline's state files live.

On your laptop (and on GitHub Actions) state is just files in the repo folder,
exactly as before. On AWS Lambda the filesystem is wiped between runs, so the
same files live in S3 instead.

Which one is used depends solely on the JOBHUNTER_S3_BUCKET environment
variable: set it and state goes to S3, leave it unset and nothing changes.
That keeps local runs, the test suite, and the GitHub Actions fallback working
untouched while the migration is in progress.

boto3 is imported lazily so nothing outside AWS needs it installed.
"""

import json
import os
from pathlib import Path

BUCKET = os.environ.get("JOBHUNTER_S3_BUCKET", "").strip()
PREFIX = os.environ.get("JOBHUNTER_S3_PREFIX", "state").strip().strip("/")

_client = None


def using_s3() -> bool:
    return bool(BUCKET)


def describe() -> str:
    return f"s3://{BUCKET}/{PREFIX}/" if using_s3() else "local files"


def _s3():
    global _client
    if _client is None:
        import boto3  # imported here so local runs don't need it installed
        _client = boto3.client("s3")
    return _client


def _key(name: str) -> str:
    """S3 key for a state file. Only the basename is used, so callers can pass
    a full local path (which local mode honours exactly, including the paths
    tests point at temp directories) without it leaking into the bucket."""
    base = os.path.basename(str(name))
    return f"{PREFIX}/{base}" if PREFIX else base


def read_text(name: str) -> str | None:
    """File contents, or None if it doesn't exist yet."""
    if not using_s3():
        p = Path(name)
        return p.read_text(encoding="utf-8") if p.exists() else None
    try:
        obj = _s3().get_object(Bucket=BUCKET, Key=_key(name))
        return obj["Body"].read().decode("utf-8")
    except Exception as e:
        # A missing object is normal on a first run; anything else is worth
        # seeing in the logs rather than silently starting from empty state.
        if type(e).__name__ not in ("NoSuchKey", "ClientError"):
            print(f"  [Storage] read {name} failed: {e}")
        elif "NoSuchKey" not in str(e) and "404" not in str(e):
            print(f"  [Storage] read {name} failed: {e}")
        return None


def write_text(name: str, text: str) -> None:
    if not using_s3():
        Path(name).write_text(text, encoding="utf-8")
        return
    _s3().put_object(
        Bucket=BUCKET, Key=_key(name),
        Body=text.encode("utf-8"),
        ContentType="application/json",
    )


def append_line(name: str, line: str) -> None:
    """Append one line. S3 objects can't be appended to, so this is a
    read-modify-write; fine for a file that grows by one line per run."""
    if not using_s3():
        with Path(name).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return
    existing = read_text(name) or ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    write_text(name, existing + line + "\n")


# ── Run claims (mutual exclusion) ────────────────────────────────────────────
# GitHub Actions prevented overlapping runs with a `concurrency` group after a
# real collision. EventBridge has no equivalent, so the guard has to live in
# the pipeline: two tasks that start together would both see the same jobs as
# unseen (seen_jobs is only written at the END of a run) and both send a
# digest.
#
# The same primitive covers the check-and-exit consumer stage if the pipeline
# is later split: the tick that finds a finished batch may take minutes to
# score and email, and the next tick must not pick up the same batch and
# double-send.
#
# S3 conditional writes (If-None-Match: *) make the claim atomic: exactly one
# concurrent caller can create the object, everyone else gets 412.

_CLAIM_TTL_SECONDS = 90 * 60   # a crashed run must not lock the pipeline forever


def _now_epoch() -> float:
    import time
    return time.time()


def claim(name: str, ttl_seconds: int = _CLAIM_TTL_SECONDS) -> bool:
    """
    Try to acquire the named claim. True = this process owns it and may
    proceed; False = someone else holds it and this process should exit.

    A claim older than ttl_seconds is treated as abandoned (the holder crashed)
    and taken over, so a failed run cannot wedge the pipeline permanently.
    """
    key = f"{name}.claim"
    payload = json.dumps({"claimed_at": _now_epoch(), "ttl": ttl_seconds})

    if not using_s3():
        # Local runs are single-process; keep the same interface without
        # pretending a file gives real mutual exclusion.
        p = Path(key)
        if p.exists():
            try:
                held = json.loads(p.read_text(encoding="utf-8"))
                if _now_epoch() - float(held.get("claimed_at", 0)) < ttl_seconds:
                    return False
            except Exception:
                pass
        p.write_text(payload, encoding="utf-8")
        return True

    try:
        _s3().put_object(Bucket=BUCKET, Key=_key(key),
                         Body=payload.encode("utf-8"), IfNoneMatch="*")
        return True
    except Exception as e:
        if "PreconditionFailed" not in str(e) and "412" not in str(e):
            # Not a lost race — don't let an unrelated S3 error silently
            # block the run.
            print(f"  [Claim] could not evaluate {name}: {e}")
            return True
        held_raw = read_text(key)
        try:
            held = json.loads(held_raw or "{}")
            age = _now_epoch() - float(held.get("claimed_at", 0))
        except Exception:
            age = ttl_seconds + 1        # unparseable claim = abandoned
        if age < ttl_seconds:
            print(f"  [Claim] {name} held by another run ({int(age)}s ago) — exiting")
            return False
        print(f"  [Claim] taking over abandoned {name} claim ({int(age)}s old)")
        write_text(key, payload)
        return True


def release(name: str) -> None:
    """Drop a claim so the next scheduled run can start immediately."""
    key = f"{name}.claim"
    try:
        if not using_s3():
            p = Path(key)
            if p.exists():
                p.unlink()
            return
        _s3().delete_object(Bucket=BUCKET, Key=_key(key))
    except Exception as e:
        print(f"  [Claim] could not release {name}: {e}")


def exists(name: str) -> bool:
    if not using_s3():
        return Path(name).exists()
    try:
        _s3().head_object(Bucket=BUCKET, Key=_key(name))
        return True
    except Exception:
        return False
