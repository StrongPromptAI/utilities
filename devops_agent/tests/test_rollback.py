"""Unit tests for rollback candidate selection.

Tests use fixture data matching Railway's actual deployment lifecycle:
- Exactly 1 SUCCESS deployment (the active one)
- All prior deployments are REMOVED
- deploymentRollback works on REMOVED IDs (verified live 2026-03-21)
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from devops_agent.errors import ErrorCode
from devops_agent.models import RailwayResult
from devops_agent.rollback import find_rollback_target, ROLLBACK_ELIGIBLE


def _make_deployment(id: str, status: str, days_ago: int) -> dict:
    """Create a deployment fixture."""
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": id,
        "status": status,
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }


def _mock_deployments(deployments: list[dict]):
    """Return a mock for get_deployments that returns the given list."""
    return RailwayResult(
        ok=True,
        code=ErrorCode.OK,
        message=f"{len(deployments)} deployments found",
        project="test-project",
        details={"deployments": deployments},
    )


class TestFindRollbackTarget:
    """Tests for find_rollback_target()."""

    @patch("devops_agent.rollback.get_deployments")
    def test_finds_removed_target(self, mock_get):
        """Most recent REMOVED deployment is selected when no other SUCCESS exists."""
        mock_get.return_value = _mock_deployments([
            _make_deployment("current-1", "SUCCESS", 0),
            _make_deployment("prev-1", "REMOVED", 1),
            _make_deployment("prev-2", "REMOVED", 3),
        ])

        result = find_rollback_target("test-project")

        assert result.ok
        assert result.details["target_deployment_id"] == "prev-1"
        assert result.details["target_status"] == "REMOVED"

    @patch("devops_agent.rollback.get_deployments")
    def test_excludes_current_deployment(self, mock_get):
        """Current deployment ID is never selected as rollback target."""
        mock_get.return_value = _mock_deployments([
            _make_deployment("current-1", "SUCCESS", 0),
            _make_deployment("prev-1", "REMOVED", 2),
        ])

        result = find_rollback_target(
            "test-project", current_deployment_id="current-1"
        )

        assert result.ok
        assert result.details["target_deployment_id"] == "prev-1"

    @patch("devops_agent.rollback.get_deployments")
    def test_respects_max_age(self, mock_get):
        """Deployments older than max_age_days are excluded unless force=True."""
        mock_get.return_value = _mock_deployments([
            _make_deployment("current-1", "SUCCESS", 0),
            _make_deployment("old-1", "REMOVED", 10),
        ])

        # Without force: no target (10 days > 7 day default)
        result = find_rollback_target("test-project")
        assert not result.ok
        assert result.code == ErrorCode.NO_ROLLBACK_TARGET

        # With force: finds target
        result = find_rollback_target("test-project", force=True)
        assert result.ok
        assert result.details["target_deployment_id"] == "old-1"

    @patch("devops_agent.rollback.get_deployments")
    def test_no_eligible_returns_error(self, mock_get):
        """Returns NO_ROLLBACK_TARGET when all deployments are FAILED/SKIPPED."""
        mock_get.return_value = _mock_deployments([
            _make_deployment("current-1", "SUCCESS", 0),
            _make_deployment("fail-1", "FAILED", 1),
            _make_deployment("skip-1", "SKIPPED", 2),
        ])

        result = find_rollback_target("test-project")
        assert not result.ok
        assert result.code == ErrorCode.NO_ROLLBACK_TARGET

    @patch("devops_agent.rollback.get_deployments")
    def test_prefers_success_over_removed(self, mock_get):
        """If both SUCCESS and REMOVED candidates exist, SUCCESS is preferred."""
        mock_get.return_value = _mock_deployments([
            _make_deployment("current-1", "SUCCESS", 0),
            _make_deployment("prev-removed", "REMOVED", 1),
            _make_deployment("prev-success", "SUCCESS", 2),
        ])

        result = find_rollback_target("test-project")
        assert result.ok
        assert result.details["target_deployment_id"] == "prev-success"

    @patch("devops_agent.rollback.get_deployments")
    def test_handles_malformed_timestamp(self, mock_get):
        """Gracefully handles deployments with unparseable timestamps."""
        deployments = [
            _make_deployment("current-1", "SUCCESS", 0),
            {"id": "bad-ts", "status": "REMOVED", "created_at": "not-a-date"},
            _make_deployment("good-1", "REMOVED", 2),
        ]
        mock_get.return_value = _mock_deployments(deployments)

        result = find_rollback_target("test-project")
        assert result.ok
        # Should skip bad-ts and find good-1
        assert result.details["target_deployment_id"] == "good-1"

    @patch("devops_agent.rollback.get_deployments")
    def test_no_deployments_returns_error(self, mock_get):
        """Returns error when project has no deployments at all."""
        mock_get.return_value = _mock_deployments([])

        result = find_rollback_target("test-project")
        assert not result.ok
        assert result.code == ErrorCode.NO_ROLLBACK_TARGET

    @patch("devops_agent.rollback.get_deployments")
    def test_single_deployment_returns_error(self, mock_get):
        """Returns error when only the current deployment exists."""
        mock_get.return_value = _mock_deployments([
            _make_deployment("only-one", "SUCCESS", 0),
        ])

        result = find_rollback_target("test-project")
        assert not result.ok
        assert result.code == ErrorCode.NO_ROLLBACK_TARGET

    @patch("devops_agent.rollback.get_deployments")
    def test_get_deployments_failure_propagates(self, mock_get):
        """API failure from get_deployments is returned directly."""
        mock_get.return_value = RailwayResult(
            ok=False,
            code=ErrorCode.TIMEOUT,
            message="Railway API timeout",
            project="test-project",
        )

        result = find_rollback_target("test-project")
        assert not result.ok
        assert result.code == ErrorCode.TIMEOUT


class TestRollbackEligible:
    """Verify the eligible status set."""

    def test_success_is_eligible(self):
        assert "SUCCESS" in ROLLBACK_ELIGIBLE

    def test_removed_is_eligible(self):
        assert "REMOVED" in ROLLBACK_ELIGIBLE

    def test_failed_is_not_eligible(self):
        assert "FAILED" not in ROLLBACK_ELIGIBLE

    def test_building_is_not_eligible(self):
        assert "BUILDING" not in ROLLBACK_ELIGIBLE
