"""Synthesis: distill call intelligence into living stakeholder markdown files.

Each stakeholder type gets a persistent .md that snowballs insights over time.
Per-call synthesis reads new call content + existing doc, proposes additions.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from .config import LM_STUDIO_URL, SUMMARY_MODEL
from .db import get_db
from .crud.projects import get_project
from .crud.chunks import get_call_batch_summaries
from .harvest import _get_stakeholder_types


def type_to_slug(stakeholder_type: str) -> str:
    """Convert stakeholder type to filename slug.

    "Orthopedic Surgeon" → "orthopedic-surgeon"
    """
    return re.sub(r"[^a-z0-9]+", "-", stakeholder_type.lower()).strip("-")


def _get_call_summaries_text(call_id: int) -> str:
    """Get concatenated batch summaries for a call."""
    summaries = get_call_batch_summaries(call_id)
    if not summaries:
        return ""
    return "\n\n".join(
        f"[Segment {s['batch_idx']+1}] {s['summary']}"
        for s in summaries
    )


def _get_call_context(call_id: int) -> dict:
    """Get call metadata for context."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.call_date, c.user_notes, c.summary,
                          o.name as org_name, p.name as project_name
                   FROM calls c
                   JOIN orgs o ON c.org_id = o.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.id = %s""",
                (call_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}

            cur.execute(
                """SELECT ct.name FROM contacts ct
                   JOIN call_contacts cc ON cc.contact_id = ct.id
                   WHERE cc.call_id = %s ORDER BY ct.name""",
                (call_id,),
            )
            contacts = [r["name"] for r in cur.fetchall()]

            return {
                "call_date": str(row["call_date"]),
                "org_name": row["org_name"],
                "project_name": row["project_name"],
                "user_notes": row.get("user_notes") or "",
                "call_summary": row.get("summary") or "",
                "contacts": contacts,
            }


def _get_harvested_items(project_id: int, call_id: int) -> str:
    """Get confirmed/open harvest items from this call as context text."""
    with get_db() as conn:
        with conn.cursor() as cur:
            items = []

            # Decided questions (was: confirmed decisions)
            cur.execute(
                """SELECT topic, resolution, stakeholder_type FROM questions
                   WHERE project_id = %s AND source_call_id = %s
                         AND status = 'decided'""",
                (project_id, call_id),
            )
            for d in cur.fetchall():
                st = f" [{d['stakeholder_type']}]" if d.get("stakeholder_type") else ""
                items.append(f"DECISION{st}: {d['topic']} — {d['resolution']}")

            # Open questions
            cur.execute(
                """SELECT topic, question, stakeholder_type FROM questions
                   WHERE project_id = %s AND source_call_id = %s
                         AND status = 'open'""",
                (project_id, call_id),
            )
            for q in cur.fetchall():
                st = f" [{q['stakeholder_type']}]" if q.get("stakeholder_type") else ""
                items.append(f"QUESTION{st}: {q['topic']} — {q['question']}")

            cur.execute(
                """SELECT title, description, stakeholder_type FROM action_items
                   WHERE project_id = %s AND source_call_id = %s
                         AND status = 'open'""",
                (project_id, call_id),
            )
            for a in cur.fetchall():
                st = f" [{a['stakeholder_type']}]" if a.get("stakeholder_type") else ""
                desc = f" — {a['description']}" if a.get("description") else ""
                items.append(f"ACTION{st}: {a['title']}{desc}")

    return "\n".join(items) if items else ""


def _build_seed_template(stakeholder_type: str, project_name: str) -> str:
    """Build the initial template for a new stakeholder doc."""
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return f"""# {stakeholder_type} Intelligence

*Project: {project_name} | Created: {now}*

## Motivations & Priorities
*What drives this stakeholder? What do they care about most?*

## Concerns & Risks
*What worries them? What could go wrong from their perspective?*

## Value Propositions
*What aspects of the product/service excite them? What resonates?*

## Key Insights
*Important observations, quotes, or patterns from conversations.*

## Open Questions
*Unresolved items that need follow-up with this stakeholder type.*
"""


def distill_for_type(
    stakeholder_type: str,
    call_summaries: str,
    call_context: dict,
    harvested_items: str,
    existing_doc: str | None,
) -> dict:
    """LLM distills call content into proposed additions for a stakeholder type.

    Returns:
        {
            "proposed_additions": [
                {"section": "Motivations & Priorities", "content": "...", "source": "call 27, 2026-02-02"},
                ...
            ],
            "raw_response": str
        }
    """
    context_str = (
        f"Call with {call_context.get('org_name', '?')} on {call_context.get('call_date', '?')}\n"
        f"Participants: {', '.join(call_context.get('contacts', []))}\n"
    )
    if call_context.get("user_notes"):
        context_str += f"User notes: {call_context['user_notes']}\n"

    existing_section = ""
    if existing_doc:
        existing_section = f"""
EXISTING {stakeholder_type.upper()} DOCUMENT (accumulated insights so far):
{existing_doc}

"""

    harvested_section = ""
    if harvested_items:
        harvested_section = f"""
STRUCTURED ITEMS EXTRACTED FROM THIS CALL:
{harvested_items}

"""

    prompt = f"""You are analyzing a call transcript to extract insights relevant to the **{stakeholder_type}** stakeholder type.

CALL CONTEXT:
{context_str}
{existing_section}CALL SUMMARIES (the new material to analyze):
{call_summaries}
{harvested_section}Your task: Identify NEW insights from this call that are relevant to **{stakeholder_type}** stakeholders.
Only propose additions that are NOT already captured in the existing document.

