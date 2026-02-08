"""Chunk CRUD operations and batch summaries."""

from openai import OpenAI
from ..db import get_db
from ..embeddings import get_embedding
from ..config import LM_STUDIO_URL, SUMMARY_MODEL, BATCH_SIZE


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
            else:
                text = chunk
                speaker = None

            embedding = get_embedding(text)
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO call_chunks(call_id, chunk_idx, text, speaker, embedding)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (call_id, idx, text, speaker, embedding)
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


# --- Batch Summaries ---

def summarize_chunk_batch(chunks: list[dict]) -> str:
    """Summarize a batch of chunks using local LLM.

    Args:
        chunks: List of chunk dicts with 'text' field

    Returns:
        Summary string (2-3 sentences)
    """
    combined = "\n\n".join([c["text"] for c in chunks])

    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    response = client.chat.completions.create(
        model=SUMMARY_MODEL,
        messages=[{
            "role": "user",
            "content": f"""Summarize this business call segment in 2-3 sentences. Focus on key decisions, action items, and important context. Be concise.

TRANSCRIPT:
{combined}

SUMMARY:"""
        }],
        max_tokens=200,
        temperature=0.3
    )
    return response.choices[0].message.content.strip()


def generate_call_batch_summaries(call_id: int, batch_size: int = BATCH_SIZE, show_progress: bool = True) -> dict:
    """Generate batch summaries for all chunks in a call.

    Processes chunks in batches of batch_size, summarizes each batch,
    and stores in chunk_batch_summaries table.

    Returns:
        {"call_id": int, "batches_created": int, "chunks_processed": int}
    """
    chunks = get_call_chunks(call_id)
    if not chunks:
        return {"error": f"No chunks found for call {call_id}"}

    # Clear existing summaries for this call
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_batch_summaries WHERE call_id = %s", (call_id,))
        conn.commit()

    batches_created = 0
    for batch_idx, i in enumerate(range(0, len(chunks), batch_size)):
        batch_chunks = chunks[i:i + batch_size]
        start_idx = batch_chunks[0]["chunk_idx"]
        end_idx = batch_chunks[-1]["chunk_idx"]

        if show_progress:
            print(f"  Summarizing batch {batch_idx} (chunks {start_idx}-{end_idx})...")

        summary = summarize_chunk_batch(batch_chunks)

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO chunk_batch_summaries
                       (call_id, batch_idx, start_chunk_idx, end_chunk_idx, summary)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (call_id, batch_idx, start_idx, end_idx, summary)
                )
            conn.commit()

        batches_created += 1

    return {
        "call_id": call_id,
        "batches_created": batches_created,
        "chunks_processed": len(chunks)
    }


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
