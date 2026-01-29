"""Quote CRUD operations."""

from datetime import datetime
from ..db import get_db


def insert_candidate_quotes(call_id: int, quotes: list[dict]) -> int:
    """Bulk insert candidate quotes for a call.

    Args:
        call_id: The call to attach quotes to
        quotes: List of quote dicts with keys:
            - quote_text (required)
            - speaker (optional)
            - context (optional)
            - category (optional)
            - chunk_id (optional)

    Returns:
        Number of quotes inserted
    """
    if not quotes:
        return 0

    with get_db() as conn:
        with conn.cursor() as cur:
            for quote in quotes:
                cur.execute(
                    """INSERT INTO call_quotes
                       (call_id, chunk_id, quote_text, speaker, context, category, status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'candidate')""",
                    (
                        call_id,
                        quote.get("chunk_id"),
                        quote["quote_text"],
                        quote.get("speaker"),
                        quote.get("context"),
                        quote.get("category"),
                    )
                )
        conn.commit()
    return len(quotes)


def get_candidate_quotes(call_id: int) -> list[dict]:
    """Get pending candidate quotes for a call."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, chunk_id, quote_text, speaker, context, category, created_at
                   FROM call_quotes
                   WHERE call_id = %s AND status = 'candidate'
                   ORDER BY id""",
                (call_id,)
            )
            return cur.fetchall()


def get_approved_quotes(call_id: int) -> list[dict]:
    """Get approved quotes for a call."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, chunk_id, quote_text, speaker, context, category, approved_at
                   FROM call_quotes
                   WHERE call_id = %s AND status = 'approved'
                   ORDER BY approved_at""",
                (call_id,)
            )
            return cur.fetchall()


def approve_quote(quote_id: int) -> bool:
    """Approve a single quote."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE call_quotes
                   SET status = 'approved', approved_at = %s
                   WHERE id = %s AND status = 'candidate'""",
                (datetime.now(), quote_id)
            )
            conn.commit()
            return cur.rowcount > 0


def reject_quote(quote_id: int) -> bool:
    """Reject a single quote."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE call_quotes
                   SET status = 'rejected'
                   WHERE id = %s AND status = 'candidate'""",
                (quote_id,)
            )
            conn.commit()
            return cur.rowcount > 0


def bulk_approve_quotes(quote_ids: list[int]) -> int:
    """Approve multiple quotes. Returns count approved."""
    if not quote_ids:
        return 0

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE call_quotes
                   SET status = 'approved', approved_at = %s
                   WHERE id = ANY(%s) AND status = 'candidate'""",
                (datetime.now(), quote_ids)
            )
            conn.commit()
            return cur.rowcount


def bulk_reject_quotes(quote_ids: list[int]) -> int:
    """Reject multiple quotes. Returns count rejected."""
    if not quote_ids:
        return 0

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE call_quotes
                   SET status = 'rejected'
                   WHERE id = ANY(%s) AND status = 'candidate'""",
                (quote_ids,)
            )
            conn.commit()
            return cur.rowcount


def clear_candidate_quotes(call_id: int) -> int:
    """Clear all candidate quotes for a call (for re-extraction).

    Returns count deleted.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM call_quotes
                   WHERE call_id = %s AND status = 'candidate'""",
                (call_id,)
            )
            conn.commit()
            return cur.rowcount
