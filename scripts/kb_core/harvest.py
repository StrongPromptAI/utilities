"""Harvest questions, decisions, and action items from call transcripts."""

import json
from openai import OpenAI
from .db import get_db
from .config import LM_STUDIO_URL, SUMMARY_MODEL, BATCH_SIZE, ensure_model
from .crud.calls import get_call_context
from .crud.chunks import get_call_chunks, get_call_batch_summaries, generate_call_batch_summaries
from .crud.questions import insert_candidate_questions, clear_candidate_questions, get_candidate_questions
from .crud.actions import insert_candidate_actions, clear_candidate_actions, get_candidate_actions
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
    """Extract questions/decisions and action items from batch summaries using LLM.

    Unified extraction: decisions are resolved questions (status=decided).

    Args:
        summaries_text: Concatenated batch summaries for the call
        call_context: Brief context (client, project, date, user notes)
        stakeholder_types: Valid stakeholder types to assign to each item

    Returns:
        {"questions": [...], "action_items": [...]}
    """
    context_line = f"\nCall context: {call_context}\n" if call_context else ""

    stakeholder_instruction = ""
    if stakeholder_types:
        types_str = ", ".join(stakeholder_types)
        stakeholder_instruction = f"""
3. **STAKEHOLDER TYPE** — For each item, assign the most relevant stakeholder type from this list: {types_str}
   - Choose the stakeholder type that the item most directly concerns
   - If an item concerns multiple types, pick the primary one
   - If none apply, use null
"""

    prompt = f"""Analyze these call summaries and extract two types of structured knowledge:

1. **QUESTIONS & DECISIONS** — Extract both questions raised AND decisions made.
   For each, indicate whether it was resolved (decided) or remains open.
   - **Decided**: Things the group agreed on, confirmed, or decided — explicit agreement from the group
   - **Open**: Questions asked but not answered, items marked for follow-up, disagreements not settled

2. **ACTION ITEMS** — Specific tasks someone needs to do. Include:
   - Tasks explicitly assigned to someone
   - Follow-ups mentioned ("we need to...", "someone should...")
   - Items that require action before the next meeting
{stakeholder_instruction}{context_line}
CALL SUMMARIES:
{summaries_text}

Return a JSON object with two arrays:

{{
  "questions": [
    {{
      "topic": "short topic label (e.g., 'CPM inclusion', 'escalation model')",
      "question": "the question or decision framed as a question",
      "resolution": "what was decided (null if still open)",
      "status": "decided" or "open",
      "involved": ["person1", "person2"],
      "owner": "who needs to answer this if open (or null)",
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
- A decided item must have clear group agreement, not just one person's opinion
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
            return {"questions": [], "action_items": []}

        # Validate structure
        valid_types = set(stakeholder_types) if stakeholder_types else set()

        def _validate_type(raw_type):
            if not raw_type or not valid_types:
                return raw_type
            if raw_type in valid_types:
                return raw_type
            for vt in valid_types:
                if raw_type.lower() == vt.lower():
                    return vt
            return raw_type

        questions = []
        for q in result.get("questions", []):
            if isinstance(q, dict) and q.get("topic") and q.get("question"):
                status = q.get("status", "open")
                if status not in ("open", "decided"):
                    status = "open"
                questions.append({
                    "topic": q["topic"],
                    "question": q["question"],
                    "resolution": q.get("resolution"),
                    "status": status,
                    "involved": q.get("involved", []),
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

        return {"questions": questions, "action_items": actions}

    except json.JSONDecodeError:
        return {"questions": [], "action_items": []}


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


def _resolve_contact(name: str | list | None, name_map: dict[str, int]) -> int | None:
    """Resolve a person name to a contact_id using the lookup map."""
    if not name:
        return None
    if isinstance(name, list):
        name = name[0] if name else None
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
    # Questions: involved names → contact_ids, owner name → owner_contact_id
    for q in result["questions"]:
        contact_ids = []
        for name in q.pop("involved", []):
            cid = _resolve_contact(name, name_map)
            if cid is not None:
                contact_ids.append(cid)
        q["contact_ids"] = contact_ids

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
    include_quotes: bool = True,
    rank_top_n: int = 10,
) -> dict:
    """Harvest questions/decisions, action items, and quotes from a call.

    Uses batch summaries if available. Falls back to call summary
    for calls without transcripts (e.g., cell phone calls with manual recaps).

    Args:
        call_id: The call to harvest from
        project_id: Project to associate findings with
        show_progress: Print progress
        clear_existing: Clear existing candidates before extraction
        include_quotes: Also extract and rank verbatim quotes
        rank_top_n: How many top quotes to rank (if include_quotes)

    Returns:
        {"call_id", "questions_extracted", "actions_extracted",
         "quotes_extracted", "quotes_ranked"}
    """
    # Ensure model is loaded with correct context length
    ensure_model()

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
        q_cleared = clear_candidate_questions(call_id)
        a_cleared = clear_candidate_actions(call_id)
        if show_progress and (q_cleared + a_cleared) > 0:
            print(f"  Cleared {q_cleared} questions/decisions, {a_cleared} actions")

    # Build summaries text from batch summaries (skip if fallback already set it)
    if summaries:
        summaries_text = "\n\n".join([
            f"[Segment {s['batch_idx']+1}] {s['summary']}"
            for s in summaries
        ])
        if show_progress:
            print(f"  Harvesting from {len(summaries)} batch summaries...")

    # Get call context (includes user_notes)
    call_context = get_call_context(call_id)

    # Load stakeholder types from PROJECT.md
    project_name = _get_project_name(project_id)
    stakeholder_types = _get_stakeholder_types(project_name)
    if show_progress and stakeholder_types:
        print(f"  Stakeholder types: {', '.join(stakeholder_types)}")

    # Extract
    result = harvest_from_summaries(summaries_text, call_context, stakeholder_types=stakeholder_types)

    # Deduplicate
    result["questions"] = deduplicate_harvest(result["questions"], key="topic")
    result["action_items"] = deduplicate_harvest(result["action_items"], key="title")

    # Resolve contact names → IDs
    name_map = _build_contact_map(call_id)
    result = _resolve_harvest_contacts(result, name_map)

    # Insert candidates
    q_count = insert_candidate_questions(project_id, call_id, result["questions"])
    a_count = insert_candidate_actions(project_id, call_id, result["action_items"])

    # Quote extraction + ranking
    quotes_extracted = 0
    quotes_ranked = []
    if include_quotes:
        from .quotes import extract_call_quotes, rank_quotes

        if show_progress:
            print("  Extracting quotes from transcript chunks...")
        q_result = extract_call_quotes(
            call_id, show_progress=show_progress, clear_existing=clear_existing
        )
        if "error" not in q_result:
            quotes_extracted = q_result["quotes_extracted"]
            if quotes_extracted > 0 and show_progress:
                print("  Ranking quotes by project relevance...")
            if quotes_extracted > 0:
                quotes_ranked = rank_quotes(call_id, top_n=rank_top_n, show_progress=show_progress)

    if show_progress:
        print(f"\n  Next: \"Show me what was extracted for call {call_id}\" · \"Build harvest review for call {call_id}\"")

    return {
        "call_id": call_id,
        "questions_extracted": q_count,
        "actions_extracted": a_count,
        "quotes_extracted": quotes_extracted,
        "quotes_ranked": quotes_ranked,
    }


def deduplicate_harvest(
    items: list[dict], key: str = "topic", threshold: float = 0.85
) -> list[dict]:
    """Remove near-duplicate items using embedding cosine similarity.

    Embeds each item's key field + question/title text, then drops items
    whose similarity to an already-kept item exceeds the threshold.
    Prefers decided items over open ones when choosing which to keep.
    """
    if len(items) <= 1:
        return items

    from nomic_onnx_embed.embed import _embed
    from .config import EMBED_MODEL
    import numpy as np

    # Build text to embed: combine key field with detail field for richer signal
    texts = []
    for item in items:
        detail = item.get("question") or item.get("title") or ""
        texts.append(f"{item[key]}: {detail}")

    vectors = _embed(texts, model_id=EMBED_MODEL)
    # Normalize for cosine similarity
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    normed = vectors / norms

    kept_indices = []
    for i in range(len(items)):
        is_dup = False
        for j in kept_indices:
            sim = float(np.dot(normed[i], normed[j]))
            if sim >= threshold:
                is_dup = True
                # If the new item is decided and the kept one isn't, swap
                if items[i].get("status") == "decided" and items[j].get("status") != "decided":
                    kept_indices.remove(j)
                    kept_indices.append(i)
                break
        if not is_dup:
            kept_indices.append(i)

    return [items[i] for i in kept_indices]


def _get_project_name(project_id: int) -> str | None:
    """Get project name by ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
            row = cur.fetchone()
            return row["name"] if row else None


