"""Figure staging helpers — generic across targets.

The chunker points each image chunk at MinerU's cropped figure (a hash-named
jpg under the extraction's images/ dir). These helpers give that figure a
deterministic, namespaced name and filter out sub-figure artifacts before a
target uploads it. Upload + the chunk's reference representation are
target-specific (KB → inline markdown link to oxp.files; thj → image_path
column to its bucket) and live in the adapter's `stage_image`.
"""
from __future__ import annotations

import re
from pathlib import Path

MIN_FIGURE_BYTES = 1000  # skip <1KB crops — dividers, specks, OCR artifacts


def slugify(text: str) -> str:
    """Title → filesystem/URL-safe slug. 'Corporate Lifecycles' → 'corporate-lifecycles'."""
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_-]+", "-", s) or "doc"


def figure_object_name(doc_slug: str, index: int, src_path: str) -> str:
    """Flat, namespaced object name for a figure: '<slug>__fig_NN.jpg'.

    Flat (no subfolders) because oxp.files' `/public/{name}` proxy + its
    traversal-rejecting `_safe_filename` serve a single prefix; the slug prefix
    namespaces one document's figures within `public/`.
    """
    suffix = Path(src_path).suffix or ".jpg"
    return f"{doc_slug}__fig_{index:02d}{suffix}"


def passes_size_filter(src_path: str) -> bool:
    p = Path(src_path)
    return p.is_file() and p.stat().st_size >= MIN_FIGURE_BYTES
