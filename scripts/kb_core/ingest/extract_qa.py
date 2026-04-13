"""Q&A extraction from ingested call transcripts.

Combines Phase 1 (evidence) and Phase 2 (Q&A pairs) into a single LLM call
per source. Uses XML-structured prompts per prompt-architecture skill.

Each call produces:
- Classification evidence (key quotes + rationale)
- Q&A pairs (caller question + agent answer, paraphrased + verbatim)
"""

import json
import sys
from openai import OpenAI
from ..config import LM_STUDIO_URL, SUMMARY_MODEL
from ..db import get_db


EXTRACT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "qa_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "classification_evidence": {
                    "type": "object",
                    "properties": {
                        "category_confirmed": {"type": "string"},
                        "caller_type": {
                            "type": "string",
                            "enum": ["patient", "family_member", "provider_office", "insurance_rep"],
                        },
                        "key_quotes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "quote": {"type": "string"},
                                    "speaker": {"type": "string"},
                                    "relevance": {"type": "string"},
                                },
                                "required": ["quote", "speaker", "relevance"],
                            },
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["category_confirmed", "caller_type", "key_quotes", "rationale"],
                },
                "qa_pairs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "answer": {"type": "string"},
                            "question_verbatim": {"type": "string"},
                            "answer_verbatim": {"type": "string"},
                            "topic": {"type": "string"},
                            "answered": {"type": "boolean"},
                        },
                        "required": ["question", "answer", "question_verbatim",
                                     "answer_verbatim", "topic", "answered"],
                    },
                },
            },
            "required": ["classification_evidence", "qa_pairs"],
        },
    },
}

SYSTEM_MSG = """\
<role_and_constraints>
You extract Q&A pairs from customer service call transcripts for a DME (Durable \
Medical Equipment) company called OrthoXpress / OrthoKinetics.

[AGENT] lines are the OrthoXpress employee. [CALLER] lines are the person calling in.

Extraction rules:
- Only extract questions the CALLER asked. NOT agent verification questions.
- If the caller asked the same question multiple times, count it once.
- If the agent transferred or couldn't answer, set answered=false.
- Paraphrase questions and answers for clarity. Also include verbatim quotes.

Caller type: Identify who the caller is — patient, family_member, provider_office, \
or insurance_rep. This goes in caller_type field of classification_evidence.
</role_and_constraints>

<negative_examples>
These are NOT caller questions — do NOT extract them:
- "Can you verify her date of birth?" (agent verification)
- "Can you spell your last name?" (agent verification)
- "What insurance do you have?" (agent verification)
- "What is your name?" (agent verification)

These ARE caller questions — DO extract them:
- "How do I get my equipment?" (process question)
- "When will it be delivered?" (tracking question)
- "Do you carry walkers?" (equipment catalog question)
</negative_examples>

<output_contract>
classification_evidence:
  category_confirmed: the category that fits this call
  caller_type: patient | family_member | provider_office | insurance_rep
  key_quotes: 2-5 quotes that justify the category, each with speaker and relevance
  rationale: 2-3 sentence explanation

qa_pairs array — each item:
  question: paraphrased caller question (clear, standalone)
  answer: paraphrased agent answer (clear, standalone)
  question_verbatim: exact words from transcript
  answer_verbatim: exact words from transcript
  topic: delivery_timeline | order_status | equipment_setup | insurance_coverage | \
referral_process | pickup_logistics | equipment_catalog | billing | call_routing | other
  answered: true if agent resolved it, false if transferred or couldn't answer

Example of a good extraction:
  question: "How do I get the equipment my doctor ordered?"
  answer: "Your doctor sends us the referral, we process it, and a local rep contacts you to schedule delivery."
  question_verbatim: "I have some paperwork from my orthopedic doctor that I'm supposed to get some supplies from your company"
  answer_verbatim: "We just received your orders and it is pending. I'm going to send this to the local rep and they will contact you upon delivery."
  topic: "referral_process"
  answered: true
</output_contract>"""


