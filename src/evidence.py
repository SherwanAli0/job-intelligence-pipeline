"""
evidence.py - retrieval-augmented "evidence" layer.

For each job that reaches scoring this module (1) pulls the requirements out of
the job description, (2) retrieves the candidate's matching evidence from a
small private corpus, and (3) labels every requirement met, partial or gap. The
scorer prompt then receives those matches and is told to rely only on them, and
the digest shows a compact requirement table.

Design notes
  * OFF by default. JOBHUNTER_EVIDENCE=1 turns it on, so production behaviour is
    unchanged until the owner enables it.
  * Privacy. The corpus is built at runtime from the private profile (candidate
    facts, the track profiles) and optional private text files named in
    JOBHUNTER_EVIDENCE_FILES. Nothing here logs, prints or persists corpus text;
    only counts are printed. Matches live on the in-memory job dicts and are
    never written to seen_jobs.json, postings archives or stats.
  * Dependency free. The default retriever is BM25 in pure Python, so the AWS
    Fargate image does not grow. An embedding backend (sentence-transformers) is
    optional: JOBHUNTER_EVIDENCE_BACKEND=embeddings. It is imported lazily and
    the module falls back to BM25 when the package is missing. No code path in
    this module touches the network except the embedding model's own loader.
  * Deterministic. Requirement extraction is rules and a skills vocabulary, no
    LLM call, so it costs nothing and is testable offline.

Status thresholds (documented, and what eval_retrieval.py measures)
  Every retriever reports a confidence in [0, 1] for the best chunk.
    BM25 coverage: share of the requirement's term weight (idf) found in the
      chunk. met >= 0.60, partial >= 0.34, otherwise gap.
    Embeddings (cosine): met >= 0.55, partial >= 0.40, otherwise gap.
  Language requirements ("German C1") are checked by level instead: a chunk that
  states a lower level for that language is a gap, however similar the words.
"""

from __future__ import annotations

import math
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

ENV_FLAG = "JOBHUNTER_EVIDENCE"
ENV_BACKEND = "JOBHUNTER_EVIDENCE_BACKEND"          # bm25 (default) | embeddings
ENV_EMBED_MODEL = "JOBHUNTER_EVIDENCE_EMBED_MODEL"
ENV_FILES = "JOBHUNTER_EVIDENCE_FILES"              # private text files, os.pathsep separated

MAX_REQUIREMENTS = 8
MAX_REQ_CHARS = 160
CHUNK_TARGET_CHARS = 280
MIN_CHUNK_CHARS = 40   # fragments shorter than this are merged into the next unit
MAX_FILE_BYTES = 256 * 1024
TOP_K = 3

THRESHOLDS = {
    "bm25": {"met": 0.60, "partial": 0.34},
    "embeddings": {"met": 0.55, "partial": 0.40},
}

STATUSES = ("met", "partial", "gap")


def enabled(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(ENV_FLAG, "")).strip().lower() in ("1", "true", "yes", "on")


# -- Corpus ---------------------------------------------------------------------
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "x"


def _split_text(text: str, target: int = CHUNK_TARGET_CHARS) -> list[str]:
    """Split free text into small chunks: paragraphs and bullet lines are the
    units, short neighbours are merged up to `target`, long units are split at
    sentence ends."""
    units: list[str] = []
    for block in re.split(r"\n\s*\n", str(text or "")):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if any(_RE_BULLET.match(ln) for ln in lines):
            units.extend(_RE_BULLET.sub("", ln).strip() for ln in lines)
        else:
            units.append(" ".join(lines))
    pieces: list[str] = []
    for u in units:
        if not u:
            continue
        if len(u) <= target:
            pieces.append(u)
            continue
        cur = ""
        for sent in re.split(r"(?<=[.!?])\s+", u):
            if cur and len(cur) + 1 + len(sent) > target:
                pieces.append(cur)
                cur = sent
            else:
                cur = (cur + " " + sent).strip()
        if cur:
            pieces.append(cur)
    chunks: list[str] = []
    for p in pieces:
        if chunks and len(chunks[-1]) < MIN_CHUNK_CHARS and len(chunks[-1]) + 1 + len(p) <= target:
            chunks[-1] = chunks[-1] + " " + p
        else:
            chunks.append(p)
    return chunks


def make_chunk(cid: str, source: str, text: str) -> dict:
    return {"id": cid, "source": source, "text": text}


def chunk_document(label: str, source: str, text: str) -> list[dict]:
    """Chunks with stable ids `<label>:<nn>` (position in the document)."""
    base = _slug(label)
    return [make_chunk("%s:%02d" % (base, i + 1), source, t)
            for i, t in enumerate(_split_text(text))]


def build_corpus(facts: dict | None = None, profiles: dict | None = None,
                 documents: dict | None = None) -> list[dict]:
    """Corpus from private inputs. Ids are stable across runs for the same input.

    facts      candidate_facts mapping; one chunk each, id `fact:<name>`.
    profiles   track profile texts; ids `profile-<name>:<nn>`; chunks whose text
               repeats an earlier one (the shared text recurs in every track)
               are dropped.
    documents  {label: text} for optional CV or project text; ids `<label>:<nn>`.
    """
    corpus: list[dict] = []
    for name in sorted(facts or {}):
        value = str((facts or {})[name]).strip()
        if value:
            label = str(name).replace("_", " ")
            corpus.append(make_chunk("fact:" + _slug(name).replace("-", "_"),
                                     "candidate fact: " + label, "%s: %s" % (label, value)))
    seen = {_norm_key(c["text"]) for c in corpus}
    for label in sorted(documents or {}):
        for c in chunk_document(label, "document " + label, documents[label]):
            if _norm_key(c["text"]) not in seen:
                seen.add(_norm_key(c["text"]))
                corpus.append(c)
    for name in sorted(profiles or {}):
        for c in chunk_document("profile-" + str(name), "profile " + str(name), profiles[name]):
            if _norm_key(c["text"]) not in seen:
                seen.add(_norm_key(c["text"]))
                corpus.append(c)
    return corpus


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def load_documents(environ=None) -> dict[str, str]:
    """Optional private CV/project text files from JOBHUNTER_EVIDENCE_FILES.
    Unreadable or oversized files are skipped silently (their paths and
    contents are never printed)."""
    env = os.environ if environ is None else environ
    out: dict[str, str] = {}
    for raw in str(env.get(ENV_FILES, "")).split(os.pathsep):
        raw = raw.strip()
        if not raw:
            continue
        try:
            path = Path(raw)
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            label = _slug(path.stem)
            while label in out:
                label += "-x"
            out[label] = path.read_text(encoding="utf-8")
        except Exception:
            continue
    return out


def load_corpus(environ=None) -> list[dict]:
    """Corpus from the already loaded private profile plus optional files."""
    import config
    return build_corpus(facts=dict(config.CANDIDATE_FACTS),
                        profiles={"AI": config.CV_PROFILE_AI, "ML": config.CV_PROFILE_ML,
                                  "DS": config.CV_PROFILE_DS, "shared": config._CV_SHARED},
                        documents=load_documents(environ))


# -- Tokens ---------------------------------------------------------------------
def _fold(text: str) -> str:
    """Lower-case and strip accents (umlauts lose their dots, sharp s becomes ss)
    so German spellings with and without umlauts meet."""
    text = str(text or "").replace("\u00df", "ss").replace("\u1e9e", "ss")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


