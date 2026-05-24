---
name: whisper
description: "Skill for the Whisper area of utilities. 6 symbols across 1 files."
---

# Whisper

6 symbols | 1 files | Cohesion: 100%

## When to Use

- Working with code in `services/`
- Understanding how require_stt_token, transcribe work
- Modifying whisper-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `services/whisper/app.py` | _log, _load_model, _validate_token, require_stt_token, _transcribe_blocking (+1) |

## Entry Points

Start here when exploring this area:

- **`require_stt_token`** (Function) — `services/whisper/app.py:52`
- **`transcribe`** (Function) — `services/whisper/app.py:121`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `require_stt_token` | Function | `services/whisper/app.py` | 52 |
| `transcribe` | Function | `services/whisper/app.py` | 121 |
| `_log` | Function | `services/whisper/app.py` | 37 |
| `_load_model` | Function | `services/whisper/app.py` | 61 |
| `_validate_token` | Function | `services/whisper/app.py` | 42 |
| `_transcribe_blocking` | Function | `services/whisper/app.py` | 95 |

## How to Explore

1. `gitnexus_context({name: "require_stt_token"})` — see callers and callees
2. `gitnexus_query({query: "whisper"})` — find related execution flows
3. Read key files listed above for implementation details
