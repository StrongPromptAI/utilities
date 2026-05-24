---
name: devops-agent
description: "Skill for the Devops_agent area of utilities. 94 symbols across 17 files."
---

# Devops_agent

94 symbols | 17 files | Cohesion: 87%

## When to Use

- Working with code in `devops_agent/`
- Understanding how run_smoke_tests, execute_rollback, verify_rollback work
- Modifying devops_agent-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `devops_agent/regression.py` | _http, _pass, _fail, _skip, test_error_no_leak (+11) |
| `devops_agent/cli.py` | _output, _exit_code, list_projects_cmd, status_cmd, health_cmd (+9) |
| `devops_agent/models.py` | to_display, OperationResult, HealthResult, RailwayResult, NotifyResult (+5) |
| `devops_agent/errors.py` | DevOpsError, ConfigError, RailwayAPIError, HealthCheckError, NotifyError (+5) |
| `devops_agent/report.py` | collect_report_data, run_report, _get_llm_insights, _health_icon, build_report_views (+2) |
| `devops_agent/rollback.py` | _operation_id, execute_rollback, verify_rollback, rollback_with_notification, _send_rollback_failed_notification (+1) |
| `devops_agent/railway.py` | get_deployments, _gql, discover_projects, execute_rollback_mutation, get_deployment_status |
| `devops_agent/templates.py` | _now_iso, _health_summary, rollback_email, deploy_success_email, rollback_failed_email |
| `devops_agent/config.py` | _load_railway_token, _load_projects_toml, get_config, get_project |
| `devops_agent/analyze.py` | _get_api_key, call_openrouter, draft_rollback_email_llm, draft_deploy_email_llm |

## Entry Points

Start here when exploring this area:

- **`run_smoke_tests`** (Function) — `devops_agent/smoke.py:147`
- **`execute_rollback`** (Function) — `devops_agent/rollback.py:143`
- **`verify_rollback`** (Function) — `devops_agent/rollback.py:180`
- **`rollback_with_notification`** (Function) — `devops_agent/rollback.py:186`
- **`validate_deploy`** (Function) — `devops_agent/rollback.py:379`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `OperationResult` | Class | `devops_agent/models.py` | 19 |
| `HealthResult` | Class | `devops_agent/models.py` | 34 |
| `RailwayResult` | Class | `devops_agent/models.py` | 42 |
| `NotifyResult` | Class | `devops_agent/models.py` | 50 |
| `AnalyzeResult` | Class | `devops_agent/models.py` | 57 |
| `ValidationResult` | Class | `devops_agent/models.py` | 65 |
| `SmokeResult` | Class | `devops_agent/models.py` | 108 |
| `RegressionResult` | Class | `devops_agent/models.py` | 130 |
| `ReportResult` | Class | `devops_agent/models.py` | 205 |
| `DevOpsError` | Class | `devops_agent/errors.py` | 31 |
| `ConfigError` | Class | `devops_agent/errors.py` | 39 |
| `RailwayAPIError` | Class | `devops_agent/errors.py` | 46 |
| `HealthCheckError` | Class | `devops_agent/errors.py` | 53 |
| `NotifyError` | Class | `devops_agent/errors.py` | 60 |
| `run_smoke_tests` | Function | `devops_agent/smoke.py` | 147 |
| `execute_rollback` | Function | `devops_agent/rollback.py` | 143 |
| `verify_rollback` | Function | `devops_agent/rollback.py` | 180 |
| `rollback_with_notification` | Function | `devops_agent/rollback.py` | 186 |
| `validate_deploy` | Function | `devops_agent/rollback.py` | 379 |
| `collect_report_data` | Function | `devops_agent/report.py` | 119 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `Rollback_cmd → _load_railway_token` | cross_community | 7 |
| `Rollback_cmd → _load_projects_toml` | cross_community | 7 |
| `Report_cmd → Retry` | cross_community | 6 |
| `Rollback_cmd → Retry` | cross_community | 6 |
| `Run_report → _load_railway_token` | intra_community | 6 |
| `Validate_deploy_cmd → _load_railway_token` | cross_community | 6 |
| `Validate_deploy_cmd → _load_projects_toml` | cross_community | 6 |
| `Report_cmd → _load_railway_token` | cross_community | 5 |
| `Report_cmd → _load_projects_toml` | cross_community | 5 |
| `Report_cmd → _health_icon` | cross_community | 5 |

## Connected Areas

| Area | Connections |
|------|-------------|
| Tests | 2 calls |

## How to Explore

1. `gitnexus_context({name: "run_smoke_tests"})` — see callers and callees
2. `gitnexus_query({query: "devops_agent"})` — find related execution flows
3. Read key files listed above for implementation details
