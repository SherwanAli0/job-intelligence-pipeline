"""
application_kit.py — pre-draft answers to real screening questions (feature B4).

Screening questions are where junior applications die (wrong salary number,
blank "why us", hesitant visa answer). For each digest job hosted on a
Greenhouse board (the largest, cleanest public question API), this fetches the
ACTUAL custom questions and drafts answers with Haiku — so applying takes 3
minutes instead of 30.

A reusable answer bank (answers.json) stores answers keyed by normalized
question text, so a question seen once ("What are your salary expectations?")
is answered instantly and for free on every future job.

Cost: at most one Haiku call per run (all unknown questions batched). Wrapped
so any failure just means jobs arrive without a kit.
"""

import json
import os
import re
from pathlib import Path

import requests

BANK_FILE = Path("answers.json")  # gitignored — persisted via Actions cache, never committed
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126"}
MAX_KIT_JOBS = 12  # only build kits for the top digest jobs (bounded cost)


def _load_facts() -> str:
    """
    The personal fact sheet (contact details, salary expectation, availability)
    lives OUTSIDE this public repo: the APPKIT_FACTS repo secret in CI, or the
    gitignored personal/facts.md locally. Without it, drafting is skipped.
    """
    env = os.environ.get("APPKIT_FACTS", "").strip()
    if env:
        return env
    p = Path("personal") / "facts.md"
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""

# ── Fact gate ────────────────────────────────────────────────────────────────
# The drafting prompt says "using ONLY these facts", and a model can still slip
# a number in: "5 years", a wrong date, an e-mail address. These answers go into
# real applications, so each draft is checked deterministically: every number,
# date, e-mail address and URL in it must appear in the fact sheet. An answer
# that fails is not banked; the question simply stays unanswered.
_RE_FACT_TOKEN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"  # e-mail address (anchored: linear time)
    r"|https?://[^\s,;)]+"                       # URL
    r"|\d[\d.,/:%-]*\d|\d",                      # numbers, dates, ranges, years, percentages
    re.IGNORECASE,
)


def _fact_norm(s: str) -> str:
    s = (s or "").lower()
    return re.sub(r"(?<=\d)[.,](?=\d{3}(?!\d))", "", s)          # 45.000 == 45,000


def ungrounded_claims(answer: str, facts: str) -> list[str]:
    """Numbers, dates, e-mail addresses and URLs in `answer` that the fact
    sheet does not contain."""
    f = _fact_norm(facts)
    bad = []
    for m in _RE_FACT_TOKEN.finditer(answer or ""):
        tok = m.group(0).rstrip(".,;:)-/")
        if not tok:
            continue
        # a WHOLE token: "5" must not be found inside "45000", and "1" must not
        # be the leading digit of "1.8"; an address or URL must match to its end
        if "@" in tok or tok.lower().startswith("http"):
            pat = r"(?<![\w@./-])" + re.escape(_fact_norm(tok)) + r"(?![\w@-]|\.\w)"
        else:
            pat = r"(?<![\d.,@])" + re.escape(_fact_norm(tok)) + r"(?!\d|[.,]\d)"
        if not re.search(pat, f):
            bad.append(tok)
    return bad


# Boilerplate question labels we never draft (the form auto-fills these).
_SKIP_LABELS = (
    "first name", "last name", "full name", "email", "phone", "resume", "cv",
    "cover letter", "linkedin", "website", "github", "portfolio", "location",
    "how did you hear", "are you legally", "consent", "gdpr", "privacy",
)

_GH_URL_RE = re.compile(r"greenhouse\.io/([^/?#]+)/jobs/(\d+)", re.IGNORECASE)
_GH_JID_RE = re.compile(r"[?&]gh_jid=(\d+)")


def _norm_q(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().lower()).rstrip("?:. ")


def load_bank() -> dict:
    import storage
    try:
        raw = storage.read_text(str(BANK_FILE))
    except storage.StorageUnavailable as e:
        # The answer bank is a convenience; an unreadable one must not stop
        # the digest. (It is also not saved over in that case: enrich_with_kits
        # only writes when it has added an answer.)
        print(f"  [AppKit] answer bank unavailable: {e}")
        return {}
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def save_bank(bank: dict) -> None:
    import storage
    storage.write_text(str(BANK_FILE), json.dumps(bank, indent=2, ensure_ascii=False))


