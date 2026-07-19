"""
calibrate.py — measure the scoring pipeline against the hand-labeled golden set.

The 180-line scoring prompt gets edited often; without measurement every edit
is vibes. This runs golden/golden_set.jsonl through the REAL pipeline and
reports where reality disagrees with the labels, so a prompt change can be
judged before it decides which jobs you ever see.

Two layers:
  FREE  — expect_dq cases go through scorer._hard_disqualify (deterministic,
          no API). Also run in CI by tests/test_golden.py on every push.
  PAID  — expect_band cases go through the real Haiku scorer per track
          (~EUR 0.07/run). Manual only:  py -3.11 calibrate.py
          or the 'Calibrate scorer' workflow_dispatch action.

Band definitions (score ranges the label maps to):
  high 70-100 | mid 45-69 | low 0-44

Edit the labels in golden/golden_set.jsonl as you learn — the set is only as
good as its labels. Add every future mis-scored job you notice as a new line.
"""

import argparse
import json
import sys
from pathlib import Path

GOLDEN_FILE = Path("golden") / "golden_set.jsonl"
BANDS = {"high": (70, 100), "mid": (45, 69), "low": (0, 44)}


def load_golden() -> list[dict]:
    rows = []
    for line in GOLDEN_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _as_job(g: dict) -> dict:
    return {
        "id": g["id"], "title": g["title"], "company": g["company"],
        "location": g["location"], "description": g["description"],
        "url": "https://golden.example/" + g["id"], "source": "Golden",
        "posted_at": "",
    }


def run_prescreen(rows: list[dict]) -> tuple[int, list[str]]:
    """FREE layer: every expect_dq case must be disqualified; every
    expect_band case must SURVIVE the pre-screen. Returns (failures, lines)."""
    from scorer import _hard_disqualify
    failures, lines = 0, []
    for g in rows:
        dq, reason, cat = _hard_disqualify(_as_job(g))
        if g.get("expect_dq"):
            ok = dq and (not g.get("dq_category") or cat == g["dq_category"])
            if not ok:
                failures += 1
            lines.append(f"  {'OK ' if ok else 'FAIL'} {g['id']} expected DQ({g.get('dq_category','any')}) "
                         f"-> got {'DQ(' + cat + ')' if dq else 'PASSED'}  [{g['title']}]")
        else:
            ok = not dq
            if not ok:
                failures += 1
            lines.append(f"  {'OK ' if ok else 'FAIL'} {g['id']} expected survive "
                         f"-> got {'DQ: ' + reason if dq else 'survived'}  [{g['title']}]")
    return failures, lines


def run_llm(rows: list[dict]) -> int:
    """PAID layer: band accuracy of the real per-track Haiku scorer."""
    from scorer import _score_batch, _TRACK_PROFILES, HAIKU_MODEL

    cases = [g for g in rows if not g.get("expect_dq")]
    by_track: dict[str, list[dict]] = {}
    for g in cases:
        by_track.setdefault(g.get("track", "AI"), []).append(g)

    results = []
    for track, group in by_track.items():
        jobs = [_as_job(g) for g in group]
        profile = _TRACK_PROFILES.get(track)
        _score_batch(jobs, model=HAIKU_MODEL, cv_profile=profile)
        for g, j in zip(group, jobs):
            score = j.get("score", 0)
            lo, hi = BANDS[g["expect_band"]]
            hit = lo <= score <= hi
            results.append((g, score, hit))

    hits = sum(1 for _, _, h in results if h)
    print(f"\n── LLM band calibration: {hits}/{len(results)} in expected band ──")
    for g, score, hit in sorted(results, key=lambda r: r[2]):
        lo, hi = BANDS[g["expect_band"]]
        mark = "OK  " if hit else "MISS"
        print(f"  {mark} {g['id']} [{g.get('track','?')}] scored {score:3d}, "
              f"expected {g['expect_band']} ({lo}-{hi})  {g['title']} — {g.get('note','')}")
    acc = hits / len(results) if results else 0.0
    print(f"\nBand accuracy: {acc:.0%}  "
          f"(edit golden/golden_set.jsonl labels if the label, not the scorer, is wrong)")
    return 0 if acc >= 0.6 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--free-only", action="store_true",
                    help="Run only the deterministic pre-screen layer (no API calls)")
    args = ap.parse_args()

    import os
    if args.free_only:
        # scorer.py builds an API client at import; the free layer never uses it
        os.environ.setdefault("ANTHROPIC_API_KEY", "sk-free-only-dummy")
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — use --free-only for the no-API layer.")
        return 2

    rows = load_golden()
    print(f"Golden set: {len(rows)} labeled cases "
          f"({sum(1 for g in rows if g.get('expect_dq'))} pre-screen, "
          f"{sum(1 for g in rows if not g.get('expect_dq'))} LLM-band)")

    failures, lines = run_prescreen(rows)
    print(f"\n── Pre-screen layer: {len(lines) - failures}/{len(lines)} correct ──")
    print("\n".join(lines))
    if args.free_only:
        return 1 if failures else 0

    rc = run_llm(rows)
    return 1 if failures else rc


if __name__ == "__main__":
    sys.exit(main())
