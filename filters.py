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

import re

# ── Title-only screen, used to decide whether a description is worth fetching ─
# LinkedIn descriptions cost one HTTP request each. Fetching all ~1,100 results
# took 420-600s and made JobSpy time out entirely, returning ZERO jobs on half
# the runs. Screening on the title first means we only spend requests on
# postings that could plausibly survive.
#
# This must stay CONSERVATIVE. It is an optimisation, not a filter: the real
# filter chain in main.py still sees every job that gets through. Anything
# dropped here is dropped without ever being read, so only patterns that are
# certain — never "probably" — belong in this list.

_RE_TITLE_HARD_SENIOR = re.compile(
    r"(?<![a-z])(senior|sr\.?|lead|principal|staff|head\s+of|director|"
    r"vice\s+president|\bvp\b|chief|manager|leiter(in)?)(?![a-z])",
    re.IGNORECASE,
)
# German-market employment forms he is not eligible for, plus gender markers
# that indicate a German-language posting.
_RE_TITLE_INELIGIBLE = re.compile(
    r"\b(werkstudent\w*|praktikum|praktikant\w*|studentische\w*|hiwi|"
    r"ausbildung|dual(es|er)|abschlussarbeit|masterarbeit|bachelorarbeit|"
    r"intern(ship)?|thesis|postdoc\w*|professor\w*)\b",
    re.IGNORECASE,
)
_RE_TITLE_JUNIOR_OK = re.compile(
    r"\b(junior|jr\.?|entry[\s-]?level|graduate|absolvent\w*|trainee|associate)\b",
    re.IGNORECASE,
)
# "(Senior)" in parentheses is German-ad convention for "senior OPTIONAL".
_RE_TITLE_OPTIONAL_SENIOR = re.compile(r"\(\s*senior\s*\)", re.IGNORECASE)


def title_is_worth_fetching(title: str) -> bool:
    """
    Should we spend an HTTP request fetching this posting's description?

    Conservative by design: when in doubt, fetch. A wrongly skipped job is
    invisible forever, whereas a wrongly fetched one just costs a request.
    """
    t = (title or "").strip()
    if not t:
        return True                      # unknown title: let the real filters decide

    if _RE_TITLE_INELIGIBLE.search(t):
        return False

    # Neutralise the optional-senior convention before the seniority test.
    t_clean = _RE_TITLE_OPTIONAL_SENIOR.sub(" ", t)
    if _RE_TITLE_HARD_SENIOR.search(t_clean):
        # "Junior Engineering Manager" and the like are rare but real.
        return bool(_RE_TITLE_JUNIOR_OK.search(t_clean))

    return True


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
