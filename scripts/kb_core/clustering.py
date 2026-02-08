"""Clustering for knowledge base chunks using vector embeddings.

Uses AgglomerativeClustering with cosine distance to group semantically
related chunks. Supports per-call clustering, cross-call clustering,
and search result expansion via cluster membership.
"""

from collections import Counter

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from .db import get_db

# Stop words for cluster labeling — standard + transcript filler
_STOP = frozenset(
    "i me my we our you your he she it they them their its a an the and but or "
    "so if in on at to for of is am are was were be been being have has had do "
    "does did will would shall should can could may might must not no nor that "
    "this these those what which who whom how when where why all any each every "
    "some much many more most other such than too very just also about after "
    "before between from into through during with without again further then "
    "once here there up down out off over under above below like well really "
    "going gonna kind mean maybe sort like yeah right okay sure well actually "
    "thing things people said say says know think believe guess stuff basically "
    "literally probably definitely certainly perhaps obviously certainly clearly "
    "pretty much anyway though however still already even ever never always "
    "want need make made way getting come came went goes take took look looking "
    "tell told talk talking asking asked give gave done doing trying tried "
    "good great nice fine okay cool awesome interesting different little "
    "whole bunch couple able point part question answer something anything "
    "nothing everything else another first last next back long "
    "start started keep kept feel felt seems seemed work working worked "
    "really truly honestly frankly simply exactly happen happened "
    "chris kevin john jeff sara bawa".split()
)


def cluster_label(chunks: list[dict], max_words: int = 3) -> str:
    """Generate a short descriptive label from chunk texts using top keywords.

    Filters filler/stop words aggressively and requires min 4 chars.
    Uses document frequency (how many chunks contain the word) rather than
    raw count, so words that appear across chunks rank higher.
    """
    doc_freq: Counter[str] = Counter()
    for ch in chunks:
        words = ch.get("text", "").lower().split()
        seen: set[str] = set()
        for w in words:
            cleaned = w.strip(".,;:!?\"'()-/[]{}#@$%^&*_+=~`<>|\\")
            if len(cleaned) > 3 and cleaned not in _STOP and cleaned.isalpha() and cleaned not in seen:
                seen.add(cleaned)
                doc_freq[cleaned] += 1

    top = [w for w, _ in doc_freq.most_common(max_words)]
    return " / ".join(top) if top else "unnamed"


