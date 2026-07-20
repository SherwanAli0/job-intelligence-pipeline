"""
main.py — daily job hunt orchestrator.

Run locally:  python main.py
Run on CI:    triggered by GitHub Actions (.github/workflows/daily.yml)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Secrets must be in the environment before any module that reads them is
# imported (config/scorer/notifier all consult os.environ). No-op unless
# JOBHUNTER_SSM_PREFIX is set, so laptop and GitHub Actions runs are unchanged.
import secrets_loader

secrets_loader.load()

import storage
from config import MAX_RESULTS, MIN_SCORE
from notifier import add_to_notion, send_email
from scrapers import scrape_all
from scorer import score_jobs, _classify_track

# ── A1 diversity quotas — guaranteed minimum digest slots per track ───────────
# The market skews AI-heavy (a recent run scored 93 AI vs 4 ML), so a pure
# score sort buries the DS/ML/DA roles Sherwan actually asked for. We reserve
# the top-N of each track first, then fill the rest by score.
_TRACK_QUOTA = {"DS": 6, "ML": 6, "DA": 4, "AI": 8}

# ── B6 ghost-job detection ────────────────────────────────────────────────────
# Postings older than this are likely stale/reposted "zombie" ads that rarely
# convert. We don't drop them (age data is imperfect) — we penalise the score
# and dim them in the digest so fresh roles rank above them.
_GHOST_AGE_DAYS = 45
_GHOST_PENALTY = 8

# ── C9 skill-demand radar vocabulary ──────────────────────────────────────────
# Skills we scan every JD for, to report what the market wants most vs the CV.
_RADAR_SKILLS = (
    "python", "sql", "pytorch", "tensorflow", "scikit-learn", "xgboost",
    "pandas", "numpy", "spark", "airflow", "dbt", "kafka", "snowflake",
    "databricks", "tableau", "power bi", "looker", "docker", "kubernetes",
    "terraform", "aws", "gcp", "azure", "mlflow", "langchain", "langgraph",
    "llm", "rag", "vector database", "hugging face", "fastapi", "git",
    "ci/cd", "statistics", "a/b test", "nlp", "computer vision", "transformers",
    "java", "scala", "go ", "rust", "javascript", "typescript", "react",
    "mlops", "vertex ai", "bedrock", "sagemaker", "observability",
)
# Skills already on the CV (so the radar can flag GAPS specifically).
_CV_SKILLS = {
    "python", "sql", "javascript", "typescript", "pytorch", "tensorflow",
    "scikit-learn", "xgboost", "pandas", "numpy", "docker", "git", "ci/cd",
    "fastapi", "react", "llm", "rag", "langchain", "hugging face", "nlp",
    "computer vision", "transformers", "statistics", "a/b test",
}


# ── Cross-source dedup ────────────────────────────────────────────────────────
# Same job listed on LinkedIn + the company's Greenhouse board + a web search
# creates three competing entries. Normalize (company, title) and keep ONE,
# preferring the most direct source.

# Higher number = preferred source. ATSs > aggregators > web search.
_SOURCE_PRIORITY: dict[str, int] = {
    # Tier 1 — direct company ATSs (cleanest, full descriptions)
    "Greenhouse":      100,
    "Lever":            95,
    "Ashby":            90,
    "Workday-CXS":      85,
    "Workday":          80,
    "Personio":         80,
    "SmartRecruiters":  78,
    "Amazon":           76,
    "Recruitee":        74,
    # Tier 2 — government / aggregator job boards
    "Arbeitsagentur":   60,
    "Arbeitnow":        55,
    "Remotive":         50,
    # Tier 3 — HN, search
    "HN-Hiring":        40,
    "Adzuna":           38,
    "BraveSearch":      20,
    "DuckDuckGoSearch": 18,
}

# Gender/seniority noise tokens to strip from titles before key construction
_TITLE_NOISE_RE = re.compile(
    r"\(\s*(m/w/d|m/f/d|w/m/d|d/m/w|f/m/x|m/f/x|x/m/f|all\s+genders?)\s*\)"
    r"|\b(m/w/d|m/f/d|w/m/d|d/m/w|f/m/x|m/f/x|all\s+genders?)\b",
    re.IGNORECASE,
)
# Legal-form suffixes only — these are clearly the same company with/without
# the corporate-form word ("X GmbH" = "X"). We do NOT strip generic words
# like "group", "tech", "labs", "ai" — those identify genuinely different
# companies (e.g. "Acme AI" and "Acme" may be unrelated, "Foo Group" is a
# distinct entity from "Foo"). Over-aggressive stripping caused false merges.
_COMPANY_SUFFIX_RE = re.compile(
    r"\s+(gmbh|ag|se|kg|ohg|inc|incorporated|ltd|limited|llc)\.?$",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    """lowercase, strip gender markers, collapse whitespace."""
    s = (s or "").lower()
    s = _TITLE_NOISE_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _normalize_company(s: str) -> str:
    """
    Conservative dedup key: lowercase, whitespace trim, strip ONLY legal forms.
    No non-alphanumeric stripping — that was collapsing genuinely different
    companies into one. When two rows aren't clearly the same company, we
    prefer to keep both rather than merge them.
    """
    s = _normalize(s)
    # Strip trailing legal form once
    s = _COMPANY_SUFFIX_RE.sub("", s).strip()
    return s


def _dedup_cross_source(jobs: list[dict]) -> list[dict]:
    """
    Collapse duplicates across sources keyed on (normalized company, title).
    Keeps one row per logical job, preferring the highest-priority source.
    """
    best: dict[str, dict] = {}
    for j in jobs:
        key = f"{_normalize_company(j.get('company',''))}::{_normalize(j.get('title',''))}"
        if not key.strip(":"):
            continue
        prior = best.get(key)
        if prior is None:
            best[key] = j
            continue
        cur_pri = _SOURCE_PRIORITY.get(j.get("source", ""), 0)
        prv_pri = _SOURCE_PRIORITY.get(prior.get("source", ""), 0)
        if cur_pri > prv_pri:
            # New source wins; preserve URL from kept row (which is the new one).
            best[key] = j
        elif cur_pri == prv_pri:
            # Tie-break: prefer the row with the longer description (more signal)
            if len(j.get("description", "")) > len(prior.get("description", "")):
                best[key] = j
    return list(best.values())

# German words anywhere in the title = German-language role
_GERMAN_TITLE_KEYWORDS = {
    "künstliche", "intelligenz", "entwicklung", "algorithmen",
    "wissenschaft", "kenntnisse", "wissenschaftler", "analytiker",
    "datenanalyse", "maschinelles", "verarbeitung", "forschung",
    "ausbildung", "gestützte", "gestützt", "bereich", "weiterentwicklung",
    "automatisierung", "digitalisierung", "daten", "geschlechter",
    "abschlussarbeit", "projektionsoptiken", "datenerfassung",
    "berichterstattung", "lieferkette", "alle", "statistiken",
}

# If the title contains any of these → almost certainly German-language
_GERMAN_TITLE_FRAGMENTS = (
    "ki-", "-ki", "ki ", " ki ", "künstlich", "entwickl", "daten",
    "gestütz", "automatisier", "digitalisi", "bereich", "geschlecht",
    "abschluss", "statistik",
)

# ── Location filter — Germany on-site OR remote that covers Germany ──────────
# Sherwan lives in Bochum (NRW) and pays German taxes. He needs jobs he can
# actually do FROM Germany — F2F/hybrid in Germany, OR a remote role that
# explicitly allows Germany-based hires. A Poland F2F job is useless even
# though Poland is in the EU; a "Spain HQ, fully remote in EU" job IS useful
# because Germany is covered.

# Shared with scorer.py's hard disqualifier — single source of truth in
# filters.py so the two stages can never drift apart again.
from filters import (
    GERMANY_TERMS as _GERMANY_TERMS,
    REMOTE_COVERS_GERMANY_SIGNALS as _REMOTE_COVERS_GERMANY_SIGNALS,
    REMOTE_LOCKED_OUT_SIGNALS as _REMOTE_LOCKED_OUT_SIGNALS,
)


# Specific non-EU cities/countries — positive identification triggers drop.
# Anything NOT in this list and NOT in _GERMANY_TERMS is treated as "unknown"
# and KEPT, so the scorer can decide from the description.
_NON_EU_LOCATIONS = (
    # USA
    "united states", " usa ", "(usa)", "/usa",
    "new york", "san francisco", "boston", "chicago", "los angeles",
    "seattle", "austin", "denver", "atlanta", "washington d.c.",
    "washington dc", "miami", "philadelphia", "houston", "dallas",
    # UK
    "london", "manchester", "birmingham", "united kingdom", " uk ",
    "(uk)", "/uk", "edinburgh", "glasgow", "leeds",
    # Canada
    "toronto", "vancouver", "montreal", "canada",
    # Australia / NZ
    "sydney", "melbourne", "brisbane", "australia", "new zealand",
    # Latin America
    "brazil", "brasil", "colombia", "mexico", "méxico",
    "argentina", "chile", "peru",
    "são paulo", "rio de janeiro", "buenos aires",
    "bogotá", "bogota", "medellín", "medellin",
    "santiago", "lima", "mexico city",
    "latin america", "latam",
    # Middle East (non-EU)
    "dubai", "abu dhabi", "saudi arabia", "qatar", "bahrain",
    "kuwait", "oman", "united arab emirates", " uae ",
    # Asia
    "singapore", "hong kong", "tokyo",
    "bangalore", "bengaluru", "hyderabad", "mumbai", "delhi",
    "chennai", "pune", " india ", "(india)",
    "china", " japan ", "south korea", "taiwan",
    "philippines", "vietnam", "thailand", "indonesia",
    "malaysia", "pakistan",
    # Africa
    "nigeria", "kenya", "south africa", "egypt",
    "nairobi", "lagos", "cape town", "johannesburg",
)


def _is_attendable_from_germany(j: dict) -> bool:
    """
    Recall-friendly location filter:
      - Empty / unknown location          → KEEP (let scorer decide)
      - Hard veto (US-only / UK-only etc) → DROP
      - Germany / DACH / EU coverage      → KEEP
      - Positively non-EU city            → DROP (Bangalore, NYC, São Paulo, etc.)
      - Anything else                     → KEEP (unknown wins)

    The change from the previous behaviour: a location string we don't
    recognise (e.g. "Some Town, Some Country" with no clear signal) used to
    be dropped. Now it passes through. Recall over precision; the scorer is
    smart enough to read the description.
    """
    loc  = (j.get("location")    or "").strip().lower()
    desc = (j.get("description") or "").lower()[:2000]

    # Empty location → KEEP (let scorer judge from description)
    if not loc:
        return True

    combined = f"{loc} {desc}"

    # Hard veto: explicit lock to non-EU region
    if any(s in combined for s in _REMOTE_LOCKED_OUT_SIGNALS):
        return False

    # Germany or DACH signal in location → keep
    if any(t in loc for t in _GERMANY_TERMS):
        return True

    # "Remote" in location → keep if Germany/EU coverage signal exists
    if "remote" in loc:
        if any(t in combined for t in _GERMANY_TERMS):
            return True
        if any(s in desc for s in _REMOTE_COVERS_GERMANY_SIGNALS):
            return True
        # Plain "Remote" with no coverage signal — could still be EU/global.
        # Recall over precision: KEEP and let the scorer judge.
        return True

    # Positively-identified non-EU city/country → drop
    # (use loc + first 200 chars of desc to catch JDs like "based in Mumbai")
    loc_check = f" {loc} {desc[:200]} "
    if any(s in loc_check for s in _NON_EU_LOCATIONS):
        # Non-EU positively identified — only keep if description says
        # explicit remote-from-EU coverage
        if any(s in desc for s in _REMOTE_COVERS_GERMANY_SIGNALS):
            return True
        return False

    # Location is unknown to us — could be a small German town, EU city, etc.
    # Default KEEP so the scorer can decide.
    return True


# Softening phrases — if one appears near a years-of-experience mention, the
# requirement is a wish, not a wall. German ads inflate requirements; a
# softened "2-3 years" routinely interviews fresh grads with internships.
_EXP_SOFTENERS = (
    "ideally", "idealerweise", "preferabl", "preferred",
    "wünschenswert", "wuenschenswert", "von vorteil", "a plus",
    "nice to have", "or equivalent", "oder vergleichbar",
    "desirable", "bonus", "would be great", "would be a plus",
)


# Titles that mark a role as designed for fresh graduates. Such postings
# often still boilerplate "X years experience" in the body (e.g. Airbus VIE
# programmes) — the title-level intent wins, so they skip the experience drop.
_GRADUATE_TITLE_RE = None  # compiled lazily below


def _is_graduate_designed(title: str) -> bool:
    global _GRADUATE_TITLE_RE
    import re
    if _GRADUATE_TITLE_RE is None:
        _GRADUATE_TITLE_RE = re.compile(
            r"\b(graduate|trainee|vie|intern(?:ship)?|praktikum|praktikant(?:in)?|"
            r"absolvent(?:in)?|entry[\s\-]?level|junior)\b",
            re.IGNORECASE,
        )
    return bool(_GRADUATE_TITLE_RE.search(title or ""))


def _no_experience_overload(j: dict) -> bool:
    """
    Tiered experience filter (recall-friendly):
      - 4+ years          → DROP always (clearly senior-targeted)
      - 3 years           → DROP unless softened nearby (ideally/preferred/…)
      - 2 years           → DROP only when HARD-required ("minimum", "at least",
                            "must", a "+" sign, "mindestens") AND not softened
      - under 2 / vague   → KEEP ("1-2 years", "0-2 years", "up to 2 years",
                            bare "2 years experience" without a hard word)

    Graduate immunity: titles containing graduate/trainee/VIE/intern/
    Praktikum/Absolvent/entry-level/junior are designed for fresh grads —
    body-text year mentions are boilerplate there, so they always KEEP.
    (Werkstudent roles are still dropped by the scorer's own check.)

    Guards: calendar years (2024), team sizes ("team of 25"), "24/7", salary
    figures never match — the number must sit next to year(s)/Jahre in an
    experience context, and range right-hand sides don't count.
    """
    import re
    if _is_graduate_designed(j.get("title", "")):
        return True
    desc = (j.get("description") or "").lower()

    written = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
               "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

    def _softened(start: int, end: int) -> bool:
        window = desc[max(0, start - 70):end + 70]
        return any(s in window for s in _EXP_SOFTENERS)

    def _violates(n: int, start: int, end: int, hard: bool) -> bool:
        """Apply the tier rules to one matched mention. True = job must drop."""
        if n >= 4:
            return True
        if n == 3:
            return not _softened(start, end)
        if n == 2:
            return hard and not _softened(start, end)
        return False

    # (pattern, hard_requirement, value_extractor) — value from group(1)
    checks = [
        # "N+ years" — the + sign is itself a hard minimum
        (r"(?<![-\d])(\d+)\+\s*years?\b", True, int),
        # "N-M years" / "N to M years" — range; lower bound, treated as soft
        # unless a hard word appears nearby (handled via the hard patterns too)
        (r"(?<![-\d])(\d+)\s*(?:[-–]|to)\s*\d+\s*years?\b", False, int),
        # "minimum N years" / "at least N years" — hard by definition
        (r"\b(?:minimum|at\s+least)\s+(?:of\s+)?(\d+)\+?\s*years?\b", True, int),
        # bare "N years (of) experience" — soft unless preceded by hard words
        (r"(?<![-\d])(?<!up to )(?<!maximum )(?<!max )"
         r"(\d+)\s*years?\s+(?:of\s+)?(?:[\w-]+\s+){0,3}experience\b", False, int),
        # "experience: N years" in a requirements table — hard
        (r"\bexperience\s*:?\s*(\d+)\+?\s*years?\b", True, int),
        # German: "N+ Jahre" hard; "N Jahre Berufserfahrung" bare soft;
        # "mindestens N Jahre" hard
        (r"(?<![-\d])(\d+)\+\s*jahre\b", True, int),
        (r"(?<![-\d])(\d+)\s*jahre\s+berufserfahrung", False, int),
        (r"\bmindestens\s+(\d+)\+?\s*jahre\b", True, int),
        # Written-out English: bare soft, minimum/at-least hard
        (r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:\(\d+\)\s+)?"
         r"years?\s+(?:of\s+)?(?:[\w-]+\s+){0,3}experience\b",
         False, lambda w: written[w]),
        (r"\b(?:minimum|at\s+least)\s+(?:of\s+)?"
         r"(one|two|three|four|five|six|seven|eight|nine|ten)\s+"
         r"(?:\(\d+\)\s+)?years?\b",
         True, lambda w: written[w]),
    ]

    for pattern, hard, to_int in checks:
        for m in re.finditer(pattern, desc):
            n = to_int(m.group(1))
            if _violates(n, m.start(), m.end(), hard):
                return False

    return True


# Senior-title detector — robust against ALL the ways recruiters format titles:
#   "Senior AI Engineer", "Senior_AI_Engineer" (LinkedIn slug),
#   "Senior-Engineer", "(Senior)", "AI Engineer, Senior",
#   "Lead/Principal Data Scientist", "Engineer III/IV"
# Negative lookbehind/lookahead use [a-z] only (NOT \w which includes "_"),
# so underscores, hyphens, slashes, parens, and brackets all act as boundaries.
import re as _re_senior

_RE_SENIOR_IN_TITLE = _re_senior.compile(
    r"(?<![a-z])"
    r"(senior|sr\.?|lead|principal|staff|"
    r"head[\s_\-]of|director|vice[\s_\-]president|\bvp\b|"
    r"chief|architect|"
    r"engineer[\s_\-]+(ii|iii|iv|v)|"
    r"manager|leiter|leiterin|führung|fuehrung|bereichsleiter)"
    r"(?![a-z])",
    _re_senior.IGNORECASE,
)


# "(Senior)" as a PARENTHESIZED prefix is German-ad convention for "senior
# OPTIONAL — mid/junior candidates also considered" (e.g. "(Senior) Applied
# Scientist"). Strip that token before the seniority test so those roles
# survive; a bare "Senior Applied Scientist" still drops.
_RE_OPTIONAL_SENIOR = _re_senior.compile(r"\(\s*senior\s*\)", _re_senior.IGNORECASE)


def _not_fulltime_senior(j: dict) -> bool:
    """
    Drop senior/lead/principal/manager/architect/director roles.

    Sherwan is targeting roles with ≤ 2 years of experience. The experience
    check (_no_experience_overload) already blocks "3+ years" requirements;
    this filter catches title-level seniority signals even when the JD
    doesn't state an explicit year count.

    Allows the role through ONLY if a junior/entry/intern/graduate qualifier
    is also present in the title (e.g. "Junior Engineering Manager" is rare
    but legitimate) — or if the ONLY seniority marker is a parenthesized
    "(Senior)" prefix, which in German ads means the level is optional.
    """
    title = j.get("title", "")
    # Neutralize the optional "(Senior)" token before testing
    title = _RE_OPTIONAL_SENIOR.sub(" ", title)
    title_lower = title.lower()

    if _RE_SENIOR_IN_TITLE.search(title):
        # Pass-through if explicitly qualified as junior/entry-level.
        # NOTE: "working student / werkstudent" are NOT here on purpose —
        # Sherwan is not enrolled at a German university and cannot do those.
        junior_qualifiers = (
            "junior", "entry", "entry-level", "entry level",
            "intern", "internship", "praktikum",
            "graduate", "grad ", "trainee",
            "associate", "jr.", "jr ",
        )
        if any(q in title_lower for q in junior_qualifiers):
            return True
        return False

    return True


# Trigger phrases for Masters / MSc / PhD detection
_MASTERS_TRIGGER_PATTERNS = (
    r"\bmaster(?:'s|s)?\s+degree\b",
    r"\bmsc\b",
    r"\bm\.sc\.?\b",
    r"\bphd\b",
    r"\bph\.\s?d\.?\b",
    r"\bdoctorate\b",
    r"\bdoctoral\b",
    r"\bmasterabschluss\b",
)

# Softening / preference phrases — if any appear in the surrounding window
# (±80 chars), the degree mention is treated as nice-to-have, not required.
_MASTERS_SOFTENING = (
    "preferred", "is a plus", "would be a plus", "is preferred",
    "nice to have", "would be nice", "would be welcome",
    "advantageous", "is an advantage", "is advantageous", "of advantage",
    "desirable", "is desirable", "is welcome",
    "von vorteil", "wäre von vorteil", "wünschenswert",
)

# Exclusion contexts — these contain the trigger phrase but aren't degree
# requirements at all. If detected in the surrounding window, skip the match.
_MASTERS_EXCLUSION_CONTEXTS = (
    "scrum master", "master data", "master class", "masterclass",
    "master branch", "master node", "master/slave", "master plan",
    "master key", "headmaster", "grandmaster", "master of ceremonies",
)


def _no_masters_required(j: dict) -> bool:
    """
    DROP jobs that require a Master's, MSc, PhD, or Doctorate.

    Trigger phrases (case-insensitive): "master's degree", "msc", "m.sc",
    "phd", "ph.d", "doctorate", "doctoral", "Masterabschluss".

    Each trigger occurrence is examined in a ±80-char window. We KEEP when:
      - The window contains a softening phrase ("preferred", "is a plus",
        "nice to have", "advantageous", "desirable", "von Vorteil", …)
      - The window contains an exclusion phrase ("scrum master", "master
        data", "masterclass", "master branch", …)

    A Bachelor's requirement on its own is NOT a trigger here — we only
    react to Master/PhD mentions.
    """
    import re
    title = (j.get("title") or "").lower()
    desc  = (j.get("description") or "").lower()
    text  = f"{title}\n{desc}"

    for pat in _MASTERS_TRIGGER_PATTERNS:
        for m in re.finditer(pat, text):
            start = max(0, m.start() - 80)
            end   = min(len(text), m.end() + 80)
            window = text[start:end]

            # Exclusion context? skip this occurrence.
            if any(excl in window for excl in _MASTERS_EXCLUSION_CONTEXTS):
                continue
            # Softened? skip this occurrence.
            if any(soft in window for soft in _MASTERS_SOFTENING):
                continue
            # Real Master/PhD requirement found → drop
            return False

    return True


def _is_english_friendly(j: dict) -> bool:
    """Drop jobs that are clearly German-language unless description confirms English."""
    title_lower = j["title"].lower()
    desc_lower = (j.get("description") or "").lower()

    # Explicit English signal in description → always keep
    english_signals = (
        "english", "working language is english", "language: english",
        "english-speaking", "team language", "our language is english",
    )
    if any(s in desc_lower for s in english_signals):
        return True

    # German word in title → drop
    title_words = set(title_lower.split())
    if title_words & _GERMAN_TITLE_KEYWORDS:
        return False
    if any(frag in title_lower for frag in _GERMAN_TITLE_FRAGMENTS):
        return False

    # Title has "(m/w/d)" or "(w/m/d)" and NO english signal in description → likely German role
    if ("(m/w/d)" in title_lower or "(w/m/d)" in title_lower or "(d/m/w)" in title_lower):
        if "english" not in desc_lower:
            return False

    return True

SEEN_FILE = Path("seen_jobs.json")
# Prune ids not re-seen for this long. Postings that vanished 60+ days ago can
# only reappear as genuine reposts, which deserve a fresh look anyway (the
# ghost tag will mark them stale if they carry an old posted_at).
_SEEN_RETENTION_DAYS = 60


def load_seen() -> dict[str, str]:
    """{job_id: last-seen ISO date}. Transparently migrates the legacy flat
    id list (all legacy ids get today's date, so nothing re-surfaces early)."""
    from datetime import date
    raw = storage.read_text(str(SEEN_FILE))
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):  # legacy format
                today = date.today().isoformat()
                return {i: today for i in data}
            return dict(data)
        except Exception:
            return {}
    return {}


def save_seen(seen: dict[str, str]) -> None:
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=_SEEN_RETENTION_DAYS)).isoformat()
    kept = {k: v for k, v in seen.items() if str(v) >= cutoff}
    if len(kept) < len(seen):
        print(f"  [Seen] pruned {len(seen) - len(kept)} ids not re-seen for {_SEEN_RETENTION_DAYS}+ days")
    storage.write_text(str(SEEN_FILE), json.dumps(dict(sorted(kept.items())), indent=2))


