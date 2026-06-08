-- Migration 004: Raw-transcript protocol
--
-- Two standing ingest-protocol upgrades:
--   1. Timestamps are ALWAYS stripped before processing. Per-turn start/end
--      seconds are no longer carried into chunking or stored on call_chunks
--      (the columns remain but are populated NULL going forward). Only the
--      overall call date is kept, on calls.call_date.
--   2. The full diarized RAW TRANSCRIPT (every turn, speaker-attributed,
--      timestamp-free, fillers retained) is ALWAYS stored at ingest on the
--      new calls.raw_transcript column — the verbatim "what was said" record,
--      distinct from the filler-filtered + chunked text used for search.
--
-- Pure additive. No renames. No drops. Existing rows get NULL raw_transcript
-- (backfill by re-ingest if a historical raw transcript is needed).

BEGIN;

ALTER TABLE calls
    ADD COLUMN raw_transcript TEXT;

COMMENT ON COLUMN calls.raw_transcript IS
    'Full diarized transcript: every turn, speaker-attributed, timestamp-free, fillers retained. Stored at ingest (protocol 004). Verbatim record, distinct from chunked call_chunks.';

COMMIT;
