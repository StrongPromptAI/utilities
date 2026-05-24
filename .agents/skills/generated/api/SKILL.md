---
name: api
description: "Skill for the Api area of utilities. 43 symbols across 12 files."
---

# Api

43 symbols | 12 files | Cohesion: 83%

## When to Use

- Working with code in `dashboard/`
- Understanding how cluster_label, get_cluster_details, expand_by_cluster work
- Modifying api-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `dashboard/api/routes.py` | _serialize, api_list_projects, api_list_questions, api_list_decisions, api_list_actions (+14) |
| `dashboard/api/storage.py` | _get_client, list_docs, read_doc, write_doc, _label |
| `scripts/kb_core/crud/questions.py` | list_questions, get_decided_questions, _attach_decided_by, get_open_question |
| `scripts/kb_core/clustering.py` | cluster_label, get_cluster_details, expand_by_cluster |
| `scripts/kb_core/crud/roadmap.py` | list_roadmap_items, get_roadmap_item |
| `scripts/kb_core/crud/decisions.py` | list_decisions, get_decision |
| `scripts/kb_core/crud/actions.py` | list_actions, get_action |
| `scripts/kb_cli.py` | resolve, dismiss_question_cmd |
| `scripts/kb_core/crud/projects.py` | list_projects |
| `scripts/kb_core/crud/org.py` | list_org |

## Entry Points

Start here when exploring this area:

- **`cluster_label`** (Function) — `scripts/kb_core/clustering.py:37`
- **`get_cluster_details`** (Function) — `scripts/kb_core/clustering.py:181`
- **`expand_by_cluster`** (Function) — `scripts/kb_core/clustering.py:235`
- **`api_list_projects`** (Function) — `dashboard/api/routes.py:62`
- **`api_list_questions`** (Function) — `dashboard/api/routes.py:69`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `cluster_label` | Function | `scripts/kb_core/clustering.py` | 37 |
| `get_cluster_details` | Function | `scripts/kb_core/clustering.py` | 181 |
| `expand_by_cluster` | Function | `scripts/kb_core/clustering.py` | 235 |
| `api_list_projects` | Function | `dashboard/api/routes.py` | 62 |
| `api_list_questions` | Function | `dashboard/api/routes.py` | 69 |
| `api_list_decisions` | Function | `dashboard/api/routes.py` | 84 |
| `api_list_actions` | Function | `dashboard/api/routes.py` | 102 |
| `api_list_calls` | Function | `dashboard/api/routes.py` | 129 |
| `api_list_orgs` | Function | `dashboard/api/routes.py` | 152 |
| `api_list_contacts` | Function | `dashboard/api/routes.py` | 169 |
| `api_search_expand` | Function | `dashboard/api/routes.py` | 186 |
| `api_list_clusters` | Function | `dashboard/api/routes.py` | 199 |
| `api_list_roadmap` | Function | `dashboard/api/routes.py` | 230 |
| `list_roadmap_items` | Function | `scripts/kb_core/crud/roadmap.py` | 28 |
| `list_questions` | Function | `scripts/kb_core/crud/questions.py` | 67 |
| `get_decided_questions` | Function | `scripts/kb_core/crud/questions.py` | 86 |
| `list_projects` | Function | `scripts/kb_core/crud/projects.py` | 15 |
| `list_org` | Function | `scripts/kb_core/crud/org.py` | 14 |
| `list_decisions` | Function | `scripts/kb_core/crud/decisions.py` | 61 |
| `list_contacts` | Function | `scripts/kb_core/crud/contacts.py` | 25 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Api_list_clusters → Get_db` | cross_community | 3 |
| `Api_list_projects → Get_db` | cross_community | 3 |
| `Api_list_questions → Get_db` | cross_community | 3 |
| `Api_list_questions → _attach_decided_by` | cross_community | 3 |
| `Api_get_question → Get_db` | cross_community | 3 |
| `Api_get_question → _attach_decided_by` | intra_community | 3 |
| `Api_list_decisions → Get_db` | cross_community | 3 |
| `Api_list_decisions → _attach_decided_by` | cross_community | 3 |
| `Api_get_decision → Get_db` | cross_community | 3 |
| `Api_get_decision → _attach_decided_by` | intra_community | 3 |

## Connected Areas

| Area | Connections |
|------|-------------|
| Crud | 12 calls |

## How to Explore

1. `gitnexus_context({name: "cluster_label"})` — see callers and callees
2. `gitnexus_query({query: "api"})` — find related execution flows
3. Read key files listed above for implementation details
