"""Open questions CRUD operations."""

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


def get_open_question(question_id: int) -> dict | None:
    """Get a single open question by ID with owner name."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT q.*, c.name as owner_name
                   FROM questions q
                   LEFT JOIN contacts c ON q.owner_contact_id = c.id
                   WHERE q.id = %s""",
                (question_id,),
            )
            return cur.fetchone()


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
            return cur.fetchall()


def resolve_question(
    question_id: int, resolution: str, decision_id: int = None
) -> bool:
    """Mark a question as answered."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE questions
                   SET status = 'answered', resolution = %s, decision_id = %s,
                       updated_at = now()
                   WHERE id = %s""",
                (resolution, decision_id, question_id),
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
    """
    if not questions:
        return 0

    with get_db() as conn:
        with conn.cursor() as cur:
            for q in questions:
                cur.execute(
                    """INSERT INTO questions
                       (project_id, topic, question, context, owner_contact_id,
                        source_call_id, stakeholder_type)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        project_id,
                        q["topic"],
                        q["question"],
                        q.get("context"),
                        q.get("owner_contact_id"),
                        call_id,
                        q.get("stakeholder_type"),
                    ),
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
