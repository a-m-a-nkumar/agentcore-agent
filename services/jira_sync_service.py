"""
Jira Sync — "Changes to apply" orchestrator.

Owns the scan pipeline: pre-filters Confluence pages by version, runs the
Diff agent on changed BRD pages, looks up downstream artifacts via the
artifact_lineage table, runs the Reconcile agent for each (changed FR ×
linked artifact), and persists everything to:

    jira_sync_runs              — one row per scan
    pending_changes             — one row per changed requirement
    proposed_artifact_updates   — one row per downstream proposal

Apply step writes approved proposals to Jira / Confluence and bumps the
lineage's source_version.

This module is sync (matches brd_comparison.py). The router invokes
scan_workspace() via FastAPI BackgroundTasks.
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from db_helper import (
    get_db_connection,
    release_db_connection,
    get_user_atlassian_credentials,
)
from environment import chat_completion
from services.confluence_service import ConfluenceService
from services.jira_service import JiraService
from services.lineage_service import (
    bump_source_version_for_page,
    get_lineage_by_source_workspace,
)
from services import jira_drift_service
from utils.requirement_ids import normalize_requirement_id
from prompts.jira_sync_diff_prompts import build_brd_diff_prompt
from prompts.jira_sync_reconcile_prompts import (
    build_reconcile_prompt,
    build_reconcile_gate_prompt,
)

logger = logging.getLogger(__name__)

BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
)

# Dedup window — if a complete scan exists for this workspace within the
# last N minutes, scan_workspace short-circuits and returns it.
SCAN_DEDUP_WINDOW_MIN = int(os.getenv("JIRA_SYNC_DEDUP_WINDOW_MIN", "5"))

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


# ── LLM JSON helper ──────────────────────────────────────────────────────────


def _extract_json(raw: str) -> dict:
    if not raw:
        raise ValueError("LLM returned empty response")
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {raw[:200]}")
    return json.loads(match.group(0))


# ── Public entry points ─────────────────────────────────────────────────────


def scan_workspace(
    workspace_key: str,
    triggered_by_user_id: str,
    triggered_by_project_id: str,
    confluence: ConfluenceService,
) -> Tuple[str, bool]:
    """
    Run a full scan for a workspace. Returns (scan_run_id, cached).
    `cached=True` means a recent complete scan was reused — no LLM cost.

    The caller (router) is responsible for constructing the ConfluenceService
    with the triggering user's credentials.
    """
    cached_id = _find_recent_complete_scan(workspace_key)
    if cached_id:
        logger.info(f"[JiraSync] reusing recent scan {cached_id} for ws={workspace_key}")
        return cached_id, True

    run_id = _create_run_row(workspace_key, triggered_by_user_id, triggered_by_project_id)
    if run_id is None:
        # A scan is already running for this workspace — reuse it.
        existing = find_running_scan(workspace_key)
        logger.info(f"[JiraSync] scan already running for ws={workspace_key}; reusing {existing}")
        return existing or "", False
    logger.info(f"[JiraSync] starting scan {run_id} ws={workspace_key} user={triggered_by_user_id}")

    try:
        _run_scan_pipeline(run_id, workspace_key, triggered_by_user_id, confluence)
        _mark_run(run_id, status="complete")
    except Exception as e:
        logger.exception(f"[JiraSync] scan {run_id} failed")
        _mark_run(run_id, status="failed", message=str(e)[:500])
    return run_id, False


def get_pulse(workspace_key: str) -> Dict:
    """Most recent scan + summary of every pending change for the workspace."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT * FROM jira_sync_runs
                 WHERE workspace_key = %s
                 ORDER BY started_at DESC
                 LIMIT 1
                """,
                (workspace_key,),
            )
            run = cursor.fetchone()
            run = dict(run) if run else None

            cursor.execute(
                """
                SELECT id, source_page_id, requirement_id, severity, summary,
                       artifacts_affected, status, detected_at
                  FROM pending_changes
                 WHERE workspace_key = %s
                   AND status = 'pending'
                 ORDER BY detected_at DESC
                """,
                (workspace_key,),
            )
            changes = [dict(r) for r in cursor.fetchall()]
        # Reverse direction: open drift items for the same workspace.
        drift_items = jira_drift_service.get_drift_items(workspace_key)
        return {"run": run, "pending_changes": changes, "drift_items": drift_items}
    finally:
        if conn:
            release_db_connection(conn)


def get_change_detail(pending_change_id: str) -> Optional[Dict]:
    """Full pending change + all proposed artifact updates."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM pending_changes WHERE id = %s",
                (pending_change_id,),
            )
            change = cursor.fetchone()
            if not change:
                return None

            cursor.execute(
                """
                SELECT * FROM proposed_artifact_updates
                 WHERE pending_change_id = %s
                 ORDER BY confidence DESC NULLS LAST, target_id
                """,
                (pending_change_id,),
            )
            artifacts = [dict(r) for r in cursor.fetchall()]
            return {"change": dict(change), "artifacts": artifacts}
    finally:
        if conn:
            release_db_connection(conn)


