"""
notifier.py — delivers scored job results via Gmail (HTML digest) and/or Notion.
"""

import os
import smtplib
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Gmail ──────────────────────────────────────────────────────────────────────

SCORE_COLOR = {
    "excellent": "#16a34a",   # green  85+
    "good":      "#2563eb",   # blue   70–84
    "decent":    "#d97706",   # amber  55–69
    "weak":      "#ea580c",   # orange 40–54
    "longshot":  "#71717a",   # gray   1–39
}

# A job posted this recently gets the 🔥 fresh badge — first-mover advantage,
# applications submitted within 24h convert ~4× better
FRESH_HOURS = 12


def _score_label(score: int) -> tuple[str, str]:
    """Map a 0-100 fit score to a label + colour for the digest table."""
    if score >= 85:
        return "Excellent", SCORE_COLOR["excellent"]
    if score >= 70:
        return "Good", SCORE_COLOR["good"]
    if score >= 55:
        return "Decent", SCORE_COLOR["decent"]
    if score >= 40:
        return "Weak", SCORE_COLOR["weak"]
    return "Long shot", SCORE_COLOR["longshot"]


def _hours_since(posted_at) -> float | None:
    """
    Return hours since the job was posted, or None if timestamp missing/bad.
    Accepts ISO 8601 strings, "YYYY-MM-DD HH:MM:SS", and "YYYY-MM-DD".
    """
    if posted_at is None:
        return None
    s = str(posted_at).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    try:
        # ISO 8601 (with or without Z, with or without microseconds)
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        elif " " in s and ":" in s:
            # "YYYY-MM-DD HH:MM:SS" — try parsing the first 19 characters
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        else:
            # Date-only "YYYY-MM-DD"
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 3600.0)
    except Exception:
        return None


def _freshness_badge(posted_at: str) -> str:
    """HTML snippet for the fresh-job indicator. Empty if not fresh or unknown."""
    hours = _hours_since(posted_at)
    if hours is None:
        return ""
    if hours <= FRESH_HOURS:
        # Truly fresh — 🔥 first-mover badge
        return (
            '<span style="background:#fef3c7;color:#b45309;padding:2px 8px;'
            'border-radius:10px;font-size:11px;font-weight:600;margin-left:6px;'
            f'">🔥 {int(hours)}h ago</span>'
        )
    if hours <= 48:
        # Still recent — quiet timestamp
        return (
            '<span style="color:#9ca3af;font-size:11px;margin-left:6px;">'
            f'{int(hours)}h ago</span>'
        )
    if hours <= 24 * 7:
        days = int(hours / 24)
        return (
            '<span style="color:#9ca3af;font-size:11px;margin-left:6px;">'
            f'{days}d ago</span>'
        )
    return ""


def _build_html(jobs: list[dict]) -> str:
    today = date.today().strftime("%d %b %Y")

    # Sort jobs so that fresh ones bubble to the top within their score band
    def _sort_key(j):
        h = _hours_since(j.get("posted_at", ""))
        # primary: score (desc), secondary: fresher first (None → far past)
        fresh_rank = h if h is not None else 1e9
        return (-j.get("score", 0), fresh_rank)
    jobs = sorted(jobs, key=_sort_key)

    # Near misses (35–44, flagged by main.py) render in their own dimmed
    # section below the main table, so the score cutoff stops hiding jobs.
    main_jobs = [j for j in jobs if not j.get("near_miss")]
    near_jobs = [j for j in jobs if j.get("near_miss")]

    rows = ""
    near_rows = ""
    fresh_count = 0
    for j in jobs:
        label, color = _score_label(j["score"])
        badge = _freshness_badge(j.get("posted_at", ""))
        if "🔥" in badge:
            fresh_count += 1

        # ── Enrichment extras (all additive; render only when present) ────────
        # Direct-apply link: apply at the company's own ATS, not the board relay.
        apply_url = (j.get("apply_url") or "").strip()
        apply_html = (
            f' &nbsp;<a href="{apply_url}" style="color:#16a34a;font-size:12px;'
            f'font-weight:600;text-decoration:none;">⚡ apply direct</a>'
        ) if apply_url and apply_url.lower().startswith("http") else ""

        # Salary band
        salary = (j.get("salary") or "").strip()
        salary_html = (
            f'<span style="color:#059669;font-size:12px;"> · 💶 {salary}</span>'
        ) if salary else ""

        # Named contact / email / phone pulled from the ad body
        contact = (j.get("contact") or "").strip()
        contact_html = (
            f'<br><span style="color:#0369a1;font-size:12px;">📇 {contact}</span>'
        ) if contact else ""

        # Keyword-gap tailoring hint
        mk = j.get("missing_keywords") or []
        hint = (j.get("cv_hint") or "").strip()
        tailor_html = ""
        if mk or hint:
            kw = ", ".join(str(x) for x in mk[:8])
            kw_line = (f'<b>Tailor — add/emphasise:</b> {kw}' if kw else "")
            hint_line = (f'<br>{hint}' if hint else "")
            tailor_html = (
                f'<div style="margin-top:5px;padding:6px 9px;background:#fffbeb;'
                f'border-radius:6px;border:1px solid #fde68a;color:#92400e;'
                f'font-size:12px;">✍️ {kw_line}{hint_line}</div>'
            )

        row_html = f"""
        <tr{' style="opacity:0.75;"' if j.get('near_miss') else ''}>
          <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;">
            <a href="{j['url']}" style="font-weight:600;color:#111827;text-decoration:none;font-size:14px;">
              {j['title']}
            </a>{badge}{apply_html}<br>
            <span style="color:#6b7280;font-size:13px;">{j['company']} · {j['location']}{salary_html}</span>{contact_html}<br>
            <span style="color:#6b7280;font-size:12px;font-style:italic;">{j.get('reason','')}</span>
            {tailor_html}
          </td>
          <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;text-align:center;white-space:nowrap;">
            <span style="background:{color};color:#fff;padding:3px 10px;border-radius:12px;font-size:13px;font-weight:600;">
              {j['score']} — {label}
            </span><br>
            <span style="color:#9ca3af;font-size:11px;">{j['source']}</span>
          </td>
        </tr>"""
        if j.get("near_miss"):
            near_rows += row_html
        else:
            rows += row_html

    # Near-miss section — only rendered when main.py flagged any
    near_html = ""
    if near_rows:
        near_html = f"""
      <div style="margin-top:22px;padding:10px 14px;background:#f4f4f5;border-radius:6px 6px 0 0;">
        <div style="font-size:13px;font-weight:700;color:#52525b;">🕵️ Near misses ({len(near_jobs)}) — scored 35–44, just under the cutoff</div>
        <div style="font-size:11px;color:#a1a1aa;">The scorer wasn't convinced. Skim them anyway — miscalibrated good matches land here.</div>
      </div>
      <table style="width:100%;border-collapse:collapse;">
        <tbody>{near_rows}</tbody>
      </table>"""

    fresh_summary = ""
    if fresh_count:
        fresh_summary = f" · 🔥 {fresh_count} posted in last {FRESH_HOURS}h"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f9fafb;margin:0;padding:20px;">
  <div style="max-width:720px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;
              box-shadow:0 1px 3px rgba(0,0,0,.1);">

    <div style="background:#1e3a5f;padding:24px 28px;">
      <h1 style="color:#fff;margin:0;font-size:20px;">🎯 Job Digest — {today}</h1>
      <p style="color:#93c5fd;margin:6px 0 0;font-size:14px;">
        {len(main_jobs)} new matches{f" · {len(near_jobs)} near misses" if near_jobs else ""} · fresh first{fresh_summary}
      </p>
    </div>

    <div style="padding:20px 28px;">
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="background:#f3f4f6;">
            <th style="padding:8px;text-align:left;font-size:12px;color:#6b7280;text-transform:uppercase;">Role</th>
            <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;text-transform:uppercase;width:150px;">Score</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      {near_html}
    </div>

    <div style="padding:16px 28px;background:#f9fafb;border-top:1px solid #e5e7eb;">
      <p style="color:#9ca3af;font-size:12px;margin:0;">
        Auto-generated by your job hunter · <a href="https://github.com" style="color:#9ca3af;">View repo</a>
      </p>
    </div>
  </div>
