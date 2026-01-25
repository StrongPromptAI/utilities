"""
Knowledge Base Core Library

Shared functions for kb-ingest and kb-check skills.
"""

import psycopg
from psycopg.rows import dict_row
from openai import OpenAI
from pathlib import Path
from datetime import date
from typing import Optional
import csv
import re
from io import StringIO

# Config
DB_URL = "postgresql://localhost/knowledge_base"
LM_STUDIO_URL = "http://localhost:1234/v1"
EMBED_MODEL = "nomic-embed-text"


def get_db():
    """Get database connection."""
    return psycopg.connect(DB_URL, row_factory=dict_row)


def get_embedding(text: str) -> list[float]:
    """Generate embedding via LM Studio."""
    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    response = client.embeddings.create(model=EMBED_MODEL, input=text)
    return response.data[0].embedding


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    """Fixed-size chunking with overlap. Use for raw transcripts."""
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def chunk_by_sections(text: str, min_chunk_size: int = 50) -> list[str]:
    """Section-based chunking for structured notes.

    Splits on:
    - Markdown headers (## or ###)
    - Numbered sections (1., 2., etc. at line start)
    - Lettered subsections (a), b), etc. at line start)

    Use for structured notes where semantic units should be preserved.
    """
    import re

    lines = text.split('\n')
    chunks = []
    current_chunk_lines = []
    current_header = ""

    # Patterns that indicate a new section
    section_patterns = [
        r'^#{1,4}\s+',           # Markdown headers
        r'^\d+\.\s+[A-Z]',       # Numbered sections starting with caps (1. TITLE)
        r'^[a-z]\)\s+[A-Z]',     # Lettered subsections (a) TITLE)
        r'^[A-Z][A-Z\s]+:$',     # ALL CAPS HEADER:
        r'^[A-Z][A-Z\s]+$',      # ALL CAPS LINE (standalone header)
    ]

    def is_section_start(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        for pattern in section_patterns:
            if re.match(pattern, stripped):
                return True
        return False

    def flush_chunk():
        nonlocal current_chunk_lines, current_header
        if current_chunk_lines:
            chunk_text = '\n'.join(current_chunk_lines).strip()
            if len(chunk_text) >= min_chunk_size:
                # Prepend header context if we have one
                if current_header and not chunk_text.startswith(current_header):
                    chunk_text = f"{current_header}\n{chunk_text}"
                chunks.append(chunk_text)
            current_chunk_lines = []

    for line in lines:
        if is_section_start(line):
            flush_chunk()
            current_header = line.strip()
            current_chunk_lines = [line]
        else:
            current_chunk_lines.append(line)

    # Don't forget the last chunk
    flush_chunk()

    # If no sections found, fall back to paragraph chunking
    if not chunks:
        chunks = [p.strip() for p in text.split('\n\n') if p.strip() and len(p.strip()) >= min_chunk_size]

    # If still nothing, return the whole text as one chunk
    if not chunks and text.strip():
        chunks = [text.strip()]

    return chunks


def preprocess_dialpad_transcript(raw_text: str, merge_speaker_turns: bool = True, filter_fillers: bool = True, llm_adjudicate: bool = True) -> dict:
    """Preprocess Dialpad CSV transcript.

    Strips timestamps, preserves speaker attribution, optionally merges
    consecutive turns by the same speaker, filters out agreement fillers.

    Args:
        raw_text: Raw CSV content from Dialpad
        merge_speaker_turns: If True, merge consecutive lines from same speaker
        filter_fillers: If True, remove low-value agreement statements
        llm_adjudicate: If True, use local LLM to classify borderline cases

    Returns:
        {
            "text": cleaned transcript text,
            "participants": list of unique speakers,
            "turn_count": number of speaker turns,
            "filtered_count": number of filler turns removed,
            "llm_filtered_count": number filtered by LLM adjudication
        }
    """
    # Filler patterns - obvious agreement/acknowledgment with no semantic value
    OBVIOUS_FILLER_PATTERNS = [
        r'^(yup|yep|yeah|yes|okay|ok|right|sure|uh-huh|uh huh|mm-hmm|mm hmm|mmm|hmm|alright|got it|correct|true|exactly|absolutely|definitely|totally|i see|oh|ah)\.?!?$',
        r'^(yup|yep|yeah|yes|okay|ok|right|sure|alright)[,\s]+(yup|yep|yeah|yes|okay|ok|right|sure|alright)?\.?$',  # "yeah, yeah"
        r'^(oh|ah|hey)[,\.]?$',  # Just "oh" or "hey"
        r'^that\'s (right|correct|true|it)\.?$',
        r'^(sounds good|for sure|of course|no doubt)\.?$',
        r'^i (agree|know|see|got it|understand)\.?$',
    ]

    # Words that suggest possible filler (for LLM adjudication)
    FILLER_INDICATORS = ['yeah', 'yup', 'yep', 'okay', 'ok', 'right', 'sure', 'alright', 'correct', 'true', 'exactly', 'definitely', 'absolutely', 'got it', 'i see', 'mm-hmm', 'uh-huh']

    def is_obvious_filler(text: str) -> bool:
        """Check if text is an obvious filler (regex match)."""
        normalized = text.lower().strip()
        if len(normalized) > 25:
            return False
        for pattern in OBVIOUS_FILLER_PATTERNS:
            if re.match(pattern, normalized, re.IGNORECASE):
                return True
        return False

    def is_borderline(text: str) -> bool:
        """Check if text is a borderline case needing LLM adjudication."""
        normalized = text.lower().strip()
        # Borderline: 15-80 chars, starts with or contains filler words, but has more content
        if len(normalized) < 15 or len(normalized) > 80:
            return False
        return any(ind in normalized for ind in FILLER_INDICATORS)

    def llm_classify_filler(texts: list[str]) -> list[bool]:
        """Use local LLM to classify borderline statements. Returns list of is_filler bools."""
        if not texts:
            return []

        client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

        results = []
        # Process in batches
        for text in texts:
            prompt = f"""Classify this statement from a business call transcript.
Is this FILLER (just agreement/acknowledgment with no real information) or CONTENT (has meaningful information)?

Statement: "{text}"

Reply with only one word: FILLER or CONTENT"""

            # Hard fail if model not loaded - no fallback
            response = client.chat.completions.create(
                model="qwen2.5-coder-1.5b-instruct-mlx",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0
            )
            answer = response.choices[0].message.content.strip().upper()
            results.append("FILLER" in answer)

        return results

    lines = []
    participants = set()
    filtered_count = 0
    llm_filtered_count = 0
    borderline_items = []  # (index, speaker, text) for LLM adjudication

    # Parse CSV - Dialpad format: "timestamp","speaker","text"
    reader = csv.reader(StringIO(raw_text))
    all_rows = []
    for row in reader:
        if len(row) >= 3:
            speaker = row[1].strip()
            text = row[2].strip()
            if speaker and text and speaker.lower() != 'name' and text.lower() != 'content':
                participants.add(speaker)
                all_rows.append({"speaker": speaker, "text": text})

    # First pass: obvious fillers and identify borderline
    for i, row in enumerate(all_rows):
        text = row["text"]
        if filter_fillers and is_obvious_filler(text):
            filtered_count += 1
            row["_status"] = "filtered"
        elif filter_fillers and llm_adjudicate and is_borderline(text):
            row["_status"] = "borderline"
            borderline_items.append((i, text))
        else:
            row["_status"] = "keep"

    # Second pass: LLM adjudication for borderline cases
    if borderline_items and llm_adjudicate:
        borderline_texts = [item[1] for item in borderline_items]
        llm_results = llm_classify_filler(borderline_texts)
        for (i, _), is_filler in zip(borderline_items, llm_results):
            if is_filler:
                all_rows[i]["_status"] = "filtered"
                llm_filtered_count += 1
            else:
                all_rows[i]["_status"] = "keep"

    # Collect kept rows
    for row in all_rows:
        if row.get("_status") == "keep":
            lines.append({"speaker": row["speaker"], "text": row["text"]})

    if not lines:
        return {"text": raw_text, "participants": [], "turn_count": 0, "filtered_count": filtered_count, "llm_filtered_count": llm_filtered_count}

    # Merge consecutive turns by same speaker
    if merge_speaker_turns:
        merged = []
        current_speaker = None
        current_texts = []

        for line in lines:
            if line["speaker"] == current_speaker:
                current_texts.append(line["text"])
            else:
                if current_speaker and current_texts:
                    merged.append({
                        "speaker": current_speaker,
                        "text": " ".join(current_texts)
                    })
                current_speaker = line["speaker"]
                current_texts = [line["text"]]

        # Don't forget last turn
        if current_speaker and current_texts:
            merged.append({
                "speaker": current_speaker,
                "text": " ".join(current_texts)
            })

        lines = merged

    # Format as clean text with speaker attribution
    formatted_lines = []
    for line in lines:
        formatted_lines.append(f"[{line['speaker']}] {line['text']}")

    return {
        "text": "\n\n".join(formatted_lines),
        "participants": sorted(list(participants)),
        "turn_count": len(lines),
        "filtered_count": filtered_count,
        "llm_filtered_count": llm_filtered_count
    }


def chunk_transcript(text: str, target_chunk_size: int = 1000) -> list[dict]:
    """Chunk a preprocessed transcript by speaker turns.

    Groups speaker turns into chunks of approximately target_chunk_size characters,
    never splitting mid-turn. Extracts speaker from [Name] prefix.

    Args:
        text: Preprocessed transcript (output of preprocess_dialpad_transcript)
        target_chunk_size: Target chunk size in characters

    Returns:
        List of dicts: [{"speaker": "Name or None", "speakers": ["all", "speakers"], "text": "content"}, ...]
    """
    # Split on double newlines (speaker turn boundaries)
    turns = [t.strip() for t in text.split("\n\n") if t.strip()]

    if not turns:
        if text.strip():
            return [{"speaker": None, "speakers": [], "text": text.strip()}]
        return []

    # Parse each turn to extract speaker
    def parse_turn(turn: str) -> dict:
        match = re.match(r'^\[([^\]]+)\]\s*(.*)$', turn, re.DOTALL)
        if match:
            return {"speaker": match.group(1), "text": match.group(2).strip()}
        return {"speaker": None, "text": turn}

    parsed_turns = [parse_turn(t) for t in turns]

    chunks = []
    current_chunk_turns = []
    current_size = 0

    for turn in parsed_turns:
        turn_size = len(turn["text"])

        # If adding this turn exceeds target and we have content, flush
        if current_size + turn_size > target_chunk_size and current_chunk_turns:
            # Combine turns into one chunk
            speakers = [t["speaker"] for t in current_chunk_turns if t["speaker"]]
            combined_text = "\n\n".join([t["text"] for t in current_chunk_turns])
            chunks.append({
                "speaker": speakers[0] if speakers else None,  # Primary speaker
                "speakers": list(dict.fromkeys(speakers)),  # Unique speakers, preserving order
                "text": combined_text
            })
            current_chunk_turns = []
            current_size = 0

        current_chunk_turns.append(turn)
        current_size += turn_size + 2  # +2 for \n\n

    # Flush remaining
    if current_chunk_turns:
        speakers = [t["speaker"] for t in current_chunk_turns if t["speaker"]]
        combined_text = "\n\n".join([t["text"] for t in current_chunk_turns])
        chunks.append({
            "speaker": speakers[0] if speakers else None,
            "speakers": list(dict.fromkeys(speakers)),
            "text": combined_text
        })

    return chunks


# --- Stakeholders ---

def get_stakeholder(name: str) -> Optional[dict]:
    """Get stakeholder by name."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM stakeholders WHERE name = %s", (name,))
            return cur.fetchone()


def list_stakeholders(type_filter: str = None) -> list[dict]:
    """List all stakeholders, optionally filtered by type."""
    with get_db() as conn:
        with conn.cursor() as cur:
            if type_filter:
                cur.execute("SELECT * FROM stakeholders WHERE type = %s ORDER BY name", (type_filter,))
            else:
                cur.execute("SELECT * FROM stakeholders ORDER BY name")
            return cur.fetchall()


def create_stakeholder(name: str, type: str, organization: str = None, notes: str = None) -> int:
    """Create stakeholder, return ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO stakeholders (name, type, organization, notes)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (name, type, organization, notes)
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_or_create_stakeholder(name: str, type: str, organization: str = None) -> int:
    """Get existing stakeholder ID or create new one."""
    existing = get_stakeholder(name)
    if existing:
        return existing["id"]
    return create_stakeholder(name, type, organization)


# --- Projects ---

def get_project(name: str) -> Optional[dict]:
    """Get project by name."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM projects WHERE name = %s", (name,))
            return cur.fetchone()


def list_projects() -> list[dict]:
    """List all projects."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM projects ORDER BY name")
            return cur.fetchall()


