-- Migration 006: Call output files
--
-- A discussion (one or more calls) produces deliverables — a job description, a
-- letter, a recap — that today live as loose files in comms/ or an email, whose
-- path you have to remember. This records the path(s) so kb is the index: given
-- a call, you can find what it produced; given a deliverable, which calls fed it.
--
-- Junction, not a column: a deliverable can derive from MULTIPLE calls (a JD
-- built from two back-to-back conversations links to both), and a call can have
-- MANY outputs. ON DELETE CASCADE matches the existing delete_call contract —
-- deleting a call drops its output links automatically (no NO-ACTION dependent
-- to register in delete_call()).
--
-- Scope (deliberate, for now): we store the PATH, not the document body. The
-- file on disk stays the source of truth; kb holds the pointer + a label. A
-- future `kb doc` store can own the content itself — this is the lean first step.

BEGIN;

CREATE TABLE call_outputs (
    id          serial PRIMARY KEY,
    call_id     integer NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    path        text NOT NULL,
    label       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (call_id, path)
);

CREATE INDEX idx_call_outputs_call_id ON call_outputs(call_id);

COMMENT ON TABLE call_outputs IS
    'Deliverables a call produced (JD, letter, recap). Stores the file PATH, not the body; the file on disk is source of truth. Junction so one deliverable can link to multiple calls.';
COMMENT ON COLUMN call_outputs.path IS 'Absolute path to the output file on disk.';
COMMENT ON COLUMN call_outputs.label IS 'Human label for the deliverable (e.g. "JourneyMan role charter"). Optional.';

COMMIT;
