"""Backend-agnostic intermediate representation for document ingestion.

Two layers:
  - `Block` / `Extraction` — a faithful, low-abstraction mirror of MinerU's
    `content_list.json` (one `Block` per JSON block). We deliberately do NOT
    invent our own schema here: MinerU is a fast-moving dependency, so the IR
    keys off MinerU's neutral JSON signal rather than re-modelling it. When
    MinerU's schema shifts, this is the one place that changes.
  - `Chunk` — the generic, target-independent chunk the structural chunker
    emits. Each `IngestTarget` adapter downcasts `Chunk` into its own schema
    (KB `reference_doc_chunks`, thj `resource_chunk`). Adapter-specific fields
    (e.g. thj's section_type / chat_value) live in `Chunk.extra`, populated by
    the adapter's `enrich()` — they never leak into the core.

Validated against MinerU 3.4.0 `vlm-engine` output (2026-06-19): block types
seen are text, header, page_number, list, table, image. Section headings are
`type="text"` with `text_level>=1`; `type="header"` is the running page header
(noise); image blocks carry `image_caption`, `image_footnote`, and `content`
(text transcribed from inside the figure).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── MinerU mirror ────────────────────────────────────────────────────────────

class Block(BaseModel):
    """One block from MinerU `content_list.json`, normalized to a stable shape.

    Unknown/extra MinerU keys are tolerated (a fast-moving dependency may add
    fields); only the load-bearing ones are typed. `bbox` is MinerU's
    `[x0, y0, x1, y1]` in its own coordinate space.
    """

    model_config = {"extra": "allow"}

    type: str                                  # text | header | page_number | list | table | image
    page_idx: int = 0
    bbox: Optional[list[float]] = None

    # text-bearing blocks
    text: Optional[str] = None
    text_level: int = 0                        # >=1 ⇒ section heading (on type="text")

    # list blocks
    list_items: list[str] = Field(default_factory=list)

    # table blocks
    table_body: Optional[str] = None           # HTML
    table_caption: list[str] = Field(default_factory=list)
    table_footnote: list[str] = Field(default_factory=list)

    # image blocks
    img_path: Optional[str] = None             # relative to the extraction dir (images/<hash>.jpg)
    image_caption: list[str] = Field(default_factory=list)
    image_footnote: list[str] = Field(default_factory=list)
    content: Optional[str] = None              # text transcribed from inside the figure
    sub_type: Optional[str] = None


class Extraction(BaseModel):
    """Result of running MinerU on one document. The ordered `blocks` list is
    document reading order — the structural chunker walks it in a single pass."""

    blocks: list[Block]
    markdown: str = ""
    page_count: int = 0
    method_dir: str                            # <out>/<name>/<backend-method>/ (holds JSON + images/)
    images_dir: str                            # <method_dir>/images
    source_pdf: str


# ── Generic chunk (chunker output, adapter input) ───────────────────────────

ChunkType = Literal["text", "table", "image"]


class Chunk(BaseModel):
    """Target-independent chunk. The chunker fills the structural fields; the
    adapter's `enrich()` may add to `extra`; the adapter's `embed()`/`write()`
    consume `text` and the image fields. Embeddings are NOT held here — the
    adapter owns embedding (per the per-surface policy)."""

    chunk_type: ChunkType
    text: str                                  # the embeddable / displayable text
    order: int                                 # document reading order, 0-based
    page_number: Optional[int] = None          # 1-based

    # heading context (breadcrumb the chunk sits under)
    heading_text: Optional[str] = None
    heading_path: Optional[str] = None         # "A > B > C"
    heading_level: Optional[int] = None

    # image chunks only
    caption: Optional[str] = None
    img_local_path: Optional[str] = None       # absolute path to the cropped figure on disk
    img_ref: Optional[str] = None              # filled by the adapter's stage_image() (bucket key / md link)

    bbox: Optional[list[float]] = None

    # adapter-specific enrichment (thj: section_type, chat_value, semantic_hints). Stays
    # out of the core; the KB adapter leaves it empty.
    extra: dict[str, Any] = Field(default_factory=dict)
