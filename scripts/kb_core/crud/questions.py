"""Questions CRUD operations (unified: questions + decisions merged)."""

from ..db import get_db


def create_open_question(
    project_id: int,
    topic: str,
    question: str,
    context: str = None,
    owner_contact_id: int = None,
    source_call_id: int = None,
) -> int:
    """Insert an open question. Returns ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO questions
                   (project_id, topic, question, context, owner_contact_id, source_call_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (project_id, topic, question, context, owner_contact_id, source_call_id),
            )
            conn.commit()
            return cur.fetchone()["id"]


def _attach_decided_by(cur, questions: list[dict]) -> list[dict]:
    """Attach decided_by contact list to each question via question_contacts."""
    if not questions:
        return questions
    ids = [q["id"] for q in questions]
    cur.execute(
        """SELECT qc.question_id, c.id as contact_id, c.name
           FROM question_contacts qc
           JOIN contacts c ON qc.contact_id = c.id
           WHERE qc.question_id = ANY(%s)
           ORDER BY c.name""",
        (ids,),
    )
    contacts_by_question = {}
    for row in cur.fetchall():
        contacts_by_question.setdefault(row["question_id"], []).append(
            {"id": row["contact_id"], "name": row["name"]}
        )
    for q in questions:
        q["decided_by"] = contacts_by_question.get(q["id"], [])
    return questions


def get_open_question(question_id: int) -> dict | None:
    """Get a single question by ID with owner name and decided_by contacts."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT q.*, c.name as owner_name
                   FROM questions q
                   LEFT JOIN contacts c ON q.owner_contact_id = c.id
                   WHERE q.id = %s""",
                (question_id,),
            )
            row = cur.fetchone()
            if row:
                _attach_decided_by(cur, [row])
            return row


def list_questions(project_id: int, status: str = None) -> list[dict]:
    """List questions for a project, optionally filtered by status."""
    query = """SELECT q.*, c.name as owner_name
               FROM questions q
               LEFT JOIN contacts c ON q.owner_contact_id = c.id
               WHERE q.project_id = %s"""
    params: list = [project_id]
    if status:
        query += " AND q.status = %s"
        params.append(status)
    query += " ORDER BY q.created_at DESC"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return _attach_decided_by(cur, rows)


def get_decided_questions(project_id: int) -> list[dict]:
    """Get all decided questions for a project (replaces list_decisions)."""
    return list_questions(project_id, status="decided")


def decide_question(
    question_id: int, resolution: str, contact_ids: list[int] = None
) -> bool:
    """Mark a question as decided (group agreement reached)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE questions
                   SET status = 'decided', resolution = %s, updated_at = now()
                   WHERE id = %s""",
                (resolution, question_id),
            )
            if contact_ids:
                for cid in contact_ids:
                    cur.execute(
                        """INSERT INTO question_contacts (question_id, contact_id)
                           VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                        (question_id, cid),
                    )
            conn.commit()
            return cur.rowcount > 0


def resolve_question(question_id: int, resolution: str) -> bool:
    """Mark a question as answered (informational resolution)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE questions
                   SET status = 'answered', resolution = %s, updated_at = now()
                   WHERE id = %s""",
                (resolution, question_id),
            )
            conn.commit()
            return cur.rowcount > 0


def clear_candidate_questions(call_id: int) -> int:
    """Delete open questions sourced from a specific call (for re-harvest)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM questions
                   WHERE status = 'open' AND source_call_id = %s""",
                (call_id,),
            )
            conn.commit()
            return cur.rowcount


def insert_candidate_questions(
    project_id: int, call_id: int, questions: list[dict]
) -> int:
    """Bulk insert candidate questions from harvest. Returns count.

    Each question dict should have: topic, question, context, owner_contact_id.
    For decided items: status='decided', resolution, contact_ids (list[int]).
    """
    if not questions:
        return 0

    with get_db() as conn:
        with conn.cursor() as cur:
            for q in questions:
                status = q.get("status", "open")
                cur.execute(
                    """INSERT INTO questions
                       (project_id, topic, question, context, owner_contact_id,
                        source_call_id, stakeholder_type, status, resolution)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (
                        project_id,
                        q["topic"],
                        q["question"],
                        q.get("context"),
                        q.get("owner_contact_id"),
                        call_id,
                        q.get("stakeholder_type"),
                        status,
                        q.get("resolution"),
                    ),
                )
                question_id = cur.fetchone()["id"]

                # Link contacts for decided items
                for cid in q.get("contact_ids", []):
                    if cid is not None:
                        cur.execute(
                            """INSERT INTO question_contacts (question_id, contact_id)
                               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                            (question_id, cid),
                        )
        conn.commit()
    return len(questions)


def get_candidate_questions(project_id: int, call_id: int = None) -> list[dict]:
    """Get open (candidate) questions, optionally filtered by source call."""
    query = """SELECT q.*, c.name as owner_name
               FROM questions q
               LEFT JOIN contacts c ON q.owner_contact_id = c.id
               WHERE q.project_id = %s AND q.status = 'open'"""
    params: list = [project_id]
    if call_id:
        query += " AND q.source_call_id = %s"
        params.append(call_id)
    query += " ORDER BY q.id"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def abandon_question(question_id: int) -> bool:
    """Mark a question as abandoned."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE questions
                   SET status = 'abandoned', updated_at = now()
                   WHERE id = %s""",
                (question_id,),
            )
            conn.commit()
            return cur.rowcount > 0
