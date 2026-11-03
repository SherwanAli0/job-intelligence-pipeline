"""Load private scoring text without logging its contents.

During migration, callers may supply the existing embedded profiles as a fallback.
After the first successful scheduled run, remove that fallback from config.py.
"""
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

LOGGER = logging.getLogger(__name__)
PROFILE_NAMES = frozenset({"AI", "ML", "DS", "shared"})
MAX_BYTES = 256 * 1024


class ProfilesUnavailable(RuntimeError):
    """Private profiles cannot be loaded or validated; contains no private text."""


@dataclass(frozen=True)
class LoadedProfiles:
    values: dict[str, str] = field(repr=False)
    source: str

    def fingerprints(self) -> dict[str, str]:
        return {name: hashlib.sha256(value.encode("utf-8")).hexdigest()
                for name, value in self.values.items()}


def _validate(values) -> dict[str, str]:
    if not isinstance(values, dict) or set(values) != PROFILE_NAMES:
        raise ProfilesUnavailable("Expected AI, ML, DS and shared scoring profiles")
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ProfilesUnavailable("Every scoring profile must be non-empty text")
    return dict(values)  # Preserve whitespace and Unicode exactly as supplied.


def _decode(raw: bytes) -> dict[str, str]:
    if len(raw) > MAX_BYTES:
        raise ProfilesUnavailable("Scoring profile object exceeds the size limit")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ProfilesUnavailable("Unsupported scoring profile schema")
    return _validate(data.get("profiles"))


def _s3_client():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", config=Config(
        connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 2}))


def load_profiles(fallback: Mapping[str, str] | None = None, *, environ=None,
                  s3_client=None) -> LoadedProfiles:
    """Prefer an explicit private file, otherwise S3 when the state bucket is set.

    A source failure uses the migration fallback unless strict mode is enabled.
    With no fallback (the final migration stage), source failures always stop startup.
    No error, log message or object representation includes profile contents.
    """
    env = os.environ if environ is None else environ
    filename = env.get("JOBHUNTER_PROFILE_FILE", "").strip()
    bucket = env.get("JOBHUNTER_S3_BUCKET", "").strip()
    strict = env.get("JOBHUNTER_REQUIRE_PRIVATE_PROFILES", "").strip() == "1"
    source = "file" if filename else "s3" if bucket else "embedded"
    if source != "embedded":
        try:
            if source == "file":
                with Path(filename).open("rb") as stream:
                    raw = stream.read(MAX_BYTES + 1)
            else:
                prefix = env.get("JOBHUNTER_S3_PREFIX", "state").strip().strip("/")
                default_key = "/".join(part for part in (prefix, "private/scoring-profiles-v1.json") if part)
                key = env.get("JOBHUNTER_PROFILE_S3_KEY", default_key).strip()
                if not key:
                    raise ProfilesUnavailable("A private scoring profile key is required")
                client = _s3_client() if s3_client is None else s3_client
                response = client.get_object(Bucket=bucket, Key=key)
                stream = response["Body"]
                try:
                    raw = stream.read(MAX_BYTES + 1)
                finally:
                    stream.close()
            loaded = LoadedProfiles(_decode(raw), source)
            print("  [Profiles] Scoring profiles loaded from private %s source" % source)
            return loaded
        except Exception:
            # SDK and JSON exceptions can contain request parameters or input text.
            # Report only the source type and suppress the original exception chain.
            if strict or fallback is None:
                raise ProfilesUnavailable("Private scoring profiles unavailable from " + source) from None
            LOGGER.warning("Private scoring profiles unavailable from %s; using embedded migration fallback", source)
    if strict or fallback is None:
        raise ProfilesUnavailable("Private scoring profiles are required")
    return LoadedProfiles(_validate(dict(fallback)), "embedded")
