"""Private profile loading, prompt preservation and failure handling; no network."""
import io
import json
import logging

import pytest

from profile_loader import LoadedProfiles, MAX_BYTES, ProfilesUnavailable, load_profiles


def profiles():
    return {name: "\n" + name + " profile: ü → text\n" for name in ("AI", "ML", "DS", "shared")}


def payload(values=None):
    return json.dumps({"schema_version": 1, "profiles": profiles() if values is None else values},
                      ensure_ascii=False).encode("utf-8")


class S3:
    def __init__(self, raw=None, error=None):
        self.raw, self.error, self.calls, self.body = raw, error, [], None

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        self.body = io.BytesIO(self.raw)
        return {"Body": self.body}


def test_s3_preserves_every_byte_and_closes_response():
    original = profiles()
    client = S3(payload(original))
    result = load_profiles(original, environ={"JOBHUNTER_S3_BUCKET": "test-bucket"}, s3_client=client)
    assert result.source == "s3"
    assert result.values == original
    assert result.fingerprints() == LoadedProfiles(original, "embedded").fingerprints()
    assert client.calls == [{"Bucket": "test-bucket", "Key": "state/private/scoring-profiles-v1.json"}]
    assert client.body.closed


def test_explicit_private_file_takes_precedence(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_bytes(payload())
    client = S3(error=AssertionError("S3 must not be called"))
    result = load_profiles(environ={"JOBHUNTER_PROFILE_FILE": str(path), "JOBHUNTER_S3_BUCKET": "test"}, s3_client=client)
    assert result.values == profiles() and result.source == "file"
    assert client.calls == []


@pytest.mark.parametrize("prefix,key", [("custom/", "custom/private/scoring-profiles-v1.json"),
                                        ("", "private/scoring-profiles-v1.json")])
def test_existing_state_prefix_is_respected(prefix, key):
    client = S3(payload())
    load_profiles(environ={"JOBHUNTER_S3_BUCKET": "test", "JOBHUNTER_S3_PREFIX": prefix}, s3_client=client)
    assert client.calls[0]["Key"] == key


def test_explicit_key_is_respected():
    client = S3(payload())
    load_profiles(environ={"JOBHUNTER_S3_BUCKET": "test", "JOBHUNTER_PROFILE_S3_KEY": "private/other.json"}, s3_client=client)
    assert client.calls[0]["Key"] == "private/other.json"


@pytest.mark.parametrize("raw", [b'{"private_text": "DO_NOT_LOG"}', b"DO_NOT_LOG",
    json.dumps({"schema_version": True, "profiles": profiles()}).encode(),
    json.dumps({"schema_version": 2, "profiles": profiles()}).encode(),
    payload({"AI": "DO_NOT_LOG"}), payload({**profiles(), "AI": ""}),
    payload({**profiles(), "ML": 42}), b"x" * (MAX_BYTES + 1)])
def test_invalid_sources_fall_back_without_logging_payload(raw, caplog):
    client = S3(raw)
    with caplog.at_level(logging.WARNING):
        result = load_profiles(profiles(), environ={"JOBHUNTER_S3_BUCKET": "test"}, s3_client=client)
    assert result.source == "embedded" and result.values == profiles()
    assert "DO_NOT_LOG" not in caplog.text
    assert client.body.closed


@pytest.mark.parametrize("fallback,strict", [(None, ""), (profiles(), "1")])
def test_source_failure_stops_startup_when_private_profiles_required(fallback, strict):
    client = S3(error=RuntimeError("DO_NOT_LOG"))
    with pytest.raises(ProfilesUnavailable) as error:
        load_profiles(fallback, environ={"JOBHUNTER_S3_BUCKET": "test", "JOBHUNTER_REQUIRE_PRIVATE_PROFILES": strict}, s3_client=client)
    assert "DO_NOT_LOG" not in str(error.value)
    assert error.value.__suppress_context__


def test_missing_explicit_file_uses_migration_fallback(tmp_path):
    result = load_profiles(profiles(), environ={"JOBHUNTER_PROFILE_FILE": str(tmp_path / "missing")})
    assert result.source == "embedded"


def test_no_source_keeps_local_behavior_without_creating_client(monkeypatch):
    monkeypatch.setattr("profile_loader._s3_client", lambda: pytest.fail("unexpected network client"))
    assert load_profiles(profiles(), environ={}).values == profiles()
    with pytest.raises(ProfilesUnavailable):
        load_profiles(environ={})


def test_private_text_is_not_in_loaded_object_repr():
    loaded = LoadedProfiles({"AI": "DO_NOT_LOG"}, "s3")
    assert "DO_NOT_LOG" not in repr(loaded)


def test_private_prompt_context_preserves_text_and_is_not_repr_visible():
    context = {"intro": "Private introduction: DO_NOT_LOG\n", "work_match": "Private experience: DO_NOT_LOG"}
    raw = json.dumps({"schema_version": 1, "profiles": profiles(), "prompt_context": context}).encode()
    result = load_profiles(environ={"JOBHUNTER_S3_BUCKET": "test"}, s3_client=S3(raw))
    assert result.prompt_context == context
    assert "DO_NOT_LOG" not in repr(result)


@pytest.mark.parametrize("context", ["DO_NOT_LOG", {"intro": "DO_NOT_LOG"}, {"intro": "DO_NOT_LOG", "work_match": None}])
def test_malformed_private_prompt_context_is_sanitized(context, caplog):
    raw = json.dumps({"schema_version": 1, "profiles": profiles(), "prompt_context": context}).encode()
    with caplog.at_level(logging.WARNING):
        result = load_profiles(profiles(), environ={"JOBHUNTER_S3_BUCKET": "test"}, s3_client=S3(raw))
    assert result.source == "embedded"
    assert "DO_NOT_LOG" not in caplog.text
