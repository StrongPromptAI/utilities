"""doc_ingest — backend-agnostic document ingestion.

Pipeline: MinerU extract → IR (`Block`/`Extraction`) → structural chunk
(`Chunk`) → an `IngestTarget` adapter (KB / thj) that enriches, embeds,
stages figures, and writes its own schema. See
symlink_docs/plans/26-6-19-document-ingestion-utility.md.
"""
from .ir import Block, Chunk, Extraction

__all__ = ["Block", "Chunk", "Extraction"]
