"""Search functions for knowledge base."""

from .db import get_db
from .embeddings import get_embedding
from .config import DEFAULT_DAYS_BACK, DECAY_RATE
from .crud.org import get_org
from .crud.calls import get_calls_for_org


def semantic_search(
    query: str,
    client_name: str = None,
    project_name: str = None,
    limit: int = 10,
    days_back: int = None,
    decay_rate: float = DECAY_RATE
) -> list[dict]:
    """Semantic search with time-decay scoring.

    Prioritizes recent content. Within the lookback window, results are
    scored as: relevance_score = (1 - distance) * (decay_rate ^ days_old)

    Args:
        query: Search query
        client_name: Optional filter
        project_name: Optional filter
        limit: Max results
        days_back: Lookback window (None = no limit, use DEFAULT_DAYS_BACK for recency)
        decay_rate: Per-day decay factor (0.95 = 22% penalty at 30 days)

    Returns:
        List of chunks with 'distance', 'days_old', and 'recency_score' fields.
        Empty list if no results in window (caller should ask user to expand).
    """
    query_embedding = get_embedding(query)

    with get_db() as conn:
        with conn.cursor() as cur:
            where_clauses = []
            filter_params = []

            if client_name:
                where_clauses.append("client_name = %s")
                filter_params.append(client_name)

            if project_name:
                where_clauses.append("project_name = %s")
                filter_params.append(project_name)

            if days_back is not None:
                where_clauses.append("call_date >= CURRENT_DATE - %s")
                filter_params.append(days_back)

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)

            # Build params: embedding, decay_rate, filters, limit
            params = [query_embedding, decay_rate] + filter_params + [limit]

            # Time-decay scoring: recency_score = (1 - distance) * (decay_rate ^ days_old)
            cur.execute(
                f"""WITH scored AS (
                        SELECT
                            id, chunk_idx, text, speaker,
                            client_name, project_name, call_date, summary,
                            embedding <=> %s::vector AS distance,
                            (CURRENT_DATE - call_date) AS days_old,
                            (1 - (embedding <=> %s::vector)) * POWER(%s, CURRENT_DATE - call_date) AS recency_score
                        FROM chunks_with_context
                        {where_sql}
                    )
                    SELECT * FROM scored
                    ORDER BY recency_score DESC
                    LIMIT %s""",
                [query_embedding, query_embedding, decay_rate] + filter_params + [limit]
            )
            return cur.fetchall()


