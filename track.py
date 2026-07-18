"""
track.py — application tracker, follow-up engine, and funnel stats (feature B5).

You apply to jobs manually; this records them so the daily digest can:
  - remind you to follow up on applications >6 days old with no response,
  - show a funnel (applied → awaiting → interview → offer/rejected),
so after ~30 applications you KNOW which tracks/channels actually convert
instead of guessing.

State lives in applied_jobs.json — gitignored (this repo is PUBLIC and your
application history is personal data). After every change it is mirrored to the
APPLIED_JOBS repo secret via `gh secret set`, which is how CI sees it.

CLI (run locally — the secret sync happens automatically):
  py -3.11 track.py apply  <job-url-or-id>  ["Job Title"]  ["Company"]
  py -3.11 track.py status <job-url-or-id>  interview|rejected|offer|awaiting
  py -3.11 track.py list
  py -3.11 track.py stats
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

APPLIED_FILE = Path("applied_jobs.json")

# Valid application states.
STATES = ("applied", "awaiting", "interview", "offer", "rejected")
FOLLOWUP_DAYS = 6          # nudge after this many days with no response
STALE_AWAIT_DAYS = 14      # treat as ghosted after this many days


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(url_or_id: str) -> str:
    """A job id: if it looks like a URL, hash it; otherwise use as-is."""
    s = (url_or_id or "").strip()
    if s.startswith("http"):
        return hashlib.md5(s.lower().encode()).hexdigest()
    return s


def load_applied() -> dict:
    if APPLIED_FILE.exists():
        try:
            return json.loads(APPLIED_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_applied(data: dict) -> None:
    APPLIED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _sync_secret(data)


def _sync_secret(data: dict) -> None:
    """
    Mirror tracker state to the APPLIED_JOBS repo secret so CI can render the
    funnel/follow-up footer without this file ever being committed (public repo).
    Best-effort: if gh isn't installed/authenticated, local tracking still works.
    """
    try:
        import subprocess
        r = subprocess.run(
            ["gh", "secret", "set", "APPLIED_JOBS",
             "--body", json.dumps(data, ensure_ascii=False)],
            capture_output=True, timeout=30,
        )
        if r.returncode != 0:
            print("(note: could not sync to GitHub secret — CI won't see this "
                  "update until `gh auth login` works)")
    except Exception:
        print("(note: gh CLI not found — tracker is local-only until it is)")


def mark_applied(url_or_id: str, title: str = "", company: str = "") -> None:
    data = load_applied()
    k = _key(url_or_id)
    now = _now()
    if k in data:
        print(f"Already tracked: {data[k].get('title', k)} ({data[k].get('status')})")
        return
    data[k] = {
        "url": url_or_id if url_or_id.startswith("http") else "",
        "title": title, "company": company,
        "status": "applied", "applied_at": now, "last_change": now,
    }
    save_applied(data)
    print(f"Marked applied: {title or k} @ {company}")


def set_status(url_or_id: str, status: str) -> None:
    status = status.lower().strip()
    if status not in STATES:
        print(f"Invalid status '{status}'. Use one of: {', '.join(STATES)}")
        return
    data = load_applied()
    k = _key(url_or_id)
    if k not in data:
        print(f"Not tracked yet: {url_or_id}. Run 'apply' first.")
        return
    data[k]["status"] = status
    data[k]["last_change"] = _now()
    save_applied(data)
    print(f"Updated {data[k].get('title', k)} → {status}")


def _days_since(iso: str) -> float:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        return 0.0


def get_followups() -> list[dict]:
    """Applications still 'applied'/'awaiting' past FOLLOWUP_DAYS — need a nudge."""
    out = []
    for k, v in load_applied().items():
        if v.get("status") in ("applied", "awaiting"):
            age = _days_since(v.get("last_change") or v.get("applied_at"))
            if age >= FOLLOWUP_DAYS:
                out.append({**v, "id": k, "days": int(age),
                            "stale": age >= STALE_AWAIT_DAYS})
    out.sort(key=lambda x: -x["days"])
    return out


def get_funnel() -> dict:
    """Counts per state + a total, for the digest footer."""
    from collections import Counter
    data = load_applied()
    c = Counter(v.get("status", "applied") for v in data.values())
    return {"total": len(data), **{s: c.get(s, 0) for s in STATES}}


def followup_draft(job: dict) -> str:
    """A short, ready-to-send follow-up message (no LLM — a solid template)."""
    company = job.get("company") or "your team"
    title = job.get("title") or "the role"
    return (
        f"Hi, I applied for {title} at {company} about {job['days']} days ago and "
        f"remain very interested. I'd be glad to share anything that would help your "
        f"review — is there an update on the process, or a good time to talk briefly?"
    )


def _cli() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    cmd = args[0].lower()
    if cmd == "apply" and len(args) >= 2:
        mark_applied(args[1], args[2] if len(args) > 2 else "", args[3] if len(args) > 3 else "")
    elif cmd == "status" and len(args) >= 3:
        set_status(args[1], args[2])
    elif cmd == "list":
        data = load_applied()
        if not data:
            print("No applications tracked yet.")
        for v in sorted(data.values(), key=lambda x: x.get("applied_at", "")):
            print(f"  [{v.get('status','?'):9s}] {v.get('title','')[:45]:45s} @ {v.get('company','')}")
    elif cmd == "stats":
        f = get_funnel()
        print(f"Applications: {f['total']}  |  " +
              "  ".join(f"{s}={f[s]}" for s in STATES))
        fu = get_followups()
        if fu:
            print(f"\n{len(fu)} need follow-up (>{FOLLOWUP_DAYS}d, no response):")
            for j in fu:
                print(f"  {j['days']:3d}d  {j.get('title','')[:45]:45s} @ {j.get('company','')}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
