"""Seam guard: the doc_ingest core must stay backend-generic.

Fails if any doc_ingest module *references* (in code, not docstrings) an
equipment-specific symbol or imports a thj module. Enforces the invariant from
plan 26-6-19 § seam: equipment classification lives in the thj adapter (thj
repo), never re-grows in the shared core. AST-based so the core's docstrings —
which legitimately explain what they DON'T carry — don't trip it.

Run: python scripts/doc_ingest/lint_core_no_equipment.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Equipment symbols that must never appear as code identifiers in the core.
FORBIDDEN_NAMES = {
    "SECTION_PATTERNS", "SectionType", "EquipmentChunk", "EquipmentManualChunker",
    "_detect_section_type", "_get_chat_value", "_get_semantic_hints",
    "_infer_image_section_type", "_get_image_chat_value",
}
# thj modules the core must never import.
FORBIDDEN_IMPORTS = {"equipment_manuals", "models", "chunker", "table_extractor", "text_cleaning"}


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            out.append(f"{path.name}:{node.lineno} references {node.id!r}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            out.append(f"{path.name}:{node.lineno} references .{node.attr}")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
            out.append(f"{path.name}:{node.lineno} imports from {node.module!r}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_IMPORTS:
                    out.append(f"{path.name}:{node.lineno} imports {a.name!r}")
    return out


def main() -> int:
    violations: list[str] = []
    files = [
        py for py in sorted(ROOT.rglob("*.py"))
        # scan only the package's own source — never the bundled .venv site-packages
        if ".venv" not in py.parts and "__pycache__" not in py.parts
        and py.name != "lint_core_no_equipment.py"
    ]
    for py in files:
        violations.extend(_violations(py))
    if violations:
        print("FAIL — equipment-specific symbols leaked into the doc_ingest core:")
        for v in violations:
            print("  " + v)
        return 1
    print(f"PASS — doc_ingest core is backend-generic ({len(files)} source files scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
