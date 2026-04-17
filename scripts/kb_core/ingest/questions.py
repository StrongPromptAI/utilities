"""Question taxonomy extraction from in-scope ingest sources.

Uses structured output (json_schema) for clean question extraction from Mistral Small 3.2.
"""

import json
import numpy as np
from openai import OpenAI
from ..db import get_db
from ..config import LM_STUDIO_URL, SUMMARY_MODEL


QUESTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "question_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "is_question": {"type": "boolean"},
            },
            "required": ["question", "is_question"],
        },
    },
}

SYSTEM_MSG = (
    "You analyze customer service call excerpts from a DME (Durable Medical Equipment) company. "
    "Given a set of transcript excerpts clustered by topic similarity, extract the core equipment "
    "question customers are asking. State it as a single clear question from the customer's "
    "perspective. If the excerpts don't contain a clear equipment question (greetings, hold, "
    "name exchanges, pleasantries), set is_question to false."
)


def _get_inscope_chunks(project_id: int, source_type: str = None) -> list[dict]:
    """Get all chunks from in-scope sources for a project."""
    query = """
        SELECT ic.id, ic.chunk_idx, ic.text, ic.timestamp_start, ic.timestamp_end,
               ic.embedding::text, ic.ingest_source_id,
               s.agent_name, s.source_date, s.source_file
        FROM ingest_chunks ic
        JOIN ingest_sources s ON ic.ingest_source_id = s.id
        WHERE s.project_id = %s AND s.in_scope = true
    """
    params: list = [project_id]
    if source_type:
        query += " AND s.source_type = %s"
        params.append(source_type)
    query += " ORDER BY s.id, ic.chunk_idx"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def _cluster_chunks(chunks: list[dict], threshold: float = 0.35) -> dict[int, list[dict]]:
    """Cluster chunks by embedding similarity. Returns {cluster_id: [chunks]}."""
    if not chunks:
        return {}

    def _parse_embedding(val):
        """Parse pgvector string '[0.1,0.2,...]' to float list."""
        if isinstance(val, (list, np.ndarray)):
            return val
        s = str(val).strip("[]")
        return [float(x) for x in s.split(",")]

    embeddings = np.array([_parse_embedding(c["embedding"]) for c in chunks])

    from sklearn.cluster import AgglomerativeClustering

    clustering = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=threshold,
        metric="cosine",
        linkage="average",
    )
    labels = clustering.fit_predict(embeddings)

    clusters: dict[int, list[dict]] = {}
    for chunk, label in zip(chunks, labels):
        clusters.setdefault(int(label), []).append(chunk)

    return clusters


def _extract_cluster_question(chunks: list[dict], client: OpenAI) -> dict:
    """Given a cluster of transcript chunks, extract the core question being asked.

    Returns {"question": str, "is_question": bool}.
    """
    sample = chunks[:10] if len(chunks) > 10 else chunks
    texts = "\n---\n".join(c["text"] for c in sample)

    try:
        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_MSG},
                {"role": "user", "content": f"Excerpts:\n{texts}"},
            ],
            max_tokens=100,
            temperature=0.1,
            response_format=QUESTION_SCHEMA,
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"question": f"Error: {e}", "is_question": False}


def extract_question_taxonomy(
    project_id: int,
    source_type: str = None,
    min_freq: int = 2,
    threshold: float = 0.35,
) -> list[dict]:
    """Extract equipment question taxonomy from in-scope sources.

    Returns ranked list of questions with frequency and example sources.
    """
    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    print("Loading in-scope chunks...")
    chunks = _get_inscope_chunks(project_id, source_type)
    if not chunks:
        print("No in-scope chunks found.")
        return []
    print(f"  {len(chunks)} chunks from in-scope sources")

    print("Clustering by topic similarity...")
    clusters = _cluster_chunks(chunks, threshold=threshold)
    print(f"  {len(clusters)} clusters found")

    results = []
    for cluster_id, cluster_chunks in clusters.items():
        source_ids = set(c["ingest_source_id"] for c in cluster_chunks)
        if len(source_ids) < min_freq:
            continue

        print(f"  Extracting question from cluster {cluster_id} ({len(source_ids)} sources)...")
        extracted = _extract_cluster_question(cluster_chunks, client)

        if not extracted["is_question"]:
            continue

        examples = []
        seen_sources = set()
        for c in cluster_chunks:
            if c["ingest_source_id"] not in seen_sources:
                seen_sources.add(c["ingest_source_id"])
                examples.append({
                    "source_id": c["ingest_source_id"],
                    "agent_name": c["agent_name"],
                    "source_date": str(c["source_date"]) if c["source_date"] else None,
                })
                if len(examples) >= 5:
                    break

        results.append({
            "question": extracted["question"],
            "frequency": len(source_ids),
            "chunk_count": len(cluster_chunks),
            "examples": examples,
        })

    results.sort(key=lambda r: r["frequency"], reverse=True)
    return results
