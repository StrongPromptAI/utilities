"""The `IngestTarget` Protocol + the shared ingest orchestrator.

A target owns FOUR things beyond a DB write — chunk sizing, enrichment, figure
representation, and embedding — because those genuinely differ per backend. The
Protocol names exactly those; `run_ingest` is the one pipeline that drives any
conforming target. Pressure-tested by two real adapters (KB + thj) before the
book ships (PY-1b), so an interface gap surfaces here, not after Phase 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from ..chunk import chunk_extraction
from ..images import figure_object_name, passes_size_filter, slugify
from ..ir import Chunk, Extraction


@dataclass
class DocMeta:
    """What a target needs to identify/upsert a document, independent of schema."""
    title: str
    category: str
    source_file: str
    markdown: str = ""              # full-document text (KB stores it on reference_docs.content)
    extra: dict[str, Any] = field(default_factory=dict)  # target-specific (thj: manufacturer, model…)


@dataclass
class WriteResult:
    doc_id: Any
    chunk_count: int
    image_count: int = 0


@runtime_checkable
class IngestTarget(Protocol):
    def chunk_config(self) -> tuple[int, int]:
        """(min_chars, max_chars) for the structural chunker."""

    def enrich(self, chunk: Chunk) -> None:
        """Annotate a chunk in place with target-specific fields (into chunk.extra).
        KB: no-op. thj: section_type / chat_value / semantic_hints."""

    def stage_image(self, chunk: Chunk, doc_slug: str, index: int) -> None:
        """Upload the chunk's cropped figure and record its reference IN THE
        TARGET'S OWN WAY: KB appends an inline markdown link to chunk.text and
        sets chunk.img_ref; thj sets chunk.img_ref for the image_path column and
        leaves text alone."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed per the surface's policy (KB: in-process ONNX; thj: cloud)."""

    def write(self, doc: DocMeta, chunks: list[Chunk], embeddings: list[list[float]]) -> WriteResult:
        """Persist the document + chunks to the target schema. Upsert by identity."""


def run_ingest(ext: Extraction, target: IngestTarget, doc: DocMeta) -> WriteResult:
    """The shared pipeline: chunk → stage figures → enrich → embed → write.

    Figure representation is delegated to `target.stage_image` so KB (inline
    link) and thj (image_path column) each render references their own way —
    the orchestrator never assumes a schema.
    """
    lo, hi = target.chunk_config()
    chunks = chunk_extraction(ext, min_chars=lo, max_chars=hi)

    doc_slug = slugify(doc.title)
    fig_index = 0
    for c in chunks:
        if c.chunk_type == "image" and c.img_local_path and passes_size_filter(c.img_local_path):
            fig_index += 1
            target.stage_image(c, doc_slug, fig_index)
        target.enrich(c)

    embeddings = target.embed([c.text for c in chunks])
    if len(embeddings) != len(chunks):
        raise RuntimeError(f"embed returned {len(embeddings)} vectors for {len(chunks)} chunks")
    return target.write(doc, chunks, embeddings)


# Re-exported so adapters share the exact naming/filter the orchestrator uses.
__all__ = ["DocMeta", "WriteResult", "IngestTarget", "run_ingest",
           "figure_object_name", "passes_size_filter", "slugify"]