def get_project_docs(project_name: str) -> dict:
    """Load project documentation files from {repo_path}/project/.

    repo_path is now the docs path (e.g., ~/repo_docs/itherapeutics),
    not the code repo path.
    """
    project = get_project(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found"}

    docs_root = Path(project["repo_path"])
    project_docs_dir = docs_root / "project"
    docs = {"project_name": project_name, "docs_path": str(docs_root)}

    # Standard doc files
    doc_files = {
        "context": "PROJECT_CONTEXT.md",
        "prd": "PRD.md",
        "db": "PROJECT_DB.md"
    }

    for doc_type, filename in doc_files.items():
        path = project_docs_dir / filename
        if path.exists():
            docs[doc_type] = path.read_text()
        else:
            docs[doc_type] = None  # File not found

    return docs


# --- Calls ---

def get_call_by_source_file(source_file: str) -> Optional[dict]:
    """Check if a call with this source file already exists.

    Returns the call record if found, None otherwise.
    Use to prevent duplicate ingestion.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.*, s.name as stakeholder_name, p.name as project_name,
                          (SELECT count(*) FROM chunks WHERE call_id = c.id) as chunk_count
                   FROM calls c
                   JOIN stakeholders s ON c.stakeholder_id = s.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.source_file = %s""",
                (source_file,)
            )
            return cur.fetchone()


