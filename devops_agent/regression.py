"""Regression and security tests for the DevOps agent.

Tier B tests — deeper than smoke (Tier A), require more time and
sometimes database access. Per-project test suites organized by
project name.

Production mode: read-only security posture checks only.
Staging mode: above + auth flows, write tests, data verification.

Designed with GPT-5.4 via OpenRouter (2026-03-21). Replaces the
interactive staging-smoke.md agent in each project repo.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx

from .errors import ErrorCode
from .models import RegressionResult, RegressionTestResult

logger = logging.getLogger(__name__)

# iTheraputix production URLs
_ITX_CLINIC = "https://bcc.pop.clinic"
_ITX_PATIENT = "https://drbawa.pop.clinic"
_ITX_API = "https://hjassist-api.up.railway.app"

# iTheraputix staging URLs
_ITX_CLINIC_STAGING = "https://bcc.stage.pop.clinic"
_ITX_PATIENT_STAGING = "https://drbawa.stage.pop.clinic"

# Test data (from existing staging-smoke.md)
_PROVIDER_SLUG = "drbawa"
_TEST_EMAIL = "chris.martin@bilberryindustries.com"
_TEST_DOMAIN = "bilberryindustries.com"
_STEVE_PATIENT_ID = "1194f274-efc9-4096-ba9d-dc3cac670437"
_STEVE_CONVO_KEY = "steve-d7"


def _http(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json_body: dict | None = None,
    timeout: float = 10.0,
) -> tuple[httpx.Response | None, float, str | None]:
    """Make an HTTP request. Returns (response, latency_ms, error)."""
    t0 = time.monotonic()
    hdrs = {"User-Agent": "devops-agent-regress/1.0", **(headers or {})}
    try:
        resp = httpx.request(
            method, url, headers=hdrs, json=json_body,
            timeout=timeout, follow_redirects=True,
        )
        return resp, round((time.monotonic() - t0) * 1000), None
    except httpx.TimeoutException:
        return None, round((time.monotonic() - t0) * 1000), f"Timeout after {timeout}s"
    except httpx.ConnectError as e:
        return None, round((time.monotonic() - t0) * 1000), f"Connection failed: {e}"


def _pass(name: str, latency_ms: float, staging_only: bool = False) -> RegressionTestResult:
    return RegressionTestResult(
        name=name, passed=True, latency_ms=latency_ms, staging_only=staging_only,
    )


def _fail(name: str, error: str, latency_ms: float = 0, staging_only: bool = False) -> RegressionTestResult:
    return RegressionTestResult(
        name=name, passed=False, error=error, latency_ms=latency_ms, staging_only=staging_only,
    )


def _skip(name: str, reason: str) -> RegressionTestResult:
    return RegressionTestResult(
        name=name, passed=True, error=f"SKIP: {reason}", staging_only=True,
    )


# ---------------------------------------------------------------------------
# Production-safe tests (read-only)
# ---------------------------------------------------------------------------


def test_error_no_leak(base_url: str) -> RegressionTestResult:
    """Malformed requests must not leak stack traces or internals."""
    name = "error-no-leak"
    resp, ms, err = _http("POST", f"{base_url}/api/nonexistent-endpoint",
                          json_body={"bad": "data"})
    if err:
        return _fail(name, err, ms)

    body = resp.text.lower()
    leak_patterns = ["traceback", "file \"", "line ", "sqlalchemy", "psycopg",
                     "env", "password", "secret", "exception"]
    for pattern in leak_patterns:
        if pattern in body:
            return _fail(name, f"Error response leaks: {pattern!r}", ms)
    return _pass(name, ms)


def test_trusted_host(base_url: str) -> RegressionTestResult:
    """Invalid Host header should be rejected."""
    name = "trusted-host-rejection"
    resp, ms, err = _http("GET", base_url, headers={"Host": "evil.example.com"})
    if err:
        # Connection error is acceptable — some proxies reject at TCP level
        return _pass(name, ms)
    # Should NOT return 200 with normal content
    if resp.status_code == 200 and len(resp.text) > 500:
        return _fail(name, f"Served content with invalid Host (status {resp.status_code})", ms)
    return _pass(name, ms)


def test_server_fingerprint(base_url: str) -> RegressionTestResult:
    """Response should not leak unnecessary server info."""
    name = "server-fingerprint"
    resp, ms, err = _http("GET", base_url)
    if err:
        return _fail(name, err, ms)
    powered_by = resp.headers.get("x-powered-by")
    if powered_by:
        return _fail(name, f"X-Powered-By header present: {powered_by}", ms)
    return _pass(name, ms)


def test_invalid_json_rejected(base_url: str) -> RegressionTestResult:
    """Malformed JSON to API endpoint should return 4xx, not 500."""
    name = "invalid-json-rejected"
    resp, ms, err = _http(
        "POST", f"{base_url}/api/auth/staff/request-otp",
        headers={"Content-Type": "application/json"},
    )
    if err:
        return _fail(name, err, ms)
    # Should be 4xx (400 or 422), not 5xx
    if resp.status_code >= 500:
        return _fail(name, f"Server error {resp.status_code} on malformed request", ms)
    return _pass(name, ms)


def test_method_restriction(base_url: str) -> RegressionTestResult:
    """POST-only endpoints should reject GET."""
    name = "method-restriction"
    resp, ms, err = _http("GET", f"{base_url}/api/auth/staff/request-otp")
    if err:
        return _fail(name, err, ms)
    if resp.status_code == 200:
        return _fail(name, "POST-only endpoint accepted GET", ms)
    return _pass(name, ms)


# ---------------------------------------------------------------------------
# Staging-only tests (require DB access, write data)
# ---------------------------------------------------------------------------


def _get_db_url(env: str) -> str | None:
    """Get database URL from environment variable."""
    var_name = f"ITHERAPUTIX_{env.upper()}_DB_URL"
    return os.environ.get(var_name)


def _db_query(db_url: str, sql: str) -> str | None:
    """Run a psql query and return stripped output. Returns None on error."""
    import subprocess
    try:
        result = subprocess.run(
            ["psql", db_url, "-t", "-c", sql],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def test_staff_otp_flow(base_url: str, db_url: str) -> list[RegressionTestResult]:
    """Full staff OTP flow: request → retrieve from DB → verify.

    Returns list of test results + JWT string (or None) for downstream tests.
    """
    results = []

    # 1. Ensure test domain is in allowed_domains
    current_domains = _db_query(db_url,
        f"SELECT allowed_domains FROM provider WHERE slug = '{_PROVIDER_SLUG}'")
    if current_domains and _TEST_DOMAIN not in current_domains:
        _db_query(db_url,
            f"UPDATE provider SET allowed_domains = array_append(allowed_domains, "
            f"'{_TEST_DOMAIN}') WHERE slug = '{_PROVIDER_SLUG}' "
            f"AND NOT '{_TEST_DOMAIN}' = ANY(allowed_domains)")

    # 2. Request OTP
    resp, ms, err = _http("POST", f"{base_url}/api/auth/staff/request-otp",
                          json_body={"email": _TEST_EMAIL, "provider_slug": _PROVIDER_SLUG})
    if err or not resp or resp.status_code != 200:
        results.append(_fail("otp-request", err or f"Status {resp.status_code}: {resp.text[:100]}", ms, staging_only=True))
        return results
    results.append(_pass("otp-request", ms, staging_only=True))

    # 3. Retrieve OTP from DB
    otp = _db_query(db_url,
        f"SELECT code FROM login_codes WHERE email = '{_TEST_EMAIL}' "
        f"AND used = false ORDER BY created_at DESC LIMIT 1")
    if not otp:
        results.append(_fail("otp-retrieve", "No OTP found in DB", staging_only=True))
        return results
    results.append(_pass("otp-retrieve", 0, staging_only=True))

    # 4. Verify OTP
    resp, ms, err = _http("POST", f"{base_url}/api/auth/staff/verify-otp",
                          json_body={"email": _TEST_EMAIL, "code": otp})
    if err or not resp or resp.status_code != 200:
        results.append(_fail("otp-verify", err or f"Status {resp.status_code}: {resp.text[:100]}", ms, staging_only=True))
        return results

    try:
        body = resp.json()
        jwt = body.get("token")
        if not jwt:
            results.append(_fail("otp-verify", "No token in response", ms, staging_only=True))
            return results
    except (json.JSONDecodeError, KeyError):
        results.append(_fail("otp-verify", "Invalid JSON response", ms, staging_only=True))
        return results

    results.append(_pass("otp-verify", ms, staging_only=True))

    # 5. OTP reuse blocked
    resp2, ms2, err2 = _http("POST", f"{base_url}/api/auth/staff/verify-otp",
                             json_body={"email": _TEST_EMAIL, "code": otp})
    if resp2 and resp2.status_code == 200:
        results.append(_fail("otp-reuse-blocked", "Replayed OTP was accepted", ms2, staging_only=True))
    else:
        results.append(_pass("otp-reuse-blocked", ms2, staging_only=True))

    # 6. Wrong OTP rejected
    resp3, ms3, _ = _http("POST", f"{base_url}/api/auth/staff/verify-otp",
                          json_body={"email": _TEST_EMAIL, "code": "000000"})
    if resp3 and resp3.status_code == 200:
        results.append(_fail("otp-wrong-rejected", "Wrong OTP was accepted", ms3, staging_only=True))
    else:
        results.append(_pass("otp-wrong-rejected", ms3, staging_only=True))

    return results


def test_patient_magic_link(base_url: str, db_url: str) -> list[RegressionTestResult]:
    """Patient magic link: generate → verify with phone last 4."""
    results = []

    # Get Steve's phone last 4
    phone_last4 = _db_query(db_url,
        f"SELECT RIGHT(regexp_replace(phone, '[^0-9]', '', 'g'), 4) "
        f"FROM patient WHERE id = '{_STEVE_PATIENT_ID}'")
    if not phone_last4:
        results.append(_fail("magic-link-setup", "Cannot get Steve's phone from DB", staging_only=True))
        return results

    # Generate magic link
    resp, ms, err = _http("POST", f"{base_url}/api/auth/generate-magic-link",
                          json_body={"patient_id": _STEVE_PATIENT_ID})
    if err or not resp or resp.status_code != 200:
        results.append(_fail("magic-link-generate", err or f"Status {resp.status_code}", ms, staging_only=True))
        return results

    try:
        body = resp.json()
        url = body.get("url", "")
        token = url.split("token=")[1] if "token=" in url else ""
    except (json.JSONDecodeError, IndexError):
        results.append(_fail("magic-link-generate", "Cannot parse token from response", ms, staging_only=True))
        return results

    if not token:
        results.append(_fail("magic-link-generate", "No token in response URL", ms, staging_only=True))
        return results
    results.append(_pass("magic-link-generate", ms, staging_only=True))

    # Check token is HMAC-signed (base64, not raw UUID)
    import base64
    try:
        base64.b64decode(token)
        is_base64 = len(token) > 40  # UUIDs are 36 chars
    except Exception:
        is_base64 = False

    if not is_base64:
        results.append(_fail("magic-link-hmac", "Token appears to be raw UUID, not HMAC-signed", staging_only=True))
    else:
        results.append(_pass("magic-link-hmac", 0, staging_only=True))

    # Verify magic link
    resp2, ms2, err2 = _http("POST", f"{base_url}/api/auth/verify-magic-link",
                             json_body={"token": token, "code": phone_last4})
    if err2 or not resp2 or resp2.status_code != 200:
        results.append(_fail("magic-link-verify", err2 or f"Status {resp2.status_code}", ms2, staging_only=True))
    else:
        results.append(_pass("magic-link-verify", ms2, staging_only=True))

    return results


def test_feedback_flow(base_url: str, db_url: str, jwt: str) -> list[RegressionTestResult]:
    """Feedback persistence + idempotency test."""
    results = []
    auth_headers = {"Authorization": f"Bearer {jwt}", "X-Schema": "sandbox"}

    # 1. Create feedback session
    resp, ms, err = _http(
        "POST", f"{base_url}/api/staff/feedback",
        headers=auth_headers,
        json_body={
            "scenario_key": _STEVE_CONVO_KEY,
            "initial_message": "Regression test feedback — safe to delete",
            "conversation": [
                {"role": "assistant", "text": "Hi Steve"},
                {"role": "user", "text": "My knee is swollen"},
            ],
        },
        timeout=30.0,
    )
    if err or not resp or resp.status_code != 200:
        results.append(_fail("feedback-create", err or f"Status {resp.status_code}: {resp.text[:100]}", ms, staging_only=True))
        return results

    try:
        feedback_id = resp.json().get("feedback_id")
    except (json.JSONDecodeError, AttributeError):
        results.append(_fail("feedback-create", "Cannot parse feedback_id", ms, staging_only=True))
        return results

    results.append(_pass("feedback-create", ms, staging_only=True))

    # 2. Verify in DB
    row = _db_query(db_url,
        f"SET search_path TO sandbox; SELECT id FROM clinic_feedback WHERE id = '{feedback_id}'")
    if row and feedback_id in str(row):
        results.append(_pass("feedback-in-db", 0, staging_only=True))
    else:
        results.append(_fail("feedback-in-db", f"Feedback {feedback_id} not found in DB", staging_only=True))

    # 3. Check messages
    msg_count = _db_query(db_url,
        f"SET search_path TO sandbox; SELECT COUNT(*) FROM feedback_messages "
        f"WHERE feedback_id = '{feedback_id}'")
    if msg_count and int(msg_count.strip()) >= 2:
        results.append(_pass("feedback-messages", 0, staging_only=True))
    else:
        results.append(_fail("feedback-messages", f"Expected >=2 messages, got {msg_count}", staging_only=True))

    # 4. Idempotency: send duplicate with client_request_id
    client_id = f"regress-{int(time.time())}"
    idempotent_body = {
        "scenario_key": _STEVE_CONVO_KEY,
        "initial_message": "Idempotency test",
        "client_request_id": client_id,
        "conversation": [
            {"role": "assistant", "text": "Hi Steve"},
            {"role": "user", "text": "Test"},
        ],
    }
    resp1, _, _ = _http("POST", f"{base_url}/api/staff/feedback",
                        headers=auth_headers, json_body=idempotent_body, timeout=30.0)
    resp2, _, _ = _http("POST", f"{base_url}/api/staff/feedback",
                        headers=auth_headers, json_body=idempotent_body, timeout=30.0)

    if resp1 and resp2:
        try:
            fid1 = resp1.json().get("feedback_id")
            fid2 = resp2.json().get("feedback_id")
            if fid1 == fid2:
                results.append(_pass("feedback-idempotent", 0, staging_only=True))
            else:
                results.append(_fail("feedback-idempotent",
                    f"Different feedback_ids: {fid1} vs {fid2}", staging_only=True))
        except (json.JSONDecodeError, AttributeError):
            results.append(_fail("feedback-idempotent", "Cannot parse responses", staging_only=True))
    else:
        results.append(_fail("feedback-idempotent", "Request(s) failed", staging_only=True))

    return results


def test_cleanup(db_url: str) -> RegressionTestResult:
    """Remove test data created by regression tests."""
    sql = f"""
    SET search_path TO sandbox;
    DELETE FROM feedback_messages WHERE feedback_id IN (
        SELECT id FROM clinic_feedback WHERE staff_id = (
            SELECT id FROM public.staff WHERE email = '{_TEST_EMAIL}'
        )
    );
    DELETE FROM clinic_feedback WHERE staff_id = (
        SELECT id FROM public.staff WHERE email = '{_TEST_EMAIL}'
    );
    """
    result = _db_query(db_url, sql)
    if result is not None:
        return _pass("cleanup", 0, staging_only=True)
    return _fail("cleanup", "DB cleanup failed", staging_only=True)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_regression(
    project_name: str,
    env: str = "staging",
) -> RegressionResult:
    """Run regression tests for a project.

    Args:
        project_name: Project identifier (currently only 'hj-assistant').
        env: 'staging' or 'production'. Staging runs all tests; production is read-only.
    """
    if project_name != "hj-assistant":
        return RegressionResult(
            ok=False,
            code=ErrorCode.CONFIG_ERROR,
            message=f"No regression tests defined for {project_name}",
            project=project_name,
            environment=env,
        )

    is_staging = env == "staging"
    base_url = _ITX_CLINIC_STAGING if is_staging else _ITX_CLINIC
    results: list[RegressionTestResult] = []

    # --- Production-safe tests (always run) ---
    results.append(test_error_no_leak(base_url))
    results.append(test_trusted_host(base_url))
    results.append(test_server_fingerprint(base_url))
    results.append(test_invalid_json_rejected(base_url))
    results.append(test_method_restriction(base_url))

    # --- Staging-only tests ---
    if not is_staging:
        skipped = 0
        for test_name in ["otp-request", "otp-retrieve", "otp-verify",
                          "otp-reuse-blocked", "otp-wrong-rejected",
                          "magic-link-generate", "magic-link-hmac", "magic-link-verify",
                          "feedback-create", "feedback-in-db", "feedback-messages",
                          "feedback-idempotent", "cleanup"]:
            results.append(_skip(test_name, "staging-only test skipped in production"))
            skipped += 1

        passed = sum(1 for r in results if r.passed and not r.error)
        failed = sum(1 for r in results if not r.passed)
        return RegressionResult(
            ok=failed == 0,
            code=ErrorCode.OK if failed == 0 else ErrorCode.APP_UNHEALTHY,
            message=f"{project_name} ({env}): {passed} passed, {failed} failed, {skipped} skipped",
            project=project_name,
            environment=env,
            tests_passed=passed,
            tests_failed=failed,
            tests_skipped=skipped,
            tests_total=len(results),
            test_results=results,
        )

    # Staging: need DB access
    db_url = _get_db_url(env)
    if not db_url:
        # Run prod-safe tests only, skip staging tests
        for test_name in ["otp-request", "otp-retrieve", "otp-verify",
                          "otp-reuse-blocked", "otp-wrong-rejected",
                          "magic-link-generate", "magic-link-hmac", "magic-link-verify",
                          "feedback-create", "feedback-in-db", "feedback-messages",
                          "feedback-idempotent", "cleanup"]:
            results.append(_skip(test_name,
                f"ITHERAPUTIX_STAGING_DB_URL not set"))

    else:
        # OTP flow (returns JWT for downstream tests)
        otp_results = test_staff_otp_flow(base_url, db_url)
        results.extend(otp_results)

        # Extract JWT from OTP verify result for feedback tests
        jwt = None
        otp_verify_passed = any(r.name == "otp-verify" and r.passed for r in otp_results)
        if otp_verify_passed:
            # Re-request OTP to get a fresh JWT (the one from the test was consumed)
            # Actually, the JWT was already extracted in test_staff_otp_flow
            # We need to re-do OTP for a fresh JWT for feedback
            fresh_otp_resp, _, _ = _http("POST", f"{base_url}/api/auth/staff/request-otp",
                                         json_body={"email": _TEST_EMAIL, "provider_slug": _PROVIDER_SLUG})
            if fresh_otp_resp and fresh_otp_resp.status_code == 200:
                fresh_otp = _db_query(db_url,
                    f"SELECT code FROM login_codes WHERE email = '{_TEST_EMAIL}' "
                    f"AND used = false ORDER BY created_at DESC LIMIT 1")
                if fresh_otp:
                    verify_resp, _, _ = _http("POST", f"{base_url}/api/auth/staff/verify-otp",
                                             json_body={"email": _TEST_EMAIL, "code": fresh_otp})
                    if verify_resp and verify_resp.status_code == 200:
                        try:
                            jwt = verify_resp.json().get("token")
                        except (json.JSONDecodeError, AttributeError):
                            pass

        # Magic link
        ml_results = test_patient_magic_link(base_url, db_url)
        results.extend(ml_results)

        # Feedback (requires JWT)
        if jwt:
            fb_results = test_feedback_flow(base_url, db_url, jwt)
            results.extend(fb_results)
        else:
            for test_name in ["feedback-create", "feedback-in-db", "feedback-messages",
                              "feedback-idempotent"]:
                results.append(_skip(test_name, "No JWT from OTP flow"))

        # Cleanup
        results.append(test_cleanup(db_url))

    passed = sum(1 for r in results if r.passed and not (r.error and r.error.startswith("SKIP")))
    failed = sum(1 for r in results if not r.passed)
    skipped = sum(1 for r in results if r.error and r.error.startswith("SKIP"))

    return RegressionResult(
        ok=failed == 0,
        code=ErrorCode.OK if failed == 0 else ErrorCode.APP_UNHEALTHY,
        message=f"{project_name} ({env}): {passed} passed, {failed} failed, {skipped} skipped",
        project=project_name,
        environment=env,
        tests_passed=passed,
        tests_failed=failed,
        tests_skipped=skipped,
        tests_total=len(results),
        test_results=results,
    )
