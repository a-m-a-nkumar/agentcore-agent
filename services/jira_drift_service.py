"""
Jira Sync — "Drift to resolve" engine (the REVERSE direction).

Where jira_sync_service propagates BRD changes forward into Jira/test artifacts,
this module detects the opposite: a Jira story that was edited by hand and now
diverges from the BRD requirement it was generated from.

Detection (run inside the same /scan, after the forward pass):
  1. List every current jira_story lineage row for the workspace.
  2. Fetch live issues (one call per Jira project) and normalize each into the
     same snapshot shape we stored at generation time.
  3. Hash a canonical subset of both sides and compare. The "baseline" is the
     last content we know is in sync — an accepted drift's frozen snapshot, or
     the lineage snapshot (which forward-apply / revert keep fresh) — so our own
     pushed changes never re-appear as drift.
  4. Upsert a jira_drift_items row per drifted (story × requirement) edge.

Resolution (resolve_drift) lives further down: revert the story, stage a BRD
amendment, or accept the drift and stop flagging it.
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional

from psycopg2.extras import RealDictCursor

from db_helper import get_db_connection, release_db_connection
from environment import chat_completion
from services.confluence_service import ConfluenceService
from services.jira_service import JiraService
from services.lineage_service import (
    get_lineage_targets_for_workspace,
    update_target_snapshot,
)
from prompts.jira_sync_drift_amendment_prompts import build_brd_amendment_prompt
from prompts.jira_sync_comment_drift_prompts import (
    build_comment_drift_judge_prompt,
    build_story_update_prompt,
)
from utils.content_hashing import hash_text, extract_section_text

logger = logging.getLogger(__name__)

STORY_POINTS_FIELD = os.getenv("JIRA_STORY_POINTS_FIELD", "customfield_10016")
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
)

# Concurrency cap for the per-story drift comparisons (independent I/O: a Jira
# comment fetch + an LLM judge each). Bounded so we don't hammer the LLM gateway
# or exhaust the DB connection pool.
JIRA_SYNC_MAX_WORKERS = int(os.getenv("JIRA_SYNC_MAX_WORKERS", "5"))

# Cheap model for the cheap-then-expensive cascade: the GATE steps (comment
# judge, reconcile relevance gate) run on this; the GENERATION steps (diff,
# reconcile draft) stay on BEDROCK_MODEL_ID. Set JIRA_SYNC_GATE_MODEL to the
# gateway's Haiku name to make gating genuinely cheap; otherwise it falls back
# to the main model (still cheaper, because the gate prompt is tiny).
GATE_MODEL = os.getenv("JIRA_SYNC_GATE_MODEL", "").strip() or BEDROCK_MODEL_ID

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(raw: str) -> dict:
    if not raw:
        raise ValueError("LLM returned empty response")
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {raw[:200]}")
    return json.loads(match.group(0))


# ── Normalization (the heart of the comparison) ──────────────────────────────


def _normalize_ws(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _adf_to_text(adf) -> str:
    """Flatten a Jira ADF description (dict) or a plain string to comparable text."""
    if adf is None:
        return ""
    if isinstance(adf, str):
        return _normalize_ws(adf)
    texts: List[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                texts.append(node["text"])
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for n in node:
                walk(n)

    walk(adf)
    return _normalize_ws(" ".join(texts))


def _to_text(v) -> str:
    if isinstance(v, (dict, list)):
        return _adf_to_text(v)
    return v or ""


def _norm_points(v) -> str:
    if v is None or v == "":
        return ""
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(v).strip()


def _norm_field(key: str, value) -> str:
    if key == "story_points":
        return _norm_points(value)
    return _normalize_ws(str(value or ""))


def _compose_description(snapshot: dict) -> str:
    """
    The description as it appears in Jira. The generation snapshot keeps the
    body and acceptance_criteria separate, but at issue-creation the AC is
    appended into the description (jira_generation.convert_to_adf), so we fold
    it back the same way ("\\n\\nAcceptance Criteria:\\n- ...") before comparing.
    A live snapshot (or a post-apply baseline) already has the AC inside the
    description and no separate list, so nothing is double-appended.
    """
    desc = _to_text(snapshot.get("description"))
    ac = snapshot.get("acceptance_criteria")
    if isinstance(ac, list) and ac:
        desc = desc + "\n\nAcceptance Criteria:\n" + "".join(f"- {c}\n" for c in ac)
    return desc


def _canon_snapshot(snapshot: Optional[dict]) -> dict:
    """Normalize a snapshot to the comparable canonical subset — same on both sides."""
    snapshot = snapshot or {}
    return {
        "title": _norm_field("title", snapshot.get("title")),
        "description": _normalize_ws(_compose_description(snapshot)),
        "story_points": _norm_field("story_points", snapshot.get("story_points")),
        "priority": _norm_field("priority", snapshot.get("priority")),
    }


def canonical_hash(snapshot: Optional[dict]) -> str:
    """SHA-256 over the normalized canonical subset — stored as a row identifier."""
    return hash_text(json.dumps(_canon_snapshot(snapshot), sort_keys=True))


# Fields compared unconditionally. story_points is handled separately because its
# Jira custom-field id (customfield_10016) is instance-specific — an empty live
# value means "Jira didn't return the field", not "the user cleared the points".
ALWAYS_KEYS = ("title", "description", "priority")


def _changed_fields(baseline: Optional[dict], live: Optional[dict]) -> List[str]:
    """Canonical fields that genuinely differ between baseline and live."""
    cb, cl = _canon_snapshot(baseline), _canon_snapshot(live)
    changed = [k for k in ALWAYS_KEYS if cb[k] != cl[k]]
    # Only treat story_points as drift when BOTH sides report a value.
    if cb["story_points"] and cl["story_points"] and cb["story_points"] != cl["story_points"]:
        changed.append("story_points")
    return changed


def snapshots_in_sync(baseline: Optional[dict], live: Optional[dict]) -> bool:
    return not _changed_fields(baseline, live)


def build_jira_snapshot(issue: dict) -> dict:
    """Map a live Jira issue into the stored snapshot shape so it's comparable."""
    f = (issue or {}).get("fields", {}) or {}
    priority = f.get("priority")
    priority_name = priority.get("name") if isinstance(priority, dict) else priority
    return {
        "title": f.get("summary") or "",
        "description": _adf_to_text(f.get("description")),
        "story_points": f.get(STORY_POINTS_FIELD),
        "priority": priority_name,
    }


