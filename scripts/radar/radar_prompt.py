"""
Claude Code / Codex UserPromptSubmit hook — semantic skill suggestion on prompt intent.

Fires before Claude processes each user message. Embeds the prompt and finds
the closest matching skill chunks in the bifurcated Skill Radar indexes. Surfaces a
skill section at the "perfect moment" — when Claude is about to strategize
on a fix, pick a direction, or answer a domain question.

Design choices (differ from hook.py):
- Higher threshold (0.72) — prompts are chattier than error strings, false
  positives are more annoying because they fire every turn.
- Skip trivial prompts (< 30 chars, sentinel messages) — nothing to retrieve
  against meaningfully.
- Top 1 match only by default — prompts are usually single-intent; two matches
  doubles the noise surface without doubling signal.
- No logging to SKILL_DEBT.md (that file is for error-time coverage gaps).
  DOES log injections to SKILL_INJECT_LOG.md so Outcome C (false positives)
  stays detectable.

Exits silently (code 0) on any failure — never blocks the agent runtime.
"""

import atexit
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Local import — Skill Radar embed client, backed by the utilities ONNX service.
sys.path.insert(0, str(Path(__file__).parent))
from embed_client import embed as _shared_embed, EmbedUnavailable
from event_adapter import PromptEvent, normalize_event
from output_adapter import render_additional_context, render_radar_block
from session_log import _slugify_cwd, append_event as session_log_append, project_log_path
from doctrine_registry import (
    match_doctrine_for_prompt,
    render_doctrine_section,
)
import schema_corpus as sc
import protocol_corpus as pc
import thresholds as th

INDEX_WISDOM_PATH = Path.home() / ".claude/radar_skills_wisdom.json"
INDEX_WHAT_PATH = Path.home() / ".claude/radar_skills_what.json"
HEARTBEAT_PATH = Path.home() / ".claude" / "last-jsonl-write.txt"
QUERY_PREFIX = "search_query: "

# ── Index freshness gate (2026-05-29) ────────────────────────────────────────
# The injected chunk text is a frozen snapshot in radar_skills_wisdom.json built
# by build_index.py. Nothing rebuilt it on skill edits, so a corrected skill kept
# getting injected in its pre-fix form until someone ran build_index.py by hand
# (the weekly launchd job runs radar_harvest.py, NOT build_index.py). This gate
# watches SKILL_REGISTRY.md's mtime — the corpus table-of-contents, and the same
# sentinel build_index.py uses to blow its wisdom cache — and, when it has
# drifted from the value the manifest recorded at the last build, fires a
# NON-BLOCKING background rebuild. The current prompt still serves the existing
# index; the index converges by the next prompt (build_index is incremental, so
# only files whose mtime changed are re-embedded). Silent no-op on any failure.
REGISTRY_PATH = Path.home() / "repo_docs" / "skills" / "SKILL_REGISTRY.md"
MANIFEST_PATH = Path.home() / ".claude" / "radar_skills_manifest.json"
UTILITIES_DIR = Path.home() / "repos" / "utilities"
BUILD_INDEX_SCRIPT = UTILITIES_DIR / "scripts" / "radar" / "build_index.py"
REBUILD_LOCK = Path.home() / ".claude" / "radar_index_rebuild.lock"
REBUILD_LOG = Path.home() / ".claude" / "last-index-rebuild.log"
REBUILD_LOCK_TTL = 600  # seconds — a rebuild is in flight; don't stampede.
# Must exceed the worst case: a SKILL_REGISTRY.md mtime change blows the whole
# wisdom cache and re-embeds all chunks (~2-4 min). Incremental single-file
# rebuilds finish in seconds, but the lock has to cover the full-rebuild case.

# Cosine-match bars live in the central `thresholds` module (single source of
# truth + per-constant Origin provenance): th.PROMPT_WISDOM / th.PROMPT_WHAT
# (bifurcated skill radar), th.SCHEMA, th.PROTOCOL, th.PREFILTER_SEMANTIC.
BUILD_SCHEMA_INDEX_SCRIPT = UTILITIES_DIR / "scripts" / "radar" / "build_schema_index.py"
SCHEMA_REBUILD_LOG = Path.home() / ".claude" / "last-schema-index-rebuild.log"
SCHEMA_REBUILD_LOCK_TTL = 300  # schema index rebuilds in seconds (no full-corpus blow)

BUILD_PROTOCOL_INDEX_SCRIPT = UTILITIES_DIR / "scripts" / "radar" / "build_protocol_index.py"
PROTOCOL_REBUILD_LOG = Path.home() / ".claude" / "last-protocol-index-rebuild.log"
PROTOCOL_REBUILD_LOCK_TTL = 600  # protocol rebuild re-embeds the corpus (~30-60s)

# Top-1 from each dimension: prompts are usually single-intent on each axis.
# Two surfaces firing at once is fine; two within one surface doubles noise.
TOP_PER_DIM = 1

CONTEXT_CHARS = 800
MIN_PROMPT_CHARS = 30

# Minimum-viable-prompt gate (Tier-3 suppression — NOT meaning interpretation):
# a continuation / acknowledgment prompt ("go ahead", "1-yes, 2-no", "let's keep
# going") doesn't need radar amplification — the context is already in the
# conversation. Suppress amplification on these even when they run past
# MIN_PROMPT_CHARS. Failure is cheap + symmetric (a false skip = one un-amplified
# turn; a false fire = a few hundred tokens), which is what licenses a regex here.
# Kept to SAFE leads (rarely start a substantive instruction); risky single
# adjectives (good/great/right/perfect) are deliberately excluded to avoid
# suppressing real prompts that open with them.
SKIP_PATTERNS = re.compile(
    r"^(?:"
    # word-shaped acks need \b so "good" doesn't match "goodness"-style longer words
    r"(?:yes|yeah|yep|yup|no|nope|ok|okay|k|sure|thanks|thank you|ty|"
    r"continue|carry on|go|go ahead|do it|do that|proceed|ship it|send it|"
    r"keep going|keep it up|let'?s (?:go|keep|continue|proceed)|"
    r"sounds good|looks good|lgtm|agreed)\b"
    # numbered answers ("1-yes", "2) no", "3. maybe") — separate alt; NO trailing
    # \b (a ")"/"."/" " after the separator is a non-word char, so \b would fail)
    r"|\d+\s*[-.):]"
    r"|__greeting__|__check_in__"
    r")",
    re.IGNORECASE,
)

# Minimum length for a keyword trigger phrase to avoid false positives
# on short common words that happen to appear in Load When text.
MIN_TRIGGER_LEN = 4