def set_decisions(
    pending_change_id: str,
    decisions: List[Dict],
    decided_by_user_id: str,
) -> int:
    """
    Bulk update proposed_artifact_updates.decision. Each entry is
    {"update_id": uuid, "decision": "approved"|"dismissed"|"edited"|"pending"}.

    Returns the count of rows actually updated.
    """
    if not decisions:
        return 0
    conn = None
    try:
        conn = get_db_connection()
        updated = 0
        with conn.cursor() as cursor:
            for d in decisions:
                cursor.execute(
                    """
                    UPDATE proposed_artifact_updates
                       SET decision = %s,
                           decided_by_user_id = %s,
                           decided_at = CURRENT_TIMESTAMP
                     WHERE id = %s
                       AND pending_change_id = %s
                       AND applied_at IS NULL
                    """,
                    (
                        d["decision"],
                        decided_by_user_id,
                        d["update_id"],
                        pending_change_id,
                    ),
                )
                updated += cursor.rowcount
            conn.commit()
        return updated
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"set_decisions failed: {e}")
        raise
    finally:
        if conn:
            release_db_connection(conn)


def apply_change(
    pending_change_id: str,
    applied_by_user_id: str,
    confluence: ConfluenceService,
    jira: JiraService,
) -> Dict:
    """
    For every proposal on this pending change that has decision='approved'
    and applied_at IS NULL, push the update to Jira / Confluence and bump
    the lineage source_version on success.

    Returns {applied: int, failed: [{update_id, target_id, error}]}.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT p.*,
                       pc.source_page_id,
                       pc.source_version_to,
                       pc.workspace_key AS pc_workspace_key,
                       al.target_metadata AS lineage_target_metadata
                  FROM proposed_artifact_updates p
                  JOIN pending_changes pc ON pc.id = p.pending_change_id
                  LEFT JOIN artifact_lineage al ON al.id = p.lineage_id
                 WHERE p.pending_change_id = %s
                   AND p.decision = 'approved'
                   AND p.applied_at IS NULL
                """,
                (pending_change_id,),
            )
            rows = [dict(r) for r in cursor.fetchall()]
    finally:
        if conn:
            release_db_connection(conn)

    applied = 0
    failed: List[Dict] = []
    for row in rows:
        try:
            _apply_one(row, confluence, jira)
            _mark_applied(row["id"], applied_by_user_id)
            applied += 1
        except Exception as e:
            logger.exception(f"apply failed for {row.get('target_id')}")
            _mark_apply_error(row["id"], str(e)[:500])
            failed.append({
                "update_id": str(row["id"]),
                "target_id": row.get("target_id"),
                "error": str(e),
            })

    # If at least one proposal applied successfully, bump the lineage
    # source_version for the page so future scans use the new baseline.
    if applied and rows:
        first = rows[0]
        bumped = bump_source_version_for_page(
            workspace_key=first["pc_workspace_key"],
            source_id=first["source_page_id"],
            new_source_version=first["source_version_to"],
        )
        logger.info(f"[JiraSync] bumped {bumped} lineage rows for page={first['source_page_id']}")

        # Mark the parent pending_change as applied if nothing's still pending.
        _maybe_close_pending_change(pending_change_id)

    return {"applied": applied, "failed": failed}


# ── Scan pipeline ────────────────────────────────────────────────────────────


