"""Phase 0a precision regression suite for the skill-radar hooks.

Covers the three failure modes documented in the 2026-05-26 audit of
SKILL_DEBT.md + SKILL_INJECT_LOG.md:

1. Prose-as-error: tool outputs containing prose words like "error" / "could
   not" / "failed to" were treated as errors by the pre-fix `extract_error()`.
   99 of 200 sampled inject entries were prose-noise.

2. Substring-collision in prompt_hook: skill names that double as common
   English nouns (`versioning`, `implementation`, `utilities`) fired synthetic
   1.00 scores whenever the prompt contained the word.

3. Prior-injection feedback loop: `<system-reminder>` blocks and "Skill Radar
   — ..." envelopes the radar injects on turn N reappear in the prompt input
   on turn N+1, causing the keyword prefilter to re-fire on text the radar
   itself injected.

Run via `uv run --project ~/repos/utilities python tests/test_precision.py`.
Exits 0 on full pass, 1 on any failure. Pure Python — no embed service
required (semantic-confirm paths are tested via stub embeddings).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hook
import prompt_hook


FAILURES: list[str] = []


def expect(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    line = f"  [{status}] {name}"
    if detail and not condition:
        line += f"\n         {detail}"
    print(line)
    if not condition:
        FAILURES.append(name)


# --------------------------------------------------------------------------
# Section 1 — extract_error() prose-noise filter
# --------------------------------------------------------------------------

print("\n=== Section 1: extract_error() ===")

# Real errors must still be caught.
real_traceback = """Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
ModuleNotFoundError: No module named 'foo'
"""
expect(
    "real Python traceback → matched",
    hook.extract_error(real_traceback) is not None,
)

expect(
    "ls /nonexistent → matched",
    hook.extract_error("ls: /nonexistent: No such file or directory\n") is not None,
)

expect(
    "command not found → matched",
    hook.extract_error("zsh: command not found\n") is not None,
)

expect(
    "asyncpg.DuplicateFunctionError → matched",
    hook.extract_error(
        "asyncpg.exceptions.DuplicateFunctionError: function already exists\n"
    )
    is not None,
)

expect(
    "HTTP 500 → matched",
    hook.extract_error("Got response HTTP/1.1 500 Internal Server Error\n") is not None,
)

expect(
    "exit code 1 → matched",
    hook.extract_error("Process completed with exit code 1\n") is not None,
)

# Prose noise must NOT fire.
expect(
    "prose 'discusses error handling' → ignored",
    hook.extract_error("This document discusses error handling patterns.") is None,
)

expect(
    "prose 'failed to understand' → ignored",
    hook.extract_error(
        "Earlier attempts failed to understand the user intent, but later iterations improved."
    )
    is None,
)

expect(
    "prose 'cannot fail' → ignored",
    hook.extract_error("The pipeline cannot fail when properly configured.") is None,
)

# Pure JSON outputs should not fire even if they contain error-like keys.
expect(
    "pure JSON {\"detail\":\"not found\"} → ignored",
    hook.extract_error('{"detail":"not found"}') is None,
)

expect(
    "pure JSON with error key → ignored",
    hook.extract_error('{"error": "Invalid input", "code": 400}') is None,
)

expect(
    "pure JSON array → ignored",
    hook.extract_error('[{"id": 1}, {"id": 2}]') is None,
)

# curl progress noise (which would have matched the old "left" / "speed" pattern).
curl_progress = """  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
                                 Dload  Upload   Total   Spent    Left  Speed
100  1234  100  1234    0     0    34k      0 --:--:-- --:--:-- --:--:--   35k"""
expect(
    "curl progress bar → ignored",
    hook.extract_error(curl_progress) is None,
)

# Empty / whitespace.
expect(
    "empty output → ignored",
    hook.extract_error("") is None,
)
expect(
    "whitespace-only output → ignored",
    hook.extract_error("   \n\t\n") is None,
)

# Test-runner output describes error patterns in PASS/FAIL labels. The hook
# must NOT fire on its own test suite (recursive self-trigger).
test_output = """  [PASS] real Python traceback → matched
  [PASS] ls /nonexistent → matched
  [PASS] command not found → matched
  [PASS] exit code 1 → matched

