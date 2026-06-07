"""Chunk CRUD operations and batch summaries."""

from ..db import get_db
from ..embeddings import get_embedding


def insert_chunks(call_id: int, chunks: list, show_progress: bool = True) -> int:
    """Embed and insert chunks for a call. Returns count.

    Args:
        call_id: The call to attach chunks to
        chunks: List of chunk dicts {"speaker": str, "text": str} or list of strings (legacy)
        show_progress: Print progress every 20 chunks
    """
    with get_db() as conn:
        for idx, chunk in enumerate(chunks):
            # Handle both dict format (new) and string format (legacy)
            if isinstance(chunk, dict):
                text = chunk["text"]
                speaker = chunk.get("speaker")
                start_time = chunk.get("start_time")
                end_time = chunk.get("end_time")
            else:
                text = chunk
                speaker = None
                start_time = None
                end_time = None

            embedding = get_embedding(text)
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO call_chunks(call_id, chunk_idx, text, speaker, start_time, end_time, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (call_id, idx, text, speaker, start_time, end_time, embedding)
                )
            if show_progress and (idx + 1) % 20 == 0:
                print(f"  Embedded {idx + 1}/{len(chunks)} chunks...")
        conn.commit()
    return len(chunks)


def get_call_chunks(call_id: int) -> list[dict]:
    """Get all chunks for a call, ordered by chunk_idx."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, chunk_idx, speaker, text FROM call_chunks WHERE call_id = %s ORDER BY chunk_idx",
                (call_id,)
            )
            return cur.fetchall()


def get_call_batch_summaries(call_id: int) -> list[dict]:
    """Get all batch summaries for a call, ordered by batch_idx."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT batch_idx, start_chunk_idx, end_chunk_idx, summary
                   FROM chunk_batch_summaries
                   WHERE call_id = %s
                   ORDER BY batch_idx""",
                (call_id,)
            )
            return cur.fetchall()


def get_call_summary_text(call_id: int) -> str:
    """Get all batch summaries concatenated for a call.

    Useful for feeding to Claude for final call-level synthesis.
    """
    summaries = get_call_batch_summaries(call_id)
    if not summaries:
        return ""
    return "\n\n".join([
        f"[Segment {s['batch_idx']+1}] {s['summary']}"
        for s in summaries
    ])