def snapshot_from_proposed(proposed: Optional[dict]) -> dict:
    """
    Build a canonical snapshot from a reconcile/revert `proposed` block, in the
    same form the live issue will take after we push it (AC appended into the
    description, matching jira_sync_service._jira_fields_from_proposed). Used to
    refresh the lineage baseline after an apply/revert.
    """
    proposed = proposed or {}
    desc = proposed.get("description") or ""
    ac = proposed.get("acceptance_criteria")
    if ac:
        desc = (desc + "\nAcceptance criteria:\n" + "\n".join(f"- {a}" for a in ac)).strip()
    return {
        "title": proposed.get("title"),
        "description": desc,
        "story_points": proposed.get("story_points"),
        "priority": proposed.get("priority"),
    }


def refresh_baseline(lineage_id: str, snapshot: dict) -> None:
    """Persist `snapshot` as the lineage's known-in-sync baseline (+ its hash)."""
    update_target_snapshot(lineage_id, snapshot, canonical_hash(snapshot))


def _as_dict(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


# ── Datetime / comment helpers (comment-driven drift) ────────────────────────


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_jira_dt(s) -> Optional[datetime]:
    """Parse Jira timestamps like '2026-06-09T10:20:13.526+0000' to aware UTC."""
    if not s:
        return None
    if isinstance(s, datetime):
        return _aware(s)
    try:
        # fromisoformat needs +HH:MM, Jira gives +HHMM
        s2 = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", str(s))
        return _aware(datetime.fromisoformat(s2))
    except Exception:
        return None


def _comments_text(comments: List[dict], limit: int = 12) -> str:
    """Flatten comments to '[author] text' lines for the judge/update prompts."""
    lines = []
    for c in comments[:limit]:
        author = c.get("author") or "unknown"
        body = _adf_to_text(c.get("body"))
        if body:
            lines.append(f"[{author}] {body}")
    return "\n".join(lines)


# ── Detection ────────────────────────────────────────────────────────────────


def run_drift_detection(
    run_id: str,
    workspace_key: str,
    jira: Optional[JiraService],
    confluence: Optional[ConfluenceService],
    user_id: str,
) -> int:
    """
    Reverse pass: flag jira_story targets whose live content diverges from the
    baseline. Returns the count of drift items upserted. Safe to call with
    jira=None (skips). Never raises — the forward scan must not fail on drift.
    """
    if jira is None:
        logger.info("[JiraDrift] no Jira credentials — skipping drift pass")
        return 0
    try:
        targets = get_lineage_targets_for_workspace(
            workspace_key, target_type="jira_story", status="current"
        )
        if not targets:
            return 0

        existing = _load_drift_rows(workspace_key)
        page_cache: Dict[str, str] = {}  # source page_id -> html, fetched once per scan

        # Group by Jira project key ("PROJ-42" -> "PROJ"); one fetch per project.
        by_project: Dict[str, List[dict]] = {}
        for row in targets:
            key = row.get("target_id") or ""
            proj = key.split("-")[0] if "-" in key else None
            if proj:
                by_project.setdefault(proj, []).append(row)

        issues_by_key: Dict[str, dict] = {}
        for proj in by_project:
            try:
                for issue in jira.get_project_issues(proj):
                    if issue.get("key"):
                        issues_by_key[issue["key"]] = issue
            except Exception as e:
                logger.warning(f"[JiraDrift] fetch issues failed for project {proj}: {e}")

        # Pre-fetch each unique source page once (sequentially) so the parallel
        # workers below only READ the cache — no thread races to populate it.
        if confluence:
            for pid in {row.get("source_id") for row in targets if row.get("source_id")}:
                if pid not in page_cache:
                    try:
                        page_cache[pid] = (confluence.get_page_content(pid) or {}).get("content", "")
                    except Exception:
                        page_cache[pid] = ""

        def _process_target(row) -> int:
            """Field + comment drift for one story. Returns drifts upserted (0-2).
            Self-contained so it can run in a worker thread; never raises."""
            count = 0
            try:
                target_id = row.get("target_id")
                req_id = row.get("source_section_id")
                issue = issues_by_key.get(target_id)
                if issue is None:
                    return 0  # deleted / inaccessible
                live_snapshot = build_jira_snapshot(issue)

                # (1) field-edit drift: live snapshot vs baseline
                field_existing = existing.get((target_id, req_id, "field"))
                baseline = _resolve_baseline(row, field_existing)
                if not snapshots_in_sync(baseline, live_snapshot):
                    upsert_drift_item(
                        workspace_key=workspace_key,
                        lineage_id=str(row.get("id")),
                        target_type="jira_story",
                        target_id=target_id,
                        source_page_id=row.get("source_id"),
                        requirement_id=req_id,
                        source_text=_fetch_source_text(confluence, row, req_id, page_cache),
                        current_text=live_snapshot.get("description") or live_snapshot.get("title") or "",
                        title=f"{target_id} drifted from {req_id}",
                        summary=_summarize_drift(baseline, live_snapshot),
                        baseline_content=baseline,
                        current_snapshot=live_snapshot,
                        current_hash=canonical_hash(live_snapshot),
                        edited_by=jira.get_issue_last_editor(target_id),
                        edited_at=(issue.get("fields") or {}).get("updated"),
                        last_scan_run_id=run_id,
                        drift_kind="field",
                    )
                    count += 1

                # (2) comment-driven drift: LLM judges whether new comments moved
                #     the story away from its title/description/requirement.
                if _detect_comment_drift(
                    run_id, workspace_key, row, target_id, req_id, issue,
                    live_snapshot, existing.get((target_id, req_id, "comment")),
                    jira, confluence, user_id, page_cache,
                ):
                    count += 1
            except Exception as e:
                logger.warning(f"[JiraDrift] drift check failed for {row.get('target_id')}: {e}")
            return count

        # Run the per-story comparisons CONCURRENTLY — each is independent I/O
        # (a Jira comment fetch + an LLM judge), so this collapses sum-of-times
        # into roughly the slowest single story.
        drift_count = 0
        with ThreadPoolExecutor(max_workers=JIRA_SYNC_MAX_WORKERS) as ex:
            for c in ex.map(_process_target, targets):
                drift_count += c

        logger.info(f"[JiraDrift] detected {drift_count} drift item(s) for ws={workspace_key}")
        return drift_count
    except Exception as e:
        logger.exception(f"[JiraDrift] drift detection failed for ws={workspace_key}: {e}")
        return 0


def _run_comment_judge(req_id, title, description, requirement_text, comments_text, user_id) -> dict:
    prompt = build_comment_drift_judge_prompt(
        requirement_id=req_id, title=title, description=description,
        requirement_text=requirement_text, comments_text=comments_text,
    )
    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=GATE_MODEL, temperature=0, max_tokens=600,  # GATE → cheap model
            user_id=user_id, token_source="jira_sync_comment_judge",
        )
        return _extract_json(raw)
    except Exception as e:
        logger.error(f"[JiraDrift] comment judge failed for {req_id}: {e}")
        return {"drifted": False, "implied_change": "", "comment_excerpt": ""}


