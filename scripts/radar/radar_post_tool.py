"""
Claude Code / Codex PostToolUse hook — semantic skill suggestion on errors +
tool-protocol enforcement (grep-on-code redirect).

Fired after shell tool use.

Two paths, in order:

1. Grep-on-code redirect (deterministic, no embedding):
   When the command uses grep/rg/ag to search code paths with an
   identifier-shaped pattern, inject a Skill Radar redirect pointing at
   gitnexus. Relational queries on source belong to the call graph, not
   text search. Logged to ~/.claude/grep-on-code-violations.log.

2. Error embedding match (existing):
   When an error is detected, embed the error text and find the closest
   matching skill chunk in the wisdom + what indices.

Outcomes (path 2):
- A: Match found (score >= threshold) AND skill is genuinely relevant —
     guidance injected into context. Also logged to SKILL_INJECT_LOG.md.
- B: No match (all scores below threshold) — logged to SKILL_DEBT.md for
     periodic review and skill-coverage improvement.
- C: Match found (score >= threshold) BUT skill is a false positive —
     guidance injected (may be noise), ALSO logged to SKILL_INJECT_LOG.md.
     Reviewing the inject log periodically is how Outcome C is detected.

Exits silently (code 0) on any failure — never blocks the agent runtime.
"""

import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

# Local import — Skill Radar embed client, backed by the utilities ONNX service.
sys.path.insert(0, str(Path(__file__).parent))
from embed_client import embed as _shared_embed, EmbedUnavailable, EmbedAuthError
from event_adapter import ToolEvent, normalize_event
from output_adapter import render_additional_context
import schema_corpus as sc
import thresholds as th
from session_log import append_event as session_log_append

INDEX_WISDOM_PATH = Path.home() / ".claude/radar_skills_wisdom.json"
INDEX_WHAT_PATH = Path.home() / ".claude/radar_skills_what.json"
HEARTBEAT_PATH = Path.home() / ".claude" / "last-jsonl-write.txt"
QUERY_PREFIX = "search_query: "

# --- Grep-on-code detection (tool-protocol enforcement) -------------------
# Captures the case where Claude reaches for grep/rg/ag to scan source code
# for a symbol — a job gitnexus does better. Heuristic: code-path/extension
# signal AND identifier-shaped pattern. False-positive cost is one extra
# round-trip; false-negative cost is context pollution.
GREP_TOOLS_RE = re.compile(r"(?:^|[\s|;&(])(grep|rg|ag)(?=\s)")

# Non-search flags — grep is being used as a filter / presence-test /
# extractor, NOT to find where an identifier is defined or used. All three are
# the opposite of a relational lookup, so never redirect them to gitnexus:
#   -v / --invert-match   suppress matching lines (filter a pipe / noisy warning)
#   -o / --only-matching  extract the matched substring (pull a value out)
#   -q / --quiet/--silent presence test, almost always `grep -q … && …`
# Matched as long flags or as any short-flag cluster containing v/o/q (e.g.
# `-nv`, `-oq`). None of the common code-search flags (-r -n -i -l -w -A -B -C
# -E -P) contain v/o/q, so the cluster match has near-zero false-negative cost.
NON_SEARCH_FLAG_RE = re.compile(
    r"(?:^|\s)(?:--(?:invert-match|only-matching|quiet|silent)\b"
    r"|-[a-zA-Z]*[voq][a-zA-Z]*\b)"
)

# Code path/extension signals — any one is enough.
CODE_PATH_RE = re.compile(
    r"--include[= ]\*?\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|cs|cpp|c|h)\b"
    r"|(?:^|[\s/'\"])(?:app|src|backend|frontend|services|routes|components|"
    r"hooks|tests|scripts|migrations|lib|core|pages|utils)/"
    r"|\b\S+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|cs|cpp|c|h)(?=\s|$|['\"])"
)

# Identifier shape: snake_case / camelCase / PascalCase, optionally
# alternated with `\|`. We require at least one lowercase letter and
# length >= 5 to filter common literal markers (TODO, FIXME, ENV vars).
IDENTIFIER_PATTERN_RE = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\\?\|[a-zA-Z_][a-zA-Z0-9_]*)*$"
)

