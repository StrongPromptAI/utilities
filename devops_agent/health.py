"""HTTP health check runner.

Pure HTTP — no LLM involved. Hits a URL, checks status code, measures latency.
"""

import logging
import time

import httpx

from .config import get_project
from .errors import ErrorCode
from .models import HealthResult
from .retry import retry

logger = logging.getLogger(__name__)


def check_health(project_name: str, *, env: str = "production") -> HealthResult:
    """Run a health check against a project's configured health URL.

    Args:
        project_name: Key from projects.toml.
        env: 'production' or 'staging'. Uses staging_health_url if available.

    Returns:
        HealthResult with pass/fail, status code, latency.
    """
    t0 = time.monotonic()
    try:
        proj = get_project(project_name)
    except Exception as e:
        return HealthResult(ok=False, code=ErrorCode.CONFIG_ERROR, message=str(e))

    url = proj.health_url
    if not url:
        return HealthResult(
            ok=False,
            code=ErrorCode.CONFIG_ERROR,
            message=f"No health_url configured for {project_name}",
        )

    timeout = proj.health_timeout
    expected_status = proj.health_expected_status
    headers = {**proj.health_headers, "User-Agent": "devops-agent/1.0"}

    def _do_request() -> httpx.Response:
        return httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)

    try:
        resp = retry(
            _do_request,
            max_retries=2,
            base_delay=1.0,
            retry_on=(httpx.TimeoutException, httpx.ConnectError),
            operation=f"health_check:{project_name}",
        )
    except httpx.TimeoutException:
        latency_ms = (time.monotonic() - t0) * 1000
        return HealthResult(
            ok=False,
            code=ErrorCode.TIMEOUT,
            message=f"Health check timed out after {timeout}s",
            url=url,
            latency_ms=latency_ms,
        )
    except httpx.ConnectError as e:
        latency_ms = (time.monotonic() - t0) * 1000
        return HealthResult(
            ok=False,
            code=ErrorCode.NETWORK_ERROR,
            message=f"Cannot connect to {url}: {e}",
            url=url,
            latency_ms=latency_ms,
        )

    latency_ms = (time.monotonic() - t0) * 1000
    passed = resp.status_code == expected_status

    # Grab response snippet for diagnostics
    body_snippet = resp.text[:200] if resp.text else ""

    result = HealthResult(
        ok=passed,
        code=ErrorCode.OK if passed else ErrorCode.APP_UNHEALTHY,
        message=(
            f"{project_name}: {resp.status_code} in {latency_ms:.0f}ms"
            if passed
            else f"{project_name}: expected {expected_status}, got {resp.status_code}"
        ),
        url=url,
        status_code=resp.status_code,
        latency_ms=latency_ms,
        details={"body_snippet": body_snippet} if not passed else {},
    )

    logger.info(
        "health_check project=%s ok=%s status=%s latency_ms=%.0f",
        project_name,
        result.ok,
        resp.status_code,
        latency_ms,
    )
    return result
