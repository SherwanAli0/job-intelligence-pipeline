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


def exists(name: str) -> bool:
    if not using_s3():
        return Path(name).exists()
    try:
        _s3().head_object(Bucket=BUCKET, Key=_key(name))
        return True
    except Exception:
        return False
