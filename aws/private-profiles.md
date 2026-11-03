# Private scoring profiles

The application requires real scoring profiles in private storage. `config.py` contains no embedded CV facts and supplies no fallback. Source order is an explicitly selected `JOBHUNTER_PROFILE_FILE`, otherwise private S3 when `JOBHUNTER_S3_BUCKET` is set. Missing, invalid or unavailable profiles stop startup with a sanitized error.

The default S3 key is `state/private/scoring-profiles-v1.json`. `JOBHUNTER_S3_PREFIX` replaces `state`; `JOBHUNTER_PROFILE_S3_KEY` overrides the complete key. Keep S3 Block Public Access enabled and server-side encryption on. The existing task role reads the object without additional credentials or permissions.

## Local use

Use `scoring_profiles.example.json` as the JSON schema. It contains fictional test text, not the owner's facts. Supply complete AI, ML and DS profiles and their shared facts under schema version 1, plus `prompt_context.intro` and `prompt_context.work_match` for the existing personal prompt context; the loader preserves every character and does not add shared text automatically.

Keep real JSON under ignored `personal/` and select it before running the application:

```powershell
$env:JOBHUNTER_PROFILE_FILE = (Resolve-Path personal/scoring-profiles.json).Path
python main.py --dry-run
```

A dry run still scrapes live sources and pays for scoring. The offline regression command is `python -m pytest tests/ -q`; its conftest explicitly selects the fictional example, without reading the owner's private profile.

## GitHub Actions

Manual calibration and the disabled rollback workflow restore `SCORING_PROFILES_JSON` from an encrypted repository secret into the runner's temporary directory. It contains an identical copy of the S3 JSON and is never printed, uploaded as an artifact or committed. Fargate continues to use S3. After intentionally editing profiles, synchronize the encrypted secret if those workflows should use the new text. The health check does not score jobs; its workflow selects the fictional example only to satisfy the shared config import, so it needs no real private profile.

## Migration gate and rollback

This cleanup must follow a full validation run on the first-stage image, with successful completion, private S3 loading and no migration fallback. The owner requested running that validation immediately rather than waiting for the next scheduled run. Rendered scoring prompts were checked for byte-for-byte equality against the pre-cleanup implementation. No prompt content, model, routing, filter or schedule changes are part of this migration.

Rollback uses the previous task definition revision or its exact SHA-tagged image. Keep the private S3 object. Local CV copies remain ignored on disk; only the current public copies are removed from Git. Old commits, old image revisions and copies previously downloaded still contain historical facts; Git history is preserved.
