---
name: sftpgo-bridge
description: "Skill for the Sftpgo-bridge area of utilities. 16 symbols across 1 files."
---

# Sftpgo-bridge

16 symbols | 1 files | Cohesion: 71%

## When to Use

- Working with code in `services/`
- Understanding how receive_event work
- Modifying sftpgo-bridge-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `services/sftpgo-bridge/main.py` | _ow_find_user_id, _ow_get_or_create_collection, _ow_upload_file, _ow_wait_for_processing, _ow_attach_file_to_collection (+11) |

## Entry Points

Start here when exploring this area:

- **`receive_event`** (Function) — `services/sftpgo-bridge/main.py:352`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `receive_event` | Function | `services/sftpgo-bridge/main.py` | 352 |
| `_ow_find_user_id` | Function | `services/sftpgo-bridge/main.py` | 147 |
| `_ow_get_or_create_collection` | Function | `services/sftpgo-bridge/main.py` | 162 |
| `_ow_upload_file` | Function | `services/sftpgo-bridge/main.py` | 205 |
| `_ow_wait_for_processing` | Function | `services/sftpgo-bridge/main.py` | 214 |
| `_ow_attach_file_to_collection` | Function | `services/sftpgo-bridge/main.py` | 228 |
| `_ow_detach_file_from_collection` | Function | `services/sftpgo-bridge/main.py` | 238 |
| `_ow_delete_file` | Function | `services/sftpgo-bridge/main.py` | 248 |
| `_s3_get` | Function | `services/sftpgo-bridge/main.py` | 263 |
| `_find_synced` | Function | `services/sftpgo-bridge/main.py` | 285 |
| `_handle_upload` | Function | `services/sftpgo-bridge/main.py` | 298 |
| `_verify_secret` | Function | `services/sftpgo-bridge/main.py` | 139 |
| `_s3_key_for` | Function | `services/sftpgo-bridge/main.py` | 256 |
| `_log_action` | Function | `services/sftpgo-bridge/main.py` | 272 |
| `_handle_delete` | Function | `services/sftpgo-bridge/main.py` | 325 |
| `_handle_rename` | Function | `services/sftpgo-bridge/main.py` | 344 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Receive_event → _s3_key_for` | intra_community | 4 |
| `Receive_event → _find_synced` | cross_community | 4 |
| `Receive_event → _ow_detach_file_from_collection` | cross_community | 4 |
| `Receive_event → _ow_delete_file` | cross_community | 4 |

## How to Explore

1. `gitnexus_context({name: "receive_event"})` — see callers and callees
2. `gitnexus_query({query: "sftpgo-bridge"})` — find related execution flows
3. Read key files listed above for implementation details
