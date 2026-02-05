"""Harvest decisions, open questions, and action items from call transcripts."""

import json
from openai import OpenAI
from .db import get_db
from .config import LM_STUDIO_URL, SUMMARY_MODEL, BATCH_SIZE
from .crud.chunks import get_call_chunks, get_call_batch_summaries, generate_call_batch_summaries
from .crud.decisions import insert_candidate_decisions, clear_candidate_decisions
from .crud.questions import insert_candidate_questions, clear_candidate_questions
from .crud.actions import insert_candidate_actions, clear_candidate_actions
from .crud.contacts import get_call_contacts
from .crud.projects import get_project_docs


def _get_stakeholder_types(project_name: str) -> list[str]:
    """Extract stakeholder types from PROJECT.md for use in harvest prompts.

    Looks for a markdown table with a 'Stakeholder' column header.
    Returns list of type names, or empty list if not found.
    """
    if not project_name:
        return []
    docs = get_project_docs(project_name)
    project_md = docs.get("project")
    if not project_md:
        return []

    types = []
    in_table = False
    for line in project_md.splitlines():
        if "Stakeholder" in line and "|" in line:
            in_table = True
            continue
        if in_table:
            if line.strip().startswith("|---"):
                continue
            if not line.strip().startswith("|"):
                break
            # Extract first cell: | **Patient** | ... |
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 2:
                name = cells[1].strip("* ")
                if name:
                    types.append(name)
    return types