_STOP = frozenset(_fold(w) for w in """
a an and are as at be been being by can could do does for from has have having how i if in into is it its
of on or our over per such that the their them then there these they this those to up us via we were what
when where which while who will with within without would you your
experience experienced knowledge strong good solid proven demonstrated ability able skills skill
understanding familiarity familiar working work proficiency proficient excellent very well plus bonus
nice have must should required requirement requirements preferred ideally least year years
build building built develop developing development professional example related field
erfahrung erfahrungen kenntnisse kenntnis kenntnissen sehr gut gute gutes guter guten und oder mit von
vom in im am an auf aus bei nach um zu zum zur fur uber als auch der die das den dem des ein eine einer
einem einen du dich deine dein deiner sie ihre ihr ihrer wir unsere sind ist haben hast verfugst
verfugen vorhanden vorkenntnisse fundierte fundiert idealerweise wunschenswert wunschenswerte
vorteilhaft gerne mindestens mind jahre jahren wie nicht nur bzw etc m w d
""".split())

# Short forms expanded on both the requirement and the corpus side.
_ALIAS = {
    "ml": ["machine", "learning"],
    "nlp": ["natural", "language", "processing"],
    "rag": ["rag", "retrieval", "augmented", "generation"],
    "deutsch": ["german"], "englisch": ["english"],
}
_RE_TOKEN = re.compile(r"\w[\w+#.\-]*[\w+#]|\w")
_RE_CEFR_TOKEN = re.compile(r"^[abc][12]$")