</body>
</html>"""


def send_email(jobs: list[dict]) -> None:
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    gmail_to   = os.environ.get("GMAIL_TO", gmail_user)

    if not gmail_user or not gmail_pass:
        print("  [Email] GMAIL_USER or GMAIL_APP_PASSWORD not set, skipping.")
        return

    today = date.today().strftime("%d %b %Y")
    fresh_count = sum(
        1 for j in jobs
        if (h := _hours_since(j.get("posted_at", ""))) is not None and h <= FRESH_HOURS
    )
    subject_prefix = f"🔥 {fresh_count} fresh · " if fresh_count else "🎯 "

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{subject_prefix}{len(jobs)} job matches — {today}"
    msg["From"]    = gmail_user
    msg["To"]      = gmail_to

    html = _build_html(jobs)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, gmail_to, msg.as_string())
        print(f"  [Email] Sent digest with {len(jobs)} jobs ({fresh_count} fresh) to {gmail_to}")
    except Exception as e:
        print(f"  [Email] Failed: {e}")


# ── Notion ─────────────────────────────────────────────────────────────────────

def add_to_notion(jobs: list[dict]) -> None:
    notion_token = os.environ.get("NOTION_TOKEN")
    database_id  = os.environ.get("NOTION_DATABASE_ID")

    if not notion_token or not database_id:
        print("  [Notion] NOTION_TOKEN or NOTION_DATABASE_ID not set, skipping.")
        return

    try:
        from notion_client import Client  # type: ignore
        notion = Client(auth=notion_token)

        for j in jobs:
            label, _ = _score_label(j["score"])
            try:
                notion.pages.create(
                    parent={"database_id": database_id},
                    properties={
                        "Title": {
                            "title": [{"text": {"content": j["title"]}}]
                        },
                        "Company": {
                            "rich_text": [{"text": {"content": j["company"]}}]
                        },
                        "Location": {
                            "rich_text": [{"text": {"content": j["location"]}}]
                        },
                        "Score": {
                            "number": j["score"]
                        },
                        "Label": {
                            "select": {"name": label}
                        },
                        "Source": {
                            "select": {"name": j["source"]}
                        },
                        "URL": {
                            "url": j["url"] or None
                        },
                        "Reason": {
                            "rich_text": [{"text": {"content": j.get("reason", "")}}]
                        },
                        "Status": {
                            "select": {"name": "New"}
                        },
                        "Date": {
                            "date": {"start": date.today().isoformat()}
                        },
                    },
                )
            except Exception as e:
                print(f"  [Notion] Could not add '{j['title']}': {e}")

        print(f"  [Notion] Added {len(jobs)} jobs to database")
    except ImportError:
        print("  [Notion] notion-client not installed, skipping.")
    except Exception as e:
        print(f"  [Notion] Failed: {e}")