def _run_scan_pipeline(
    run_id: str,
    workspace_key: str,
    user_id: str,
    confluence: ConfluenceService,
    jira: Optional[JiraService] = None,
) -> None:
    """
    Steps:
      1. List confluence_pages for every project in this workspace.
      2. For each: fetch fresh version from Confluence.
         If current.version > stored.version → candidate.
      3. Filter to pages that appear as source_id in artifact_lineage.
      4. For each candidate: run Diff agent.
      5. For each detected change: run Reconcile agent per linked artifact.
      6. Persist pending_change + proposed_artifact_updates rows.
      7. Reverse pass: detect Jira stories that drifted from their requirement.
    """
    candidate_pages = _pages_with_version_drift(workspace_key, confluence)
    pages_scanned = len(candidate_pages)
    pages_changed = 0
    changes_detected = 0

    for page in candidate_pages:
        page_id = page["page_id"]
        stored_version = page["stored_version"]
        current_version = page["current_version"]
        current_html = page["current_content"]
        title = page["title"]

        # Fetch previous version's body for the diff.
        try:
            prev_page = confluence.get_page_version(page_id, stored_version)
            previous_html = prev_page.get("content", "")
        except Exception as e:
            logger.warning(f"[JiraSync] can't fetch prior version of {page_id}: {e}")
            previous_html = ""

        diff_changes = _run_diff_agent(
            previous_text=_strip_html(previous_html),
            current_text=_strip_html(current_html),
            title=title,
            user_id=user_id,
        )
        # Watermark this page at its current version so the next scan skips it
        # unless it advances again — whether or not this diff found changes.
        _record_page_scanned(workspace_key, page_id, current_version)
        if not diff_changes:
            continue

        pages_changed += 1
        for ch in diff_changes:
            req_id = normalize_requirement_id(ch.get("requirement_id", ""))
            if not req_id:
                continue

            linked = get_lineage_by_source_workspace(
                workspace_key=workspace_key,
                source_id=page_id,
                source_section_id=req_id,
            )

            pending_id = _persist_pending_change(
                workspace_key=workspace_key,
                scan_run_id=run_id,
                source_page_id=page_id,
                requirement_id=req_id,
                severity=ch.get("severity", "MINOR"),
                summary=ch.get("summary", ""),
                old_text=ch.get("old_text"),
                new_text=ch.get("new_text"),
                source_version_from=stored_version,
                source_version_to=current_version,
                artifacts_affected=len(linked),
            )
            changes_detected += 1

            # For ADDED requirements with no lineage, no proposals to generate —
            # the row stands as an "FYI" change (matches FR-12 in the design).
            # One reconcile LLM call per linked artifact; run them CONCURRENTLY
            # (each is an independent LLM call) so 3 stories don't take 3× as long.
            def _reconcile_and_persist(lineage_row, _pending_id=pending_id, _ch=ch, _req_id=req_id):
                sev = _ch.get("severity", "MINOR")
                old_t = _ch.get("old_text") or ""
                new_t = _ch.get("new_text") or ""
                # CHEAP relevance gate first — skip the expensive reconcile for
                # artifacts this change doesn't touch (records a NO_CHANGE).
                if not _run_reconcile_gate(_req_id, sev, old_t, new_t, lineage_row, user_id):
                    _persist_proposal(
                        pending_change_id=_pending_id,
                        workspace_key=workspace_key,
                        lineage_id=str(lineage_row["id"]),
                        target_type=lineage_row["target_type"],
                        target_id=lineage_row["target_id"],
                        action="NO_CHANGE",
                        current_snapshot=lineage_row.get("original_generated_content") or {},
                        proposed_snapshot=None,
                        confidence=None,
                        rationale="Gated out — this change doesn't affect this artifact.",
                    )
                    return
                # EXPENSIVE generation only for artifacts the gate kept.
                proposal = _run_reconcile_agent(
                    requirement_id=_req_id,
                    severity=sev,
                    old_text=old_t,
                    new_text=new_t,
                    artifact_type=lineage_row["target_type"],
                    artifact_id=lineage_row["target_id"],
                    artifact_current_content=lineage_row.get("original_generated_content") or {},
                    user_id=user_id,
                )
                _persist_proposal(
                    pending_change_id=_pending_id,
                    workspace_key=workspace_key,
                    lineage_id=str(lineage_row["id"]),
                    target_type=lineage_row["target_type"],
                    target_id=lineage_row["target_id"],
                    action=proposal.get("action", "NO_CHANGE"),
                    current_snapshot=lineage_row.get("original_generated_content") or {},
                    proposed_snapshot=proposal.get("proposed"),
                    confidence=proposal.get("confidence"),
                    rationale=proposal.get("rationale"),
                )

            if linked:
                with ThreadPoolExecutor(
                    max_workers=jira_drift_service.JIRA_SYNC_MAX_WORKERS
                ) as ex:
                    list(ex.map(_reconcile_and_persist, linked))

    # Mark prior runs' still-pending changes as superseded so the new scan's
    # results are the only ones the UI sees. Done at the END of the pipeline
    # so old cards remain visible during scanning (better UX than wiping them
    # up-front and showing an empty panel for 30s). Scoped to pages we actually
    # re-diffed this run — pages we SKIPPED (unchanged) keep their prior pending
    # changes instead of being wiped.
    rescanned_page_ids = [p["page_id"] for p in candidate_pages]
    _supersede_prior_pending_changes(
        workspace_key,
        current_scan_run_id=run_id,
        rescanned_page_ids=rescanned_page_ids,
    )

    _update_run_counters(run_id, pages_scanned, pages_changed, changes_detected)

    # Reverse direction: flag Jira stories edited out of sync with their BRD
    # requirement. Self-contained + never raises, so it can't fail the forward
    # scan; skipped silently when no Jira credentials were threaded in.
    jira_drift_service.run_drift_detection(
        run_id=run_id,
        workspace_key=workspace_key,
        jira=jira,
        confluence=confluence,
        user_id=user_id,
    )