def harvest_from_summaries(
    summaries_text: str,
    call_context: str = "",
    stakeholder_types: list[str] = None,
) -> dict:
    """Extract decisions, open questions, and action items from batch summaries using LLM.

    Args:
        summaries_text: Concatenated batch summaries for the call
        call_context: Brief context (client, project, date, user notes)
        stakeholder_types: Valid stakeholder types to assign to each item

    Returns:
        {"decisions": [...], "open_questions": [...], "action_items": [...]}
    """
    context_line = f"\nCall context: {call_context}\n" if call_context else ""

    stakeholder_instruction = ""
    if stakeholder_types:
        types_str = ", ".join(stakeholder_types)
        stakeholder_instruction = f"""
4. **STAKEHOLDER TYPE** — For each item, assign the most relevant stakeholder type from this list: {types_str}
   - Choose the stakeholder type that the item most directly concerns
   - If an item concerns multiple types, pick the primary one
   - If none apply, use null
"""

    prompt = f"""Analyze these call summaries and extract three types of structured knowledge:

1. **DECISIONS** — Things the group agreed on, confirmed, or decided. Include:
   - Confirmed approaches or strategies
   - Requirements that were validated
   - Items that got explicit agreement from the group

2. **OPEN QUESTIONS** — Things raised but NOT resolved. Include:
   - Questions asked but not answered
   - Items marked for follow-up
   - Disagreements not yet settled
   - Information someone needs to provide later

3. **ACTION ITEMS** — Specific tasks someone needs to do. Include:
   - Tasks explicitly assigned to someone
   - Follow-ups mentioned ("we need to...", "someone should...")
   - Items that require action before the next meeting
{stakeholder_instruction}{context_line}
CALL SUMMARIES:
{summaries_text}

Return a JSON object with three arrays:

{{
  "decisions": [
    {{
      "topic": "short topic label (e.g., 'CPM inclusion', 'escalation model')",
      "summary": "what was decided, in 1-2 sentences",
      "status": "confirmed" or "open" (use confirmed only if explicitly agreed by group),
      "decided_by": ["person1", "person2"],
      "stakeholder_type": "which stakeholder type this most concerns (or null)"
    }}
  ],
  "open_questions": [
    {{
      "topic": "short topic label",
      "question": "the specific question that remains unanswered",
      "context": "why this matters, in 1 sentence",
      "owner": "who needs to answer this (or null if unclear)",
      "stakeholder_type": "which stakeholder type this most concerns (or null)"
    }}
  ],
  "action_items": [
    {{
      "title": "short task title (imperative, e.g., 'Load manufacturer manuals')",
      "description": "what needs to be done, in 1-2 sentences",
      "assigned_to": "who should do this (or null if unclear)",
      "stakeholder_type": "which stakeholder type this most concerns (or null)"
    }}
  ]
}}

Rules:
- Extract ONLY what is explicitly stated, do not infer or speculate
- A decision must have clear agreement, not just one person's opinion
- An open question must be genuinely unresolved in the conversation
- An action item must be a concrete task, not a vague wish
- Keep topics/titles short (2-5 words)
- If nothing fits a category, return an empty array for it

Return ONLY valid JSON, no other text.

JSON:"""

    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    response = client.chat.completions.create(
        model=SUMMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3500,
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
            return {"decisions": [], "open_questions": [], "action_items": []}

        # Validate structure
        valid_types = set(stakeholder_types) if stakeholder_types else set()

        def _validate_type(raw_type):
            if not raw_type or not valid_types:
                return raw_type
            # Accept exact match or case-insensitive match
            if raw_type in valid_types:
                return raw_type
            for vt in valid_types:
                if raw_type.lower() == vt.lower():
                    return vt
            return raw_type

        decisions = []
        for d in result.get("decisions", []):
            if isinstance(d, dict) and d.get("topic") and d.get("summary"):
                decisions.append({
                    "topic": d["topic"],
                    "summary": d["summary"],
                    "status": d.get("status", "open"),
                    "decided_by": d.get("decided_by", []),
                    "stakeholder_type": _validate_type(d.get("stakeholder_type")),
                })

        questions = []
        for q in result.get("open_questions", []):
            if isinstance(q, dict) and q.get("topic") and q.get("question"):
                questions.append({
                    "topic": q["topic"],
                    "question": q["question"],
                    "context": q.get("context"),
                    "owner": q.get("owner"),
                    "stakeholder_type": _validate_type(q.get("stakeholder_type")),
                })

        actions = []
        for a in result.get("action_items", []):
            if isinstance(a, dict) and a.get("title"):
                actions.append({
                    "title": a["title"],
                    "description": a.get("description"),
                    "assigned_to": a.get("assigned_to"),
                    "stakeholder_type": _validate_type(a.get("stakeholder_type")),
                })

        return {"decisions": decisions, "open_questions": questions, "action_items": actions}

    except json.JSONDecodeError:
        return {"decisions": [], "open_questions": [], "action_items": []}


def _build_contact_map(call_id: int) -> dict[str, int]:
    """Build a name→contact_id lookup from call contacts.

    Returns lowercase name → contact_id mapping.
    """
    contacts = get_call_contacts(call_id)
    name_map = {}
    for c in contacts:
        name = c.get("name") or ""
        if name:
            name_map[name.lower().strip()] = c["id"]
            # Also map first name for partial matches
            first = name.split()[0].lower().strip()
            if first not in name_map:
                name_map[first] = c["id"]
    return name_map


def _resolve_contact(name: str | None, name_map: dict[str, int]) -> int | None:
    """Resolve a person name to a contact_id using the lookup map."""
    if not name:
        return None
    key = name.lower().strip()
    # Try exact match first
    if key in name_map:
        return name_map[key]
    # Try first name
    first = key.split()[0]
    return name_map.get(first)


def _resolve_harvest_contacts(result: dict, name_map: dict[str, int]) -> dict:
    """Resolve all person names in harvest result to contact_ids."""
    # Decisions: decided_by names → contact_ids
    for d in result["decisions"]:
        contact_ids = []
        for name in d.pop("decided_by", []):
            cid = _resolve_contact(name, name_map)
            if cid is not None:
                contact_ids.append(cid)
        d["contact_ids"] = contact_ids

    # Questions: owner name → owner_contact_id
    for q in result["open_questions"]:
        owner_name = q.pop("owner", None)
        q["owner_contact_id"] = _resolve_contact(owner_name, name_map)

    # Actions: assigned_to name → assigned_contact_id
    for a in result["action_items"]:
        assigned_name = a.pop("assigned_to", None)
        a["assigned_contact_id"] = _resolve_contact(assigned_name, name_map)

    return result


def harvest_call(
    call_id: int,
    project_id: int,
    show_progress: bool = True,
    clear_existing: bool = True,
) -> dict:
    """Harvest decisions, open questions, and action items from a call.

    Uses batch summaries if available. Falls back to call summary
    for calls without transcripts (e.g., cell phone calls with manual recaps).

    Args:
        call_id: The call to harvest from
        project_id: Project to associate findings with
        show_progress: Print progress
        clear_existing: Clear existing candidates before extraction

    Returns:
        {"call_id", "decisions_extracted", "questions_extracted", "actions_extracted"}
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
        a_cleared = clear_candidate_actions(call_id)
        if show_progress and (d_cleared + q_cleared + a_cleared) > 0:
            print(f"  Cleared {d_cleared} decisions, {q_cleared} questions, {a_cleared} actions")

    # Build summaries text from batch summaries (skip if fallback already set it)
    if summaries:
        summaries_text = "\n\n".join([
            f"[Segment {s['batch_idx']+1}] {s['summary']}"
            for s in summaries
        ])
        if show_progress:
            print(f"  Harvesting from {len(summaries)} batch summaries...")

    # Get call context (includes user_notes)
    call_context = _get_call_context(call_id)

    # Load stakeholder types from PROJECT.md
    project_name = _get_project_name(project_id)
    stakeholder_types = _get_stakeholder_types(project_name)
    if show_progress and stakeholder_types:
        print(f"  Stakeholder types: {', '.join(stakeholder_types)}")

    # Extract
    result = harvest_from_summaries(summaries_text, call_context, stakeholder_types=stakeholder_types)

    # Deduplicate
    result["decisions"] = deduplicate_harvest(result["decisions"], key="topic")
    result["open_questions"] = deduplicate_harvest(result["open_questions"], key="topic")
    result["action_items"] = deduplicate_harvest(result["action_items"], key="title")

    # Resolve contact names → IDs
    name_map = _build_contact_map(call_id)
    result = _resolve_harvest_contacts(result, name_map)

    # Insert candidates
    d_count = insert_candidate_decisions(project_id, call_id, result["decisions"])
    q_count = insert_candidate_questions(project_id, call_id, result["open_questions"])
    a_count = insert_candidate_actions(project_id, call_id, result["action_items"])

    return {
        "call_id": call_id,
        "decisions_extracted": d_count,
        "questions_extracted": q_count,
        "actions_extracted": a_count,
    }


def deduplicate_harvest(items: list[dict], key: str = "topic") -> list[dict]:
    """Remove near-duplicate items based on topic/title similarity."""
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


def _get_project_name(project_id: int) -> str | None:
    """Get project name by ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
            row = cur.fetchone()
            return row["name"] if row else None


def _get_call_context(call_id: int) -> str:
    """Get brief context about a call for harvest prompts, including user notes."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.call_date, c.user_notes, o.name as org, p.name as project
                   FROM calls c
                   JOIN orgs o ON c.org_id = o.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.id = %s""",
                (call_id,),
            )
            row = cur.fetchone()
            if row:
                parts = [f"Call with {row['org']}"]
                if row.get("project"):
                    parts.append(f"re: {row['project']}")
                # Get contacts via call_contacts junction
                cur.execute(
                    """SELECT ct.name FROM contacts ct
                       JOIN call_contacts cc ON cc.contact_id = ct.id
                       WHERE cc.call_id = %s ORDER BY ct.name""",
                    (call_id,),
                )
                contact_names = [r['name'] for r in cur.fetchall()]
                if contact_names:
                    parts.append(f"participants: {', '.join(contact_names)}")
                parts.append(f"on {row['call_date']}")

                context = " ".join(parts)

                # Append user notes if present
                if row.get("user_notes"):
                    context += f"\n\nUser notes: {row['user_notes']}"

                return context
            return ""
