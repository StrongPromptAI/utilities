"""Figure storage on the coach Railway volume (no bucket).

Layout under COACH_FIGURES_ROOT (default `/data/figures`):
    <doc-slug>__fig_NN.jpg     a cropped book/manual figure

Flat namespace (the doc-slug prefix namespaces a document's figures). All paths
resolve inside the figures root and refuse traversal — names are basename-only.
"""
from __future__ import annotations

import os
from pathlib import Path

FIGURES_ROOT = Path(os.environ.get("COACH_FIGURES_ROOT", "/data/figures")).expanduser()

MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def safe_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise ValueError("invalid name")
    return name


def figure_path(name: str) -> Path | None:
    """Resolved path to one figure, or None if absent. Refuses traversal."""
    name = safe_name(name)
    p = FIGURES_ROOT / name
    try:
        p.resolve().relative_to(FIGURES_ROOT.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def write_figure(name: str, data: bytes) -> Path:
    name = safe_name(name)
    FIGURES_ROOT.mkdir(parents=True, exist_ok=True)
    p = FIGURES_ROOT / name
    p.write_bytes(data)
    return p