def _pages_with_version_drift(
    workspace_key: str,
    confluence: ConfluenceService,
) -> List[Dict]:
    """
    Return Confluence pages that:
      a) belong to ≥1 project in this workspace,
      b) have current_version > stored confluence_pages.version_number,
      c) appear as source_id in artifact_lineage for this workspace
         (i.e. have actually been used to generate downstream artifacts —
         that's our definition of "is a BRD").
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Distinct pages across every project in this workspace that have
            # appeared as a lineage source, plus the version we last diffed
            # (the watermark) so we can skip pages that haven't moved.
            cursor.execute(
                """
                SELECT DISTINCT cp.page_id, cp.title, cp.version_number AS stored_version,
                       ps.scanned_version
                  FROM confluence_pages cp
                  JOIN projects p ON p.id = cp.project_id
                  JOIN artifact_lineage al ON al.source_id = cp.page_id
                                          AND al.workspace_key = %s
                  LEFT JOIN jira_sync_page_scan ps ON ps.workspace_key = %s
                                                  AND ps.page_id = cp.page_id
                 WHERE al.status = 'current'
                """,
                (workspace_key, workspace_key),
            )
            rows = [dict(r) for r in cursor.fetchall()]
    finally:
        if conn:
            release_db_connection(conn)

    drifted: List[Dict] = []
    skipped = 0
    for row in rows:
        page_id = row["page_id"]
        stored = row["stored_version"]
        try:
            current = confluence.get_page_content(page_id)
        except Exception as e:
            logger.warning(f"[JiraSync] skip {page_id} — Confluence fetch failed: {e}")
            continue

        current_version = current.get("version") or 1
        # Skip the LLM diff unless the live version is newer than BOTH the synced
        # version and the version we last scanned. A comment-only change leaves
        # every page <= its watermark, so the whole forward diff pass is skipped.
        baseline = max(stored or 0, row.get("scanned_version") or 0)
        if current_version > baseline:
            drifted.append({
                "page_id": page_id,
                "title": current.get("title") or row.get("title"),
                "stored_version": stored,
                "current_version": current_version,
                "current_content": current.get("content", ""),
            })
        else:
            skipped += 1

    if skipped:
        logger.info(
            f"[JiraSync] skipped {skipped} unchanged page(s) (version not advanced) "
            f"for ws={workspace_key}; {len(drifted)} to diff"
        )
    return drifted


def _record_page_scanned(workspace_key: str, page_id: str, version: int) -> None:
    """Watermark a page at `version` after diffing it, so the next scan skips it
    unless it advances. GREATEST() never moves the watermark backwards."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO jira_sync_page_scan (workspace_key, page_id, scanned_version, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (workspace_key, page_id)
                DO UPDATE SET
                    scanned_version = GREATEST(jira_sync_page_scan.scanned_version, EXCLUDED.scanned_version),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (workspace_key, page_id, version),
            )
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.warning(f"[JiraSync] failed to watermark page {page_id}: {e}")
    finally:
        if conn:
            release_db_connection(conn)


# ── Agent calls ──────────────────────────────────────────────────────────────


def _run_diff_agent(
    previous_text: str,
    current_text: str,
    title: str,
    user_id: str,
) -> List[Dict]:
    if not previous_text or not current_text:
        logger.info("[JiraSync] diff agent skipped — one side is empty")
        return []

    prompt = build_brd_diff_prompt(previous_text, current_text, title)
    raw = chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=BEDROCK_MODEL_ID,
        temperature=0,
        max_tokens=4000,
        user_id=user_id,
        token_source="jira_sync_diff",
    )
    try:
        parsed = _extract_json(raw)
    except Exception as e:
        logger.error(f"[JiraSync] diff agent parse failed: {e}\n--- raw ---\n{raw[:500]}")
        return []
    return parsed.get("changes", []) or []


