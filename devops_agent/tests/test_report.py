"""Unit tests for report.py audit trail parsing and data collection."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from devops_agent.models import ReportWindow
from devops_agent.report import _parse_audit_trail


def _window(days: int = 7) -> ReportWindow:
    """Create a reporting window ending now."""
    now = datetime.now(timezone.utc)
    return ReportWindow(start=now - timedelta(days=days), end=now, days=days)


def _write_audit_lines(lines: list[str], tmp: Path) -> None:
    """Write lines to a temporary audit file."""
    tmp.write_text("\n".join(lines) + "\n")


class TestParseAuditTrail:
    """Tests for _parse_audit_trail with various JSONL inputs."""

    def test_empty_file(self, tmp_path: Path):
        """Empty audit trail returns zero counts."""
        audit_file = tmp_path / "audit.jsonl"
        audit_file.write_text("")
        with patch("devops_agent.report.AUDIT_LOG", audit_file):
            summary, events = _parse_audit_trail(_window())
        assert summary.total_operations == 0
        assert events == []

    def test_missing_file(self, tmp_path: Path):
        """Missing audit file returns zero counts without error."""
        audit_file = tmp_path / "nonexistent.jsonl"
        with patch("devops_agent.report.AUDIT_LOG", audit_file):
            summary, events = _parse_audit_trail(_window())
        assert summary.total_operations == 0

    def test_malformed_lines_skipped(self, tmp_path: Path):
        """Malformed JSON lines are counted but don't crash parsing."""
        now = datetime.now(timezone.utc)
        good_record = json.dumps({
            "timestamp": now.isoformat(),
            "operation": "validate_deploy",
            "final_status": "success",
        })
        lines = [
            "not json at all",
            good_record,
            '{"broken: json',
            "",  # blank line
        ]
        audit_file = tmp_path / "audit.jsonl"
        _write_audit_lines(lines, audit_file)

        with patch("devops_agent.report.AUDIT_LOG", audit_file):
            summary, events = _parse_audit_trail(_window())

        assert summary.total_operations == 1
        assert summary.malformed_lines == 2  # "not json" + broken json
        assert summary.validate_deploy_count == 1

    def test_filters_by_date_window(self, tmp_path: Path):
        """Only events within the reporting window are counted."""
        now = datetime.now(timezone.utc)
        in_window = json.dumps({
            "timestamp": (now - timedelta(days=1)).isoformat(),
            "operation": "rollback",
            "final_status": "success",
        })
        out_of_window = json.dumps({
            "timestamp": (now - timedelta(days=30)).isoformat(),
            "operation": "rollback",
            "final_status": "success",
        })
        audit_file = tmp_path / "audit.jsonl"
        _write_audit_lines([in_window, out_of_window], audit_file)

        with patch("devops_agent.report.AUDIT_LOG", audit_file):
            summary, events = _parse_audit_trail(_window(days=7))

        assert summary.total_operations == 1
        assert summary.rollback_count == 1
        assert summary.rollback_succeeded == 1

    def test_counts_operation_types(self, tmp_path: Path):
        """Each operation type increments the correct counter."""
        now = datetime.now(timezone.utc)
        ts = now.isoformat()
        records = [
            {"timestamp": ts, "operation": "validate_deploy", "final_status": "success"},
            {"timestamp": ts, "operation": "validate_deploy", "final_status": "failed"},
            {"timestamp": ts, "operation": "rollback", "final_status": "success"},
            {"timestamp": ts, "operation": "rollback", "final_status": "failed"},
            {"timestamp": ts, "operation": "smoke", "final_status": "success"},
            {"timestamp": ts, "operation": "smoke", "final_status": "failed"},
            {"timestamp": ts, "operation": "health_check", "final_status": "success"},
            {"timestamp": ts, "operation": "health_check", "final_status": "failed"},
            {"timestamp": ts, "operation": "discover", "final_status": "success"},
        ]
        audit_file = tmp_path / "audit.jsonl"
        _write_audit_lines([json.dumps(r) for r in records], audit_file)

        with patch("devops_agent.report.AUDIT_LOG", audit_file):
            summary, events = _parse_audit_trail(_window())

        assert summary.total_operations == 9
        assert summary.validate_deploy_count == 2
        assert summary.rollback_count == 2
        assert summary.rollback_succeeded == 1
        assert summary.rollback_failed == 1
        assert summary.smoke_runs == 2
        assert summary.smoke_failures == 1
        assert summary.health_checks == 2
        assert summary.health_failures == 1
        assert summary.other_operations == 1  # "discover" is not in _METRIC_OPS

    def test_missing_timestamp_counted_as_malformed(self, tmp_path: Path):
        """Records without timestamp field are counted as malformed."""
        record = json.dumps({"operation": "rollback", "final_status": "success"})
        audit_file = tmp_path / "audit.jsonl"
        _write_audit_lines([record], audit_file)

        with patch("devops_agent.report.AUDIT_LOG", audit_file):
            summary, events = _parse_audit_trail(_window())

        assert summary.total_operations == 0
        assert summary.malformed_lines == 1

    def test_naive_timestamp_treated_as_utc(self, tmp_path: Path):
        """Timestamps without timezone info are treated as UTC."""
        now = datetime.now(timezone.utc)
        # Write a naive timestamp (no +00:00 suffix)
        naive_ts = now.strftime("%Y-%m-%dT%H:%M:%S")
        record = json.dumps({
            "timestamp": naive_ts,
            "operation": "health_check",
            "final_status": "success",
        })
        audit_file = tmp_path / "audit.jsonl"
        _write_audit_lines([record], audit_file)

        with patch("devops_agent.report.AUDIT_LOG", audit_file):
            summary, events = _parse_audit_trail(_window())

        assert summary.total_operations == 1
        assert summary.health_checks == 1
