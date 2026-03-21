"""Deterministic email templates for DevOps agent notifications.

These are the primary notification path. LLM-enhanced drafts (analyze.py)
are optional enhancements behind --use-llm flag.

Every template returns (subject, body_text, body_html).
"""

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _health_summary(health_evidence: dict | None) -> str:
    """Format health check evidence for email body."""
    if not health_evidence:
        return "No health check data available."
    parts = []
    if "url" in health_evidence:
        parts.append(f"URL: {health_evidence['url']}")
    if "status_code" in health_evidence:
        parts.append(f"Status: {health_evidence['status_code']}")
    if "latency_ms" in health_evidence:
        parts.append(f"Latency: {health_evidence['latency_ms']:.0f}ms")
    if "message" in health_evidence:
        parts.append(f"Message: {health_evidence['message']}")
    return " | ".join(parts) if parts else "No details."


def rollback_email(
    *,
    project: str,
    service: str,
    environment: str,
    failed_deployment_id: str,
    rollback_deployment_id: str,
    rollback_status: str,
    reason: str,
    health_evidence: dict | None = None,
    timestamp: str | None = None,
    operation_id: str = "",
) -> tuple[str, str, str]:
    """Deterministic rollback notification email.

    Covers all spec section 13.3 fields:
    - Service name, environment, deployment ID, time
    - Reason, failed health signal, DB migration status
    - Rollback outcome, what needs review, operation ID
    """
    ts = timestamp or _now_iso()
    health_str = _health_summary(health_evidence)
    status_label = "SUCCEEDED" if rollback_status == "succeeded" else "FAILED"

    subject = f"[DevOps] Rollback {status_label}: {project}/{service} ({environment})"

    body_text = f"""ROLLBACK {status_label}

Project: {project}
Service: {service}
Environment: {environment}
Time: {ts}

Failed Deployment: {failed_deployment_id}
Rolled Back To: {rollback_deployment_id}
Rollback Status: {status_label}

Reason: {reason}

Health Check Evidence:
{health_str}

Database Changes Executed: No
(v1 agent does not execute migrations)

What Needs Human Review:
- Verify the rolled-back deployment is serving correctly
- Investigate the root cause of the failed deployment
- Check if any data or state changes occurred during the failed deploy

Operation ID: {operation_id}
"""

    body_html = f"""<html><body style="font-family: -apple-system, system-ui, sans-serif; max-width: 600px;">
<h2 style="color: {'#c0392b' if rollback_status != 'succeeded' else '#e67e22'};">
  Rollback {status_label}</h2>

<table style="border-collapse: collapse; width: 100%;">
<tr><td style="padding: 4px 8px; font-weight: bold;">Project</td><td style="padding: 4px 8px;">{project}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Service</td><td style="padding: 4px 8px;">{service}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Environment</td><td style="padding: 4px 8px;">{environment}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Time</td><td style="padding: 4px 8px;">{ts}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Failed Deploy</td><td style="padding: 4px 8px;"><code>{failed_deployment_id}</code></td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Rolled Back To</td><td style="padding: 4px 8px;"><code>{rollback_deployment_id}</code></td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Status</td><td style="padding: 4px 8px;"><strong>{status_label}</strong></td></tr>
</table>

<h3>Reason</h3>
<p>{reason}</p>

<h3>Health Check Evidence</h3>
<p>{health_str}</p>

<h3>Database Changes</h3>
<p>No (v1 agent does not execute migrations)</p>

<h3>What Needs Human Review</h3>
<ul>
<li>Verify the rolled-back deployment is serving correctly</li>
<li>Investigate the root cause of the failed deployment</li>
<li>Check if any data or state changes occurred during the failed deploy</li>
</ul>

<p style="color: #888; font-size: 12px;">Operation ID: {operation_id}</p>
</body></html>"""

    return subject, body_text, body_html