def _stem(tok: str) -> str:
    """Very light suffix stripping (plurals, -ing, -ed, -ation, -ize forms).
    Tokens with digits or symbols are left alone."""
    if not tok.isalpha() or len(tok) <= 4:
        return tok
    if tok.endswith("ies"):
        return tok[:-3] + "y"
    for suf in ("ization", "ation", "izing", "ised", "ized", "ing", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4 and not tok.endswith("ss"):
            tok = tok[:-len(suf)]
            break
    return tok[:-2] if tok.endswith("iz") and len(tok) > 6 else tok


def tokenize(text: str) -> list[str]:
    """Terms for matching. A bare CEFR level (c1, b2) is dropped: a level only
    means something together with its language, which match_requirement checks
    separately, so it must never match another chunk on its own."""
    text = _fold(text).replace("_", " ").replace("/", " ")
    out: list[str] = []
    for raw in _RE_TOKEN.findall(text):
        raw = raw.strip(".-")
        if _RE_CEFR_TOKEN.match(raw):
            continue
        for tok in _ALIAS.get(raw, [raw]):
            if tok and tok not in _STOP:
                out.append(_stem(tok))
    return out


# -- Requirement extraction -------------------------------------------------------
_RE_BULLET = re.compile(r"^\s*(?:[-*•·▪●>]|\d{1,2}[.)])\s+")
_RE_REQ_HEADER = re.compile(
    r"(?i)^\W*(?:requirements?|qualifications?|what you(?:'ll| will)? (?:bring|need)|"
    r"what we(?:'re| are) looking for|who you are|your profile|your skills|you bring|"
    r"you have|must[- ]haves?|minimum qualifications|preferred qualifications|"
    r"nice[- ]to[- ]haves?|skills(?: and experience)?|anforderungen|dein profil|"
    r"ihr profil|dein hintergrund|das bringst du mit|das bringen sie mit)\b[^\n]{0,40}$")
_RE_OTHER_HEADER = re.compile(
    r"(?i)^\W*(?:responsibilit\w*|what you(?:'ll| will) do|your (?:role|tasks?|mission)|"
    r"the role|about (?:us|the (?:role|team|company))|we offer|what we offer|benefits|"
    r"perks|why (?:join|us)|how to apply|our offer|aufgaben|deine aufgaben|wir bieten|"
    r"das bieten wir|ihre aufgaben)\b[^\n]{0,40}$")
# Skills vocabulary: extracted even when the posting has no requirements section.
SKILLS_VOCAB = (
    "python", "sql", "pandas", "numpy", "scikit-learn", "pytorch", "tensorflow", "keras", "spark",
    "pyspark", "airflow", "dbt", "docker", "kubernetes", "aws", "azure", "gcp", "git", "linux",
    "java", "scala", "c++", "c#", "javascript", "typescript", "react", "fastapi", "flask", "django",
    "rest api", "postgresql", "mysql", "mongodb", "snowflake", "bigquery", "tableau", "power bi",
    "excel", "r", "matlab", "statistics", "machine learning", "deep learning", "nlp", "computer vision",
    "llm", "rag", "langchain", "prompt engineering", "mlops", "ci/cd", "terraform", "hugging face",
    "data analysis", "data visualization", "a/b testing", "etl", "data pipelines", "xgboost",
    "time series", "reinforcement learning", "sap",
)
_VOCAB_RES = [(s, re.compile(r"(?i)(?<![\w+#])" + re.escape(s) + r"(?![\w+#])")) for s in SKILLS_VOCAB]

_LEVEL_RANK = {"a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 6}
_LANG_FORMS = {
    "german": "german", "deutsch": "german", "deutschkenntnisse": "german",
    "english": "english", "englisch": "english", "englischkenntnisse": "english",
    "french": "french", "franzosisch": "french", "spanish": "spanish", "spanisch": "spanish",
    "dutch": "dutch", "turkish": "turkish", "arabic": "arabic", "italian": "italian",
    "russian": "russian", "polish": "polish", "japanese": "japanese", "chinese": "chinese",
}
_RE_LANG_MENTION = re.compile(r"\b(" + "|".join(sorted(_LANG_FORMS, key=len, reverse=True)) + r")\b")
_RE_CEFR = re.compile(r"\b([abc][12])\b")
# Level words (accent-folded) with their rank on the CEFR scale above.
_LEVEL_WORDS = (
    (r"native[- ]level|mother tongue|muttersprach\w*|native|bilingual", 6),
    (r"professional working proficiency|full professional proficiency|"
     r"verhandlungssicher\w*|business[- ]fluent|fliessend\w*|fluent|proficient|"
     r"sehr gut\w*|very good|excellent|ausgezeichnet\w*|hervorragend\w*", 5),
    (r"gut\w*|good", 4),
    (r"conversational", 3),
    (r"grundkenntnisse|grundlegend\w*|basic", 2),
)
_RE_LEVEL_WORD = [(re.compile(r"\b(?:" + pat + r")"), rank) for pat, rank in _LEVEL_WORDS]
_RE_END_LEVEL = re.compile(
    r"\b(?:[abc][12]|" + "|".join("(?:%s)" % pat for pat, _ in _LEVEL_WORDS) + r")[ :(\-]*$")


def _level_in(window: str, last: bool):
    """(rank, label) of the level stated in a text window: a CEFR code first, else
    a level word. `last` prefers the one nearest the right end, else the first."""
    codes = list(_RE_CEFR.finditer(window))
    if codes:
        m = codes[-1] if last else codes[0]
        return _LEVEL_RANK[m.group(1)], m.group(1).upper()
    hits = []
    for rx, rank in _RE_LEVEL_WORD:
        hits.extend((rank, m.group(0)) for m in rx.finditer(window))
    return max(hits) if hits else None


def language_requirements(text: str) -> list[tuple[str, int, str]]:
    """[(language, required level rank, display text)] from phrases such as
    'German C1', 'Fliessend Deutsch', 'Deutschkenntnisse C1', 'native-level German'
    or 'professional working proficiency in English'.

    A level counts only next to its own language: the window left of a language
    name stops at the previous language name, the window right of it at the next
    one or the end of the clause. A language named without any level yields
    nothing."""
    t = re.sub(r"\b(mind|min|ca|bzw)\.", r"\1", _fold(text))
    mentions = list(_RE_LANG_MENTION.finditer(t))
    found: dict[str, tuple[int, str]] = {}
    for i, m in enumerate(mentions):
        lang = _LANG_FORMS[m.group(1)]
        if lang in found:
            continue
        lo = max(mentions[i - 1].end() if i else 0, m.start() - 45)
        hi = min(mentions[i + 1].start() if i + 1 < len(mentions) else len(t), m.end() + 30)
        before = re.split(r"[.;\n]", t[lo:m.start()])[-1]
        after = re.split(r"[.;,\n]| and | und | & ", t[m.end():hi])[0]
        level = _level_in(after, last=False) if _RE_CEFR.search(after) else None
        if level is None and _RE_END_LEVEL.search(before):      # "fluent German", "C1 German"
            level = _level_in(before.rstrip(" :(-")[-40:], last=True)
        if level is None:
            level = _level_in(after, last=False)
        if level is None:
            level = _level_in(before, last=True)
        if level:
            rank, label = level
            shown = label if _RE_CEFR.fullmatch(label.lower()) else "(%s)" % label
            found[lang] = (rank, "%s %s" % (lang.capitalize(), shown))
    return [(lang, rank, disp) for lang, (rank, disp) in found.items()]


# -- Language demands: every mention, softening and shared levels (2026-11-15) ------
# language_requirements() above reads the FIRST levelled mention of a language and
# was written for short requirement lines. The German gate needs more: the audit
# of 11-13 Nov 2026 found ads scored up to 82 that demand German far above the
# candidate's level, in wordings the phrase list in scorer.py never matched:
#   "fließende Deutsch\- und gute Englischkenntnisse"   (markdown-escaped hyphen)
#   "fließende Deutschkenntnisse und gute Englischkenntnisse"      (inflection)
#   "Du sprichst verhandlungssicher Deutsch und gut Englisch"      (word order)
#   "sehr gute Ausdrucksweise in Deutsch und Englisch"             (other noun)
#   "sichere Deutschkenntnisse runden dein Profil ab"              (other word)
# language_demands() reads every mention, lets coordinated languages share one
# level ("sehr gute Englisch- und Deutschkenntnisse", "Deutsch und Englisch
# fließend"), and marks a demand soft when the ad calls it a plus.
_DEMAND_FORMS = dict(_LANG_FORMS)
_DEMAND_FORMS.update({
    "deutschkenntnissen": "german", "deutsche sprache": "german", "deutschen sprache": "german",
    "deutscher sprache": "german", "deutschniveau": "german", "deutschsprachig": "german",
    "englischkenntnissen": "english", "englische sprache": "english", "englischen sprache": "english",
    "englischer sprache": "english",
})
_DEMAND_ALT = "|".join(sorted(_DEMAND_FORMS, key=len, reverse=True))
_RE_DEMAND_MENTION = re.compile(r"\b(" + _DEMAND_ALT + r")\b")
# Level words as whole adjectives: "gut" must not fire inside "Gutachten", nor
# "native" inside "cloud-native", nor "sicher" inside "Sicherheit".
_DEMAND_LEVEL_WORDS = (
    (r"native[- ]level|near[- ]native|mother tongue|muttersprach\w*|native|bilingual", 6),
    (r"professional working proficiency|full professional proficiency|full professional|"
     r"verhandlungssicher\w*|business[- ]fluent|business[- ]level|professional[- ]level|"
     r"fliessend\w*|fluently|fluent|fluency|proficient|sehr gut(?:e[mnrs]?)?(?![a-z])|very good|"
     r"excellent|ausgezeichnet\w*|hervorragend\w*|perfekt\w*|exzellent\w*|souveran\w*|"
     r"einwandfrei\w*|sicher(?:e[mnrs]?)?(?![a-z])|advanced|strong|professional proficiency|"
     r"perfect|flawless|impeccable|outstanding", 5),
    (r"gut(?:e[mnrs]?)?(?![a-z])|good|solide[mnrs]?(?![a-z])|solid|fundiert\w*|fortgeschritten\w*|"
     r"upper[- ]intermediate", 4),
    (r"conversational|intermediate", 3),
    (r"grundkenntnisse\w*|grundlegend\w*|basic", 2),
)
_NOT_INSIDE = r"(?<![\w\-])"
_RE_DEMAND_LEVEL = [(re.compile(_NOT_INSIDE + r"(?:" + pat + r")"), rank) for pat, rank in _DEMAND_LEVEL_WORDS]
_DEMAND_LEVEL_ALT = "|".join("(?:%s)" % pat for pat, _ in _DEMAND_LEVEL_WORDS)
_RE_DEMAND_END_LEVEL = re.compile(
    _NOT_INSIDE + r"(?:[abc][12]|" + _DEMAND_LEVEL_ALT + r")(?:[- ]niveau|[- ]level)?[ :(\-]*$")
# Level right after the language name: "Deutsch fließend", "German (fluent)",
# "Deutsch: Muttersprache", "German skills: fluent", "Deutsch auf muttersprachlichem Niveau".
_RE_DEMAND_START_LEVEL = re.compile(
    r"^[ :(\-]*(?:(?:auf|at|on|is|are|ist|sind|a|an|einem|dem|skills?|kenntnisse\w*|sprachkenntnisse\w*|"
    r"language|niveau|level|proficiency|als|as)[ :(\-]+)*" + _NOT_INSIDE
    + r"(?P<level>[abc][12]\b|" + _DEMAND_LEVEL_ALT + r")")
_RE_MD_ESCAPE = re.compile(r"\\([\-*_.()#+!\[\]&>~|`])")       # markdownify writes "Deutsch\- und"
_RE_HARD_BREAK = re.compile(r"[.;!?\n]")
_COORD = r"(?:und|and|&|sowie|oder|or|bzw|as well as)"
_RE_COORD_SPLIT = re.compile(r"\s" + _COORD + r"\s|\s*/\s*")
# What may stand between two languages that share one level: "Deutsch- und
# Englisch", "German and English", "Deutsch/Englisch", "Deutsch, Englisch und".
# Never across a line break: "Sehr gute Englischkenntnisse" and "Deutschkenntnisse"
# on two lines are two list items, and the second one states no level.
_RE_SHARE_GAP = re.compile(
    r"^[^\S\n]*(?:kenntnisse\w*)?[^\S\n]*-?[^\S\n]*(?:,|/|" + _COORD + r")?[^\S\n]*(?:in |der |auf )?\Z")
# "German" is also a nationality ("a solid German company"). A level standing
# BEFORE the bare word counts only when what follows reads as a language skill.
_RE_SKILL_AFTER = re.compile(
    r"^\s*(?:$|[),.;:!?\n(/+]|-?\s*(?:skills?|language|knowledge|speak\w*|proficien\w*|level|fluen\w*|"
    r"kenntnisse\w*|sprach\w*|niveau|is|are|required|needed|mandatory|must|as|" + _COORD + r")\b)")
_SOFT = (
    r"von vorteil|vorteilhaft|wunschenswert|erwunscht|gerne? gesehen|\bplus\b|pluspunkt\w*|"
    r"nice[- ]to[- ]have|\boptional\w*|idealerweise|im idealfall|ideally|kein muss|keine voraussetzung|"
    r"keine pflicht|nicht (?:zwingend |unbedingt )?(?:erforderlich|notwendig|notig|vorausgesetzt)|"
    r"not (?:strictly )?(?:required|necessary|needed|mandatory|essential)|not a (?:must|requirement)|"
    r"\bbeneficial|\badvantage(?:ous)?\b|\bprefer(?:red|able|ably)\b|\bdesirable|\bbonus\b|"
    r"hilfreich|\bhelpful|\bappreciated|willkommen|\bwelcome\b|\basset\b|\bbenefit\b|\bdesired\b|"
    r"would be (?:great|nice|good)|ware (?:schon|toll|super|hilfreich)"
)
_RE_SOFT = re.compile(_SOFT)
_SOFT_BEHIND = _SOFT.replace(
    "idealerweise|im idealfall|ideally|",
    r"(?:idealerweise|im idealfall|ideally)(?=\W*(?:$|[abc][12]\b))|")
assert _SOFT_BEHIND != _SOFT
_RE_SOFT_BEHIND = re.compile(_SOFT_BEHIND)
_RE_SOFT_START = re.compile(r"^\W*(?:" + _SOFT + r")")
_RE_SOFT_END = re.compile(r"(?:" + _SOFT_BEHIND + r")\W*$")
# "keine Deutschkenntnisse", "no German", "you don't need fluent German".
_RE_NEGATED = re.compile(r"(?:\b(?:kein\w*|no|ohne|without|not|nicht|never)|n['’]t)\s+(?:[\w\-]+\s+){0,3}$")
_RE_COORD_LANG = re.compile(
    r"^\s*(?:kenntnisse\w*)?\s*-?\s*(?:/|" + _COORD + r")\s*(?:(?:[abc][12]|" + _DEMAND_LEVEL_ALT
    + r")\s+)?(?:(?:" + _DEMAND_ALT + r")\w*"
    r"|(?:(?:an)?other|further|additional|weitere[nr]?|andere[nr]?) (?:languages?|sprachen|fremdsprachen))")


# -- Flattened lists and levels in nouns (2026-11-16) --------------------------------
# The first production run with the level gate showed two weak spots.
#
# 1. LinkedIn descriptions arrive as ONE line: the page-body fetch joins the text
#    with spaces, so list items lose their breaks. In
#      "... Excellent English and German skills Strong analytical skills ..."
#    the "strong" of the next item was read as the level of German. The outcome
#    was right that day (the ad does ask for excellent German), but the same
#    reading drops an ad that says only "English and German skills".
# 2. "Kommunikations- und Praesentationssicherheit in Deutsch und Englisch"
#    (an internship ad, sent with score 48) states the level in a noun, which a
#    list of adjectives cannot see.
_RE_ITEM_BULLET = re.compile(r"(?:(?<=\s)|^)\*(?=\s)|[•·▪●◦‣∙]")
# Only nouns about speaking and writing, and the bare word. "IT-Sicherheit in ..."
# and "Datensicherheit" are something else.
_NOUN_LEVEL_HEADS = (r"kommunikations|prasentations|verhandlungs|sprach|ausdrucks|stil|formulierungs|"
                     r"gesprachs|korrespondenz|text|rede|schreib|schrift")
_RE_DEMAND_NOUN_LEVEL = re.compile(
    _NOT_INSIDE + r"(?:(?:" + _NOUN_LEVEL_HEADS + r")-? ?(?:und|sowie|oder|&|/) ?)*"
    r"(?:(?:" + _NOUN_LEVEL_HEADS + r")sicherheit (?:im umgang mit|in|im|auf)|sicherheit im umgang mit)"
    r" (?:der |dem |den )?$")
# The bare word counts in front of "... Sprache" only: "Sicherheit in der deutschen Sprache".
# "Schulungen zur Sicherheit in Deutsch und Englisch" is about safety.
_RE_DEMAND_BARE_NOUN = re.compile(_NOT_INSIDE + r"sicherheit in der $")
# A level word right behind the language name can also be the first word of the
# next item. German shows the difference in the ending: "Deutsch fliessend"
# (belongs to the language) against "fliessende ..." (attribute of the noun
# that follows). English words that mean nothing but language skill ("fluent",
# "native") stay with the language; general adjectives ("strong", "excellent",
# "good") count only when the demand ends right there.
_RE_LEVEL_INFLECTED = re.compile(
    r"(?:fliessend|verhandlungssicher|sicher|gut|perfekt|ausgezeichnet|hervorragend|exzellent|einwandfrei|"
    r"muttersprachlich|fortgeschritten|solid|fundiert|souveran|grundlegend)(?:e|er|es|en|em)$")
_RE_LEVEL_GENERAL = re.compile(
    r"^(?:very good|good|excellent|strong|advanced|solid|solide|perfect|flawless|impeccable|outstanding|basic|"
    r"professional[- ]level|business[- ]level)$")
_OWN_NOUN = (r"(?:sprach|fremdsprachen)?kenntnisse\w*|sprachniveau|niveau|level|skills?|knowledge|proficiency|"
             r"command|sprache|language(?: skills)?")
_DEMAND_ENDS = (r"$|[),.;:!?/\n+]|" + _COORD + r"\b|bis\b|to\b|[abc][12]\b|(?:" + _DEMAND_ALT + r")\b|"
                r"speaker|sprecher\w*|sprechen|sprichst|spricht|speak\w*|spoken|beherrsch(?:en|st|t|e)\b|is\b|are\b|ist\b|"
                r"sind\b|required|needed|mandatory|necessary|essential|expected|erforderlich|notwendig|notig|"
                r"vorausgesetzt|voraussetzung|zwingend|unabdingbar|erwartet|pflicht|a must|ein muss|"
                r"in (?:speaking|writing|speech|wort|schrift)|(?:" + _SOFT + r")")
_RE_DEMAND_ENDS = re.compile(r"^\s*(?:" + _DEMAND_ENDS + r")")
_RE_OWN_NOUN_THEN_END = re.compile(r"^\s*(?:" + _OWN_NOUN + r")\b\s*(?:" + _DEMAND_ENDS + r")")
_RE_IN_OTHER_SUBJECT = re.compile(
    r"^\s*(?:in|im)\b(?!\s+(?:der |den |dem |the |both )?(?:" + _DEMAND_ALT + r")\b)")
# Words that may stand between a level and the language name it belongs to:
# nothing but modifiers ("strong written German"), or a phrase that leads to the
# name ("sehr gute Kenntnisse in", "good command of", "sehr gute Sprachkenntnisse").
_RE_GAP_MODIFIERS = re.compile(
    r"^(?:[\s:(\-]*(?:written|spoken|verbal|oral|schriftlich\w*|mundlich\w*|sehr|very|both|sowohl|niveau|level|"
    + _COORD + r")\b)*[\s:(\-]*$")
_RE_GAP_LINK = re.compile(
    r"(?:\b(?:in|im|of|der|des|auf|at|mit|with|fur|for)(?:\s+(?:the|der|den|dem|both|beiden|sowohl))?"
    r"|\b(?:(?:sprach|fremdsprachen)?kenntnisse\w*|sprachniveau|sprachen?|languages?|language skills))[\s:(\-]*$")


# The bare word "German" is also a nationality, so a level in front of it counts
# only when a language skill follows ("fluent German skills", "good German and
# English"). In a flattened list nothing follows but the next item:
#   "... fluent German Strong analytical skills ..."
# The capital letter of that item is the one sign left, so the original spelling
# is read for it. Title-case names ("the German Market", "German Aerospace
# Center") are no item start; the list is short on purpose, because words such
# as "Team", "Data" or "Business" open list items far more often than names.
_RE_BARE_GERMAN = re.compile(r"\bgerman\b", re.I)
_RE_CAPITAL_NEXT = re.compile(
    r"[*_`\\]*[ \t]+[*_`\\]*(?!(?:Market|Law|Company|Companies|Mittelstand|Economy|Government|Federal|"
    r"Aerospace|Cancer|Institute|Universit\w+|Society|Association|Bank|Railway|Embassy|Cent(?:er|re)|"
    r"Chamber|Ministry|Army|Armed|Red|National|Foreign|Headquarters|Subsidiar\w+|Speaking)\b)"
    r"[A-Z][a-z]")


def _german_item_ends(text: str, wanted: int):
    """For every bare word "German" of the original text, in order: does a
    capitalised word follow, the start of the next list item? None when the
    count differs from the mentions found in the folded text (then the
    spelling cannot be matched to them and is not used)."""
    flags = [bool(_RE_CAPITAL_NEXT.match(text, m.end())) for m in _RE_BARE_GERMAN.finditer(text)]
    return flags if len(flags) == wanted else None


_RE_NEXT_IS_LANGUAGE = re.compile(r"^\s*(?:" + _DEMAND_ALT + r")\b")
_RE_LEVEL_GERMAN_PLAIN = re.compile(
    r"^(?:fliessend|verhandlungssicher|sicher|sehr gut|gut|perfekt|ausgezeichnet|hervorragend|exzellent|"
    r"einwandfrei|muttersprach\w*|fortgeschritten|fundiert|souveran|grundkenntnisse\w*)$")
# With nothing but a capital letter as the sign that a language item ended behind "German",
# a general adjective right in front of it is not enough: "Strong German Presence in the DACH
# region", "Excellent German Engineering", "A good German Employer". A word that means nothing
# but language skill is ("Fluent German Team player"), and so is a phrase that leads to the
# name ("Very good knowledge of German Experience with SQL", "Excellent written German ...").
_RE_GAP_LANGUAGE_WORD = re.compile(r"\b(?:written|spoken|verbal|oral|schriftlich\w*|mundlich\w*)\b")


def _opens_next_item(label: str, rest: str) -> bool:
    """True when a level word found right behind a language name belongs to the
    list item that follows, not to the language: "German skills Strong
    analytical skills", "Deutsch Sehr gute Kenntnisse in SQL", "German
    Proficient in MS Office"."""
    if _RE_CEFR.fullmatch(label):
        return False
    if _RE_NEXT_IS_LANGUAGE.match(rest):
        # "German Fluent English", "Deutsch fliessende Englischkenntnisse": the level belongs
        # to the language behind it. Only an uninflected German word stays with the language
        # in front: "Deutsch sehr gut Englisch gut".
        return not _RE_LEVEL_GERMAN_PLAIN.match(label)
    if _RE_DEMAND_ENDS.match(rest) or _RE_OWN_NOUN_THEN_END.match(rest):
        return False
    if _RE_LEVEL_INFLECTED.search(label) or _RE_LEVEL_GENERAL.match(label):
        return True                                   # an attribute of what follows
    return bool(_RE_IN_OTHER_SUBJECT.match(rest))     # "sicher im Umgang mit ...", "proficient in Excel"


_RE_RANGE_JOIN = re.compile(r"\s*(?:or|oder|to|bis|/|-|" + chr(0x2013) + r")\s*$")


def _closest_level(segment: str):
    """(rank, label, end) of the level nearest to the end of `segment`, the text
    in front of a language name. A range ("good to very good", "B2/C1", "native
    or fluent") counts with its upper end. The nearest level, not the highest
    one in the window: in a flattened list an earlier item may open with a
    stronger word ("Strong analytical skills Good German skills")."""
    hits = [(m.start(), m.end(), _LEVEL_RANK[m.group(1)], m.group(1).upper()) for m in _RE_CEFR.finditer(segment)]
    for rx, rank in _RE_DEMAND_LEVEL:
        hits.extend((m.start(), m.end(), rank, m.group(0)) for m in rx.finditer(segment))
    if not hits:
        return None
    start, end, rank, label = max(hits, key=lambda h: (h[1], h[2], h[1] - h[0]))
    while True:                                            # walk a range to its other end
        joined = _RE_RANGE_JOIN.search(segment[:start])
        earlier = [h for h in hits if joined and h[1] == joined.start()]
        if not earlier:
            break
        other = max(earlier, key=lambda h: (h[2], h[1] - h[0]))
        if other[2] > rank:
            rank, label = other[2], other[3]
        start = other[0]
    return rank, label, end


def _level_before(segment: str):
    """(rank, label) of the level nearest to the language name that follows
    `segment`, when the words in between lead to that name. In a flattened list
    the level word of an earlier item ("Strong communication skills German and
    English ...") does not, and nothing is returned."""
    near = _closest_level(segment)
    if near is None:
        return None
    gap = segment[near[2]:]
    if _RE_GAP_MODIFIERS.match(gap) or _RE_GAP_LINK.search(gap):
        return near[0], near[1]
    return None


def _demand_text(text: str) -> str:
    """Folded ad text with markdown escapes and emphasis removed and blanks
    collapsed, so a wording matches however the source formatted it."""
    t = _RE_MD_ESCAPE.sub(r"\1", _fold(text))
    for odd in (0x2010, 0x2011, 0x2012):                   # hyphen variants
        t = t.replace(chr(odd), "-")
    for hidden in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x00AD):  # zero-width and soft hyphen
        t = t.replace(chr(hidden), "")
    t = _RE_ITEM_BULLET.sub("\n", t)                       # a bullet starts a new item
    t = re.sub(r"[*_#`]+", " ", t)
    t = re.sub(r"\b(mind|min|ca|bzw|inkl)\.", r"\1", t)
    # Fixed pairs that are no new item: "in Wort und Schrift", "written and spoken".
    t = re.sub(r"\b(?:in )?wort und schrift\b", " ", t)
    t = re.sub(r"\b(?:schriftlich\w*|mundlich\w*) (?:und|sowie|wie) (?:schriftlich\w*|mundlich\w*)\b", " ", t)
    t = re.sub(r"\b(?:written|verbal|oral|spoken) (?:and|&) (?:written|verbal|oral|spoken)\b", " ", t)
    t = re.sub(r"[^\S\n]+", " ", t)
    return re.sub(r" ?\n ?", "\n", t)


def _demand_level_in(window: str):
    """(rank, label) of the highest CEFR code or level word in a window, or None."""
    codes = [(_LEVEL_RANK[m.group(1)], m.group(1).upper()) for m in _RE_CEFR.finditer(window)]
    if codes:
        return max(codes)
    hits = []
    for rx, rank in _RE_DEMAND_LEVEL:
        hits.extend((rank, m.group(0)) for m in rx.finditer(window))
    return max(hits) if hits else None


def language_demands(text: str) -> list[dict]:
    """Every levelled mention of a language in an ad or a requirement line:
    [{lang, rank, label, soft, quote, side}], in text order.

    `rank` is the CEFR rank (B1 = 3, B2 = 4, C1 = 5, C2 = 6). `soft` is true when
    the ad calls the demand a plus, optional or not required. `side` says where
    the level stood relative to the language name ("before" or "after"); a
    language that only shares its neighbour's level carries that neighbour's side.
    Deterministic, no network."""
    t = _demand_text(text)
    mentions = list(_RE_DEMAND_MENTION.finditer(t))
    if not mentions:
        return []
    n = len(mentions)
    bare = [i for i, m in enumerate(mentions) if m.group(1) == "german"]
    spelled = str(text or "")
    ends = _german_item_ends(spelled, len(bare)) if any(ch.isupper() for ch in spelled) else None
    item_end = {i: ends[k] for k, i in enumerate(bare)} if ends else {}
    levels: list = [None] * n          # (rank, label, side)
    spans: list = [None] * n           # (start, end) of the wording that carries the demand

    def lvl(window: str, side: str):
        hit = _demand_level_in(window)
        return hit + (side,) if hit else None

    def behind(window: str, then_language: bool = False):
        """The level at the start of `window`, the text right behind a language
        name, unless that word opens the next item of a flattened list.
        `then_language`: the window ends where the next language name starts."""
        start = _RE_DEMAND_START_LEVEL.search(window)
        if not start:
            return None
        rest = window[start.end():]
        if then_language and not rest.strip():
            rest = " " + follower                      # "German Fluent English": fluent English
        if _opens_next_item(start.group("level"), rest):
            return None
        return lvl(window, "after")

    for i, m in enumerate(mentions):
        prev_end = mentions[i - 1].end() if i else 0
        next_start = mentions[i + 1].start() if i + 1 < n else len(t)
        floor = max(prev_end, m.start() - 70)
        breaks = [b.end() for b in _RE_HARD_BREAK.finditer(t, floor, m.start())]
        lo = max([floor] + breaks)
        comma = t.rfind(",", lo, m.start())
        before = t[(comma + 1) if comma != -1 else lo:m.start()]
        limit = min(next_start, m.end() + 45)
        stop = _RE_HARD_BREAK.search(t, m.end(), limit)
        hi = stop.start() if stop else limit
        parts = t[m.end():hi].split(",")
        after = _RE_COORD_SPLIT.split(parts[0])[0]
        # Does the text behind this name run straight into the next language name?
        follower = mentions[i + 1].group(0) if i + 1 < n and hi == next_start else ""
        into_next = bool(follower) and len(parts) == 1 and after == parts[0]
        # A bare "german" with the level in front needs a skill reading behind it.
        front_ok = m.group(1) != "german" or bool(_RE_SKILL_AFTER.match(t[m.end():m.end() + 30]))
        by_capital = not front_ok and item_end.get(i, False)      # only the next item's capital letter
        found = lvl(after, "after") if _RE_CEFR.search(after) else None
        if found is None and (front_ok or by_capital) and _RE_DEMAND_END_LEVEL.search(before):
            near = _closest_level(before.rstrip(" :(-"))                       # "fluent German"
            if near and (front_ok or not _RE_LEVEL_GENERAL.match(near[1])):
                found = near[:2] + ("before",)
        if found is None:
            # "German fluent English good": a lower-case level word stays with German.
            stays = m.group(1) == "german" and item_end.get(i) is False
            found = behind(after, into_next and not stays)                      # "Deutsch fließend"
        if found is None and (front_ok or by_capital):
            segment = _RE_COORD_SPLIT.split(before)[-1]
            linked = _level_before(segment)                                    # "sehr gute Kenntnisse in Deutsch"
            if linked and not front_ok:
                gap = segment[_closest_level(segment)[2]:]
                if not (_RE_GAP_LINK.search(gap) or _RE_GAP_LANGUAGE_WORD.search(gap)):
                    linked = None
            if linked:
                found = linked + ("before",)
        if found is None and front_ok:
            noun = _RE_DEMAND_NOUN_LEVEL.search(before) or (                    # "...sicherheit in Deutsch"
                m.group(1).endswith("sprache") and _RE_DEMAND_BARE_NOUN.search(before))
            if noun:
                found = (5, noun.group(0).strip(), "before")
        if found is None and len(parts) > 1:
            second = _RE_COORD_SPLIT.split(parts[1])[0]                         # "Deutsch, verhandlungssicher"
            found = behind(second, bool(follower) and len(parts) == 2 and second == parts[1])
        if found is None and t[hi:hi + 1] == "\n" and not before.strip(" -:(") and not parts[0].strip(" -:("):
            end = t.find("\n", hi + 1)                                          # a list: "Deutsch" / "fließend"
            below = t[hi + 1:end if end != -1 else len(t)][:40]
            if not _RE_DEMAND_MENTION.search(below):
                found = behind(_RE_COORD_SPLIT.split(below)[0])
        levels[i] = found
        spans[i] = (m.start() - len(before), hi)
    # Shared levels. Left to right: "sehr gute Englisch- und Deutschkenntnisse",
    # "fluent in English and German". Right to left: "Deutsch und Englisch fließend".
    for i in range(1, n):
        if levels[i] is None and levels[i - 1] and levels[i - 1][2] == "before" \
                and t[mentions[i - 1].end():mentions[i].start()].strip() \
                and _RE_SHARE_GAP.match(t[mentions[i - 1].end():mentions[i].start()]):
            levels[i] = levels[i - 1]
            spans[i] = (spans[i - 1][0], spans[i][1])
    for i in range(n - 2, -1, -1):
        if levels[i] is None and levels[i + 1] and levels[i + 1][2] == "after" \
                and t[mentions[i].end():mentions[i + 1].start()].strip() \
                and _RE_SHARE_GAP.match(t[mentions[i].end():mentions[i + 1].start()]):
            levels[i] = levels[i + 1]
            spans[i] = (spans[i][0], spans[i + 1][1])
    out = []
    for i, m in enumerate(mentions):
        if not levels[i]:
            continue
        rank, label, side = levels[i]
        out.append({"lang": _DEMAND_FORMS[m.group(1)], "rank": rank, "label": label, "side": side,
                    "soft": _demand_is_soft(t, m),
                    "quote": re.sub(r"\s+", " ", t[max(0, spans[i][0]):spans[i][1]]).strip(" ,:-(")[:110]})
    return out


# A softener that stands alone on a line is a heading: "Nice to have", "Von Vorteil:",
# "Bonus points". What follows, up to the next heading, is a plus. Since the page text
# keeps its lines (2026-11-16) such a heading no longer runs into the item below it.
_RE_SOFT_HEADING = re.compile(
    r"^\W*(?:(?:das |this |it |these )?(?:waren?|would be|is|ist|sind|are) )?(?:(?:a|an|ein|eine|von|als) )?"
    r"(?:nice[- ]to[- ]haves?|good to have|bonus(?: points?| skills?)?|pluspunkte?|(?:(?:a|ein|grosses?) )?plus|"
    r"vorteil|vorteilhaft|wunschenswert\w*|optional\w*|preferred(?: qualifications| skills| experience)?|"
    r"desirable\w*|idealerweise(?: bringst du| bringen sie| hast du| haben sie)?(?: (?:auch|ausserdem|zusatzlich))?"
    r"(?: mit)?|ideally(?:,? you(?: also)? (?:have|bring))?|what would be (?:great|nice|a plus))"
    r"(?: (?:skills|qualifications|experience|kenntnisse|qualifikationen))?\W*$")
_RE_COLON_HEADING = re.compile(r"^[^\n]{1,60}:\s*$")


def _under_soft_heading(t: str, pos: int) -> bool:
    """True when the nearest heading above position `pos` is a softener on a line of its own."""
    start = t.rfind("\n", 0, pos) + 1
    for _ in range(14):
        if start <= 0:
            return False
        end = start - 1
        start = t.rfind("\n", 0, end) + 1
        line = t[start:end].strip()
        if not line:
            continue
        if len(line) <= 60 and _RE_SOFT_HEADING.match(line):
            return True
        if _RE_COLON_HEADING.match(line) or _RE_REQ_HEADER.match(line) or _RE_OTHER_HEADER.match(line):
            return False
    return False


# German offered as an alternative to English is no German demand: "very good
# German or English", "good command of German or English", "English or German",
# "Deutsch- oder Englischkenntnisse", "German and/or English". With "and" both
# are demanded and the rule stays. (2026-11-24: an AI internship and a vision
# internship were dropped as "German above B1" for exactly these wordings.)
# A word between German and the "or" (a level, "skills") or between the "or"
# and English ("fluent"); never "and" or another language: "German or Spanish
# and English", "German and French or English" still demand German.
_ALT_OTHER = (r"and|und|sowie|plus|as|french|franz\w*|spanish|spanisch\w*|italian|italienisch\w*|dutch"
              r"|niederl\w*|polish|polnisch\w*|portuguese|portugiesisch\w*|russian|russisch\w*|turkish"
              r"|t(?:ü|ue)rkisch\w*|arabic|arabisch\w*|chinese|chinesisch\w*|japanese|japanisch\w*")
_ALT_TOKEN = rf"(?!(?:{_ALT_OTHER})\b)[^\s,;.:!?]+"
# Nothing but spaces, brackets and dashes before the first word and no line
# break anywhere: no sentence or paragraph between German and the "or"
# ("fluent German. Or English speakers"). "English-speaking" is no language
# demand at all ("German or English-speaking customers").
_RE_ALT_ENGLISH_AFTER = re.compile(
    rf"^[ \t()\[\]/-]*(?:{_ALT_TOKEN}[ \t]+){{0,3}}?(?:or|oder|and/or|und/oder)[ \t]+"
    rf"(?:{_ALT_TOKEN}[ \t]+){{0,3}}?(?:english|englisch)(?![\w-]*speaking)",
    re.IGNORECASE)
_RE_ALT_ENGLISH_BEFORE = re.compile(
    rf"(?:english|englisch)[\w-]*[ \t]+(?:or|oder|and/or|und/oder)[ \t]+(?:{_ALT_TOKEN}[ \t]+){{0,3}}?[^\w\n]*$",
    re.IGNORECASE)


def german_or_english(text: str, start: int, end: int) -> bool:
    """True when the German named at text[start:end] stands as an alternative
    to English ("German or English", "English or German")."""
    after = text[end:end + 60]
    before = text[max(0, start - 60):start]
    return bool(_RE_ALT_ENGLISH_AFTER.match(after) or _RE_ALT_ENGLISH_BEFORE.search(before))


def _demand_is_soft(t: str, m) -> bool:
    """True when the ad presents this language demand as a plus, optional or not
    required. Softeners count only inside the demand's own comma-clause; behind a
    new item ("... Deutschkenntnisse und idealerweise SAP-Erfahrung") they do not."""
    if _DEMAND_FORMS.get(m.group(1)) == "german" and german_or_english(t, m.start(), m.end()):
        return True                                   # "German or English": English is enough
    floor = max(0, m.start() - 90)
    breaks = [b.end() for b in _RE_HARD_BREAK.finditer(t, floor, m.start())]
    lo = max([floor] + breaks)
    stop = _RE_HARD_BREAK.search(t, m.end(), m.end() + 120)
    hi = stop.start() if stop else min(len(t), m.end() + 120)
    comma = t.rfind(",", lo, m.start())
    head = t[(comma + 1) if comma != -1 else lo:m.start()]
    tail = t[m.end():hi].split(",")[0]
    if _RE_NEGATED.search(head):
        return True                                   # "keine Deutschkenntnisse", "no German"
    if _under_soft_heading(t, m.start()):
        return True                                   # a line under "Nice to have"
    if _RE_SOFT_START.search(head):
        return True                                   # "Idealerweise ...", "Nice to have: ..."
    if _RE_SOFT.search(_RE_COORD_SPLIT.split(head)[-1]):
        return True                                   # "... und idealerweise gute Deutschkenntnisse"
    if _RE_SOFT_END.search(tail):
        return True                                   # "... und Englischkenntnisse sind von Vorteil"
    # Skip the languages coordinated with this one, then look only up to the
    # next coordinator: what follows it is a new item.
    rest = tail
    while True:
        nxt = _RE_COORD_LANG.match(rest)
        if not nxt:
            break
        rest = rest[nxt.end():]
    return bool(_RE_SOFT_BEHIND.search(_RE_COORD_SPLIT.split(rest)[0]))


def required_level(text: str, lang: str = "german") -> tuple[int, str]:
    """(highest CEFR rank the text demands outright for `lang`, the wording);
    (0, "") when it states none. Demands the ad calls a plus are ignored."""
    low = str(text or "").lower()
    if lang == "german" and "deutsch" not in low and "german" not in low:
        return 0, ""                                   # most ads: nothing to parse
    hard = [d for d in language_demands(text) if d["lang"] == lang and not d["soft"]]
    if not hard:
        return 0, ""
    best = max(hard, key=lambda d: d["rank"])
    return best["rank"], best["quote"]


def _clean_line(line: str) -> str:
    line = _RE_BULLET.sub("", line).strip(" \t-*:;,.")
    line = re.sub(r"\s+", " ", line)
    if len(line) > MAX_REQ_CHARS:
        cut = line[:MAX_REQ_CHARS]
        line = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return line


def extract_requirements(description: str, limit: int = MAX_REQUIREMENTS) -> list[str]:
    """Short list of requirement strings from a job description.

    Order of preference: lines inside a requirements/qualifications section (the
    section ends at the next known header such as 'Responsibilities' or 'We
    offer'), then bullet lines anywhere, then skills from the vocabulary. Language
    levels ("German C1") are always added as their own requirement. Deterministic,
    no network."""
    text = str(description or "")
    lines = text.splitlines()
    section: list[str] = []
    in_section = False
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if _RE_REQ_HEADER.match(stripped):
            in_section = True
            continue
        if _RE_OTHER_HEADER.match(stripped):
            in_section = False
            continue
        if in_section:
            section.append(stripped)
    reqs: list[str] = []

    def add(item: str) -> None:
        item = _clean_line(item)
        if len(item) >= 3 and item.lower() not in {r.lower() for r in reqs}:
            reqs.append(item)

    for ln in section:
        # A long prose line in a section is kept whole; very long lines are
        # split at sentence ends so each requirement stays short.
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])", ln):
            add(part)
    if not reqs:
        for raw in lines:
            if _RE_BULLET.match(raw):
                add(raw)
    langs = language_requirements(text)
    covered = lambda lang: any(  # noqa: E731
        lang in {l for l, _, _ in language_requirements(r)} for r in reqs)
    skills = [s for s, rx in _VOCAB_RES if rx.search(text)]
    if len(reqs) < 3:
        for s in skills:
            if not any(s.lower() in r.lower() for r in reqs):
                add(s)
    out = reqs[:max(0, limit - len(langs))]
    for lang, _, disp in langs:
        if not covered(lang):
            out.append(disp)
    return out[:limit]


