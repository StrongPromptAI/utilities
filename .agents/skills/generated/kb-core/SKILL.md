---
name: kb-core
description: "Skill for the Kb_core area of utilities. 56 symbols across 13 files."
---

# Kb_core

56 symbols | 13 files | Cohesion: 81%

## When to Use

- Working with code in `scripts/`
- Understanding how owu_stats, owu_users, owu_config work
- Modifying kb_core-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `scripts/kb_core/openwebui_runtime.py` | _gql, _get_db_url, _connect, list_users, get_config (+6) |
| `scripts/kb_core/transcripts.py` | _extract_docx, _detect_text_format, detect_and_extract, preprocess_transcript, is_obvious_filler (+3) |
| `scripts/kb_cli.py` | owu_stats, owu_users, owu_config, owu_knowledge, owu_files (+2) |
| `scripts/kb_core/chunking.py` | _split_sentences, chunk_transcript, parse_turn, _flush, chunk_by_sections (+2) |
| `scripts/kb_core/harvest.py` | harvest_from_summaries, _validate_type, _get_stakeholder_types, _get_project_name, _find_tension_batches (+1) |
| `scripts/kb_core/synthesis.py` | type_to_slug, _get_call_context, _get_harvested_items, distill_for_type, synthesize_call |
| `scripts/kb_core/quotes.py` | extract_quotes_from_batch, rank_quotes, _get_project_context, _get_user_notes |
| `scripts/kb_core/llm.py` | _is_zai, _zai_complete, complete |
| `scripts/kb_core/crud/quotes.py` | get_candidate_quotes |
| `scripts/kb_core/crud/projects.py` | get_project_docs |

## Entry Points

Start here when exploring this area:

- **`owu_stats`** (Function) — `scripts/kb_cli.py:2110`
- **`owu_users`** (Function) — `scripts/kb_cli.py:2120`
- **`owu_config`** (Function) — `scripts/kb_cli.py:2134`
- **`owu_knowledge`** (Function) — `scripts/kb_cli.py:2165`
- **`owu_files`** (Function) — `scripts/kb_cli.py:2177`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `owu_stats` | Function | `scripts/kb_cli.py` | 2110 |
| `owu_users` | Function | `scripts/kb_cli.py` | 2120 |
| `owu_config` | Function | `scripts/kb_cli.py` | 2134 |
| `owu_knowledge` | Function | `scripts/kb_cli.py` | 2165 |
| `owu_files` | Function | `scripts/kb_cli.py` | 2177 |
| `owu_chats` | Function | `scripts/kb_cli.py` | 2189 |
| `owu_models` | Function | `scripts/kb_cli.py` | 2203 |
| `list_users` | Function | `scripts/kb_core/openwebui_runtime.py` | 95 |
| `get_config` | Function | `scripts/kb_core/openwebui_runtime.py` | 111 |
| `list_knowledge` | Function | `scripts/kb_core/openwebui_runtime.py` | 127 |
| `list_files` | Function | `scripts/kb_core/openwebui_runtime.py` | 142 |
| `list_chats` | Function | `scripts/kb_core/openwebui_runtime.py` | 156 |
| `list_models` | Function | `scripts/kb_core/openwebui_runtime.py` | 172 |
| `count_all` | Function | `scripts/kb_core/openwebui_runtime.py` | 188 |
| `get_config_key` | Function | `scripts/kb_core/openwebui_runtime.py` | 203 |
| `detect_and_extract` | Function | `scripts/kb_core/transcripts.py` | 47 |
| `preprocess_transcript` | Function | `scripts/kb_core/transcripts.py` | 71 |
| `is_obvious_filler` | Function | `scripts/kb_core/transcripts.py` | 109 |
| `is_borderline` | Function | `scripts/kb_core/transcripts.py` | 119 |
| `llm_classify_filler` | Function | `scripts/kb_core/transcripts.py` | 169 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Owu_config → _gql` | intra_community | 6 |
| `Harvest_call → _is_zai` | cross_community | 5 |
| `Harvest_call → _zai_complete` | cross_community | 5 |
| `Synthesize_call → Get_db` | cross_community | 5 |
| `Rank_quotes → Get_db` | cross_community | 5 |
| `Owu_stats → _gql` | intra_community | 5 |
| `Owu_users → _gql` | intra_community | 5 |
| `Owu_knowledge → _gql` | intra_community | 5 |
| `Owu_files → _gql` | intra_community | 5 |
| `Owu_chats → _gql` | intra_community | 5 |

## Connected Areas

| Area | Connections |
|------|-------------|
| Crud | 15 calls |
| Scripts | 3 calls |

## How to Explore

1. `gitnexus_context({name: "owu_stats"})` — see callers and callees
2. `gitnexus_query({query: "kb_core"})` — find related execution flows
3. Read key files listed above for implementation details
