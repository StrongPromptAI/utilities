-- Migration 003: Guided summary pivot (plan 26-5-21)
--
-- Adds outline-driven summarization on top of existing tables. The harvest
-- LLM extraction logic and CLI surface have been deleted in code, but the
-- underlying tables (questions, decisions, action_items, call_quotes) STAY
-- because the dashboard at dashboard/api/routes.py reads them for the task
-- and decision views described in project CLAUDE.md.
--
-- Net effect:
--   - new tables for outline-driven summarization
--   - new kb_config columns for primary/backup LLM routing
--   - existing tables untouched (dashboard continues to work)
--
-- Pure additive. No renames. No drops. Safe to roll back via reverse migration.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. New tables for the outline-driven summary workflow
-- ---------------------------------------------------------------------------

CREATE TABLE outlines (
    id         SERIAL PRIMARY KEY,
    call_id    INTEGER NOT NULL UNIQUE REFERENCES calls(id) ON DELETE CASCADE,
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_outlines_call_id ON outlines(call_id);

CREATE TABLE meeting_summaries (
    id            SERIAL PRIMARY KEY,
    call_id       INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    outline_id    INTEGER REFERENCES outlines(id) ON DELETE SET NULL,
    content       TEXT NOT NULL,
    model_used    TEXT NOT NULL,
    phi_scrubbed  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_meeting_summaries_call_id ON meeting_summaries(call_id);
CREATE INDEX idx_meeting_summaries_created_at ON meeting_summaries(created_at DESC);

-- ---------------------------------------------------------------------------
-- 2. Primary/backup LLM config
-- ---------------------------------------------------------------------------
-- llm_url and llm_model stay during transition (transcripts.py still reads
-- them for filler-removal). A future cleanup plan can drop them after the
-- transcript path migrates too.

ALTER TABLE kb_config
    ADD COLUMN primary_llm_url      TEXT NOT NULL DEFAULT 'https://openrouter.ai/api/v1',
    ADD COLUMN primary_llm_model    TEXT NOT NULL DEFAULT 'anthropic/claude-opus-4.7',
    ADD COLUMN primary_llm_provider TEXT NOT NULL DEFAULT 'openrouter',
    ADD COLUMN backup_llm_url       TEXT NOT NULL DEFAULT 'https://openrouter.ai/api/v1',
    ADD COLUMN backup_llm_model     TEXT NOT NULL DEFAULT 'google/gemini-3.5-flash',
    ADD COLUMN backup_llm_provider  TEXT NOT NULL DEFAULT 'openrouter';

COMMIT;