def _detect_comment_drift(
    run_id, workspace_key, row, target_id, req_id, issue,
    live_snapshot, comment_existing, jira, confluence, user_id, page_cache=None,
) -> bool:
    """Judge whether comments newer than our cutoff imply the story has drifted."""
    comments = jira.get_issue_comments(target_id)
    if not comments:
        return False
    # Cutoff: newest comment we've already reconciled, else generation time.
    if comment_existing and comment_existing.get("last_comment_at"):
        cutoff = _aware(comment_existing["last_comment_at"])
    else:
        cutoff = _parse_jira_dt(row.get("created_at"))
    new_comments = [
        c for c in comments
        if (_dt := _parse_jira_dt(c.get("created"))) and (cutoff is None or _dt > cutoff)
    ]
    if not new_comments:
        return False  # no fresh discussion — nothing to judge (keeps accepts sticky)
    comments_text = _comments_text(new_comments)
    if not comments_text.strip():
        return False

    source_text = _fetch_source_text(confluence, row, req_id, page_cache)
    judge = _run_comment_judge(
        req_id, live_snapshot.get("title") or "", live_snapshot.get("description") or "",
        source_text, comments_text, user_id,
    )
    if not judge.get("drifted"):
        return False

    newest = max(
        (_parse_jira_dt(c.get("created")) for c in new_comments if _parse_jira_dt(c.get("created"))),
        default=None,
    )
    upsert_drift_item(
        workspace_key=workspace_key,
        lineage_id=str(row.get("id")),
        target_type="jira_story",
        target_id=target_id,
        source_page_id=row.get("source_id"),
        requirement_id=req_id,
        source_text=source_text,
        current_text=judge.get("implied_change") or "",
        title=f"{target_id}: a comment moved it off {req_id}",
        summary="Comment implies a scope change",
        baseline_content=live_snapshot,
        current_snapshot=live_snapshot,
        current_hash=canonical_hash(live_snapshot),
        edited_by=new_comments[0].get("author"),
        edited_at=new_comments[0].get("created"),
        last_scan_run_id=run_id,
        drift_kind="comment",
        comment_excerpt=judge.get("comment_excerpt") or _comments_text(new_comments[:1]),
        last_comment_at=newest.isoformat() if newest else None,
    )
    return True


