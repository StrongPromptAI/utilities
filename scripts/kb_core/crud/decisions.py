"""Decisions compatibility shim — redirects to questions CRUD.

Decisions have been merged into the questions table (a decision is a resolved
question with status='decided'). This module provides backward-compatible
function signatures during transition.
"""

from .questions import (
    list_questions,
    get_open_question,
    insert_candidate_questions,
    clear_candidate_questions,
    get_candidate_questions,
    decide_question,
    get_decided_questions,
)


def create_decision(
    project_id: int,
    topic: str,
    summary: str,
    source_call_id: int = None,
    contact_ids: list[int] = None,
    status: str = "open",
) -> int:
    """Insert a decision as a decided question. Returns ID."""
    from ..db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO questions
                   (project_id, topic, question, resolution, source_call_id,
                    status, stakeholder_type)
                   VALUES (%s, %s, %s, %s, %s, %s, NULL)
                   RETURNING id""",
                (project_id, topic, f"What was decided about {topic}?",
                 summary, source_call_id,
                 "decided" if status == "confirmed" else "open"),
            )
            question_id = cur.fetchone()["id"]
            if contact_ids:
                for cid in contact_ids:
                    cur.execute(
                        """INSERT INTO question_contacts (question_id, contact_id)
                           VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                        (question_id, cid),
                    )
            conn.commit()
            return question_id


def get_decision(decision_id: int) -> dict | None:
    """Get a question by ID (backward compat for decisions)."""
    row = get_open_question(decision_id)
    if row:
        # Map to old decision shape for consumers expecting it
        row["summary"] = row.get("resolution") or row.get("question", "")
    return row


def list_decisions(project_id: int, status: str = None) -> list[dict]:
    """List decided questions. Maps old status names."""
    mapped = {"confirmed": "decided"}.get(status, status)
    rows = list_questions(project_id, status=mapped)
    for r in rows:
        r["summary"] = r.get("resolution") or r.get("question", "")
        # Map decided_by from _attach_decided_by
        if "decided_by" not in r:
            r["decided_by"] = []
    return rows


def update_decision_status(decision_id: int, status: str, summary: str = None) -> bool:
    """Update question status (backward compat)."""
    from ..db import get_db
    mapped = {"confirmed": "decided"}.get(status, status)
    with get_db() as conn:
        with conn.cursor() as cur:
            if summary:
                cur.execute(
                    "UPDATE questions SET status = %s, resolution = %s, updated_at = now() WHERE id = %s",
                    (mapped, summary, decision_id),
                )
            else:
                cur.execute(
                    "UPDATE questions SET status = %s, updated_at = now() WHERE id = %s",
                    (mapped, decision_id),
                )
            conn.commit()
            return cur.rowcount > 0


def clear_candidate_decisions(call_id: int) -> int:
    """Clear open questions from a call (backward compat)."""
    return clear_candidate_questions(call_id)


def insert_candidate_decisions(project_id: int, call_id: int, decisions: list[dict]) -> int:
    """Insert decisions as questions (backward compat)."""
    questions = []
    for d in decisions:
        questions.append({
            "topic": d["topic"],
            "question": f"What was decided about {d['topic']}?",
            "resolution": d.get("summary"),
            "status": "decided" if d.get("status") == "confirmed" else "open",
            "contact_ids": d.get("contact_ids", []),
            "stakeholder_type": d.get("stakeholder_type"),
        })
    return insert_candidate_questions(project_id, call_id, questions)


def get_candidate_decisions(project_id: int, call_id: int = None) -> list[dict]:
    """Get candidate questions (backward compat)."""
    rows = get_candidate_questions(project_id, call_id)
    for r in rows:
        r["summary"] = r.get("resolution") or r.get("question", "")
    return rows


def confirm_decision(decision_id: int) -> bool:
    """Confirm a decision (set status=decided)."""
    return decide_question(decision_id, resolution=None)


def reject_decision(decision_id: int) -> bool:
    """Delete a rejected candidate."""
    from ..db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM questions WHERE id = %s AND status = 'open'",
                (decision_id,),
            )
            conn.commit()
            return cur.rowcount > 0