# ── T1d: run-stats history + silent-scraper-death detection ──────────────────
# Every run appends one JSON line (per-source counts, filter drops, scores,
# digest size, email flag) to run_stats.jsonl — committed by CI, so pipeline
# behaviour is trendable long after Actions logs expire. On each run, any
# source that historically delivered jobs but has now returned 0 for several
# consecutive runs raises a warning that lands IN the digest email.

STATS_FILE = Path("run_stats.jsonl")
_DEAD_RUNS = 3          # consecutive zero-runs (incl. current) that trigger the alarm
_DEAD_MIN_MEDIAN = 10   # only alarm for sources that historically deliver >= this
_HISTORY_WINDOW = 30    # runs of history considered for the median


def _load_run_history() -> list[dict]:
    raw = storage.read_text(str(STATS_FILE))
    if not raw:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out[-_HISTORY_WINDOW:]


def _detect_platform() -> str:
    """Which compute platform produced this run. Both runtimes set these vars
    themselves, so no configuration is needed. Recorded in run_stats.jsonl so
    per-source counts can be compared across a platform migration — some
    sources (notably LinkedIn/Indeed via JobSpy) rate-limit datacenter IP
    ranges differently, and a degraded source looks identical to a quiet day
    unless you can group runs by where they ran."""
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "aws-lambda"
    if os.environ.get("GITHUB_ACTIONS"):
        return "github-actions"
    return "local"


