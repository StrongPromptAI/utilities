"""Verify plan 26-5-9 Codex Skill Radar hook compatibility.

This is intentionally source-level: it catches the structural wiring the plan
promised without depending on a live Codex hook event.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SYMBOLS = {
    "scripts/skill_hook/event_adapter.py": {
        "PromptEvent",
        "ToolEvent",
        "UnknownEvent",
        "normalize_event",
    },
    "scripts/skill_hook/output_adapter.py": {
        "render_additional_context",
    },
}

EXPECTED_CALLS = {
    "scripts/skill_hook/prompt_hook.py": {
        "normalize_event",
        "render_additional_context",
    },
    "scripts/skill_hook/hook.py": {
        "normalize_event",
        "render_additional_context",
    },
}


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def defined_symbols(tree: ast.Module) -> set[str]:
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.add(node.name)
    return symbols


def called_symbols(tree: ast.Module) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
    return calls


def main() -> int:
    failures: list[str] = []

    for rel_path, expected in EXPECTED_SYMBOLS.items():
        path = ROOT / rel_path
        if not path.exists():
            failures.append(f"missing file: {rel_path}")
            continue
        missing = expected - defined_symbols(parse(path))
        if missing:
            failures.append(f"{rel_path}: missing symbols {sorted(missing)}")

    for rel_path, expected in EXPECTED_CALLS.items():
        path = ROOT / rel_path
        if not path.exists():
            failures.append(f"missing file: {rel_path}")
            continue
        missing = expected - called_symbols(parse(path))
        if missing:
            failures.append(f"{rel_path}: missing calls {sorted(missing)}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("OK: plan 26-5-9 Codex Skill Radar hook compatibility wiring verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
