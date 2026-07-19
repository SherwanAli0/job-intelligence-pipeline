"""
filters.py — single source of truth for Germany-eligibility term lists.

main.py (pre-scorer location filter) and scorer.py (hard disqualifier) used to
carry their own copies of these three tuples, and they drifted: scorer knew
"dach"/"german market" but not "hiring across europe"; main knew the reverse.
Result: a job could pass one stage and be killed by the other, and any tuning
fix applied to one file silently didn't apply to the other — the same failure
shape as the two historical filter regressions.

Every list below is the UNION of the two former copies. Add new signals HERE
and only here; both stages pick them up automatically.
"""

# Germany-presence signals — these confirm a role is doable from Germany.
GERMANY_TERMS = (
    "germany", "deutschland", "berlin", "munich", "münchen", "hamburg",
    "frankfurt", "cologne", "köln", "düsseldorf", "bochum", "dortmund",
    "essen", "stuttgart", "leipzig", "nrw", "bavaria", "bayern",
    "saxony", "sachsen", "hessen", "baden-württemberg",
    # formerly scorer-only:
    "dach", "german market", "german office", "german team",
)

# Phrases that confirm a remote role accepts Germany-based hires.
# Required when a non-German location appears in the location field.
REMOTE_COVERS_GERMANY_SIGNALS = (
    "remote in germany", "remote from germany", "remote within germany",
    "remote (germany", "germany-remote", "remote-germany",
    "remote in eu", "remote within the eu", "remote in the european union",
    "fully remote eu", "fully remote within europe", "fully remote in europe",
    "remote in europe", "remote within europe", "remote across europe",
    "remote anywhere in europe", "europe-wide remote", "eu-wide remote",
    "remote in dach", "dach region", "remote across the dach",
    "we hire across europe", "we hire across the eu",
    "open to candidates in germany", "open to applicants in germany",
    "hiring across europe", "hiring across the eu",
    "based in germany", "based anywhere in europe", "based anywhere in the eu",
    "you can work from anywhere in europe",
    "you can work from anywhere in the eu",
    "you can work from anywhere in the european union",
    "remote across emea",
)

# Phrases that REVOKE Germany eligibility — even if "remote" appears, these
# lock the role outside Germany.
REMOTE_LOCKED_OUT_SIGNALS = (
    "us-based only", "us only", "united states only", "must be based in the us",
    "must reside in the us", "must be in the us", "us residents only",
    "uk only", "uk-based only", "must be based in the uk",
    "canada only", "must be based in canada",
    "latin america only", "latam only", "must be based in latam",
    "remote in latam", "remote in latin america",
    "remote in india", "india only", "must be based in india",
    "apac only", "must be based in apac",
)
