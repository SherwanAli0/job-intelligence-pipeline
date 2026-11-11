"""Evidence layer (evidence.py): corpus, requirement extraction, BM25, gap detection,
the off-by-default flag, scorer prompt, digest table and privacy. Offline, fictional
data only; any socket connection fails the test."""
import socket

import pytest

import evidence
import eval_retrieval
import notifier
import scorer
from graph import build_graph


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.delenv(evidence.ENV_FLAG, raising=False)


FACTS = {"german_level": "B1", "english_level": "C1"}
CV = ("Built a churn model in Python with scikit-learn and pandas.\n\n"
      "Wrote SQL queries against a PostgreSQL database for analytics.\n\n"
      "Deployed a Flask API in a Docker container.")


def corpus():
    return evidence.build_corpus(facts=FACTS, documents={"My CV.md": CV})


# -- corpus and chunk ids ---------------------------------------------------------
def test_chunk_ids_are_stable_and_labelled():
    a, b = corpus(), corpus()
    assert a == b
    ids = [c["id"] for c in a]
    assert ids == ["fact:english_level", "fact:german_level", "my-cv-md:01", "my-cv-md:02", "my-cv-md:03"]
    assert len(set(ids)) == len(ids)
    assert all(c["source"] and c["text"] for c in a)
    assert next(c for c in a if c["id"] == "fact:german_level")["text"] == "german level: B1"


def test_long_text_is_split_into_small_chunks():
    text = " ".join("Sentence number %d describes some project work in detail." % i for i in range(30))
    chunks = evidence.chunk_document("doc", "document doc", text)
    assert len(chunks) > 3
    assert all(len(c["text"]) <= evidence.CHUNK_TARGET_CHARS + 60 for c in chunks)
    assert [c["id"] for c in chunks][:2] == ["doc:01", "doc:02"]


def test_profile_chunks_repeated_across_tracks_are_deduplicated():
    shared = "Shared policy sentence that recurs in every track."
    c = evidence.build_corpus(profiles={"AI": shared + "\n\nAI only.", "ML": shared + "\n\nML only."})
    texts = [x["text"] for x in c]
    assert texts.count(shared) == 1 and "AI only." in texts and "ML only." in texts


def test_load_documents_skips_missing_and_reads_files(tmp_path):
    f = tmp_path / "cv.txt"
    f.write_text("Python and SQL.", encoding="utf-8")
    docs = evidence.load_documents({evidence.ENV_FILES: str(f) + __import__("os").pathsep + str(tmp_path / "nope.txt")})
    assert docs == {"cv": "Python and SQL."}


# -- requirement extraction -------------------------------------------------------
AD = """About us
We build things.

Requirements:
- 2+ years of Python
- Experience with SQL and PostgreSQL
- Fluent German (C1)

What we offer:
- Free coffee
- Table football
"""


def test_requirements_come_from_the_section_and_stop_at_next_header():
    reqs = evidence.extract_requirements(AD)
    assert "2+ years of Python" in reqs
    assert "Experience with SQL and PostgreSQL" in reqs
    assert not any("coffee" in r.lower() or "football" in r.lower() for r in reqs)
    assert any("german" in r.lower() and "c1" in r.lower() for r in reqs)


def test_language_level_is_extracted_without_a_section():
    reqs = evidence.extract_requirements("We need German C1 and English B2 for this role.")
    assert "German C1" in reqs and "English B2" in reqs


def test_vocabulary_fallback_and_limit():
    reqs = evidence.extract_requirements("You will work with Python, Docker and Airflow every day.")
    assert {"python", "docker", "airflow"} <= {r.lower() for r in reqs}
    many = "Requirements:\n" + "\n".join("- skill number %d" % i for i in range(30))
    assert len(evidence.extract_requirements(many)) <= evidence.MAX_REQUIREMENTS


def test_empty_description_has_no_requirements():
    assert evidence.extract_requirements("") == []
    assert evidence.extract_requirements(None) == []


# -- BM25 -------------------------------------------------------------------------
def test_bm25_ranks_the_right_chunk_first():
    r = evidence.BM25Retriever(corpus())
    top = r.search("PostgreSQL queries")[0]
    assert top[0]["id"] == "my-cv-md:02"
    assert r.search("Docker containers")[0][0]["id"] == "my-cv-md:03"
    assert r.search("zzz unknown") == []


