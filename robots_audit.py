"""robots_audit.py - which source addresses does robots.txt allow? (2026-11-13)

Builds the list of addresses the scrapers request (feeds, sitemaps, search
pages, sample ad pages) from the source configuration and, optionally, from
recent audit_<date>.jsonl files, checks each against the host's robots.txt
with the same rules the scrapers use (robots.py), and prints a table.

    py -3.11 robots_audit.py [audit_*.jsonl ...] [--json out.json]

Rows are tagged by kind:
  page       checked by robots.allowed() in the scrapers
  api        documented public job API: governed by its own terms, shown for
             information only
  exception  owner-accepted (robots.OWNER_EXCEPTIONS), shown for information
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from collections import defaultdict

import robots


def _endpoints() -> list[tuple[str, str, str]]:
    import config
    import scrapers as s

    rows: list[tuple[str, str, str]] = []
    add = lambda src, url, kind="page": rows.append((src, url, kind))

    # Documented public APIs
    for slug in getattr(config, "GREENHOUSE_SLUGS", [])[:3]:
        add("Greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", "api")
    for slug in getattr(config, "LEVER_SLUGS", [])[:3]:
        add("Lever", f"https://api.lever.co/v0/postings/{slug}?mode=json", "api")
    for slug in getattr(config, "ASHBY_SLUGS", [])[:3]:
        add("Ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}", "api")
    for slug in getattr(config, "SMARTRECRUITERS_SLUGS", [])[:3]:
        add("SmartRecruiters", s._SR_API.format(slug=slug), "api")
    for slug in getattr(config, "PERSONIO_SLUGS", [])[:3]:
        add("Personio", f"https://{slug}.jobs.personio.de/xml", "api")
    for slug in getattr(config, "RECRUITEE_SLUGS", [])[:3]:
        add("Recruitee", f"https://{slug}.recruitee.com/api/offers/", "api")
    add("Arbeitsagentur", s._BA_SEARCH_URL, "api")
    add("Adzuna", s._ADZUNA_BASE.format(page=1), "api")
    add("Arbeitnow", "https://www.arbeitnow.com/api/job-board-api", "api")
    add("Remotive", "https://remotive.com/api/remote-jobs", "api")
    add("Jobicy", "https://jobicy.com/api/v2/remote-jobs", "api")
    for slug in [w[0] for w in getattr(s, "_WORKABLE_ACCOUNTS", ())][:3]:
        add("Workable", f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", "api")

    # Owner-accepted exceptions
    add("LinkedIn guest", s._LI_GUEST_URL + "?keywords=werkstudent&location=Germany", "exception")
    add("JobSpy Indeed", "https://de.indeed.com/jobs?q=werkstudent&l=Germany", "exception")

    # Web pages, sitemaps and site feeds
    for entry in getattr(config, "WORKDAY_CXS_TENANTS", []):
        if isinstance(entry, (tuple, list)) and len(entry) >= 3:
            t, r, site = entry[:3]
            add("Workday-CXS", f"https://{t}.{r}.myworkdayjobs.com/wday/cxs/{t}/{site}/jobs")
    for host, src in s._CSB_SITES:
        add(f"CSB {src}", f"{host}/search/?q=Student")
        add(f"CSB {src}", f"{host}/services/rss/job/?locale=de_DE&keywords=(Student)")
        add(f"CSB {src}", f"{host}/sitemap.xml")
    for site, src in s._RMK_SITES:
        add(src, f"{site}/sitemap.xml")
    for host, src, _city in s._BEESITE_HOSTS:
        add(f"BeeSite {src}", f"https://{host}/search/?data=%7B%7D")
    for slug, company, _city in s._DVINCI_TENANTS:
        base = slug if "." in slug else f"{slug}.dvinci-hr.com"
        add(f"d.vinci {company}", f"https://{base}/de/jobs")
    for page in getattr(config, "COMPANY_PAGES", []):
        add(f"Company page {page.get('name')}", page.get("url", ""))
    for name, url in (("Stellenwerk", s._STELLENWERK_SITEMAP), ("Absolventa", s._ABSOLVENTA_SITEMAP),
                      ("get-in-IT", s._GETINIT_SITEMAP), ("BWI", s._BWI_SITEMAP_INDEX),
                      ("igus", s._IGUS_API), ("REWE", s._REWE_API), ("UKB", s._UKB_API),
                      ("rhenag", s._RHENAG_URL), ("Deutsche Bahn", s._DB_API),
                      ("bund.de", s._BUND_RSS), ("Uni Bonn", s._UNIBONN_URL),
                      ("GermanTechJobs", s._GERMANTECHJOBS_RSS), ("BerlinStartupJobs", s._BSJ_RSS),
                      ("XING", s._XING_SITEMAP_INDEX), ("Amazon", s._AMAZON_API)):
        add(name, url)
    add("WebSearch (DuckDuckGo)", "https://html.duckduckgo.com/html/?q=werkstudent")
    add("WeWorkRemotely", "https://weworkremotely.com/categories/remote-programming-jobs.rss")
    add("WorkingNomads", "https://www.workingnomads.com/api/exposed_jobs/")
    for name, url, *_ in getattr(s, "_WATCHLIST", ()):
        add(f"Watchlist {name}", url)
    for name, url in getattr(s, "_RESEARCH_FEEDS", ()):
        add(name, url)
    for site in [t[0] for t in getattr(s, "_TEAMTAILOR_SITES", ())][:3]:
        add("Teamtailor", f"https://{site}/jobs.rss?per_page=1000")
    for slug in [b[0] for b in getattr(s, "_BREEZY_ACCOUNTS", ())][:3]:
        add("Breezy", f"https://{slug}.breezy.hr/json")
    if getattr(s, "_EON_SITEMAP", ""):
        add("E.ON (jobs.eon.com)", s._EON_SITEMAP)
    return rows


def _job_pages(paths: list[str], per_host: int = 2) -> list[tuple[str, str, str]]:
    by_host: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in paths:
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            url = r.get("url") or ""
            host = urllib.parse.urlsplit(url).netloc.lower()
            if host and len(by_host[host]) < per_host and url not in [u for _, u in by_host[host]]:
                by_host[host].append((r.get("source") or "?", url))
    rows = []
    for host, items in sorted(by_host.items()):
        exc = any(host == h or host.endswith("." + h) for h in robots.OWNER_EXCEPTIONS)
        for src, url in items:
            rows.append((f"ad page ({src})", url, "exception" if exc else "page"))
    return rows


def main(argv: list[str]) -> int:
    out_json = ""
    if "--json" in argv:
        i = argv.index("--json")
        out_json = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    rows = _endpoints() + (_job_pages(argv) if argv else [])
    saved = dict(robots.OWNER_EXCEPTIONS and {"x": 1})
    robots.reset()
    results = []
    exceptions = robots.OWNER_EXCEPTIONS
    for src, url, kind in rows:
        if not url:
            continue
        if kind == "exception":
            robots.OWNER_EXCEPTIONS = ()
        try:
            verdict = robots.blocked_by(url)
        finally:
            robots.OWNER_EXCEPTIONS = exceptions
        status = robots._CACHE.get(urllib.parse.urlsplit(url).netloc.lower(), ("?", []))[0]
        results.append({"source": src, "url": url, "kind": kind,
                        "verdict": verdict or "allowed", "robots": status})
    del saved
    blocked = [r for r in results if r["verdict"] != "allowed"]
    print(f"{len(results)} addresses checked, {len(blocked)} not allowed\n")
    print("| Source | Kind | Verdict | Address |")
    print("|---|---|---|---|")
    for r in sorted(blocked, key=lambda r: (r["kind"], r["source"])):
        print(f"| {r['source']} | {r['kind']} | blocked for {r['verdict']} | {r['url'][:110]} |")
    if out_json:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
