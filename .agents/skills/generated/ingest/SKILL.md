---
name: ingest
description: "Skill for the Ingest area of utilities. 37 symbols across 7 files."
---

# Ingest

37 symbols | 7 files | Cohesion: 80%

## When to Use

- Working with code in `scripts/`
- Understanding how ingest_pretag, regex_pretag, refine_speaker_tags work
- Modifying ingest-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `scripts/kb_core/ingest/pretag.py` | _get_name_variants, _build_patterns, _score_segment, _split_blob, regex_pretag (+5) |
| `scripts/kb_core/ingest/docs.py` | strip_frontmatter, build_source_url, _iter_sections, current_headings, emit (+5) |
| `scripts/kb_cli.py` | ingest_pretag, ingest_classify, ingest_list, ingest_questions, docs_ingest |
| `scripts/kb_core/ingest/questions.py` | _get_inscope_chunks, _cluster_chunks, _parse_embedding, _extract_cluster_question, extract_question_taxonomy |
| `scripts/kb_core/ingest/crud.py` | get_ingest_source, list_ingest_sources, update_classification |
| `scripts/kb_core/ingest/classify.py` | classify_source, classify_batch |
| `scripts/kb_core/crud/docs.py` | upsert_doc_chunks, purge_stale |

## Entry Points

Start here when exploring this area:

- **`ingest_pretag`** (Function) — `scripts/kb_cli.py:1919`
- **`regex_pretag`** (Function) — `scripts/kb_core/ingest/pretag.py:123`
- **`refine_speaker_tags`** (Function) — `scripts/kb_core/ingest/pretag.py:186`
- **`scrub_pii`** (Function) — `scripts/kb_core/ingest/pretag.py:245`
- **`classify_backend_required`** (Function) — `scripts/kb_core/ingest/pretag.py:298`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `ingest_pretag` | Function | `scripts/kb_cli.py` | 1919 |
| `regex_pretag` | Function | `scripts/kb_core/ingest/pretag.py` | 123 |
| `refine_speaker_tags` | Function | `scripts/kb_core/ingest/pretag.py` | 186 |
| `scrub_pii` | Function | `scripts/kb_core/ingest/pretag.py` | 245 |
| `classify_backend_required` | Function | `scripts/kb_core/ingest/pretag.py` | 298 |
| `pretag_diarized` | Function | `scripts/kb_core/ingest/pretag.py` | 346 |
| `pretag_and_scrub` | Function | `scripts/kb_core/ingest/pretag.py` | 409 |
| `ingest_classify` | Function | `scripts/kb_cli.py` | 1595 |
| `ingest_list` | Function | `scripts/kb_cli.py` | 1672 |
| `get_ingest_source` | Function | `scripts/kb_core/ingest/crud.py` | 33 |
| `list_ingest_sources` | Function | `scripts/kb_core/ingest/crud.py` | 59 |
| `update_classification` | Function | `scripts/kb_core/ingest/crud.py` | 107 |
| `classify_source` | Function | `scripts/kb_core/ingest/classify.py` | 68 |
| `classify_batch` | Function | `scripts/kb_core/ingest/classify.py` | 111 |
| `strip_frontmatter` | Function | `scripts/kb_core/ingest/docs.py` | 43 |
| `build_source_url` | Function | `scripts/kb_core/ingest/docs.py` | 56 |
| `current_headings` | Function | `scripts/kb_core/ingest/docs.py` | 87 |
| `emit` | Function | `scripts/kb_core/ingest/docs.py` | 90 |
| `ingest_questions` | Function | `scripts/kb_cli.py` | 1768 |
| `extract_question_taxonomy` | Function | `scripts/kb_core/ingest/questions.py` | 114 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Docs_ingest → Current_headings` | cross_community | 7 |
| `Ingest_pretag → _get_name_variants` | intra_community | 6 |
| `Ingest_pretag → _split_blob` | intra_community | 5 |
| `Ingest_pretag → _score_segment` | intra_community | 5 |
| `Ingest_classify → Get_db` | cross_community | 5 |
| `Docs_ingest → Strip_frontmatter` | cross_community | 5 |
| `Docs_ingest → Build_source_url` | cross_community | 5 |
| `Docs_ingest → _chunk_section` | cross_community | 5 |
| `Docs_ingest → Get_db` | cross_community | 5 |
| `Docs_ingest → Get_embedding` | cross_community | 5 |

## Connected Areas

| Area | Connections |
|------|-------------|
| Crud | 8 calls |
| Scripts | 5 calls |

## How to Explore

1. `gitnexus_context({name: "ingest_pretag"})` — see callers and callees
2. `gitnexus_query({query: "ingest"})` — find related execution flows
3. Read key files listed above for implementation details