# -- matching and gap detection -----------------------------------------------------
def test_match_job_statuses_and_evidence_shape():
    job = {"description": "Requirements:\n- Python and pandas\n- Kubernetes operations\n- German C1"}
    matches = evidence.match_job(job, corpus())
    by = {m["requirement"]: m for m in matches}
    assert by["Python and pandas"]["status"] == "met"
    assert by["Python and pandas"]["evidence"][0]["id"] == "my-cv-md:01"
    assert set(by["Python and pandas"]["evidence"][0]) == {"id", "source", "text"}
    assert by["Kubernetes operations"]["status"] == "gap" and by["Kubernetes operations"]["evidence"] == []
    assert all(m["status"] in evidence.STATUSES for m in matches)


def test_language_level_gap_is_not_matched_by_word_overlap():
    (g,) = evidence.match_job({}, corpus(), requirements=["German C1"])
    assert g["status"] == "gap"                       # candidate has B1
    assert g["evidence"][0]["id"] == "fact:german_level"
    (ok,) = evidence.match_job({}, corpus(), requirements=["German B1"])
    assert ok["status"] == "met"
    (en,) = evidence.match_job({}, corpus(), requirements=["English C1"])
    assert en["status"] == "met"
    (none,) = evidence.match_job({}, corpus(), requirements=["French B2"])
    assert none["status"] == "gap" and none["evidence"] == []


def test_status_thresholds():
    assert evidence.status_for(0.7) == "met"
    assert evidence.status_for(0.4) == "partial"
    assert evidence.status_for(0.1) == "gap"


def test_embedding_backend_falls_back_to_bm25_when_missing(monkeypatch):
    monkeypatch.setattr(evidence.EmbeddingRetriever, "available", staticmethod(lambda: False))
    assert isinstance(evidence.make_retriever(corpus(), "embeddings"), evidence.BM25Retriever)


def test_eval_set_scores_stay_above_floor():
    res = eval_retrieval.evaluate(eval_retrieval.load_rows(), "bm25")
    assert res["rows"] >= 30
    assert res["recall@3"] >= 0.9 and res["mrr"] >= 0.9
    assert res["gap_precision"] >= 0.7 and res["gap_recall"] >= 0.7


# -- flag off = unchanged behaviour -------------------------------------------------
def test_flag_is_off_by_default_and_parses_values():
    assert evidence.enabled({}) is False
    assert evidence.enabled({evidence.ENV_FLAG: "0"}) is False
    assert evidence.enabled({evidence.ENV_FLAG: "1"}) is True


def _job(**kw):
    base = {"id": "j1", "title": "Data Scientist", "company": "Acme", "location": "Bonn",
            "url": "https://example.com/j", "source": "Greenhouse", "score": 70, "reason": "ok",
            "description": "Requirements:\n- Python and pandas\n- Kubernetes operations\n- German C1"}
    base.update(kw)
    return base


def test_prompt_is_unchanged_without_evidence():
    j = _job()
    expected = scorer.USER_TEMPLATE.format(n=1, jobs_block=(
        "[0] Title: Data Scientist\n    Company: Acme\n    Location: Bonn\n"
        "    Description snippet: " + j["description"][:1500]))
    assert scorer._build_prompt([j]) == expected
    assert "evidence" not in scorer._build_prompt([j]).lower()


def test_graph_topology_unchanged_without_evidence_node():
    order = []

    def mk(name, upd=None):
        def f(state):
            order.append(name)
            return upd or {}
        return f
    nodes = {n: mk(n) for n in ("scrape", "filter", "score", "rank", "notify", "persist")}
    nodes["filter"] = mk("filter", {"new_jobs": [{"id": "a"}]})
    build_graph(nodes).invoke({})
    assert order == ["scrape", "filter", "score", "rank", "notify", "persist"]
    order.clear()
    nodes["evidence"] = mk("evidence")
    build_graph(nodes).invoke({})
    assert order == ["scrape", "filter", "evidence", "score", "rank", "notify", "persist"]
    order.clear()
    nodes["filter"] = mk("filter", {"new_jobs": []})
    build_graph(nodes).invoke({})
    assert order == ["scrape", "filter", "persist"]