# Per-dimension bars live in the central `thresholds` module: th.POST_TOOL_WISDOM
# / th.POST_TOOL_WHAT. Error text favours a code/cluster "what" match over a
# wisdom narrative. NOTE: POST_TOOL_WISDOM (0.70) is LOWER than the prompt hook's
# 0.72 — see thresholds.py's ⚠ VERIFY INTENT note (preserved, not yet reconciled).

# Top-1 from each dimension — same discipline as prompt_hook: side-by-side
# beats stacked-within-one-pool for noise control.
TOP_PER_DIM = 1

CONTEXT_CHARS = 800  # max chars of chunk text to inject per match

# Real-error markers — anchored to structural patterns (line starts, colons,
# exit-code numbers, exception class names) rather than bare prose words. The
# pre-2026-05-26 pattern matched prose like "could not find a way" or "failed
# to understand" — phrases that appear naturally in non-error tool output.
# Audit 2026-05-26 showed 99/200 sampled inject entries were prose-noise.
ERROR_SIGNALS = re.compile(
    r"(traceback \(most recent call last\)"
    r"|^error[: ]"                            # "Error:" or "error: " at line start
    r"|^[A-Z][A-Za-z]+Error: "                # PythonError class with colon-space
    r"|^[a-z][a-z_.]+\.[A-Z][A-Za-z]+Error: " # asyncpg.exceptions.Error etc.
    r"|exit code [1-9]\d*\b"
    r"|exited with code [1-9]"
    r"|^fatal: "
    r"|^panic: "
    r"|: command not found$"
    r"|^permission denied|: permission denied"
    r"|: no such file or directory"
    r"|HTTP/\d\.\d [45]\d\d"
    r"|^connection refused|connection refused$"
    r"|connection reset by peer"
    r"|segmentation fault"
    r"|^killed$"
    r"|: syntax error"
    r"|modulenotfounderror|importerror)",
    re.IGNORECASE | re.MULTILINE,
)


def _looks_like_pure_json(text: str) -> bool:
    """Pure JSON object or array → not an error worth embedding against skills.
    The radar embeds against natural-language skill content; JSON data has
    different statistical shape and overfires on `{"detail":"not found"}` etc."""
    s = text.strip()
    if not s:
        return False
    if not (s.startswith("{") and s.endswith("}")) and not (s.startswith("[") and s.endswith("]")):
        return False
    try:
        json.loads(s)
        return True
    except Exception:
        return False


def _looks_like_curl_progress(text: str) -> bool:
    """curl progress-bar output matched ERROR_SIGNALS on the word 'speed' / 'left'
    in the old pattern; the new pattern shouldn't catch it, but belt-and-braces."""
    return "Dload  Upload   Total" in text and "% Total" in text


_TEST_PASS_RE = re.compile(r"\[PASS\]|\[FAIL\]|PASSED|FAILED")
_TEST_SUMMARY_RE = re.compile(
    r"All .* tests passed"
    r"|\d+ passed(?:, \d+ failed)?"
    r"|All precision tests passed"
    r"|^Section \d+:",
    re.MULTILINE,
)


def _looks_like_test_output(text: str) -> bool:
    """Test runner output describes error patterns in PASS/FAIL labels, which
    contain the same strings ERROR_SIGNALS looks for. Recognized signals:
    ≥ 3 [PASS]/[FAIL] markers, OR a pytest-style summary line. False-positive
    cost is missing a real error that happens to look like a test report (very
    rare); false-negative cost is the hook embedding "[PASS] exit code 1"
    every time the precision suite runs."""
    if len(_TEST_PASS_RE.findall(text)) >= 3:
        return True
    if _TEST_SUMMARY_RE.search(text):
        return True
    return False


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def embed(text: str) -> list[float] | None:
    """Single-text embed via shared-svcs.

    Returns None when the backend is up but the call failed (503 shedding, etc.) so
    the hook degrades quietly. Propagates EmbedUnavailable — no backend reachable at
    all — so main() hard-fails loudly instead of silently dropping radar. retries=1
    keeps down-detection snappy."""
    try:
        return _shared_embed([text], timeout=3.0, retries=1)[0]
    except EmbedUnavailable:
        raise
    except Exception:
        return None