All precision tests passed."""
expect(
    "test-runner [PASS] output → ignored (no self-trigger)",
    hook.extract_error(test_output) is None,
)

# pytest-style summary should also be skipped
pytest_summary = """===== test session starts =====
test_foo.py::test_one PASSED
test_foo.py::test_two PASSED
test_foo.py::test_three PASSED
===== 3 passed in 0.42s ====="""
expect(
    "pytest-style summary → ignored",
    hook.extract_error(pytest_summary) is None,
)


# --------------------------------------------------------------------------
# Section 2 — strip_prior_injections() removes contamination
# --------------------------------------------------------------------------

print("\n=== Section 2: strip_prior_injections() ===")

clean_prompt = "create a mermaid diagram showing the proposed flow."
contaminated = (
    f"{clean_prompt}\n\n"
    "<system-reminder>\n"
    "Skill Radar — what we've learned (Layers 1+4):\n"
    "[1.00] versioning › Scope (...)\n"
    "This skill covers application versioning for frontend and backend code.\n"
    "</system-reminder>"
)
stripped = prompt_hook.strip_prior_injections(contaminated)
expect(
    "system-reminder block removed",
    "<system-reminder>" not in stripped and "</system-reminder>" not in stripped,
    detail=f"got: {stripped!r}",
)
expect(
    "user-typed content preserved",
    clean_prompt in stripped,
)
expect(
    "skill name 'versioning' removed (it was inside the reminder)",
    "versioning" not in stripped.lower(),
    detail=f"got: {stripped!r}",
)


# --------------------------------------------------------------------------
# Section 3 — keyword_prefilter() two-tier check
# --------------------------------------------------------------------------

print("\n=== Section 3: keyword_prefilter() ===")


def fake_chunk(skill_name: str, load_when: str = "", emb: list[float] | None = None) -> dict:
    return {
        "skill_name": skill_name,
        "header": "Test header",
        "text": "Test text",
        "file_path": f"/fake/{skill_name}.md",
        "load_when": load_when,
        "embedding": emb or [0.0] * 384,
    }


# Build a small index containing a common-English-noun skill name and one
# specific skill name. Embeddings are stubbed.
fake_index = [
    fake_chunk("versioning", load_when='"version this"'),
    fake_chunk("implementation", load_when=""),
    fake_chunk("gitnexus", load_when='"impact analysis"'),
]


# Substring "implementation" appearing in user prompt should NOT auto-fire
# because the embed call will return None (no embed service) — falling closed.
# Mock embed to return None to simulate offline; should fail-closed and return None.
_orig_embed = prompt_hook.embed
prompt_hook.embed = lambda text: None  # type: ignore

result = prompt_hook.keyword_prefilter(
    "Let's discuss the current implementation of the feature.",
    fake_index,
)
expect(
    "common-noun skill name + no embed → fails closed (no synthetic 1.00)",
    result is None,
    detail=f"got: {result!r}",
)

# Restore embed but stub it to return a vector that doesn't match anything well.
def low_sim_embed(text: str) -> list[float] | None:
    return [0.0] * 384  # produces 0.0 dot with the stub chunk embeddings


prompt_hook.embed = low_sim_embed  # type: ignore
result = prompt_hook.keyword_prefilter(
    "Let's discuss the current implementation of the feature.",
    fake_index,
)
expect(
    "common-noun match + low semantic similarity → rejected",
    result is None,
    detail=f"got: {result!r}",
)


# When the trigger phrase dominates the prompt, the deterministic carve-out
# should fire (no semantic confirm needed). "use gitnexus" — 13 chars, "gitnexus"
# is 8 chars, that's 8/13 = 0.615 — well above the 0.30 dominance ratio.
def high_sim_embed(text: str) -> list[float] | None:
    return [1.0] + [0.0] * 383


# Use embeddings that match high for "gitnexus" chunk.
fake_index_dominant = [
    fake_chunk("gitnexus", load_when='"impact analysis"', emb=[1.0] + [0.0] * 383),
]
prompt_hook.embed = lambda text: [1.0] + [0.0] * 383  # type: ignore
result = prompt_hook.keyword_prefilter("use gitnexus", fake_index_dominant)
expect(
    "trigger phrase dominates short prompt → deterministic 1.00",
    result is not None and result[0]["score"] == 1.0,
    detail=f"got: {result!r}",
)


# Substring match + HIGH semantic similarity → returns chunk with the actual
# similarity score (not synthetic 1.00).
fake_index_semantic = [
    fake_chunk(
        "implementation",
        load_when="",
        emb=[1.0] + [0.0] * 383,
    ),
]
prompt_hook.embed = lambda text: [1.0] + [0.0] * 383  # type: ignore
result = prompt_hook.keyword_prefilter(
    "Let's discuss the current implementation of the new caching feature for production.",
    fake_index_semantic,
)
expect(
    "substring + high semantic → returns match with semantic score",
    result is not None and 0.65 <= result[0]["score"] <= 1.001,
    detail=f"got: {result!r}",
)


# Restore embed
prompt_hook.embed = _orig_embed  # type: ignore


# --------------------------------------------------------------------------
# Section 4 — prior-injection feedback loop
# --------------------------------------------------------------------------

print("\n=== Section 4: prior-injection feedback loop ===")

# When the prompt contains a prior <system-reminder> that mentions a skill name,
# the prefilter must NOT match on that skill — the user didn't type it.
contaminated_prompt = (
    "explain the diff between session-log.jsonl and skill queue.md\n\n"
    "<system-reminder>\n"
    "[1.00] versioning › Scope\n"
    "This skill covers application versioning for frontend and backend code.\n"
    "</system-reminder>"
)

# Even if `versioning` is a trigger, stripping prior-injection should hide it.
prompt_hook.embed = lambda text: [0.0] * 384  # type: ignore
result = prompt_hook.keyword_prefilter(
    contaminated_prompt, [fake_chunk("versioning")]
)
expect(
    "prior-injection 'versioning' inside system-reminder → no match",
    result is None,
    detail=f"got: {result!r}",
)
prompt_hook.embed = _orig_embed  # type: ignore


# --------------------------------------------------------------------------

print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} test(s)")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("All precision tests passed.")
    sys.exit(0)
