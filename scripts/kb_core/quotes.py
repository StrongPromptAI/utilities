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
        call_context: Optional context about the call (client, topic)

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
1. **Insightful** - Reveals client thinking, priorities, or motivations
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


def rank_quotes(
    call_id: int,
    top_n: int = 10,
    show_progress: bool = True,
) -> list[dict]:
    """Rank candidate quotes using PROJECT.md context via LLM.

    Reads project documentation to understand goals, priorities, and current phase,
    then asks the LLM to select the top_n most strategically relevant quotes.

    Args:
        call_id: The call whose candidates to rank
        top_n: Number of top quotes to return
        show_progress: Print progress

    Returns:
        Ordered list of candidate quote dicts (best first), trimmed to top_n.
        Each dict includes the original DB fields (id, quote_text, speaker, etc.)
        plus an "llm_reason" field explaining why it was selected.
    """
    from .crud.quotes import get_candidate_quotes
    from .crud.projects import get_project_docs

    candidates = get_candidate_quotes(call_id)
    if not candidates:
        return []

    # If fewer candidates than top_n, just return them all
    if len(candidates) <= top_n:
        for c in candidates:
            c["llm_reason"] = "Included — fewer candidates than requested"
        return candidates

    # Load project docs + user notes for context
    project_context = _get_project_context(call_id)

    # Add user notes to ranking context
    user_notes = _get_user_notes(call_id)
    if user_notes:
        project_context += f"\n\nUSER NOTES (observations from the call owner — use to guide ranking):\n{user_notes}"

    if show_progress:
        print(f"  Ranking {len(candidates)} candidates with project context...")

    # Build numbered candidate list for LLM
    candidates_text = ""
    for idx, q in enumerate(candidates, 1):
        speaker = q.get("speaker") or "Unknown"
        category = q.get("category") or "uncategorized"
        candidates_text += f"\n[{idx}] ({category}) {speaker}: \"{q['quote_text']}\""
        if q.get("context"):
            candidates_text += f"\n    Context: {q['context']}"

    prompt = f"""You are a stakeholder intelligence analyst. Your job is to select the {top_n} most strategically valuable quotes from a business call.

PROJECT CONTEXT:
{project_context}

CANDIDATE QUOTES:
{candidates_text}

Select the {top_n} quotes that are most valuable given the project context. Prioritize quotes that:
1. Reveal stakeholder priorities, concerns, or decision criteria relevant to the project
2. Contain commitments, decisions, or action items that move the project forward
3. Expose objections or risks that need to be addressed
4. Provide leverage or talking points for future conversations
5. Capture the authentic voice/tone of the stakeholder relationship

Return a JSON array of objects, each with:
- "num": the candidate number (e.g. 1, 5, 12)
- "reason": one sentence explaining why this quote is strategically valuable

Order from most to least valuable. Return ONLY valid JSON array, no other text.

JSON:"""

    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    response = client.chat.completions.create(
        model=SUMMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
        temperature=0.3,
    )

    content = response.choices[0].message.content.strip()

    # Parse JSON response
    try:
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        rankings = json.loads(content)
        if not isinstance(rankings, list):
            if show_progress:
                print("  Warning: LLM returned non-list, falling back to all candidates")
            return candidates[:top_n]
    except json.JSONDecodeError:
        if show_progress:
            print("  Warning: Could not parse LLM ranking, falling back to all candidates")
        return candidates[:top_n]

    # Map rankings back to candidate dicts
    ranked = []
    for r in rankings:
        if not isinstance(r, dict) or "num" not in r:
            continue
        idx = r["num"] - 1  # 0-indexed
        if 0 <= idx < len(candidates):
            q = candidates[idx].copy()
            q["llm_reason"] = r.get("reason", "")
            ranked.append(q)

    if show_progress:
        print(f"  Selected {len(ranked)} top quotes from {len(candidates)} candidates")

    return ranked[:top_n]


def _get_project_context(call_id: int) -> str:
    """Load PROJECT.md content for the project linked to a call."""
    from .crud.projects import get_project_docs

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT p.name as project_name
                   FROM calls c
                   JOIN projects p ON c.project_id = p.id
                   WHERE c.id = %s""",
                (call_id,)
            )
            row = cur.fetchone()

    if not row or not row.get("project_name"):
        return "(No project linked to this call — rank based on general business value)"

    docs = get_project_docs(row["project_name"])
    if "error" in docs or not docs.get("project"):
        return f"Project: {row['project_name']}\n(PROJECT.md not found — rank based on general business value)"

    return f"Project: {row['project_name']}\n\n{docs['project']}"


def _get_user_notes(call_id: int) -> str | None:
    """Get user_notes for a call, or None."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_notes FROM calls WHERE id = %s", (call_id,))
            row = cur.fetchone()
            return row["user_notes"] if row and row.get("user_notes") else None


def _get_call_context(call_id: int) -> str:
    """Get brief context about a call for quote extraction."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.call_date, o.name as org, p.name as project
                   FROM calls c
                   JOIN orgs o ON c.org_id = o.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.id = %s""",
                (call_id,)
            )
            row = cur.fetchone()
            if row:
                parts = [f"Call with {row['org']}"]
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
                """SELECT c.call_date, c.summary, c.user_notes, o.name as org_name,
                          p.name as project
                   FROM calls c
                   JOIN orgs o ON c.org_id = o.id
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

    # Include user notes if present
    user_notes = ""
    if call.get("user_notes"):
        user_notes = f"\n\nMY NOTES (use to guide tone and emphasis):\n{call['user_notes']}"

    # Build quotes context for the LLM prompt
    quotes_context = ""
    if quotes:
        quotes_lines = "\n".join([
            f'- "{q["quote_text"]}" — {q.get("speaker") or "Unknown"}'
            for q in quotes
        ])
        quotes_context = f"\n\nKEY QUOTES (use these to match the vibe and priorities of the call):\n{quotes_lines}"

    # Build custom instructions
    custom = ""
    if instructions:
        custom = f"\n\nADDITIONAL INSTRUCTIONS: {instructions}"

    # Get first name from client
    recipient_first = call["org_name"].split()[0]

    prompt = f"""Write a professional follow-up email/letter in Markdown format.

CONTEXT:
- Recipient: {call["org_name"]}
- Date of call: {call["call_date"]}
- Project: {call.get("project") or "General discussion"}

CALL SUMMARY:
{summary_text}
{user_notes}
{quotes_context}
{custom}

REQUIREMENTS:
- Address to "{recipient_first},"
- Recap key points from the call concisely
- Use headers and bullet points for readability
- Professional but warm tone — let the quotes and notes guide your emphasis
- Sign off as "{sender_name}"
- Do NOT include a quotes section — quotes will be appended separately

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
    name_slug = call["org_name"].lower().replace(" ", "-")
    filename = f"letter-{name_slug}-{date_str}.md"

    return {
        "markdown": letter,
        "recipient": call["org_name"],
        "filename": filename,
        "call_date": str(call["call_date"])
    }
