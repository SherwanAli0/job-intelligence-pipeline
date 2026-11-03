# Private scoring profiles: staged migration

The scoring profiles can be read from a private S3 object instead of being maintained in public source. The JSON schema is shown in `../scoring_profiles.example.json`; it contains the full AI, ML and DS strings and their shared facts. Do not replace complete profiles with only track-specific additions. Whitespace and Unicode are preserved, so identical profile hashes mean identical text reaches the existing scoring prompt.

Source order:

1. `JOBHUNTER_PROFILE_FILE`, when explicitly set, selects a private local JSON file.
2. Otherwise, `JOBHUNTER_S3_BUCKET` selects S3. The default object key is `state/private/scoring-profiles-v1.json`; `JOBHUNTER_S3_PREFIX` replaces `state`, or `JOBHUNTER_PROFILE_S3_KEY` overrides the complete key.
3. During this first migration stage only, an unavailable source falls back to the existing embedded strings. A warning identifies fallback without printing personal text. Setting `JOBHUNTER_REQUIRE_PRIVATE_PROFILES=1` disables fallback.

Keep private local files under ignored `personal/`, outside the Docker build and Git. The example file contains no real personal data. The S3 object uses the existing task role; do not put credentials or personal text in repository variables. Keep S3 Block Public Access enabled and use server-side encryption. Profile contents and SDK error payloads are never logged by the loader.

## Rollout

1. Export the existing strings to the private S3 object without editing them. Verify read-back hashes for all four strings. Do not overwrite a different existing object silently.
2. Deploy this loader. `config.py` exposes the same profile variables, so scoring prompts, routing, models and filters do not change. Startup logs say `Scoring profiles loaded from private s3 source`; warnings indicate that the migration fallback was used.
3. Observe the next normal 10:00 Europe/Berlin run. Require a successful task and normal completion, with private-source loading and no fallback warning. Do not start a paid scrape just to test this migration.
4. Only after that run, remove embedded strings and public `cv/` copies, remove real deployment identifiers from tracked templates in favor of private repository variables, and make the private source mandatory. Keep Git history intact. The first-stage commit still contains embedded facts and is not the final privacy cleanup.

Rollback: restore the previous task definition revision or register a revision using its exact SHA-tagged image. Do not delete the private object during rollback. Existing images and Git history still contain historical public facts; this migration cleans the current application source after the run gate, not old copies.

Tests are offline: `python -m pytest tests/test_private_profiles.py -q`, then the full existing test suite. They cover unchanged text, source selection, malformed or unavailable objects, strict mode, bounded reads and sanitized logs.