def _scraper_timings() -> dict:
    """Per-scraper wall time from the last scrape_all(), if available."""
    try:
        from scrapers import SCRAPER_TIMINGS
        return dict(SCRAPER_TIMINGS)
    except Exception:
        return {}


def _record_run_stats(stats: dict) -> None:
    try:
        stats.setdefault("platform", _detect_platform())
        storage.append_line(str(STATS_FILE), json.dumps(stats, ensure_ascii=False))
    except Exception as e:
        print(f"  [Stats] could not record run stats: {e}")


def _dead_source_warnings(history: list[dict], current: dict) -> list[str]:
    """Sources whose history says they deliver, but which have now been at
    zero for _DEAD_RUNS consecutive runs — the silent-death signature."""
    if len(history) < _DEAD_RUNS:
        return []
    import statistics
    warnings = []
    known = set()
    for h in history:
        known.update((h.get("sources") or {}).keys())
    recent = history[-(_DEAD_RUNS - 1):]
    for src in sorted(known):
        series = [int((h.get("sources") or {}).get(src, 0)) for h in history]
        med = statistics.median(series)
        if med < _DEAD_MIN_MEDIAN:
            continue
        recent_zero = all(int((h.get("sources") or {}).get(src, 0)) == 0 for h in recent)
        if recent_zero and int(current.get(src, 0)) == 0:
            warnings.append(
                f"Source '{src}' has returned 0 jobs for {_DEAD_RUNS} straight runs "
                f"(historical median {int(med)}) — probably broken."
            )
    return warnings