_TENSION_KEYWORDS = [
    "tension", "disagree", "concern", "however", "push back",
    "hesitat", "not sure", "reluctan", "skeptic", "frustrated",
]


def _find_tension_batches(summaries: list[dict]) -> list[int]:
    """Return batch_idx values whose summary text contains tension keywords."""
    results = []
    for s in summaries:
        text = s["summary"].lower()
        if any(kw in text for kw in _TENSION_KEYWORDS):
            results.append(s["batch_idx"])
    return results


def build_harvest_review(call_id: int, project_id: int) -> dict:
    """Gather harvest data and build a review prompt for Claude Code.

    Follows the analysis.py:suggested_next_step() pattern: gathers data,
    returns dict with structured fields + review_prompt string.

    Args:
        call_id: The call to review
        project_id: Project context

    Returns:
        {
            "call_id": int,
            "call": dict,
            "summaries": list,
            "decisions": list,
            "questions": list,
            "actions": list,
            "quotes": list,
            "review_prompt": str (markdown for Claude Code)
        }
    """
    from .crud.quotes import get_candidate_quotes

    ensure_model()

    # Call metadata (same join pattern as analysis.py)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.*, o.name as org_name, p.name as project_name
                FROM calls c
                JOIN orgs o ON c.org_id = o.id
                LEFT JOIN projects p ON c.project_id = p.id
                WHERE c.id = %s
            """, (call_id,))
            call = cur.fetchone()
            if not call:
                return {"error": f"Call {call_id} not found"}

    # Contacts
    contacts = get_call_contacts(call_id)
    contacts_str = ", ".join(c["name"] for c in contacts) if contacts else "None"

    # Batch summaries
    summaries = get_call_batch_summaries(call_id)
    if not summaries:
        return {"error": f"No batch summaries for call {call_id}. Run harvest first."}

    summaries_text = "\n\n".join([
        f"**Segment {s['batch_idx']+1}** (chunks {s['start_chunk_idx']}-{s['end_chunk_idx']}):\n{s['summary']}"
        for s in summaries
    ])

    # Harvest candidates (unified: questions include both open and decided)
    questions = get_candidate_questions(project_id, call_id)
    actions = get_candidate_actions(project_id, call_id)
    quotes = get_candidate_quotes(call_id)

    # Raw transcript excerpts
    chunks = get_call_chunks(call_id)

    # Opening batch
    opening_chunks = chunks[:BATCH_SIZE] if chunks else []
    opening_text = "\n".join([
        f"{c.get('speaker', '???')}: {c['text']}" for c in opening_chunks
    ])

    # Closing batch
    closing_chunks = chunks[-BATCH_SIZE:] if len(chunks) > BATCH_SIZE else []
    closing_text = "\n".join([
        f"{c.get('speaker', '???')}: {c['text']}" for c in closing_chunks
    ])

    # Tension segments — chunks from batches with tension keywords
    tension_batch_idxs = _find_tension_batches(summaries)
    tension_text = ""
    if tension_batch_idxs:
        tension_chunks = []
        for bidx in tension_batch_idxs:
            start = bidx * BATCH_SIZE
            end = start + BATCH_SIZE
            tension_chunks.extend(chunks[start:end])
        tension_text = "\n".join([
            f"{c.get('speaker', '???')}: {c['text']}" for c in tension_chunks
        ])

    # PROJECT.md content
    project_name = _get_project_name(project_id)
    project_docs = get_project_docs(project_name) if project_name else {}
    project_md = project_docs.get("project", "No PROJECT.md found.")

    # User notes
    user_notes = call.get("user_notes") or ""

    # Format questions/decisions for prompt (unified)
    questions_section = ""
    if questions:
        lines = []
        for q in questions:
            decided_by = ", ".join(c["name"] for c in q.get("decided_by", []))
            owner = q.get("owner_name") or ""
            status_label = q.get("status", "open")
            resolution = q.get("resolution") or ""
            line = f"- **[Q{q['id']}]** ({status_label}) {q['topic']}: {q['question']}"
            if resolution:
                line += f"\n  Resolution: {resolution}"
            if decided_by:
                line += f"\n  Decided by: {decided_by}"
            if owner and status_label == "open":
                line += f" (owner: {owner})"
            lines.append(line)
        questions_section = "\n".join(lines)
    else:
        questions_section = "None extracted."

    # Format actions for prompt
    actions_section = ""
    if actions:
        lines = []
        for a in actions:
            assigned = a.get("assigned_name") or "unassigned"
            lines.append(
                f"- **[A{a['id']}]** {a['title']}"
                + (f": {a['description']}" if a.get("description") else "")
                + (f" (assigned: {assigned})" if assigned != "unassigned" else "")
            )
        actions_section = "\n".join(lines)
    else:
        actions_section = "None extracted."

    # Format quotes for prompt
    quotes_section = ""
    if quotes:
        lines = []
        for q in quotes:
            speaker = q.get("speaker") or "Unknown"
            lines.append(f'- **[QT{q["id"]}]** "{q["quote_text"]}" — {speaker}')
            if q.get("context"):
                lines.append(f"  Context: {q['context']}")
        quotes_section = "\n".join(lines)
    else:
        quotes_section = "None extracted."

    # Tension segments section
    tension_section = ""
    if tension_text:
        tension_section = f"""
