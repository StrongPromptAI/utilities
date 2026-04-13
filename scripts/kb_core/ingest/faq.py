"""FAQ assembly from extracted Q&A pairs.

Phase 3: Cluster questions by similarity, rank by frequency,
synthesize canonical answers, attach citations.
Filters to patient + family_member caller types only.
"""

import json
import sys
import numpy as np
from openai import OpenAI
from ..db import get_db
from ..config import LM_STUDIO_URL, SUMMARY_MODEL
from ..embeddings import get_embedding


SYNTH_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "answer_synthesis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "faq_question": {"type": "string"},
                "faq_answer": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["faq_question", "faq_answer", "confidence"],
        },
    },
}

SYNTH_SYSTEM_MSG = """\
<role_and_constraints>
You synthesize FAQ entries from multiple customer service Q&A pairs for a DME \
(Durable Medical Equipment) company. Given a cluster of similar questions and \
their agent answers, produce one canonical FAQ entry.

Rules:
- The FAQ question should be clear, standalone, and phrased from the patient's perspective.
- The FAQ answer should combine the best information from all agent answers.
- Write the answer as if a patient is reading it on their phone — plain language, no jargon.
- If agent answers conflict, use the most complete/helpful one.
- Keep answers under 3 sentences when possible.
- Confidence is 0-1: how well the agent answers cover this question (1.0 = complete, 0.5 = partial).
</role_and_constraints>"""


def _get_patient_qa(project_id: int) -> list[dict]:
    """Get all Q&A pairs from patient + family_member callers."""
    query = """
        SELECT qa.id, qa.question, qa.answer, qa.question_verbatim,
               qa.answer_verbatim, qa.topic, qa.category, qa.answered,
               qa.ingest_source_id,
               s.agent_name, s.source_date,
               e.caller_type
        FROM ingest_qa qa
        JOIN ingest_sources s ON qa.ingest_source_id = s.id
        LEFT JOIN ingest_evidence e ON e.ingest_source_id = qa.ingest_source_id
            AND e.project_id = qa.project_id
        WHERE qa.project_id = %s
        AND (e.caller_type IN ('patient', 'family_member') OR e.caller_type IS NULL)
        ORDER BY qa.id
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (project_id,))
            return cur.fetchall()


def _cluster_questions(qa_pairs: list[dict], min_cluster_size: int = 2) -> list[list[dict]]:
    """Cluster Q&A pairs by question embedding similarity using HDBSCAN."""
    if not qa_pairs:
        return []

    print("  Embedding questions...")
    sys.stdout.flush()
    embeddings = []
    for qa in qa_pairs:
        emb = get_embedding(qa["question"])
        embeddings.append(emb)

    X = np.array(embeddings)

    try:
        import hdbscan
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            metric="cosine",
        )
        labels = clusterer.fit_predict(X)
    except ImportError:
        # Fallback to agglomerative if hdbscan not installed
        from sklearn.cluster import AgglomerativeClustering
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=0.35,
            metric="cosine",
            linkage="average",
        )
        labels = clustering.fit_predict(X)

    # Group by cluster
    clusters: dict[int, list[dict]] = {}
    for qa, label in zip(qa_pairs, labels):
        if label == -1:  # HDBSCAN noise
            continue
        clusters.setdefault(int(label), []).append(qa)

    # Filter by min size and sort by frequency
    result = [c for c in clusters.values() if len(c) >= min_cluster_size]
    result.sort(key=len, reverse=True)
    return result


def _synthesize_faq_entry(cluster: list[dict], client: OpenAI) -> dict:
    """Synthesize one FAQ entry from a cluster of similar Q&A pairs."""
    # Build the examples for the LLM
    examples = []
    for qa in cluster[:8]:  # Cap at 8 to stay within context
        entry = f"Q: {qa['question']}\nA: {qa['answer']}"
        if not qa["answered"]:
            entry += " [agent could not fully answer]"
        examples.append(entry)

    examples_text = "\n---\n".join(examples)

    user_msg = f"""\
<task_instruction>
Synthesize one FAQ entry from these {len(cluster)} similar Q&A pairs.
Write the question from the patient's perspective.
Write the answer in plain language a 65-year-old would understand.
</task_instruction>

<qa_pairs>
{examples_text}
</qa_pairs>"""

    try:
        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": SYNTH_SYSTEM_MSG},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0.1,
            response_format=SYNTH_SCHEMA,
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        # Fallback: use the most common question and best answer
        return {
            "faq_question": cluster[0]["question"],
            "faq_answer": cluster[0]["answer"],
            "confidence": 0.5,
            "error": str(e),
        }


def assemble_faq(
    project_id: int,
    min_freq: int = 2,
    export_path: str = None,
) -> list[dict]:
    """Assemble FAQ from extracted Q&A pairs.

    1. Filter to patient + family_member callers
    2. Cluster questions by embedding similarity
    3. Synthesize canonical answer per cluster
    4. Rank by frequency
    5. Attach citations
    """
    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    print("Loading patient Q&A pairs...")
    sys.stdout.flush()
    qa_pairs = _get_patient_qa(project_id)
    if not qa_pairs:
        print("No patient Q&A pairs found.")
        return []
    print(f"  {len(qa_pairs)} pairs from patient/family callers")
    sys.stdout.flush()

    print("Clustering by question similarity...")
    sys.stdout.flush()
    clusters = _cluster_questions(qa_pairs, min_cluster_size=min_freq)
    print(f"  {len(clusters)} clusters (min frequency: {min_freq})")
    sys.stdout.flush()

    # Synthesize FAQ entries
    faq = []
    for i, cluster in enumerate(clusters):
        print(f"  [{i+1}/{len(clusters)}] Synthesizing ({len(cluster)} pairs)...", end=" ")
        sys.stdout.flush()

        entry = _synthesize_faq_entry(cluster, client)

        # Collect citations
        sources = {}
        topics = set()
        for qa in cluster:
            sid = qa["ingest_source_id"]
            if sid not in sources:
                sources[sid] = {
                    "source_id": sid,
                    "agent_name": qa["agent_name"],
                    "source_date": str(qa["source_date"]) if qa["source_date"] else None,
                    "verbatim": qa["question_verbatim"],
                }
            topics.add(qa["topic"])

        faq_entry = {
            "rank": i + 1,
            "question": entry["faq_question"],
            "answer": entry["faq_answer"],
            "confidence": entry.get("confidence", 0),
            "frequency": len(cluster),
            "unique_calls": len(sources),
            "topics": sorted(topics),
            "citations": list(sources.values()),
        }
        faq.append(faq_entry)
        print(f"\"{entry['faq_question'][:60]}...\"")
        sys.stdout.flush()

    # Sort by frequency
    faq.sort(key=lambda f: f["frequency"], reverse=True)
    for i, f in enumerate(faq):
        f["rank"] = i + 1

    # Export if requested
    if export_path:
        with open(export_path, "w") as f:
            json.dump(faq, f, indent=2)
        print(f"\nExported {len(faq)} FAQ entries to {export_path}")

    return faq