def extract_qa_from_source(source_id: int, client: OpenAI = None) -> dict:
    """Extract evidence + Q&A pairs from a single source.

    Returns the full extraction result or {"error": ...}.
    """
    if client is None:
        client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, agent_name, category, tagged_text, raw_text
                   FROM ingest_sources WHERE id = %s""",
                (source_id,),
            )
            src = cur.fetchone()

    if not src:
        return {"error": f"Source {source_id} not found"}

    text = src["tagged_text"] or src["raw_text"] or ""
    if len(text) > 16000:
        text = text[:16000] + "\n[... truncated]"

    cat_attr = f' category="{src["category"]}"' if src["category"] else ""
    agent_attr = f' agent_name="{src["agent_name"]}"' if src["agent_name"] else ""

    user_msg = f"""\
<task_instruction>
Analyze this customer service call transcript. Extract:
1. Classification evidence: 2-5 key quotes that justify the category "{src['category'] or 'unknown'}".
2. Every distinct question the CALLER asked and how the AGENT answered.
</task_instruction>

<context>
Agent name: {src['agent_name'] or 'Unknown'}
Assigned category: {src['category'] or 'unknown'}
</context>

<transcript{agent_attr}{cat_attr}>
{text}
</transcript>"""

    try:
        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_MSG},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=4096,
            temperature=0.1,
            response_format=EXTRACT_SCHEMA,
        )
        result = json.loads(response.choices[0].message.content)
        return {"source_id": source_id, **result}
    except Exception as e:
        return {"error": f"LLM error for source {source_id}: {e}"}


def extract_qa_batch(
    project_id: int,
    limit: int = None,
    category: str = None,
) -> list[dict]:
    """Extract evidence + Q&A from multiple sources. Commits each to DB individually."""
    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    # Get sources that haven't been extracted yet
    query = """
        SELECT s.id, s.agent_name, s.category
        FROM ingest_sources s
        WHERE s.project_id = %s
        AND s.category IS NOT NULL
        AND s.id NOT IN (SELECT DISTINCT ingest_source_id FROM ingest_evidence WHERE project_id = %s)
    """
    params = [project_id, project_id]
    if category:
        query += " AND s.category = %s"
        params.append(category)
    query += " ORDER BY s.id"
    if limit:
        query += f" LIMIT {limit}"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            sources = cur.fetchall()

    if not sources:
        print("No sources to extract (all already processed or none classified).")
        return []

    print(f"Extracting Q&A from {len(sources)} sources...")
    results = []

    for i, src in enumerate(sources):
        print(f"  [{i+1}/{len(sources)}] source {src['id']} ({src['agent_name']}, {src['category']})...", end=" ")
        sys.stdout.flush()

        result = extract_qa_from_source(src["id"], client=client)

        if "error" in result:
            print(f"ERROR: {result['error']}")
            sys.stdout.flush()
            results.append(result)
            continue

        # Store evidence
        evidence = result["classification_evidence"]
        qa_pairs = result["qa_pairs"]

        with get_db() as conn:
            with conn.cursor() as cur:
                # Insert evidence
                cur.execute(
                    """INSERT INTO ingest_evidence
                       (ingest_source_id, project_id, category, caller_type, key_quotes, rationale)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (src["id"], project_id, evidence["category_confirmed"],
                     evidence.get("caller_type"), json.dumps(evidence["key_quotes"]),
                     evidence["rationale"]),
                )

                # Insert Q&A pairs
                for qa in qa_pairs:
                    cur.execute(
                        """INSERT INTO ingest_qa
                           (ingest_source_id, project_id, question, answer,
                            question_verbatim, answer_verbatim, topic, category, answered)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (src["id"], project_id, qa["question"], qa["answer"],
                         qa["question_verbatim"], qa["answer_verbatim"],
                         qa["topic"], src["category"], qa["answered"]),
                    )
            conn.commit()

        print(f"{len(qa_pairs)} Q&A pairs")
        sys.stdout.flush()
        results.append(result)

    return results