The document has these sections:
- **Motivations & Priorities** — what drives this stakeholder, what they care about
- **Concerns & Risks** — what worries them, what could go wrong
- **Value Propositions** — what excites them about this product/service
- **Key Insights** — observations, notable quotes, patterns
- **Open Questions** — unresolved items needing follow-up

Return a JSON array of proposed additions:

[
  {{
    "section": "one of the section names above",
    "content": "the insight to add, written as a concise bullet point",
    "evidence": "brief quote or reference from the call that supports this"
  }}
]

Rules:
- Only include insights genuinely relevant to {stakeholder_type} stakeholders
- Write content as actionable intelligence, not meeting notes
- If something is already in the existing document, do NOT propose it again
- If nothing new is relevant to this stakeholder type, return an empty array []
- Be selective — quality over quantity
- Include the perspective and emotional context ("this excited them", "this concerned them")

Return ONLY valid JSON array, no other text.

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
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        additions = json.loads(content)
        if not isinstance(additions, list):
            additions = []

        # Validate structure
        valid = []
        for a in additions:
            if isinstance(a, dict) and a.get("section") and a.get("content"):
                valid.append({
                    "section": a["section"],
                    "content": a["content"],
                    "evidence": a.get("evidence", ""),
                })

        return {"proposed_additions": valid, "raw_response": content}

    except json.JSONDecodeError:
        return {"proposed_additions": [], "raw_response": content, "error": "Failed to parse LLM response"}


def apply_additions(
    existing_doc: str,
    additions: list[dict],
    call_date: str,
) -> str:
    """Apply approved additions to the existing document.

    Inserts bullet points under the matching section headers.
    """
    if not additions:
        return existing_doc

    # Group additions by section
    by_section = {}
    for a in additions:
        by_section.setdefault(a["section"], []).append(a)

    lines = existing_doc.splitlines()
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]
        result.append(line)

        # Check if this is a section header that has additions
        if line.startswith("## "):
            section_name = line[3:].strip()
            if section_name in by_section:
                # Find the insertion point: after header, after any italic subtitle, before content
                i += 1
                while i < len(lines) and (lines[i].startswith("*") and lines[i].endswith("*")):
                    result.append(lines[i])
                    i += 1

                # Insert new additions
                for a in by_section[section_name]:
                    result.append(f"- {a['content']} *(Call {call_date})*")

                # Remove the section from pending
                del by_section[section_name]
                continue

        i += 1

    # Update timestamp
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    for idx, line in enumerate(result):
        if line.startswith("*Project:") and "Updated:" in line:
            result[idx] = re.sub(r"Updated: [^*]+", f"Updated: {now}", line)
            break
        elif line.startswith("*Project:") and "Created:" in line:
            result[idx] = line.replace("Created:", "Updated:")
            result[idx] = re.sub(r"Updated: [^*]+", f"Updated: {now}", result[idx])
            break

    return "\n".join(result)


def synthesize_call(
    project_name: str,
    call_id: int,
    stakeholder_type: str = None,
) -> list[dict]:
    """Synthesize intelligence from a single call into stakeholder docs.

    Returns list of dicts, one per stakeholder type:
    {
        'stakeholder_type': str,
        'slug': str,
        'existing_doc': str | None,
        'proposed_additions': list[dict],
        'file_path': Path,
        'error': str | None,
    }
    """
    project = get_project(project_name)
    if not project:
        return [{"error": f"Project not found: {project_name}"}]

    project_id = project["id"]
    repo_path = project.get("repo_path")
    if not repo_path:
        return [{"error": f"Project '{project_name}' has no repo_path set"}]

    stakeholders_dir = Path(repo_path).expanduser() / "symlink_docs" / "stakeholders"

    # Get stakeholder types from PROJECT.md
    all_types = _get_stakeholder_types(project_name)
    if not all_types:
        return [{"error": (
            "No stakeholder types found in PROJECT.md. "
            "Add a Stakeholder table to PROJECT.md first."
        )}]

    if stakeholder_type:
        if stakeholder_type not in all_types:
            return [{"error": f"'{stakeholder_type}' not in PROJECT.md types: {', '.join(all_types)}"}]
        all_types = [stakeholder_type]

    # Load call data
    call_summaries = _get_call_summaries_text(call_id)
    if not call_summaries:
        # Fallback to call summary
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT summary FROM calls WHERE id = %s", (call_id,))
                row = cur.fetchone()
        if row and row["summary"]:
            call_summaries = row["summary"]
        else:
            return [{"error": f"No summaries found for call {call_id}"}]

    call_context = _get_call_context(call_id)
    harvested_items = _get_harvested_items(project_id, call_id)

    results = []
    for st in all_types:
        slug = type_to_slug(st)
        file_path = stakeholders_dir / f"{slug}.md"

        # Load or seed existing doc
        existing_doc = None
        if file_path.exists():
            existing_doc = file_path.read_text()

        # Distill
        distill_result = distill_for_type(
            stakeholder_type=st,
            call_summaries=call_summaries,
            call_context=call_context,
            harvested_items=harvested_items,
            existing_doc=existing_doc,
        )

        results.append({
            "stakeholder_type": st,
            "slug": slug,
            "existing_doc": existing_doc,
            "proposed_additions": distill_result["proposed_additions"],
            "file_path": file_path,
            "error": distill_result.get("error"),
        })

    return results


# Keep for backward compat with __init__.py
synthesize_project = synthesize_call
