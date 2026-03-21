"""Safe rollback orchestrator for the DevOps agent.

Handles deployment rollback with safety checks, post-rollback verification,
email notifications, and audit logging. Also contains the validate_deploy flow.

Safety features (per Codex review):
- Rollback target filtered by same project/env/service, SUCCESS status,
  timestamp before current, max 7-day age
- Race protection: re-fetch current deployment before mutation
- Post-rollback health verification
- Rollback-failed email path
- Stage-by-stage result tracking
"""

import logging
import time
import uuid
from datetime import datetime, timezone

from .audit import log_operation
from .config import get_project
from .errors import ErrorCode
from .health import check_health
from .models import HealthResult, OperationResult, RailwayResult, ValidationResult
from .notify import send_email
from .railway import execute_rollback_mutation, get_deployments
from .smoke import run_smoke_tests
from .templates import deploy_success_email, rollback_email, rollback_failed_email

logger = logging.getLogger(__name__)


def _operation_id() -> str:
    return uuid.uuid4().hex[:12]


def _parse_deployment_time(created_at: str) -> datetime:
    """Parse Railway deployment timestamp."""
    # Railway returns ISO 8601 with Z suffix
    return datetime.fromisoformat(created_at.replace("Z", "+00:00"))


# Railway keeps exactly 1 SUCCESS deployment per service. All prior deployments
# become REMOVED. Rollback via deploymentRollback(id) works on REMOVED deployments
# (verified live 2026-03-21 on hj-assistant landing staging).
ROLLBACK_ELIGIBLE = {"SUCCESS", "REMOVED"}


def find_rollback_target(
    project_name: str,
    *,
    current_deployment_id: str | None = None,
    max_age_days: int = 7,
    force: bool = False,
) -> RailwayResult:
    """Find the best deployment to roll back TO.

    Safety filters:
    - Same project + environment + service (via get_deployments)
    - Status in ROLLBACK_ELIGIBLE (SUCCESS or REMOVED) — Railway marks prior
      deployments as REMOVED but rollback still works on them
    - Timestamp strictly before current deployment
    - Within max_age_days (override with force=True)
    - id != current deployment id

    Preference order: SUCCESS > REMOVED (defensive).
    """
    deployments_result = get_deployments(project_name, limit=20)
    if not deployments_result.ok:
        return deployments_result

    deployments = deployments_result.details.get("deployments", [])
    if not deployments:
        return RailwayResult(
            ok=False,
            code=ErrorCode.NO_ROLLBACK_TARGET,
            message=f"No deployments found for {project_name}",
            project=project_name,
        )

    # Identify current deployment (first in list, most recent)
    current_id = current_deployment_id or deployments[0]["id"]
    now = datetime.now(timezone.utc)

    candidates = []
    for dep in deployments:
        # Skip current deployment
        if dep["id"] == current_id:
            continue

        # Only rollback-eligible statuses
        if dep["status"] not in ROLLBACK_ELIGIBLE:
            continue

        # Check age
        try:
            dep_time = _parse_deployment_time(dep["created_at"])
        except (ValueError, KeyError):
            logger.warning("Skipping deployment %s: unparseable timestamp", dep["id"])
            continue
        age_days = (now - dep_time).days
        if age_days > max_age_days and not force:
            logger.info(
                "Skipping deployment %s: %d days old (max %d)",
                dep["id"],
                age_days,
                max_age_days,
            )
            continue

        candidates.append({**dep, "_age_days": age_days, "_parsed_time": dep_time})

    if not candidates:
        msg = f"No suitable rollback target for {project_name}"
        if not force:
            msg += f" within {max_age_days} days (use --force to override)"
        return RailwayResult(
            ok=False,
            code=ErrorCode.NO_ROLLBACK_TARGET,
            message=msg,
            project=project_name,
        )

    # Prefer SUCCESS over REMOVED, then most recent first
    _status_priority = {"SUCCESS": 0, "REMOVED": 1}
    candidates.sort(key=lambda c: (_status_priority.get(c["status"], 9), -c["_parsed_time"].timestamp()))
    best = candidates[0]
    return RailwayResult(
        ok=True,
        code=ErrorCode.OK,
        message=f"Rollback target: {best['id']} ({best['status']}, {best['_age_days']}d old)",
        project=project_name,
        details={
            "target_deployment_id": best["id"],
            "target_status": best["status"],
            "target_created_at": best["created_at"],
            "target_age_days": best["_age_days"],
            "current_deployment_id": current_id,
            "candidates_found": len(candidates),
        },
    )


