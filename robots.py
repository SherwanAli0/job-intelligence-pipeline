"""robots.txt checks for the scrapers (RFC 9309).

The owner's rule for this public repository: no bot evasion and no scraping
that breaks a site's terms. robots.txt is where a site says which paths
automated clients may fetch. This module was added on 2026-11-13, after an
audit found the CSB scraper reading /services/rss/job/, which robots.txt
disallowed on all 13 CSB employer sites that afternoon (several of those files
answered 404 later the same day), and a career portal whose
robots.txt blocks python-requests by name while the scrapers send a browser
User-Agent.

Policy, in one place:

* Web pages, sitemaps and site feeds requested by scrapers.py itself are
  checked with allowed() (python-jobspy and the duckduckgo_search library make
  their own requests; liveness.py calls the Workday API directly). A path that
  robots.txt disallows for "*", or for a script client this pipeline actually
  is (python-requests, curl, ...), is skipped. Sending a browser User-Agent
  does not make a block aimed at scripts stop applying.
* Documented public job APIs (Greenhouse, Lever, Ashby, SmartRecruiters,
  Recruitee, Personio XML, Workable widget, Teamtailor RSS, the Bundesagentur
  and Adzuna APIs, ...) are governed by their own documentation and terms and
  are not checked here.
* OWNER_EXCEPTIONS lists the hosts the owner has accepted as they are
  (LinkedIn and Indeed through python-jobspy and the LinkedIn guest search),
  so the exception stays visible instead of silent.

Network rules follow RFC 9309: a robots.txt that cannot be found (4xx) allows
everything; one that cannot be reached (5xx, timeout) disallows everything
for this run. One fetch per host per run, cached.

JOBHUNTER_ROBOTS=0 switches the check off (the test suite does this, as it
does for the liveness check, so no unit test touches the network).
"""
from __future__ import annotations

import os
import re
import threading
import urllib.parse
from collections import Counter

import requests

# Agent tokens this pipeline answers to. "*" is the default group; the others
# are the clients the code really uses (requests, curl_cffi through jobspy) and
# the plain "job-hunter/1.0" User-Agent the d.vinci scraper sends.
SCRIPT_AGENTS = ("*", "python-requests", "python-urllib", "python", "requests", "curl", "job-hunter")

# Hosts accepted by the owner as they are. Not checked, but listed by the
# audit tool so the exception is a decision on record.
OWNER_EXCEPTIONS = ("linkedin.com", "indeed.com")

FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
_TIMEOUT = 10

_CACHE: dict[str, tuple[str, list]] = {}
_LOCK = threading.Lock()
_HOST_LOCKS: dict[str, threading.Lock] = {}   # one fetch per host, the rest wait for it
SKIPPED: Counter = Counter()          # host -> URLs skipped this run
UNREACHABLE: set = set()              # hosts whose robots.txt could not be reached


def enabled() -> bool:
    return os.environ.get("JOBHUNTER_ROBOTS", "1").strip() != "0"


def reset() -> None:
    """Forget cached robots.txt files and skip counts (one run = one cache)."""
    with _LOCK:
        _CACHE.clear()
        SKIPPED.clear()
        UNREACHABLE.clear()


def parse(text: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """Groups of (user-agent tokens, [(allow|disallow, pattern)])."""
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    in_agents = False
    # A byte-order mark (also when decoded as ISO-8859-1) would turn the first
    # "User-agent" into an unknown field and drop that group's rules.
    text = (text or "").lstrip("\ufeff").lstrip("\xef\xbb\xbf")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = line.split(":", 1)
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if not in_agents and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.split("/", 1)[0].strip().lower())
            in_agents = True
        elif field in ("allow", "disallow"):
            if agents:
                rules.append((field, value))
            in_agents = False
    if agents:
        groups.append((agents, rules))
    return groups


def _pattern_matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern)
    return re.match(rx + ("$" if anchored else ""), path) is not None