def _resolve_baseline(lineage_row: dict, existing_drift_row: Optional[dict]) -> dict:
    """
    The content we last knew was in sync:
      1. an accepted drift's frozen baseline_content (so "accept" sticks), else
      2. the lineage snapshot (forward-apply / revert keep it fresh).
    """
    if existing_drift_row and existing_drift_row.get("status") == "accepted":
        bc = _as_dict(existing_drift_row.get("baseline_content"))
        if bc:
            return bc
    return _as_dict(lineage_row.get("original_generated_content"))


def _fetch_source_text(
    confluence: Optional[ConfluenceService],
    lineage_row: dict,
    req_id: str,
    page_cache: Optional[dict] = None,
) -> str:
    """The current BRD requirement text (source side of the drift display).

    `page_cache` (page_id -> html) dedupes Confluence fetches within one scan —
    many stories share the same BRD page, so we fetch each page at most once.
    """
    page_id = lineage_row.get("source_id")
    if confluence and page_id:
        try:
            if page_cache is not None and page_id in page_cache:
                html = page_cache[page_id]
            else:
                html = (confluence.get_page_content(page_id) or {}).get("content", "")
                if page_cache is not None:
                    page_cache[page_id] = html
            text = extract_section_text(html, req_id or "")
            if text:
                return text
        except Exception as e:
            logger.warning(f"[JiraDrift] source text fetch failed for {page_id}/{req_id}: {e}")
    # Fallback to the requirement text captured at generation time.
    return _as_dict(lineage_row.get("original_generated_content")).get("mapped_to_requirement") or ""