# -- scorer prompt with evidence ------------------------------------------------------
def test_scorer_prompt_carries_evidence_ids_and_the_rely_only_rule():
    j = _job()
    assert evidence.attach_evidence([j], corpus()) == 1
    prompt = scorer._build_prompt([j])
    assert "[my-cv-md:01]" in prompt
    assert "GAP (no evidence)" in prompt
    assert "EVIDENCE RULES" in prompt and "only" in prompt.lower() and "Cite the evidence ids" in prompt
    assert j["evidence_summary"]["gap"] >= 1 and j["evidence_summary"]["met"] >= 1


def test_attach_evidence_survives_a_bad_job():
    good, bad = _job(), _job(id="j2", description=None)
    assert evidence.attach_evidence([bad, good], corpus()) == 1
    assert "evidence" not in bad


# -- digest table ----------------------------------------------------------------------
def test_digest_shows_table_and_escapes_hostile_text():
    hostile = '<script>alert(1)</script> "><img src=x onerror=1>'
    j = _job()
    j["evidence"] = [
        {"requirement": hostile, "status": "met",
         "evidence": [{"id": 'fact:"><b>x</b>', "source": "s", "text": "t"}], "score": 1.0},
        {"requirement": "Kubernetes", "status": "gap", "evidence": [], "score": 0.0},
        {"requirement": "weird", "status": "<i>bogus</i>", "evidence": [], "score": 0.0},
    ]
    html_out = notifier._build_html([j])
    assert "Requirements vs evidence" in html_out
    assert "&lt;script&gt;" in html_out
    assert "<script>alert(1)</script>" not in html_out
    assert '"><img' not in html_out and "<b>x</b>" not in html_out
    assert ">met<" in html_out and ">gap<" in html_out
    assert "bogus" not in html_out                       # unknown status is shown as gap, never raw


def test_digest_unchanged_without_evidence():
    assert "Requirements vs evidence" not in notifier._build_html([_job()])


# -- privacy ------------------------------------------------------------------------------
def test_pipeline_node_prints_counts_only(monkeypatch, capsys):
    import main
    secret = [{"id": "fact:x", "source": "candidate fact: x", "text": "x: PRIVATE_SECRET_TEXT python"}]
    monkeypatch.setattr(evidence, "load_corpus", lambda environ=None: secret)
    state = {"new_jobs": [_job(description="Requirements:\n- python")]}
    out = main.node_evidence(state)
    printed = capsys.readouterr().out
    assert out == {"evidence_jobs": 1}
    assert "PRIVATE_SECRET_TEXT" not in printed and "corpus chunks: 1" in printed


def test_pipeline_node_failure_does_not_stop_the_run(monkeypatch, capsys):
    import main

    def boom(environ=None):
        raise RuntimeError("PRIVATE_SECRET_TEXT")
    monkeypatch.setattr(evidence, "load_corpus", boom)
    assert main.node_evidence({"new_jobs": [_job()]}) == {"evidence_jobs": 0}
    assert "PRIVATE_SECRET_TEXT" not in capsys.readouterr().out


def test_corpus_loads_from_the_example_profile():
    c = evidence.load_corpus({})
    assert any(x["id"] == "fact:german_level" for x in c)
    (g,) = evidence.match_job({}, c, requirements=["German C1"])
    assert g["status"] == "gap"                          # fictional example candidate has A2


# -- language requirements in German and English forms --------------------------------------
@pytest.mark.parametrize("text,lang,rank", [
    ("Fließend Deutsch", "german", 5),
    ("Deutsch: Muttersprache", "german", 6),
    ("Muttersprache Deutsch", "german", 6),
    ("Deutschkenntnisse C1", "german", 5),
    ("verhandlungssicheres Deutsch", "german", 5),
    ("gute Deutschkenntnisse", "german", 4),
    ("Sehr gute Deutschkenntnisse (mind. C1)", "german", 5),
    ("native-level German", "german", 6),
    ("professional working proficiency in English", "english", 5),
    ("fluent English", "english", 5),
    ("Englisch B2", "english", 4),
])
def test_language_requirement_forms(text, lang, rank):
    ((got_lang, got_rank, _),) = evidence.language_requirements(text)
    assert (got_lang, got_rank) == (lang, rank)


def test_levels_belong_to_their_own_language():
    got = {l: r for l, r, _ in evidence.language_requirements("C1 German, B2 English")}
    assert got == {"german": 5, "english": 4}
    got = {l: r for l, r, _ in evidence.language_requirements("German C1 and English B2")}
    assert got == {"german": 5, "english": 4}
    assert evidence.language_requirements("Deutschland, Berlin") == []
    assert evidence.language_requirements("Our team is German-speaking") == []