def _run_reconcile_agent(
    requirement_id: str,
    severity: str,
    old_text: str,
    new_text: str,
    artifact_type: str,
    artifact_id: str,
    artifact_current_content: dict,
    user_id: str,
) -> Dict:
    prompt = build_reconcile_prompt(
        requirement_id=requirement_id,
        severity=severity,
        old_requirement_text=old_text,
        new_requirement_text=new_text,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        artifact_current_content=artifact_current_content,
    )
    raw = chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=BEDROCK_MODEL_ID,
        temperature=0,
        max_tokens=3000,
        user_id=user_id,
        token_source="jira_sync_reconcile",
    )
    try:
        return _extract_json(raw)
    except Exception as e:
        logger.error(f"[JiraSync] reconcile parse failed for {artifact_id}: {e}\n--- raw ---\n{raw[:500]}")
        return {
            "action": "NO_CHANGE",
            "proposed": None,
            "confidence": 0.0,
            "rationale": f"Agent output unparseable: {e}",
        }


def _artifact_summary(snapshot: Dict, target_type: str) -> str:
    """One-line summary of an artifact for the cheap relevance gate (keeps its
    prompt tiny vs. the full reconcile, which sends the whole artifact)."""
    snapshot = snapshot or {}
    title = snapshot.get("title") or snapshot.get("summary") or ""
    desc = snapshot.get("description") or ""
    if not isinstance(desc, str):
        desc = ""
    summary = f"{title} — {desc[:280]}".strip(" —")
    return summary or f"({target_type} with no stored summary)"


def _run_reconcile_gate(
    requirement_id: str,
    severity: str,
    old_text: str,
    new_text: str,
    lineage_row: Dict,
    user_id: str,
) -> bool:
    """
    CHEAP cascade gate: does this requirement change affect this artifact? Runs on
    GATE_MODEL with a tiny prompt before the expensive reconcile. Conservative —
    returns True on any uncertainty or error so a real propagation is never
    silently dropped.
    """
    snapshot = lineage_row.get("original_generated_content") or {}
    prompt = build_reconcile_gate_prompt(
        requirement_id=requirement_id,
        severity=severity,
        old_requirement_text=old_text,
        new_requirement_text=new_text,
        artifact_type=lineage_row["target_type"],
        artifact_id=lineage_row["target_id"],
        artifact_summary=_artifact_summary(snapshot, lineage_row["target_type"]),
    )
    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=jira_drift_service.GATE_MODEL,  # GATE → cheap model
            temperature=0,
            max_tokens=200,
            user_id=user_id,
            token_source="jira_sync_reconcile_gate",
        )
        parsed = _extract_json(raw)
        # Only skip on an explicit false; missing/None/anything-else → affected.
        return parsed.get("affects") is not False
    except Exception as e:
        logger.warning(
            f"[JiraSync] reconcile gate failed for {lineage_row.get('target_id')}: {e} "
            f"— defaulting to affected"
        )
        return True


# ── DB writes ────────────────────────────────────────────────────────────────


