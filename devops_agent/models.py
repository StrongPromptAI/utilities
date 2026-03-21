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