## Tension Segments (raw transcript)

These segments had tension indicators in the batch summaries (batches: {tension_batch_idxs}):

```
{tension_text}
```
"""

    # Build the review prompt
    review_prompt = f"""# Harvest Review: Call {call_id}

## Call Metadata

- **Org:** {call['org_name']}
- **Project:** {call.get('project_name') or 'None'}
- **Date:** {call['call_date']}
- **Contacts:** {contacts_str}

{f"## My Notes{chr(10)}{chr(10)}{user_notes}{chr(10)}" if user_notes else ""}
## Batch Summaries (Mistral-generated)

{summaries_text}

## Mistral's Harvest Candidates

### Questions & Decisions
{questions_section}

### Action Items
{actions_section}

### Quote Candidates
{quotes_section}

## Raw Transcript: Opening

```
{opening_text}
```

{"## Raw Transcript: Closing" + chr(10) + chr(10) + "```" + chr(10) + closing_text + chr(10) + "```" + chr(10) if closing_text else ""}
{tension_section}
## PROJECT.md Context

```markdown
{project_md[:3000]}
```

---

# Review Instructions

You are reviewing Mistral Small 3.2's harvest extraction for this call. Apply your relational intelligence to:

## 1. Decision Quality Assessment
For each decided item (status=decided), assess:
- Is this a genuine group agreement, or just one person's opinion?
- Confidence level: explicit agreement vs implicit/assumed?
- Who was notably silent? Does silence = assent or disengagement?
- Recommend: **confirm** [Q<id>] or **reject** [Q<id>] with reasoning