# Two-tier prefilter (Phase 0a, 2026-05-26 audit): a substring match alone
# fires synthetic 1.00 scores on meta-skill names that double as common English
# nouns (`versioning`, `implementation`, `utilities`, `skill-agent-curation`).
# The fix: substring match candidates must ALSO clear a semantic-similarity
# bar against the prompt (th.PREFILTER_SEMANTIC), unless the trigger phrase
# dominates the prompt (e.g. "use gitnexus" — short, intentional invocation).
PREFILTER_DOMINANCE_RATIO = 0.30  # trigger ≥ 30% of cleaned prompt → deterministic

# Prior-injection contamination (Phase 0a): the Skill Radar injects
# <system-reminder> blocks and "Skill Radar — ..." envelopes into the
# conversation. Those reappear in subsequent prompts; the prefilter then
# matches on skill names the radar itself injected — a feedback loop that
# produces the 1.00 false-fires documented in the 2026-05-26 audit.
SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>", re.DOTALL
)
SKILL_RADAR_INJECT_RE = re.compile(
    r"Skill Radar\s+—.*?(?=\n\n\Z|\n\n[A-Z]|\Z)", re.DOTALL
)


def strip_prior_injections(prompt: str) -> str:
    """Remove system-reminder blocks and Skill Radar injection envelopes from
    `prompt` so the keyword prefilter doesn't match on text the radar itself
    injected on a prior turn. The user-typed content is what we want to embed
    against, not the radar's own output bouncing back."""
    cleaned = SYSTEM_REMINDER_RE.sub("", prompt)
    cleaned = SKILL_RADAR_INJECT_RE.sub("", cleaned)
    return cleaned.strip()


def extract_triggers(index: list[dict]) -> list[tuple[str, dict]]:
    """Build (lowercase_phrase, best_chunk) pairs from Load When keywords.

    Extracts:
    1. Quoted phrases from Load When text (e.g., "quick take")
    2. Skill names themselves (e.g., "gitnexus")

    Returns longest-first so "quick take" matches before "quick".
    """
    # Group chunks by skill_name — we'll inject the first chunk per skill
    by_skill: dict[str, dict] = {}
    triggers: dict[str, str] = {}  # phrase → skill_name

    for entry in index:
        sname = entry.get("skill_name", "")
        if sname and sname not in by_skill:
            by_skill[sname] = entry

        load_when = entry.get("load_when", "")
        if not load_when:
            continue

        # Extract quoted phrases: "quick take", "review this", etc.
        for m in re.finditer(r'"([^"]+)"', load_when):
            phrase = m.group(1).strip().lower()
            if len(phrase) >= MIN_TRIGGER_LEN:
                triggers[phrase] = sname

    # Add skill names as triggers
    for sname in by_skill:
        if len(sname) >= MIN_TRIGGER_LEN:
            triggers[sname.lower()] = sname

    # Build (phrase, chunk) pairs, longest-first
    pairs = []
    for phrase, sname in sorted(triggers.items(), key=lambda t: -len(t[0])):
        if sname in by_skill:
            pairs.append((phrase, by_skill[sname]))
    return pairs


def keyword_prefilter(prompt: str, index: list[dict]) -> list[dict] | None:
    """Two-tier prefilter (Phase 0a, 2026-05-26): a substring match against
    skill names or quoted Load When phrases is a STRONG signal but not by
    itself sufficient. Common-English-noun skill names (`versioning`,
    `implementation`, `utilities`) match constantly without clinical relevance.

    Rules:
    1. Strip prior-injection text from the prompt (kills the feedback loop).
    2. Find substring-match candidates on the cleaned prompt.
    3. For each candidate, EITHER the trigger phrase is ≥30% of the cleaned
       prompt (intentional invocation — keep deterministic 1.00) OR semantic
       similarity vs the prompt must be ≥ 0.65 (contextual relevance).
    4. Failing both, drop the candidate. Returns None if nothing survives.

    Failing closed when the embed service is unavailable preserves the radar's
    silent-no-op discipline."""
    cleaned = strip_prior_injections(prompt)
    if not cleaned:
        return None
    cleaned_lower = cleaned.lower()
    cleaned_len = max(1, len(cleaned))
    triggers = extract_triggers(index)

    candidates: list[tuple[str, dict]] = []
    for phrase, chunk in triggers:
        if phrase in cleaned_lower:
            candidates.append((phrase, chunk))
    if not candidates:
        return None

    # Dominance carve-out — if the trigger is most of what the user typed,
    # honor the intent without embedding.
    deterministic: list[dict] = []
    needs_semantic: list[tuple[str, dict]] = []
    for phrase, chunk in candidates:
        if len(phrase) / cleaned_len >= PREFILTER_DOMINANCE_RATIO:
            deterministic.append({"score": 1.0, **chunk})
        else:
            needs_semantic.append((phrase, chunk))

    if not needs_semantic:
        return deterministic

    # Semantic confirm for the rest.
    query_vec = embed(QUERY_PREFIX + cleaned[:1000])
    if not query_vec:
        # Fail closed — return only the deterministic matches (or None).
        return deterministic if deterministic else None

    for phrase, chunk in needs_semantic:
        chunk_emb = chunk.get("embedding")
        if not chunk_emb:
            continue
        sim = dot(query_vec, chunk_emb)
        if sim >= th.PREFILTER_SEMANTIC:
            deterministic.append({"score": sim, **chunk})

    return deterministic if deterministic else None


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def embed(text: str) -> list[float] | None:
    """Single-text embed via shared-svcs.

    Returns None when the service is up but the call failed (503 shedding, etc.) so
    the hook degrades quietly. But propagates EmbedUnavailable — no backend reachable
    at all (neither local ONNX nor Railway) — so main() can hard-fail loudly instead
    of silently losing radar coverage. retries=1 keeps the down-detection snappy."""
    try:
        return _shared_embed([text], timeout=3.0, retries=1)[0]
    except EmbedUnavailable:
        raise
    except Exception:
        return None