# -- Retrievers -------------------------------------------------------------------
class BM25Retriever:
    """Okapi BM25 over corpus chunks. Pure Python."""
    name = "bm25"

    def __init__(self, corpus: list[dict], k1: float = 1.4, b: float = 0.6):
        self.corpus = list(corpus)
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(c["text"])) for c in self.corpus]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.idf_unseen = math.log(1 + (n + 0.5) / 0.5) if n else 1.0

    def search(self, query: str, k: int = TOP_K) -> list[tuple[dict, float, float]]:
        """[(chunk, rank score, confidence in 0..1)], best first. Chunks with no
        query term are never returned."""
        q = set(tokenize(query))
        if not q or not self.corpus:
            return []
        total = sum(self.idf.get(t, self.idf_unseen) for t in q)
        hits = []
        for i, d in enumerate(self.docs):
            score = 0.0
            matched = 0.0
            for t in q:
                tf = d.get(t, 0)
                if not tf:
                    continue
                idf = self.idf[t]
                denom = tf + self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avg or 1.0))
                score += idf * tf * (self.k1 + 1) / denom
                matched += idf
            if score > 0:
                hits.append((self.corpus[i], score, matched / total if total else 0.0))
        hits.sort(key=lambda h: (-h[1], h[0]["id"]))
        return hits[:k]