def delete_call(call_id: int) -> dict:
    """Delete a call and all its chunks (for re-ingestion).

    Returns info about what was deleted.
    Chunks are deleted automatically via ON DELETE CASCADE.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            # Get info before deletion
            cur.execute(
                """SELECT c.id, c.call_date, c.source_file, s.name as stakeholder_name,
                          (SELECT count(*) FROM chunks WHERE call_id = c.id) as chunk_count
                   FROM calls c
                   JOIN stakeholders s ON c.stakeholder_id = s.id
                   WHERE c.id = %s""",
                (call_id,)
            )
            call_info = cur.fetchone()
            if not call_info:
                return {"error": f"Call {call_id} not found"}

            # Delete (chunks cascade)
            cur.execute("DELETE FROM calls WHERE id = %s", (call_id,))
            conn.commit()

            return {
                "deleted_call_id": call_info["id"],
                "call_date": call_info["call_date"],
                "stakeholder": call_info["stakeholder_name"],
                "chunks_deleted": call_info["chunk_count"],
                "source_file": call_info["source_file"]
            }


def create_call(
    call_date: date,
    participants: list[str],
    stakeholder_id: int,
    source_type: str,
    source_file: str = None,
    summary: str = None,
    project_id: int = None,
    user_notes: str = None
) -> int:
    """Create a call record, return ID.

    user_notes: Optional personal observations/thoughts about the call.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO calls (call_date, participants, stakeholder_id, source_type, source_file, summary, project_id, user_notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (call_date, participants, stakeholder_id, source_type, source_file, summary, project_id, user_notes)
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_calls_for_stakeholder(stakeholder_name: str) -> list[dict]:
    """Get all calls for a stakeholder."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.*, s.name as stakeholder_name, p.name as project_name
                   FROM calls c
                   JOIN stakeholders s ON c.stakeholder_id = s.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE s.name = %s
                   ORDER BY c.call_date DESC""",
                (stakeholder_name,)
            )
            return cur.fetchall()


