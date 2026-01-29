"""Quote extraction logic."""

import json
from openai import OpenAI
from .db import get_db
from .config import LM_STUDIO_URL, SUMMARY_MODEL, BATCH_SIZE
from .crud.chunks import get_call_chunks
from .crud.quotes import insert_candidate_quotes, clear_candidate_quotes

QUOTES_PER_BATCH = 5


def extract_quotes_from_batch(chunks: list[dict], call_context: str = "") -> list[dict]:
    """Extract 2-5 notable quotes from a batch of chunks using local LLM.

    Args:
        chunks: List of chunk dicts with 'text' and optionally 'speaker'
        call_context: Optional context about the call (stakeholder, topic)

    Returns:
        List of quote dicts with keys: quote_text, speaker, context, category
    """
    combined = "\n\n".join([
        f"{c.get('speaker', 'Unknown')}: {c['text']}" if c.get('speaker') else c['text']
        for c in chunks
    ])

    context_line = f"\nCall context: {call_context}\n" if call_context else ""

    prompt = f"""Analyze this business call transcript segment and extract 2-5 of the most notable quotes.

Select quotes that are:
1. **Insightful** - Reveals stakeholder thinking, priorities, or motivations
2. **Memorable** - Phrased distinctively or powerfully
3. **Actionable** - Contains a commitment, decision, or action item
4. **Revealing** - Exposes objections, concerns, or underlying issues
5. **Strategic** - Provides leverage or context for future conversations
{context_line}
TRANSCRIPT:
{combined}

Return a JSON array of quotes. Each quote should have:
- "quote_text": The exact quote (preserve original wording)
- "speaker": Who said it (if identifiable)
- "context": Brief 1-sentence context of when/why this was said
- "category": One of: insight, commitment, objection, decision, priority, concern

Return ONLY valid JSON array, no other text. If no notable quotes, return [].

JSON:"""

    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    response = client.chat.completions.create(
        model=SUMMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
        temperature=0.3
    )

    content = response.choices[0].message.content.strip()

    # Parse JSON response
    try:
        # Handle markdown code blocks if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        quotes = json.loads(content)
        if not isinstance(quotes, list):
            return []

        # Validate and clean quotes
        valid_quotes = []
        for q in quotes:
            if isinstance(q, dict) and q.get("quote_text"):
                valid_quotes.append({
                    "quote_text": q["quote_text"],
                    "speaker": q.get("speaker"),
                    "context": q.get("context"),
                    "category": q.get("category"),
                })
        return valid_quotes

    except json.JSONDecodeError:
        return []


def extract_call_quotes(
    call_id: int,
    batch_size: int = BATCH_SIZE,
    show_progress: bool = True,
    clear_existing: bool = True
) -> dict:
    """Extract quotes from all chunks in a call, processing in batches.

    Args:
        call_id: The call to extract quotes from
        batch_size: Chunks per batch (default 10)
        show_progress: Print progress
        clear_existing: Clear existing candidates before extraction

    Returns:
        {"call_id": int, "batches_processed": int, "quotes_extracted": int}
    """
    chunks = get_call_chunks(call_id)
    if not chunks:
        return {"error": f"No chunks found for call {call_id}"}

    # Get call context for better extraction
    call_context = _get_call_context(call_id)

    if clear_existing:
        cleared = clear_candidate_quotes(call_id)
        if show_progress and cleared > 0:
            print(f"  Cleared {cleared} existing candidate quotes")

    all_quotes = []
    batches_processed = 0

    for batch_idx, i in enumerate(range(0, len(chunks), batch_size)):
        batch_chunks = chunks[i:i + batch_size]
        start_idx = batch_chunks[0]["chunk_idx"]
        end_idx = batch_chunks[-1]["chunk_idx"]

        if show_progress:
            print(f"  Processing batch {batch_idx} (chunks {start_idx}-{end_idx})...")

        quotes = extract_quotes_from_batch(batch_chunks, call_context)

        # Add chunk reference to quotes
        for q in quotes:
            q["chunk_id"] = batch_chunks[0]["id"]  # Reference first chunk in batch

        all_quotes.extend(quotes)
        batches_processed += 1

    # Deduplicate before inserting
    deduped = deduplicate_quotes(all_quotes)

    # Insert candidates
    inserted = insert_candidate_quotes(call_id, deduped)

    return {
        "call_id": call_id,
        "batches_processed": batches_processed,
        "quotes_extracted": inserted,
        "chunks_processed": len(chunks)
    }


