---
name: oidc-otp
description: "Skill for the Oidc-otp area of utilities. 12 symbols across 1 files."
---

# Oidc-otp

12 symbols | 1 files | Cohesion: 86%

## When to Use

- Working with code in `services/`
- Understanding how authorize_post, otp_get, authorize_get work
- Modifying oidc-otp-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `services/oidc-otp/main.py` | _title_for, _page, _email_form, _otp_form, authorize_post (+7) |

## Entry Points

Start here when exploring this area:

- **`authorize_post`** (Function) — `services/oidc-otp/main.py:569`
- **`otp_get`** (Function) — `services/oidc-otp/main.py:624`
- **`authorize_get`** (Function) — `services/oidc-otp/main.py:541`
- **`otp_post`** (Function) — `services/oidc-otp/main.py:634`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `authorize_post` | Function | `services/oidc-otp/main.py` | 569 |
| `otp_get` | Function | `services/oidc-otp/main.py` | 624 |
| `authorize_get` | Function | `services/oidc-otp/main.py` | 541 |
| `otp_post` | Function | `services/oidc-otp/main.py` | 634 |
| `_title_for` | Function | `services/oidc-otp/main.py` | 196 |
| `_page` | Function | `services/oidc-otp/main.py` | 420 |
| `_email_form` | Function | `services/oidc-otp/main.py` | 429 |
| `_otp_form` | Function | `services/oidc-otp/main.py` | 453 |
| `_issue_auth_code` | Function | `services/oidc-otp/main.py` | 480 |
| `_redirect_with_code` | Function | `services/oidc-otp/main.py` | 492 |
| `_set_sso_cookie` | Function | `services/oidc-otp/main.py` | 497 |
| `_sso_email_from_cookie` | Function | `services/oidc-otp/main.py` | 509 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Authorize_get → _title_for` | cross_community | 3 |
| `Authorize_get → _page` | cross_community | 3 |
| `Otp_post → _title_for` | cross_community | 3 |
| `Otp_post → _page` | cross_community | 3 |
| `Authorize_post → _title_for` | intra_community | 3 |
| `Authorize_post → _page` | intra_community | 3 |
| `Otp_get → _title_for` | intra_community | 3 |
| `Otp_get → _page` | intra_community | 3 |

## How to Explore

1. `gitnexus_context({name: "authorize_post"})` — see callers and callees
2. `gitnexus_query({query: "oidc-otp"})` — find related execution flows
3. Read key files listed above for implementation details
