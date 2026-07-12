"""
health_check.py — monthly sweep that flags dead company boards.

Companies migrate ATS platforms every few months; when a slug 404s we silently
lose that company's jobs (Delivery Hero's 1,087 vanished this way). This script
HTTP-tests every board in config.py and reports:

  - DEAD  : HTTP 404/error — the board is GONE (needs relocation)
  - EMPTY : HTTP 200 but 0 jobs — company just has no openings (fine, kept)
  - OK    : HTTP 200 with jobs

Run:   py -3.11 health_check.py
CI:    .github/workflows/health.yml (monthly). Exits 1 only when DEAD boards
       exist, so GitHub emails a failure — EMPTY never fails the build.
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import config as C

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0"}
TIMEOUT = 10


def _greenhouse(slug):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", headers=HEADERS, timeout=TIMEOUT)
    return r.status_code, (len(r.json().get("jobs", [])) if r.status_code == 200 else 0)


def _lever(slug):
    r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", headers=HEADERS, timeout=TIMEOUT)
    return r.status_code, (len(r.json()) if r.status_code == 200 and isinstance(r.json(), list) else 0)


def _ashby(slug):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", headers=HEADERS, timeout=TIMEOUT)
    return r.status_code, (len(r.json().get("jobs", [])) if r.status_code == 200 else 0)


def _personio(slug):
    r = requests.get(f"https://{slug}.jobs.personio.de/xml", headers=HEADERS, timeout=TIMEOUT)
    return r.status_code, (r.text.count("<position") if r.status_code == 200 else 0)


def _recruitee(slug):
    r = requests.get(f"https://{slug}.recruitee.com/api/offers", headers=HEADERS, timeout=TIMEOUT)
    return r.status_code, (len(r.json().get("offers", [])) if r.status_code == 200 else 0)


def _smartrecruiters(slug):
    r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1", headers=HEADERS, timeout=TIMEOUT)
    return r.status_code, (r.json().get("totalFound", 0) if r.status_code == 200 else 0)


# (platform label, config list, checker) — extend here when a new ATS is added
_TARGETS = [
    ("greenhouse",      getattr(C, "GREENHOUSE_SLUGS", []),      _greenhouse),
    ("lever",           getattr(C, "LEVER_SLUGS", []),           _lever),
    ("ashby",           getattr(C, "ASHBY_SLUGS", []),           _ashby),
    ("personio",        getattr(C, "PERSONIO_SLUGS", []),        _personio),
    ("recruitee",       getattr(C, "RECRUITEE_SLUGS", []),       _recruitee),
    ("smartrecruiters", getattr(C, "SMARTRECRUITERS_SLUGS", []), _smartrecruiters),
]


def _probe(platform, slug, checker):
    """
    Return (platform, slug, verdict, http_status, jobs).

    Verdicts:
      OK          200 with jobs
      EMPTY       200/429 with 0 jobs — company just has no openings (kept)
      DEAD        404/410 — board genuinely removed (needs relocation → fails CI)
      UNREACHABLE network error or 403/5xx after a retry — transient/blocked,
                  NOT a migration, so it does NOT fail the build

    Only DEAD fails the build, so a timeout or a Cloudflare 403 never triggers
    a false alarm.
    """
    last_status = None
    for attempt in range(2):  # one retry to absorb transient blips
        try:
            status, n = checker(slug)
            last_status = status
            if status == 200 and n > 0:
                return (platform, slug, "OK", status, n)
            if status in (200, 429):
                return (platform, slug, "EMPTY", status, n)
            if status in (404, 410):
                return (platform, slug, "DEAD", status, 0)
            # 403 / 5xx / other — retry then treat as unreachable, not dead
        except Exception as exc:
            last_status = type(exc).__name__
        if attempt == 0:
            time.sleep(1.5)
    return (platform, slug, "UNREACHABLE", last_status, 0)


def main() -> int:
    jobs = [(p, s, fn) for p, slugs, fn in _TARGETS for s in slugs]
    print(f"Health-checking {len(jobs)} boards across {len(_TARGETS)} platforms...\n")

    results = []
    with ThreadPoolExecutor(max_workers=30) as pool:
        for fut in as_completed([pool.submit(_probe, p, s, fn) for p, s, fn in jobs]):
            results.append(fut.result())

    ok      = [r for r in results if r[2] == "OK"]
    empty   = [r for r in results if r[2] == "EMPTY"]
    unreach = [r for r in results if r[2] == "UNREACHABLE"]
    dead    = [r for r in results if r[2] == "DEAD"]

    total_jobs = sum(r[4] for r in ok)
    print(f"OK:          {len(ok):4d} boards  ({total_jobs:,} jobs live)")
    print(f"EMPTY:       {len(empty):4d} boards  (200 but 0 jobs — kept, will repopulate)")
    print(f"UNREACHABLE: {len(unreach):4d} boards  (timeout/403/5xx — transient, not failing build)")
    print(f"DEAD:        {len(dead):4d} boards  (404/410 — need relocation)\n")

    if empty:
        print("── EMPTY (informational) ──")
        for p, s, _, st, _ in sorted(empty):
            print(f"  {p:16s} {s:26s} HTTP {st}")
        print()

    if unreach:
        print("── UNREACHABLE (transient — re-check next run) ──")
        for p, s, _, st, _ in sorted(unreach):
            print(f"  {p:16s} {s:26s} {st}")
        print()

    if dead:
        print("── DEAD — RELOCATE OR REMOVE ──")
        for p, s, _, st, _ in sorted(dead):
            print(f"  {p:16s} {s:26s} {st}")
        print(f"\n{len(dead)} dead board(s). Re-probe their new ATS with a relocation sweep, "
              f"then update config.py.")
        return 1  # fail the CI build so GitHub emails a notification

    print("All boards healthy (or temporarily empty). Nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
