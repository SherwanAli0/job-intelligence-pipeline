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


class StorageUnavailable(RuntimeError):
    """State could not be READ, for a reason other than "it does not exist
    yet". Deliberately not swallowed: see read_text."""


def _is_missing(e: Exception) -> bool:
    """Is this S3 error "no such object" (normal on a first run)?"""
    try:
        code = str(e.response["Error"]["Code"])          # botocore ClientError
    except Exception:
        code = ""
    if code in ("NoSuchKey", "404", "NotFound"):
        return True
    text = f"{type(e).__name__} {e}"
    return "NoSuchKey" in text or "404" in text


def read_text(name: str) -> str | None:
    """File contents, or None if it doesn't exist yet.

    Any OTHER read failure raises StorageUnavailable. This used to return
    None too, and every caller treats None as "empty history": one access
    error or timeout made the pipeline see all ~10,000 scraped jobs as new,
    score a multiple of the usual volume, re-email roles sent weeks ago, and
    then overwrite the run history and the emailed-log with a single line.
    Stopping the run costs one day; carrying on cost the state. Callers for
    whom the data is optional (run history, source registry, answer bank)
    catch the exception themselves.
    """
    if not using_s3():
        p = Path(name)
        return p.read_text(encoding="utf-8") if p.exists() else None
    try:
        obj = _s3().get_object(Bucket=BUCKET, Key=_key(name))
        return obj["Body"].read().decode("utf-8")
    except Exception as e:
        if _is_missing(e):
            return None
        raise StorageUnavailable(f"could not read {name} from {describe()}: {e}") from e


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

# A crashed run must not lock the pipeline forever, but the TTL has to outlast
# a HEALTHY run. It was 90 minutes, which is the batch wait alone: the run of
# 2026-09-07 took 2h03 (40 min scrape + 86 min in Anthropic's batch queue) and
# so outlived its own claim by half an hour, during which a manual start
# would have sent a second digest. Scrape (<= 60 min) + batch (<= 90 min) +
# straggler re-scoring and margin.
_CLAIM_TTL_SECONDS = 4 * 60 * 60

# Claims this PROCESS holds, name -> owner token. release() deletes a claim
# only when the token still matches, so a run that overran its TTL and was
# taken over cannot delete the newer run's claim on its way out.
_OWNED_CLAIMS: dict[str, str] = {}


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
    import uuid
    key = f"{name}.claim"
    owner = uuid.uuid4().hex
    payload = json.dumps({"claimed_at": _now_epoch(), "ttl": ttl_seconds, "owner": owner})

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
        _OWNED_CLAIMS[name] = owner
        return True

    try:
        _s3().put_object(Bucket=BUCKET, Key=_key(key),
                         Body=payload.encode("utf-8"), IfNoneMatch="*")
        _OWNED_CLAIMS[name] = owner
        return True
    except Exception as e:
        if "PreconditionFailed" not in str(e) and "412" not in str(e):
            # Not a lost race — don't let an unrelated S3 error silently
            # block the run. No claim was written, so nothing is owned and
            # release() will leave the object alone. If S3 is genuinely
            # unreachable the run stops at its first state read instead.
            print(f"  [Claim] could not evaluate {name}: {e}")
            return True
        try:
            held_raw = read_text(key)
        except StorageUnavailable as err:
            # The claim exists (412) but cannot be read. It used to be parsed
            # as "{}", i.e. claimed in 1970, and taken over — stealing a claim
            # that could be seconds old. Not knowing is not abandonment.
            print(f"  [Claim] {name} exists but could not be read ({err}) — exiting")
            return False
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
        _OWNED_CLAIMS[name] = owner
        return True


def release(name: str) -> None:
    """Drop a claim so the next scheduled run can start immediately."""
    key = f"{name}.claim"
    owner = _OWNED_CLAIMS.pop(name, None)
    try:
        if not using_s3():
            p = Path(key)
            if p.exists():
                p.unlink()
            return
        if owner is None:
            return                       # this process never wrote the claim
        held_raw = read_text(key)
        if held_raw is None:
            return                       # already gone
        try:
            held_owner = json.loads(held_raw).get("owner")
        except Exception:
            held_owner = None
        if held_owner != owner:
            # This run outlived its TTL and another run took the claim over.
            # Deleting it now would unlock the pipeline under that run.
            print(f"  [Claim] {name} is now held by another run — leaving it in place")
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
