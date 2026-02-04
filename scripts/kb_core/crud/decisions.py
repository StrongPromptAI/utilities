"""Decisions CRUD operations."""

from ..db import get_db


def create_decision(
    project_id: int,
    topic: str,
    summary: str,
    source_call_ids: list[int] = None,
    decided_by: list[str] = None,
    status: str = "open",
) -> int:
    """Insert a decision. Returns ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO decisions
                   (project_id, topic, summary, source_call_ids, decided_by, status)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (project_id, topic, summary, source_call_ids, decided_by, status),
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_decision(decision_id: int) -> dict | None:
    """Get a single decision by ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM decisions WHERE id = %s", (decision_id,))
            return cur.fetchone()


def list_decisions(project_id: int, status: str = None) -> list[dict]:
    """List decisions for a project, optionally filtered by status."""
    query = "SELECT * FROM decisions WHERE project_id = %s"
    params: list = [project_id]
    if status:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY created_at DESC"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def update_decision_status(
    decision_id: int, status: str, summary: str = None
) -> bool:
    """Update decision status and optionally summary."""
    if summary:
        query = "UPDATE decisions SET status = %s, summary = %s, updated_at = now() WHERE id = %s"
        params = (status, summary, decision_id)
    else:
        query = "UPDATE decisions SET status = %s, updated_at = now() WHERE id = %s"
        params = (status, decision_id)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            conn.commit()
            return cur.rowcount > 0


def clear_candidate_decisions(call_id: int) -> int:
    """Delete open decisions sourced from a specific call (for re-harvest)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM decisions
                   WHERE status = 'open' AND %s = ANY(source_call_ids)""",
                (call_id,),
            )
            conn.commit()
            return cur.rowcount


def insert_candidate_decisions(
    project_id: int, call_id: int, decisions: list[dict]
) -> int:
    """Bulk insert candidate decisions from harvest. Returns count."""
    if not decisions:
        return 0

    with get_db() as conn:
        with conn.cursor() as cur:
            for d in decisions:
                cur.execute(
                    """INSERT INTO decisions
                       (project_id, topic, summary, source_call_ids, decided_by, status)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        project_id,
                        d["topic"],
                        d["summary"],
                        [call_id],
                        d.get("decided_by", []),
                        "open",  # always insert as candidate; human review confirms
                    ),
                )
        conn.commit()
    return len(decisions)


def get_candidate_decisions(project_id: int, call_id: int = None) -> list[dict]:
    """Get open (candidate) decisions, optionally filtered by source call."""
    query = "SELECT * FROM decisions WHERE project_id = %s AND status = 'open'"
    params: list = [project_id]
    if call_id:
        query += " AND %s = ANY(source_call_ids)"
        params.append(call_id)
    query += " ORDER BY id"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def confirm_decision(decision_id: int) -> bool:
    """Confirm a decision (approve from candidate)."""
    return update_decision_status(decision_id, "confirmed")


def reject_decision(decision_id: int) -> bool:
    """Delete a rejected candidate decision."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM decisions WHERE id = %s AND status = 'open'",
                (decision_id,),
            )
            conn.commit()
            return cur.rowcount > 0
