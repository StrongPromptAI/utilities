---
name: scripts
description: "Skill for the Scripts area of utilities. 49 symbols across 13 files."
---

# Scripts

49 symbols | 13 files | Cohesion: 69%

## When to Use

- Working with code in `scripts/`
- Understanding how summarize, harvest, harvest_review work
- Modifying scripts-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `scripts/kb_cli.py` | _print_llm_banner, summarize, harvest, harvest_review, decisions (+14) |
| `scripts/rebuild_faq_pages.py` | cluster_and_synthesize, get_patient_qa_by_stage, write_faq_page, write_hub_page, sync_db_slugs (+1) |
| `scripts/ingest_cs_batch.py` | parse_dote, extract_agent_name, extract_call_date, ingest_one, main |
| `scripts/kb_core/ingest/crud.py` | ingest_stats, create_ingest_source, get_ingest_source_by_file, insert_ingest_chunks |
| `scripts/kb_core/ingest/faq.py` | _get_patient_qa, _cluster_questions, _synthesize_faq_entry, assemble_faq |
| `scripts/kb_ingest.py` | _validate_ids, ingest, main |
| `scripts/ingest_reference_doc_clean.py` | strip_references_section, ingest_reference_doc |
| `scripts/kb_core/crud/projects.py` | get_project |
| `scripts/kb_core/config.py` | ensure_model |
| `scripts/kb_core/embeddings.py` | get_embedding |

## Entry Points

Start here when exploring this area:

- **`summarize`** (Function) — `scripts/kb_cli.py:360`
- **`harvest`** (Function) — `scripts/kb_cli.py:745`
- **`harvest_review`** (Function) — `scripts/kb_cli.py:922`
- **`decisions`** (Function) — `scripts/kb_cli.py:971`
- **`questions`** (Function) — `scripts/kb_cli.py:1017`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `summarize` | Function | `scripts/kb_cli.py` | 360 |
| `harvest` | Function | `scripts/kb_cli.py` | 745 |
| `harvest_review` | Function | `scripts/kb_cli.py` | 922 |
| `decisions` | Function | `scripts/kb_cli.py` | 971 |
| `questions` | Function | `scripts/kb_cli.py` | 1017 |
| `ingest_stats` | Function | `scripts/kb_cli.py` | 1626 |
| `ingest_qa_list` | Function | `scripts/kb_cli.py` | 1866 |
| `ingest_stats` | Function | `scripts/kb_core/ingest/crud.py` | 123 |
| `get_project` | Function | `scripts/kb_core/crud/projects.py` | 7 |
| `search` | Function | `scripts/kb_cli.py` | 74 |
| `ingest_load` | Function | `scripts/kb_cli.py` | 1455 |
| `parse_dote` | Function | `scripts/ingest_cs_batch.py` | 34 |
| `extract_agent_name` | Function | `scripts/ingest_cs_batch.py` | 57 |
| `extract_call_date` | Function | `scripts/ingest_cs_batch.py` | 63 |
| `ingest_one` | Function | `scripts/ingest_cs_batch.py` | 71 |
| `create_ingest_source` | Function | `scripts/kb_core/ingest/crud.py` | 8 |
| `get_ingest_source_by_file` | Function | `scripts/kb_core/ingest/crud.py` | 48 |
| `cluster_and_synthesize` | Function | `scripts/rebuild_faq_pages.py` | 63 |
| `ingest_faq` | Function | `scripts/kb_cli.py` | 1801 |
| `ingest_extract_qa` | Function | `scripts/kb_cli.py` | 1833 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Synthesize_call → Get_db` | cross_community | 5 |
| `Rank_quotes → Get_db` | cross_community | 5 |
| `Docs_ingest → Get_embedding` | cross_community | 5 |
| `Api_get_org → Get_embedding` | cross_community | 4 |
| `Main → Get_embedding` | cross_community | 4 |
| `Suggested_next_step → Get_embedding` | cross_community | 4 |
| `Ingest_faq → Get_db` | cross_community | 4 |
| `Ingest_faq → Get_embedding` | cross_community | 4 |
| `Ingest_extract_qa → Get_db` | cross_community | 4 |
| `Transcribe → Get_db` | cross_community | 4 |

## Connected Areas

| Area | Connections |
|------|-------------|
| Crud | 15 calls |
| Ingest | 3 calls |

## How to Explore

1. `gitnexus_context({name: "summarize"})` — see callers and callees
2. `gitnexus_query({query: "scripts"})` — find related execution flows
3. Read key files listed above for implementation details
