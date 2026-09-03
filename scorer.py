"""
scorer.py — uses Claude to score each job against Sherwan's CV profile.

Pipeline:
  1. _hard_disqualify()  — regex pre-screen, sets score=0, skips Claude API call
  2. _score_batch()      — Claude Haiku scores the remaining jobs in batches of 10
"""

import json
import os
import re
import time

import anthropic

from config import CV_PROFILE, CV_PROFILE_AI, CV_PROFILE_ML, CV_PROFILE_DS

# The client is built on first use, not at import time. On Lambda the API key
# arrives from Parameter Store during startup, which happens after imports —
# building the client here would crash the function in its init phase with a
# bare KeyError, before any of our own error handling could report why. Tests
# and callers may still assign `scorer.client` directly to inject a fake.
client = None


# ── Token accounting ─────────────────────────────────────────────────────────
# Cost per run was previously an estimate quoted from memory. The API reports
# exact usage on every response, so the pipeline now measures what it spent
# instead — the same "measure, don't estimate" discipline that decided Fargate
# over Lambda.
TOKEN_USAGE: dict = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                     "by_model": {}, "batched": False}

# USD per million tokens. Batch requests bill at 50%; cached reads at ~10% of
# the input rate, cache writes at 125%.
_PRICES = {
    "claude-haiku-4-5-20251001": {"in": 1.00, "out": 5.00},
    "claude-sonnet-5": {"in": 2.00, "out": 10.00},   # intro pricing to 2026-08-31
}
_DEFAULT_PRICE = {"in": 3.00, "out": 15.00}


def _record_usage(model: str, usage, batched: bool = False) -> None:
    """Accumulate token usage from a Messages API response. Never raises: cost
    telemetry must not be able to break a scoring run."""
    try:
        if usage is None:
            return
        i = int(getattr(usage, "input_tokens", 0) or 0)
        o = int(getattr(usage, "output_tokens", 0) or 0)
        cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cw = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        TOKEN_USAGE["input"] += i
        TOKEN_USAGE["output"] += o
        TOKEN_USAGE["cache_read"] += cr
        TOKEN_USAGE["cache_write"] += cw
        TOKEN_USAGE["batched"] = TOKEN_USAGE["batched"] or batched
        m = TOKEN_USAGE["by_model"].setdefault(model, {"in": 0, "out": 0, "cache_read": 0,
                                                       "cache_write": 0, "batched": batched})
        m["in"] += i; m["out"] += o; m["cache_read"] += cr; m["cache_write"] += cw
        m["batched"] = m["batched"] or batched
    except Exception:
        pass


def estimated_cost_usd() -> float:
    """Cost of this run from measured token counts, in USD."""
    total = 0.0
    for model, u in TOKEN_USAGE["by_model"].items():
        p = _PRICES.get(model, _DEFAULT_PRICE)
        discount = 0.5 if u.get("batched") else 1.0
        total += (u["in"] / 1e6) * p["in"] * discount
        total += (u["out"] / 1e6) * p["out"] * discount
        total += (u["cache_read"] / 1e6) * p["in"] * 0.10 * discount
        total += (u["cache_write"] / 1e6) * p["in"] * 1.25 * discount
    return round(total, 4)


def _client():
    global client
    if client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. On AWS it should come from SSM "
                "Parameter Store (JOBHUNTER_SSM_PREFIX); locally, export it."
            )
        client = anthropic.Anthropic(api_key=key)
    return client

# ── Per-track CV routing ──────────────────────────────────────────────────────
# Each job is classified into a track and scored against the CV framed for that
# track, so a Data Scientist job is judged against the DS-framed CV (statistics,
# experimentation, analytics) rather than the AI-framed one. This is what lifts
# DS/ML scores instead of every role reading as "a stretch for an AI person".
# Data-Analyst jobs use the DS profile (closest data framing); unknown → AI.
_TRACK_PROFILES = {"AI": CV_PROFILE_AI, "ML": CV_PROFILE_ML,
                   "DS": CV_PROFILE_DS, "DA": CV_PROFILE_DS}


def _classify_track(j: dict) -> str:
    """Lightweight keyword classifier → 'AI' | 'ML' | 'DS' | 'DA'. Title-weighted."""
    t = (j.get("title") or "").lower()
    d = (j.get("description") or "").lower()[:600]
    blob = f"{t} {t} {d}"  # title double-weighted

    def has(*kw):
        return any(k in blob for k in kw)

    # Order matters: most-specific first.
    if has("data analyst", "business intelligence", " bi ", "bi analyst",
           "analytics engineer", "reporting analyst", "business analyst",
           "power bi", "tableau", "dashboards"):
        return "DA"
    if has("data scientist", "data science", "quantitative analyst",
           "statistician", "experimentation", "a/b test", "causal"):
        return "DS"
    if has("machine learning", "ml engineer", "mlops", "ml ops",
           "deep learning", "computer vision", " nlp", "nlp ",
           "applied scientist", "research engineer", "pytorch", "tensorflow"):
        return "ML"
    if has(" ai ", "ai engineer", "llm", "genai", "generative ai",
           "ai agent", "agentic", "rag", "prompt", "gpt", "conversational ai"):
        return "AI"
    # Fall back on broad data vs AI hints, else AI (broadest current focus).
    if has("sql", "analytics", "reporting", "data "):
        return "DS"
    return "AI"

BATCH_SIZE = 10

# Mean Haiku score per batch from the last run — a drift signal for
# run_stats.jsonl. If batch means trend up or down over weeks with a stable
# job mix, the judge is drifting, not the market.
SCORE_BATCH_MEANS: list = []

# Two-stage scoring (budget mode):
#   Stage 2 — Haiku scores everything that survives the pre-screen (cheap bulk)
#   Stage 3 — Sonnet re-scores only the finalists Haiku rates >= the floor,
#             replacing their scores/reasons with sharper judgments.
HAIKU_MODEL = "claude-haiku-4-5-20251001"
# Sonnet 5: better than Sonnet 4.6 AND cheaper (intro $2/$10 per MTok through
# 2026-08-31, then $3/$15 — never more than 4.6 cost). Sonnet 5 runs adaptive
# thinking by default, so scoring calls explicitly disable it (see
# _thinking_kwargs) to keep behaviour and cost flat.
SONNET_MODEL = "claude-sonnet-5"
SONNET_RESCORE_FLOOR = 50

# ── T3 cost controls ─────────────────────────────────────────────────────────
# Message Batches API: 50% off ALL tokens; used when there's enough volume to
# be worth the polling latency (a cron pipeline doesn't care about minutes).
# Prompt caching: cache_control on the per-track system prompt — batches of
# the same track share the prompt, so repeats bill at ~0.1x.
# Structured outputs: output_config guarantees valid JSON — no more losing a
# batch of jobs to a malformed reply.
_BATCH_API_MIN_JOBS = 30      # below this, sync calls are simpler and fine
_BATCH_POLL_SECONDS = 20
# How long to wait on the batch queue before cancelling stragglers and scoring
# them synchronously.
#
# Anthropic's queue is genuinely variable: 1.8 min one evening, 30.6 min the
# next morning, for the same workload. The expensive outcome is falling back to
# sync, because those tokens bill at FULL price — one run cost $0.22 instead of
# the usual $0.07 that way. Waiting costs almost nothing by comparison: the
# Fargate task is idle-polling, roughly a cent an hour.
#
# So the timeout is deliberately generous. It exists only as a backstop against
# a batch that never returns, not as a latency control. A timeout also now
# KEEPS whatever finished, so the worst case degrades gradually instead of
# discarding the whole batch.
_BATCH_TIMEOUT_SECONDS = int(os.environ.get("BATCH_TIMEOUT_SECONDS", "5400"))  # 90 min

