"""Shared result models for the DevOps agent.

Every operation returns a typed result object with a consistent interface:
ok, code, message, details, timestamp. This enables machine-readable
outcomes for rollback policy, weekly reporting, and automation.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .errors import ErrorCode


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OperationResult(BaseModel):
    """Base result — every operation returns one of these."""

    ok: bool
    code: ErrorCode
    message: str
    details: dict[str, Any] = {}
    timestamp: datetime = Field(default_factory=_now)

    def to_display(self) -> str:
        """Human-readable one-liner."""
        status = "OK" if self.ok else "FAIL"
        return f"[{status}] {self.code.value}: {self.message}"


class HealthResult(OperationResult):
    """Result from a health check."""

    url: str = ""
    status_code: int | None = None
    latency_ms: float | None = None


class RailwayResult(OperationResult):
    """Result from a Railway GraphQL operation."""

    project: str = ""
    service: str = ""
    environment: str = ""


class NotifyResult(OperationResult):
    """Result from sending an email notification."""

    message_id: str = ""
    recipient: str = ""


class AnalyzeResult(OperationResult):
    """Result from an OpenRouter LLM call."""

    model: str = ""
    response_text: str = ""
    cost: float | None = None


class ValidationResult(OperationResult):
    """Result from validate_deploy with stage tracking."""

    stages: list[dict[str, Any]] = []
    rollback_triggered: bool = False
    rollback_succeeded: bool | None = None
    notification_sent: bool = False


class SmokeTest(BaseModel):
    """Single smoke test definition from projects.toml.

    Covers both functional health checks and security posture checks
    in a single per-project test list.
    """

    name: str
    url: str
    method: str = "GET"
    expected_status: int = 200
    expected_body_contains: str | None = None
    json_path: str | None = None
    json_value: str | None = None
    expected_header: str | None = None
    expected_header_contains: str | None = None
    headers: dict[str, str] = {}
    timeout: float = 10.0


class SmokeTestResult(BaseModel):
    """Result of a single smoke test execution."""

    name: str
    url: str
    passed: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None


class SmokeResult(OperationResult):
    """Result from running all smoke tests for a project."""

    tests_passed: int = 0
    tests_failed: int = 0
    tests_total: int = 0
    test_results: list[SmokeTestResult] = []
