---
name: skill-hook
description: "Skill for the Skill_hook area of utilities. 33 symbols across 4 files."
---

# Skill_hook

33 symbols | 4 files | Cohesion: 83%

## When to Use

- Working with code in `scripts/`
- Understanding how dot, extract_error, load_index work
- Modifying skill_hook-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `scripts/skill_hook/hook.py` | dot, extract_error, load_index, log_skill_debt, log_skill_inject (+6) |
| `scripts/skill_hook/build_index.py` | parse_registry, parse_project_trees, walk_project_tree, load_prior_manifest, load_prior_index (+6) |
| `scripts/skill_hook/prompt_hook.py` | extract_triggers, keyword_prefilter, dot, load_index, log_skill_inject (+2) |
| `scripts/skill_hook/embed_client.py` | _is_local_endpoint, _load_secret, _make_token, embed |

## Entry Points

Start here when exploring this area:

- **`dot`** (Function) — `scripts/skill_hook/hook.py:93`
- **`extract_error`** (Function) — `scripts/skill_hook/hook.py:106`
- **`load_index`** (Function) — `scripts/skill_hook/hook.py:114`
- **`log_skill_debt`** (Function) — `scripts/skill_hook/hook.py:124`
- **`log_skill_inject`** (Function) — `scripts/skill_hook/hook.py:163`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `dot` | Function | `scripts/skill_hook/hook.py` | 93 |
| `extract_error` | Function | `scripts/skill_hook/hook.py` | 106 |
| `load_index` | Function | `scripts/skill_hook/hook.py` | 114 |
| `log_skill_debt` | Function | `scripts/skill_hook/hook.py` | 124 |
| `log_skill_inject` | Function | `scripts/skill_hook/hook.py` | 163 |
| `detect_grep_on_code` | Function | `scripts/skill_hook/hook.py` | 255 |
| `log_grep_violation` | Function | `scripts/skill_hook/hook.py` | 291 |
| `emit_grep_redirect` | Function | `scripts/skill_hook/hook.py` | 307 |
| `main` | Function | `scripts/skill_hook/hook.py` | 345 |
| `extract_triggers` | Function | `scripts/skill_hook/prompt_hook.py` | 64 |
| `keyword_prefilter` | Function | `scripts/skill_hook/prompt_hook.py` | 105 |
| `dot` | Function | `scripts/skill_hook/prompt_hook.py` | 117 |
| `load_index` | Function | `scripts/skill_hook/prompt_hook.py` | 130 |
| `log_skill_inject` | Function | `scripts/skill_hook/prompt_hook.py` | 141 |
| `main` | Function | `scripts/skill_hook/prompt_hook.py` | 178 |
| `embed` | Function | `scripts/skill_hook/prompt_hook.py` | 121 |
| `embed` | Function | `scripts/skill_hook/hook.py` | 97 |
| `embed` | Function | `scripts/skill_hook/embed_client.py` | 85 |
| `parse_registry` | Function | `scripts/skill_hook/build_index.py` | 58 |
| `parse_project_trees` | Function | `scripts/skill_hook/build_index.py` | 99 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Main → _load_secret` | cross_community | 5 |
| `Build_dimension → _load_secret` | cross_community | 5 |
| `Main → _is_local_endpoint` | cross_community | 4 |
| `Build_dimension → _is_local_endpoint` | cross_community | 4 |
| `Main → _extract_grep_pattern` | intra_community | 3 |
| `Main → Extract_triggers` | intra_community | 3 |

## How to Explore

1. `gitnexus_context({name: "dot"})` — see callers and callees
2. `gitnexus_query({query: "skill_hook"})` — find related execution flows
3. Read key files listed above for implementation details