def extract_error(output: str) -> str | None:
    """Return the trailing error snippet if `output` looks like a real error,
    None if it's clean output or prose-noise that happens to contain error words.

    Precision rules (Phase 0a, 2026-05-26 audit):
    1. Skip pure-JSON outputs — `{"detail":"not found"}` and similar are
       payload data, not errors at the tool layer.
    2. Skip curl progress-bar dumps.
    3. Require a structural error marker from ERROR_SIGNALS (line-start
       prefixes, exception class names, exit codes — NOT bare prose words).
    4. Return only the last 600 chars; ANSI-strip.
    """
    if not output or not output.strip():
        return None
    if _looks_like_pure_json(output):
        return None
    if _looks_like_curl_progress(output):
        return None
    if _looks_like_test_output(output):
        return None
    match = ERROR_SIGNALS.search(output)
    if not match:
        return None
    # Return text centered on the error signal, not the unconditional tail.
    # Prevents raw ls/git-log output from entering the queue when a
    # multi-command bash has an error early and clean output late.
    start = max(0, match.start() - 200)
    end = min(len(output), match.end() + 400)
    snippet = output[start:end]
    snippet = re.sub(r"\x1b\[[0-9;]*m", "", snippet)
    return snippet.strip()


def load_index(path: Path) -> list[dict]:
    """Load one dimension's index. Empty list on any failure."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def _touch_heartbeat() -> None:
    """Stamp the last-jsonl-write heartbeat — Phase 4.5 observability surface.
    Stale heartbeat means the hook stopped writing; the health CLI surfaces it."""
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(
            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
            + "\n"
        )
    except Exception:
        pass


def _extract_grep_pattern(segment: str) -> str | None:
    """Best-effort extraction of the search pattern from a grep/rg/ag command.

    Naive: shlex-split, find the tool token, skip flags (and flags that take
    a value), return the first non-flag token. Handles single/double quotes
    via shlex. Returns None when extraction fails.
    """
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return None
    saw_grep = False
    skip_next = False
    flags_with_value = {"-e", "-f", "--regexp", "--file", "--include",
                        "--exclude", "--include-dir", "--exclude-dir"}
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in ("grep", "rg", "ag"):
            saw_grep = True
            continue
        if not saw_grep:
            continue
        if tok in flags_with_value:
            skip_next = True
            continue
        if "=" in tok and tok.split("=")[0] in flags_with_value:
            continue
        if tok.startswith("-"):
            continue
        return tok
    return None


# --- Symbol-relevance gate -------------------------------------------------
# The redirect only helps when the grep pattern names something gitnexus can
# answer *relationally* — a node with call-graph / blast-radius edges. In a
# gitnexus parse-cache, that's Function/Method/Class/Interface. The dominant
# node type is Variable (12k+ on thj), which has no CALLS edge: grepping for a
# variable / JSONB key / dict key / param name (`llm_raw`, `escalation_target`)
# is a literal lookup gitnexus context can't improve on. Gating the redirect on
# callable-symbol membership closes that overfire class without a brittle
# denylist — the index itself is the ground truth for "is this a real symbol."
CALLABLE_LABELS = frozenset({"Function", "Method", "Class", "Interface"})
_SYMBOL_CACHE_DIR = Path.home() / ".claude"
_ABS_PATH_RE = re.compile(r"(?<![\w])/[^\s'\"|;&()]+")


def _walk_up_for_gitnexus(start: Path) -> Path | None:
    """Nearest ancestor of `start` containing `.gitnexus/parse-cache/`."""
    try:
        base = start if start.is_dir() else start.parent
    except OSError:
        return None
    for cand in (base, *base.parents):
        if (cand / ".gitnexus" / "parse-cache").is_dir():
            return cand
    return None


def _find_indexed_repo_root(command: str, cwd: str | None) -> Path | None:
    """Resolve the indexed repo for this grep — prefer an absolute path
    argument in the command (the grep target), else the hook's cwd."""
    for abs_path in _ABS_PATH_RE.findall(command):
        root = _walk_up_for_gitnexus(Path(abs_path))
        if root:
            return root
    if cwd:
        return _walk_up_for_gitnexus(Path(cwd))
    return None