def _job_age_days(j: dict):
    """Age of a posting in days from posted_at, or None if unknown/unparseable."""
    from datetime import datetime, timezone
    s = str(j.get("posted_at") or "").strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        elif " " in s and ":" in s:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except Exception:
        return None


def _apply_ghost_penalty(scored: list[dict]) -> int:
    """B6: penalise + tag stale postings. Returns how many were flagged."""
    n = 0
    for j in scored:
        age = _job_age_days(j)
        if age is not None and age > _GHOST_AGE_DAYS:
            j["score"] = max(0, j.get("score", 0) - _GHOST_PENALTY)
            j["ghost"] = True
            n += 1
    return n


def _skill_radar(jobs: list[dict], top_n: int = 15) -> None:
    """C9: report the most-requested skills across all JDs, flagging CV gaps."""
    from collections import Counter
    counts = Counter()
    for j in jobs:
        blob = ((j.get("title") or "") + " " + (j.get("description") or "")).lower()
        for skill in _RADAR_SKILLS:
            if skill in blob:
                counts[skill.strip()] += 1
    if not counts:
        return
    total = len(jobs)
    print(f"\n── C9 skill-demand radar (across {total} scraped JDs) ──")
    for skill, c in counts.most_common(top_n):
        pct = 100 * c / total
        have = skill in _CV_SKILLS
        tag = "  ✓ on CV" if have else "  ← GAP (not on CV)"
        bar = "█" * min(int(pct / 2), 40)
        print(f"  {skill:18s} {pct:4.0f}%  {bar}{tag}")
    gaps = [s for s, _ in counts.most_common(top_n) if s not in _CV_SKILLS]
    if gaps:
        print(f"  Top demand gaps to consider learning: {', '.join(gaps[:6])}")


