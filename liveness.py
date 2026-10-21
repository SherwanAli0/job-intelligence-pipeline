"""
liveness.py - is a posting still open? Checked just before the digest goes out.

The freshness filter reads DATES; a date says nothing about whether the employer
has taken the ad down since. For employer career sites with a public JSON API, a
closed posting answers HTTP 404 / 410, which is definitive (verified live
2026-10-15 for Greenhouse, Lever, SmartRecruiters, Workday and the Bundesagentur
detail API). Everything else - LinkedIn, Indeed, XING, Adzuna and every URL we
cannot map to such an API - is never requested and counts as open: a job is
dropped only on a definitive "gone", never on a timeout, a 5xx or a surprise.

At most one small GET per ranked job (about 55 a day), six at a time.
JOBHUNTER_LIVENESS=0 switches the check off.
"""
import base64
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126"}
TIMEOUT = 8
WORKERS = 6

_GH = re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([^/?#]+)/jobs/(\d+)", re.IGNORECASE)
_LEVER = re.compile(r"jobs(\.eu)?\.lever\.co/([^/?#]+)/([0-9a-f]{8}-[0-9a-f-]{27})", re.IGNORECASE)
_SR = re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)", re.IGNORECASE)
_WD = re.compile(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com(/[^?#]*)", re.IGNORECASE)
_BA = re.compile(r"arbeitsagentur\.de/jobsuche/jobdetail/([^/?#]+)", re.IGNORECASE)
_BA_DETAIL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails"


def enabled() -> bool:
    return os.environ.get("JOBHUNTER_LIVENESS", "1").strip() != "0"


def _workday_site(tenant: str, region: str) -> str | None:
    """The site name of a Workday tenant from the scraper's own list, or None
    when it is not listed or ambiguous (a wrong site would answer 404 for a job
    that is perfectly open)."""
    try:
        import scrapers
        sites = {e[2] for e in scrapers.WORKDAY_CXS_TENANTS
                 if isinstance(e, (tuple, list)) and len(e) >= 3
                 and str(e[0]).lower() == tenant.lower() and str(e[1]).lower() == region.lower()}
    except Exception:
        return None
    return next(iter(sites)) if len(sites) == 1 else None


def probe(url: str) -> tuple[str, dict] | None:
    """(API url, headers) whose 404/410 means 'this posting is gone', or None."""
    url = url or ""
    m = _GH.search(url)
    if m:
        return f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}", HEADERS
    m = _LEVER.search(url)
    if m:
        host = "api.eu.lever.co" if m.group(1) else "api.lever.co"
        return f"https://{host}/v0/postings/{m.group(2)}/{m.group(3)}", HEADERS
    m = _SR.search(url)
    if m:
        return f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}", HEADERS
    m = _BA.search(url)
    if m:
        enc = base64.b64encode(m.group(1).encode()).decode()
        return f"{_BA_DETAIL}/{enc}", {**HEADERS, "X-API-Key": "jobboerse-jobsuche"}
    m = _WD.search(url)
    if m:
        tenant, region, path = m.group(1), m.group(2), m.group(3)
        segs = [s for s in path.split("/") if s]
        if "job" not in segs:
            return None
        i = segs.index("job")
        site = segs[i - 1] if i > 0 else _workday_site(tenant, region)
        if not site:
            return None
        ext = "/" + "/".join(segs[i:])
        return (f"https://{tenant}.{region}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{ext}",
                {**HEADERS, "Accept": "application/json"})
    return None


def check(url: str) -> str:
    """'closed' (definitive), 'open' (the API answered 200) or 'unknown'."""
    p = probe(url)
    if not p:
        return "unknown"
    api, headers = p
    try:
        r = requests.get(api, headers=headers, timeout=TIMEOUT)
    except Exception:
        return "unknown"
    if r.status_code in (404, 410):
        return "closed"
    return "open" if r.status_code == 200 else "unknown"


# Circuit breaker: if an employer API route starts answering 404 for (nearly)
# every posting - a changed URL scheme, a WAF page - the answers are worthless.
# A family (same API host) with at least this many probes of which this share
# is "closed" is treated as unknown: its jobs stay in the digest and a warning
# is raised instead of the whole source silently vanishing.
BREAKER_MIN_PROBES = 5
BREAKER_SHARE = 0.6
LAST_WARNINGS: list[str] = []


def drop_closed(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """(still-open-or-unknown jobs in the original order, closed jobs).
    Warnings of the last call are in LAST_WARNINGS."""
    LAST_WARNINGS.clear()
    if not jobs or not enabled():
        return list(jobs), []
    todo = sorted({j.get("url") or "" for j in jobs if probe(j.get("url") or "")})
    verdict: dict[str, str] = {}
    if todo:
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(todo))) as pool:
            verdict = dict(zip(todo, pool.map(check, todo)))
        families: dict[str, list[str]] = {}
        for u in todo:
            families.setdefault(urlparse(probe(u)[0]).netloc, []).append(u)
        for host, urls in families.items():
            n_closed = sum(1 for u in urls if verdict[u] == "closed")
            if len(urls) >= BREAKER_MIN_PROBES and n_closed / len(urls) >= BREAKER_SHARE:
                for u in urls:
                    verdict[u] = "unknown"
                LAST_WARNINGS.append(
                    f"Liveness check ignored for {host}: {n_closed} of {len(urls)} postings answered "
                    f"'closed' - the API route has probably changed, so none of them was dropped.")
    kept, closed = [], []
    for j in jobs:
        (closed if verdict.get(j.get("url") or "") == "closed" else kept).append(j)
    return kept, closed
