---
name: tts
description: "Skill for the Tts area of utilities. 11 symbols across 1 files."
---

# Tts

11 symbols | 1 files | Cohesion: 100%

## When to Use

- Working with code in `services/`
- Understanding how speech, require_tts_token, health work
- Modifying tts-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `services/tts/app.py` | _log_event, _samples_to_pcm_s16le, _pcm_to_wav, speech, _log (+6) |

## Entry Points

Start here when exploring this area:

- **`speech`** (Function) — `services/tts/app.py:270`
- **`require_tts_token`** (Function) — `services/tts/app.py:102`
- **`health`** (Function) — `services/tts/app.py:205`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `speech` | Function | `services/tts/app.py` | 270 |
| `require_tts_token` | Function | `services/tts/app.py` | 102 |
| `health` | Function | `services/tts/app.py` | 205 |
| `_log_event` | Function | `services/tts/app.py` | 86 |
| `_samples_to_pcm_s16le` | Function | `services/tts/app.py` | 249 |
| `_pcm_to_wav` | Function | `services/tts/app.py` | 254 |
| `_log` | Function | `services/tts/app.py` | 81 |
| `_patch_kokoro_speed_dtype` | Function | `services/tts/app.py` | 113 |
| `_load_kokoro` | Function | `services/tts/app.py` | 149 |
| `_validate_token` | Function | `services/tts/app.py` | 92 |
| `_recent_synth_count` | Function | `services/tts/app.py` | 197 |

## How to Explore

1. `gitnexus_context({name: "speech"})` — see callers and callees
2. `gitnexus_query({query: "tts"})` — find related execution flows
3. Read key files listed above for implementation details