def _summarize_drift(baseline: dict, live: dict) -> str:
    labels = {
        "title": "summary",
        "description": "description",
        "story_points": "story points",
        "priority": "priority",
    }
    changed = [labels[k] for k in _changed_fields(baseline, live)]
    if not changed:
        return "Edited in Jira"
    return "Edited in Jira — " + ", ".join(changed) + " changed"


# ── Persistence ──────────────────────────────────────────────────────────────


def _load_drift_rows(workspace_key: str) -> Dict[tuple, dict]:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM jira_drift_items WHERE workspace_key = %s",
                (workspace_key,),
            )
            return {
                (r["target_id"], r["requirement_id"], r.get("drift_kind") or "field"): dict(r)
                for r in cursor.fetchall()
            }
    except Exception as e:
        logger.error(f"[JiraDrift] load drift rows failed: {e}")
        return {}
    finally:
        if conn:
            release_db_connection(conn)


def upsert_drift_item(
    workspace_key: str,
    lineage_id: str,
    target_type: str,
    target_id: str,
    source_page_id: Optional[str],
    requirement_id: str,
    source_text: str,
    current_text: str,
    title: str,
    summary: str,
    baseline_content: dict,
    current_snapshot: dict,
    current_hash: str,
    edited_by: Optional[str],
    edited_at: Optional[str],
    last_scan_run_id: str,
    drift_kind: str = "field",
    comment_excerpt: Optional[str] = None,
    last_comment_at: Optional[str] = None,
) -> None:
    """
    Insert or refresh the drift row for this (workspace, story, requirement,
    kind) edge — a story can carry a 'field' drift and a 'comment' drift at
    once. On conflict the row is (re)opened: a resolved/accepted pair that has
    drifted again flips back to 'open' with resolution fields cleared.
    detected_at is preserved while already open, reset on (re)open.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO jira_drift_items (
                    workspace_key, lineage_id, source, target_type, target_id,
                    source_page_id, requirement_id, source_text, current_text,
                    title, summary, baseline_content, current_snapshot, current_hash,
                    edited_by, edited_at, status, last_scan_run_id, detected_at,
                    drift_kind, comment_excerpt, last_comment_at
                ) VALUES (
                    %s, %s, 'JIRA', %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s::jsonb, %s,
                    %s, %s, 'open', %s, CURRENT_TIMESTAMP,
                    %s, %s, %s
                )
                ON CONFLICT (workspace_key, target_type, target_id, requirement_id, drift_kind)
                DO UPDATE SET
                    lineage_id = EXCLUDED.lineage_id,
                    source_page_id = EXCLUDED.source_page_id,
                    source_text = EXCLUDED.source_text,
                    current_text = EXCLUDED.current_text,
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    baseline_content = EXCLUDED.baseline_content,
                    current_snapshot = EXCLUDED.current_snapshot,
                    current_hash = EXCLUDED.current_hash,
                    edited_by = EXCLUDED.edited_by,
                    edited_at = EXCLUDED.edited_at,
                    comment_excerpt = EXCLUDED.comment_excerpt,
                    last_comment_at = EXCLUDED.last_comment_at,
                    status = 'open',
                    resolution = NULL,
                    resolution_note = NULL,
                    proposed_brd_amendment = NULL,
                    proposed_story_update = NULL,
                    resolved_by_user_id = NULL,
                    resolved_at = NULL,
                    last_scan_run_id = EXCLUDED.last_scan_run_id,
                    detected_at = CASE
                        WHEN jira_drift_items.status = 'open' THEN jira_drift_items.detected_at
                        ELSE CURRENT_TIMESTAMP
                    END
                """,
                (
                    workspace_key, lineage_id, target_type, target_id,
                    source_page_id, requirement_id, source_text, current_text,
                    title, summary, json.dumps(baseline_content or {}),
                    json.dumps(current_snapshot or {}), current_hash,
                    edited_by, edited_at, last_scan_run_id,
                    drift_kind, comment_excerpt, last_comment_at,
                ),
            )
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"[JiraDrift] upsert drift item failed for {target_id}/{requirement_id}/{drift_kind}: {e}")
    finally:
        if conn:
            release_db_connection(conn)