class EmbeddingRetriever:
    """Optional sentence-transformers backend. Imported lazily; check
    `available()` first. The model loader may download weights on first use, which
    is why this backend is opt-in and never used by the tests."""
    name = "embeddings"

    @staticmethod
    def available() -> bool:
        try:
            import importlib.util
            return importlib.util.find_spec("sentence_transformers") is not None
        except Exception:
            return False

    def __init__(self, corpus: list[dict], model_name: str | None = None):
        from sentence_transformers import SentenceTransformer  # lazy
        self.corpus = list(corpus)
        self.model = SentenceTransformer(model_name or os.environ.get(ENV_EMBED_MODEL, "all-MiniLM-L6-v2"))
        self.vecs = self.model.encode([c["text"] for c in self.corpus], normalize_embeddings=True) \
            if self.corpus else []

    def search(self, query: str, k: int = TOP_K) -> list[tuple[dict, float, float]]:
        if not self.corpus or not str(query).strip():
            return []
        q = self.model.encode([query], normalize_embeddings=True)[0]
        sims = [float(sum(float(a) * float(b) for a, b in zip(v, q))) for v in self.vecs]
        order = sorted(range(len(sims)), key=lambda i: (-sims[i], self.corpus[i]["id"]))[:k]
        return [(self.corpus[i], sims[i], max(0.0, min(1.0, sims[i]))) for i in order]