def deploy_success_email(
    *,
    project: str,
    service: str,
    environment: str,
    deployment_id: str,
    health_result: dict | None = None,
    timestamp: str | None = None,
    operation_id: str = "",
) -> tuple[str, str, str]:
    """Deterministic deploy success notification email.

    Covers spec section 15.1 fields. Fields not yet implemented
    are marked N/A rather than claiming false positives.
    """
    ts = timestamp or _now_iso()
    health_str = _health_summary(health_result)

    subject = f"[DevOps] Deploy OK: {project}/{service} ({environment})"

    body_text = f"""DEPLOYMENT SUCCESSFUL

Project: {project}
Service: {service}
Environment: {environment}
Deployment Time: {ts}
Deployment ID: {deployment_id}

Commit Reference: N/A (DeploymentMeta unavailable via API)
Staging Validated: N/A (not implemented — Phase 5)
Observation Window (5min): N/A (not implemented — Phase 5)
Smoke Tests: Health check only (Phase 3 adds full smoke suite)
Migration Involved: No

Health Check Result:
{health_str}

Operation ID: {operation_id}
"""

    body_html = f"""<html><body style="font-family: -apple-system, system-ui, sans-serif; max-width: 600px;">
<h2 style="color: #27ae60;">Deployment Successful</h2>

<table style="border-collapse: collapse; width: 100%;">
<tr><td style="padding: 4px 8px; font-weight: bold;">Project</td><td style="padding: 4px 8px;">{project}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Service</td><td style="padding: 4px 8px;">{service}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Environment</td><td style="padding: 4px 8px;">{environment}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Time</td><td style="padding: 4px 8px;">{ts}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Deployment ID</td><td style="padding: 4px 8px;"><code>{deployment_id}</code></td></tr>
</table>

<h3>Validation Summary</h3>
<table style="border-collapse: collapse; width: 100%;">
<tr><td style="padding: 4px 8px;">Commit Reference</td><td style="padding: 4px 8px; color: #888;">N/A (DeploymentMeta unavailable)</td></tr>
<tr><td style="padding: 4px 8px;">Staging Validated</td><td style="padding: 4px 8px; color: #888;">N/A (Phase 5)</td></tr>
<tr><td style="padding: 4px 8px;">Observation Window</td><td style="padding: 4px 8px; color: #888;">N/A (Phase 5)</td></tr>
<tr><td style="padding: 4px 8px;">Smoke Tests</td><td style="padding: 4px 8px;">Health check only (Phase 3)</td></tr>
<tr><td style="padding: 4px 8px;">Migration</td><td style="padding: 4px 8px;">No</td></tr>
</table>

<h3>Health Check</h3>
<p>{health_str}</p>

<p style="color: #888; font-size: 12px;">Operation ID: {operation_id}</p>
</body></html>"""

    return subject, body_text, body_html


def rollback_failed_email(
    *,
    project: str,
    service: str,
    environment: str,
    error_message: str,
    timestamp: str | None = None,
    operation_id: str = "",
) -> tuple[str, str, str]:
    """Critical alert: rollback attempt itself failed.

    This means the service is still running the bad deployment
    and needs immediate human intervention.
    """
    ts = timestamp or _now_iso()

    subject = f"[DevOps] CRITICAL: Rollback FAILED — {project}/{service} ({environment})"

    body_text = f"""CRITICAL: ROLLBACK FAILED

The DevOps agent attempted to roll back a failed deployment but the
rollback itself failed. The service may still be running the bad deployment.

IMMEDIATE HUMAN ACTION REQUIRED.

Project: {project}
Service: {service}
Environment: {environment}
Time: {ts}

Error: {error_message}

Recommended Actions:
1. Check the Railway dashboard for the current deployment status
2. Manually rollback via Railway dashboard if needed
3. Verify service health after manual intervention
4. Investigate why the automated rollback failed

Operation ID: {operation_id}
"""

    body_html = f"""<html><body style="font-family: -apple-system, system-ui, sans-serif; max-width: 600px;">
<h2 style="color: #c0392b; background: #ffeaa7; padding: 12px; border-radius: 4px;">
  CRITICAL: Rollback FAILED</h2>

<p><strong>The DevOps agent attempted to roll back a failed deployment but the
rollback itself failed.</strong> The service may still be running the bad deployment.</p>

<p style="color: #c0392b; font-weight: bold; font-size: 16px;">
  IMMEDIATE HUMAN ACTION REQUIRED</p>

<table style="border-collapse: collapse; width: 100%;">
<tr><td style="padding: 4px 8px; font-weight: bold;">Project</td><td style="padding: 4px 8px;">{project}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Service</td><td style="padding: 4px 8px;">{service}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Environment</td><td style="padding: 4px 8px;">{environment}</td></tr>
<tr><td style="padding: 4px 8px; font-weight: bold;">Time</td><td style="padding: 4px 8px;">{ts}</td></tr>
</table>

<h3>Error</h3>
<pre style="background: #f8f9fa; padding: 8px; border-radius: 4px;">{error_message}</pre>

<h3>Recommended Actions</h3>
<ol>
<li>Check the Railway dashboard for the current deployment status</li>
<li>Manually rollback via Railway dashboard if needed</li>
<li>Verify service health after manual intervention</li>
<li>Investigate why the automated rollback failed</li>
</ol>

<p style="color: #888; font-size: 12px;">Operation ID: {operation_id}</p>
</body></html>"""

    return subject, body_text, body_html