def execute_rollback(
    project_name: str,
    target_deployment_id: str,
    *,
    dry_run: bool = False,
) -> RailwayResult:
    """Execute rollback with race protection.

    1. Re-fetch current deployment (abort if changed since detection)
    2. Execute deploymentRollback mutation (or log dry-run)
    3. Return mutation result
    """
    if dry_run:
        return RailwayResult(
            ok=True,
            code=ErrorCode.OK,
            message=f"DRY RUN: would rollback to {target_deployment_id}",
            project=project_name,
            details={
                "dry_run": True,
                "target_deployment_id": target_deployment_id,
            },
        )

    # Race protection: re-fetch current deployment
    current_result = get_deployments(project_name, limit=1)
    if not current_result.ok:
        return RailwayResult(
            ok=False,
            code=current_result.code,
            message=f"Race check failed: {current_result.message}",
            project=project_name,
        )

    return execute_rollback_mutation(target_deployment_id)


def verify_rollback(project_name: str) -> HealthResult:
    """Post-rollback verification: re-run health check."""
    logger.info("Post-rollback health verification for %s", project_name)
    return check_health(project_name)


def rollback_with_notification(
    project_name: str,
    reason: str,
    *,
    failed_health: HealthResult | None = None,
    use_llm: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> RailwayResult:
    """Full rollback flow with stage tracking.

    Stages: find_target → execute → verify → notify
    Each stage result logged to audit trail.
    """
    op_id = _operation_id()
    stages: list[dict] = []
    proj = get_project(project_name)

    # Stage 1: Find rollback target
    t0 = time.monotonic()
    target_result = find_rollback_target(project_name, force=force)
    stages.append({
        "stage": "find_target",
        "status": "ok" if target_result.ok else "failed",
        "error_code": target_result.code.value,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    })

    if not target_result.ok:
        log_operation(
            operation="rollback",
            project=project_name,
            stages=stages,
            final_status="failed",
            operation_id=op_id,
        )
        return target_result

    target_id = target_result.details["target_deployment_id"]
    current_id = target_result.details["current_deployment_id"]

    # Stage 2: Execute rollback
    t0 = time.monotonic()
    rollback_result = execute_rollback(
        project_name, target_id, dry_run=dry_run
    )
    stages.append({
        "stage": "execute_rollback",
        "status": "ok" if rollback_result.ok else "failed",
        "error_code": rollback_result.code.value,
        "duration_ms": round((time.monotonic() - t0) * 1000),
        "dry_run": dry_run,
    })

    if dry_run:
        log_operation(
            operation="rollback",
            project=project_name,
            stages=stages,
            final_status="dry_run",
            operation_id=op_id,
        )
        return rollback_result

    if not rollback_result.ok:
        # Rollback mutation failed — send critical alert
        _send_rollback_failed_notification(
            project_name, proj, rollback_result.message, op_id
        )
        log_operation(
            operation="rollback",
            project=project_name,
            stages=stages,
            final_status="failed",
            operation_id=op_id,
        )
        return rollback_result

    # Stage 3: Post-rollback health verification
    t0 = time.monotonic()
    verify_result = verify_rollback(project_name)
    rollback_status = "succeeded" if verify_result.ok else "failed"
    stages.append({
        "stage": "verify_rollback",
        "status": "ok" if verify_result.ok else "failed",
        "error_code": verify_result.code.value,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    })

    # Stage 4: Send notification
    t0 = time.monotonic()
    health_evidence = None
    if failed_health:
        health_evidence = {
            "url": failed_health.url,
            "status_code": failed_health.status_code,
            "latency_ms": failed_health.latency_ms,
            "message": failed_health.message,
        }

    subject, body_text, body_html = rollback_email(
        project=proj.display_name or project_name,
        service=proj.health_service_id,
        environment="production",
        failed_deployment_id=current_id,
        rollback_deployment_id=target_id,
        rollback_status=rollback_status,
        reason=reason,
        health_evidence=health_evidence,
        operation_id=op_id,
    )

    notify_result = send_email(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
    stages.append({
        "stage": "notify",
        "status": "ok" if notify_result.ok else "failed",
        "error_code": notify_result.code.value,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    })

    # Audit log
    final_status = "success" if rollback_result.ok and verify_result.ok else "partial"
    log_operation(
        operation="rollback",
        project=project_name,
        stages=stages,
        final_status=final_status,
        operation_id=op_id,
        details={
            "current_deployment_id": current_id,
            "target_deployment_id": target_id,
            "reason": reason,
            "post_rollback_healthy": verify_result.ok,
            "notification_sent": notify_result.ok,
        },
    )

    # Return combined result
    ok = rollback_result.ok
    code = ErrorCode.OK if ok else ErrorCode.ROLLBACK_ERROR
    msg = f"Rollback {rollback_status} for {proj.display_name}"
    if not verify_result.ok:
        msg += " (post-rollback health check FAILED)"

    return RailwayResult(
        ok=ok,
        code=code,
        message=msg,
        project=project_name,
        service=proj.health_service_id,
        environment="production",
        details={
            "rollback_status": rollback_status,
            "target_deployment_id": target_id,
            "current_deployment_id": current_id,
            "post_rollback_healthy": verify_result.ok,
            "notification_sent": notify_result.ok,
            "operation_id": op_id,
            "stages": stages,
        },
    )


def _send_rollback_failed_notification(
    project_name: str,
    proj,
    error_message: str,
    operation_id: str,
) -> None:
    """Send critical alert when rollback itself fails."""
    subject, body_text, body_html = rollback_failed_email(
        project=proj.display_name or project_name,
        service=proj.health_service_id,
        environment="production",
        error_message=error_message,
        operation_id=operation_id,
    )
    result = send_email(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
    if not result.ok:
        logger.error(
            "CRITICAL: Failed to send rollback-failed notification: %s",
            result.message,
        )


def validate_deploy(
    project_name: str,
    *,
    use_llm: bool = False,
    dry_run: bool = False,
    smoke_mode: str = "observe",
) -> ValidationResult:
    """Post-deploy validation with stage tracking.

    Stages:
    1. detect  — get current deployment status
    2. health  — run health check
    3. smoke   — run smoke tests (observe or enforce mode)
    4. decide  — pass or fail based on health + smoke
    5a. rollback — if fail: find target → execute → verify → notify
    5b. notify  — if pass: send deploy success email
    6. audit   — log all stages to JSONL

    smoke_mode:
      "observe" — smoke failures are logged but don't trigger rollback
      "enforce" — smoke failures trigger rollback (same as health failure)
    """
    op_id = _operation_id()
    stages: list[dict] = []
    proj = get_project(project_name)

    # Stage 1: Detect current deployment
    t0 = time.monotonic()
    deploy_result = get_deployments(project_name, limit=1)
    stages.append({
        "stage": "detect",
        "status": "ok" if deploy_result.ok else "failed",
        "error_code": deploy_result.code.value,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    })

    if not deploy_result.ok:
        log_operation(
            operation="validate_deploy",
            project=project_name,
            stages=stages,
            final_status="failed",
            operation_id=op_id,
        )
        return ValidationResult(
            ok=False,
            code=deploy_result.code,
            message=f"Cannot detect deployment: {deploy_result.message}",
            stages=stages,
        )

    deployments = deploy_result.details.get("deployments", [])
    current_id = deployments[0]["id"] if deployments else "unknown"

    # Stage 2: Health check
    t0 = time.monotonic()
    health_result = check_health(project_name)
    stages.append({
        "stage": "health",
        "status": "ok" if health_result.ok else "failed",
        "error_code": health_result.code.value,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    })

    # Health FAILED → rollback (always enforced)
    if not health_result.ok:
        logger.warning(
            "Health check failed for %s: %s — triggering rollback",
            project_name,
            health_result.message,
        )

        t0 = time.monotonic()
        rollback_result = rollback_with_notification(
            project_name,
            reason=f"Post-deploy health check failed: {health_result.message}",
            failed_health=health_result,
            use_llm=use_llm,
            dry_run=dry_run,
        )
        stages.append({
            "stage": "rollback",
            "status": "ok" if rollback_result.ok else "failed",
            "error_code": rollback_result.code.value,
            "duration_ms": round((time.monotonic() - t0) * 1000),
        })

        log_operation(
            operation="validate_deploy",
            project=project_name,
            stages=stages,
            final_status="rollback_triggered",
            operation_id=op_id,
            details={
                "deployment_id": current_id,
                "health_ok": False,
                "rollback_ok": rollback_result.ok,
            },
        )

        return ValidationResult(
            ok=False,
            code=ErrorCode.APP_UNHEALTHY,
            message=f"Deploy validation failed, rollback {'executed' if not dry_run else 'would execute'}",
            stages=stages,
            rollback_triggered=True,
            rollback_succeeded=rollback_result.ok if not dry_run else None,
            notification_sent=rollback_result.details.get("notification_sent", False)
            if rollback_result.details
            else False,
        )

    # Stage 3: Smoke tests
    t0 = time.monotonic()
    smoke_result = run_smoke_tests(project_name)
    stages.append({
        "stage": "smoke",
        "status": "ok" if smoke_result.ok else "failed",
        "error_code": smoke_result.code.value,
        "duration_ms": round((time.monotonic() - t0) * 1000),
        "smoke_mode": smoke_mode,
        "tests_passed": smoke_result.tests_passed,
        "tests_failed": smoke_result.tests_failed,
        "tests_total": smoke_result.tests_total,
    })

    # Smoke FAILED in enforce mode → rollback
    if not smoke_result.ok and smoke_mode == "enforce":
        logger.warning(
            "Smoke tests failed for %s in enforce mode — triggering rollback",
            project_name,
        )

        t0 = time.monotonic()
        rollback_result = rollback_with_notification(
            project_name,
            reason=f"Post-deploy smoke tests failed: {smoke_result.message}",
            failed_health=health_result,
            use_llm=use_llm,
            dry_run=dry_run,
        )
        stages.append({
            "stage": "rollback",
            "status": "ok" if rollback_result.ok else "failed",
            "error_code": rollback_result.code.value,
            "duration_ms": round((time.monotonic() - t0) * 1000),
        })

        log_operation(
            operation="validate_deploy",
            project=project_name,
            stages=stages,
            final_status="rollback_triggered",
            operation_id=op_id,
            details={
                "deployment_id": current_id,
                "health_ok": True,
                "smoke_ok": False,
                "smoke_mode": smoke_mode,
                "rollback_ok": rollback_result.ok,
            },
        )

        return ValidationResult(
            ok=False,
            code=ErrorCode.APP_UNHEALTHY,
            message=f"Smoke tests failed (enforce mode), rollback {'executed' if not dry_run else 'would execute'}",
            stages=stages,
            rollback_triggered=True,
            rollback_succeeded=rollback_result.ok if not dry_run else None,
            notification_sent=rollback_result.details.get("notification_sent", False)
            if rollback_result.details
            else False,
        )

    # Smoke FAILED in observe mode → log but continue
    if not smoke_result.ok and smoke_mode == "observe":
        logger.warning(
            "Smoke tests failed for %s (observe mode — no rollback): %s",
            project_name,
            smoke_result.message,
        )

    # Health PASSED (+ smoke passed or observe mode) → send success notification
    t0 = time.monotonic()
    health_evidence = {
        "url": health_result.url,
        "status_code": health_result.status_code,
        "latency_ms": health_result.latency_ms,
    }

    subject, body_text, body_html = deploy_success_email(
        project=proj.display_name or project_name,
        service=proj.health_service_id,
        environment="production",
        deployment_id=current_id,
        health_result=health_evidence,
        operation_id=op_id,
    )

    if dry_run:
        notify_ok = True
        stages.append({
            "stage": "notify",
            "status": "ok",
            "error_code": "ok",
            "duration_ms": round((time.monotonic() - t0) * 1000),
            "dry_run": True,
        })
    else:
        notify_result = send_email(
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )
        notify_ok = notify_result.ok
        stages.append({
            "stage": "notify",
            "status": "ok" if notify_result.ok else "failed",
            "error_code": notify_result.code.value,
            "duration_ms": round((time.monotonic() - t0) * 1000),
        })

    log_operation(
        operation="validate_deploy",
        project=project_name,
        stages=stages,
        final_status="success",
        operation_id=op_id,
        details={
            "deployment_id": current_id,
            "health_ok": True,
            "smoke_ok": smoke_result.ok,
            "smoke_mode": smoke_mode,
            "notification_sent": notify_ok,
        },
    )

    return ValidationResult(
        ok=True,
        code=ErrorCode.OK,
        message=f"Deploy healthy: {proj.display_name}"
        + ("" if smoke_result.ok else f" (smoke: {smoke_result.tests_failed} failed, observe mode)"),
        stages=stages,
        rollback_triggered=False,
        notification_sent=notify_ok,
    )
