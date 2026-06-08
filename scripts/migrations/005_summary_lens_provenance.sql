-- Migration 005: Summary lens provenance
--
-- The `kb summary --lens <path>` capability lets a lens file dictate both the
-- priming context and the output contract of a generated summary (a recap lens
-- vs an extraction lens vs the default business-meeting template). Record which
-- lens produced a row so re-runs are reproducible and rows are distinguishable.
--
-- Pure additive. NULL = the default business-meeting template (no lens).

BEGIN;

ALTER TABLE meeting_summaries
    ADD COLUMN lens TEXT;

COMMENT ON COLUMN meeting_summaries.lens IS
    'Basename of the lens file that produced this summary (kb summary --lens). NULL = default business-meeting template.';

COMMIT;