def make_retriever(corpus: list[dict], backend: str | None = None):
    """BM25 unless embeddings were requested AND are installed; a missing or
    failing embedding backend falls back to BM25."""
    want = (backend or os.environ.get(ENV_BACKEND, "bm25")).strip().lower()
    if want == "embeddings" and EmbeddingRetriever.available():
        try:
            return EmbeddingRetriever(corpus)
        except Exception:
            print("  [Evidence] embedding backend failed to start; using BM25")
    elif want == "embeddings":
        print("  [Evidence] sentence-transformers not installed; using BM25")
    return BM25Retriever(corpus)


# -- Matching ---------------------------------------------------------------------
def status_for(confidence: float, backend: str = "bm25") -> str:
    t = THRESHOLDS.get(backend, THRESHOLDS["bm25"])
    return "met" if confidence >= t["met"] else "partial" if confidence >= t["partial"] else "gap"


def _evidence_item(chunk: dict) -> dict:
    return {"id": chunk["id"], "source": chunk["source"], "text": chunk["text"]}


def _level_in_chunk(text: str, lang: str) -> int:
    """Highest level of `lang` stated in a chunk, 0 when none."""
    return max([rank for lg, rank, _ in language_requirements(text) if lg == lang] or [0])


def _mentions_language(text: str, lang: str) -> bool:
    return any(_LANG_FORMS[m.group(1)] == lang for m in _RE_LANG_MENTION.finditer(_fold(text)))