def load_index(path: Path) -> list[dict]:
    """Load one dimension's index. Empty list on any failure (silent no-op
    discipline — never block Claude Code on a missing/malformed cache)."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def index_stale_vs_registry() -> bool:
    """True when SKILL_REGISTRY.md's current mtime differs from the value the
    manifest recorded at the last build — i.e. the skill corpus changed since
    the index was built. Mirrors build_index.py's own registry-mtime cache-blow
    signal so the two stay consistent. Any error → False (silent no-op)."""
    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
        recorded = manifest.get("files", {}).get("wisdom", {}).get(str(REGISTRY_PATH))
        if recorded is None:
            return False
        return REGISTRY_PATH.stat().st_mtime != recorded
    except Exception:
        return False


def trigger_background_rebuild() -> None:
    """Fire build_index.py detached and lock-guarded so concurrent prompts don't
    stampede a rebuild that's already in flight. Never blocks the prompt; never
    raises. The lock is time-based (REBUILD_LOCK_TTL) so a crashed build self-heals
    on the next stale prompt, and build_index updates the manifest's recorded
    registry mtime on success — which clears index_stale_vs_registry() naturally."""
    try:
        if REBUILD_LOCK.exists():
            if (time.time() - REBUILD_LOCK.stat().st_mtime) < REBUILD_LOCK_TTL:
                return  # a rebuild is already running
        REBUILD_LOCK.write_text(
            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z") + "\n"
        )
        uv = "/opt/homebrew/bin/uv"
        if not Path(uv).exists():
            import shutil
            uv = shutil.which("uv") or ""
        if not uv:
            return
        logf = open(REBUILD_LOG, "a")
        subprocess.Popen(
            [uv, "run", "--project", str(UTILITIES_DIR), "python", str(BUILD_INDEX_SCRIPT)],
            cwd=str(UTILITIES_DIR),
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        pass


def _touch_heartbeat() -> None:
    """Phase 4.5 observability — stamp last-jsonl-write.txt on every JSONL write."""
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(
            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
            + "\n"
        )
    except Exception:
        pass


HARVEST_HEARTBEAT_PATH = Path.home() / ".claude" / "last-skill-harvest.txt"
HARVEST_STALE_DAYS = 7  # nag threshold
QUEUE_PATH = Path.home() / "repo_docs" / "skills" / "SKILL_QUEUE.md"
QUEUE_BLOAT_THRESHOLD = 20  # nag when queue exceeds this
QUEUE_STALE_DAYS = 14  # Phase 4.5 — nag when queue hasn't changed in N days
                       # (per GLM 5.1 quick-take 2026-05-26: "pipeline runs
                       #  but learns nothing" failure mode)


# When the per-project radar log accrues this many `radar_turn_aggregate` rows,
# there's enough data to read the enhancement-ratio distribution and revisit
# Phase 2 (coverage) of the prompt-amplifier plan. A data-threshold trigger that
# surfaces a decision — the radar's own purpose, dogfooded.
RADAR_COVERAGE_THRESHOLD = 50


def check_radar_coverage_review(cwd: str | None = None) -> str | None:
    """Phase 2 trigger (plan 26-6-16 radar-prompt-amplifier-spry): once the radar
    log reaches RADAR_COVERAGE_THRESHOLD amplifier turns, surface a reminder to
    read `radar_ratio_report.py` and revisit coverage. Fires once per session
    (the lifecycle-nag demotion handles cadence) until dismissed via a marker.
    Counts only this project's log (where the data is accruing). Silent no-op on
    any error."""
    try:
        cwd = cwd or os.getcwd()
        slug = _slugify_cwd(cwd)
        dismiss = Path.home() / ".claude" / "projects" / slug / "radar-phase2-dismissed.txt"
        if dismiss.exists():
            return None
        log_path = project_log_path(cwd)
        if not log_path.exists():
            return None
        count = sum(
            1 for line in log_path.read_text(errors="replace").splitlines()
            if '"event_type": "radar_turn_aggregate"' in line or '"event_type":"radar_turn_aggregate"' in line
        )
        if count < RADAR_COVERAGE_THRESHOLD:
            return None
        return (
            f"Radar log hit {count} amplifier turns (≥{RADAR_COVERAGE_THRESHOLD}) — enough data to "
            "read the enhancement-ratio distribution and revisit **Phase 2 (coverage)** of "
            "`symlink_docs/plans/26-6-16_radar-prompt-amplifier-spry.md`.\n"
            "  Read it:  `uv run --project ~/repos/utilities python "
            "~/repos/utilities/scripts/radar/radar_ratio_report.py`\n"
            f"  Dismiss:  `touch {dismiss}`"
        )
    except Exception:
        return None


def check_harvest_overdue() -> str | None:
    """Phase 4 fallback path — return a nag notice if the weekly harvest
    routine hasn't fired in HARVEST_STALE_DAYS. Reads ~/.claude/last-skill-harvest.txt;
    missing OR stale → emit notice. Returns None when harvest is fresh."""
    try:
        if not HARVEST_HEARTBEAT_PATH.exists():
            return (
                "Skill harvest never run — `~/.claude/last-skill-harvest.txt` "
                "is missing. Run manually:\n"
                "  `uv run --project ~/repos/utilities python "
                "~/repos/utilities/scripts/radar/radar_harvest.py`\n"
                "Or set up the weekly scheduled routine via `/schedule`."
            )
        ts_str = HARVEST_HEARTBEAT_PATH.read_text().strip()
        last_dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S%z")
        age = (datetime.now(timezone.utc).astimezone() - last_dt).days
        if age > HARVEST_STALE_DAYS:
            return (
                f"Skill harvest overdue — last run {age}d ago (threshold "
                f"{HARVEST_STALE_DAYS}d). Run `uv run --project ~/repos/utilities "
                "python ~/repos/utilities/scripts/radar/radar_harvest.py` or "
                "accept the drift. Persistent staleness means session learnings "
                "aren't being surfaced to SKILL_QUEUE.md."
            )
        return None
    except Exception:
        return None


def check_queue_zero_candidates() -> str | None:
    """Phase 4.5 — nag when SKILL_QUEUE.md hasn't changed in QUEUE_STALE_DAYS
    even though harvest is running. The "pipeline looks healthy but learns
    nothing" failure mode flagged by GLM 5.1 quick-take 2026-05-26."""
    try:
        if not QUEUE_PATH.exists():
            return None
        # Use file mtime as the "last change" proxy. Comparing against
        # last-skill-harvest.txt would miss the case where harvest runs but
        # writes no candidates (which IS the failure mode we want to catch).
        mtime = datetime.fromtimestamp(QUEUE_PATH.stat().st_mtime, tz=timezone.utc).astimezone()
        age = (datetime.now(timezone.utc).astimezone() - mtime).days
        if age <= QUEUE_STALE_DAYS:
            return None
        # Also confirm harvest IS running (so we don't double-nag on overdue).
        if HARVEST_HEARTBEAT_PATH.exists():
            harvest_ts = datetime.strptime(
                HARVEST_HEARTBEAT_PATH.read_text().strip(),
                "%Y-%m-%dT%H:%M:%S%z",
            )
            harvest_age = (datetime.now(timezone.utc).astimezone() - harvest_ts).days
            if harvest_age > HARVEST_STALE_DAYS:
                # Harvest itself is stale — check_harvest_overdue handles this.
                return None
        return (
            f"SKILL_QUEUE.md unchanged for {age}d — harvest is running but "
            "producing zero new candidates. This is the \"pipeline looks "
            "healthy but learns nothing\" failure: either briefs aren't being "
            "authored, or candidates aren't crossing the dedupe threshold. "
            "Spot-check the last session-log.jsonl rows + most recent brief."
        )
    except Exception:
        return None


