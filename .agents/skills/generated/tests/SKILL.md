---
name: tests
description: "Skill for the Tests area of utilities. 34 symbols across 5 files."
---

# Tests

34 symbols | 5 files | Cohesion: 98%

## When to Use

- Working with code in `devops_agent/`
- Understanding how find_rollback_target, test_embed_valid_token, test_embed_wrong_audience_rejected work
- Modifying tests-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `devops_agent/tests/test_rollback.py` | _make_deployment, _mock_deployments, test_finds_removed_target, test_excludes_current_deployment, test_respects_max_age (+6) |
| `services/tests/test_shared_svcs.py` | _tok, test_embed_valid_token, test_embed_wrong_audience_rejected, test_embed_expired_token_rejected, test_embed_openai_compat_endpoint (+6) |
| `devops_agent/tests/test_report.py` | _window, _write_audit_lines, test_empty_file, test_missing_file, test_malformed_lines_skipped (+4) |
| `devops_agent/rollback.py` | _parse_deployment_time, find_rollback_target |
| `devops_agent/report.py` | _parse_audit_trail |

## Entry Points

Start here when exploring this area:

- **`find_rollback_target`** (Function) — `devops_agent/rollback.py:48`
- **`test_embed_valid_token`** (Function) — `services/tests/test_shared_svcs.py:80`
- **`test_embed_wrong_audience_rejected`** (Function) — `services/tests/test_shared_svcs.py:101`
- **`test_embed_expired_token_rejected`** (Function) — `services/tests/test_shared_svcs.py:109`
- **`test_embed_openai_compat_endpoint`** (Function) — `services/tests/test_shared_svcs.py:116`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `find_rollback_target` | Function | `devops_agent/rollback.py` | 48 |
| `test_embed_valid_token` | Function | `services/tests/test_shared_svcs.py` | 80 |
| `test_embed_wrong_audience_rejected` | Function | `services/tests/test_shared_svcs.py` | 101 |
| `test_embed_expired_token_rejected` | Function | `services/tests/test_shared_svcs.py` | 109 |
| `test_embed_openai_compat_endpoint` | Function | `services/tests/test_shared_svcs.py` | 116 |
| `test_stt_wrong_audience_closes_4401` | Function | `services/tests/test_shared_svcs.py` | 163 |
| `test_stt_expired_token_closes_4401` | Function | `services/tests/test_shared_svcs.py` | 176 |
| `test_stt_valid_token_stays_open` | Function | `services/tests/test_shared_svcs.py` | 188 |
| `test_stt_server_closes_at_token_expiry` | Function | `services/tests/test_shared_svcs.py` | 206 |
| `test_stt_three_concurrent_connections` | Function | `services/tests/test_shared_svcs.py` | 240 |
| `connect_and_hold` | Function | `services/tests/test_shared_svcs.py` | 242 |
| `test_finds_removed_target` | Method | `devops_agent/tests/test_rollback.py` | 41 |
| `test_excludes_current_deployment` | Method | `devops_agent/tests/test_rollback.py` | 56 |
| `test_respects_max_age` | Method | `devops_agent/tests/test_rollback.py` | 71 |
| `test_no_eligible_returns_error` | Method | `devops_agent/tests/test_rollback.py` | 89 |
| `test_prefers_success_over_removed` | Method | `devops_agent/tests/test_rollback.py` | 102 |
| `test_handles_malformed_timestamp` | Method | `devops_agent/tests/test_rollback.py` | 115 |
| `test_no_deployments_returns_error` | Method | `devops_agent/tests/test_rollback.py` | 130 |
| `test_single_deployment_returns_error` | Method | `devops_agent/tests/test_rollback.py` | 139 |
| `test_get_deployments_failure_propagates` | Method | `devops_agent/tests/test_rollback.py` | 150 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Rollback_cmd → _load_railway_token` | cross_community | 7 |
| `Rollback_cmd → _load_projects_toml` | cross_community | 7 |
| `Rollback_cmd → Retry` | cross_community | 6 |
| `Report_cmd → _parse_audit_trail` | cross_community | 4 |
| `Rollback_cmd → _parse_deployment_time` | cross_community | 4 |

## Connected Areas

| Area | Connections |
|------|-------------|
| Devops_agent | 1 calls |

## How to Explore

1. `gitnexus_context({name: "find_rollback_target"})` — see callers and callees
2. `gitnexus_query({query: "tests"})` — find related execution flows
3. Read key files listed above for implementation details