_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                    "missing_keywords": {"type": "array", "items": {"type": "string"}},
                    "cv_hint": {"type": "string"},
                },
                "required": ["index", "score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}
_OUTPUT_CONFIG = {"format": {"type": "json_schema", "schema": _SCORE_SCHEMA}}


def _system_blocks(cv_profile: str, long_ttl: bool = False) -> list[dict]:
    """System prompt as a cacheable block. Batch entries use the 1h TTL (they
    may process over a longer window); sync calls use the default 5m."""
    cc = {"type": "ephemeral"}
    if long_ttl:
        cc["ttl"] = "1h"
    return [{"type": "text", "text": _system_prompt(cv_profile), "cache_control": cc}]


def _thinking_kwargs(model: str) -> dict:
    # Sonnet 5 defaults to adaptive thinking when the field is omitted; scoring
    # is a structured judgment task where thinking spend buys little — disable
    # explicitly. Haiku 4.5 (older API generation) is thinking-off by default.
    if model == SONNET_MODEL:
        return {"thinking": {"type": "disabled"}}
    return {}

# ── Hard disqualifiers — checked BEFORE any Claude API call ───────────────────
# These patterns catch clear mismatches and set score=0 without spending tokens.

# Numeric year patterns (3, 4, … years)
# Allows ANY 0-3 word qualifier between "of" and "experience" — catches
# "industry experience", "hands-on experience", "relevant practical experience",
# "applicable professional experience", etc.
# Also catches: "with 3+ years", "demonstrated 3 years", "proven 3+ years",
# "successful 5 years" — common phrasings in job descriptions.
# The (?<![-–\d]) lookbehinds stop the UPPER bound of a range from matching:
# "2-3 years experience" must read as a 2-year ask (within target), not as
# "3 years" — this false-positive was caught by the golden calibration set.
_RE_EXP_NUM = re.compile(
    r"\b(?<![-–\d])([3-9]|\d{2})\+?\s*years?\s+(?:of\s+)?(?:[\w-]+\s+){0,3}experience"
    r"|\b(?<![-–\d])([3-9]|\d{2})\+?\s*years?\s+experience"
    r"|\bminimum\s*(of\s+)?([3-9]|\d{2})\s*\+?\s*years?"
    r"|\bat\s+least\s+([3-9]|\d{2})\+?\s*years?"
    r"|\b([3-9]|\d{2})\s*[-–]\s*\d+\s*years?\s+(?:of\s+)?(?:[\w-]+\s+){0,3}experience"
    r"|\b(?<![-–\d])([3-9]|\d{2})\+\s*years?"
    r"|\bexperience\s*:?\s*([3-9]|\d{2})\+?\s*years?"
    # NEW: "with [optional adjective] N+ years" — e.g.
    #   "with 3+ years", "with proven 3 years", "with strong 5+ years"
    r"|\bwith\s+(?:[\w-]+\s+){0,2}([3-9]|\d{2})\+?\s*years?"
    # NEW: "demonstrated|proven|successful N+ years"
    r"|\b(?:demonstrated|proven|successful|track\s+record\s+of)\s+(?:[\w-]+\s+){0,2}([3-9]|\d{2})\+?\s*years?",
    re.IGNORECASE,
)

# Written-out year patterns ("three years", "minimum three (3) years", etc.)
_RE_EXP_TEXT = re.compile(
    r"\b(three|four|five|six|seven|eight|nine|ten)\s+(?:or\s+more\s+)?"
    r"(?:\(\s*\d+\s*\)\s+)?years?\s+(?:of\s+)?(?:[\w-]+\s+){0,3}experience"
    r"|\b(minimum|at\s+least)\s+(of\s+)?(three|four|five|six|seven|eight|nine|ten)"
    r"(\s*\(\s*\d+\s*\))?\s+years?"
    r"|\bsenior.?level\s+experience\s+required",
    re.IGNORECASE,
)

# Master's-required signals — a COMPLETED Master's/PhD only.
# Used in concert with _RE_BACHELOR_OK and _RE_ENROLLED_CONTEXT below.
#
# This regex used to also catch student-status phrasing ("currently enrolled
# in a Master's programme", "Master's student", "must be enrolled at a
# university") because those marked Werkstudent ads the owner was ineligible
# for. Since the pivot he IS an enrolled M.Sc. student at Uni Bonn, so that
# phrasing describes his exact situation and disqualifying on it would drop
# every single target role. Only completed-degree demands remain.
_RE_MASTERS_REQ = re.compile(
    r"(?:"
    r"master(?:'s)?\s+degree\s+(?:is\s+)?required"
    r"|must\s+have\s+a\s+master"
    r"|requires\s+a\s+master"
    r"|completed\s+master"
    r"|master\s+of\s+science\s+in\b"
    r"|masterabschluss\s+erforderlich"
    r"|abgeschlossenes\s+masterstudium"
    r")",
    re.IGNORECASE,
)

# Student-status framing. Its presence means the ad is describing WHO may
# apply (an enrolled student), not demanding a finished degree, so the
# Master's disqualifier must stand down.
_RE_ENROLLED_CONTEXT = re.compile(
    r"enrolled|immatrikuliert|eingeschrieben|matriculated|student\s+status"
    r"|werkstudent|working\s+student|studentische|studierende|studying\s+at"
    r"|bachelor(?:'s)?\s+or\s+master|bachelor-\s*oder\s+master",
    re.IGNORECASE,
)
_RE_MASTERS_CONTEXT = re.compile(
    r"(minimum|required|essential)\s+qualifications?[\s\S]{0,300}?\bmaster"
    r"|requirements?\s*:[\s\S]{0,300}?\bmaster\s+degree",
    re.IGNORECASE,
)
_RE_BACHELOR_OK = re.compile(
    r"\b(bachelor|b\.sc\b|\bbsc\b|undergraduate|or\s+equivalent|or\s+related\s+degree|ba/bs)\b",
    re.IGNORECASE,
)

# Senior / Lead title — catches any slip-throughs from main.py filter.
# Uses [a-z] lookbehind/lookahead (NOT \b which considers "_" a word char) so
# titles like "Senior_AI_Engineer" (LinkedIn slug), "Senior-Engineer",
# "(Senior)", "AI Engineer, Senior" all match.
_RE_SENIOR_TITLE = re.compile(
    r"(?<![a-z])"
    r"(senior|sr\.?|lead|principal|staff|"
    r"head[\s_\-]of|director|vice[\s_\-]president|\bvp\b|"
    r"chief|architect|"
    r"engineer[\s_\-]+(ii|iii|iv|v)|"
    r"manager|leiter|leiterin|bereichsleiter)"
    r"(?![a-z])",
    re.IGNORECASE,
)

# Non-Python primary language signals in the JOB TITLE
_RE_NONPY_TITLE = re.compile(
    r"\b("
    r"golang|go\s+(developer|engineer|backend|programmer)|"
    r"rust\s+(developer|engineer|programmer)|"
    r"java\s+(developer|engineer|backend|programmer)|"   # NOT javascript
    r"c\+\+\s*(developer|engineer|programmer|specialist)|"
    r"embedded\s+c\b|"
    r"\.net\s+(developer|engineer|programmer)|"
    r"scala\s+(developer|engineer)|"
    r"kotlin\s+(developer|engineer)"
    r")\b",
    re.IGNORECASE,
)

# Non-Python keywords that appear anywhere (description), paired with absence of Python
_NONPY_DESC_SIGNALS = ("golang", "typescript developer", "typescript engineer",
                        "rust developer", "rust engineer")

# Wrong domain — completely different field
_RE_BAD_DOMAIN = re.compile(
    r"\b("
    r"embedded\s+systems?|tinyml|tiny\s*ml|fpga|microcontroller|"
    r"firmware\s+(developer|engineer)|vhdl|verilog|"
    r"hardware\s+(developer|engineer|design)|"
    r"biomedical\s+(engineer|scientist)|medical\s+device|"
    r"clinical\s+trial|histopath|patholog|radiology|oncolog|"
    r"laboratory\s+information|pharma\s+(engineer|developer)|"
    r"embedded\s+(linux|software|developer|engineer)"
    r")\b",
    re.IGNORECASE,
)

# Freelance / contractor-only signals
_RE_FREELANCE_TITLE = re.compile(
    r"\b(freelance|freelancer|freiberuflich)\b", re.IGNORECASE
)
_RE_FREELANCE_DESC = re.compile(
    r"\b("
    r"freelance\s+only|freelancer\s+only|contractors?\s+only|"
    r"contract\s+only|self.?employed|auf\s+freiberuflicher\s+basis|"
    r"als\s+freelancer|no\s+permanent"
    r")\b",
    re.IGNORECASE,
)

# Web analytics focus — GA4, tag management, etc.
_RE_WEB_ANALYTICS = re.compile(
    r"\b("
    r"ga4|google\s+analytics\s+4|looker\s+studio|econda|"
    r"tag\s+management|google\s+tag\s+manager|\bgtm\b|"
    r"adobe\s+analytics|piano\s+analytics|web\s+tracking|"
    r"tracking\s+pixel|conversion\s+tracking|matomo|"
    r"tag\s+implementation"
    r")\b",
    re.IGNORECASE,
)

# ML/AI signals — if present alongside web analytics, keep the job
_RE_ML_SIGNALS = re.compile(
    r"\b("
    r"machine\s+learning|deep\s+learning|neural\s+network|"
    r"data\s+science|large\s+language|llm|\bnlp\b|computer\s+vision|"
    r"model\s+train|pytorch|tensorflow|scikit|xgboost|langchain"
    r")\b",
    re.IGNORECASE,
)

# Non-Germany / Non-EU locations — pre-screener blocklist
_RE_NONGER_LOC = re.compile(
    r"\b("
    # UK
    r"london|manchester|birmingham|united\s+kingdom\b|"
    # USA
    r"new\s+york|san\s+francisco|seattle|boston|chicago|los\s+angeles|"
    r"austin|denver|atlanta|washington\s+d\.?c\.?|united\s+states\b|\busa\b|"
    # Canada
    r"toronto|vancouver|montreal|canada\b|"
    # Australia / NZ
    r"sydney|melbourne|brisbane|australia\b|new\s+zealand\b|"
    # Latin America
    r"brazil|brasil|colombia|mexico|méxico|argentina|chile|peru|"
    r"bogot[aá]|são\s+paulo|rio\s+de\s+janeiro|buenos\s+aires|"
    r"medell[ií]n|santiago|lima|latin\s+america|latam\b|"
    # Middle East (non-EU)
    r"dubai|abu\s+dhabi|saudi\s+arabia|qatar|bahrain|kuwait|oman|"
    r"united\s+arab\s+emirates\b|\buae\b|"
    # Asia
    r"singapore|hong\s+kong|tokyo|bangalore|bengaluru|hyderabad|"
    r"mumbai|delhi|chennai|pune|india\b|china\b|japan\b|"
    r"south\s+korea\b|taiwan\b|philippines\b|vietnam\b|thailand\b|indonesia\b|"
    r"malaysia\b|pakistan\b|"
    # Africa
    r"nigeria\b|kenya\b|south\s+africa\b|egypt\b|nairobi|lagos|cape\s+town|"
    r"johannesburg"
    r")\b",
    re.IGNORECASE,
)

# Germany-presence / remote-eligibility signals — shared with main.py's
# location filter via filters.py so the two stages can never drift apart again.
from filters import (
    GERMANY_TERMS as _GERMANY_TERMS,
    REMOTE_COVERS_GERMANY_SIGNALS as _REMOTE_COVERS_GERMANY_SIGNALS,
    REMOTE_LOCKED_OUT_SIGNALS as _REMOTE_LOCKED_OUT_SIGNALS,
)

# Unpaid / equity-only compensation
_RE_UNPAID = re.compile(
    r"\b("
    r"unpaid\s+(internship|position|role|placement)|"
    r"equity[\s-]only|no\s+salary|without\s+(pay|compensation|remuneration)|"
    r"volunteer\s+(position|role|opportunity|basis)|"
    r"keine\s+(vergütung|bezahlung)|ehrenamtlich(e|er|es)?\b|"
    r"stipend\s+only|honorarium\s+only"
    r")\b",
    re.IGNORECASE,
)

# Fluent / business / native German required — catches English-language JDs
# that bury the German requirement in the body.  Sherwan is B1 only.
# NOTE: phrasing varies wildly. JDs use "C1 German", "C1 fluency in German",
# "fluency in German on C1", "C1-level German", "German skills (C1)" etc.
# We match generously, then rely on the softening check to spare
# "German is a plus" type mentions.
_GERMAN_FLUENCY_PHRASES = (
    "fluent german", "business fluent in german", "business-fluent german",
    "verhandlungssicheres deutsch", "verhandlungssicher in deutsch",
    "german native", "native german",
    "mandatory: english and german", "fluent english and german",
    "fluent german and english", "german fluency required",
    "business level german", "business-level german",
    "professional level german", "professional-level german",
    "business proficiency in german",
    # CEFR level mentions — catch any C1/C2 + German combo regardless of word order
    "c1 german", "c2 german", "c1-german", "c2-german",
    "c1 fluency", "c2 fluency", "c1 level", "c2 level",
    "c1-level", "c2-level", "level c1", "level c2",
    "fluency in german", "fluency on c1", "fluency on c2",
    "german (c1", "german (c2", "german c1", "german c2",
    "german skills (c1", "german skills (c2",
    "german at c1", "german at c2",
    "advanced german", "proficient in german",
    # German-language equivalents
    "fließend deutsch", "fliessend deutsch", "muttersprachlich deutsch",
    "deutsch auf muttersprachlichem niveau", "deutsch auf c1",
    "deutsch auf c2", "deutschkenntnisse auf c1", "deutschkenntnisse auf c2",
    # Phrasings that reached a real digest and were NOT caught. The pattern
    # these share is naming German alongside English, which reads as
    # bilingual-friendly but demands C1 German all the same.
    "deutsch- und englischkenntnisse", "deutsch und englischkenntnisse",
    "deutsche und englische sprachkenntnisse",
    "sehr gute deutschkenntnisse", "gute deutschkenntnisse",
    "sehr gute deutsch- und englischkenntnisse",
    "sicher auf deutsch und englisch", "deutsch und englisch fließend",
    "verhandlungssichere deutschkenntnisse",
    "deutschkenntnisse auf mindestens c1", "deutsch mindestens c1",
    "sehr gute deutsch und englischkenntnisse",
    "deutsch- und englischkenntnisse auf mindestens c1",
    "german and english at c1", "german and english (c1",
    "german and english on c1 level", "very good german",
)

# If any of these appear within ~50 chars of a German-fluency phrase, treat the
# requirement as soft (nice-to-have, not blocking) and DON'T disqualify.
_GERMAN_SOFTENING = (
    "german is a plus", "german would be beneficial", "german preferable",
    "preferable on german", "advantageous", "nice to have",
    "wäre von vorteil", "von vorteil",
)


def _requires_fluent_german(d_low: str) -> bool:
    """
    True if the description requires fluent/native/business German AND no
    softening qualifier appears nearby. Surgical: catches Mandatory:
    English-and-German style phrasing that the existing prompt missed.
    """
    for phrase in _GERMAN_FLUENCY_PHRASES:
        idx = d_low.find(phrase)
        if idx == -1:
            continue
        # Look for softening within +/- 50 chars
        window_start = max(0, idx - 50)
        window_end   = idx + len(phrase) + 50
        window = d_low[window_start:window_end]
        if any(soft in window for soft in _GERMAN_SOFTENING):
            continue  # soft requirement — don't disqualify on this match
        return True
    return False


def _hard_disqualify(j: dict) -> tuple[bool, str, str]:
    """
    Run hard pre-screening checks.
    Returns (True, reason, category)  → score=0, skip Claude.
    Returns (False, "", "")            → proceed to Claude scoring.

    Category is one of: "experience", "german_required", "senior_title",
    "non_python", "wrong_domain", "location", "freelance", "web_analytics",
    "unpaid", "masters_required".  Used by main.py for histogram logging.
    """
    title    = (j.get("title")       or "")
    desc     = (j.get("description") or "")
    loc      = (j.get("location")    or "")
    # "(Senior)" as a parenthesized prefix = German-ad convention for "senior
    # OPTIONAL — mid/junior also considered". Neutralize before title tests.
    title_for_seniority = re.sub(r"\(\s*senior\s*\)", " ", title, flags=re.IGNORECASE)
    t_low    = title.lower()
    d_low    = desc.lower()
    combined = f"{t_low} {d_low}"

    # Graduate-designed titles (graduate/trainee/VIE/intern/Praktikum/
    # Absolvent/entry-level/junior) skip the experience checks — body-text
    # year mentions are boilerplate in programmes built for fresh grads.
    graduate_designed = bool(re.search(
        r"\b(graduate|trainee|vie|intern(?:ship)?|praktikum|praktikant(?:in)?|"
        r"absolvent(?:in)?|entry[\s\-]?level|junior)\b",
        t_low,
    ))

    # ── Check 1: Experience requirement ───────────────────────────────────────
    # 1a. Numeric patterns: "3+ years", "minimum 4 years", "3-5 years experience",
    #     "with 3+ years", "demonstrated 3 years", "proven 5+ years"
    if not graduate_designed and _RE_EXP_NUM.search(d_low):
        return True, "Requires 3+ years experience", "experience"
    # 1b. Written-out patterns: "three years", "minimum three (3) years"
    if not graduate_designed and _RE_EXP_TEXT.search(d_low):
        return True, "Requires 3+ years experience (written out)", "experience"
    # 1c. Title-level seniority: Senior / Lead / Head / Principal in title
    #     with no junior/entry/intern qualifier → auto-disqualify
    if _RE_SENIOR_TITLE.search(title_for_seniority):
        junior_qualifiers = ("junior", "entry", "intern", "graduate", "associate", "jr.")
        if not any(q in t_low for q in junior_qualifiers):
            return True, "Senior/Lead title with no junior qualifier", "senior_title"

    # ── Check 2: Fluent German required (English JDs that bury the German req)
    if _requires_fluent_german(d_low):
        return True, "Requires fluent/native German — candidate is B1", "german_required"

    # ── Check 3: Non-Python primary language ──────────────────────────────────
    if _RE_NONPY_TITLE.search(title) and "python" not in combined:
        return True, "Primary language is not Python (no Python mentioned)", "non_python"
    for signal in _NONPY_DESC_SIGNALS:
        if signal in combined and "python" not in combined:
            return True, f"Primary language appears non-Python ({signal})", "non_python"

    # ── Check 4: Wrong domain ─────────────────────────────────────────────────
    if _RE_BAD_DOMAIN.search(combined):
        return True, "Wrong domain (embedded / hardware / biomedical / pharma)", "wrong_domain"

    # ── Check 5: Location — Germany on-site OR remote-that-covers-Germany ─────
    # Sherwan lives in Bochum (NRW). He needs jobs he can attend from Germany:
    #   - Germany on-site/hybrid → keep
    #   - Remote with Germany or EU-wide coverage → keep
    #   - Anything else (Poland F2F, Spain F2F, US-only remote, LATAM) → drop
    loc_low      = loc.lower()
    desc_2000    = d_low[:2000]
    combined_loc = f"{loc_low} {desc_2000}"
    has_remote   = "remote" in loc_low or "remote" in desc_2000
    has_blocked  = bool(_RE_NONGER_LOC.search(loc_low))
    germany_in_loc_or_desc = (
        any(t in loc_low    for t in _GERMANY_TERMS) or
        any(t in desc_2000  for t in _GERMANY_TERMS)
    )
    remote_covers_de = any(s in desc_2000 for s in _REMOTE_COVERS_GERMANY_SIGNALS)

    # Hard veto: explicitly locked out of Germany region
    if any(s in combined_loc for s in _REMOTE_LOCKED_OUT_SIGNALS):
        return True, "Remote role locked outside Germany (US/UK/LATAM/APAC only)", "location"

    # Non-EU location (LATAM, US, Asia, etc.) and not remote → drop
    if has_blocked and not has_remote:
        return True, "Location outside Germany and not remote", "location"

    # Non-EU location + remote, but no Germany/EU coverage → drop
    if has_blocked and has_remote and not germany_in_loc_or_desc and not remote_covers_de:
        return True, "Remote based outside Germany — coverage not confirmed", "location"

    # Plain "remote" with zero Germany/EU signal → drop
    if has_remote and not has_blocked and not germany_in_loc_or_desc and not remote_covers_de:
        return True, "Remote role — Germany not confirmed as eligible location", "location"

    # Catch-all: location is non-empty, NOT Germany, NOT remote, NOT covered.
    if loc_low and not germany_in_loc_or_desc and not has_remote and not remote_covers_de:
        return True, "Location outside Germany and no remote/EU-wide coverage", "location"

    # ── Check 6: Freelance / contractor only ──────────────────────────────────
    if _RE_FREELANCE_TITLE.search(title):
        return True, "Freelance role — seeking permanent/fixed-term employment", "freelance"
    if _RE_FREELANCE_DESC.search(d_low):
        return True, "Contractor/freelance only — not permanent employment", "freelance"

    # ── Check 7: Web analytics primary focus without ML/AI ───────────────────
    if _RE_WEB_ANALYTICS.search(combined):
        if not _RE_ML_SIGNALS.search(combined) and "python" not in combined:
            return True, "Primary focus is web analytics (GA4/tag management), not ML/AI", "web_analytics"

    # ── Check 8: Unpaid / equity-only ────────────────────────────────────────
    if _RE_UNPAID.search(d_low):
        return True, "Unpaid or equity-only role — not paid employment", "unpaid"

    # ── Check 9: Master's degree strictly required (no Bachelor's alternative)
    bachelor_mentioned = bool(_RE_BACHELOR_OK.search(d_low))
    # An ad written for enrolled students is not asking for a finished degree.
    enrolled_context = bool(_RE_ENROLLED_CONTEXT.search(d_low))
    if not bachelor_mentioned and not enrolled_context:
        if _RE_MASTERS_REQ.search(d_low):
            return True, "Master's degree required and no Bachelor's alternative — candidate has B.Sc.", "masters_required"
        ctx = _RE_MASTERS_CONTEXT.search(d_low)
        if ctx and not _RE_BACHELOR_OK.search(ctx.group(0)):
            return True, "Master's required under qualifications header — candidate has B.Sc.", "masters_required"

    return False, "", ""

def _system_prompt(cv_profile: str = CV_PROFILE) -> str:
    return f"""You are a strict job-matching filter for Sherwan Ali, a Computer Engineering graduate and incoming M.Sc. Artificial Intelligence student at the University of Bonn (enrolled from October 2026). He is hunting roles that fit ALONGSIDE a full-time M.Sc.: Werkstudent / working-student roles (up to 20h/week), internships (Praktikum, Pflichtpraktikum, Praxissemester, internship), AND part-time (Teilzeit) regular employment of roughly 20h/week or less. Full-time (Vollzeit) permanent roles and thesis positions are the WRONG employment form, no matter how good the topical fit. Your job is to score how worth-applying-to each role is. Be honest and conservative. False positives waste Sherwan's time; false negatives are recoverable because he can adjust filters.

CANDIDATE PROFILE (this profile is already framed for the track of the jobs in this batch — score against it directly):
{cv_profile}

═══════════════════════════════════════════════════════════════
SCORING SCALE — be calibrated, not generous
═══════════════════════════════════════════════════════════════
- 85-100: Excellent fit. Real shot at interview. Werkstudent or internship, English-OK, commutable from Bonn or DE-remote, in ANY of the four tracks (AI, ML, Data Science, Data Analyst — see ladder below). Examples across tracks: Working Student AI Agents in Köln, Werkstudent Machine Learning with PyTorch in Bonn, Werkstudent Data Science with scikit-learn in Düsseldorf, Werkstudent Data Analytics on SQL + Power BI, remote within Germany.
- 70-84: Good fit. Worth a tailored application. Minor gaps but core fit is real.
- 55-69: Decent fit. Apply only if you have time and a tailored angle.
- 40-54: Weak. Likely auto-rejected. Skip unless desperate.
- 0-39: Wrong field, wrong stack, wrong seniority, or wrong language requirement.

CALIBRATION ANCHORS — score every job RELATIVE TO THESE THREE, not relative
to the other jobs in this batch. A batch full of weak jobs must not inflate a
mediocre one; a batch full of strong jobs must not deflate a good one.
- ANCHOR 75: "Werkstudent Machine Learning (m/w/d), Köln. Python, PyTorch.
  Enrolled Bachelor's or Master's student, 15-20h/week, English-speaking
  team, German nice to have." → 75. Clean Werkstudent fit, minor unknowns.
- ANCHOR 45: "Werkstudent Data Engineering, Düsseldorf. Airflow, dbt,
  Snowflake. Sehr gute Deutschkenntnisse erforderlich." → 45. Right
  employment form, adjacent stack, German requirement at the edge.
- ANCHOR 20: "Junior Data Scientist (full-time), Berlin. Python,
  scikit-learn, English team." → 20. Perfect topical fit but WRONG
  employment form (full-time, not Werkstudent) and not commutable from
  Bonn; topic match alone cannot lift it.

═══════════════════════════════════════════════════════════════
WORK AUTHORIZATION (highest priority, applied before any other cap)
═══════════════════════════════════════════════════════════════
The candidate is authorized to work in Germany only, not the US, UK, or
other non-EU countries. Score 0 to 15 for any role that is US-only,
US-remote-only, requires US/UK or other non-EU work authorization, or is
onsite outside Germany with no Germany-remote option.

LOCATION (applied with the same priority): the candidate studies in BONN.
On-site or hybrid roles are attendable ONLY within roughly one hour of Bonn
by train: Bonn, Köln/Cologne, Siegburg, Sankt Augustin, Troisdorf, Hennef,
Brühl, Wesseling, Hürth, Bornheim, Königswinter, Bad Honnef, Remagen,
Andernach, Koblenz, Euskirchen, Leverkusen, Bergisch Gladbach, Dormagen,
Neuss, Düsseldorf. Score 0 to 15 for on-site/hybrid roles anywhere else in
Germany (Berlin, Munich, Hamburg, Frankfurt, Aachen, Dortmund, ... — a
weekly office day there does not work alongside Bonn lectures). Genuinely
remote-within-Germany roles are fully eligible.

═══════════════════════════════════════════════════════════════
HARD CAPS — these set a MAXIMUM score the role can receive.
Apply the LOWEST applicable cap. Boosts cannot exceed the cap.
═══════════════════════════════════════════════════════════════

CAP AT 15 if the role is the WRONG employment form:
- Full-time (Vollzeit) regular employment, Ausbildung, trainee/graduate
  programme, thesis (Abschlussarbeit/Bachelorarbeit/Masterarbeit), PhD,
  or freelance. ACCEPTABLE forms are: Werkstudent / working student /
  studentische Hilfskraft (HiWi), internships (Praktikum, Pflichtpraktikum,
  Praxissemester, internship), AND part-time (Teilzeit) regular roles of
  roughly 20h/week or less.
- Do NOT apply this cap merely because the ad is vague about hours. The
  pre-filter already guarantees every job you see signalled a student
  role, an internship, or part-time work somewhere in its text. Only apply it when the ad
  EXPLICITLY states a form from the excluded list above.

CAP AT 20 if ANY of these are true:
- Role requires fluent/business/native German (C1+), even if the JD is written in English. Look for: "fluent German", "verhandlungssicheres Deutsch", "Mandatory: English and German", "business-fluent German", "German native". Sherwan is B1.
- Role requires 5+ years professional experience. Sherwan has zero.
- Role title contains: Senior, Lead, Principal, Staff, Head of, Director, VP, Vice President, Manager, Architect (without "junior"/"associate" qualifier).
- Role requires a COMPLETED Master's or PhD. Sherwan has a completed B.Sc. and is an ENROLLED M.Sc. student from October 2026 — ads asking for "enrolled Bachelor's or Master's students" are his exact situation and must NOT trigger this cap.
- Role is in a domain Sherwan has zero exposure to AND requires domain knowledge: energy markets / power trading, public sector / municipal, embedded systems / firmware / FPGA, hardware engineering, biomedical / clinical / pharma, manufacturing OT / SCADA / PLC, financial risk / actuarial.

CAP AT 35 if ANY of these are true:
- Role requires 3-4 years professional experience.
- Role is "Data Scientist" or "ML Scientist" with research-track framing (publications, novel research, advanced degree mentioned as preferred).
- Primary stack is non-Python (Java, Go, Rust, .NET, Scala, C++) without Python listed as acceptable.
- Role is enterprise data engineering on legacy stacks (SSIS, SQL Server stored procedures, SAP BW, Informatica) without ML/AI work.
- Role is web analytics / GA4 / tag management primary focus without ML.

CAP AT 50 if ANY of these are true:
- Role requires 2 years professional experience.
- Role is general DevOps / platform engineering / Kubernetes primary focus.
- Role is general full-stack web dev with no data/AI component.

═══════════════════════════════════════════════════════════════
TARGET ROLE LADDER — four EQUAL tracks: AI · ML · Data Science · Data Analyst
Score generously when these match. The candidate is equally interested in
AI engineering, machine learning, data science, AND data analytics — do NOT
favour AI roles over data-analyst or data-science roles. All four tracks below
start at the SAME score.
═══════════════════════════════════════════════════════════════

All tiers assume the role IS an acceptable form — a Werkstudent/working-
student role, an internship, or a part-time (Teilzeit) role. Anything else
is already capped at 15. The three forms are scored EQUALLY: judge on the
work, the location and the language, not on the contract type. A paid
internship or a 20h/week Teilzeit data role in the commute belt is as good
as a Werkstudent role. A part-time role in a track uses that track's tier.

TIER 1 (start at 80, then apply caps and adjustments) — Werkstudent in ANY
of the 4 tracks:
  AI track:
    - Werkstudent AI / Working Student AI Engineering / GenAI / LLM
    - Working Student AI Agents / Agentic AI / RAG / Conversational AI
  ML track:
    - Werkstudent Machine Learning / ML Engineering / MLOps
    - Werkstudent Computer Vision / NLP / Deep Learning
  Data Science track:
    - Werkstudent Data Science / Working Student Data Scientist
  Data Analyst track:
    - Werkstudent Data Analytics / BI / Reporting / Analytics Engineering
    - Werkstudent Business Analysis with a clear data/SQL/Python focus
  (studentische Hilfskraft / HiWi postings in these fields count equally)

TIER 2 (start at 70, then apply caps and adjustments):
- Werkstudent software engineering with a real data/AI component
  (e.g. Werkstudent Softwareentwicklung Python, backend for an ML product).

TIER 3 (start at 60, then apply caps and adjustments):
- Werkstudent IT / software engineering without a data/AI component but with
  Python or modern web stack — acceptable to stay funded, less CV value.

OFF-TARGET (CAP AT 25 — a hard maximum, boosts cannot lift it):
- A student role with NO data/AI/software content. The 2026-08-16
  digest carried an outlet sales assistant, a retail sales operation
  role and a quality-management role as near misses because off-target
  used to START at 40; anything above 35 reaches the owner's inbox, so
  off-target must sit strictly below that line.
- Examples: retail/outlet/cashier, sales/Vertrieb, marketing/
  communications, HR/recruiting, office management, event management,
  pure QM, pure controlling/accounting, legal, logistics floor work.
- Werkstudent IT/software WITHOUT data content is NOT off-target — it
  is Tier 3 above. Off-target means no technical content at all.

═══════════════════════════════════════════════════════════════
BOOSTS — additive, but never exceed the applicable cap.
IMPORTANT: exactly ONE track-specific boost block applies per job (the one
matching its track). Do not let a job's track determine whether ANY boost is
reachable — each track has an equal-value +15 and +10 boost available.
Never apply an AI-track boost to a non-AI role just because AI is trendy.
═══════════════════════════════════════════════════════════════

TRACK-SPECIFIC BOOSTS (pick the block matching the job's track — each track
has an equally-weighted +15 and +10 available, so no track has a structural
ceiling advantage over another):

  AI track — +15 if JD mentions: Claude Code, Claude Agent SDK, MCP, custom
    skills, hooks, plan/execute/review loop, LangGraph, LangChain agents,
    agentic systems, agent workflows, tool calling, function calling.
    (Matches Sherwan's iseremo work and his own agentic projects.)
  AI track — +10 if JD mentions: LLM evaluation frameworks, RAG, vector
    databases, prompt engineering, fine-tuning (LoRA/QLoRA), Anthropic API,
    OpenAI API, Hugging Face, hallucination detection/evaluation.

  ML track — +15 if JD mentions: production ML pipelines, model deployment/
    serving, MLOps tooling (MLflow, Weights & Biases, Kubeflow), CI/CD for
    ML, imbalanced classification, ensemble methods (XGBoost/boosting).
    (Matches Sherwan's CSRBoost forensic-audit project — imbalanced-learn,
    75k configurations, reproducibility engineering.)
  ML track — +10 if JD mentions: PyTorch, TensorFlow, scikit-learn, computer
    vision, transformers, transfer learning, hyperparameter tuning,
    cross-validation, model reproducibility/benchmarking.

  Data Science track — +15 if JD mentions: experimentation / A-B testing,
    statistical rigor, hypothesis testing, causal inference, recommender
    systems, research replication/reproducibility.
    (Matches Sherwan's FUS recommender-system replication — 10-fold CV,
    matched paper metrics to 4 decimal places.)
  Data Science track — +10 if JD mentions: NumPy/Pandas-heavy analysis,
    feature engineering, model evaluation methodology, Jupyter-based
    research workflows, published/reproducible results.

  Data Analyst track — +15 if JD mentions: SQL-heavy analysis, dashboarding
    (Tableau, Power BI, Looker), stakeholder reporting, KPI definition,
    data storytelling for non-technical audiences.
    (Matches Sherwan's Google Advanced Data Analytics cert — Tableau,
    regression, statistics — and IBM SQL for Data Science cert.)
  Data Analyst track — +10 if JD mentions: business intelligence tooling,
    ETL/data pipeline basics, A/B test reporting, data quality/cleaning,
    cohort or funnel analysis.

UNIVERSAL BOOSTS (apply regardless of track):

+8: Working language is explicitly English / international team / "we work in English"

+8: Located in Bonn or Köln — walkable/20-minute commute from his university city, zero friction alongside lectures

+5: Genuinely remote within Germany, or explicitly flexible hours / "flexible Arbeitszeiten passend zum Stundenplan" — fits around a lecture schedule

+5: Visa sponsorship not required (Sherwan has full work auth)

+5: Company is an English-first tech/data/AI startup known for strong engineering culture (e.g. Aleph Alpha, deepset, parloa, Helsing, Black Forest Labs, n8n, Langfuse, Cohere, Mistral, Hugging Face, Stability for AI; Celonis, Personio, N26, DeepL, Contentful for broader tech/data) — this boost is NOT AI-exclusive, apply it to any track if the company fits the profile.

═══════════════════════════════════════════════════════════════
PENALTIES — subtractive, applied AFTER caps and boosts
═══════════════════════════════════════════════════════════════

-15: Ad restricts to students of a DIFFERENT field ("enrolled in business administration / mechanical engineering / law") with no computer-science/AI/data option listed

-10: Ad demands more than 20h/week during the lecture period. NOTE: this does NOT apply to internships, which are commonly full-time for a fixed block and can be taken in the semester break or as a Pflichtpraktikum

-10: Role description shows clear professional-level expectations despite the student title (e.g. "ownership of large systems", "on-call", "drive technical roadmap")

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════

Return ONLY a valid JSON array. No preamble, no markdown fences, no commentary.
Each entry MUST have: {{"index": N, "score": 0-100, "reason": "ONE concrete sentence: identify the cap that applied OR the tier match, and the single biggest factor."}}

For any job you score >= 55, ALSO include two tailoring fields (omit them for lower scores):
- "missing_keywords": array of 4-8 EXACT terms/skills/tools the job description requires that are absent or weak in the candidate's CV profile above (e.g. ["Airflow", "dbt", "Tableau", "stakeholder management"]). These are what he should add/emphasise before applying so his CV passes the ATS keyword filter for THIS job. Use the job's own wording.
- "cv_hint": ONE short concrete sentence telling him how to re-angle his CV for this specific role (e.g. "Lead with the FUS recommender replication and frame it as production-style A/B evaluation, and add a SQL/Tableau line to match their BI stack.").

Examples of good reasons:
- "Tier 1 Werkstudent AI Agents match, LangGraph mentioned (+15), English-first (+8), Köln (+8) → 90"
- "Tier 1 Werkstudent Data Science match, scikit-learn/XGBoost (+15), remote in Germany (+5) → 84"
- "Tier 1 Werkstudent BI match, SQL + Power BI dashboarding (+15), Bonn (+8) → 85"
- "Capped at 15: full-time Junior Data Scientist — wrong employment form"
- "Capped at 15: on-site Werkstudent in Hamburg — not commutable from Bonn, no remote option"
- "Capped at 20: explicitly requires fluent German for DACH customer-facing role"
- "Off-target: Werkstudent marketing, no data component → 30"

Be terse. The reason exists so Sherwan can audit your decisions, not so you can be polite.
"""

USER_TEMPLATE = """Score these {n} jobs. Return a JSON array with one object per job in the same order:
[{{"index": 0, "score": 75, "reason": "one-line reason"}}, ...]

JOBS:
{jobs_block}"""


def _format_job_block(jobs: list[dict]) -> str:
    lines = []
    for i, j in enumerate(jobs):
        lines.append(
            f"[{i}] Title: {j['title']}\n"
            f"    Company: {j['company']}\n"
            f"    Location: {j['location']}\n"
            f"    Description snippet: {j['description'][:1500] or 'N/A'}"
        )
    return "\n\n".join(lines)


def _apply_scores(batch: list[dict], raw: str) -> None:
    """Parse a scoring response (structured output or legacy fenced JSON) and
    write score/reason/tailoring fields onto the batch in place."""
    raw = raw.strip()
    # Legacy fence-stripping kept as a harmless fallback
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    scores = data["scores"] if isinstance(data, dict) else data
    for item in scores:
        idx = item["index"]
        if 0 <= idx < len(batch):
            batch[idx]["score"] = item.get("score", 0)
            batch[idx]["reason"] = item.get("reason", "")
            # Optional tailoring fields (present for score >= 55). Kept only
            # when non-empty so a later pass can't blank out an earlier one.
            mk = item.get("missing_keywords")
            if isinstance(mk, list) and mk:
                batch[idx]["missing_keywords"] = [str(x) for x in mk][:8]
            hint = item.get("cv_hint")
            if isinstance(hint, str) and hint.strip():
                batch[idx]["cv_hint"] = hint.strip()


def _finalize_scores(batch: list[dict]) -> list[dict]:
    """Guarantee every job leaves with a score. setdefault keeps this safe for
    the Sonnet re-score stage too: on a Stage-3 failure the jobs already
    carry their Haiku score/reason and are left untouched, while a Stage-2
    failure yields score 0 instead of a missing key that would crash main."""
    for j in batch:
        j.setdefault("score", 0)
        j.setdefault("reason", "[scorer-failed] no score returned")
    return batch


def _score_batch(batch: list[dict], model: str = HAIKU_MODEL,
                 cv_profile: str = CV_PROFILE) -> list[dict]:
    jobs_block = _format_job_block(batch)
    prompt = USER_TEMPLATE.format(n=len(batch), jobs_block=jobs_block)

    # Two attempts: one transient API error / malformed response must not cost
    # a batch of jobs.
    for attempt in (1, 2):
        try:
            response = _client().messages.create(
                model=model,
                max_tokens=1500,
                system=_system_blocks(cv_profile),
                messages=[{"role": "user", "content": prompt}],
                output_config=_OUTPUT_CONFIG,
                **_thinking_kwargs(model),
            )
            _record_usage(model, getattr(response, "usage", None))
            _apply_scores(batch, response.content[0].text)
            break

        except Exception as e:
            print(f"  [Scorer] batch failed (attempt {attempt}/2): {e}")
            if attempt == 1:
                time.sleep(3)

    return _finalize_scores(batch)


def _score_groups_via_batch_api(groups: list[tuple[list[dict], str, str]]) -> bool:
    """
    Score [(jobs, model, cv_profile), ...] through the Message Batches API —
    50% off every token. Returns True if the batch ran; False means the caller
    must fall back to the sync path (nothing was scored). Groups whose entries
    error inside an otherwise-successful batch are re-scored synchronously.
    """
    if os.environ.get("DISABLE_BATCH_API"):
        return False
    try:
        requests = []
        for gi, (grp, model, profile) in enumerate(groups):
            prompt = USER_TEMPLATE.format(n=len(grp), jobs_block=_format_job_block(grp))
            requests.append({
                "custom_id": f"g{gi}",
                "params": {
                    "model": model,
                    "max_tokens": 1500,
                    "system": _system_blocks(profile, long_ttl=True),
                    "messages": [{"role": "user", "content": prompt}],
                    "output_config": _OUTPUT_CONFIG,
                    **_thinking_kwargs(model),
                },
            })
        batch = _client().messages.batches.create(requests=requests)
        print(f"  [BatchAPI] submitted {len(requests)} requests as {batch.id} (50% token discount)")

        waited = 0
        timed_out = False
        while True:
            time.sleep(_BATCH_POLL_SECONDS)
            waited += _BATCH_POLL_SECONDS
            batch = _client().messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            if waited >= _BATCH_TIMEOUT_SECONDS:
                # Cancel the stragglers, but HARVEST whatever already finished.
                # Returning False here used to discard completed work: one real
                # run had 8 of 15 groups succeed, cancelled all 15, then
                # re-scored everything synchronously — paying for the batch and
                # again at full price ($0.22 instead of the usual $0.07).
                print(f"  [BatchAPI] timeout after {waited}s — cancelling stragglers, "
                      f"keeping whatever finished")
                try:
                    _client().messages.batches.cancel(batch.id)
                    # Cancellation is not instant; results are only readable
                    # once the batch reports 'ended'.
                    for _ in range(15):
                        time.sleep(4)
                        batch = _client().messages.batches.retrieve(batch.id)
                        if batch.processing_status == "ended":
                            break
                except Exception as e:
                    print(f"  [BatchAPI] cancel/settle failed: {e}")
                timed_out = True
                break

        ok, redo = 0, []
        seen_ids = set()
        for result in _client().messages.batches.results(batch.id):
            seen_ids.add(result.custom_id)
            gi = int(result.custom_id[1:])
            grp, model, profile = groups[gi]
            if result.result.type == "succeeded":
                try:
                    msg = result.result.message
                    text = next(b.text for b in msg.content if b.type == "text")
                    _record_usage(model, getattr(msg, "usage", None), batched=True)
                    _apply_scores(grp, text)
                    ok += 1
                except Exception as e:
                    print(f"  [BatchAPI] parse failed for {result.custom_id}: {e}")
                    redo.append(gi)
            else:
                redo.append(gi)
        # Entries the batch never returned at all also need the sync fallback
        redo.extend(gi for gi in range(len(groups)) if f"g{gi}" not in seen_ids)

        for gi in redo:
            grp, model, profile = groups[gi]
            _score_batch(grp, model=model, cv_profile=profile)
        for grp, _, _ in groups:
            _finalize_scores(grp)
        print(f"  [BatchAPI] {'timed out' if timed_out else 'done'} after ~{waited}s: "
              f"{ok}/{len(groups)} groups from the batch (50% off), "
              f"{len(redo)} re-scored synchronously")
        return True

    except Exception as e:
        print(f"  [BatchAPI] unavailable ({e}) — using sync scoring")
        return False


def score_jobs(jobs: list[dict]) -> list[dict]:
    print(f"Scoring {len(jobs)} jobs...")

    # ── Stage 1: hard pre-screening (no Claude API call) ──────────────────────
    to_score: list[dict] = []
    prescreened_out = 0
    for j in jobs:
        disqualified, reason, category = _hard_disqualify(j)
        if disqualified:
            j["score"] = 0
            j["reason"] = f"[Pre-screened] {reason}"
            j["disqualified_category"] = category
            prescreened_out += 1
        else:
            j["disqualified_category"] = ""
            to_score.append(j)

    print(f"  Pre-screened out: {prescreened_out} | Sending to Claude: {len(to_score)}")

    # Tag each survivor with its track so both scoring stages route it to the
    # matching CV profile. Jobs are grouped by track and each group scored with
    # its own profile — a DS job is judged against the DS-framed CV, etc.
    for j in to_score:
        j["_track"] = _classify_track(j)
    from collections import Counter
    tcount = Counter(j["_track"] for j in to_score)
    print(f"  Track split: " + ", ".join(f"{k}={v}" for k, v in sorted(tcount.items())))

    def _by_track(pool):
        groups = {}
        for j in pool:
            groups.setdefault(j.get("_track", "AI"), []).append(j)
        return groups

    def _make_groups(pool: list[dict], model: str) -> list[tuple[list[dict], str, str]]:
        import random
        out = []
        for track, group in _by_track(pool).items():
            profile = _TRACK_PROFILES.get(track, CV_PROFILE_AI)
            # P2 debias: batches used to form in stable scrape order, so a
            # job's neighbours were always the same source's postings — and a
            # pointwise judge's absolute score is measurably contaminated by
            # its batch context (arXiv:2506.22316, 2406.07791). Shuffling
            # breaks the systematic pairing; a fresh seed per run means any
            # residual composition effect averages out across runs instead of
            # biasing the same jobs the same way every time.
            group = list(group)
            random.shuffle(group)
            for i in range(0, len(group), BATCH_SIZE):
                out.append((group[i : i + BATCH_SIZE], model, profile))
        return out

    # ── Stage 2: Haiku scoring, per-track profile (cheap bulk pass) ────────────
    # Large runs go through the Message Batches API (50% off every token, and
    # a cron pipeline doesn't mind minutes of polling); small runs and any
    # batch failure use the sync path.
    groups = _make_groups(to_score, HAIKU_MODEL)
    if len(to_score) < _BATCH_API_MIN_JOBS or not _score_groups_via_batch_api(groups):
        done = 0
        for batch, model, profile in groups:
            _score_batch(batch, model=model, cv_profile=profile)
            done += len(batch)
            time.sleep(1)
            print(f"  Scored {done}/{len(to_score)}")
    scored = [j for grp, _, _ in groups for j in grp]

    SCORE_BATCH_MEANS.clear()
    for grp, _, _ in groups:
        vals = [j.get("score", 0) for j in grp]
        if vals:
            SCORE_BATCH_MEANS.append(round(sum(vals) / len(vals), 1))

    # ── Stage 3: Sonnet re-score of finalists (budget mode), per-track profile ─
    # Only jobs Haiku rates >= SONNET_RESCORE_FLOOR get the expensive second
    # opinion. Scoring overwrites score/reason in place on success and leaves
    # the Haiku values untouched on any failure, so this stage can never lose
    # a job — worst case it just keeps the Haiku ranking.
    finalists = [jb for jb in scored if jb.get("score", 0) >= SONNET_RESCORE_FLOOR]
    if finalists:
        print(f"  [Sonnet] re-scoring {len(finalists)} finalists (Haiku >= {SONNET_RESCORE_FLOOR})")
        s3_groups = _make_groups(finalists, SONNET_MODEL)
        if len(finalists) < _BATCH_API_MIN_JOBS or not _score_groups_via_batch_api(s3_groups):
            for batch, model, profile in s3_groups:
                _score_batch(batch, model=model, cv_profile=profile)
                time.sleep(1)
        print(f"  [Sonnet] done")

    # Merge Claude-scored jobs with pre-screened (score=0) jobs
    scored_ids = {j["id"] for j in scored}
    all_results = scored + [j for j in jobs if j["id"] not in scored_ids]
    return all_results