def _diversify(good: list[dict], cap: int) -> list[dict]:
    """
    A1: build the digest so no single track dominates. Reserve the top
    _TRACK_QUOTA[track] of each track first (in score order), then fill the
    remaining slots by pure score. Preserves overall score ordering within
    the guaranteed set as much as possible.
    """
    by_track: dict[str, list[dict]] = {}
    for j in good:  # good is already score-sorted desc
        by_track.setdefault(j.get("_track", "AI"), []).append(j)

    picked_ids: set[str] = set()
    picked: list[dict] = []
    # Phase 1: quota per track
    for track, quota in _TRACK_QUOTA.items():
        for j in by_track.get(track, [])[:quota]:
            if j["id"] not in picked_ids:
                picked_ids.add(j["id"]); picked.append(j)
    # Phase 2: fill remaining capacity by score
    for j in good:
        if len(picked) >= cap:
            break
        if j["id"] not in picked_ids:
            picked_ids.add(j["id"]); picked.append(j)
    # Return in score order for a clean digest
    picked.sort(key=lambda x: x.get("score", 0), reverse=True)
    return picked[:cap]


def main(dry_run: bool = False) -> None:
    print("=" * 60)
    print("  Daily Job Hunter — starting run" + ("  [DRY RUN]" if dry_run else ""))
    print("=" * 60)

    # Phase timing. The compute decision turns on how the run splits between
    # the long scrape and the comparatively short post-scoring work: the
    # scrape needs an unbounded runtime (measured at 40 min, vs Lambda's
    # 15-minute ceiling), while everything after batch retrieval is small
    # enough to be a Lambda if we ever separate the stages. Recorded per run
    # so that split is decided on data rather than estimates.
    import time as _time
    _phase_t0 = _time.time()
    _phases: dict[str, float] = {}

    def _phase(name: str) -> None:
        nonlocal _phase_t0
        now = _time.time()
        _phases[name] = round(now - _phase_t0, 1)
        _phase_t0 = now

    seen = load_seen()
    print(f"Previously seen jobs: {len(seen)}")

    # ── Scrape ────────────────────────────────────────────────────────────────
    all_jobs = scrape_all()
    _phase("scrape")

    # T1d: per-source counts + dead-source alarm (vs committed run history)
    from collections import Counter as _Counter
    src_counts = dict(_Counter(j.get("source", "?") for j in all_jobs))
    run_history = _load_run_history()
    health_warnings = _dead_source_warnings(run_history, src_counts)
    for w in health_warnings:
        print(f"⚠️  {w}")

    # ── Cross-source dedup (before unseen filtering so logs are intuitive) ────
    before = len(all_jobs)
    all_jobs = _dedup_cross_source(all_jobs)
    after = len(all_jobs)
    print(f"Cross-source dedup: merged {before} jobs, {after} after dedup")

    new_jobs = [j for j in all_jobs if j["id"] not in seen]
    n_new = len(new_jobs)  # captured before the filter chain reassigns
    print(f"New (unseen) jobs: {n_new}")

    # A2: accumulate which filter drops which track, printed as a matrix at the end.
    from collections import Counter, defaultdict
    drop_by_filter_track: dict = defaultdict(Counter)

    def _apply_filter(jobs: list[dict], fn, label: str) -> list[dict]:
        """Run fn over jobs; print kept/dropped counts + track-level drop tally."""
        kept, dropped = [], []
        for j in jobs:
            if fn(j):
                kept.append(j)
            else:
                dropped.append(j)
                drop_by_filter_track[label][_classify_track(j)] += 1
        if dropped:
            print(f"[{label}] dropped {len(dropped)} jobs, e.g.:")
            for ex in dropped[:3]:
                print(f"  - {ex.get('title', '')[:90]} @ {ex.get('company', '')}")
        else:
            print(f"[{label}] dropped 0 jobs")
        print(f"After {label}: {len(kept)}")
        return kept

    # Germany on-site OR remote-that-includes-Germany only.
    new_jobs = _apply_filter(new_jobs, _is_attendable_from_germany, "Location filter (Germany-attendable)")
    # Drop jobs that are clearly German-language with no English mention
    new_jobs = _apply_filter(new_jobs, _is_english_friendly, "English filter")
    # HARD filter: experience requirement of 2 or more years
    new_jobs = _apply_filter(new_jobs, _no_experience_overload, "ExperienceFilter (>=2 years)")
    # Drop senior/lead roles
    new_jobs = _apply_filter(new_jobs, _not_fulltime_senior, "Senior-title filter")
    # HARD filter: Master/MSc/PhD required (with softening + exclusion handling)
    new_jobs = _apply_filter(new_jobs, _no_masters_required, "MastersFilter")

    # A2: which filter kills which track (diagnoses the DS/ML famine)
    if drop_by_filter_track:
        print("\n── A2 per-track drop matrix (which filter kills which track) ──")
        print(f"  {'filter':32s} {'AI':>4s} {'ML':>4s} {'DS':>4s} {'DA':>4s}")
        for label, c in drop_by_filter_track.items():
            print(f"  {label[:32]:32s} {c['AI']:4d} {c['ML']:4d} {c['DS']:4d} {c['DA']:4d}")

    # C9: skill-demand radar over everything scraped this run
    _skill_radar(all_jobs)

    if not new_jobs:
        print("Nothing new today. Exiting.")
        # Refresh last-seen dates so active postings never age into pruning
        from datetime import date as _date, datetime as _dt, timezone as _tz
        _today = _date.today().isoformat()
        seen.update({j["id"]: _today for j in all_jobs})
        save_seen(seen)
        if not dry_run:
            _record_run_stats({
                "ts": _dt.now(_tz.utc).isoformat(timespec="seconds"),
                "sources": src_counts, "scraped": before, "deduped": after,
                "new": 0, "digest": 0, "near": 0, "email_ok": True,
                "warnings": health_warnings,
            })
        sys.exit(0)

    _phase("filter")

    # ── Score ─────────────────────────────────────────────────────────────────
    scored = score_jobs(new_jobs)
    _phase("score")

    # B6: penalise + tag stale/zombie postings so fresh roles rank above them
    ghosts = _apply_ghost_penalty(scored)
    if ghosts:
        print(f"\nB6 ghost-job penalty: flagged {ghosts} stale postings (>{_GHOST_AGE_DAYS}d, -{_GHOST_PENALTY})")

    # ── Disqualification histogram ───────────────────────────────────────────
    # Helps tune filters over time — if "experience" dominates, the bar is too
    # strict; if "german_required" is huge, search pool needs better targeting.
    from collections import Counter
    dq_counts = Counter(
        j.get("disqualified_category") or "(passed)"
        for j in scored
    )
    print("\n── Pre-screen disqualification histogram ──")
    for category, count in dq_counts.most_common():
        bar = "█" * min(count, 40)
        print(f"  {category:22s} {count:4d}  {bar}")

    # ── Single ranked list — sorted by score desc, capped at MAX_RESULTS ─────
    # Bands removed in the restored notifier; notifier sorts internally by
    # score desc + fresh-first within score, so we only need to apply the
    # floor and cap here.
    good = [j for j in scored if j.get("score", 0) >= MIN_SCORE]
    good.sort(key=lambda x: x["score"], reverse=True)
    # A1: diversity quotas so DS/ML/DA aren't buried under AI
    top = _diversify(good, MAX_RESULTS)
    from collections import Counter as _C
    _mix = _C(j.get("_track", "?") for j in top)
    print(f"Digest track mix (A1 quotas applied): " +
          ", ".join(f"{k}={v}" for k, v in sorted(_mix.items())))

    # ── Near misses (35–44): visible, clearly separated ───────────────────────
    # Haiku fails >90% of pre-screen survivors below the 45 floor; the 35-44
    # band is where miscalibrated-but-real matches die invisibly. Surface the
    # top 10 of that band in a dimmed section so Sherwan can judge calibration
    # himself instead of trusting the cliff.
    near = [j for j in scored if 35 <= j.get("score", 0) < MIN_SCORE]
    near.sort(key=lambda x: x["score"], reverse=True)
    near = near[:10]
    for j in near:
        j["near_miss"] = True

    print(f"\nJobs scoring >= {MIN_SCORE}: {len(good)}")
    print(f"Near misses (35-{MIN_SCORE - 1}): {len(near)}")
    print(f"Sending top {len(top)} + {len(near)} near misses to notifications\n")

    for j in top[:10]:  # preview in CI logs
        print(f"  [{j['score']:3d}] {j['title']} @ {j['company']} ({j['source']})")

    # ── B4: Application Kit — pre-draft screening answers for digest jobs ──────
    try:
        from application_kit import enrich_with_kits
        enrich_with_kits(top)
    except Exception as e:
        print(f"  [AppKit] skipped: {e}")

    _phase("rank_and_kits")

    # ── Notify ────────────────────────────────────────────────────────────────
    email_ok = True  # nothing-to-send counts as success
    if dry_run:
        print("\n[DRY RUN] Skipping email + Notion. Pipeline complete.")
    elif top or near:
        email_ok = send_email(top + near, warnings=health_warnings)
        if top:
            add_to_notion(top)
    else:
        print("No jobs above threshold — no notifications sent.")

    # ── Update seen ───────────────────────────────────────────────────────────
    # B2 guard: if the digest email FAILED, do NOT mark jobs seen — otherwise a
    # single SMTP outage permanently buries that day's matches. Leaving them
    # unseen means the next run re-scores and re-sends them (cents, not losses).
    if dry_run:
        print("\n[DRY RUN] seen_jobs.json NOT updated.")
    elif email_ok:
        # Refresh EVERY id seen this run (not just new ones) so still-listed
        # postings keep a current last-seen date and only genuinely vanished
        # ids age into the 60-day pruning window.
        from datetime import date as _date
        _today = _date.today().isoformat()
        seen.update({j["id"]: _today for j in all_jobs})
        save_seen(seen)
        print("\nDone. seen_jobs.json updated.")
    else:
        print("\nEmail delivery FAILED — seen_jobs.json NOT updated so today's "
              "matches are retried next run instead of being buried.")

    # ── T1d: append this run's stats line (trendable pipeline history) ────────
    if not dry_run:
        from datetime import datetime as _dt, timezone as _tz
        _record_run_stats({
            "ts": _dt.now(_tz.utc).isoformat(timespec="seconds"),
            "sources": src_counts,
            "scraped": before, "deduped": after, "new": n_new,
            "drops": {label: sum(c.values()) for label, c in drop_by_filter_track.items()},
            "dq": dict(dq_counts.most_common(8)),
            "digest": len(top), "near": len(near),
            "track_mix": dict(_mix),
            "email_ok": email_ok,
            "warnings": health_warnings,
            # Per-phase seconds. The scrape phase needs unbounded runtime;
            # everything after it is small enough to run as a Lambda if the
            # stages are ever separated. Recorded so that choice stays
            # evidence-based.
            "phases": _phases,
            "scrapers": dict(sorted(_scraper_timings().items(),
                                    key=lambda x: -x[1])[:10]),
        })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily Job Hunter")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=bool(os.environ.get("DRY_RUN")),
        help="Run the full pipeline but skip email/Notion delivery and don't update seen_jobs.json. Useful for local testing.",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run)
