-- Migration 002: doc_chunks table for documentation ingest
-- Sibling to call_chunks. Stores chunked markdown from external doc repos
-- (e.g. github.com/open-webui/docs) so Claude can query them via `kb docs search`.

BEGIN;

CREATE TABLE doc_chunks (
    id              BIGSERIAL PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_url      TEXT NOT NULL,                   -- canonical URL on the live docs site
    repo_path       TEXT NOT NULL,                   -- e.g. docs/features/web-search.md
    section_path    TEXT,                            -- "Features > Web Search > Brave"
    chunk_idx       INTEGER NOT NULL,
    text            TEXT NOT NULL,
    embedding       VECTOR(768) NOT NULL,            -- nomic-embed-text-v1.5 dims
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (project_id, repo_path, chunk_idx)
);

-- HNSW index for fast ANN (cosine). Matches call_chunks pattern.
CREATE INDEX doc_chunks_embedding_hnsw
    ON doc_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX doc_chunks_project     ON doc_chunks (project_id);
CREATE INDEX doc_chunks_repo_path   ON doc_chunks (project_id, repo_path);

COMMIT;