def _fetch_embeddings(call_id: int = None) -> list[dict]:
    """Fetch chunk IDs and embeddings from database.

    Args:
        call_id: If provided, only fetch chunks for this call.

    Returns:
        List of {"id": int, "call_id": int, "chunk_idx": int, "embedding": list[float]}
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            if call_id:
                cur.execute(
                    """SELECT c.id, c.call_id, c.chunk_idx, c.embedding::text
                       FROM call_chunks c
                       WHERE c.call_id = %s AND c.embedding IS NOT NULL
                       ORDER BY c.chunk_idx""",
                    (call_id,),
                )
            else:
                cur.execute(
                    """SELECT c.id, c.call_id, c.chunk_idx, c.embedding::text
                       FROM call_chunks c
                       WHERE c.embedding IS NOT NULL
                       ORDER BY c.call_id, c.chunk_idx"""
                )
            rows = cur.fetchall()

    results = []
    for row in rows:
        # Parse pgvector text representation: "[0.1,0.2,...]"
        emb_str = row["embedding"].strip("[]")
        embedding = [float(x) for x in emb_str.split(",")]
        results.append({
            "id": row["id"],
            "call_id": row["call_id"],
            "chunk_idx": row["chunk_idx"],
            "embedding": embedding,
        })
    return results


def compute_clusters(
    call_id: int = None,
    distance_threshold: float = 0.3,
) -> dict[int, list[int]]:
    """Compute clusters from chunk embeddings.

    Uses AgglomerativeClustering with cosine distance. Chunks closer
    than distance_threshold are grouped together.

    Args:
        call_id: Scope to a single call, or None for all chunks.
        distance_threshold: Max cosine distance within a cluster (0.0-1.0).
            Lower = tighter clusters, higher = broader grouping.
            0.3 is a good default for topically related content.

    Returns:
        Dict mapping cluster_label -> list of chunk IDs.
    """
    rows = _fetch_embeddings(call_id)
    if len(rows) < 2:
        if rows:
            return {0: [rows[0]["id"]]}
        return {}

    embeddings = np.array([r["embedding"] for r in rows])
    chunk_ids = [r["id"] for r in rows]

    clustering = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    )
    labels = clustering.fit_predict(embeddings)

    clusters = {}
    for chunk_id, label in zip(chunk_ids, labels):
        label = int(label)
        if label not in clusters:
            clusters[label] = []
        clusters[label].append(chunk_id)

    return clusters


def store_clusters(call_id: int = None, distance_threshold: float = 0.3) -> dict:
    """Compute and store cluster assignments on chunks.

    Stores cluster_id in the chunk_clusters table for fast lookup
    during search expansion.

    Returns:
        {"clusters": int, "chunks_clustered": int}
    """
    clusters = compute_clusters(call_id, distance_threshold)

    with get_db() as conn:
        with conn.cursor() as cur:
            # Clear existing assignments for scope
            if call_id:
                cur.execute(
                    "DELETE FROM chunk_clusters WHERE chunk_id IN (SELECT id FROM call_chunks WHERE call_id = %s)",
                    (call_id,),
                )
            else:
                cur.execute("DELETE FROM chunk_clusters")

            # Insert new assignments
            total = 0
            for label, chunk_ids in clusters.items():
                for chunk_id in chunk_ids:
                    cur.execute(
                        "INSERT INTO chunk_clusters (chunk_id, cluster_id) VALUES (%s, %s)",
                        (chunk_id, label),
                    )
                    total += 1
        conn.commit()

    return {"clusters": len(clusters), "chunks_clustered": total}


def get_cluster_details(call_id: int = None, min_size: int = 2) -> list[dict]:
    """Get clusters with their chunks, sorted by size descending.

    Args:
        call_id: Scope to a single call, or None for all.
        min_size: Minimum cluster size to include.

    Returns:
        List of {"cluster_id", "size", "chunks": [{"id", "call_id", "text", "speaker", "client_name", "call_date"}]}
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            if call_id:
                scope_sql = "AND c.call_id = %s"
                params = (call_id,)
            else:
                scope_sql = ""
                params = ()

            cur.execute(
                f"""SELECT cc.cluster_id, count(*) as size
                    FROM chunk_clusters cc
                    JOIN call_chunks c ON cc.chunk_id = c.id
                    WHERE 1=1 {scope_sql}
                    GROUP BY cc.cluster_id
                    HAVING count(*) >= %s
                    ORDER BY count(*) DESC""",
                params + (min_size,),
            )
            cluster_rows = cur.fetchall()

            results = []
            for cr in cluster_rows:
                cur.execute(
                    f"""SELECT c.id, c.call_id, c.text, c.speaker,
                               cl.name as client_name, ca.call_date
                        FROM chunk_clusters cc
                        JOIN call_chunks c ON cc.chunk_id = c.id
                        JOIN calls ca ON c.call_id = ca.id
                        JOIN orgs cl ON ca.org_id = cl.id
                        WHERE cc.cluster_id = %s {scope_sql}
                        ORDER BY ca.call_date, c.chunk_idx""",
                    (cr["cluster_id"],) + params,
                )
                chunks = cur.fetchall()
                results.append({
                    "cluster_id": cr["cluster_id"],
                    "size": cr["size"],
                    "chunks": chunks,
                })

    return results


def expand_by_cluster(chunk_ids: list[int], exclude_ids: list[int] = None) -> list[dict]:
    """Given chunk IDs, find all other chunks in the same clusters.

    This is the agentic search expansion: take initial search results,
    find their clusters, return the sister chunks that weren't in the
    original results.

    Args:
        chunk_ids: Chunk IDs from initial search results.
        exclude_ids: Chunk IDs to exclude (typically the original results).

    Returns:
        List of chunk dicts with cluster_id, text, speaker, client_name, call_date.
    """
    if not chunk_ids:
        return []

    exclude = set(exclude_ids or chunk_ids)

    with get_db() as conn:
        with conn.cursor() as cur:
            # Find cluster IDs for the input chunks
            placeholders = ",".join(["%s"] * len(chunk_ids))
            cur.execute(
                f"SELECT DISTINCT cluster_id FROM chunk_clusters WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            cluster_ids = [r["cluster_id"] for r in cur.fetchall()]

            if not cluster_ids:
                return []

            # Get all chunks in those clusters, excluding originals
            cl_placeholders = ",".join(["%s"] * len(cluster_ids))
            ex_placeholders = ",".join(["%s"] * len(exclude))
            cur.execute(
                f"""SELECT cc.cluster_id, c.id, c.call_id, c.text, c.speaker,
                           cl.name as client_name, ca.call_date, ca.summary
                    FROM chunk_clusters cc
                    JOIN call_chunks c ON cc.chunk_id = c.id
                    JOIN calls ca ON c.call_id = ca.id
                    JOIN orgs cl ON ca.org_id = cl.id
                    WHERE cc.cluster_id IN ({cl_placeholders})
                      AND c.id NOT IN ({ex_placeholders})
                    ORDER BY ca.call_date DESC, c.chunk_idx""",
                cluster_ids + list(exclude),
            )
            return cur.fetchall()