def _find_recent_complete_scan(workspace_key: str) -> Optional[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=SCAN_DEDUP_WINDOW_MIN)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM jira_sync_runs
                 WHERE workspace_key = %s
                   AND status = 'complete'
                   AND completed_at > %s
                 ORDER BY completed_at DESC
                 LIMIT 1
                """,
                (workspace_key, cutoff),
            )
            row = cursor.fetchone()
            return str(row[0]) if row else None
    finally:
        if conn:
            release_db_connection(conn)


def find_running_scan(workspace_key: str) -> Optional[str]:
    """
    Return the id of an in-flight scan for this workspace, if any.
    Used to prevent concurrent scans from race-superseding each other's
    output. This check is independent of the dedup time window — even a
    `force=true` request must respect an in-flight scan.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM jira_sync_runs
                 WHERE workspace_key = %s
                   AND status = 'running'
                 ORDER BY started_at DESC
                 LIMIT 1
                """,
                (workspace_key,),
            )
            row = cursor.fetchone()
            return str(row[0]) if row else None
    finally:
        if conn:
            release_db_connection(conn)


def _create_run_row(workspace_key: str, user_id: str, project_id: str) -> Optional[str]:
    """
    Create a 'running' scan row. Returns the new id, or None if a scan is
    already running for this workspace (the partial unique index
    uq_one_running_scan_per_ws makes the insert atomic, closing the race
    between find_running_scan() and this insert). Self-heals crashed scans by
    retiring 'running' rows older than 15 minutes first.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jira_sync_runs
                   SET status = 'failed',
                       message = COALESCE(message, 'stale — retired'),
                       completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
                 WHERE workspace_key = %s AND status = 'running'
                   AND started_at < (CURRENT_TIMESTAMP - INTERVAL '15 minutes')
                """,
                (workspace_key,),
            )
            cursor.execute(
                """
                INSERT INTO jira_sync_runs
                    (workspace_key, triggered_by_user_id, triggered_by_project_id, status)
                VALUES (%s, %s, %s, 'running')
                RETURNING id
                """,
                (workspace_key, user_id, project_id),
            )
            run_id = str(cursor.fetchone()[0])
            conn.commit()
            return run_id
    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
        logger.info(
            f"[JiraSync] a scan is already running for ws={workspace_key}; skipping duplicate"
        )
        return None
    finally:
        if conn:
            release_db_connection(conn)


def _mark_run(run_id: str, status: str, message: Optional[str] = None) -> None:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jira_sync_runs
                   SET status = %s,
                       message = COALESCE(%s, message),
                       completed_at = CASE
                           WHEN %s IN ('complete','failed') THEN CURRENT_TIMESTAMP
                           ELSE completed_at
                       END
                 WHERE id = %s
                """,
                (status, message, status, run_id),
            )
            conn.commit()
    finally:
        if conn:
            release_db_connection(conn)


def _update_run_counters(
    run_id: str,
    pages_scanned: int,
    pages_changed: int,
    changes_detected: int,
) -> None:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jira_sync_runs
                   SET pages_scanned = %s,
                       pages_changed = %s,
                       changes_detected = %s
                 WHERE id = %s
                """,
                (pages_scanned, pages_changed, changes_detected, run_id),
            )
            conn.commit()
    finally:
        if conn:
            release_db_connection(conn)


def _persist_pending_change(
    workspace_key: str,
    scan_run_id: str,
    source_page_id: str,
    requirement_id: str,
    severity: str,
    summary: str,
    old_text: Optional[str],
    new_text: Optional[str],
    source_version_from: Optional[int],
    source_version_to: Optional[int],
    artifacts_affected: int,
) -> str:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pending_changes (
                    workspace_key, scan_run_id, source_page_id, requirement_id,
                    severity, summary, old_text, new_text,
                    source_version_from, source_version_to, artifacts_affected
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    workspace_key, scan_run_id, source_page_id, requirement_id,
                    severity, summary, old_text, new_text,
                    source_version_from, source_version_to, artifacts_affected,
                ),
            )
            pending_id = str(cursor.fetchone()[0])
            conn.commit()
            return pending_id
    finally:
        if conn:
            release_db_connection(conn)


def _persist_proposal(
    pending_change_id: str,
    workspace_key: str,
    lineage_id: str,
    target_type: str,
    target_id: str,
    action: str,
    current_snapshot: dict,
    proposed_snapshot: Optional[dict],
    confidence: Optional[float],
    rationale: Optional[str],
) -> None:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO proposed_artifact_updates (
                    pending_change_id, workspace_key, lineage_id,
                    target_type, target_id, action,
                    current_snapshot, proposed_snapshot,
                    confidence, rationale
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s::jsonb, %s::jsonb,
                    %s, %s
                )
                """,
                (
                    pending_change_id, workspace_key, lineage_id,
                    target_type, target_id, action,
                    json.dumps(current_snapshot or {}),
                    json.dumps(proposed_snapshot) if proposed_snapshot is not None else None,
                    confidence, rationale,
                ),
            )
            conn.commit()
    finally:
        if conn:
            release_db_connection(conn)


def _mark_applied(update_id: str, applied_by_user_id: str) -> None:
    """
    Concurrency-safe apply mark. The partial unique index on
    (id WHERE applied_at IS NOT NULL) plus the `WHERE applied_at IS NULL`
    guard makes a second concurrent apply a no-op.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE proposed_artifact_updates
                   SET applied_at = CURRENT_TIMESTAMP,
                       applied_by_user_id = %s,
                       apply_error = NULL
                 WHERE id = %s
                   AND applied_at IS NULL
                """,
                (applied_by_user_id, update_id),
            )
            conn.commit()
    finally:
        if conn:
            release_db_connection(conn)


