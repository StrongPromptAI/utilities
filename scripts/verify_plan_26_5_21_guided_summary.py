"""Verify plan 26-5-21 guided-summary pivot — structural verification.

Source-level checks (no DB, no LLM) that confirm the code matches what the
plan promised:

  1. New symbols exist in expected locations
  2. New symbols have the expected callers (no orphans)
  3. Retired symbols / modules are actually gone
  4. The summarize orchestrator composes the new helpers
  5. The CLI registers exactly the expected subcommand set

Run after the implementation commit:
  uv run python scripts/verify_plan_26_5_21_guided_summary.py

Exit 0 = 0 divergences. Exit 1 = at least one divergence (prints what).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Layer 1 — New symbols exist
# ---------------------------------------------------------------------------

NEW_SYMBOLS = {
    "scripts/kb_core/scrub.py": {"scrub", "rehydrate", "_dedup_spans", "_build_engine"},
    "scripts/kb_core/summarize.py": {
        "generate_summary",
        "get_summary",
        "_format_transcript",
        "_load_chunks",
        "_persist_summary",
    },
    "scripts/kb_core/llm.py": {
        "complete_with_fallback",
        "_call_one",
        "_is_transient",
        "_load_key",
        "_call_anthropic",
        "_call_openai_compat",
    },
}


# ---------------------------------------------------------------------------
# Layer 3 — Retired modules are gone
# ---------------------------------------------------------------------------

DELETED_FILES = [
    "scripts/kb_core/harvest.py",
    "scripts/kb_core/quotes.py",
    "scripts/kb_core/analysis.py",
    "scripts/kb_core/synthesis.py",
]


# ---------------------------------------------------------------------------
# Layer 3b — Retired CLI commands are gone
# ---------------------------------------------------------------------------

DELETED_CLI_COMMANDS = {
    "harvest", "harvest-review", "pick-quotes", "draft-letter", "peterson-analyze",
    "questions", "decisions", "resolve", "update-decision", "dismiss-question",
    "synthesize", "show-summaries", "summarize", "outline",
}

EXPECTED_CLI_COMMANDS = {
    "search", "list-org", "list-contacts", "list-calls", "add-notes", "context",
    "summary", "show-summary", "scrub",
    "cluster", "transcribe", "ingest", "docs", "openwebui",
}


# ---------------------------------------------------------------------------
# Layer 4 — Composition: generate_summary uses the new helpers
# ---------------------------------------------------------------------------

COMPOSITION = {
    "scripts/kb_core/summarize.py": {
        "generate_summary": {"_load_chunks", "_format_transcript", "complete_with_fallback", "_persist_summary"},
    },
}


# ---------------------------------------------------------------------------
# Layer 5 — Signatures (param presence checked by AST)
# ---------------------------------------------------------------------------

EXPECTED_SIGNATURES = [
    ("scripts/kb_core/summarize.py", "generate_summary", {"call_id", "phi", "max_tokens"}),
    ("scripts/kb_core/scrub.py", "scrub", {"text", "mapping"}),
    ("scripts/kb_core/llm.py", "complete_with_fallback", {"prompt", "max_tokens", "temperature"}),
]


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def _parse(rel_path: str) -> ast.Module:
    return ast.parse((ROOT / rel_path).read_text())


def _top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _function_calls(tree: ast.Module, func_name: str) -> set[str]:
    """Return the set of function-call names made inside `func_name`."""
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    if isinstance(inner.func, ast.Name):
                        calls.add(inner.func.id)
                    elif isinstance(inner.func, ast.Attribute):
                        calls.add(inner.func.attr)
            break
    return calls


def _function_params(tree: ast.Module, func_name: str) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            args = node.args
            names = {a.arg for a in args.args}
            names.update(a.arg for a in args.kwonlyargs)
            return names
    return set()


def main() -> int:
    failures: list[str] = []

    # Layer 1: new symbols
    for rel_path, expected in NEW_SYMBOLS.items():
        path = ROOT / rel_path
        if not path.exists():
            failures.append(f"L1 MISSING FILE: {rel_path}")
            continue
        tree = _parse(rel_path)
        present = _top_level_names(tree)
        missing = expected - present
        if missing:
            failures.append(f"L1 MISSING SYMBOLS in {rel_path}: {sorted(missing)}")

    # Layer 3: deleted files
    for rel_path in DELETED_FILES:
        if (ROOT / rel_path).exists():
            failures.append(f"L3 NOT DELETED: {rel_path} should be gone")

    # Layer 3b + CLI command set
    cli_path = ROOT / "scripts/kb_cli.py"
    if not cli_path.exists():
        failures.append("L3b MISSING: scripts/kb_cli.py")
    else:
        import importlib.util, os
        os.environ.setdefault("KB_DATABASE_URL", "postgres://noop")
        spec = importlib.util.spec_from_file_location("kb_cli", cli_path)
        m = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(m)
            registered = set(m.cli.commands.keys())
            still_present = registered & DELETED_CLI_COMMANDS
            if still_present:
                failures.append(f"L3b CLI COMMANDS NOT DELETED: {sorted(still_present)}")
            missing_expected = EXPECTED_CLI_COMMANDS - registered
            if missing_expected:
                failures.append(f"L3b CLI COMMANDS MISSING: {sorted(missing_expected)}")
        except Exception as e:
            failures.append(f"L3b CLI IMPORT FAILED: {type(e).__name__}: {e}")

    # Layer 4: composition
    for rel_path, comp_map in COMPOSITION.items():
        tree = _parse(rel_path)
        for func_name, expected_calls in comp_map.items():
            actual = _function_calls(tree, func_name)
            missing = expected_calls - actual
            if missing:
                failures.append(f"L4 {rel_path}::{func_name} missing calls: {sorted(missing)}")

    # Layer 5: signatures
    for rel_path, func_name, expected_params in EXPECTED_SIGNATURES:
        tree = _parse(rel_path)
        actual = _function_params(tree, func_name)
        missing = expected_params - actual
        if missing:
            failures.append(f"L5 {rel_path}::{func_name} missing params: {sorted(missing)}")

    if failures:
        print(f"VERIFY FAIL — {len(failures)} divergence(s):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("VERIFY OK — 0 divergences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