def _rules_for(groups, agent: str) -> list[tuple[str, str]]:
    agent = agent.lower()
    own = [r for a, rs in groups if agent in a for r in rs] if agent != "*" else []
    if own or any(agent in a for a, _ in groups if agent != "*"):
        return own
    return [r for a, rs in groups if "*" in a for r in rs]


def _allowed_for(groups, agent: str, path: str) -> bool:
    best_len, best_allow = -1, True
    for kind, pattern in _rules_for(groups, agent):
        if not pattern:
            continue                     # "Disallow:" with no value allows everything
        p = urllib.parse.unquote(pattern)
        if _pattern_matches(p, path):
            n = len(p)
            allow = kind == "allow"
            if n > best_len or (n == best_len and allow):
                best_len, best_allow = n, allow
    return best_allow


def _fetch_robots(url: str):
    """The one network call in this module (tests replace it). A timeout or
    connection error is retried once, more patiently, so a passing network
    blip does not count as 'unreachable' and cost the host a whole run."""
    try:
        return requests.get(url, headers=FETCH_HEADERS, timeout=_TIMEOUT)
    except Exception:
        import time
        time.sleep(2)
        return requests.get(url, headers=FETCH_HEADERS, timeout=2 * _TIMEOUT)


def _load(scheme: str, host: str) -> tuple[str, list]:
    with _LOCK:
        if host in _CACHE:
            return _CACHE[host]
        host_lock = _HOST_LOCKS.setdefault(host, threading.Lock())
    with host_lock:
        with _LOCK:
            if host in _CACHE:          # another thread fetched it meanwhile
                return _CACHE[host]
        return _load_once(scheme, host)


def _load_once(scheme: str, host: str) -> tuple[str, list]:
    try:
        r = _fetch_robots(f"{scheme}://{host}/robots.txt")
        code = int(getattr(r, "status_code", 0) or 0)
    except Exception:
        entry = ("unreachable", [])
    else:
        if code == 200:
            try:
                content = getattr(r, "content", None)
                text = content.decode("utf-8-sig", "replace") if isinstance(content, bytes) and content \
                    else (r.text if isinstance(getattr(r, "text", None), str) else "")
                entry = ("ok", parse(text))
            except Exception:
                entry = ("ok", [])
        elif 400 <= code < 500:
            entry = ("unavailable", [])  # RFC 9309: no file, everything allowed
        else:
            entry = ("unreachable", [])  # RFC 9309: assume complete disallow
    with _LOCK:
        _CACHE[host] = entry
        if entry[0] == "unreachable":
            UNREACHABLE.add(host)
    return entry


def blocked_by(url: str, agents=SCRIPT_AGENTS) -> str:
    """"" if `url` may be fetched, otherwise the agent token whose rules block
    it, or "unreachable" when robots.txt could not be reached."""
    if not enabled():
        return ""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    host = parts.netloc.lower()
    if parts.path == "/robots.txt" or any(host == h or host.endswith("." + h) for h in OWNER_EXCEPTIONS):
        return ""
    status, groups = _load(parts.scheme, host)
    if status == "unreachable":
        return "unreachable"
    if status != "ok":
        return ""
    path = urllib.parse.unquote(parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    for agent in agents:
        if not _allowed_for(groups, agent, path):
            return agent
    return ""


def allowed(url: str) -> bool:
    """True when robots.txt lets this pipeline fetch `url`. Counts skips."""
    reason = blocked_by(url)
    if reason:
        with _LOCK:
            SKIPPED[urllib.parse.urlsplit(url).netloc.lower()] += 1
        return False
    return True


def summary_line() -> str:
    with _LOCK:
        if not SKIPPED:
            return ""
        top = ", ".join(f"{h} {n}" for h, n in SKIPPED.most_common(8))
        down = sorted(UNREACHABLE)
        return (f"robots.txt: skipped {sum(SKIPPED.values())} URLs ({top})"
                + (f"; robots.txt unreachable (5xx or network), treated as disallowed per RFC 9309: "
                   f"{', '.join(down[:8])}" if down else ""))