def _parse_cache_signature(repo_root: Path) -> str | None:
    """A cheap freshness token for the repo's parse-cache — the indexed commit
    if recorded, else the newest shard mtime."""
    pc = repo_root / ".gitnexus" / "parse-cache"
    if not pc.is_dir():
        return None
    try:
        meta = json.loads((repo_root / ".gitnexus" / "meta.json").read_text())
        last_commit = meta.get("lastCommit")
        if last_commit:
            return f"commit:{last_commit}"
    except Exception:
        pass
    try:
        return f"mtime:{max(f.stat().st_mtime for f in pc.glob('*.json')):.0f}"
    except ValueError:
        return None


def _callable_symbols_for_repo(repo_root: Path) -> set[str] | None:
    """Set of callable/structural symbol names in the repo's gitnexus index,
    cached at ~/.claude/radar_symbols_<repo-slug>.json and rebuilt only when the
    parse-cache signature changes. Returns None if the index can't be read."""
    sig = _parse_cache_signature(repo_root)
    if not sig:
        return None
    slug = str(repo_root).strip("/").replace("/", "_") or "root"
    cache_path = _SYMBOL_CACHE_DIR / f"radar_symbols_{slug}.json"
    try:
        cached = json.loads(cache_path.read_text())
        if cached.get("sig") == sig:
            return set(cached.get("symbols", []))
    except Exception:
        pass
    names: set[str] = set()
    try:
        for shard in (repo_root / ".gitnexus" / "parse-cache").glob("*.json"):
            if shard.name == "index.json":
                continue
            blocks = json.loads(shard.read_text())
            if not isinstance(blocks, list):
                continue
            for blk in blocks:
                for node in blk.get("nodes", []):
                    if node.get("label") in CALLABLE_LABELS:
                        name = node.get("properties", {}).get("name")
                        if name:
                            names.add(name)
    except Exception:
        return None
    try:
        cache_path.write_text(json.dumps({"sig": sig, "symbols": sorted(names)}))
    except Exception:
        pass
    return names


def _pattern_is_relational(pattern: str, command: str, cwd: str | None) -> bool:
    """True when the pattern names a call-graph symbol gitnexus can answer
    relationally. Fail-open: when the repo isn't indexed or the symbol set can't
    be loaded, return True so the redirect fires exactly as before — only an
    *indexed* repo where NONE of the pattern's alternatives is a callable symbol
    suppresses it (the JSONB-key / variable / literal overfire)."""
    repo_root = _find_indexed_repo_root(command, cwd)
    if repo_root is None:
        return True
    symbols = _callable_symbols_for_repo(repo_root)
    if not symbols:
        return True
    alternatives = [a for a in re.split(r"\\?\|", pattern) if a]
    return any(a in symbols for a in alternatives)


