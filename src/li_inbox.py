"""Postings that a LinkedIn-only read found between two email runs.

LinkedIn lets one address read only so much at a time: in the run of
18 November 10:00 it refused seven times, a measurement at noon was refused
after 20 requests, and an address it has refused stays refused for more than
an hour. Two big reads a day therefore could not read every list to its end.
LinkedIn is now also read on its own between the email runs (handler.py,
JOBHUNTER_MODE=linkedin, main.read_linkedin_only), in smaller portions, and
what such a read finds waits here until the next email run takes it in.

The next email run puts the waiting postings in front of what its own lane
finds (scrapers.scrape_linkedin). Nothing is taken out by an email run:
seen_jobs decides what is new, so a posting handled once is skipped the next
time, and one whose page could not be fetched is met again. A LinkedIn-only
read rewrites the file without the postings seen since and without those it
first found more than KEEP_HOURS ago.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import storage

INBOX_FILE = "linkedin_inbox.jsonl"
KEEP_HOURS = 72          # more than the 48 hours of the freshness rule


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _rows(text: str):
    for line in (text or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id") and row.get("url"):
            yield row


def load(skip_ids=frozenset(), now: datetime | None = None) -> list[dict]:
    """The postings waiting, without those in `skip_ids` (seen_jobs) and those
    first found more than KEEP_HOURS ago. Never raises: without the file an
    email run reads LinkedIn as it did before these reads existed."""
    try:
        text = storage.read_text(INBOX_FILE) or ""
    except Exception as e:
        print(f"  [LinkedIn] the postings of the reads in between could not be read "
              f"({type(e).__name__}): this run goes without them")
        return []
    limit = (now or _now()) - timedelta(hours=KEEP_HOURS)
    out, ids = [], set()
    for row in _rows(text):
        found = _parse(row.get("_collected"))
        if row["id"] in skip_ids or row["id"] in ids or found is None or found < limit:
            continue
        ids.add(row["id"])
        out.append(row)
    return out


def save(waiting: list[dict], found: list[dict], skip_ids=frozenset(),
         now: datetime | None = None) -> int | None:
    """Write the file: the postings still waiting and the ones found now.
    A posting found again keeps the time it was first found and the longer of
    its texts. Returns the number of postings written, or None if the write
    failed (then the caller must not move the search marks: what this read
    found would otherwise count as read and reach nobody)."""
    now = now or _now()
    stamp = now.isoformat(timespec="seconds")
    limit = now - timedelta(hours=KEEP_HOURS)
    best: dict[str, dict] = {}
    for row in list(waiting) + [dict(r, _collected=stamp) for r in found]:
        if not row.get("id") or not row.get("url") or row["id"] in skip_ids:
            continue
        first = _parse(row.get("_collected"))
        if first is None or first < limit:
            continue
        held = best.get(row["id"])
        if held is None:
            best[row["id"]] = dict(row)
            continue
        if len(row.get("description") or "") > len(held.get("description") or ""):
            held["description"] = row["description"]
        if first < _parse(held["_collected"]):
            held["_collected"] = row["_collected"]
    text = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in best.values())
    try:
        storage.write_text(INBOX_FILE, text)
    except Exception as e:
        print(f"  [LinkedIn] the postings found could not be kept for the next run ({type(e).__name__})")
        return None
    return len(best)