def _match_language(lang: str, required: int, corpus: list[dict]) -> dict:
    stated = [(c, _level_in_chunk(c["text"], lang)) for c in corpus if _mentions_language(c["text"], lang)]
    stated = [(c, lv) for c, lv in stated if lv]
    if stated:
        c, lv = max(stated, key=lambda x: (x[1], x[0]["id"]))
        status = "met" if lv >= required else "gap"
        return {"status": status, "evidence": [_evidence_item(c)], "score": 1.0 if status == "met" else 0.0}
    return {"status": "gap", "evidence": [], "score": 0.0}


def match_requirement(requirement: str, retriever, corpus: list[dict], k: int = TOP_K) -> dict:
    gap = {"requirement": requirement, "status": "gap", "evidence": [], "score": 0.0}
    # A line that states a language level is judged on that level, whatever its
    # length. Retrieval used to mark "Sehr gute Deutsch- und gute
    # Englischkenntnisse" as met, because its words overlap with the candidate's
    # own language line (seen in the runs of 11 to 13 November 2026).
    demanded: dict[str, int] = {}
    for d in language_demands(requirement):
        if not d["soft"]:
            demanded[d["lang"]] = max(demanded.get(d["lang"], 0), d["rank"])
    missed = [m for m in (_match_language(lang, rank, corpus) for lang, rank in sorted(demanded.items()))
              if m["status"] == "gap"]
    if missed:
        m = max(missed, key=lambda x: len(x["evidence"]))
        return {"requirement": requirement, "status": "gap", "evidence": m["evidence"], "score": 0.0}
    langs = language_requirements(requirement)
    if langs and len(tokenize(requirement)) <= 6:
        lang, rank, _ = langs[0]
        m = _match_language(lang, rank, corpus)
        return {"requirement": requirement, "status": m["status"],
                "evidence": m["evidence"], "score": m["score"]}
    if not langs and _RE_CEFR.search(_fold(requirement)):
        return gap          # a level for a language we cannot identify has no evidence
    hits = retriever.search(requirement, k)
    if not hits:
        return gap
    status = status_for(hits[0][2], getattr(retriever, "name", "bm25"))
    evidence = [_evidence_item(c) for c, _, _ in hits] if status != "gap" else []
    return {"requirement": requirement, "status": status, "evidence": evidence,
            "score": round(hits[0][2], 3)}