def detect_grep_on_code(command: str, cwd: str | None = None) -> tuple[str, str] | None:
    """Detect grep-on-code violation. Returns (pattern, path_signal) on
    violation, None when the command is fine.

    Tight rule: must hit ALL of:
      1. Command uses grep/rg/ag as a tool word
      2. Command has a code-path or code-extension signal
      3. Pattern is identifier-shaped (or alternation of identifiers)
      4. Pattern has at least one lowercase letter
      5. Pattern is at least 5 chars long
      6. Command is NOT a same-file lookup (single concrete file path, no glob)
         — that's the doctrine's "narrow same-file lookup" exemption per
         ~/repo_docs/skills/gitnexus/SKILL.md.
      7. The pattern names a callable/structural symbol in the local gitnexus
         index (Function/Method/Class/Interface). A variable / JSONB key /
         literal that merely looks identifier-shaped is NOT a relational query.
    """
    if not GREP_TOOLS_RE.search(command):
        return None

    # Non-search flag (filter / extract / presence-test) → not a relational
    # code query. Bail before the code-path/identifier checks. (Conservative:
    # the flag anywhere in a compound `grep foo file.py | grep -v bar`
    # suppresses the whole command — the rare cost of missing a legit first
    # grep, vs. the common false-positive of flagging `... | grep -v warning`.)
    if NON_SEARCH_FLAG_RE.search(command):
        return None

    # Operate on the first ~600 chars of the command (most fit; long pipelines
    # we sample the head to keep regex cost predictable)
    head = command[:600]

    code_match = CODE_PATH_RE.search(head)
    if not code_match:
        return None

    pattern = _extract_grep_pattern(head)
    if not pattern:
        return None

    if not IDENTIFIER_PATTERN_RE.match(pattern):
        return None
    if not any(c.islower() for c in pattern):
        return None  # all-caps tokens (DATABASE_URL, RESULT) — usually literals
    if len(pattern) < 5:
        return None  # short tokens (TODO, item) — usually literals

    # Same-file lookup exemption — the doctrine explicitly allows narrow
    # same-file lookups (line numbers within a known file). Exempt when the
    # command targets exactly one file with no recursion flag and no glob.
    if _is_same_file_lookup(head):
        return None

    # Symbol-relevance gate — suppress when the indexed repo proves the pattern
    # is not a callable/structural symbol (variable / JSONB key / literal).
    if not _pattern_is_relational(pattern, head, cwd):
        return None

    return (pattern, code_match.group(0).strip())


_GLOB_OUTSIDE_QUOTES_RE = re.compile(r"(?<!\\)\*")
_SINGLE_FILE_PATH_RE = re.compile(
    r"\b\S+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|cs|cpp|c|h)(?=\s|$|['\"])"
)


def _is_same_file_lookup(head: str) -> bool:
    """Same-file lookup detection. Single file path, no recursion, no glob.

    The doctrine: ``grep -n "pattern" /path/to/specific-file.py`` is a
    literal-text lookup within ONE file, not a relational query. Don't
    redirect those to gitnexus.

    We don't inspect ``matched_signal`` — the CODE_PATH_RE may match a
    directory-name substring inside an absolute file path (e.g.
    ``/repo/backend/foo.py`` matches the ``backend/`` dir-name alternative
    even though the COMMAND targets a single file). Counting actual file
    arguments + checking for glob is the structural read. A recursion flag is
    NOT a directory signal here: ``grep -rn pat one_file.py`` is a vacuous
    ``-r`` over a single concrete file — still a same-file lookup. The
    directory-scan cases (``grep -rn pat app/``, ``grep -rn pat .``) carry zero
    concrete file paths and fail the count check below.
    """
    # --include filter → directory-scoped grep with extension filter.
    if "--include" in head:
        return False
    # Glob outside quotes → directory scan; not same-file.
    stripped = re.sub(r'"[^"]*"', "", head)
    stripped = re.sub(r"'[^']*'", "", stripped)
    if _GLOB_OUTSIDE_QUOTES_RE.search(stripped):
        return False
    # Exactly one file path in the command. Multiple file paths → enumerated
    # directory scan; zero file paths with no recursion flag → likely a bare
    # directory arg (rare; treat conservatively as not-same-file).
    file_paths = _SINGLE_FILE_PATH_RE.findall(head)
    if len(file_paths) != 1:
        return False
    return True


def log_grep_violation(command: str, pattern: str, path_signal: str) -> None:
    """Log a grep-on-code violation as a JSONL row in session-log.jsonl.
    Replaces the pre-2026-05-26 grep-on-code-violations.log (now archived)."""
    if session_log_append(
        event_type="grep_on_code",
        tool="Bash",
        command_or_context=command[:400],
        error_text=f"pattern={pattern} path={path_signal}",
        outcome="violation",
    ):
        _touch_heartbeat()