def check_brief_pending(cwd: str | None = None) -> str | None:
    """Phase 2 — return a brief-author notice if the Stop hook from a prior
    session set a `brief-pending.txt` marker AND no brief has been authored
    since the timestamp inside it.

    The marker contains an ISO timestamp written at session-end. The marker
    is cleared (and None returned) when any file in `<cwd>/symlink_docs/briefs/`
    has an mtime after the marker's timestamp — that's our "brief was written"
    detection. Returns the notice text on pending; None when nothing's owed.

    Silent no-op discipline preserved — any error returns None."""
    try:
        cwd = cwd or os.getcwd()
        slug = _slugify_cwd(cwd)
        marker = Path.home() / ".claude" / "projects" / slug / "brief-pending.txt"
        if not marker.exists():
            return None

        pending_ts_str = marker.read_text().strip()
        if not pending_ts_str:
            return None

        pending_dt = datetime.strptime(pending_ts_str, "%Y-%m-%dT%H:%M:%S%z")
        pending_epoch = pending_dt.timestamp()

        # Find the briefs/ dir for this project.
        briefs_dir = Path(cwd) / "symlink_docs" / "briefs"
        if not briefs_dir.exists():
            briefs_dir = Path(cwd) / "briefs"  # fallback for non-symlinked projects

        # Brief lanes (introduced 2026-05-27): sessions/ is the dual-radar
        # harvest input — that's where a fresh brief lands and where this
        # marker-clearing check should look first. Fall through to flat
        # briefs/ for projects still on the pre-lanes layout.
        scan_dirs = []
        sessions_dir = briefs_dir / "sessions"
        if sessions_dir.exists():
            scan_dirs.append(sessions_dir)
        if briefs_dir.exists():
            scan_dirs.append(briefs_dir)

        for scan_dir in scan_dirs:
            for brief_file in scan_dir.iterdir():
                if brief_file.name == "_TEMPLATE.md":
                    continue
                if brief_file.is_file() and brief_file.stat().st_mtime > pending_epoch:
                    # A brief was authored after the pending timestamp; clear
                    # both the brief-pending marker AND the sibling
                    # doctrine-catches-pending marker (the addendum is bundled
                    # with the brief-pending notice; clearing one without the
                    # other leaves a stale file).
                    try:
                        marker.unlink()
                    except Exception:
                        pass
                    try:
                        doctrine_marker = Path.home() / ".claude" / "projects" / slug / "doctrine-catches-pending.txt"
                        if doctrine_marker.exists():
                            doctrine_marker.unlink()
                    except Exception:
                        pass
                    return None

        # Phase 2 — append a doctrine-catches addendum when the sibling
        # marker is present (Stop hook saw ≥1 doctrine_match row with
        # outcome="caught_in_review" this session). The harvest reads the
        # brief's "Doctrine Violations Caught in Review" section to promote
        # candidates into SKILL_QUEUE.md § Doctrine Candidates.
        doctrine_addendum = ""
        try:
            doctrine_marker = Path.home() / ".claude" / "projects" / slug / "doctrine-catches-pending.txt"
            if doctrine_marker.exists():
                content = doctrine_marker.read_text().strip()
                count = ""
                if "\t" in content:
                    _, count = content.split("\t", 1)
                doctrine_addendum = (
                    f"\n\n**Doctrine catches this session** ({count or 'see JSONL'}): "
                    "You caught doctrine violations in review — add them to the "
                    "brief under \"## Doctrine Violations Caught in Review\" so "
                    "the next harvest surfaces them for promotion in "
                    "SKILL_QUEUE.md § Doctrine Candidates."
                )
        except Exception:
            pass

        # No brief authored — emit the notice. Suggest the sessions/ lane if
        # it exists (the dual-radar harvest input); fall back to flat briefs/
        # for projects on the pre-lanes layout.
        suggested_path = sessions_dir if sessions_dir.exists() else briefs_dir
        return (
            "Session brief pending — the Stop hook from a prior session "
            f"({pending_ts_str}) flagged this project as owing a brief.\n"
            f"\n"
            f"Author one at `{suggested_path}/YY-M-D_<topic>.md` covering this "
            "session's pivots, gotchas, and skill candidates. The template is "
            f"`{briefs_dir}/_TEMPLATE.md`.\n"
            f"\n"
            "The brief is the harvest's input — without it, the weekly skill "
            "harvest has nothing per-session to read from. Once authored, the "
            "marker auto-clears (next prompt will not re-notify).\n"
            f"\n"
            "See ~/.claude/CLAUDE.md § \"Skill Lifecycle\" for the full loop."
            + doctrine_addendum
        )
    except Exception:
        return None


def log_skill_inject(prompt_text: str, matches: list[dict]) -> None:
    """Record a prompt-hook injection as a JSONL row.

    Replaces the pre-2026-05-26 markdown SKILL_INJECT_LOG.md writes (archived).
    Sets event_type='prompt_match' and includes the top skill_match. The
    legacy [prompt] marker is preserved in the event_type, not the timestamp."""
    if not matches:
        return
    best = max(matches, key=lambda m: m.get("score", 0.0))
    skill_match_row = {
        "score": round(best["score"], 3),
        "skill": best.get("skill_name", best.get("name", "?")),
        "header": best.get("header", ""),
    }
    if session_log_append(
        event_type="prompt_match",
        tool="UserPromptSubmit",
        command_or_context=prompt_text[:400],
        skill_match=skill_match_row,
        outcome="injected",
    ):
        _touch_heartbeat()


