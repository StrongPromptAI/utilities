"""
Central cosine-similarity thresholds for the radar (utilities repo).

Single source of truth for every semantic-match bar the radar applies, so a
value can no longer drift between two hook files — it already had: post-tool
wisdom 0.70 vs prompt wisdom 0.72, in two files, with nothing reconciling them.

Mirrors thj's ``app/semantic_thresholds.py`` consolidation (plan 26-6-4 — one
imported source, a provenance ``Origin:`` block per constant) — but thj's
``lint_semantic_thresholds.py`` scans only the thj repo's ``app/services/**``,
so the radar (a separate repo) needs its own home and its own discipline.

Convention: every constant carries an ``Origin:`` line stating the evidence (or
``uncalibrated``). Surfaces are named explicitly (``PROMPT_`` vs ``POST_TOOL_``)
so a per-distribution difference is VISIBLE here, decidable by a human — not
buried in two files where it reads as a bug.

This module is a strict LEAF (no intra-package imports) so any hook can import
it without a cycle.
"""

from __future__ import annotations

# ── Prompt hook (UserPromptSubmit) — chat-prompt distribution ────────────────
# Origin: the skill radar's calibrated prompt-match bar. WHAT was raised
#   0.65 → 0.72 on 2026-05-13 after SKILL_INJECT_LOG analysis (cluster digests
#   are identifier-soup that embeds broadly; near-threshold matches don't teach
#   anything the routing table doesn't already say). WISDOM holds the same 0.72
#   conservative bar.
PROMPT_WISDOM = 0.72
PROMPT_WHAT = 0.72

# ── Post-tool hook (PostToolUse, error text) — error-string distribution ─────
# Origin: per-distribution tuning — error text favours a code/cluster "what"
#   match over a wisdom narrative. WHAT raised 0.65 → 0.72 on 2026-05-13 (same
#   rationale as the prompt hook).
#   ⚠ VERIFY INTENT: POST_TOOL_WISDOM sits at 0.70, LOWER than PROMPT_WISDOM's
#   0.72, and no comment ever justified the split. It may be deliberate (error
#   strings differ from prompts) or stale (the prompt bar was raised and this one
#   was not). Preserved EXACTLY as found during consolidation (zero behaviour
#   change) — decide and either unify to PROMPT_WISDOM or document the reason.
POST_TOOL_WISDOM = 0.70
POST_TOOL_WHAT = 0.72

# ── Schema corpus (prompt hook) ──────────────────────────────────────────────
# Origin: started at the prompt wisdom bar (0.72) per the schema-corpus plan
#   (thj/26-6-16); calibrate in its Phase 5 off the SCHEMA inject log. A
#   table-name keyword prefilter does most of the precision work, so this cosine
#   bar only separates "mentioned and relevant" from "mentioned in passing".
SCHEMA = 0.72

# ── Protocol corpus (prompt hook) ────────────────────────────────────────────
# Origin: inherited the 0.72 prompt-match bar — UNCALIBRATED for protocol.
#   Unlike schema there is NO keyword prefilter (component_keys are opaque
#   pseudonyms that never appear in a prompt), so this cosine bar is the ONLY
#   precision gate — calibrate off the PROTOCOL inject log once there is real
#   traffic (it may need to rise to hold precision without the prefilter).
PROTOCOL = 0.72

# ── Doctrine registry (prompt hook) ──────────────────────────────────────────
# Origin: doctrine is higher-stakes than skill suggestions, so the bar sits
#   above the 0.72 skill bar. The Phase-1 spec proposed 0.85, but empirical
#   probes against the embed model top out at ~0.82-0.84 even on near-verbatim
#   title repetition (tests/test_precision.py). 0.78 sits above the skill bar,
#   below the empirical ceiling. Re-tune via match_type='auto' rows in
#   session-log.jsonl if auto-fires prove noisy.
DOCTRINE = 0.78

# ── Keyword prefilter semantic confirm (prompt hook, skill matches) ──────────
# Origin: a substring trigger match must ALSO clear this cosine bar vs the
#   prompt, unless the trigger dominates the cleaned prompt (Phase 0a, the
#   2026-05-26 audit) — kills the synthetic-1.00 fires on skill names that
#   double as common English nouns (versioning, implementation, utilities).
PREFILTER_SEMANTIC = 0.65