def emit_grep_redirect(pattern: str, path_signal: str, event: ToolEvent) -> None:
    """Print the Skill Radar JSON envelope with a gitnexus redirect message."""
    msg = (
        "Skill Radar — tool-protocol redirect (grep-on-code):\n"
        "\n"
        f"You used grep/rg/ag to search code (`{path_signal}`) for an "
        f"identifier-shaped pattern (`{pattern}`). Relational queries on "
        "source code belong to the call graph, not text search.\n"
        "\n"
        "Try instead:\n"
        f"  gitnexus context {pattern}              — callers/callees/file/line\n"
        f"  gitnexus impact {pattern} -d upstream   — blast radius before edit\n"
        "  gitnexus query \"<concept>\"               — process-grouped flow search\n"
        "\n"
        "If grep already ran, the right next action is to rerun the relational "
        "part via gitnexus context/impact/query — NOT \"I already have what I "
        "need.\" The grep result is text co-occurrence; the gitnexus result is "
        "the call graph. They are not the same answer.\n"
        "\n"
        "grep is correct for literal text in markdown / JSON / SQL / configs / "
        "logs, or for narrow same-file lookups. The signal that fired this "
        "redirect: code-path filter + identifier-shaped pattern. If the pattern "
        "is genuinely a literal that happens to look like an identifier, "
        "narrow the path to a non-code file or add `--include` with a non-code "
        "extension.\n"
        "\n"
        "See ~/repo_docs/skills/gitnexus/SKILL.md § \"When the grep-on-code "
        "redirect fires\" for the full doctrine.\n"
        "Logged to session-log.jsonl as event_type=grep_on_code."
    )
    print(render_additional_context(
        msg,
        hook_event_name=event.hook_event_name,
        runtime=event.runtime,
    ))


# ── Schema-on-code redirect (plan thj/26-6-16) ───────────────────────────────
# On an Edit/Write to a schema.sql / migration / DDL-bearing file, surface the
# touched table's authored COMMENT — the residency/gotcha ORACLE — at the edit
# moment (the point residency-blindness bites, e.g. an out-of-band schema change
# that forgets the both-schemas rule). Deterministic: parses schema.sql, no embed.

_DDL_DML_RE = re.compile(
    r"\b(?:CREATE\s+TABLE|ALTER\s+TABLE|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
    r"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([A-Za-z0-9_.\"]+)",
    re.IGNORECASE,
)
_QUALIFIED_RE = re.compile(r"\b(?:public|sandbox)\.([a-z0-9_]+)", re.IGNORECASE)


def _repo_for_path(file_path: str):
    """Resolve an edited file to (slug, cfg) via the schema-repo registry roots."""
    for slug, cfg in sc.load_repos().items():
        root = sc.expand(cfg.get("repo_root", "")).rstrip("/")
        if root and file_path.startswith(root + "/"):
            return slug, cfg
    return None, None


def detect_schema_edit(tool_name: str, file_path: str, content: str, cwd: str | None):
    """Return (slug, [rendered_table_chunks]) when an Edit/Write touches a
    schema.sql / migration / DDL-bearing file that references commented tables.
    None otherwise — scoped tight so it never fires on ordinary code edits."""
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return None
    if not file_path:
        return None
    is_schema_file = file_path.endswith("schema.sql") or "/migrations/" in file_path
    has_ddl = bool(_DDL_DML_RE.search(content or ""))
    if not (is_schema_file or has_ddl):
        return None

    slug, cfg = _repo_for_path(file_path)
    if not slug:
        slug, cfg = sc.repo_for_cwd(cwd or "")
    if not slug:
        return None
    schema_path = sc.expand(cfg.get("schema_sql", ""))
    if not Path(schema_path).exists():
        return None

    names: set[str] = set()
    for m in _DDL_DML_RE.finditer(content or ""):
        names.add(m.group(1).split(".")[-1].strip('"').lower())
    for m in _QUALIFIED_RE.finditer(content or ""):
        names.add(m.group(1).lower())
    if not names:
        return None

    parsed = sc.parse_comments(schema_path)
    lower_map = {t.lower(): t for t in parsed}
    hits: list[str] = []
    for n in names:
        t = lower_map.get(n)
        if t and sc.is_substantive(parsed[t]):
            hits.append(sc.render_chunk(t, parsed[t]))
    if not hits:
        return None
    return slug, hits