def match_job(job: dict, corpus: list[dict], retriever=None,
              requirements: list[str] | None = None) -> list[dict]:
    """[{requirement, status: met|partial|gap, evidence: [{id, source, text}], score}]

    One entry per extracted requirement. Evidence lists the top chunks (at most
    3) for met and partial and is empty for a gap, except that a language gap
    keeps the chunk that states the lower level. `score` is the confidence of the
    best chunk, see the thresholds at the top of this module."""
    reqs = requirements if requirements is not None else extract_requirements(job.get("description", ""))
    if not reqs:
        return []
    retr = retriever or BM25Retriever(corpus)
    return [match_requirement(r, retr, corpus) for r in reqs]


def summarize(matches: list[dict]) -> dict:
    counts = Counter(m["status"] for m in matches)
    return {s: counts.get(s, 0) for s in STATUSES}


# -- Pipeline hook ------------------------------------------------------------------
def attach_evidence(jobs: list[dict], corpus: list[dict], retriever=None) -> int:
    """Attach `evidence` (matches) and `evidence_summary` to each job in place.
    Never raises for one job. Returns how many jobs got at least one match."""
    retr = retriever or make_retriever(corpus)
    n = 0
    for j in jobs:
        try:
            matches = match_job(j, corpus, retr)
        except Exception:
            matches = []
        if matches:
            j["evidence"] = matches
            j["evidence_summary"] = summarize(matches)
            n += 1
    return n


def evidence_prompt_lines(job: dict, max_reqs: int = 6, snippet: int = 140) -> str:
    """Prompt lines for one job: each requirement with its status and the
    retrieved evidence ids and short snippets. Empty when the job has no matches."""
    matches = job.get("evidence") or []
    if not matches:
        return ""
    lines = ["    Retrieved candidate evidence (the ONLY facts you may credit):"]
    for m in matches[:max_reqs]:
        req = str(m["requirement"])[:MAX_REQ_CHARS]
        if m["status"] == "gap" and not m["evidence"]:
            lines.append("      - %s => GAP (no evidence)" % req)
            continue
        ev = "; ".join("[%s] %s" % (e["id"], str(e["text"])[:snippet]) for e in m["evidence"][:2])
        lines.append("      - %s => %s: %s" % (req, m["status"].upper(), ev))
    return "\n".join(lines)


EVIDENCE_INSTRUCTION = (
    "\n\nEVIDENCE RULES: Some jobs list 'Retrieved candidate evidence'. For those jobs, "
    "credit the candidate only with what that evidence (and the candidate facts in the system prompt) "
    "states. A requirement marked GAP is not met; do not infer the missing skill from the CV profile. "
    "Cite the evidence ids you relied on in the reason, in square brackets, for example [fact:german_level]."
)


def batch_has_evidence(batch: list[dict]) -> bool:
    return any(j.get("evidence") for j in batch)
