"""CRUD for documentation chunks (doc_chunks table).

Sibling to crud/chunks.py. Different shape: docs are tied to files, not calls.
Upsert on (project_id, repo_path, chunk_idx) so re-ingest is idempotent.
"""
from typing import Optional
from ..db import get_db
from ..embeddings import get_embedding


def get_or_create_project(name: str, repo_path: Optional[str] = None) -> int:
    """Get existing project id or create a new row. Returns project.id."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM projects WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                return row["id"]
            cur.execute(
                "INSERT INTO projects (name, repo_path) VALUES (%s, %s) RETURNING id",
                (name, repo_path),
            )
            project_id = cur.fetchone()["id"]
        conn.commit()
        return project_id


def upsert_doc_chunks(project_id: int, chunks: list[dict], show_progress: bool = True) -> int:
    """Embed and upsert chunks. Returns count written.

    chunks is a list of dicts with keys:
        source_url, repo_path, section_path, chunk_idx, text
    Embedding is computed here, not passed in.
    """
    written = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            for i, c in enumerate(chunks):
                embedding = get_embedding(c["text"])
                cur.execute(
                    """
                    INSERT INTO doc_chunks
                        (project_id, source_url, repo_path, section_path, chunk_idx, text, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, repo_path, chunk_idx) DO UPDATE SET
                        source_url   = EXCLUDED.source_url,
                        section_path = EXCLUDED.section_path,
                        text         = EXCLUDED.text,
                        embedding    = EXCLUDED.embedding,
                        ingested_at  = now()
                    """,
                    (project_id, c["source_url"], c["repo_path"], c.get("section_path"),
                     c["chunk_idx"], c["text"], embedding),
                )
                written += 1
                if show_progress and written % 25 == 0:
                    print(f"  embedded {written}/{len(chunks)}")
        conn.commit()
    return written


def purge_stale(project_id: int, keep_repo_paths: set[str]) -> int:
    """Delete chunks for repo_paths no longer present in the current ingest.
    Returns number of rows deleted."""
    if not keep_repo_paths:
        return 0
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM doc_chunks WHERE project_id = %s AND repo_path != ALL(%s)",
                (project_id, list(keep_repo_paths)),
            )
            deleted = cur.rowcount
        conn.commit()
        return deleted


def reset_project(project_id: int) -> int:
    """Delete all doc_chunks for a project. Project row itself stays.
    Returns rows deleted."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM doc_chunks WHERE project_id = %s", (project_id,))
            deleted = cur.rowcount
        conn.commit()
        return deleted


def semantic_search_docs(query: str, project_name: str, limit: int = 5) -> list[dict]:
    """kNN search over doc_chunks for a given project by name."""
    query_emb = get_embedding(query)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    dc.source_url,
                    dc.repo_path,
                    dc.section_path,
                    dc.chunk_idx,
                    dc.text,
                    1 - (dc.embedding <=> %s::vector) AS similarity
                FROM doc_chunks dc
                JOIN projects p ON dc.project_id = p.id
                WHERE p.name = %s
                ORDER BY dc.embedding <=> %s::vector
                LIMIT %s
                """,
                (query_emb, project_name, query_emb, limit),
            )
            return cur.fetchall()


def count_chunks(project_name: str) -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM doc_chunks dc JOIN projects p ON dc.project_id=p.id WHERE p.name=%s",
                (project_name,),
            )
            return cur.fetchone()["n"]