def log_doctrine_auto_inject(prompt_text: str, rule: dict) -> None:
    """Record an auto-detected doctrine match as a JSONL row.

    Distinguishing fields from the manual record-session-event path:
    - `match_type: "auto"` (the radar fired vs human-judged)
    - `outcome: "injected"` (not yet caught_in_review / violated_in_code)

    The harvest reads only `match_type="manual"` rows with
    `outcome="caught_in_review"` into the doctrine queue — auto-fires are
    observability signal, not promotion candidates. A reviewer who later
    judges the fire as a real catch records it manually via
    record-session-event."""
    if not rule:
        return
    score = rule.get("score", 0.0)
    extra = {
        "rule": (rule.get("title") or "")[:600],
        "rule_source": (rule.get("source") or "")[:400],
        "touchpoint": [],
        "match_type": "auto",
        "evidence": f"prompt_hook semantic match (score {score:.3f})",
        "receipt": (rule.get("receipt") or "").split("\n", 1)[0][:600],
        # Field name aligned with schema_match / protocol_match rows (both use
        # "score") — doctrine was the lone outlier on "match_score", which left
        # `r.get("score")` NULL on every doctrine row. No consumer read
        # match_score; the rename is back-compat-safe.
        "score": round(score, 3),
    }
    if session_log_append(
        event_type="doctrine_match",
        tool="UserPromptSubmit",
        command_or_context=prompt_text[:400],
        outcome="injected",
        extra=extra,
        dedupe_parts=(extra["rule"], "injected"),
    ):
        _touch_heartbeat()


# ── Schema Radar (plan thj/26-6-16) ──────────────────────────────────────────
# A third corpus alongside wisdom + what: per-repo schema.sql COMMENT chunks.
# Fires INDEPENDENTLY of the skill match (like doctrine) when cwd is a repo
# registered in ~/.claude/radar_schema_repos.json. Source is the committed
# schema.sql (durable semantic context, no DATABASE_URL needed); the freshness
# gate mirrors the skill index's registry-mtime gate, watching schema.sql's
# mtime (its every-push rewrite keeps the index fresh for free).

def load_schema_index(slug: str) -> dict | None:
    """Load a repo's schema index. None on any failure (silent no-op discipline)."""
    try:
        return json.loads(sc.index_path(slug).read_text())
    except Exception:
        return None


def schema_index_stale(slug: str, cfg: dict) -> bool:
    """True when schema.sql's current mtime differs from the value the manifest
    recorded at the last build. Any error → False (silent no-op)."""
    try:
        schema_path = sc.expand(cfg.get("schema_sql", ""))
        manifest = json.loads(sc.manifest_path(slug).read_text())
        recorded = manifest.get("schema_mtime")
        if recorded is None:
            return False
        return Path(schema_path).stat().st_mtime != recorded
    except Exception:
        return False