def get_drift_items(workspace_key: str) -> List[dict]:
    """Open drift items for the workspace — returned inside the /pulse payload."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, source, drift_kind, target_type, target_id, requirement_id,
                       title, summary, source_text, current_text, comment_excerpt,
                       edited_by, edited_at, status, detected_at
                  FROM jira_drift_items
                 WHERE workspace_key = %s AND status = 'open'
                 ORDER BY detected_at DESC
                """,
                (workspace_key,),
            )
            return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"[JiraDrift] get_drift_items failed: {e}")
        return []
    finally:
        if conn:
            release_db_connection(conn)


# ── Detail + resolution ──────────────────────────────────────────────────────


def _get_drift_row(drift_id: str) -> Optional[dict]:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM jira_drift_items WHERE id = %s", (drift_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[JiraDrift] _get_drift_row failed: {e}")
        return None
    finally:
        if conn:
            release_db_connection(conn)


def _get_lineage_row(lineage_id: str) -> Optional[dict]:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM artifact_lineage WHERE id = %s", (lineage_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[JiraDrift] _get_lineage_row failed: {e}")
        return None
    finally:
        if conn:
            release_db_connection(conn)


def get_drift_detail(drift_id: str) -> Optional[dict]:
    """Full drift row for the resolution dialog (incl. any staged amendment)."""
    row = _get_drift_row(drift_id)
    if not row:
        return None
    row["baseline_content"] = _as_dict(row.get("baseline_content"))
    row["current_snapshot"] = _as_dict(row.get("current_snapshot"))
    row["proposed_brd_amendment"] = _as_dict(row.get("proposed_brd_amendment")) or None
    row["proposed_story_update"] = _as_dict(row.get("proposed_story_update")) or None
    return row


