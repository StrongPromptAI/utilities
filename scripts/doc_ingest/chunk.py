"""Structural chunker — `Extraction` → `list[Chunk]`, in one document-order pass.

This is the generic half lifted from thj's `equipment_manuals/chunker.py`
(heading stack, text accumulation, the image↔text association, size-bounded
splitting). Everything equipment-specific — section_type, chat_value,
semantic_hints, equipment boilerplate noise — was deliberately NOT ported; that
lives in the thj adapter's `enrich()`. The `lint_core_no_equipment` gate
enforces that this module never re-grows those.

The image↔text association (the reason this pipeline beats a text-only ingest):
each image chunk's text is assembled from its caption + the text transcribed
inside the figure + the paragraph before it + the paragraph after it + its
heading breadcrumb — so a retrieved figure carries the context that explains it.
"""
from __future__ import annotations

import re
from typing import Optional

from .ir import Block, Chunk, Extraction

# Drop blocks that are page furniture, never content.
_FURNITURE = {"header", "page_number"}
# ToC dot-leaders: "Maintenance . . . . 18" — generic (page-layout), not domain noise.
_TOC_RE = re.compile(r"(?:\.\s*){3,}\s*\d|\.{3,}\s*\d")


def _is_heading(b: Block) -> bool:
    """A `text_level>=1` block is a real section heading unless it looks like a
    mis-tagged sentence fragment (MinerU sometimes flags short lowercase lines,
    e.g. 'thus'). Title/upper case or multi-word ⇒ heading; a single short
    lowercase word ⇒ demote to body."""
    if b.type != "text" or b.text_level < 1 or not b.text:
        return False
    t = b.text.strip()
    if len(t) < 5:
        return False
    if t.islower() and len(t.split()) <= 2:
        return False
    return True


def _html_table_to_text(html: str) -> str:
    """Flatten a `<table>` HTML body to readable rows (cells joined by ' | ',
    rows by newline). Good enough for embedding/display; structure-faithful
    table parsing is a target concern, not the core's."""
    if not html:
        return ""
    rows = re.findall(r"<tr.*?>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)
    out = []
    for row in rows:
        cells = re.findall(r"<t[dh].*?>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)
        cells = [re.sub(r"<[^>]+>", " ", c) for c in cells]
        cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
        if any(cells):
            out.append(" | ".join(cells))
    return "\n".join(out)


def _split_paragraphs(text: str, max_chars: int) -> list[str]:
    """Split an over-long text chunk on paragraph then sentence boundaries,
    keeping each piece <= max_chars. Ported from thj's chunker."""
    if len(text) <= max_chars:
        return [text]
    out, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 <= max_chars:
            cur = f"{cur}\n\n{para}".strip()
        else:
            if cur:
                out.append(cur)
            if len(para) > max_chars:
                out.extend(_split_sentences(para, max_chars))
                cur = ""
            else:
                cur = para
    if cur:
        out.append(cur)
    return out


def _split_sentences(text: str, max_chars: int) -> list[str]:
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if len(cur) + len(sent) + 1 <= max_chars:
            cur = f"{cur} {sent}".strip()
        else:
            if cur:
                out.append(cur)
            cur = sent
    if cur:
        out.append(cur)
    return out


def chunk_extraction(
    ext: Extraction,
    min_chars: int = 100,
    max_chars: int = 3000,
) -> list[Chunk]:
    """Walk the blocks in reading order, emitting text/table/image chunks.

    `min_chars`/`max_chars` are the adapter's `chunk_config()` (KB book prose
    100/3000; thj 50/2000). They bound text chunks only — table and image
    chunks are atomic and emitted regardless of length.
    """
    from pathlib import Path

    chunks: list[Chunk] = []
    order = 0
    heading_stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    section_page: Optional[int] = None
    section_bbox: Optional[list[float]] = None
    section_heading: Optional[str] = None
    images_dir = Path(ext.images_dir)

    def heading_path() -> Optional[str]:
        return " > ".join(t for _, t in heading_stack) if heading_stack else None

    def heading_level() -> Optional[int]:
        return heading_stack[-1][0] if heading_stack else None

    def flush_text():
        nonlocal order, section_page, section_bbox
        text = "\n\n".join(buffer).strip()
        buffer.clear()
        if not text or len(text) < min_chars:
            return
        if _TOC_RE.search(text) and len(text) < 400:
            return  # a short ToC-leader block; generic page furniture
        hp, hl, ht = heading_path(), heading_level(), section_heading
        for piece in _split_paragraphs(text, max_chars):
            if len(piece.strip()) < min_chars:
                continue
            chunks.append(Chunk(
                chunk_type="text", text=piece.strip(), order=order,
                page_number=section_page, heading_text=ht, heading_path=hp,
                heading_level=hl, bbox=section_bbox,
            ))
            order += 1

    def peek_next_text(i: int) -> str:
        """Look ahead for the next body-text block (for image post-context)."""
        for k in range(i + 1, min(i + 5, len(ext.blocks))):
            nb = ext.blocks[k]
            if nb.type in _FURNITURE or nb.type in ("image", "table"):
                continue
            t = (nb.text or "").strip()
            if t and len(t) > 10:
                return t[:200]
            break
        return ""

    for i, b in enumerate(ext.blocks):
        page_number = b.page_idx + 1

        if b.type in _FURNITURE:
            continue

        if _is_heading(b):
            flush_text()
            level = max(1, b.text_level)
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, b.text.strip()))
            section_heading = b.text.strip()
            section_page = page_number
            section_bbox = b.bbox
            continue

        if b.type == "table":
            flush_text()
            cap = b.table_caption[0].strip() if b.table_caption else ""
            body = _html_table_to_text(b.table_body or "")
            text = f"{cap}\n{body}".strip() if cap else body
            if text:
                chunks.append(Chunk(
                    chunk_type="table", text=text, order=order, page_number=page_number,
                    caption=cap or None, heading_text=section_heading,
                    heading_path=heading_path(), heading_level=heading_level(), bbox=b.bbox,
                ))
                order += 1
            continue

        if b.type == "image":
            pre = buffer[-1].strip()[:200] if buffer else ""
            flush_text()
            if not b.img_path:
                continue
            caption = (b.image_caption[0].strip() if b.image_caption else "")
            in_figure = (b.content or "").strip()
            post = peek_next_text(i)
            hp = heading_path()
            parts = [p for p in (f"[{hp}]" if hp else "", caption, in_figure, pre, post) if p]
            context = "\n\n".join(parts) if parts else caption
            chunks.append(Chunk(
                chunk_type="image", text=context or caption or "(figure)", order=order,
                page_number=page_number, caption=caption or None,
                img_local_path=str(images_dir / Path(b.img_path).name),
                heading_text=section_heading, heading_path=hp,
                heading_level=heading_level(), bbox=b.bbox,
            ))
            order += 1
            continue

        if b.type == "list" and b.list_items:
            buffer.append("\n".join(b.list_items))
            if section_page is None:
                section_page, section_bbox = page_number, b.bbox
            continue

        # body text
        if b.text and b.text.strip():
            buffer.append(b.text.strip())
            if section_page is None:
                section_page, section_bbox = page_number, b.bbox

    flush_text()
    return chunks
