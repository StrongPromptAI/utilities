"""Decisions CRUD operations."""

from ..db import get_db


def create_decision(
    project_id: int,
    topic: str,
    summary: str,
    source_call_id: int = None,
    contact_ids: list[int] = None,
    status: str = "open",
) -> int:
    """Insert a decision and link contacts. Returns ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO decisions
                   (project_id, topic, summary, source_call_id, status)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id""",
                (project_id, topic, summary, source_call_id, status),
            )
            decision_id = cur.fetchone()["id"]

            if contact_ids:
                for cid in contact_ids:
                    cur.execute(
                        """INSERT INTO decision_contacts (decision_id, contact_id)
                           VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                        (decision_id, cid),
                    )
            conn.commit()
            return decision_id


def _attach_contacts(cur, decisions: list[dict]) -> list[dict]:
    """Attach decided_by contact list to each decision."""
    if not decisions:
        return decisions
    ids = [d["id"] for d in decisions]
    cur.execute(
        """SELECT dc.decision_id, c.id as contact_id, c.name
           FROM decision_contacts dc
           JOIN contacts c ON dc.contact_id = c.id
           WHERE dc.decision_id = ANY(%s)
           ORDER BY c.name""",
        (ids,),
    )
    contacts_by_decision = {}
    for row in cur.fetchall():
        contacts_by_decision.setdefault(row["decision_id"], []).append(
            {"id": row["contact_id"], "name": row["name"]}
        )
    for d in decisions:
        d["decided_by"] = contacts_by_decision.get(d["id"], [])
    return decisions


def get_decision(decision_id: int) -> dict | None:
    """Get a single decision by ID with contacts."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM decisions WHERE id = %s", (decision_id,))
            row = cur.fetchone()
            if row:
                _attach_contacts(cur, [row])
            return row


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
            rows = cur.fetchall()
            return _attach_contacts(cur, rows)


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
                   WHERE status = 'open' AND source_call_id = %s""",
                (call_id,),
            )
            conn.commit()
            return cur.rowcount


def insert_candidate_decisions(
    project_id: int, call_id: int, decisions: list[dict]
) -> int:
    """Bulk insert candidate decisions from harvest. Returns count.

    Each decision dict should have: topic, summary, status, contact_ids (list[int]).
    """
    if not decisions:
        return 0

    with get_db() as conn:
        with conn.cursor() as cur:
            for d in decisions:
                cur.execute(
                    """INSERT INTO decisions
                       (project_id, topic, summary, source_call_id, status, stakeholder_type)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (
                        project_id,
                        d["topic"],
                        d["summary"],
                        call_id,
                        "open",
                        d.get("stakeholder_type"),
                    ),
                )
                decision_id = cur.fetchone()["id"]

                for cid in d.get("contact_ids", []):
                    if cid is not None:
                        cur.execute(
                            """INSERT INTO decision_contacts (decision_id, contact_id)
                               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                            (decision_id, cid),
                        )
        conn.commit()
    return len(decisions)


def get_candidate_decisions(project_id: int, call_id: int = None) -> list[dict]:
    """Get open (candidate) decisions, optionally filtered by source call."""
    query = "SELECT * FROM decisions WHERE project_id = %s AND status = 'open'"
    params: list = [project_id]
    if call_id:
        query += " AND source_call_id = %s"
        params.append(call_id)
    query += " ORDER BY id"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return _attach_contacts(cur, rows)


def confirm_decision(decision_id: int) -> bool:
    """Confirm a decision (approve from candidate)."""
    return update_decision_status(decision_id, "confirmed")


def reject_decision(decision_id: int) -> bool:
    """Delete a rejected candidate decision. CASCADE removes junction rows."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM decisions WHERE id = %s AND status = 'open'",
                (decision_id,),
            )
            conn.commit()
            return cur.rowcount > 0