def _update_drift_resolution(
    drift_id: str,
    status: str,
    resolution: str,
    note: Optional[str],
    user_id: str,
    baseline_content: Optional[dict] = None,
    proposed_amendment: Optional[dict] = None,
    proposed_story_update: Optional[dict] = None,
) -> None:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jira_drift_items
                   SET status = %s,
                       resolution = %s,
                       resolution_note = %s,
                       resolved_by_user_id = %s,
                       resolved_at = CURRENT_TIMESTAMP,
                       baseline_content = COALESCE(%s::jsonb, baseline_content),
                       proposed_brd_amendment = COALESCE(%s::jsonb, proposed_brd_amendment),
                       proposed_story_update = COALESCE(%s::jsonb, proposed_story_update)
                 WHERE id = %s
                """,
                (
                    status, resolution, note, user_id,
                    json.dumps(baseline_content) if baseline_content is not None else None,
                    json.dumps(proposed_amendment) if proposed_amendment is not None else None,
                    json.dumps(proposed_story_update) if proposed_story_update is not None else None,
                    drift_id,
                ),
            )
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"[JiraDrift] update resolution failed for {drift_id}: {e}")
        raise
    finally:
        if conn:
            release_db_connection(conn)


def _run_amendment_agent(
    requirement_id: str,
    current_requirement_text: str,
    drifted_artifact: dict,
    user_id: str,
) -> dict:
    prompt = build_brd_amendment_prompt(
        requirement_id=requirement_id,
        current_requirement_text=current_requirement_text,
        drifted_artifact=drifted_artifact,
    )
    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=BEDROCK_MODEL_ID,
            temperature=0,
            max_tokens=2000,
            user_id=user_id,
            token_source="jira_sync_drift_amendment",
        )
        return _extract_json(raw)
    except Exception as e:
        logger.error(f"[JiraDrift] amendment agent failed for {requirement_id}: {e}")
        return {
            "requirement_id": requirement_id,
            "amended_text": "",
            "change_summary": "Amendment generation failed — please draft manually.",
            "rationale": str(e)[:300],
        }


def resolve_drift(
    drift_id: str,
    resolution: str,
    note: Optional[str],
    user_id: str,
    jira: Optional[JiraService] = None,
) -> dict:
    """
    Apply one of the three resolutions to a drift item:
      - intentional : accept the Jira version; freeze it as the baseline so the
                      pair stops being flagged.
      - story-wrong : revert the Jira issue to the BRD source (lineage's
                      original generated snapshot); refresh the baseline.
      - brd-outdated: generate a proposed BRD amendment matching the story and
                      stage it for review (no Confluence write in v1).
    """
    row = _get_drift_row(drift_id)
    if not row:
        return {"ok": False, "error": "Drift item not found"}
    target_id = row.get("target_id")
    req_id = row.get("requirement_id")

    if resolution == "intentional":
        _update_drift_resolution(
            drift_id, status="accepted", resolution="intentional",
            note=note, user_id=user_id,
            baseline_content=_as_dict(row.get("current_snapshot")),
        )
        return {"ok": True, "status": "accepted",
                "message": f"Drift accepted — {target_id} no longer flagged"}

    if resolution == "story-wrong":
        if jira is None:
            return {"ok": False, "error": "Jira account not linked — cannot revert."}
        lineage = _get_lineage_row(row.get("lineage_id")) if row.get("lineage_id") else None
        if not lineage:
            return {"ok": False, "error": "Lineage not found — cannot rebuild the BRD source version."}
        original = _as_dict(lineage.get("original_generated_content"))
        # Lazy import to avoid a module-load cycle (jira_sync_service imports this module).
        from services.jira_sync_service import _jira_fields_from_proposed
        fields = _jira_fields_from_proposed(original)
        if not fields:
            return {"ok": False, "error": "Nothing to revert — original snapshot is empty."}
        jira.update_issue(target_id, fields)
        new_baseline = snapshot_from_proposed(original)
        refresh_baseline(str(lineage["id"]), new_baseline)
        _update_drift_resolution(
            drift_id, status="resolved", resolution="story-wrong",
            note=note, user_id=user_id, baseline_content=new_baseline,
        )
        return {"ok": True, "status": "resolved",
                "message": f"{target_id} reverted to match {req_id}"}

    if resolution == "brd-outdated":
        # Feed the amendment the story snapshot, plus (for comment drift) what the
        # comments imply so the proposed requirement reflects the real decision.
        drifted_artifact = dict(_as_dict(row.get("current_snapshot")))
        if (row.get("drift_kind") or "field") == "comment":
            drifted_artifact["implied_change_from_comments"] = row.get("current_text") or ""
            drifted_artifact["comment"] = row.get("comment_excerpt") or ""
        amendment = _run_amendment_agent(
            requirement_id=req_id,
            current_requirement_text=row.get("source_text") or "",
            drifted_artifact=drifted_artifact,
            user_id=user_id,
        )
        _update_drift_resolution(
            drift_id, status="resolved", resolution="brd-outdated",
            note=note, user_id=user_id, proposed_amendment=amendment,
        )
        return {"ok": True, "status": "resolved",
                "proposed_brd_amendment": amendment,
                "message": f"BRD amendment proposed for {req_id} — review next"}

    if resolution == "update-story":
        # Draft an updated title/description that folds the implied change in,
        # then push it to Jira and refresh the baseline so it's the new in-sync.
        if jira is None:
            return {"ok": False, "error": "Jira account not linked — cannot update the story."}
        current = _as_dict(row.get("current_snapshot"))
        draft = _run_story_update_agent(
            requirement_id=req_id,
            title=current.get("title") or "",
            description=current.get("description") or "",
            requirement_text=row.get("source_text") or "",
            implied_change=row.get("current_text") or "",
            comments_text=row.get("comment_excerpt") or "",
            user_id=user_id,
        )
        if not (draft.get("title") or draft.get("description")):
            return {"ok": False, "error": "Couldn't draft a story update — try again."}
        from services.jira_sync_service import _jira_fields_from_proposed
        fields = _jira_fields_from_proposed({
            "title": draft.get("title"),
            "description": draft.get("description"),
        })
        if fields:
            jira.update_issue(target_id, fields)
        # The pushed content is now the in-sync baseline for the field detector.
        if row.get("lineage_id"):
            new_baseline = snapshot_from_proposed({
                "title": draft.get("title"),
                "description": draft.get("description"),
            })
            try:
                refresh_baseline(str(row["lineage_id"]), new_baseline)
            except Exception as e:
                logger.warning(f"[JiraDrift] baseline refresh after update-story failed: {e}")
        _update_drift_resolution(
            drift_id, status="resolved", resolution="update-story",
            note=note, user_id=user_id, proposed_story_update=draft,
        )
        return {"ok": True, "status": "resolved",
                "proposed_story_update": draft,
                "message": f"{target_id} updated from the comment"}

    return {"ok": False, "error": f"Unknown resolution '{resolution}'"}


def _run_story_update_agent(
    requirement_id: str,
    title: str,
    description: str,
    requirement_text: str,
    implied_change: str,
    comments_text: str,
    user_id: str,
) -> dict:
    prompt = build_story_update_prompt(
        requirement_id=requirement_id, title=title, description=description,
        requirement_text=requirement_text, implied_change=implied_change,
        comments_text=comments_text,
    )
    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=BEDROCK_MODEL_ID, temperature=0, max_tokens=2000,
            user_id=user_id, token_source="jira_sync_story_update",
        )
        return _extract_json(raw)
    except Exception as e:
        logger.error(f"[JiraDrift] story update agent failed for {requirement_id}: {e}")
        return {"title": "", "description": "",
                "change_summary": "Story update generation failed — please edit manually.",
                "rationale": str(e)[:300]}