def _greenhouse_questions(url: str) -> list[str]:
    """Return the substantive (non-boilerplate) custom question labels, or []."""
    m = _GH_URL_RE.search(url or "")
    if not m:
        return []
    board, jid = m.group(1), m.group(2)
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}",
            params={"questions": "true"}, headers=HEADERS, timeout=12,
        )
        if r.status_code != 200:
            return []
        out = []
        for q in r.json().get("questions", []) or []:
            label = (q.get("label") or "").strip()
            if not label or any(s in label.lower() for s in _SKIP_LABELS):
                continue
            out.append(label)
        return out[:8]
    except Exception:
        return []


def enrich_with_kits(jobs: list[dict]) -> None:
    """
    For up to MAX_KIT_JOBS Greenhouse-hosted jobs, attach j['app_kit'] = list of
    {q, a}. Answers come from the bank first; unknowns are drafted in ONE Haiku
    call and saved back to the bank. Mutates in place; never raises.
    """
    # The fact sheet and the banked answers were written for Werkstudent roles
    # ("hourly Werkstudent rate", "available alongside my M.Sc."). A full-time
    # role would get those answers, which are wrong for it. Until the owner
    # adds the line "FULLTIME_KIT: yes" to the fact sheet (after covering full-
    # time availability and pay in it), full-time jobs get no screening kit.
    facts = _load_facts()
    fulltime_ok = bool(re.search(r"^\s*fulltime_kit\s*:\s*yes\b", facts, re.IGNORECASE | re.MULTILINE))
    targets = [j for j in jobs[:MAX_KIT_JOBS]
               if _GH_URL_RE.search(j.get("url", "") or "")
               and (fulltime_ok or j.get("_form") != "full-time")]
    if not targets:
        return

    bank = load_bank()
    # Collect questions per job + the set of unknowns to draft.
    per_job, unknown = {}, {}
    for j in targets:
        qs = _greenhouse_questions(j["url"])
        if not qs:
            continue
        per_job[j["id"]] = qs
        for q in qs:
            nq = _norm_q(q)
            if nq not in bank:
                unknown[nq] = q  # keep original wording for the prompt

    # Draft all unknown answers in one Haiku call.
    added = 0
    if unknown and not facts:
        print("  [AppKit] no fact sheet (APPKIT_FACTS secret / personal/facts.md) "
              "— skipping answer drafting")
    elif unknown:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            qlist = list(unknown.values())
            numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(qlist))
            prompt = (
                "Draft concise, honest answers to these job-application screening "
                "questions for the candidate, using ONLY these facts:\n"
                f"{facts}\n"
                "Rules: 1-2 sentences each; first person; confident, not apologetic; "
                "if a fact isn't given, answer generically without inventing specifics. "
                "For yes/no eligibility questions answer directly.\n\n"
                f"QUESTIONS:\n{numbered}"
            )
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
                # Structured output: guaranteed-valid JSON, no fence-stripping
                output_config={"format": {"type": "json_schema", "schema": {
                    "type": "object",
                    "properties": {"answers": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"index": {"type": "integer"},
                                       "answer": {"type": "string"}},
                        "required": ["index", "answer"],
                        "additionalProperties": False,
                    }}},
                    "required": ["answers"],
                    "additionalProperties": False,
                }}},
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                raw = raw[4:] if raw.startswith("json") else raw
            data = json.loads(raw.strip())
            for item in (data["answers"] if isinstance(data, dict) else data):
                i = item.get("index")
                a = (item.get("answer") or "").strip()
                if isinstance(i, int) and 0 <= i < len(qlist) and a:
                    bad = ungrounded_claims(a, facts)
                    if bad:
                        print(f"  [AppKit] answer to {qlist[i][:50]!r} dropped: "
                              f"{bad[:3]} not in the fact sheet")
                        continue
                    bank[_norm_q(qlist[i])] = a
                    added += 1
            if added:                       # never write the bank back when nothing was added
                save_bank(bank)
        except Exception as e:
            print(f"  [AppKit] drafting failed: {e}")

    # Attach kits from the (now-updated) bank.
    n = 0
    for j in targets:
        qs = per_job.get(j["id"], [])
        kit = [{"q": q, "a": bank.get(_norm_q(q), "")} for q in qs]
        kit = [x for x in kit if x["a"]]
        if kit:
            j["app_kit"] = kit
            n += 1
    print(f"  [AppKit] attached screening-answer kits to {n} jobs "
          f"({len(unknown)} new answers drafted, bank now {len(bank)})")