def hybrid_search(
    query: str,
    client_name: str = None,
    project_name: str = None,
    limit: int = 10,
    days_back: int = None,
    decay_rate: float = DECAY_RATE,
    semantic_weight: float = 0.7,
    fts_weight: float = 0.3
) -> list[dict]:
    """Hybrid semantic + FTS search with time-decay.

    Combines:
    - Semantic similarity (pgvector cosine distance)
    - Full-text search (PostgreSQL tsvector)
    - Time decay (recent content prioritized)

    final_score = (semantic * weight + fts * weight) * time_decay

    Args:
        query: Search query
        client_name: Optional filter
        project_name: Optional filter
        limit: Max results
        days_back: Lookback window (None = no limit)
        decay_rate: Per-day decay factor (0.95 = 22% penalty at 30 days)
        semantic_weight: Weight for semantic score (default 0.7)
        fts_weight: Weight for FTS score (default 0.3)

    Returns:
        List of chunks with semantic_score, fts_score, combined_score, days_old.
    """
    query_embedding = get_embedding(query)

    with get_db() as conn:
        with conn.cursor() as cur:
            # Build filter clauses
            where_clauses = []
            filter_params = []

            if client_name:
                where_clauses.append("client_name = %s")
                filter_params.append(client_name)

            if project_name:
                where_clauses.append("project_name = %s")
                filter_params.append(project_name)

            if days_back is not None:
                where_clauses.append("call_date >= CURRENT_DATE - %s")
                filter_params.append(days_back)

            filter_sql = ""
            if where_clauses:
                filter_sql = "AND " + " AND ".join(where_clauses)

            # Build params in order matching placeholder positions:
            # Semantic CTE: emb(SELECT), emb(WHERE), [filters], emb(ORDER BY)
            # FTS CTE: query(SELECT), query(WHERE), [filters], query(ORDER BY)
            # Final: sem_weight, fts_weight, decay_rate, limit
            params = (
                [query_embedding, query_embedding] + filter_params + [query_embedding] +
                [query, query] + filter_params + [query] +
                [semantic_weight, fts_weight, decay_rate, limit]
            )

            cur.execute(
                f"""WITH semantic AS (
                        SELECT id, text, speaker, client_name, project_name, call_date, summary,
                               (1 - (embedding <=> %s::vector)) * 8.0 as semantic_score,
                               (CURRENT_DATE - call_date) as days_old
                        FROM chunks_with_context
                        WHERE embedding <=> %s::vector < 0.8
                          {filter_sql}
                        ORDER BY embedding <=> %s::vector
                        LIMIT 100
                    ),
                    fts AS (
                        SELECT id, text, speaker, client_name, project_name, call_date, summary,
                               ts_rank_cd(search_vector, websearch_to_tsquery('english', %s)) * 8.0 as fts_score,
                               (CURRENT_DATE - call_date) as days_old
                        FROM chunks_with_context
                        WHERE search_vector @@ websearch_to_tsquery('english', %s)
                          {filter_sql}
                        ORDER BY ts_rank_cd(search_vector, websearch_to_tsquery('english', %s)) DESC
                        LIMIT 100
                    )
                    SELECT DISTINCT
                        COALESCE(s.id, f.id) as id,
                        COALESCE(s.text, f.text) as text,
                        COALESCE(s.speaker, f.speaker) as speaker,
                        COALESCE(s.client_name, f.client_name) as client_name,
                        COALESCE(s.project_name, f.project_name) as project_name,
                        COALESCE(s.call_date, f.call_date) as call_date,
                        COALESCE(s.summary, f.summary) as summary,
                        COALESCE(s.days_old, f.days_old) as days_old,
                        COALESCE(s.semantic_score, 0) as semantic_score,
                        COALESCE(f.fts_score, 0) as fts_score,
                        (COALESCE(s.semantic_score, 0) * %s + COALESCE(f.fts_score, 0) * %s)
                            * POWER(%s, COALESCE(s.days_old, f.days_old)) as combined_score
                    FROM semantic s
                    FULL OUTER JOIN fts f ON s.id = f.id
                    ORDER BY combined_score DESC
                    LIMIT %s""",
                params
            )
            return cur.fetchall()


def semantic_search_with_fallback(
    query: str,
    client_name: str = None,
    project_name: str = None,
    limit: int = 10
) -> dict:
    """Search with 21-day window, return flag if no results.

    Returns:
        {
            "results": [...],
            "days_back": 21,
            "needs_expansion": True/False
        }
    """
    results = semantic_search(
        query,
        client_name=client_name,
        project_name=project_name,
        limit=limit,
        days_back=DEFAULT_DAYS_BACK
    )

    return {
        "results": results,
        "days_back": DEFAULT_DAYS_BACK,
        "needs_expansion": len(results) == 0
    }


def get_org_context(org_name: str, query: str = None, limit: int = 20) -> dict:
    """Get comprehensive context about an org."""
    org = get_org(org_name)
    if not org:
        return {"error": f"Org '{org_name}' not found"}

    calls = get_calls_for_org(org_name)

    result = {
        "org": org,
        "calls": calls,
        "all_chunks_count": 0
    }

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) as cnt FROM chunks_with_context WHERE client_name = %s",
                (org_name,)
            )
            result["all_chunks_count"] = cur.fetchone()["cnt"]

    if query:
        result["relevant_chunks"] = semantic_search(query, client_name=org_name, limit=limit)

    return result
