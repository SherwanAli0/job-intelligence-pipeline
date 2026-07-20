"""
compare_sources.py — did any job source silently degrade after a migration?

A source that returns 12 postings instead of 300 produces a completely green
run: no exception, no empty result, no alarm. The existing dead-source warning
in main.py only fires on an exact zero sustained over three runs, so partial
degradation is invisible to it. That matters when moving compute between
platforms, because LinkedIn and Indeed (via JobSpy) rate-limit datacenter IP
ranges differently, and AWS ranges are blocked more aggressively than most.

This compares per-source posting counts from run_stats.jsonl between a
baseline platform and a target platform, and reports which sources moved by
more than day-to-day noise.

Usage:
  py -3.11 compare_sources.py                                   # auto: github-actions -> aws-lambda
  py -3.11 compare_sources.py --baseline github-actions --target aws-lambda
  py -3.11 compare_sources.py --baseline-runs 5                 # widen the baseline window

Exit code is 1 if any source regressed, so it can gate a migration cutover.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

STATS_FILE = Path("run_stats.jsonl")

# Degradation is measured against the WORST healthy baseline run, not the
# median. Small sources are genuinely noisy: `indeed` swung 70 -> 46 (-34%)
# between two healthy GitHub runs purely on scrape timing, and a median-based
# test flags that as a regression. Comparing against the observed floor asks
# the right question — "is this worse than any healthy run ever was?" — which
# is noise-tolerant while still catching a block (LinkedIn 396 -> 8 is far
# below its floor of 360).
_DROP_PCT = 40        # percent below the baseline floor
_DROP_ABS = 25        # and at least this many postings fewer than the floor

# Sources whose counts depend on the caller's IP reputation. Reported even on a
# smaller drop, because these are the ones a platform migration actually breaks.
_IP_SENSITIVE = ("linkedin", "indeed", "google", "glassdoor", "ziprecruiter")
_IP_DROP_PCT = 25


def load_runs() -> list[dict]:
    if not STATS_FILE.exists():
        sys.exit(f"{STATS_FILE} not found — run the pipeline at least once first.")
    runs = []
    for line in STATS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return runs


def _platform(run: dict) -> str:
    # Runs recorded before the platform field existed came from GitHub Actions.
    return run.get("platform", "github-actions")


def build_baseline(runs: list[dict], platform: str, window: int) -> dict[str, dict]:
    """Per source: the median and the floor (worst run) across the baseline
    window, plus whether it was already dead in the most recent baseline run."""
    selected = [r for r in runs if _platform(r) == platform][-window:]
    if not selected:
        sys.exit(f"No runs found for baseline platform '{platform}'.")
    sources = set()
    for r in selected:
        sources.update((r.get("sources") or {}).keys())
    last = selected[-1].get("sources") or {}
    baseline = {}
    for src in sources:
        series = [int((r.get("sources") or {}).get(src, 0)) for r in selected]
        baseline[src] = {
            "median": statistics.median(series),
            "floor": min(series),
            "already_dead": int(last.get(src, 0)) == 0,
        }
    print(f"Baseline: {len(selected)} '{platform}' run(s), {len(baseline)} sources")
    for r in selected:
        print(f"  {r.get('ts', '?')}  {r.get('scraped', '?')} postings")
    return baseline


def compare(baseline: dict[str, dict], target: dict[str, int]) -> tuple[list[dict], list[str]]:
    """Returns (regressions, pre_existing). A source that was already at zero
    before the migration is reported separately: it's a real problem, but not
    one this migration caused, and conflating the two would either mask a true
    regression or block a cutover for an unrelated reason."""
    findings, pre_existing = [], []
    for src, stats in sorted(baseline.items(), key=lambda x: -x[1]["median"]):
        now = int(target.get(src, 0))
        median, floor = stats["median"], stats["floor"]

        if stats["already_dead"]:
            if median > 0 or now == 0:
                pre_existing.append(src)
            continue
        if median <= 0:
            continue

        ip_sensitive = any(k in src.lower() for k in _IP_SENSITIVE)
        threshold = _IP_DROP_PCT if ip_sensitive else _DROP_PCT
        # Measured against the floor: only worse-than-any-healthy-run counts.
        drop_abs = floor - now
        drop_pct = (drop_abs / floor * 100) if floor > 0 else 100.0

        if now == 0 and median >= 5:
            severity = "GONE"
        elif drop_pct >= threshold and (drop_abs >= _DROP_ABS or ip_sensitive):
            severity = "DEGRADED"
        else:
            continue
        findings.append({
            "source": src, "median": median, "floor": floor, "now": now,
            "drop_pct": drop_pct, "severity": severity,
            "ip_sensitive": ip_sensitive,
        })
    return findings, pre_existing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", default="github-actions", help="platform to compare against")
    ap.add_argument("--target", default="aws-lambda", help="platform being validated")
    ap.add_argument("--baseline-runs", type=int, default=5, help="baseline window size")
    args = ap.parse_args()

    runs = load_runs()
    target_runs = [r for r in runs if _platform(r) == args.target]
    if not target_runs:
        print(f"No '{args.target}' run recorded yet — run the pipeline there first, "
              f"then re-run this comparison.")
        return 0

    target_run = target_runs[-1]
    target = {k: int(v) for k, v in (target_run.get("sources") or {}).items()}
    baseline = build_baseline(runs, args.baseline, args.baseline_runs)

    print(f"\nTarget:   1 '{args.target}' run at {target_run.get('ts', '?')}, "
          f"{target_run.get('scraped', '?')} postings\n")

    findings, pre_existing = compare(baseline, target)

    base_total = sum(s["median"] for s in baseline.values())
    now_total = sum(target.values())
    delta = (now_total - base_total) / base_total * 100 if base_total else 0
    print(f"Total postings: {int(base_total)} -> {now_total} ({delta:+.0f}%)\n")

    if pre_existing:
        print(f"Already broken before the migration (not caused by it): "
              f"{', '.join(sorted(pre_existing))}\n")

    if not findings:
        print("No source regressed beyond normal run-to-run variance.")
        extra = [s for s in target if s not in baseline and target[s] > 0]
        if extra:
            print(f"Sources seen only on the target platform: {', '.join(sorted(extra))}")
        return 0

    print(f"{'source':22s} {'median':>7s} {'floor':>7s} {'now':>7s} {'vs floor':>9s}  flag")
    for f in findings:
        tag = f["severity"] + (" (IP-sensitive)" if f["ip_sensitive"] else "")
        print(f"{f['source']:22s} {f['median']:7.0f} {f['floor']:7.0f} {f['now']:7d} "
              f"{f['drop_pct']:8.0f}%  {tag}")

    if any(f["ip_sensitive"] for f in findings):
        print("\nAn IP-sensitive source regressed. This is the expected signature of "
              "the new platform's IP range being rate-limited or blocked, not a code "
              "bug. Confirm by running the same scrape from the old platform on the "
              "same day: if it still returns normal counts, the difference is the IP.")

    return 1


if __name__ == "__main__":
    sys.exit(main())
