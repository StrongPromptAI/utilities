---
name: embed
description: "Skill for the Embed area of utilities. 7 symbols across 2 files."
---

# Embed

7 symbols | 2 files | Cohesion: 100%

## When to Use

- Working with code in `services/`
- Understanding how generate_embedding, generate_embeddings, embed work
- Modifying embed-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `services/embed/nomic_embed.py` | _get_session, _embed, generate_embedding, generate_embeddings |
| `services/embed/app.py` | _warmup, embed, openai_embeddings |

## Entry Points

Start here when exploring this area:

- **`generate_embedding`** (Function) — `services/embed/nomic_embed.py:88`
- **`generate_embeddings`** (Function) — `services/embed/nomic_embed.py:94`
- **`embed`** (Function) — `services/embed/app.py:132`
- **`openai_embeddings`** (Function) — `services/embed/app.py:155`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `generate_embedding` | Function | `services/embed/nomic_embed.py` | 88 |
| `generate_embeddings` | Function | `services/embed/nomic_embed.py` | 94 |
| `embed` | Function | `services/embed/app.py` | 132 |
| `openai_embeddings` | Function | `services/embed/app.py` | 155 |
| `_get_session` | Function | `services/embed/nomic_embed.py` | 26 |
| `_embed` | Function | `services/embed/nomic_embed.py` | 57 |
| `_warmup` | Function | `services/embed/app.py` | 75 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Embed → _get_session` | intra_community | 3 |
| `Openai_embeddings → _get_session` | intra_community | 3 |
| `Generate_embedding → _get_session` | intra_community | 3 |
| `Generate_embeddings → _get_session` | intra_community | 3 |

## How to Explore

1. `gitnexus_context({name: "generate_embedding"})` — see callers and callees
2. `gitnexus_query({query: "embed"})` — find related execution flows
3. Read key files listed above for implementation details
