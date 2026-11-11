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