def deduplicate_quotes(quotes: list[dict]) -> list[dict]:
    """Remove near-duplicate quotes based on text similarity.

    Uses simple approach: normalize and check for substring containment.
    """
    if len(quotes) <= 1:
        return quotes

    seen = []
    result = []

    for q in quotes:
        text = q["quote_text"].lower().strip()
        # Check if this quote is contained in or contains an existing quote
        is_dup = False
        for existing in seen:
            if text in existing or existing in text:
                is_dup = True
                break
        if not is_dup:
            seen.append(text)
            result.append(q)

    return result


def _get_call_context(call_id: int) -> str:
    """Get brief context about a call for quote extraction."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.call_date, s.name as stakeholder, p.name as project
                   FROM calls c
                   JOIN stakeholders s ON c.stakeholder_id = s.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.id = %s""",
                (call_id,)
            )
            row = cur.fetchone()
            if row:
                parts = [f"Call with {row['stakeholder']}"]
                if row.get("project"):
                    parts.append(f"re: {row['project']}")
                parts.append(f"on {row['call_date']}")
                return " ".join(parts)
            return ""


def draft_letter(
    call_id: int,
    instructions: str = None,
    include_quotes: bool = True,
    sender_name: str = "Chris"
) -> dict:
    """Generate a markdown letter/email based on a call.

    Args:
        call_id: The call to draft letter for
        instructions: Custom instructions (e.g., "close with PS about tomorrow's meeting")
        include_quotes: Include approved quotes at bottom
        sender_name: Name to sign letter with

    Returns:
        {"markdown": str, "recipient": str, "filename": str} or {"error": str}
    """
    from .crud.chunks import get_call_batch_summaries
    from .crud.quotes import get_approved_quotes

    # Get call metadata
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.call_date, c.summary, s.name as stakeholder,
                          s.organization, p.name as project
                   FROM calls c
                   JOIN stakeholders s ON c.stakeholder_id = s.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.id = %s""",
                (call_id,)
            )
            call = cur.fetchone()
            if not call:
                return {"error": f"Call {call_id} not found"}

    # Get batch summaries
    summaries = get_call_batch_summaries(call_id)
    summary_text = "\n".join([s["summary"] for s in summaries]) if summaries else ""

    # Get approved quotes
    quotes = get_approved_quotes(call_id) if include_quotes else []

    # Build quotes section
    quotes_section = ""
    if quotes:
        quotes_md = "\n\n".join([
            f'> "{q["quote_text"]}"\n> — {q["speaker"] or "Unknown"}'
            for q in quotes
        ])
        quotes_section = f"\n\n---\n\n**P.S. — Quotes from our call:**\n\n{quotes_md}"

    # Build custom instructions
    custom = ""
    if instructions:
        custom = f"\n\nADDITIONAL INSTRUCTIONS: {instructions}"

    # Get first name from stakeholder
    recipient_first = call["stakeholder"].split()[0]

    prompt = f"""Write a professional follow-up email/letter in Markdown format.

CONTEXT:
- Recipient: {call["stakeholder"]}
- Date of call: {call["call_date"]}
- Project: {call.get("project") or "General discussion"}

CALL SUMMARY:
{summary_text}
{custom}

REQUIREMENTS:
- Address to "{recipient_first},"
- Recap key points from the call concisely
- Use headers and bullet points for readability
- Professional but warm tone
- Sign off as "{sender_name}"
- Do NOT include quotes section - that will be added separately

OUTPUT only the markdown letter, nothing else:"""

    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    response = client.chat.completions.create(
        model=SUMMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
        temperature=0.4
    )

    letter = response.choices[0].message.content.strip()

    # Append quotes section
    letter += quotes_section

    # Generate filename
    date_str = str(call["call_date"]).replace("-", "")
    name_slug = call["stakeholder"].lower().replace(" ", "-")
    filename = f"letter-{name_slug}-{date_str}.md"

    return {
        "markdown": letter,
        "recipient": call["stakeholder"],
        "filename": filename,
        "call_date": str(call["call_date"])
    }
