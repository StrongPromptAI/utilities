"""Smoke test orchestrator for the DevOps agent.

Runs fast HTTP-based smoke tests against project endpoints. Designed for
automated pipelines (cron, validate-deploy) — not a replacement for the
deep interactive smoke agents in each project's .claude/agents/.

Supports:
- Status code assertions
- Body substring matching
- JSON path assertions (e.g., json_path="status", json_value="ok")
- Per-test timeout and custom headers

Observe mode (default): failures alert but do NOT trigger rollback.
Enforce mode: failures are treated as deploy validation failures.
"""

import logging
import time
from functools import reduce

import httpx

from .config import get_project
from .errors import ErrorCode
from .models import OperationResult, SmokeResult, SmokeTest, SmokeTestResult

logger = logging.getLogger(__name__)


def _resolve_json_path(data: dict, path: str) -> object:
    """Resolve a dot-separated JSON path like 'status' or 'data.count'."""
    try:
        return reduce(lambda d, key: d[key], path.split("."), data)
    except (KeyError, TypeError, IndexError):
        return _MISSING


_MISSING = object()


def _run_single_test(test: SmokeTest) -> SmokeTestResult:
    """Execute a single smoke test and return structured result."""
    t0 = time.monotonic()

    try:
        resp = httpx.request(
            test.method,
            test.url,
            headers={
                "User-Agent": "devops-agent-smoke/1.0",
                **test.headers,
            },
            content=test.body.encode() if test.body else None,
            timeout=test.timeout,
            follow_redirects=True,
        )
    except httpx.TimeoutException:
        return SmokeTestResult(
            name=test.name,
            url=test.url,
            passed=False,
            latency_ms=round((time.monotonic() - t0) * 1000),
            error=f"Timeout after {test.timeout}s",
        )
    except httpx.ConnectError as e:
        return SmokeTestResult(
            name=test.name,
            url=test.url,
            passed=False,
            latency_ms=round((time.monotonic() - t0) * 1000),
            error=f"Connection failed: {e}",
        )

    latency_ms = round((time.monotonic() - t0) * 1000)
    errors: list[str] = []

    # Check status code
    if resp.status_code != test.expected_status:
        errors.append(
            f"Status {resp.status_code} != expected {test.expected_status}"
        )

    # Check body contains
    if test.expected_body_contains:
        if test.expected_body_contains not in resp.text:
            errors.append(
                f"Body missing expected string: {test.expected_body_contains!r}"
            )

    # Check response header
    if test.expected_header:
        header_val = resp.headers.get(test.expected_header)
        if header_val is None:
            errors.append(f"Missing header: {test.expected_header}")
        elif test.expected_header_contains and test.expected_header_contains not in header_val:
            errors.append(
                f"Header {test.expected_header}: {header_val!r} missing {test.expected_header_contains!r}"
            )

    # Check reject_header (negative assertion — header must NOT contain value)
    if test.reject_header:
        header_val = resp.headers.get(test.reject_header)
        if header_val is not None and test.reject_header_contains:
            if test.reject_header_contains in header_val:
                errors.append(
                    f"Header {test.reject_header} must NOT contain "
                    f"{test.reject_header_contains!r}, but got: {header_val!r}"
                )

    # Check JSON path
    if test.json_path is not None:
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            errors.append(
                f"Expected JSON response for json_path check, got {content_type}"
            )
        else:
            try:
                data = resp.json()
            except ValueError:
                errors.append("Response is not valid JSON")
                data = None

            if data is not None:
                actual = _resolve_json_path(data, test.json_path)
                if actual is _MISSING:
                    errors.append(f"JSON path {test.json_path!r} not found")
                elif test.json_value is not None and str(actual) != test.json_value:
                    errors.append(
                        f"JSON path {test.json_path!r}: {actual!r} != expected {test.json_value!r}"
                    )

    passed = len(errors) == 0
    return SmokeTestResult(
        name=test.name,
        url=test.url,
        passed=passed,
        status_code=resp.status_code,
        latency_ms=latency_ms,
        error="; ".join(errors) if errors else None,
    )


def run_smoke_tests(project_name: str) -> SmokeResult:
    """Run all configured smoke tests for a project.

    Loads test definitions from projects.toml. Returns structured result
    with per-test pass/fail. Does not trigger rollback — that decision
    belongs to the caller (validate_deploy) based on smoke_mode.
    """
    try:
        proj = get_project(project_name)
    except Exception as e:
        return SmokeResult(
            ok=False,
            code=ErrorCode.CONFIG_ERROR,
            message=str(e),
        )

    tests = proj.smoke_tests
    if not tests:
        return SmokeResult(
            ok=True,
            code=ErrorCode.OK,
            message=f"No smoke tests configured for {project_name}",
            tests_total=0,
        )

    test_results: list[SmokeTestResult] = []
    for test_def in tests:
        test = SmokeTest(**test_def) if isinstance(test_def, dict) else test_def
        logger.info("smoke test=%s url=%s", test.name, test.url)
        result = _run_single_test(test)
        test_results.append(result)
        status = "PASS" if result.passed else f"FAIL: {result.error}"
        logger.info(
            "smoke test=%s status=%s latency_ms=%s",
            test.name,
            status,
            result.latency_ms,
        )

    passed = sum(1 for r in test_results if r.passed)
    failed = sum(1 for r in test_results if not r.passed)
    total = len(test_results)
    all_passed = failed == 0

    return SmokeResult(
        ok=all_passed,
        code=ErrorCode.OK if all_passed else ErrorCode.APP_UNHEALTHY,
        message=f"{project_name}: {passed}/{total} smoke tests passed"
        + ("" if all_passed else f" ({failed} failed)"),
        tests_passed=passed,
        tests_failed=failed,
        tests_total=total,
        test_results=test_results,
    )
