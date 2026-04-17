"""LM Studio classification for ingest sources.

Uses structured output (json_schema) for guaranteed valid JSON.
Optimized for Mistral Small 3.2 (24B, 4-bit) on Apple Silicon:
- temperature 0.1 avoids quantization rounding traps
- few-shot examples disambiguate borderline categories
- confidence score enables human-in-the-loop for low-confidence calls
"""

import json
from openai import OpenAI
from ..config import LM_STUDIO_URL, SUMMARY_MODEL
from .crud import get_ingest_source, update_classification, list_ingest_sources


CLASSIFY_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [
                        "equipment", "tracking", "insurance",
                        "billing", "order_mgmt", "referral", "other",
                    ],
                },
                "in_scope": {"type": "boolean"},
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["category", "in_scope", "confidence", "reasoning"],
        },
    },
}

SYSTEM_MSG = (
    "You classify customer service calls for a DME (Durable Medical Equipment) company. "
    "Classify each call into exactly one primary category. Never invent categories.\n\n"
    "Categories:\n"
    "- equipment: questions about equipment setup, usage, settings, adjustments, returns, how-to\n"
    "- tracking: where is my order, delivery status, shipping updates\n"
    "- insurance: coverage, authorization, denial, prior auth, benefits\n"
    "- billing: payment, invoices, charges, refunds, collections\n"
    "- order_mgmt: placing, canceling, modifying, or verifying orders\n"
    "- referral: new referral processing, referral status, doctor's office sending referral\n"
    "- other: anything that doesn't fit above\n\n"
    "in_scope is true ONLY for equipment category.\n"
    "confidence is a number between 0 and 1.\n\n"
    "Examples:\n\n"
    "Transcript: Customer calls asking how to adjust the angle on their CPM machine after knee surgery.\n"
    'Answer: {"category": "equipment", "in_scope": true, "confidence": 0.95, '
    '"reasoning": "Customer asking how to use/adjust DME equipment."}\n\n'
    "Transcript: Insurance company calls to verify whether a brace is covered under the patient's plan.\n"
    'Answer: {"category": "insurance", "in_scope": false, "confidence": 0.9, '
    '"reasoning": "Call is about insurance coverage verification, not equipment usage."}\n\n'
    "Transcript: Customer calls to ask where their knee scooter delivery is and when it will arrive.\n"
    'Answer: {"category": "tracking", "in_scope": false, "confidence": 0.95, '
    '"reasoning": "Customer asking about delivery status of ordered equipment."}\n\n'
    "Transcript: Doctor's office calls to send over a new prescription referral for a patient.\n"
    'Answer: {"category": "referral", "in_scope": false, "confidence": 0.9, '
    '"reasoning": "Provider sending a new referral, not an equipment question."}'
)


def classify_source(source_id: int, client: OpenAI = None) -> dict:
    """Classify a single ingest source. Returns classification dict."""
    source = get_ingest_source(source_id)
    if not source:
        return {"error": f"Source {source_id} not found"}

    if client is None:
        client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    raw_text = source["raw_text"] or ""
    if len(raw_text) > 16000:
        raw_text = raw_text[:16000] + "\n[... truncated]"

    try:
        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_MSG},
                {"role": "user", "content": f"Classify this call transcript:\n\n{raw_text}"},
            ],
            max_tokens=200,
            temperature=0.1,
            response_format=CLASSIFY_SCHEMA,
        )
        result = json.loads(response.choices[0].message.content)
        category = result["category"]
        in_scope = result["in_scope"]
        confidence = result.get("confidence", 0)
        reasoning = result.get("reasoning", "")

        update_classification(source_id, category, in_scope, reasoning)
        return {
            "source_id": source_id,
            "category": category,
            "in_scope": in_scope,
            "confidence": confidence,
            "reasoning": reasoning,
        }

    except Exception as e:
        return {"error": f"LLM error for source {source_id}: {e}"}


def classify_batch(
    project_id: int,
    source_type: str = None,
    limit: int = None,
    reclassify: bool = False,
) -> list[dict]:
    """Classify multiple ingest sources. Returns list of results."""
    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    scope = None if reclassify else "unclassified"
    sources = list_ingest_sources(
        project_id=project_id,
        source_type=source_type,
        scope=scope,
        limit=limit or 1000,
    )

    results = []
    for i, source in enumerate(sources):
        print(f"  [{i+1}/{len(sources)}] {source['source_file'] or source['id']}...", end=" ")
        result = classify_source(source["id"], client=client)
        if "error" in result:
            print(f"ERROR: {result['error']}")
        else:
            conf = f"{result['confidence']:.0%}" if result.get("confidence") else "?"
            label = "IN-SCOPE" if result["in_scope"] else "out"
            print(f"{result['category']} ({label}, {conf})")
        results.append(result)

    return results