## 2. Missing Extractions
What did Mistral miss?
- Implicit decisions (agreements buried in context, not stated explicitly)
- Questions that were raised but not captured
- Action items hidden in hedging language ("we should probably...", "it might be worth...")
- Provide specific text to add, with the same field structure

## 3. Quote Re-ranking
From the quote candidates, select the **top 5** by:
- Strategic value (useful in follow-ups, proposals, stakeholder docs)
- Emotional resonance (captures the "vibe" of the conversation)
- Specificity (concrete statements > vague generalities)
- Recommend: **approve** [QT<id>] for top picks, with brief reasoning

## 4. Relational Dynamics
Read between the lines:
- Trust level between participants
- Who has real influence vs positional authority?
- Unspoken concerns or elephants in the room
- Tone shifts during the conversation
- Power dynamics and their implications for next steps

## 5. Specific Recommendations
Provide actionable next steps:
- Which items to confirm/reject (by ID)
- New decisions/questions/actions to add (provide exact text)
- Quotes to approve (by ID)
- Any follow-up actions based on relational dynamics
"""

    return {
        "call_id": call_id,
        "call": dict(call),
        "summaries": [dict(s) for s in summaries],
        "questions": [dict(q) for q in questions],
        "actions": [dict(a) for a in actions],
        "quotes": [dict(q) for q in quotes],
        "review_prompt": review_prompt,
    }


