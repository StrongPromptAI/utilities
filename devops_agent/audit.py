"""JSONL audit trail for DevOps agent operations.

Append-only log for operation tracking. Feeds Phase 4 weekly report.
Each line is a self-contained JSON object.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_AUDIT_DIR = Path(__file__).parent
AUDIT_LOG = _AUDIT_DIR / "audit.jsonl"


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

    Args:
        operation: Type of operation (rollback, validate_deploy, health_check)
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

    try:
        with AUDIT_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        logger.error("Failed to write audit log: %s", e)
