"""Local read-only dashboard for reviewing captured influencer data."""

from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .eligibility import eligibility_error, is_human_indian_micro_creator
from .models import ProfileObservation


DEFAULT_DATABASE = "data/microindia.sqlite3"


def esc(value: Any) -> str:
    return html.escape("—" if value is None or value == "" else str(value))


def format_number(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def format_percent(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.3f}%"


class DashboardRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def list_captures(self, query: str = "") -> List[Dict[str, Any]]:
        connection = self.connection()
        try:
            if query:
                rows = connection.execute(
                    """SELECT c.*, p.payload AS profile_payload
                    FROM profile_captures c
                    LEFT JOIN profile_snapshots p ON p.capture_id = c.capture_id
                    WHERE c.profile_url LIKE ? OR c.candidate_key LIKE ? OR p.payload LIKE ?
                    ORDER BY c.captured_at DESC""",
                    (f"%{query}%", f"%{query}%", f"%{query}%"),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT c.*, p.payload AS profile_payload
                    FROM profile_captures c
                    LEFT JOIN profile_snapshots p ON p.capture_id = c.capture_id
                    ORDER BY c.captured_at DESC"""
                ).fetchall()
            return [self._capture_summary(connection, row) for row in rows]
        finally:
            connection.close()

    def get_capture(self, capture_id: str) -> Optional[Dict[str, Any]]:
        connection = self.connection()
        try:
            row = connection.execute(
                "SELECT * FROM profile_captures WHERE capture_id = ?", (capture_id,)
            ).fetchone()
            if row is None:
                return None
            capture = dict(row)
            capture["missing_fields"] = json.loads(capture["missing_fields"])
            capture["warnings"] = json.loads(capture["warnings"])
            profile_row = connection.execute(
                "SELECT payload, observed_at FROM profile_snapshots WHERE capture_id = ?",
                (capture_id,),
            ).fetchone()
            content_rows = connection.execute(
                """SELECT content_index, payload, observed_at
                FROM content_snapshots WHERE capture_id = ? ORDER BY content_index""",
                (capture_id,),
            ).fetchall()
            metric_row = connection.execute(
                """SELECT payload, calculated_at FROM metric_snapshots
                WHERE capture_id = ? ORDER BY metric_id DESC LIMIT 1""",
                (capture_id,),
            ).fetchone()
            feature_rows = connection.execute(
                """SELECT p.permalink, f.payload FROM posts p
                JOIN post_features f ON f.post_id = p.post_id
                JOIN creators cr ON cr.creator_id = p.creator_id
                WHERE cr.profile_url = ?""",
                (capture["profile_url"],),
            ).fetchall()
            feature_by_permalink = {row["permalink"]: json.loads(row["payload"]) for row in feature_rows}
            capture["profile"] = json.loads(profile_row["payload"]) if profile_row else {}
            capture["profile_observed_at"] = profile_row["observed_at"] if profile_row else None
            capture["content"] = [
                {
                    "content_index": row["content_index"],
                    "observed_at": row["observed_at"],
                    **json.loads(row["payload"]),
                }
                for row in content_rows
            ]
            for item in capture["content"]:
                item["features"] = feature_by_permalink.get(item.get("permalink"), {})
            capture["metrics"] = json.loads(metric_row["payload"]) if metric_row else {}
            capture["metrics_calculated_at"] = metric_row["calculated_at"] if metric_row else None
            return capture
        finally:
            connection.close()

    def _capture_summary(self, connection: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
        summary = dict(row)
        summary["profile"] = json.loads(summary.pop("profile_payload") or "{}")
        summary["missing_fields"] = json.loads(summary["missing_fields"])
        summary["warnings"] = json.loads(summary["warnings"])
        summary["content_count"] = connection.execute(
            "SELECT COUNT(*) FROM content_snapshots WHERE capture_id = ?", (summary["capture_id"],)
        ).fetchone()[0]
        return summary

    def overview(self) -> Dict[str, Any]:
        connection = self.connection()
        try:
            reel_counts = connection.execute(
                """SELECT COUNT(*) AS total,
                   SUM(CASE WHEN json_extract(f.payload, '$.reel_analysis.analysis_version') = 'reel-analysis-v1' THEN 1 ELSE 0 END) AS analyzed,
                   SUM(CASE WHEN json_extract(f.payload, '$.reel_analysis.analysis_confidence') = 'insufficient_text_evidence' THEN 1 ELSE 0 END) AS low_evidence
                   FROM posts p LEFT JOIN post_features f ON f.post_id = p.post_id
                   WHERE p.content_type = 'reel'"""
            ).fetchone()
            return {
                "profiles": connection.execute("SELECT COUNT(*) FROM profile_captures").fetchone()[0],
                "complete": connection.execute("SELECT COUNT(*) FROM profile_captures WHERE status = 'complete'").fetchone()[0],
                "partial": connection.execute("SELECT COUNT(*) FROM profile_captures WHERE status = 'partial'").fetchone()[0],
                "content": connection.execute("SELECT COUNT(*) FROM content_snapshots").fetchone()[0],
                "reels": reel_counts["total"] or 0,
                "reels_analyzed": reel_counts["analyzed"] or 0,
                "reels_low_evidence": reel_counts["low_evidence"] or 0,
            }
        finally:
            connection.close()

    def runtime_status(self) -> Dict[str, Any]:
        connection = self.connection()
        try:
            kinds: Dict[str, Dict[str, int]] = {}
            for row in connection.execute("SELECT kind, state, COUNT(*) AS n FROM tasks GROUP BY kind, state"):
                kinds.setdefault(row["kind"], {})[row["state"]] = row["n"]
            runners = [dict(row) for row in connection.execute("SELECT * FROM runners ORDER BY runner_id")]
            flag = connection.execute("SELECT value FROM runtime_flags WHERE name='auth_blocked'").fetchone()
            return {"kinds": kinds, "runners": runners, "auth_blocked": flag[0] if flag else None}
        except sqlite3.OperationalError:
            return {"kinds": {}, "runners": [], "auth_blocked": None}
        finally:
            connection.close()

    def cohort_status(self, target: int = 1000) -> Dict[str, Any]:
        connection = self.connection()
        try:
            rows = connection.execute(
                """SELECT p.payload FROM profile_snapshots p
                JOIN profile_captures c ON c.capture_id = p.capture_id
                WHERE c.status IN ('complete', 'partial')"""
            ).fetchall()
            handles = set()
            eligible = []
            for row in rows:
                try:
                    profile = ProfileObservation(**json.loads(row["payload"]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                handle = (profile.handle or profile.profile_url or "").lower().rstrip("/").split("/")[-1]
                if handle and is_human_indian_micro_creator(profile) and handle not in handles:
                    handles.add(handle)
                    eligible.append(profile)
            job_counts = {
                row["status"]: row["count"]
                for row in connection.execute("SELECT status, COUNT(*) AS count FROM collection_jobs GROUP BY status")
            }
            candidate_counts = {
                row["status"]: row["count"]
                for row in connection.execute("SELECT status, COUNT(*) AS count FROM profile_candidates GROUP BY status")
            }
            cursor_row = connection.execute(
                "SELECT source_name, cursor, exhausted FROM source_cursors ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            preflight_rejections = connection.execute(
                """SELECT COUNT(*) FROM profile_candidates
                   WHERE status='quarantined' AND last_error LIKE '%PUBLIC_PREFLIGHT_REJECTED%'"""
            ).fetchone()[0]
            return {
                "target": target,
                "eligible": len(handles),
                "remaining": max(0, target - len(handles)),
                "queued_or_leased": candidate_counts.get("queued", 0) + candidate_counts.get("leased", 0),
                "queued": candidate_counts.get("queued", 0),
                "leased": candidate_counts.get("leased", 0),
                "complete": job_counts.get("complete", 0),
                "failed": candidate_counts.get("failed", 0),
                "failed_jobs": job_counts.get("failed", 0),
                "discovered": candidate_counts.get("discovered", 0),
                "verified_public": candidate_counts.get("verified_public", 0),
                "captured": candidate_counts.get("captured", 0),
                "quarantined": candidate_counts.get("quarantined", 0),
                "failed_candidates": candidate_counts.get("failed", 0),
                "exhausted": candidate_counts.get("exhausted", 0),
                "source_cursor": cursor_row["cursor"] if cursor_row else None,
                "source_exhausted": bool(cursor_row and cursor_row["exhausted"]),
                "public_preflight_rejections": preflight_rejections,
                "captures": connection.execute("SELECT COUNT(*) FROM profile_captures").fetchone()[0],
                "creators": connection.execute("SELECT COUNT(*) FROM creators").fetchone()[0],
                "eligible_profiles": eligible,
            }
        finally:
            connection.close()

    def list_creators(self, query: str = "", category: str = "", language: str = "", min_followers: str = "", max_followers: str = "", min_engagement: str = "") -> List[Dict[str, Any]]:
        connection = self.connection()
        try:
            def integer(value: str) -> Optional[int]:
                try:
                    return int(value) if value else None
                except ValueError:
                    return None

            def number(value: str) -> Optional[float]:
                try:
                    return float(value) if value else None
                except ValueError:
                    return None

            from .store import CaptureStore
            # Reuse the parameterized query implementation without creating a
            # second connection or changing the dashboard's read-only behavior.
            original = CaptureStore.__new__(CaptureStore)
            original.connection = connection
            return original.discover_creators(
                query=query,
                category=category or None,
                language=language or None,
                min_followers=integer(min_followers),
                max_followers=integer(max_followers),
                min_engagement=number(min_engagement),
            )
        finally:
            connection.close()


CSS = """
:root { color-scheme: dark; --bg:#0b1020; --panel:#131b2e; --panel2:#1a2540; --text:#eef4ff; --muted:#9aa9c5; --accent:#7c9cff; --green:#3ddc97; --yellow:#ffd166; --red:#ff6b81; }
* { box-sizing:border-box; } body { margin:0; background:linear-gradient(135deg,#0b1020,#10182b); color:var(--text); font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
.shell { max-width:1400px; margin:0 auto; padding:28px; } header { display:flex; justify-content:space-between; align-items:end; gap:20px; margin-bottom:24px; } h1,h2,h3 { margin:0 0 8px; } h1 { font-size:30px; } h2 { font-size:22px; } h3 { font-size:16px; } p { color:var(--muted); }
.eyebrow { color:var(--accent); text-transform:uppercase; letter-spacing:.12em; font-size:11px; font-weight:700; } .subtle { color:var(--muted); font-size:13px; }
.grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:22px; } .card,.panel { background:rgba(19,27,46,.9); border:1px solid #263554; border-radius:16px; box-shadow:0 10px 30px #05091444; } .card { padding:18px; } .card .value { font-size:28px; font-weight:750; margin-top:5px; }
.toolbar { display:flex; gap:10px; margin-bottom:16px; } input { flex:1; background:var(--panel); color:var(--text); border:1px solid #334568; border-radius:10px; padding:12px 14px; font-size:14px; } button,.button { border:0; border-radius:10px; padding:11px 15px; background:var(--accent); color:#071025; font-weight:700; cursor:pointer; }
.panel { padding:20px; margin-bottom:18px; overflow:hidden; } .table-wrap { overflow:auto; } table { width:100%; border-collapse:collapse; min-width:760px; } th,td { text-align:left; padding:13px 12px; border-bottom:1px solid #263554; vertical-align:top; } th { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; } td { font-size:14px; } tr:hover td { background:#1b2742; }
.badge { display:inline-flex; border-radius:999px; padding:5px 9px; font-size:12px; font-weight:700; } .complete { color:#06291d; background:var(--green); } .partial { color:#382700; background:var(--yellow); } .failed,.needs_manual_auth { color:#35040e; background:var(--red); } .running { color:#071025; background:var(--accent); }
.profile-head { display:flex; justify-content:space-between; gap:20px; align-items:start; } .profile-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:18px; } .metric { background:var(--panel2); border-radius:12px; padding:14px; } .metric strong { display:block; font-size:21px; margin-top:5px; }
.two-col { display:grid; grid-template-columns:1fr 1fr; gap:18px; } .chips { display:flex; flex-wrap:wrap; gap:7px; } .chip { background:#253454; border-radius:999px; padding:6px 9px; color:#c9d5ef; font-size:12px; } .warning { color:#ffd166; }
.content-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:12px; } .content-card { background:var(--panel2); border:1px solid #2b3c60; border-radius:13px; padding:14px; } .content-card .index { color:var(--accent); font-weight:700; } .content-card dl { display:grid; grid-template-columns:1fr 1fr; gap:8px; } dt { color:var(--muted); font-size:12px; } dd { margin:2px 0 0; font-weight:650; }
.reel-analysis { margin-top:14px; padding-top:12px; border-top:1px solid #334568; } .reel-analysis h3 { color:var(--accent); } .reel-analysis p { font-size:12px; line-height:1.45; }
@media(max-width:900px) { .grid,.profile-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .two-col { grid-template-columns:1fr; } header { display:block; } } @media(max-width:560px) { .shell { padding:16px; } .grid,.profile-grid { grid-template-columns:1fr; } .toolbar { display:block; } button { margin-top:8px; width:100%; } }
"""


def layout(title: str, content: str) -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)} · microIndia Dataset</title><style>{CSS}</style></head><body><main class='shell'>{content}</main></body></html>"""


def render_overview(repository: DashboardRepository, query: str) -> str:
    overview = repository.overview()
    cohort = repository.cohort_status()
    runtime = repository.runtime_status()
    captures = repository.list_captures(query)
    rows = []
    for capture in captures:
        profile = capture["profile"]
        rows.append(
            f"<tr><td><a href='/capture/{esc(capture['capture_id'])}'><strong>@{esc(profile.get('handle') or capture['candidate_key'])}</strong></a><div class='subtle'>{esc(profile.get('display_name'))}</div></td>"
            f"<td>{esc(format_number(profile.get('follower_count')))}</td><td>{esc(capture['content_count'])} / {esc(capture['requested_content_count'])}</td>"
            f"<td><span class='badge {esc(capture['status'])}'>{esc(capture['status'])}</span></td><td>{esc(capture['captured_at'])}</td></tr>"
        )
    table = "".join(rows) or "<tr><td colspan='5'><p>No captures match your search.</p></td></tr>"
    cohort_rows = "".join(
        f"<tr><td><strong>@{esc(profile.handle)}</strong></td><td>{esc(format_number(profile.follower_count))}</td><td>{esc(profile.location_text or '—')}</td><td>{esc(profile.bio_text or '—')}</td></tr>"
        for profile in reversed(cohort["eligible_profiles"][-25:])
    ) or "<tr><td colspan='4'><p>No eligible profiles yet.</p></td></tr>"
    states = ("queued", "leased", "done", "skipped", "failed", "auth_blocked")
    task_rows = "".join(
        f"<tr><td><code>{esc(kind)}</code></td>" + "".join(f"<td>{format_number(counts.get(state, 0))}</td>" for state in states) + "</tr>"
        for kind, counts in sorted(runtime["kinds"].items())
    ) or f"<tr><td colspan='{len(states) + 1}'><p>No tasks yet. Start a runner: <code>python -m microindia_scraper.run work</code></p></td></tr>"
    runner_rows = "".join(
        f"<tr><td>{esc(runner['runner_id'])}</td><td><code>{esc(runner['kinds'])}</code></td><td>{esc(runner['tabs'])}</td>"
        f"<td><span class='badge {esc(runner['status'])}'>{esc(runner['status'])}</span></td><td>{format_number(runner['processed'])}</td>"
        f"<td>{esc(datetime.fromtimestamp(runner['last_heartbeat']).strftime('%H:%M:%S'))}</td></tr>"
        for runner in runtime["runners"]
    ) or "<tr><td colspan='6'><p>No runners have reported yet.</p></td></tr>"
    auth_note = (
        f"<p><strong>Paused: the signed-in session hit a login/challenge ({esc(runtime['auth_blocked'])}).</strong> Sign in again in Chrome; runners resume on their own.</p>"
        if runtime["auth_blocked"] else ""
    )
    runtime_panel = (
        "<section class='panel'><h2>Task runtime</h2>" + auth_note
        + "<div class='table-wrap'><table><thead><tr><th>Kind</th>" + "".join(f"<th>{esc(state)}</th>" for state in states)
        + f"</tr></thead><tbody>{task_rows}</tbody></table></div>"
        + "<div class='table-wrap'><table><thead><tr><th>Runner</th><th>Kinds</th><th>Tabs</th><th>Status</th><th>Processed</th><th>Heartbeat</th></tr></thead>"
        + f"<tbody>{runner_rows}</tbody></table></div></section>"
    )
    content = f"""
      <meta http-equiv='refresh' content='15'>
      <header><div><div class='eyebrow'>microIndia · local dataset review</div><h1>Influencer Capture Dashboard</h1><p>Live public Indian human micro-creator collection. Refreshes every 15 seconds.</p></div><a class='button' href='/'>Refresh data</a></header>
      {runtime_panel}
      <section class='grid'><div class='card'><div class='eyebrow'>eligible / target</div><div class='value'>{format_number(cohort['eligible'])} / {format_number(cohort['target'])}</div></div><div class='card'><div class='eyebrow'>remaining</div><div class='value'>{format_number(cohort['remaining'])}</div></div><div class='card'><div class='eyebrow'>queued / leased</div><div class='value'>{format_number(cohort['queued'])} / {format_number(cohort['leased'])}</div></div><div class='card'><div class='eyebrow'>completed jobs</div><div class='value'>{format_number(cohort['complete'])}</div></div></section>
      <section class='panel'><h2>Candidate frontier</h2><div class='grid'><div class='card'><div class='eyebrow'>discovered</div><div class='value'>{format_number(cohort['discovered'])}</div></div><div class='card'><div class='eyebrow'>verified public</div><div class='value'>{format_number(cohort['verified_public'])}</div></div><div class='card'><div class='eyebrow'>queued</div><div class='value'>{format_number(cohort['queued'])}</div></div><div class='card'><div class='eyebrow'>leased</div><div class='value'>{format_number(cohort['leased'])}</div></div><div class='card'><div class='eyebrow'>captured</div><div class='value'>{format_number(cohort['captured'])}</div></div><div class='card'><div class='eyebrow'>quarantined</div><div class='value'>{format_number(cohort['quarantined'])}</div></div></div><p>Source cursor: <code>{esc(cohort['source_cursor'])}</code> · exhausted: <strong>{esc(cohort['source_exhausted'])}</strong> · preflight rejections: <strong>{esc(cohort['public_preflight_rejections'])}</strong></p></section>
      <section class='panel'><h2>Eligible Indian human micro-creators</h2><p>Only unique profiles passing the public, 10K–100K follower, India-signal, and non-organization rules count toward the target.</p><div class='table-wrap'><table><thead><tr><th>Handle</th><th>Followers</th><th>Location</th><th>Bio</th></tr></thead><tbody>{cohort_rows}</tbody></table></div></section>
      <section class='grid'><div class='card'><div class='eyebrow'>captures</div><div class='value'>{overview['profiles']}</div></div><div class='card'><div class='eyebrow'>complete</div><div class='value'>{overview['complete']}</div></div><div class='card'><div class='eyebrow'>partial</div><div class='value'>{overview['partial']}</div></div><div class='card'><div class='eyebrow'>content observations</div><div class='value'>{overview['content']}</div></div></section>
      <section class='panel'><h2>Reel analysis coverage</h2><p>{overview['reels_analyzed']} of {overview['reels']} reels have the current analysis version. {overview['reels_low_evidence']} are marked low-evidence because historical captures lack caption or extracted text.</p></section>
      <section class='panel'><form class='toolbar' method='get' action='/'><input name='q' value='{esc(query)}' placeholder='Search handle, URL, candidate, or captured bio'><button type='submit'>Search</button></form><div class='table-wrap'><table><thead><tr><th>Profile</th><th>Followers</th><th>Content</th><th>Status</th><th>Captured</th></tr></thead><tbody>{table}</tbody></table></div></section>
    """
    return layout("Dashboard", content)


def render_creators(repository: DashboardRepository, params: Dict[str, str]) -> str:
    creators = repository.list_creators(**params)
    rows = []
    for creator in creators:
        features = creator["features"]
        reasons = []
        research = features.get("profile_research") or {}
        if features.get("primary_category"):
            reasons.append(features["primary_category"])
        if research.get("location_text"):
            reasons.append(f"location: {research['location_text']}")
        if features.get("metric_confidence"):
            reasons.append(f"{features['metric_confidence']} metrics")
        if features.get("post_sample_size") is not None:
            reasons.append(f"{features['post_sample_size']} posts researched")
        if features.get("posts_with_dates"):
            reasons.append(f"{features['posts_with_dates']} dated")
        if features.get("top_hashtags"):
            reasons.append("hashtags: " + ", ".join(item["tag"] for item in features["top_hashtags"][:3]))
        if features.get("data_quality"):
            reasons.append(f"{features['data_quality']} data")
        rows.append(
            f"<tr><td><strong>@{esc(creator.get('handle') or creator['canonical_key'])}</strong><div class='subtle'>{esc(creator['profile_url'])}</div></td>"
            f"<td>{esc(features.get('primary_category'))}</td><td>{esc(', '.join(features.get('languages') or []) or 'unknown')}</td>"
            f"<td>{esc(format_number(features.get('follower_count')))}</td><td>{esc(format_percent(features.get('engagement_rate')))}</td>"
            f"<td>{esc(' · '.join(reasons))}</td></tr>"
        )
    table = "".join(rows) or "<tr><td colspan='6'><p>No normalized creators match these filters. Run a capture first.</p></td></tr>"
    content = f"""
      <header><div><div class='eyebrow'>microIndia · creator intelligence</div><h1>Creator Explorer</h1><p>Normalized, explainable features derived from immutable public observations.</p></div><a class='button' href='/'>Raw captures</a></header>
      <section class='panel'><form class='toolbar' method='get' action='/creators'>
        <input name='query' value='{esc(params.get('query', ''))}' placeholder='Handle, profile URL, or feature text'>
        <input name='category' value='{esc(params.get('category', ''))}' placeholder='Category, e.g. food'>
        <input name='language' value='{esc(params.get('language', ''))}' placeholder='Language, e.g. en'>
        <input name='min_followers' value='{esc(params.get('min_followers', ''))}' placeholder='Min followers'>
        <input name='max_followers' value='{esc(params.get('max_followers', ''))}' placeholder='Max followers'>
        <input name='min_engagement' value='{esc(params.get('min_engagement', ''))}' placeholder='Min engagement (0.01)'>
        <button type='submit'>Filter</button></form>
        <div class='table-wrap'><table><thead><tr><th>Creator</th><th>Category</th><th>Language</th><th>Followers</th><th>Engagement</th><th>Why it matches</th></tr></thead><tbody>{table}</tbody></table></div>
      </section>
    """
    return layout("Creator Explorer", content)


def render_capture(capture: Dict[str, Any]) -> str:
    profile = capture["profile"]
    metrics = capture["metrics"]
    missing = "".join(f"<span class='chip'>{esc(item)}</span>" for item in capture["missing_fields"]) or "<span class='subtle'>None recorded</span>"
    warnings = "".join(f"<li class='warning'>{esc(item)}</li>" for item in capture["warnings"]) or "<li class='subtle'>None recorded</li>"
    content_cards = []
    for item in capture["content"]:
        permalink = item.get("permalink")
        link = f"<a href='{esc(permalink)}' target='_blank' rel='noreferrer'>Open on Instagram ↗</a>" if permalink else "<span class='subtle'>No permalink</span>"
        caption = item.get("caption_text") or "No caption text captured"
        hashtags = ", ".join(item.get("hashtags") or []) or "—"
        content_cards.append(
            f"""<article class='content-card'><div class='index'>#{esc(item.get('content_index'))} · {esc(item.get('content_type'))}</div><p>{link}</p><p><strong>Caption:</strong> {esc(caption)}</p><p class='subtle'><strong>Hashtags:</strong> {esc(hashtags)}</p><dl><div><dt>Published</dt><dd>{esc(item.get('published_at'))}</dd></div><div><dt>Location</dt><dd>{esc(item.get('location_text'))}</dd></div><div><dt>Status</dt><dd>{esc(item.get('item_status'))}</dd></div><div><dt>Pinned</dt><dd>{esc(item.get('is_pinned'))}</dd></div><div><dt>Collaboration</dt><dd>{esc(item.get('is_collaboration'))}</dd></div><div><dt>Likes</dt><dd>{esc(format_number(item.get('like_count')))}</dd></div><div><dt>Comments</dt><dd>{esc(format_number(item.get('comment_count')))}</dd></div><div><dt>Views</dt><dd>{esc(format_number(item.get('view_count')))}</dd></div><div><dt>Metrics</dt><dd>{esc(item.get('metric_availability'))}</dd></div></dl>{render_reel_analysis(item.get('features', {}).get('reel_analysis'))}</article>"""
        )
    metrics_html = "".join(
        f"<div class='metric'><div class='subtle'>{esc(label)}</div><strong>{esc(value)}</strong></div>"
        for label, value in [
            ("Eligible items", metrics.get("eligible_item_count")),
            ("Metric confidence", metrics.get("metric_confidence")),
            ("Average likes", format_number(metrics.get("average_likes"))),
            ("Median likes", format_number(metrics.get("median_likes"))),
            ("Average comments", format_number(metrics.get("average_comments"))),
            ("Median reel views", format_number(metrics.get("median_reel_views"))),
            ("Engagement rate", format_percent(metrics.get("engagement_rate_by_followers"))),
        ]
    )
    profile_fields = [
        ("Handle", f"@{profile.get('handle')}" if profile.get("handle") else None),
        ("Display name", profile.get("display_name")),
        ("Followers", format_number(profile.get("follower_count"))),
        ("Following", format_number(profile.get("following_count"))),
        ("Posts", format_number(profile.get("post_count"))),
        ("Category", profile.get("business_category")),
        ("Location", profile.get("location_text")),
        ("Languages", ", ".join(profile.get("language_signals") or []) or None),
        ("Account type", profile.get("account_type")),
        ("Commercial signals", ", ".join(key for key, value in (profile.get("commercial_signals") or {}).items() if value) or None),
        ("Verified", "Yes" if profile.get("is_verified") else "No"),
        ("Private", "Yes" if profile.get("is_private") else "No"),
        ("External URL", profile.get("external_url")),
    ]
    fields_html = "".join(f"<div class='metric'><div class='subtle'>{esc(label)}</div><strong>{esc(value)}</strong></div>" for label, value in profile_fields)
    content = f"""
      <header><div><div class='eyebrow'>profile capture</div><h1>@{esc(profile.get('handle') or capture['candidate_key'])}</h1><p>{esc(profile.get('bio_text'))}</p></div><div><span class='badge {esc(capture['status'])}'>{esc(capture['status'])}</span><br><br><a href='{esc(capture['profile_url'])}' target='_blank' rel='noreferrer'>View profile ↗</a></div></header>
      <p><a href='/'>← Back to all captures</a> · Capture ID <code>{esc(capture['capture_id'])}</code> · Captured {esc(capture['captured_at'])}</p>
      <section class='panel'><h2>Profile overview</h2><div class='profile-grid'>{fields_html}</div></section>
      <section class='panel'><h2>Derived metrics</h2><div class='profile-grid'>{metrics_html}</div></section>
      <section class='two-col'><div class='panel'><h2>Capture quality</h2><p>Saved {esc(capture['observed_content_count'])} of {esc(capture['requested_content_count'])} requested content items.</p><p>Completeness score: <strong>{esc(capture['completeness_score'])}</strong></p><h3>Missing fields</h3><div class='chips'>{missing}</div></div><div class='panel'><h2>Warnings</h2><ul>{warnings}</ul></div></section>
      <section class='panel'><h2>Captured content <span class='subtle'>({len(capture['content'])} items)</span></h2><div class='content-grid'>{''.join(content_cards) or '<p>No content observations saved.</p>'}</div></section>
    """
    return layout(f"@{profile.get('handle') or capture['candidate_key']}", content)


def render_reel_analysis(analysis: Optional[Dict[str, Any]]) -> str:
    if not analysis:
        return ""
    return (
        "<div class='reel-analysis'><h3>Reel analysis</h3>"
        f"<p><strong>Topic:</strong> {esc(analysis.get('topic'))} · <strong>Format:</strong> {esc(', '.join(analysis.get('format_signals') or []))}</p>"
        f"<p><strong>Languages:</strong> {esc(', '.join(analysis.get('language_signals') or []) or 'unknown')} · <strong>CTA:</strong> {esc('yes' if analysis.get('call_to_action') else 'no')} · <strong>Commercial:</strong> {esc('yes' if analysis.get('commercial') else 'no')}</p>"
        f"<p><strong>Hook:</strong> {esc(analysis.get('hook_text') or 'No text hook captured')}</p>"
        f"<p class='subtle'>Evidence: {esc(analysis.get('text_evidence_chars'))} chars · confidence {esc(analysis.get('analysis_confidence'))}</p></div>"
    )


class DashboardHandler(BaseHTTPRequestHandler):
    repository: DashboardRepository

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            query = parse_qs(parsed.query).get("q", [""])[0]
            self.respond(render_overview(self.repository, query))
            return
        if parsed.path == "/creators":
            query = parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if key in {"query", "category", "language", "min_followers", "max_followers", "min_engagement"}}
            self.respond(render_creators(self.repository, params))
            return
        if parsed.path.startswith("/capture/"):
            capture_id = parsed.path.split("/", 2)[2]
            capture = self.repository.get_capture(capture_id)
            if capture is None:
                self.respond(layout("Not found", "<h1>Capture not found</h1><p><a href='/'>Back to dashboard</a></p>"), 404)
            else:
                self.respond(render_capture(capture))
            return
        self.respond("Not found", 404, content_type="text/plain; charset=utf-8")

    def respond(self, body: str, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local microIndia dataset dashboard")
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    repository = DashboardRepository(args.database)
    handler = type("ConfiguredDashboardHandler", (DashboardHandler,), {"repository": repository})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"microIndia dataset dashboard: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()