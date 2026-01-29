"""Agentic analysis functions for stakeholder intelligence."""

from pathlib import Path
from .db import get_db
from .search import hybrid_search, get_stakeholder_context
from .crud.quotes import get_approved_quotes


def suggested_next_step(call_id: int, letter_path: str = None) -> dict:
    """
    Gather call context using agentic search for Claude Code analysis.

    Process:
    1. Retrieve call summary
    2. Load Peterson framework from reference_docs
    3. Load approved quotes for call vibe
    4. Perform agentic search on relevant KB context
    5. Optionally load letter for review
    6. Return structured data for Claude Code to analyze

    Args:
        call_id: Call to analyze
        letter_path: Optional path to letter/email to review

    Returns:
        {
            "call": {call data},
            "framework": "Peterson framework content",
            "quotes": [approved quotes],
            "letter": "letter content if provided",
            "agentic_search_results": [{query, text, score}, ...],
            "stakeholder_context": {stakeholder data},
            "analysis_prompt": "Formatted prompt for Claude Code"
        }
    """
    # 1. Get call summary
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.*, s.name as stakeholder_name, p.name as project_name
                FROM calls c
                JOIN stakeholders s ON c.stakeholder_id = s.id
                LEFT JOIN projects p ON c.project_id = p.id
                WHERE c.id = %s
            """, (call_id,))
            call = cur.fetchone()

            if not call:
                raise ValueError(f"Call {call_id} not found")

    # 2. Load Peterson framework
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT content
                FROM reference_docs
                WHERE category = 'sales_framework'
                LIMIT 1
            """)
            framework_doc = cur.fetchone()

            if not framework_doc:
                raise ValueError("Sales framework not found in reference_docs")

            framework_content = framework_doc['content']

    # 3. Load approved quotes for call vibe
    quotes = get_approved_quotes(call_id)

    # 4. Load letter if provided
    letter_content = None
    if letter_path:
        letter_file = Path(letter_path).expanduser()
        if letter_file.exists():
            letter_content = letter_file.read_text()

    # 5. Agentic search: Find related calls and context
    search_queries = [
        f"conversations with {call['stakeholder_name']}",
        f"{call['project_name']} project discussions" if call['project_name'] else None,
        "sales strategy and next steps"
    ]
    # Filter out None queries
    search_queries = [q for q in search_queries if q]

    agentic_search_results = []
    for query in search_queries:
        results = hybrid_search(
            query=query,
            stakeholder_name=call['stakeholder_name'],
            limit=3
        )
        for r in results:
            agentic_search_results.append({
                "query": query,
                "text": r['text'][:300],
                "score": float(r['combined_score']),
                "call_date": str(r['call_date'])
            })

    # Get stakeholder context
    stakeholder_context = get_stakeholder_context(call['stakeholder_name'])

    # 6. Build analysis prompt for Claude Code

    # Build quotes section
    quotes_section = ""
    if quotes:
        quotes_lines = []
        for q in quotes:
            speaker = q.get('speaker') or 'Unknown'
            category = f" [{q['category']}]" if q.get('category') else ""
            quotes_lines.append(f'> "{q["quote_text"]}"\n> — {speaker}{category}')
        quotes_section = f"""
# Key Quotes (vibe of the call)

{chr(10).join(quotes_lines)}
"""

    # Build letter section
    letter_section = ""
    if letter_content:
        letter_section = f"""
# Letter/Email to Review

```markdown
{letter_content}
```
"""

    analysis_prompt = f"""# Call Summary

**Stakeholder:** {call['stakeholder_name']}
**Project:** {call['project_name'] or 'None'}
**Date:** {call['call_date']}
**Participants:** {', '.join(call['participants'])}

{call['summary']}
{quotes_section}
# Related Context (from agentic search)

{chr(10).join([f"- [{r['call_date']}] {r['text']}..." for r in agentic_search_results[:5]])}

# Stakeholder History

- Total calls: {len(stakeholder_context['calls'])}
- Total chunks: {stakeholder_context['all_chunks_count']}

# Peterson Framework Principles

**Critical Principles:**
1. Customer is the HERO (not you, not your company)
2. Your role is MENTOR/GUIDE
3. Focus on challenging status quo, not pitching solutions
4. Earn right to ask questions by sharing insights
5. Use provocation with constructive tension
6. Tell customer's story, not your story
{letter_section}
# Your Task

Using Peterson's Power Messaging framework, provide:

1. **Current Stage:** Where is this conversation in the Peterson framework?
2. **Analysis:** What's working? What's missing from a Power Messaging perspective?
3. **Suggested Next Step:** ONE specific, actionable next step
4. **Rationale:** Why this step, grounded in Peterson's principles
5. **Peterson Concepts to Apply:** Which specific framework elements should guide this next step?"""

    # Add letter review task if letter provided
    if letter_content:
        analysis_prompt += """
6. **Letter Review:** Does this letter align with Peterson's principles? Specific improvements?"""

    return {
        "call_id": call_id,
        "call": dict(call),
        "framework": framework_content,
        "quotes": [dict(q) for q in quotes],
        "letter": letter_content,
        "agentic_search_results": agentic_search_results,
        "stakeholder_context": {
            "stakeholder": dict(stakeholder_context['stakeholder']),
            "total_calls": len(stakeholder_context['calls']),
            "total_chunks": stakeholder_context['all_chunks_count']
        },
        "analysis_prompt": analysis_prompt
    }