def _mark_apply_error(update_id: str, error: str) -> None:
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE proposed_artifact_updates
                   SET apply_error = %s
                 WHERE id = %s
                """,
                (error, update_id),
            )
            conn.commit()
    finally:
        if conn:
            release_db_connection(conn)


def _supersede_prior_pending_changes(
    workspace_key: str,
    current_scan_run_id: str,
    rescanned_page_ids: Optional[List[str]] = None,
) -> int:
    """
    After this scan finishes, flip earlier-scan pending rows to 'superseded'.

    Constrained three ways:
      1. scan_run_id != current_scan_run_id  — don't flip our own rows
      2. the originating run's completed_at < THIS scan's started_at
         — so a scan that was running concurrently with us never has its
         rows flipped by us (and vice versa)
      3. source_page_id was actually re-diffed this run — pages we SKIPPED
         (version unchanged) keep their prior pending changes instead of
         being wiped. If nothing was re-diffed, supersede nothing.

    Without (2), two scans started seconds apart would mutually annihilate
    each other's output: each would flip the other's `pending` rows because
    both finish their inserts before either runs supersede.
    """
    # No page re-diffed this run → no fresh results to replace anything with.
    if rescanned_page_ids is not None and not rescanned_page_ids:
        return 0
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pending_changes pc
                   SET status = 'superseded'
                  FROM jira_sync_runs r_prior, jira_sync_runs r_self
                 WHERE pc.workspace_key = %s
                   AND pc.status = 'pending'
                   AND pc.scan_run_id <> %s
                   AND pc.scan_run_id = r_prior.id
                   AND r_self.id = %s
                   AND r_prior.completed_at IS NOT NULL
                   AND r_prior.completed_at < r_self.started_at
                   AND (%s::text[] IS NULL OR pc.source_page_id = ANY(%s::text[]))
                """,
                (
                    workspace_key, current_scan_run_id, current_scan_run_id,
                    rescanned_page_ids, rescanned_page_ids,
                ),
            )
            superseded = cursor.rowcount
            conn.commit()
            if superseded:
                logger.info(
                    f"[JiraSync] superseded {superseded} stale pending_changes for ws={workspace_key}"
                )
            return superseded
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Failed to supersede prior pending_changes: {e}")
        return 0
    finally:
        if conn:
            release_db_connection(conn)


def _maybe_close_pending_change(pending_change_id: str) -> None:
    """If every approved proposal on this change is now applied, close it."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) AS unapplied
                  FROM proposed_artifact_updates
                 WHERE pending_change_id = %s
                   AND decision = 'approved'
                   AND applied_at IS NULL
                """,
                (pending_change_id,),
            )
            row = cursor.fetchone()
            if row and row[0] == 0:
                cursor.execute(
                    """
                    UPDATE pending_changes
                       SET status = 'applied'
                     WHERE id = %s
                    """,
                    (pending_change_id,),
                )
                conn.commit()
    finally:
        if conn:
            release_db_connection(conn)


# ── Apply helpers ────────────────────────────────────────────────────────────