def update_call_summary(call_id: int, summary: str) -> bool:
    """Update the summary field for a call (after HITL review)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE calls SET summary = %s WHERE id = %s",
                (summary, call_id)
            )
            conn.commit()
            return cur.rowcount > 0


# --- Chunks ---

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
                    """INSERT INTO chunks (call_id, chunk_idx, text, speaker, embedding)
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
                "SELECT id, chunk_idx, speaker, text FROM chunks WHERE call_id = %s ORDER BY chunk_idx",
                (call_id,)
            )
            return cur.fetchall()


# --- Batch Summaries ---

SUMMARY_MODEL = "qwen3-vl-8b-instruct-mlx"
BATCH_SIZE = 10  # chunks per batch (~5 min of conversation)


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


# --- Search ---

# Default lookback window for recency-weighted search
DEFAULT_DAYS_BACK = 21
DECAY_RATE = 0.95  # Per-day decay factor


def semantic_search(
    query: str,
    stakeholder_name: str = None,
    project_name: str = None,
    limit: int = 10,
    days_back: int = None,
    decay_rate: float = DECAY_RATE
) -> list[dict]:
    """Semantic search with time-decay scoring.

    Prioritizes recent content. Within the lookback window, results are
    scored as: relevance_score = (1 - distance) * (decay_rate ^ days_old)

    Args:
        query: Search query
        stakeholder_name: Optional filter
        project_name: Optional filter
        limit: Max results
        days_back: Lookback window (None = no limit, use DEFAULT_DAYS_BACK for recency)
        decay_rate: Per-day decay factor (0.95 = 22% penalty at 30 days)

    Returns:
        List of chunks with 'distance', 'days_old', and 'recency_score' fields.
        Empty list if no results in window (caller should ask user to expand).
    """
    query_embedding = get_embedding(query)

    with get_db() as conn:
        with conn.cursor() as cur:
            where_clauses = []
            filter_params = []

            if stakeholder_name:
                where_clauses.append("stakeholder_name = %s")
                filter_params.append(stakeholder_name)

            if project_name:
                where_clauses.append("project_name = %s")
                filter_params.append(project_name)

            if days_back is not None:
                where_clauses.append("call_date >= CURRENT_DATE - %s")
                filter_params.append(days_back)

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)

            # Build params: embedding, decay_rate, filters, limit
            params = [query_embedding, decay_rate] + filter_params + [limit]

            # Time-decay scoring: recency_score = (1 - distance) * (decay_rate ^ days_old)
            cur.execute(
                f"""WITH scored AS (
                        SELECT
                            id, chunk_idx, text, speaker,
                            stakeholder_name, project_name, call_date, summary,
                            embedding <=> %s::vector AS distance,
                            (CURRENT_DATE - call_date) AS days_old,
                            (1 - (embedding <=> %s::vector)) * POWER(%s, CURRENT_DATE - call_date) AS recency_score
                        FROM chunks_with_context
                        {where_sql}
                    )
                    SELECT * FROM scored
                    ORDER BY recency_score DESC
                    LIMIT %s""",
                [query_embedding, query_embedding, decay_rate] + filter_params + [limit]
            )
            return cur.fetchall()


