"""JSONL audit trail for DevOps agent operations.

Append-only log for operation tracking. Feeds Phase 4 weekly report.
Each line is a self-contained JSON object. Secret patterns are redacted
before writing.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_AUDIT_DIR = Path(__file__).parent
AUDIT_LOG = _AUDIT_DIR / "audit.jsonl"

# Patterns that look like secrets — redact before writing to disk
_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(sk-or-v1-)\w+"),
    re.compile(r"(sk-ant-)\w+"),
    re.compile(r"(password[\"':\s=]+)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key[\"':\s=]+)\S+", re.IGNORECASE),
    re.compile(r"(token[\"':\s=]+)\S+", re.IGNORECASE),
]


def _redact_secrets(text: str) -> str:
    """Redact known secret patterns from a string."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "***REDACTED***", text)
    return text


def _redact_record(obj: object) -> object:
    """Recursively redact string values in a record."""
    if isinstance(obj, str):
        return _redact_secrets(obj)
    if isinstance(obj, dict):
        return {k: _redact_record(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_record(item) for item in obj]
    return obj


def log_operation(
    *,
    operation: str,
    project: str,
    stages: list[dict],
    final_status: str,
    operation_id: str,
    details: dict | None = None,
) -> None:
    """Append operation record to JSONL audit log.

    Secret patterns (Bearer tokens, API keys, passwords) are redacted
    before writing to disk.

    Args:
        operation: Type of operation (rollback, validate_deploy, health_check, smoke)
        project: Project name
        stages: List of stage results [{stage, status, error_code, duration_ms, details}]
        final_status: Overall outcome (success, failed, partial)
        operation_id: Unique operation identifier for log correlation
        details: Additional context
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "project": project,
        "operation_id": operation_id,
        "final_status": final_status,
        "stages": stages,
    }
    if details:
        record["details"] = details

    # Redact secrets before writing
    record = _redact_record(record)

    try:
        with AUDIT_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        logger.error("Failed to write audit log: %s", e)