def trigger_schema_rebuild(slug: str) -> None:
    """Fire build_schema_index.py --repo <slug> detached + lock-guarded. Never
    blocks the prompt; never raises. Mirrors trigger_background_rebuild."""
    try:
        lock = Path.home() / ".claude" / f"radar_schema_rebuild_{slug}.lock"
        if lock.exists() and (time.time() - lock.stat().st_mtime) < SCHEMA_REBUILD_LOCK_TTL:
            return
        lock.write_text(
            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z") + "\n"
        )
        uv = "/opt/homebrew/bin/uv"
        if not Path(uv).exists():
            import shutil
            uv = shutil.which("uv") or ""
        if not uv:
            return
        logf = open(SCHEMA_REBUILD_LOG, "a")
        subprocess.Popen(
            [uv, "run", "--project", str(UTILITIES_DIR), "python",
             str(BUILD_SCHEMA_INDEX_SCRIPT), "--repo", slug],
            cwd=str(UTILITIES_DIR),
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        pass


def match_schema(prompt: str, index: dict) -> dict | None:
    """Top-1 schema chunk over th.SCHEMA by pure semantic similarity — NO
    table-name-token gate.

    The exact-name prefilter was removed 2026-06-16: a concept-only sweep
    (questions asked the way a dev actually asks — no snake_case table name)
    showed it surfaced the right table for **0/53** natural questions, and fired
    the WRONG table 11× (almost all `patient`, a common word semantically near
    most patient-domain prompts). Real questions almost never type the table
    name, so the gate that required it was the dominant recall limiter. Pure
    cosine recovered 30/53. The name token is deliberately NOT reintroduced as a
    boost: boosting a common single-word name (`patient`, `session`, `alert`)
    would amplify the same wrong-fire pathology.

    The remaining wrong-fires (a broad table edging out the specific one) are
    suppressed by a top-1-vs-top-2 MARGIN (th.SCHEMA_MARGIN): fire only if the
    winner clears the bar AND beats #2 by the margin — an ambiguous near-tie
    stays silent rather than confidently injecting the wrong table's comment.
    Cut concept wrong-fires 16 → 5 at margin 0.02. None on no match / embed
    failure / ambiguous near-tie (silent no-op).
    See thj/plans/26-6-16_schema-comment-enrichment-agent.md § Folded-in findings."""
    chunks = index.get("chunks", [])
    if not chunks:
        return None
    cleaned = strip_prior_injections(prompt)
    if not cleaned:
        return None
    query_vec = embed(QUERY_PREFIX + cleaned[:1000])
    if not query_vec:
        return None
    scored = sorted(
        ({"score": dot(query_vec, c["embedding"]), **c} for c in chunks if c.get("embedding")),
        key=lambda x: x["score"],
        reverse=True,
    )
    if not scored or scored[0]["score"] < th.SCHEMA:
        return None
    if len(scored) > 1 and (scored[0]["score"] - scored[1]["score"]) < th.SCHEMA_MARGIN:
        return None  # ambiguous near-tie — don't confidently inject the wrong table
    return scored[0]


def render_schema_section(repo: str, match: dict) -> str:
    """Render a schema-comment match as a shared provenance block (plan
    thj/26-6-16 Phase 1). `trust="cached:verify-vs-live"` carries what the old
    "committed snapshot; live `\\d+` is the oracle" prose said — the schema.sql
    dump is a cached snapshot to verify against the live DB for residency-
    critical calls. The match score stays retrieval-side (not rendered)."""
    body = f"{match['table']}\n---\n{match['text'][:CONTEXT_CHARS]}"
    return render_radar_block(
        body,
        source=f"schema:{repo}/schema.sql",
        trust="cached:verify-vs-live",
    )


def log_schema_inject(prompt_text: str, repo: str, match: dict) -> None:
    """Record a schema-match injection as a JSONL row (Phase 5 calibration input)."""
    try:
        if session_log_append(
            event_type="schema_match",
            tool="UserPromptSubmit",
            command_or_context=prompt_text[:400],
            outcome="injected",
            extra={
                "repo": repo,
                "table": match.get("table", "?"),
                "score": round(match.get("score", 0.0), 3),
            },
        ):
            _touch_heartbeat()
    except Exception:
        pass


# ── Protocol Radar (plan thj/26-6-16 Phase 2) ────────────────────────────────
# Ambient protocol_component awareness: surfaces talking points, coaching voice,
# and triage thresholds where the chat-pathway truth lives. The index is built
# from live Postgres by build_protocol_index.py; the hot path NEVER queries the
# DB. Freshness is a fact-assertion — `trust="live-oracle"` is emitted ONLY when
# the independent watermark accessor (written by the thj promote hook, step 6)
# confirms the index matches live. Absent/mismatched accessor → fail-closed
# `live:unverified` (the trust-class gate refusing to assert an unchecked fact).

def load_protocol_index(slug: str) -> dict | None:
    """Load a repo's protocol index. None on any failure (silent no-op)."""
    try:
        return json.loads(pc.index_path(slug).read_text())
    except Exception:
        return None


def protocol_accessor_path(slug: str) -> Path:
    """The independent live-watermark accessor — written by the thj promote hook
    (step 6), read here. Deliberately NOT written by the build (build-seeding
    would make `verified` always-True by construction, defeating the gate)."""
    return Path.home() / ".claude" / f"radar_protocol_watermark_{slug}.json"


def read_protocol_accessor(slug: str) -> dict | None:
    try:
        return json.loads(protocol_accessor_path(slug).read_text())
    except Exception:
        return None


def read_protocol_manifest_watermark(slug: str):
    """The watermark the index was last CONFIRMED-current at — read from the
    MANIFEST, not the index file. The builder's idempotent no-op path (content
    unchanged but a promote advanced the watermark) updates the manifest watermark
    WITHOUT rewriting the large index file, so the index file's build-time
    watermark lags. The freshness gate must read the manifest (the freshness
    record), matching the schema corpus's manifest-based gate. None on error."""
    try:
        return json.loads(pc.manifest_path(slug).read_text()).get("watermark")
    except Exception:
        return None


def protocol_freshness(slug: str) -> tuple[bool, bool]:
    """Return (stale, verified) from the EXTERNAL watermark check.

    `verified=True` ONLY when the independent accessor (written by the thj
    promote hook) matches the manifest's confirmed-current watermark — the
    external check the trust-class gate requires. Absent accessor OR manifest →
    cannot verify → (False, False), fail-closed (the block emits `live:unverified`,
    never an unchecked `live-oracle`). Accessor present but != manifest →
    (True, False): stale → trigger a rebuild, and stay unverified this turn.
    Silent no-op on any error."""
    try:
        accessor = read_protocol_accessor(slug)
        if accessor is None:
            return (False, False)
        manifest_wm = read_protocol_manifest_watermark(slug)
        if manifest_wm is None:
            return (False, False)
        if accessor == manifest_wm:
            return (False, True)
        return (True, False)
    except Exception:
        return (False, False)


def trigger_protocol_rebuild(slug: str) -> None:
    """Fire build_protocol_index.py --repo <slug> detached + lock-guarded. Needs
    DATABASE_URL in the inherited env; if absent the rebuild exits 2 gracefully
    and the prompt serves the existing index. Never blocks; never raises.
    (The authoritative, DB-having rebuild is the thj promote hook — step 6; this
    is the best-effort hot-path fallback.)"""
    try:
        lock = Path.home() / ".claude" / f"radar_protocol_rebuild_{slug}.lock"
        if lock.exists() and (time.time() - lock.stat().st_mtime) < PROTOCOL_REBUILD_LOCK_TTL:
            return
        lock.write_text(
            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z") + "\n"
        )
        uv = "/opt/homebrew/bin/uv"
        if not Path(uv).exists():
            import shutil
            uv = shutil.which("uv") or ""
        if not uv:
            return
        logf = open(PROTOCOL_REBUILD_LOG, "a")
        subprocess.Popen(
            [uv, "run", "--project", str(UTILITIES_DIR), "python",
             str(BUILD_PROTOCOL_INDEX_SCRIPT), "--repo", slug],
            cwd=str(UTILITIES_DIR),
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        pass


def match_protocol(prompt: str, index: dict) -> dict | None:
    """Top-1 protocol chunk over th.PROTOCOL — PURELY semantic (no keyword
    prefilter; component_keys are opaque and never appear in a prompt). None on
    no match or embed failure (silent no-op)."""
    chunks = index.get("chunks", [])
    if not chunks:
        return None
    cleaned = strip_prior_injections(prompt)
    if not cleaned:
        return None
    query_vec = embed(QUERY_PREFIX + cleaned[:1000])
    if not query_vec:
        return None
    best = None
    best_score = -1.0
    for c in chunks:
        emb = c.get("embedding")
        if not emb:
            continue
        s = dot(query_vec, emb)
        if s > best_score:
            best_score = s
            best = c
    if best and best_score >= th.PROTOCOL:
        return {"score": best_score, **best}
    return None


def render_protocol_section(match: dict, verified: bool) -> str:
    """Render via the shared provenance block. `trust="live-oracle"` is gated on
    `verified` — the gate downgrades to `live:unverified` when the external
    watermark check hasn't confirmed freshness."""
    section = f" § {match['section']}" if match.get("section") else ""
    title = match.get("title") or match["component_key"]
    body = f"{title}{section}\n---\n{match['text'][:CONTEXT_CHARS]}"
    return render_radar_block(
        body,
        source=f"protocol:{match['component_key']}",
        trust="live-oracle",
        verified=verified,
    )


def log_protocol_inject(prompt_text: str, repo: str, match: dict, verified: bool) -> None:
    """Record a protocol-match injection as a JSONL row (calibration input)."""
    try:
        if session_log_append(
            event_type="protocol_match",
            tool="UserPromptSubmit",
            command_or_context=prompt_text[:400],
            outcome="injected",
            extra={
                "repo": repo,
                "component_key": match.get("component_key", "?"),
                "section": match.get("section"),
                "score": round(match.get("score", 0.0), 3),
                "verified": verified,
            },
        ):
            _touch_heartbeat()
    except Exception:
        pass


def _nag_state_path() -> Path:
    """Per-project file recording which lifecycle nag last fired for which
    session (or day). Lives beside the session log."""
    return Path.home() / ".claude" / "projects" / _slugify_cwd(os.getcwd()) / "radar-nag-state.json"


def nag_already_fired(key: str, session_token: str) -> bool:
    """Phase 1 (plan 26-6-16 radar-prompt-amplifier-spry): a lifecycle nag is
    TRUE every turn until its condition resolves, so it must not RE-fire every
    turn — it crowds the substantive corpora out of the prompt budget (doctrine
    principle #3). Fire once per session (or per day if no session id), then
    stay silent until the condition is acted on or a new session starts."""
    try:
        p = _nag_state_path()
        if not p.exists():
            return False
        return json.loads(p.read_text()).get(key) == session_token
    except Exception:
        return False  # fail open — a missed suppression just nags once more, never blocks


def record_nag_fired(key: str, session_token: str) -> None:
    try:
        p = _nag_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        st = {}
        if p.exists():
            try:
                st = json.loads(p.read_text())
            except Exception:
                st = {}
        st[key] = session_token
        p.write_text(json.dumps(st))
    except Exception:
        pass


def log_turn_aggregate(prompt: str, emitted: list[dict]) -> None:
    """Phase 0 (plan thj/26-6-16 radar-prompt-amplifier-spry): ONE observation
    row per turn — the prompt-amplifier enhancement ratio (radar bytes vs typed
    bytes), block count, substance-vs-nag split, surfaces, collision count, and
    per-injection evidence. Logging ONLY — writes to session-log.jsonl, never to
    stdout (stdout is the agent's injected context). Registered via atexit so it
    fires once per turn even when the hook takes an early sys.exit(0). Silent
    no-op on any error (never block Claude Code)."""
    try:
        if not emitted and len(prompt or "") < MIN_PROMPT_CHARS:
            return  # nothing fired and not a real prompt turn — don't log noise
        typed = len(prompt or "")
        radar_bytes = sum(int(e.get("bytes", 0)) for e in emitted)
        substance = [e for e in emitted if e.get("kind") == "substance"]
        nags = [e for e in emitted if e.get("kind") == "nag"]
        substance_surfaces = sorted({e["surface"] for e in substance})
        session_log_append(
            event_type="radar_turn_aggregate",
            command_or_context=(prompt or "")[:200],
            outcome="recorded",
            extra={
                "typed_bytes": typed,
                "radar_bytes": radar_bytes,
                "ratio": round(radar_bytes / max(typed, 1), 3),
                "n_blocks": len(emitted),
                "n_substance": len(substance),
                "n_nag": len(nags),
                "n_collision": len(substance_surfaces),  # distinct substance corpora firing together
                "surfaces": sorted({e["surface"] for e in emitted}),
                "injections": [
                    {"surface": e.get("surface"), "kind": e.get("kind"),
                     "bytes": int(e.get("bytes", 0)), "score": e.get("score"),
                     "ref": e.get("ref")}
                    for e in emitted
                ],
            },
            # high-entropy dedupe so every turn logs (metrics row, not an event to suppress)
            dedupe_parts=("radar_turn_aggregate", (prompt or "")[:80],
                          str(radar_bytes), str(len(emitted)), str(int(time.time()))),
        )
    except Exception:
        pass


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        sys.exit(0)

    event = normalize_event(payload)
    if not isinstance(event, PromptEvent):
        sys.exit(0)

    prompt = event.prompt.strip()
    if len(prompt) < MIN_PROMPT_CHARS:
        sys.exit(0)

    if SKIP_PATTERNS.match(prompt):
        sys.exit(0)

    # Phase 0 (prompt-amplifier instrumentation): accumulate every block this turn
    # emits; an atexit flush writes one aggregate metrics row even on early exit.
    # Pure observation — mutated by reference at each emission site below.
    emitted: list[dict] = []
    atexit.register(log_turn_aggregate, prompt, emitted)

    # Phase 2 + Phase 4 + Phase 4.5 — lifecycle nag notices fire INDEPENDENTLY
    # of the skill-match injection. They render before the skill match so
    # session-discipline signals lead. Each can return None to skip.
    # Phase 1 — a lifecycle nag fires at most ONCE per session (it stays true
    # every turn, but re-emitting it every turn taxes the prompt budget). Key on
    # the session id when present, else the calendar day. Demotes cadence only —
    # the obligation stands and re-surfaces next session until acted on.
    session_token = (
        str(payload.get("session_id") or payload.get("sessionId") or "").strip()
        or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    )
    lifecycle_notices: list[str] = []
    for notice_key, notice_fn in (
        ("brief_pending", check_brief_pending),
        ("harvest_overdue", check_harvest_overdue),
        ("queue_zero", check_queue_zero_candidates),
        ("radar_phase2", check_radar_coverage_review),
    ):
        notice = notice_fn()
        if not notice:
            continue
        if nag_already_fired(notice_key, session_token):
            continue  # already nagged this session — don't crowd substance
        record_nag_fired(notice_key, session_token)
        lifecycle_notices.append(notice)

    if lifecycle_notices:
        combined = "\n\n---\n\n".join(lifecycle_notices)
        print(render_additional_context(
            combined,
            hook_event_name=event.hook_event_name,
            runtime=event.runtime,
        ))
        for _notice in lifecycle_notices:
            emitted.append({"surface": "lifecycle", "kind": "nag", "bytes": len(_notice)})

    # Phase 1 — doctrine match runs INDEPENDENTLY of the skill radar so a
    # prompt can fire both surfaces. Doctrine is higher-stakes (threshold
    # 0.85 vs skill's 0.72) and renders BEFORE the skill section so the
    # architectural constraint leads.
    doctrine_rule = match_doctrine_for_prompt(prompt)
    if doctrine_rule:
        log_doctrine_auto_inject(prompt, doctrine_rule)
        doctrine_section = render_doctrine_section(doctrine_rule)
        print(render_additional_context(
            doctrine_section,
            hook_event_name=event.hook_event_name,
            runtime=event.runtime,
        ))
        emitted.append({"surface": "doctrine", "kind": "substance", "bytes": len(doctrine_section),
                        "score": doctrine_rule.get("score") if isinstance(doctrine_rule, dict) else None,
                        "ref": doctrine_rule.get("rule") if isinstance(doctrine_rule, dict) else None})

    # Schema Radar (plan thj/26-6-16) — fires INDEPENDENTLY of the skill match,
    # like doctrine, so a prompt naming a table gets its comment even when no
    # skill chunk matches. Only engages when cwd is a registered schema repo.
    # The freshness gate watches schema.sql's mtime (every-push rewrite keeps it
    # fresh); the current prompt serves the existing index, converging next turn.
    try:
        schema_slug, schema_cfg = sc.repo_for_cwd(os.getcwd())
        if schema_slug and schema_cfg:
            if schema_index_stale(schema_slug, schema_cfg):
                trigger_schema_rebuild(schema_slug)
            schema_idx = load_schema_index(schema_slug)
            if schema_idx:
                schema_match = match_schema(prompt, schema_idx)
                if schema_match:
                    log_schema_inject(prompt, schema_slug, schema_match)
                    schema_section = render_schema_section(schema_slug, schema_match)
                    print(render_additional_context(
                        schema_section,
                        hook_event_name=event.hook_event_name,
                        runtime=event.runtime,
                    ))
                    emitted.append({"surface": "schema", "kind": "substance", "bytes": len(schema_section),
                                    "score": schema_match.get("score"), "ref": schema_match.get("table")})
    except Exception:
        pass

    # Protocol Radar (plan thj/26-6-16 Phase 2) — fires INDEPENDENTLY, like
    # schema/doctrine. Reuses the cwd→repo resolver; engages when a protocol
    # index exists for the repo. Freshness is the EXTERNAL watermark check:
    # trust="live-oracle" only when the accessor (thj promote hook) confirms the
    # index matches live, else fail-closed "live:unverified". No DB in the hot
    # path; a stale watermark fires a best-effort background rebuild.
    try:
        proto_slug, _ = sc.repo_for_cwd(os.getcwd())
        if proto_slug:
            proto_idx = load_protocol_index(proto_slug)
            if proto_idx:
                stale, verified = protocol_freshness(proto_slug)
                if stale:
                    trigger_protocol_rebuild(proto_slug)
                proto_match = match_protocol(prompt, proto_idx)
                if proto_match:
                    log_protocol_inject(prompt, proto_slug, proto_match, verified)
                    proto_section = render_protocol_section(proto_match, verified)
                    print(render_additional_context(
                        proto_section,
                        hook_event_name=event.hook_event_name,
                        runtime=event.runtime,
                    ))
                    emitted.append({"surface": "protocol", "kind": "substance", "bytes": len(proto_section),
                                    "score": proto_match.get("score"), "ref": proto_match.get("component_key")})
    except Exception:
        pass

    # Index freshness gate (2026-05-29): if the skill corpus changed since the
    # index was built (SKILL_REGISTRY.md mtime drift vs the manifest), fire a
    # NON-BLOCKING background rebuild. We still serve the current index this turn
    # — it converges by the next prompt. Never blocks; failures no-op silently.
    try:
        if index_stale_vs_registry():
            trigger_background_rebuild()
    except Exception:
        pass

    wisdom_idx = load_index(INDEX_WISDOM_PATH)
    what_idx = load_index(INDEX_WHAT_PATH)
    if not wisdom_idx and not what_idx:
        sys.exit(0)

    # Tier 1: deterministic keyword match — fires on either dimension.
    # Search the union; if the matched chunk is in the what index, it goes
    # to the "what" surface, otherwise wisdom. Score is 1.0 either way.
    matches_by_dim: dict[str, list[dict]] = {"wisdom": [], "what": []}
    try:
        kw_matches = keyword_prefilter(prompt, wisdom_idx + what_idx)
        if kw_matches:
            # Bucket each match by which index it came from
            what_paths = {(e.get("file_path"), e.get("skill_name")) for e in what_idx}
            for m in kw_matches:
                key = (m.get("file_path"), m.get("skill_name"))
                dim = "what" if key in what_paths else "wisdom"
                matches_by_dim[dim].append(m)
        else:
            # Tier 2: semantic — single embed, score against each index, apply
            # per-dimension threshold, take top-N from each.
            query_vec = embed(QUERY_PREFIX + prompt[:1000])
            if not query_vec:
                sys.exit(0)

            for dim, idx, threshold in (
                ("wisdom", wisdom_idx, th.PROMPT_WISDOM),
                ("what", what_idx, th.PROMPT_WHAT),
            ):
                if not idx:
                    continue
                scored = sorted(
                    [{"score": dot(query_vec, s["embedding"]), **s} for s in idx],
                    key=lambda x: x["score"],
                    reverse=True,
                )
                matches_by_dim[dim] = [s for s in scored[:TOP_PER_DIM] if s["score"] >= threshold]
    except EmbedUnavailable as e:
        # No embed backend reachable at all (neither local ONNX nor Railway). Fail
        # closed and LOUD — exit 2 blocks the prompt with the message — rather than
        # silently dropping radar coverage. (A 503/busy backend is "up" and degrades
        # to None above, not here.)
        print(
            f"❌ Skill Radar: embed backend unavailable — {e}\n"
            "Start local ONNX (services/embed on :8100) or point SKILL_RADAR_EMBED_URL "
            "at Railway. (This block is deliberate: radar fails loud when no embed "
            "backend is reachable.)",
            file=sys.stderr,
        )
        sys.exit(2)

    all_matches = matches_by_dim["wisdom"] + matches_by_dim["what"]
    if not all_matches:
        sys.exit(0)

    log_skill_inject(prompt, all_matches)

    # Render each skill match as a shared provenance block (plan thj/26-6-16
    # Phase 1): source="skill:<name>" is agent-legible,
    # trust="learned:judge-applicability" marks it learned guidance. The
    # wisdom/what layer distinction is folded into the body label; the match
    # score stays retrieval-side (not rendered).
    layer_labels = {
        "wisdom": "Skill Radar — what we've learned (Layers 1+4)",
        "what":   "Skill Radar — what is (Layer 3, project clusters)",
    }
    blocks: list[str] = []
    for dim in ("wisdom", "what"):
        for m in matches_by_dim[dim]:
            skill = m.get("skill_name", m.get("name", "?"))
            header = m.get("header", "")
            fpath = m.get("file_path", "")
            text = m.get("text", m.get("description", ""))
            body = (
                f"{layer_labels[dim]}\n"
                f"{skill} › {header}  ({fpath})\n"
                f"---\n"
                f"{text[:CONTEXT_CHARS]}"
            )
            blocks.append(render_radar_block(
                body,
                source=f"skill:{skill}",
                trust="learned:judge-applicability",
            ))

    skill_body = "\n\n".join(blocks)
    print(render_additional_context(
        skill_body,
        hook_event_name=event.hook_event_name,
        runtime=event.runtime,
    ))
    emitted.append({"surface": "skill", "kind": "substance", "bytes": len(skill_body),
                    "score": max((m.get("score", 0) for m in all_matches), default=None),
                    "ref": [m.get("skill_name", m.get("name", "?")) for m in all_matches]})


if __name__ == "__main__":
    main()
