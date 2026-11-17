"""What each search has been read to its end.

A job board hands a search out in pages. A run that stops early (a block, a
time limit, the board's own result cap) has not seen the rest, and the next
run, asking only for the newest hours, does not see it either: the ad turns
up days later through some wider search, by then too old for the freshness
rule. That is how an ad was lost although the pipeline ran every day.

This module keeps, for every search, the time up to which it has been read
completely. The next run asks that search for everything since then, plus
slack, so a search that could not be finished keeps its place instead of
losing its tail. The marks are saved only by a run that delivered its
digest, like seen_jobs: a run whose results were thrown away has read
nothing.
"""
from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timedelta, timezone

import storage

MARKS_FILE = "search_marks.json"
SLACK_HOURS = 2      # a board lists an ad some time after the ad's own stamp
MAX_HOURS = 168      # the widest window a search is ever asked for
FORGET_DAYS = 30     # a search that is no longer asked leaves the file

_lock = threading.Lock()
_marks: dict[str, dict] = {}       # key -> {"covered": iso, "asked": iso}
_outcomes: dict[str, dict] = {}    # this run only
_run_start: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _aware(when: datetime | None) -> datetime | None:
    """A time without a zone is taken as UTC."""
    if when is not None and when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when


def begin(now: datetime | None = None) -> None:
    """Start of a run: read the marks and forget the last run's outcomes.
    Never raises: without the file every search is asked for its normal
    window, which is what happened before this module existed."""
    global _run_start
    marks: dict[str, dict] = {}
    try:
        raw = storage.read_text(MARKS_FILE)
        data = json.loads(raw) if raw else {}
        if isinstance(data, dict):
            marks = {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
    except Exception as e:
        print(f"  [Sweep] search marks unreadable ({type(e).__name__}): normal windows this run")
    with _lock:
        _marks.clear()
        _marks.update(marks)
        _outcomes.clear()
        _run_start = _aware(now) or _now()


def run_start() -> datetime:
    return _run_start or _now()


def covered_until(key: str) -> datetime | None:
    """The time up to which `key` has been read completely, if known."""
    with _lock:
        return _parse((_marks.get(key) or {}).get("covered"))


def hours_unread(key: str, now: datetime | None = None) -> float:
    """Hours since `key` was last read completely; 0 for a search without a
    mark. Searches read in the same run share one mark, so they compare equal
    and a search that was cut short stands out."""
    covered = covered_until(key)
    if covered is None:
        return 0.0
    hours = ((_aware(now) or run_start()) - covered).total_seconds() / 3600.0
    return round(hours, 2) if math.isfinite(hours) and hours > 0 else 0.0


def window_hours(key: str, default_hours: int, now: datetime | None = None) -> int:
    """Hours to ask `key` for: everything since it was last read to its end,
    plus slack. The normal window when the search has no mark yet."""
    covered = covered_until(key)
    if covered is None:
        return _hours(default_hours)
    hours = ((_aware(now) or run_start()) - covered).total_seconds() / 3600.0
    if not math.isfinite(hours) or hours <= 0:
        return max(1, SLACK_HOURS)
    return int(min(MAX_HOURS, max(1, math.ceil(hours) + SLACK_HOURS)))


def _hours(value, fallback: int = 24) -> int:
    """A number of hours as an int from 1 to MAX_HOURS, whatever came in."""
    try:
        hours = int(value)
    except (TypeError, ValueError, OverflowError):
        hours = fallback
    return int(min(MAX_HOURS, max(1, hours)))


def record(key: str, complete: bool, asked_hours: int, why: str = "", **numbers) -> None:
    """Outcome of one search in this run. `complete` means: read as far as planned."""
    with _lock:
        _outcomes[key] = {"complete": bool(complete), "hours": _hours(asked_hours),
                          "why": str(why)[:60], **numbers}


def outcomes(prefix: str = "") -> dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _outcomes.items() if k.startswith(prefix)}


def summary(prefix: str = "") -> dict:
    """Counts for the run log and the run statistics."""
    rows = outcomes(prefix)
    cut = [k for k, v in rows.items() if not v["complete"]]
    reasons: dict[str, int] = {}
    for k in cut:
        reasons[rows[k]["why"] or "?"] = reasons.get(rows[k]["why"] or "?", 0) + 1
    return {"searches": len(rows), "complete": len(rows) - len(cut), "cut_short": len(cut),
            "reasons": reasons}


def overdue(hours: float, prefix: str = "", now: datetime | None = None) -> list[tuple[str, float]]:
    """Searches of this run that were cut short and whose last complete read
    lies more than `hours` back: (key, hours since), longest first. From then
    on the freshness rule can drop what they find late."""
    now = _aware(now) or run_start()
    out = []
    for key, row in outcomes(prefix).items():
        if row["complete"]:
            continue
        covered = covered_until(key)
        since = ((now - covered).total_seconds() / 3600.0 if covered is not None
                 else float(row["hours"]))
        if since > hours:
            out.append((key, round(since, 1)))
    return sorted(out, key=lambda x: -x[1])


def save() -> None:
    """Move the mark of every search this run read to its end to the start of
    this run. Called only after a delivered run. Never raises: the marks are
    a refinement, and the run that calls this has already delivered."""
    try:
        _save()
    except Exception as e:
        print(f"  [Sweep] could not save the search marks ({type(e).__name__})")


def _save() -> None:
    start = run_start()
    stamp = start.isoformat(timespec="seconds")
    with _lock:
        if not _outcomes:
            return                      # no search reported: nothing has changed
        for key, row in _outcomes.items():
            mark = _marks.setdefault(key, {})
            if row["complete"]:
                mark["covered"] = stamp
            elif _parse(mark.get("covered")) is None:
                # First try, not finished: the stretch it could not cover
                # starts where its window started.
                mark["covered"] = (start - timedelta(hours=row["hours"])).isoformat(timespec="seconds")
            mark["asked"] = stamp
        limit = start - timedelta(days=FORGET_DAYS)
        kept = {k: v for k, v in _marks.items() if (_parse(v.get("asked")) or start) >= limit}
        text = json.dumps(dict(sorted(kept.items())), indent=1)
        _outcomes.clear()               # a run is saved once
    storage.write_text(MARKS_FILE, text)
