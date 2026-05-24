---
name: crud
description: "Skill for the Crud area of utilities. 88 symbols across 20 files."
---

# Crud

88 symbols | 20 files | Cohesion: 63%

## When to Use

- Working with code in `scripts/`
- Understanding how list_contacts_cmd, add_notes, show_summaries work
- Modifying crud-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `scripts/kb_core/crud/calls.py` | get_call_by_source_file, delete_call, create_call, get_calls_for_org, update_call_summary (+3) |
| `scripts/kb_cli.py` | list_contacts_cmd, add_notes, show_summaries, cluster, synthesize (+2) |
| `dashboard/api/routes.py` | api_get_action_prompt, api_get_org, api_search, api_create_roadmap, api_update_roadmap (+2) |
| `scripts/kb_core/crud/quotes.py` | get_approved_quotes, approve_quote, reject_quote, bulk_approve_quotes, bulk_reject_quotes (+2) |
| `scripts/kb_core/crud/contacts.py` | get_contact, get_contact_by_id, create_contact, get_or_create_contact, add_contacts_to_call (+2) |
| `scripts/kb_core/crud/questions.py` | create_open_question, decide_question, resolve_question, abandon_question, clear_candidate_questions (+1) |
| `scripts/kb_core/crud/decisions.py` | create_decision, update_decision_status, confirm_decision, reject_decision, clear_candidate_decisions (+1) |
| `scripts/kb_core/crud/actions.py` | create_action, get_action_prompt_file, update_action_status, reject_action, clear_candidate_actions (+1) |
| `scripts/kb_core/harvest.py` | _resolve_contact, _resolve_harvest_contacts, harvest_call, deduplicate_harvest, _build_contact_map |
| `scripts/kb_core/crud/chunks.py` | get_call_chunks, summarize_chunk_batch, generate_call_batch_summaries, get_call_batch_summaries, get_call_summary_text |

## Entry Points

Start here when exploring this area:

- **`list_contacts_cmd`** (Function) — `scripts/kb_cli.py:216`
- **`add_notes`** (Function) — `scripts/kb_cli.py:317`
- **`show_summaries`** (Function) — `scripts/kb_cli.py:388`
- **`cluster`** (Function) — `scripts/kb_cli.py:1162`
- **`synthesize`** (Function) — `scripts/kb_cli.py:1237`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `list_contacts_cmd` | Function | `scripts/kb_cli.py` | 216 |
| `add_notes` | Function | `scripts/kb_cli.py` | 317 |
| `show_summaries` | Function | `scripts/kb_cli.py` | 388 |
| `cluster` | Function | `scripts/kb_cli.py` | 1162 |
| `synthesize` | Function | `scripts/kb_cli.py` | 1237 |
| `docs_reset` | Function | `scripts/kb_cli.py` | 2079 |
| `docs_stats` | Function | `scripts/kb_cli.py` | 2090 |
| `semantic_search` | Function | `scripts/kb_core/search.py` | 9 |
| `hybrid_search` | Function | `scripts/kb_core/search.py` | 80 |
| `semantic_search_with_fallback` | Function | `scripts/kb_core/search.py` | 189 |
| `get_org_context` | Function | `scripts/kb_core/search.py` | 219 |
| `draft_letter` | Function | `scripts/kb_core/quotes.py` | 319 |
| `get_db` | Function | `scripts/kb_core/db.py` | 7 |
| `compute_clusters` | Function | `scripts/kb_core/clustering.py` | 100 |
| `store_clusters` | Function | `scripts/kb_core/clustering.py` | 145 |
| `suggested_next_step` | Function | `scripts/kb_core/analysis.py` | 8 |
| `api_get_action_prompt` | Function | `dashboard/api/routes.py` | 115 |
| `api_get_org` | Function | `dashboard/api/routes.py` | 157 |
| `api_search` | Function | `dashboard/api/routes.py` | 176 |
| `api_create_roadmap` | Function | `dashboard/api/routes.py` | 248 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Harvest_call → _is_zai` | cross_community | 5 |
| `Harvest_call → _zai_complete` | cross_community | 5 |
| `Synthesize_call → Get_db` | cross_community | 5 |
| `Ingest_classify → Get_db` | cross_community | 5 |
| `Rank_quotes → Get_db` | cross_community | 5 |
| `Docs_ingest → Get_db` | cross_community | 5 |
| `Harvest_call → Get_db` | cross_community | 4 |
| `Api_get_call → Get_db` | cross_community | 4 |
| `Api_get_org → Get_db` | intra_community | 4 |
| `Api_get_org → Get_embedding` | cross_community | 4 |

## Connected Areas

| Area | Connections |
|------|-------------|
| Kb_core | 7 calls |
| Api | 5 calls |
| Scripts | 3 calls |

## How to Explore

1. `gitnexus_context({name: "list_contacts_cmd"})` — see callers and callees
2. `gitnexus_query({query: "crud"})` — find related execution flows
3. Read key files listed above for implementation details
