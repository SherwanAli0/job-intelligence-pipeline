# Private scoring profiles

The application requires real scoring profiles in private storage. `config.py` contains no embedded CV facts and supplies no fallback. Candidate-specific facts that appear inside scoring prompts and rule messages are loaded from the private object's top-level `candidate_facts` key and substituted when the prompt is rendered; code comments and docstrings refer to "the candidate". Search-region wording (the North Rhine-Westphalia and commute-belt city lists) is search policy and stays in source. Source order is an explicitly selected `JOBHUNTER_PROFILE_FILE`, otherwise private S3 when `JOBHUNTER_S3_BUCKET` is set. Missing, invalid or unavailable profiles stop startup with a sanitized error.

The default S3 key is `state/private/scoring-profiles-v1.json`. `JOBHUNTER_S3_PREFIX` replaces `state`; `JOBHUNTER_PROFILE_S3_KEY` overrides the complete key. Keep S3 Block Public Access enabled and server-side encryption on. The existing task role reads the object without additional credentials or permissions.

## Required object layout

Schema version 1 with these top-level keys: `schema_version`, `profiles` (AI, ML, DS and shared), `prompt_context` (`intro` and `work_match`) and `candidate_facts`. `candidate_facts` must contain exactly these non-empty text keys, no more and no fewer:

| Key | Substituted into |
| --- | --- |
| `candidate_name` | name mentions in the scoring rules |
| `german_level` | the German-level cap and the pre-screen reason text |
| `professional_experience` | the experience cap |
| `degree_status` | the Master's-required cap |
| `completed_degree` | the pre-screen reason text for Master's-required ads |
| `domain_exposure` | the unfamiliar-domain cap |
| `study_city` | the location rule |
| `work_scope` | the work-authorization rule |
| `work_auth_short` | the visa-sponsorship boost |
| `ml_match_note`, `ds_match_note`, `da_match_note` | the note under each track's top boost |
| `cv_hint_example` | the example in the tailoring instructions |

Text is inserted exactly as stored, including line breaks and indentation of continuation lines. The loader has no embedded fallback: a missing or malformed `candidate_facts` stops startup with `Private candidate facts are required` or `Invalid private candidate facts`, and neither message contains profile text. Because the key set is exact, adding another fact later needs the same upload-first order described below.

Older images read only `schema_version`, `profiles` and `prompt_context` and ignore other top-level keys, so the object with `candidate_facts` added still works with the currently deployed image. This was checked by rendering every scoring prompt with the previous code against the updated object and comparing SHA-256 hashes.

## Local use

Use `scoring_profiles.example.json` as the JSON schema. It contains fictional test text, not the owner's facts. Supply complete AI, ML and DS profiles and their shared facts under schema version 1, plus `prompt_context.intro`, `prompt_context.work_match` and every `candidate_facts` key; the loader preserves every character and does not add shared text automatically.

Keep real JSON under ignored `personal/` and select it before running the application:

```powershell
$env:JOBHUNTER_PROFILE_FILE = (Resolve-Path personal/scoring-profiles.json).Path
python main.py --dry-run
```

A dry run still scrapes live sources and pays for scoring. The offline regression command is `python -m pytest tests/ -q`; its conftest explicitly selects the fictional example, without reading the owner's private profile.

One test scans the source and docs for the private facts. It runs only when a private object is available, either as `personal/scoring-profiles.json` or through `JOBHUNTER_PRIVATE_PROFILE_FOR_SCAN`, and otherwise skips. It builds its forbidden text from that private object at run time, plus an optional word list in `personal/forbidden-terms.txt` or `JOBHUNTER_FORBIDDEN_TERMS_FILE` (one term per line), and reports only file names and term numbers.

## GitHub Actions

Manual calibration and the disabled rollback workflow restore `SCORING_PROFILES_JSON` from an encrypted repository secret into the runner's temporary directory. It contains an identical copy of the S3 JSON and is never printed, uploaded as an artifact or committed. Fargate continues to use S3. After intentionally editing profiles, synchronize the encrypted secret if those workflows should use the new text; for the `candidate_facts` change this is required, because those workflows run the merged code. The health check does not score jobs; its workflow selects the fictional example only to satisfy the shared config import, so it needs no real private profile.

## Deployment order for the candidate_facts change

The new code refuses to start without `candidate_facts`, so the private object must be updated before the code is merged and deployed:

1. Build the updated JSON locally from the current private object by adding the `candidate_facts` key. Do not edit the other keys. Render the scoring prompts for each track with the new code and compare hashes with the previous code before uploading.
2. Confirm S3 versioning is enabled on the state bucket and note the current object's version id (this is the rollback point for the JSON).
3. Upload the updated object to the same key with server-side encryption. Nothing else changes yet: the running image ignores the new key.
4. Update the `SCORING_PROFILES_JSON` repository secret with the same JSON.
5. Merge the change and let the deploy workflow roll out the new image.
6. Check the next scheduled run: the log shows the profiles loaded from the private S3 source and no `ProfilesUnavailable` error.

If the merge or deploy happens before step 3, the new task stops at startup with the sanitized `Private candidate facts are required` error; uploading the object fixes it without a code change.

## Migration gate and rollback

Status: complete for the profile move. The validation run for it succeeded on 3 November 2026 and the cleanup was merged. The `candidate_facts` change is a later, separate step that follows the order above. Rendered scoring prompts were checked for byte-for-byte equality against the previous implementation. No prompt content, model, routing, filter or schedule changes are part of either migration.

Rollback uses the previous task definition revision or its exact SHA-tagged image; both work with the updated object because they ignore `candidate_facts`. Keep the private S3 object, and restore the earlier S3 object version only if the JSON itself needs reverting. Local CV copies remain ignored on disk; only the current public copies are removed from Git. Old commits, old image revisions and copies previously downloaded still contain historical facts; Git history is preserved.