def _apply_one(row: Dict, confluence: ConfluenceService, jira: JiraService) -> None:
    """
    Push one proposed update to the right system. Raises on failure so the
    caller can record apply_error.
    """
    action = row.get("action") or "NO_CHANGE"
    if action == "NO_CHANGE":
        # Nothing to push; we still want to mark as applied so the user
        # confirms they reviewed it.
        return

    target_type = row["target_type"]
    target_id = row["target_id"]
    proposed = row.get("proposed_snapshot") or {}
    if isinstance(proposed, str):
        proposed = json.loads(proposed)

    if target_type == "jira_story":
        fields = _jira_fields_from_proposed(proposed)
        if not fields:
            logger.info(f"[JiraSync] {target_id}: nothing to push (empty proposed)")
            return
        jira.update_issue(target_id, fields)
        # Refresh the lineage baseline to what we just pushed, so this story
        # isn't re-flagged as drift on the next scan (the change was ours).
        lineage_id = row.get("lineage_id")
        if lineage_id:
            try:
                jira_drift_service.refresh_baseline(
                    str(lineage_id), jira_drift_service.snapshot_from_proposed(proposed)
                )
            except Exception as e:
                logger.warning(f"[JiraSync] baseline refresh failed for {target_id}: {e}")
    elif target_type == "test_scenario":
        # Test scenarios live on Confluence. The page they live on was stored
        # in target_metadata.confluence_page_id when lineage was first written.
        meta = row.get("lineage_target_metadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        page_id = meta.get("confluence_page_id")
        if not page_id:
            raise Exception(
                f"test_scenario {target_id} has no confluence_page_id in target_metadata — cannot update"
            )
        # Confluence update_page expects (page_id, title, content, current_version).
        # We fetch current page to learn its title + version, then post the
        # proposed body. NOTE: a real test-scenario update needs to splice
        # just this scenario into the existing page rather than overwriting
        # the whole page. That splicing logic lives in test_generation; this
        # caller-side path is intentionally conservative and leaves the
        # splicing as a TODO for the follow-up "Edit then approve" UI.
        current = confluence.get_page_content(page_id)
        new_body = proposed.get("body_xhtml") or current.get("content")
        confluence.update_page(
            page_id=page_id,
            title=current.get("title"),
            content=new_body,
            current_version=current.get("version") or 1,
        )
    else:
        raise Exception(f"Unknown target_type {target_type} for {target_id}")


def _jira_fields_from_proposed(proposed: Dict) -> Dict:
    """
    Map the Reconcile agent's `proposed` block to Jira PUT field shape.
    Description goes in as plain text (Jira ADF wrapper) for simplicity —
    callers wanting richer formatting can post-process.
    """
    fields: Dict = {}
    if "title" in proposed and proposed["title"]:
        fields["summary"] = proposed["title"]
    if "description" in proposed and proposed["description"]:
        fields["description"] = _to_adf_paragraph(proposed["description"])
    if "story_points" in proposed and proposed["story_points"] is not None:
        # The custom field id for story points varies per Jira instance —
        # we leave it configurable via env var, default to common 10016.
        sp_field = os.getenv("JIRA_STORY_POINTS_FIELD", "customfield_10016")
        fields[sp_field] = proposed["story_points"]
    if "priority" in proposed and proposed["priority"]:
        fields["priority"] = {"name": proposed["priority"]}
    # Acceptance criteria typically lives inside the description on most
    # Jira instances. If the team uses a dedicated AC field, surface it via
    # JIRA_ACCEPTANCE_CRITERIA_FIELD env var.
    if "acceptance_criteria" in proposed and proposed["acceptance_criteria"]:
        ac_field = os.getenv("JIRA_ACCEPTANCE_CRITERIA_FIELD")
        if ac_field:
            fields[ac_field] = "\n".join(f"- {a}" for a in proposed["acceptance_criteria"])
        else:
            # Append AC into the description ADF instead.
            existing = fields.get("description") or _to_adf_paragraph("")
            ac_text = "Acceptance criteria:\n" + "\n".join(
                f"- {a}" for a in proposed["acceptance_criteria"]
            )
            fields["description"] = _adf_append(existing, ac_text)
    return fields


def _to_adf_paragraph(text: str) -> Dict:
    """Minimal Jira ADF wrapper around plain text."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _adf_append(adf: Dict, extra_text: str) -> Dict:
    out = json.loads(json.dumps(adf))  # cheap deep copy
    out["content"].append(
        {"type": "paragraph", "content": [{"type": "text", "text": extra_text}]}
    )
    return out


# ── HTML helper (mirrors brd_comparison.py:_strip_html) ──────────────────────


def _strip_html(html: str) -> str:
    if not html:
        return ""
    cleaned = re.sub(r"<ac:structured-macro[\s\S]*?</ac:structured-macro>", " ", html)
    cleaned = re.sub(r"<ac:adf-extension[\s\S]*?</ac:adf-extension>", " ", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    from html import unescape as _un
    cleaned = _un(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


# ── Convenience: build a ConfluenceService / JiraService for a user ──────────


def build_confluence_for_user(user_id: str) -> Optional[ConfluenceService]:
    creds = get_user_atlassian_credentials(user_id)
    if not creds or not creds.get("atlassian_api_token"):
        return None
    return ConfluenceService(
        domain=creds["atlassian_domain"],
        email=creds["atlassian_email"],
        api_token=creds["atlassian_api_token"],
    )


def build_jira_for_user(user_id: str) -> Optional[JiraService]:
    creds = get_user_atlassian_credentials(user_id)
    if not creds or not creds.get("atlassian_api_token"):
        return None
    return JiraService(
        domain=creds["atlassian_domain"],
        email=creds["atlassian_email"],
        api_token=creds["atlassian_api_token"],
    )
