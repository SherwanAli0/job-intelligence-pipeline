"""
secrets_loader.py — pull secrets from AWS SSM Parameter Store into the process.

The pipeline reads its credentials from environment variables (ANTHROPIC_API_KEY,
GMAIL_APP_PASSWORD, ...). On GitHub Actions those come from repo secrets; on
Lambda there is no such mechanism, and baking them into the container image or
the function's plain environment variables would put them in places that get
copied around. Instead they live in Parameter Store as encrypted SecureStrings
and are loaded into os.environ once, at process start.

Enabled by setting JOBHUNTER_SSM_PREFIX (e.g. "/job-hunter"). When it is unset
this module does nothing at all, so laptop runs and GitHub Actions are
unaffected.

Values already present in the environment WIN over Parameter Store, so a local
override or a CI secret is never silently replaced by a stale stored copy.
"""

import os

# Only these names are ever imported. An explicit list means a typo'd or
# unexpected parameter in the path can't inject arbitrary environment
# variables into the process.
_ALLOWED = (
    "ANTHROPIC_API_KEY",
    "GMAIL_USER",
    "GMAIL_APP_PASSWORD",
    "GMAIL_TO",
    "APPKIT_FACTS",
    "BRAVE_API_KEY",
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "NOTION_TOKEN",
    "NOTION_DATABASE_ID",
)


def load(prefix: str | None = None, quiet: bool = False) -> int:
    """
    Copy SecureString parameters under `prefix` into os.environ.
    Returns how many variables were set. Never raises: a credentials problem
    should surface as the specific "key not set" message the pipeline already
    prints, not as an opaque crash inside the loader.
    """
    prefix = (prefix or os.environ.get("JOBHUNTER_SSM_PREFIX", "")).strip().rstrip("/")
    if not prefix:
        return 0

    try:
        import boto3
        ssm = boto3.client("ssm")
        loaded, skipped = [], []
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=prefix, Recursive=True, WithDecryption=True):
            for p in page.get("Parameters", []):
                name = p["Name"].rsplit("/", 1)[-1]
                if name not in _ALLOWED:
                    continue
                if os.environ.get(name):
                    skipped.append(name)      # environment wins
                    continue
                os.environ[name] = p["Value"]
                loaded.append(name)
        if not quiet:
            print(f"  [Secrets] loaded {len(loaded)} from {prefix}: "
                  f"{', '.join(sorted(loaded)) or 'none'}"
                  + (f" (already set, kept: {', '.join(sorted(skipped))})" if skipped else ""))
        return len(loaded)
    except Exception as e:
        if not quiet:
            print(f"  [Secrets] could not read {prefix}: {e}")
        return 0
