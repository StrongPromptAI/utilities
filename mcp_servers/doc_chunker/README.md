# Document Chunker MCP Server

**V1**: Fixed chunking for meeting transcripts + local LM Studio embeddings + Qdrant semantic search

## Prerequisites

1. **Qdrant running** (local or remote):
   ```bash
   # Local
   docker ps | grep qdrant  # verify running on port 6333

   # Remote (e.g., obstack)
   curl http://quadrant.orb.local:6333/health
   ```

2. **LM Studio** with embedding model loaded:
   - Launch LM Studio
   - Load `nomic-embed-text` (or your preferred embedding model)
   - Start local server (default: `http://localhost:1234`)

## Installation

Add to `~/.config/claude-code/mcp_settings.json`:

```json
{
  "mcpServers": {
    "doc-chunker": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/YOUR_USERNAME/repos/utilities",
        "python",
        "mcp_servers/doc_chunker/server.py"
      ],
      "env": {
        "LM_STUDIO_BASE_URL": "http://localhost:1234/v1",
        "EMBED_MODEL": "nomic-embed-text",
        "QDRANT_HOST": "192.168.215.2",
        "QDRANT_PORT": "6333"
      }
    }
  }
}
```

Restart Claude Code to load the server.

## Tools Available

Once installed, Claude Code can use these tools in ANY session:

### `chunk_and_index`
```python
chunk_and_index(
    file_path="/path/to/meeting_transcript.txt",
    collection="meeting_20260122",  # optional, defaults to filename
    chunk_size=512,                 # optional
    overlap=50                      # optional
)
```

### `search_collection`
```python
search_collection(
    collection="meeting_20260122",
    query="What were the action items?",
    limit=5
)
```

### `list_collections`
```python
list_collections()  # Shows all indexed collections
```

### `delete_collection`
```python
delete_collection(collection="meeting_20260122")
```

## Usage Example

```
User: Index this meeting transcript
Claude: [Uses chunk_and_index tool automatically]
        "Indexed 45 chunks into collection 'meeting_202601-21_v3'"

User: What did we decide about the Qdrant integration?
Claude: [Uses search_collection tool]
        [Returns relevant chunks from the meeting]
```

## Configuration

Environment variables (set in MCP config):
- `LM_STUDIO_BASE_URL`: LM Studio API endpoint (default: `http://localhost:1234/v1`)
- `EMBED_MODEL`: Model name loaded in LM Studio (default: `nomic-embed-text`)

## Metadata Stored

Each chunk includes:
- `source`: Full file path
- `chunk_idx`: Position in document (0-indexed)
- `total_chunks`: Total chunks in document
- `text`: Actual chunk text
- `timestamp`: When indexed (ISO 8601)
- `chunk_size` / `overlap`: Chunking parameters used

## Future Enhancements

- Semantic chunking (topic boundary detection)
- PDF/image support via MinerU
- Collection merging/deduplication
- Metadata filtering in search
