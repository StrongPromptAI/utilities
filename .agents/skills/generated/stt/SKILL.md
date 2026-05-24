---
name: stt
description: "Skill for the Stt area of utilities. 6 symbols across 1 files."
---

# Stt

6 symbols | 1 files | Cohesion: 100%

## When to Use

- Working with code in `services/`
- Understanding how transcribe work
- Modifying stt-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `services/stt/app.py` | _log, _validate_token, _load_stt, _normalize_transcript, transcribe (+1) |

## Entry Points

Start here when exploring this area:

- **`transcribe`** (Function) — `services/stt/app.py:162`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `transcribe` | Function | `services/stt/app.py` | 162 |
| `_log` | Function | `services/stt/app.py` | 63 |
| `_validate_token` | Function | `services/stt/app.py` | 68 |
| `_load_stt` | Function | `services/stt/app.py` | 79 |
| `_normalize_transcript` | Function | `services/stt/app.py` | 152 |
| `_expire` | Function | `services/stt/app.py` | 199 |

## How to Explore

1. `gitnexus_context({name: "transcribe"})` — see callers and callees
2. `gitnexus_query({query: "stt"})` — find related execution flows
3. Read key files listed above for implementation details
