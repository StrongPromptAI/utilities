"""Structured JSON logging for the DevOps agent.

Logs go to stderr. CLI stdout is reserved for results.
"""

import json
import logging
import sys
import uuid
from datetime import datetime, timezone

# Unique run ID for correlating log lines within a single invocation
RUN_ID = uuid.uuid4().hex[:12]


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "run_id": RUN_ID,
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(*, verbose: bool = False) -> None:
    """Configure structured JSON logging to stderr."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger("devops_agent")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False


def redact(value: str, *, visible: int = 4) -> str:
    """Redact a secret, showing only the last N characters."""
    if len(value) <= visible:
        return "***"
    return f"***{value[-visible:]}"