def semantic_search_with_fallback(
    query: str,
    stakeholder_name: str = None,
    project_name: str = None,
    limit: int = 10
) -> dict:
    """Search with 21-day window, return flag if no results.

    Returns:
        {
            "results": [...],
            "days_back": 21,
            "needs_expansion": True/False
        }
    """
    results = semantic_search(
        query,
        stakeholder_name=stakeholder_name,
        project_name=project_name,
        limit=limit,
        days_back=DEFAULT_DAYS_BACK
    )

    return {
        "results": results,
        "days_back": DEFAULT_DAYS_BACK,
        "needs_expansion": len(results) == 0
    }


def get_stakeholder_context(stakeholder_name: str, query: str = None, limit: int = 20) -> dict:
    """Get comprehensive context about a stakeholder."""
    stakeholder = get_stakeholder(stakeholder_name)
    if not stakeholder:
        return {"error": f"Stakeholder '{stakeholder_name}' not found"}

    calls = get_calls_for_stakeholder(stakeholder_name)

    result = {
        "stakeholder": stakeholder,
        "calls": calls,
        "all_chunks_count": 0
    }

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) as cnt FROM chunks_with_context WHERE stakeholder_name = %s",
                (stakeholder_name,)
            )
            result["all_chunks_count"] = cur.fetchone()["cnt"]

    if query:
        result["relevant_chunks"] = semantic_search(query, stakeholder_name=stakeholder_name, limit=limit)

    return result