def emit_schema_redirect(slug: str, hits: list[str], event: ToolEvent) -> None:
    body = "\n\n".join(hits)
    msg = (
        "Schema Radar — schema-on-code (you're editing schema/DDL):\n"
        "\n"
        "The table(s) you're touching carry authored COMMENTs — the residency / "
        "gotcha ORACLE. Per doctrine, per-table facts (public-only? sandbox twin? "
        "both-schemas?) live in the table COMMENT, not in DATABASE.md (which holds "
        "the residency CLASSES). For residency-critical changes, the live `\\d+` "
        "is the oracle; this is the committed-snapshot view.\n"
        "\n"
        f"{body}\n"
        "\n"
        "Out-of-band schema change reminder: a table present in BOTH schemas must "
        "be changed in public AND sandbox; a public-only telemetry table has no "
        "sandbox twin (the comment says which)."
    )
    print(render_additional_context(
        msg,
        hook_event_name=event.hook_event_name,
        runtime=event.runtime,
    ))


def log_schema_edit(slug: str, file_path: str, tables: list[str]) -> None:
    try:
        session_log_append(
            event_type="schema_edit_redirect",
            tool="PostToolUse",
            command_or_context=file_path[:400],
            outcome="injected",
            extra={"repo": slug, "tables": tables[:10]},
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
    if not isinstance(event, ToolEvent):
        sys.exit(0)

    # ---- Path 1: grep-on-code redirect (deterministic, no embedding) ----
    command = event.command or ""
    cwd = (
        payload.get("cwd")
        or payload.get("workdir")
        or payload.get("working_directory")
    )
    if command:
        violation = detect_grep_on_code(command, cwd)
        if violation:
            pattern, path_signal = violation
            log_grep_violation(command, pattern, path_signal)
            emit_grep_redirect(pattern, path_signal, event)
            sys.exit(0)

    # ---- Path 1.5: schema-on-code redirect (deterministic, no embedding) ----
    tool_input = payload.get("tool_input", {}) or {}
    edit_file_path = tool_input.get("file_path") or ""
    edit_content = (
        tool_input.get("new_string")
        or tool_input.get("content")
        or tool_input.get("new_str")
        or ""
    )
    if not edit_content and isinstance(tool_input.get("edits"), list):
        edit_content = "\n".join(str(e.get("new_string", "")) for e in tool_input["edits"])
    sed = detect_schema_edit(event.tool_name or "", edit_file_path, edit_content, cwd)
    if sed:
        sed_slug, sed_hits = sed
        sed_tables = [h.split(":", 1)[0].splitlines()[0] for h in sed_hits]
        log_schema_edit(sed_slug, edit_file_path, sed_tables)
        emit_schema_redirect(sed_slug, sed_hits, event)
        sys.exit(0)

    # ---- Path 2: error embedding match (existing) ----
    output = event.output

    error_text = extract_error(output)
    if not error_text:
        sys.exit(0)

    command_context = (event.command or "")[:400]

    wisdom_idx = load_index(INDEX_WISDOM_PATH)
    what_idx = load_index(INDEX_WHAT_PATH)
    if not wisdom_idx and not what_idx:
        # Index empty — record the bash_error with no match so harvest can
        # still see there's a skill-coverage gap.
        if session_log_append(
            event_type="bash_error",
            tool=event.tool_name or "Bash",
            command_or_context=command_context,
            error_text=error_text,
            outcome="missed",
        ):
            _touch_heartbeat()
        sys.exit(0)

    try:
        query_vec = embed(QUERY_PREFIX + error_text)
    except EmbedUnavailable as e:
        # Backend not usable — either unreachable (EmbedUnavailable) or auth-rejected
        # (EmbedAuthError subclass: bad/wrong/expired JWT). Record the signal so harvest
        # still sees the error, then fail LOUD (exit 2) rather than silently dropping
        # radar coverage. (503/busy is "up" → None below.)
        if session_log_append(
            event_type="bash_error",
            tool=event.tool_name or "Bash",
            command_or_context=command_context,
            error_text=error_text,
            outcome="missed",
        ):
            _touch_heartbeat()
        if isinstance(e, EmbedAuthError):
            print(
                f"❌ Skill Radar: embed auth rejected — {e}\n"
                "Fix shared_svc_jwt_secret in ~/.config/keys.json (= shared-svcs Railway "
                "JWT_SECRET), or unset SKILL_RADAR_EMBED_URL to use local ONNX.",
                file=sys.stderr,
            )
        else:
            print(
                f"❌ Skill Radar: embed backend unavailable — {e}\n"
                "Start local ONNX (services/embed on :8100) or point SKILL_RADAR_EMBED_URL "
                "at Railway.",
                file=sys.stderr,
            )
        sys.exit(2)
    if not query_vec:
        # Backend up but the call failed (busy/transient) — record the error so we
        # still capture signal; harvest can decide what to do with un-matched rows.
        if session_log_append(
            event_type="bash_error",
            tool=event.tool_name or "Bash",
            command_or_context=command_context,
            error_text=error_text,
            outcome="missed",
        ):
            _touch_heartbeat()
        sys.exit(0)

    matches_by_dim: dict[str, list[dict]] = {"wisdom": [], "what": []}
    top_scored: list[dict] = []  # captured for the JSONL row even when nothing fires

    for dim, idx, threshold in (
        ("wisdom", wisdom_idx, th.POST_TOOL_WISDOM),
        ("what", what_idx, th.POST_TOOL_WHAT),
    ):
        if not idx:
            continue
        scored = sorted(
            [{"score": dot(query_vec, s["embedding"]), **s} for s in idx],
            key=lambda x: x["score"],
            reverse=True,
        )
        matches_by_dim[dim] = [s for s in scored[:TOP_PER_DIM] if s["score"] >= threshold]
        top_scored.extend(scored[:1])

    all_matches = matches_by_dim["wisdom"] + matches_by_dim["what"]

    # JSONL row in both branches — outcome distinguishes "missed" (no match
    # above threshold) from "injected" (match above threshold, surfaced to
    # Claude). `skill_match` captures the top scorer either way so harvest can
    # bucketize misses by what came closest.
    top_scored.sort(key=lambda x: x["score"], reverse=True)
    best = top_scored[0] if top_scored else None
    skill_match_row = (
        {
            "score": round(best["score"], 3),
            "skill": best.get("skill_name", best.get("name", "?")),
            "header": best.get("header", ""),
        }
        if best
        else None
    )

    if not all_matches:
        if session_log_append(
            event_type="bash_error",
            tool=event.tool_name or "Bash",
            command_or_context=command_context,
            error_text=error_text,
            skill_match=skill_match_row,
            outcome="missed",
        ):
            _touch_heartbeat()
        sys.exit(0)

    if session_log_append(
        event_type="bash_error",
        tool=event.tool_name or "Bash",
        command_or_context=command_context,
        error_text=error_text,
        skill_match=skill_match_row,
        outcome="injected",
    ):
        _touch_heartbeat()

    lines: list[str] = []
    section_labels = {
        "wisdom": "Skill Radar — what we've learned (Layers 1+4):",
        "what":   "Skill Radar — what is (Layer 3, project clusters):",
    }
    for dim in ("wisdom", "what"):
        ms = matches_by_dim[dim]
        if not ms:
            continue
        lines.append(section_labels[dim])
        lines.append("")
        for m in ms:
            skill = m.get("skill_name", m.get("name", "?"))
            header = m.get("header", "")
            fpath = m.get("file_path", "")
            score = m["score"]
            text = m.get("text", m.get("description", ""))
            lines.append(f"[{score:.2f}] {skill} › {header}  ({fpath})")
            lines.append("---")
            lines.append(text[:CONTEXT_CHARS])
            lines.append("")

    print(render_additional_context(
        "\n".join(lines),
        hook_event_name=event.hook_event_name,
        runtime=event.runtime,
    ))


if __name__ == "__main__":
    main()
