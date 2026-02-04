"""Harvest decisions and open questions from call transcripts."""

import json
from openai import OpenAI
from .db import get_db
from .config import LM_STUDIO_URL, SUMMARY_MODEL, BATCH_SIZE
from .crud.chunks import get_call_chunks, get_call_batch_summaries, generate_call_batch_summaries
from .crud.decisions import insert_candidate_decisions, clear_candidate_decisions
from .crud.open_questions import insert_candidate_questions, clear_candidate_questions


def harvest_from_summaries(summaries_text: str, call_context: str = "") -> dict:
    """Extract decisions and open questions from batch summaries using LLM.

    Args:
        summaries_text: Concatenated batch summaries for the call
        call_context: Brief context (client, project, date)

    Returns:
        {"decisions": [...], "open_questions": [...]}
    """
    context_line = f"\nCall context: {call_context}\n" if call_context else ""

    prompt = f"""Analyze these call summaries and extract two types of structured knowledge:

1. **DECISIONS** — Things the group agreed on, confirmed, or decided. Include:
   - Confirmed approaches or strategies
   - Requirements that were validated
   - Items that got explicit agreement from the group

2. **OPEN QUESTIONS** — Things raised but NOT resolved. Include:
   - Questions asked but not answered
   - Items marked for follow-up
   - Disagreements not yet settled
   - Information someone needs to provide later
{context_line}
CALL SUMMARIES:
{summaries_text}

Return a JSON object with two arrays:

{{
  "decisions": [
    {{
      "topic": "short topic label (e.g., 'CPM inclusion', 'escalation model')",
      "summary": "what was decided, in 1-2 sentences",
      "status": "confirmed" or "open" (use confirmed only if explicitly agreed by group),
      "decided_by": ["person1", "person2"]
    }}
  ],
  "open_questions": [
    {{
      "topic": "short topic label",
      "question": "the specific question that remains unanswered",
      "context": "why this matters, in 1 sentence",
      "owner": "who needs to answer this (or null if unclear)"
    }}
  ]
}}

Rules:
- Extract ONLY what is explicitly stated, do not infer or speculate
- A decision must have clear agreement, not just one person's opinion
- An open question must be genuinely unresolved in the conversation
- Keep topics short (2-4 words)
- If nothing fits a category, return an empty array for it

Return ONLY valid JSON, no other text.

JSON:"""

    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    response = client.chat.completions.create(
        model=SUMMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.3,
    )

    content = response.choices[0].message.content.strip()

    try:
        # Handle markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        result = json.loads(content)
        if not isinstance(result, dict):
            return {"decisions": [], "open_questions": []}

        # Validate structure
        decisions = []
        for d in result.get("decisions", []):
            if isinstance(d, dict) and d.get("topic") and d.get("summary"):
                decisions.append({
                    "topic": d["topic"],
                    "summary": d["summary"],
                    "status": d.get("status", "open"),
                    "decided_by": d.get("decided_by", []),
                })

        questions = []
        for q in result.get("open_questions", []):
            if isinstance(q, dict) and q.get("topic") and q.get("question"):
                questions.append({
                    "topic": q["topic"],
                    "question": q["question"],
                    "context": q.get("context"),
                    "owner": q.get("owner"),
                })

        return {"decisions": decisions, "open_questions": questions}

    except json.JSONDecodeError:
        return {"decisions": [], "open_questions": []}


def harvest_call(
    call_id: int,
    project_id: int,
    show_progress: bool = True,
    clear_existing: bool = True,
) -> dict:
    """Harvest decisions and open questions from a call.

    Uses batch summaries if available. Falls back to call summary
    for calls without transcripts (e.g., cell phone calls with manual recaps).

    Args:
        call_id: The call to harvest from
        project_id: Project to associate findings with
        show_progress: Print progress
        clear_existing: Clear existing candidates before extraction

    Returns:
        {"call_id", "decisions_extracted", "questions_extracted"}
    """
    # Ensure batch summaries exist
    summaries = get_call_batch_summaries(call_id)
    if not summaries:
        if show_progress:
            print("  No batch summaries found, generating...")
        generate_call_batch_summaries(call_id, show_progress=show_progress)
        summaries = get_call_batch_summaries(call_id)

    # Fallback: use call summary for calls without chunks (cell phone, manual recap)
    if not summaries:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT summary FROM calls WHERE id = %s", (call_id,))
                row = cur.fetchone()
        if row and row["summary"]:
            summaries_text = row["summary"]
            if show_progress:
                print("  Using call summary (no transcript chunks available)")
        else:
            return {"error": f"No summaries or recap found for call {call_id}"}

    # Clear existing candidates
    if clear_existing:
        d_cleared = clear_candidate_decisions(call_id)
        q_cleared = clear_candidate_questions(call_id)
        if show_progress and (d_cleared + q_cleared) > 0:
            print(f"  Cleared {d_cleared} decisions, {q_cleared} questions")

    # Build summaries text from batch summaries (skip if fallback already set it)
    if summaries:
        summaries_text = "\n\n".join([
            f"[Segment {s['batch_idx']+1}] {s['summary']}"
            for s in summaries
        ])
        if show_progress:
            print(f"  Harvesting from {len(summaries)} batch summaries...")

    # Get call context
    call_context = _get_call_context(call_id)

    # Extract
    result = harvest_from_summaries(summaries_text, call_context)

    # Deduplicate
    result["decisions"] = deduplicate_harvest(result["decisions"], key="topic")
    result["open_questions"] = deduplicate_harvest(result["open_questions"], key="topic")

    # Insert candidates
    d_count = insert_candidate_decisions(project_id, call_id, result["decisions"])
    q_count = insert_candidate_questions(project_id, call_id, result["open_questions"])

    return {
        "call_id": call_id,
        "decisions_extracted": d_count,
        "questions_extracted": q_count,
    }


def deduplicate_harvest(items: list[dict], key: str = "topic") -> list[dict]:
    """Remove near-duplicate items based on topic similarity."""
    if len(items) <= 1:
        return items

    seen = []
    result = []

    for item in items:
        text = item[key].lower().strip()
        is_dup = False
        for existing in seen:
            if text in existing or existing in text:
                is_dup = True
                break
        if not is_dup:
            seen.append(text)
            result.append(item)

    return result


def _get_call_context(call_id: int) -> str:
    """Get brief context about a call for harvest prompts."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.call_date, s.name as client, p.name as project
                   FROM calls c
                   JOIN clients s ON c.client_id = s.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.id = %s""",
                (call_id,),
            )
            row = cur.fetchone()
            if row:
                parts = [f"Call with {row['client']}"]
                if row.get("project"):
                    parts.append(f"re: {row['project']}")
                # Get participants from participants table
                cur.execute(
                    "SELECT name FROM participants WHERE call_id = %s ORDER BY name",
                    (call_id,),
                )
                participant_names = [r['name'] for r in cur.fetchall()]
                if participant_names:
                    parts.append(f"participants: {', '.join(participant_names)}")
                parts.append(f"on {row['call_date']}")
                return " ".join(parts)
            return ""
