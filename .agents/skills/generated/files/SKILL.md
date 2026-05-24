---
name: files
description: "Skill for the Files area of utilities. 20 symbols across 2 files."
---

# Files

20 symbols | 2 files | Cohesion: 100%

## When to Use

- Working with code in `services/`
- Understanding how login_redirect_html, error_html, file_browser_html work
- Modifying files-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `services/files/main.py` | _verify_session_jwt, require_user, home, _safe_filename, api_upload_url (+9) |
| `services/files/templates.py` | _page, login_redirect_html, error_html, _fmt_size, _fmt_time (+1) |

## Entry Points

Start here when exploring this area:

- **`login_redirect_html`** (Function) — `services/files/templates.py:377`
- **`error_html`** (Function) — `services/files/templates.py:389`
- **`file_browser_html`** (Function) — `services/files/templates.py:455`
- **`require_user`** (Function) — `services/files/main.py:225`
- **`home`** (Function) — `services/files/main.py:235`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `login_redirect_html` | Function | `services/files/templates.py` | 377 |
| `error_html` | Function | `services/files/templates.py` | 389 |
| `file_browser_html` | Function | `services/files/templates.py` | 455 |
| `require_user` | Function | `services/files/main.py` | 225 |
| `home` | Function | `services/files/main.py` | 235 |
| `api_upload_url` | Function | `services/files/main.py` | 387 |
| `api_download` | Function | `services/files/main.py` | 408 |
| `api_stream` | Function | `services/files/main.py` | 438 |
| `api_rename` | Function | `services/files/main.py` | 468 |
| `api_delete` | Function | `services/files/main.py` | 504 |
| `oidc_callback` | Function | `services/files/main.py` | 295 |
| `_page` | Function | `services/files/templates.py` | 368 |
| `_fmt_size` | Function | `services/files/templates.py` | 441 |
| `_fmt_time` | Function | `services/files/templates.py` | 451 |
| `_verify_session_jwt` | Function | `services/files/main.py` | 184 |
| `_safe_filename` | Function | `services/files/main.py` | 349 |
| `_make_session_jwt` | Function | `services/files/main.py` | 171 |
| `_set_session_cookie` | Function | `services/files/main.py` | 194 |
| `_get_jwks` | Function | `services/files/main.py` | 206 |
| `_verify_id_token` | Function | `services/files/main.py` | 214 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Home → _page` | intra_community | 3 |
| `Home → _fmt_size` | intra_community | 3 |
| `Home → _fmt_time` | intra_community | 3 |
| `Oidc_callback → _get_jwks` | intra_community | 3 |
| `Oidc_callback → _make_session_jwt` | intra_community | 3 |

## How to Explore

1. `gitnexus_context({name: "login_redirect_html"})` — see callers and callees
2. `gitnexus_query({query: "files"})` — find related execution flows
3. Read key files listed above for implementation details
