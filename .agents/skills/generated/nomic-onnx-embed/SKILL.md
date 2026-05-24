---
name: nomic-onnx-embed
description: "Skill for the Nomic_onnx_embed area of utilities. 4 symbols across 1 files."
---

# Nomic_onnx_embed

4 symbols | 1 files | Cohesion: 100%

## When to Use

- Working with code in `packages/`
- Understanding how generate_embedding, generate_embeddings work
- Modifying nomic_onnx_embed-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `packages/embed/src/nomic_onnx_embed/embed.py` | _get_session, _embed, generate_embedding, generate_embeddings |

## Entry Points

Start here when exploring this area:

- **`generate_embedding`** (Function) — `packages/embed/src/nomic_onnx_embed/embed.py:66`
- **`generate_embeddings`** (Function) — `packages/embed/src/nomic_onnx_embed/embed.py:72`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `generate_embedding` | Function | `packages/embed/src/nomic_onnx_embed/embed.py` | 66 |
| `generate_embeddings` | Function | `packages/embed/src/nomic_onnx_embed/embed.py` | 72 |
| `_get_session` | Function | `packages/embed/src/nomic_onnx_embed/embed.py` | 21 |
| `_embed` | Function | `packages/embed/src/nomic_onnx_embed/embed.py` | 35 |

## How to Explore

1. `gitnexus_context({name: "generate_embedding"})` — see callers and callees
2. `gitnexus_query({query: "nomic_onnx_embed"})` — find related execution flows
3. Read key files listed above for implementation details
