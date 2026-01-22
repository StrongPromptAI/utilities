"""
Document Chunking + Qdrant Semantic Search MCP Server

V1: Fixed chunking for meeting transcripts, local LM Studio embeddings
"""

from mcp.server.fastmcp import FastMCP
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from openai import OpenAI
from pathlib import Path
from datetime import datetime
import hashlib
import os

# Initialize
mcp = FastMCP("doc-chunker")

# Qdrant client (remote instance on obstack)
qdrant_host = os.getenv("QDRANT_HOST", "192.168.215.2")
qdrant_port = int(os.getenv("QDRANT_PORT", "6333"))
qdrant = QdrantClient(host=qdrant_host, port=qdrant_port)

# LM Studio client (OpenAI-compatible API)
LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
lm_studio = OpenAI(base_url=LM_STUDIO_BASE_URL, api_key="not-needed")

# Embedding model (loaded in LM Studio)
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768  # nomic-embed-text dimension


def get_embedding(text: str) -> list[float]:
    """Generate embedding via local LM Studio."""
    response = lm_studio.embeddings.create(
        model=EMBED_MODEL,
        input=text
    )
    return response.data[0].embedding


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    """Fixed-size chunking with character overlap."""
    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():  # Skip empty chunks
            chunks.append(chunk)
        start = end - overlap

    return chunks


@mcp.tool()
def chunk_and_index(
    file_path: str,
    collection: str = None,
    chunk_size: int = 512,
    overlap: int = 50
) -> dict:
    """
    Read file, chunk, embed, and upsert to Qdrant collection.

    Args:
        file_path: Path to document
        collection: Collection name (defaults to filename stem)
        chunk_size: Characters per chunk
        overlap: Character overlap between chunks

    Returns:
        {"collection": str, "chunks_indexed": int, "file": str}
    """
    path = Path(file_path).expanduser()
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    # Default collection name: filename stem
    if not collection:
        collection = path.stem.replace(" ", "_").lower()

    # Read file
    text = path.read_text()

    # Chunk
    chunks = chunk_text(text, chunk_size, overlap)
    if not chunks:
        return {"error": "No chunks generated (empty file?)"}

    # Create collection if doesn't exist
    try:
        qdrant.get_collection(collection)
    except Exception:
        qdrant.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE)
        )

    # Embed and upsert
    timestamp = datetime.now().isoformat()
    points = []

    for idx, chunk in enumerate(chunks):
        embedding = get_embedding(chunk)

        # Use hash of content as point ID for deduplication
        point_id = hashlib.md5(f"{file_path}:{idx}".encode()).hexdigest()[:16]
        point_id_int = int(point_id, 16) % (2**63)  # Qdrant needs int ID

        points.append(PointStruct(
            id=point_id_int,
            vector=embedding,
            payload={
                "source": str(path),
                "chunk_idx": idx,
                "total_chunks": len(chunks),
                "text": chunk,
                "timestamp": timestamp,
                "chunk_size": chunk_size,
                "overlap": overlap
            }
        ))

    qdrant.upsert(collection_name=collection, points=points)

    return {
        "collection": collection,
        "chunks_indexed": len(chunks),
        "file": str(path)
    }


@mcp.tool()
def search_collection(
    collection: str,
    query: str,
    limit: int = 5
) -> list[dict]:
    """
    Semantic search in Qdrant collection.

    Args:
        collection: Collection name
        query: Search query
        limit: Max results

    Returns:
        List of {"text": str, "score": float, "source": str, "chunk_idx": int}
    """
    try:
        qdrant.get_collection(collection)
    except Exception:
        return [{"error": f"Collection '{collection}' not found"}]

    # Embed query
    query_embedding = get_embedding(query)

    # Search
    results = qdrant.search(
        collection_name=collection,
        query_vector=query_embedding,
        limit=limit
    )

    # Format results
    hits = []
    for hit in results:
        hits.append({
            "text": hit.payload.get("text", ""),
            "score": hit.score,
            "source": hit.payload.get("source", ""),
            "chunk_idx": hit.payload.get("chunk_idx", -1),
            "total_chunks": hit.payload.get("total_chunks", -1)
        })

    return hits


@mcp.tool()
def list_collections() -> list[dict]:
    """
    List all Qdrant collections with metadata.

    Returns:
        List of {"name": str, "vectors_count": int, "points_count": int}
    """
    collections = qdrant.get_collections().collections

    result = []
    for collection in collections:
        info = qdrant.get_collection(collection.name)
        result.append({
            "name": collection.name,
            "vectors_count": info.vectors_count,
            "points_count": info.points_count
        })

    return result


@mcp.tool()
def delete_collection(collection: str) -> dict:
    """
    Delete a Qdrant collection.

    Args:
        collection: Collection name

    Returns:
        {"deleted": str, "success": bool}
    """
    try:
        qdrant.delete_collection(collection_name=collection)
        return {"deleted": collection, "success": True}
    except Exception as e:
        return {"error": str(e), "success": False}


if __name__ == "__main__":
    mcp.run(transport="stdio")
