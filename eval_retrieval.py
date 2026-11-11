"""
eval_retrieval.py - offline evaluation of the evidence retriever.

Reads golden/evidence_eval_tuning.jsonl (in-sample) and
golden/evidence_eval_heldout.jsonl (never tuned on): fictional labelled pairs of (requirement text,
candidate corpus, ids of the correct evidence chunks). A row with an empty
`gold` list is a true gap: the candidate has no evidence for it.

Reports, per backend:
  recall@1, recall@3, MRR       over rows that have gold evidence (retrieval rank
                                of the best gold chunk, whole corpus ranked)
  gap precision / recall        a prediction is "gap" when match_requirement
                                returns status gap; met and partial both count as
                                "evidence found". Language rows go through the
                                level check, exactly as in production.

Free and offline: BM25 needs no network. The embedding backend runs only when
sentence-transformers is installed (its model loader may download weights).

    python eval_retrieval.py            # BM25, plus embeddings if installed
    python eval_retrieval.py --verbose  # also list every miss
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import evidence

GOLDEN = Path(__file__).resolve().parent / "golden"
# The tuning set is what the stop list, stemmer, aliases and thresholds were
# adjusted on, so its numbers are in-sample. The held-out set uses corpora that
# were written afterwards and never used to tune anything: report it first.
SETS = {"tuning": GOLDEN / "evidence_eval_tuning.jsonl",
        "heldout": GOLDEN / "evidence_eval_heldout.jsonl"}
DATA = SETS["tuning"]


def load_rows(path: Path = DATA) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _rank_of_gold(retriever, row: dict) -> int | None:
    hits = retriever.search(row["requirement"], len(row["corpus"]))
    gold = set(row["gold"])
    for pos, (chunk, _, _) in enumerate(hits, start=1):
        if chunk["id"] in gold:
            return pos
    return None


def evaluate(rows: list[dict], backend: str = "bm25") -> dict:
    pos_rows = [r for r in rows if r["gold"]]
    gap_rows = [r for r in rows if not r["gold"]]
    ranks, misses = [], []
    tp = fp = fn = 0
    for row in rows:
        retr = (evidence.EmbeddingRetriever(row["corpus"]) if backend == "embeddings"
                else evidence.BM25Retriever(row["corpus"]))
        predicted_gap = evidence.match_requirement(
            row["requirement"], retr, row["corpus"])["status"] == "gap"
        if row["gold"]:
            rank = _rank_of_gold(retr, row)
            ranks.append(rank)
            if rank != 1:
                misses.append((row["id"], "rank %s" % rank, row["requirement"]))
            if predicted_gap:
                fp += 1
                misses.append((row["id"], "false gap", row["requirement"]))
        else:
            if predicted_gap:
                tp += 1
            else:
                fn += 1
                misses.append((row["id"], "missed gap", row["requirement"]))
    n = len(pos_rows) or 1
    return {
        "backend": backend,
        "rows": len(rows), "with_evidence": len(pos_rows), "true_gaps": len(gap_rows),
        "recall@1": sum(1 for r in ranks if r == 1) / n,
        "recall@3": sum(1 for r in ranks if r is not None and r <= 3) / n,
        "mrr": sum(1.0 / r for r in ranks if r) / n,
        "gap_precision": tp / (tp + fp) if tp + fp else 0.0,
        "gap_recall": tp / (tp + fn) if tp + fn else 0.0,
        "misses": misses,
    }


def format_report(res: dict, verbose: bool = False) -> str:
    out = [
        "backend %-10s rows=%d (with evidence %d, true gaps %d)" % (
            res["backend"], res["rows"], res["with_evidence"], res["true_gaps"]),
        "  recall@1 %.3f   recall@3 %.3f   MRR %.3f" % (res["recall@1"], res["recall@3"], res["mrr"]),
        "  gap precision %.3f   gap recall %.3f" % (res["gap_precision"], res["gap_recall"]),
    ]
    if verbose:
        for rid, kind, req in res["misses"]:
            out.append("  miss %s [%s] %s" % (rid, kind, req))
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Evidence retriever evaluation")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    for name in ("heldout", "tuning"):
        rows = load_rows(SETS[name])
        label = "held-out" if name == "heldout" else "tuning (in-sample)"
        print("== %s set ==" % label)
        print(format_report(evaluate(rows, "bm25"), args.verbose))
        if evidence.EmbeddingRetriever.available():
            print(format_report(evaluate(rows, "embeddings"), args.verbose))
        else:
            print("backend embeddings: skipped (sentence-transformers not installed)")


if __name__ == "__main__":
    main()