def test_german_language_lines_are_extracted_from_a_german_ad():
    reqs = evidence.extract_requirements(
        "Dein Profil:\n- Erfahrung mit Python\n- Fließend Deutsch\n\nWir bieten:\n- Kaffee")
    assert reqs == ["Erfahrung mit Python", "Fließend Deutsch"]


def test_german_and_english_levels_are_compared_with_the_corpus():
    c = evidence.build_corpus(facts={"german_level": "Muttersprache", "english_level": "B2"})
    st = lambda r: evidence.match_job({}, c, requirements=[r])[0]["status"]  # noqa: E731
    assert st("Fließend Deutsch") == "met" and st("Deutschkenntnisse C1") == "met"
    assert st("Englisch C1") == "gap" and st("Englisch B2") == "met"
    assert st("native-level English") == "gap"


def test_level_token_only_counts_with_its_language():
    c = evidence.build_corpus(facts={"english_level": "C1"})
    (m,) = evidence.match_job({}, c, requirements=["日本語 C1"])
    assert m["status"] == "gap" and m["evidence"] == []
    assert "c1" not in evidence.tokenize("English C1 level")
    (m2,) = evidence.match_job({}, c, requirements=["Level C1 in Klingon"])
    assert m2["status"] == "gap"


# -- German filler words ---------------------------------------------------------------------------
def test_german_filler_is_ignored_in_matching():
    assert evidence.tokenize("Erfahrung mit Python") == ["python"]
    assert evidence.tokenize("Sehr gute Kenntnisse in SQL und Excel, idealerweise von Vorteil") == \
        ["sql", "excel", "vorteil"]
    c = evidence.build_corpus(documents={"cv": "Built Python pandas models for a university project."})
    (m,) = evidence.match_job({}, c, requirements=["Erfahrung mit Python"])
    assert m["status"] == "met" and m["evidence"][0]["id"] == "cv:01"


def test_umlauts_and_sharp_s_fold_together():
    assert evidence.tokenize("Prüfung Fließend") == evidence.tokenize("Prufung Fliessend")


@pytest.mark.parametrize("short,expanded", [
    ("ML", "machine learning"), ("NLP", "natural language processing"),
    ("RAG", "retrieval augmented generation"), ("Deutsch", "german"), ("Englisch", "english"),
])
def test_each_alias_is_exercised(short, expanded):
    assert set(evidence.tokenize(expanded)) <= set(evidence.tokenize(short))


# -- held-out evaluation set ----------------------------------------------------------------------------
def test_heldout_set_is_separate_and_includes_german_postings():
    tuning = eval_retrieval.load_rows(eval_retrieval.SETS["tuning"])
    held = eval_retrieval.load_rows(eval_retrieval.SETS["heldout"])
    assert len(held) >= 15
    t_corp = {c["text"] for r in tuning for c in r["corpus"] if c["id"].startswith("cv:")}
    h_corp = {c["text"] for r in held for c in r["corpus"] if c["id"].startswith("cv:")}
    assert not (t_corp & h_corp)
    assert len({r["candidate"] for r in held}) >= 2
    assert sum("german posting" in r["note"] for r in held) >= 5
    assert any(not r["gold"] for r in held)
    res = eval_retrieval.evaluate(held, "bm25")      # runs offline; numbers are reported, not tuned to
    assert res["rows"] == len(held)


# -- flag-off digest is byte-identical to the base branch -----------------------------------------------
def test_flag_off_digest_is_byte_identical_to_origin_main():
    import pathlib
    import subprocess
    root = pathlib.Path(__file__).resolve().parent.parent
    try:
        src = subprocess.run(["git", "show", "origin/main:notifier.py"], cwd=root, check=True,
                             capture_output=True, timeout=20).stdout.decode("utf-8")
    except Exception:
        pytest.skip("origin/main not available")
    base = {"__name__": "notifier_base"}
    exec(compile(src, "notifier_base.py", "exec"), base)
    jobs = [_job(), _job(id="j2", near_miss=True, missing_keywords=["sql"], cv_hint="Add SQL.",
                         app_kit=[{"q": "Why?", "a": "Because."}], ghost=True),
            _job(id="j3", _where="REMOTE", _form="student")]
    assert notifier._build_html(jobs) == base["_build_html"](jobs)
