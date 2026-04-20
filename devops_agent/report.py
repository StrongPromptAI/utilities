"""Weekly reliability report for the DevOps agent.

Collects deployment data from three sources:
1. JSONL audit trail (agent operations)
2. Railway GraphQL (deployment counts/statuses)
3. Live health + smoke checks (current snapshot)

Three-function split per Codex review:
- collect_report_data() — gather all data
- build_report_views() — format markdown + HTML
- run_report() — orchestrate collect → [LLM] → format → [email]
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from .analyze import call_openrouter
from .audit import AUDIT_LOG
from .config import get_config
from .errors import ErrorCode
from .health import check_health
from .models import (
    AuditSummary,
    ProjectHealthSnapshot,
    ReportData,
    ReportDiagnostics,
    ReportResult,
    ReportWindow,
)
from .notify import send_email
from .railway import get_deployments
from .smoke import run_smoke_tests

logger = logging.getLogger(__name__)

# Operation types we track for metrics
_METRIC_OPS = {
    "validate_deploy", "rollback", "smoke", "health_check",
}


# ---------------------------------------------------------------------------
# 1. Data collection
# ---------------------------------------------------------------------------


def _parse_audit_trail(window: ReportWindow) -> tuple[AuditSummary, list[dict]]:
    """Parse JSONL audit trail for the reporting period.

    Tolerant of malformed lines per Codex review — skips bad lines
    and counts them in malformed_lines.
    """
    summary = AuditSummary()
    events: list[dict] = []

    if not AUDIT_LOG.exists():
        logger.info("No audit log found at %s", AUDIT_LOG)
        return summary, events

    for line_num, line in enumerate(AUDIT_LOG.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            summary.malformed_lines += 1
            logger.warning("audit.jsonl line %d: malformed JSON, skipping", line_num)
            continue

        # Parse timestamp and filter by window
        ts_str = record.get("timestamp")
        if not ts_str:
            summary.malformed_lines += 1
            continue

        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            summary.malformed_lines += 1
            continue

        if ts < window.start or ts > window.end:
            continue

        # Count by operation type
        summary.total_operations += 1
        events.append(record)

        op = record.get("operation", "")
        status = record.get("final_status", "")

        if op == "validate_deploy":
            summary.validate_deploy_count += 1
        elif op == "rollback":
            summary.rollback_count += 1
            if status == "success":
                summary.rollback_succeeded += 1
            else:
                summary.rollback_failed += 1
        elif op == "smoke":
            summary.smoke_runs += 1
            if status != "success":
                summary.smoke_failures += 1
        elif op == "health_check":
            summary.health_checks += 1
            if status != "success":
                summary.health_failures += 1
        elif op not in _METRIC_OPS:
            summary.other_operations += 1

    return summary, events


def collect_report_data(
    days: int = 7,
    run_live_checks: bool = True,
) -> ReportData:
    """Collect all data needed for the weekly reliability report.

    1. Parse JSONL audit trail for the reporting period
    2. Query Railway GraphQL for deployment counts per project
    3. If run_live_checks: run health + smoke checks across all projects
    4. Aggregate into structured ReportData with diagnostics
    """
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)
    window = ReportWindow(
        start=now - timedelta(days=days),
        end=now,
        days=days,
    )

    diag = ReportDiagnostics()
    diag.data_sources.append("audit_trail")

    # 1. Audit trail
    audit_summary, audit_events = _parse_audit_trail(window)
    if audit_summary.malformed_lines > 0:
        diag.warnings.append(
            f"{audit_summary.malformed_lines} malformed lines in audit.jsonl"
        )

    # 2. Railway deployments + 3. Live checks
    config = get_config()
    snapshots: list[ProjectHealthSnapshot] = []

    diag.data_sources.append("railway_graphql")
    if run_live_checks:
        diag.data_sources.append("live_checks")

    for name, proj in sorted(config.projects.items()):
        diag.projects_checked += 1
        snap = ProjectHealthSnapshot(
            project=name,
            tier=proj.tier,
        )

        # Railway deployment count
        try:
            dep_result = get_deployments(name, limit=10)
            if dep_result.ok:
                deployments = dep_result.details.get("deployments", [])
                snap.deployment_count = len(deployments)
                if deployments:
                    snap.current_status = deployments[0].get("status", "UNKNOWN")
        except Exception as e:
            logger.warning("Railway query failed for %s: %s", name, e)
            diag.warnings.append(f"Railway query failed for {name}: {e}")

        # Live health check
        if run_live_checks and proj.health_url:
            try:
                health = check_health(name)
                snap.health_ok = health.ok
                snap.health_latency_ms = health.latency_ms
                if not health.ok:
                    snap.health_error = health.message
            except Exception as e:
                snap.health_ok = False
                snap.health_error = f"unreachable: {e}"
                diag.projects_unreachable += 1
        elif run_live_checks and not proj.health_url:
            snap.health_ok = None
            snap.health_error = "no health_url configured"
        # else: --no-live-checks, leave health_ok as None

        # Live smoke tests
        if run_live_checks and proj.smoke_tests:
            try:
                smoke = run_smoke_tests(name)
                snap.smoke_passed = smoke.tests_passed
                snap.smoke_failed = smoke.tests_failed
                snap.smoke_total = smoke.tests_total
            except Exception as e:
                logger.warning("Smoke tests failed for %s: %s", name, e)
                diag.warnings.append(f"Smoke tests failed for {name}: {e}")

        snapshots.append(snap)

    diag.generation_time_ms = round((time.monotonic() - t0) * 1000)

    return ReportData(
        window=window,
        project_snapshots=snapshots,
        audit_summary=audit_summary,
        diagnostics=diag,
        audit_events=audit_events,
    )


# ---------------------------------------------------------------------------
# 2. Report formatting
# ---------------------------------------------------------------------------


def _health_icon(ok: bool | None) -> str:
    """Health status indicator for markdown."""
    if ok is None:
        return "—"
    return "OK" if ok else "FAIL"


def build_report_views(
    data: ReportData,
    llm_insights: str | None = None,
) -> tuple[str, str]:
    """Build markdown and HTML views of the report.

    Returns (markdown, html). Deterministic — LLM insights are an optional
    section appended if provided.
    """
    s = data.audit_summary
    d = data.diagnostics
    w = data.window

    # --- Markdown ---
    lines = [
        f"# Weekly Reliability Report",
        f"",
        f"**Period**: {w.start.strftime('%Y-%m-%d %H:%M')} to {w.end.strftime('%Y-%m-%d %H:%M')} UTC ({w.days} days)",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"",
        f"## Deployment Activity",
        f"",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total agent operations | {s.total_operations} |",
        f"| Validate-deploy runs | {s.validate_deploy_count} |",
        f"| Rollbacks triggered | {s.rollback_count} |",
        f"| Rollbacks succeeded | {s.rollback_succeeded} |",
        f"| Rollbacks failed | {s.rollback_failed} |",
        f"| Smoke test runs | {s.smoke_runs} |",
        f"| Smoke failures | {s.smoke_failures} |",
        f"| Health checks | {s.health_checks} |",
        f"| Health failures | {s.health_failures} |",
        f"",
        f"## Project Health Snapshot",
        f"",
        f"| Project | Tier | Health | Latency | Smoke | Deploys | Status |",
        f"|---------|------|--------|---------|-------|---------|--------|",
    ]

    for snap in data.project_snapshots:
        health_str = _health_icon(snap.health_ok)
        latency_str = f"{snap.health_latency_ms:.0f}ms" if snap.health_latency_ms else "—"
        smoke_str = (
            f"{snap.smoke_passed}/{snap.smoke_total}"
            if snap.smoke_total > 0
            else "—"
        )
        lines.append(
            f"| {snap.project} | T{snap.tier} | {health_str} "
            f"| {latency_str} | {smoke_str} | {snap.deployment_count} | {snap.current_status} |"
        )

    # Reliability observations (deterministic)
    lines.extend(["", "## Reliability Observations", ""])
    observations = _build_observations(data)
    if observations:
        for obs in observations:
            lines.append(f"- {obs}")
    else:
        lines.append("- No notable issues detected in this period.")

    # Staging tracking note
    lines.extend([
        "",
        "## Environment Coverage",
        "",
        "- **Production**: Tracked via Railway GraphQL + live health checks",
        "- **Staging**: Not configured for automated tracking (manual validation only)",
    ])

    # LLM insights
    if llm_insights:
        lines.extend([
            "",
            "## AI-Generated Reliability Insights",
            "",
            llm_insights,
        ])

    # Diagnostics
    lines.extend([
        "",
        "## Report Diagnostics",
        "",
        f"- Projects checked: {d.projects_checked}",
        f"- Projects unreachable: {d.projects_unreachable}",
        f"- Projects timed out: {d.projects_timed_out}",
        f"- Data sources: {', '.join(d.data_sources)}",
        f"- Generation time: {d.generation_time_ms:.0f}ms",
    ])
    if s.malformed_lines > 0:
        lines.append(f"- Malformed audit lines skipped: {s.malformed_lines}")
    if d.warnings:
        lines.append(f"- Warnings: {len(d.warnings)}")
        for w_msg in d.warnings:
            lines.append(f"  - {w_msg}")

    markdown = "\n".join(lines)

    # --- HTML ---
    html = _build_html(data, llm_insights)

    return markdown, html


def _build_observations(data: ReportData) -> list[str]:
    """Generate deterministic reliability observations from data."""
    obs = []
    s = data.audit_summary

    if s.rollback_failed > 0:
        obs.append(
            f"CRITICAL: {s.rollback_failed} rollback(s) failed during this period. "
            f"Manual intervention may have been needed."
        )
    if s.rollback_count > 0:
        obs.append(
            f"{s.rollback_count} rollback(s) triggered "
            f"({s.rollback_succeeded} succeeded, {s.rollback_failed} failed)."
        )
    if s.health_failures > 0:
        obs.append(f"{s.health_failures} health check failure(s) detected.")
    if s.smoke_failures > 0:
        obs.append(f"{s.smoke_failures} smoke test failure(s) in {s.smoke_runs} runs.")

    # Live snapshot observations
    unhealthy = [
        snap.project
        for snap in data.project_snapshots
        if snap.health_ok is False
    ]
    if unhealthy:
        obs.append(
            f"Currently unhealthy: {', '.join(unhealthy)}. "
            f"Immediate investigation recommended."
        )

    unreachable = [
        snap.project
        for snap in data.project_snapshots
        if snap.health_error and "unreachable" in snap.health_error.lower()
    ]
    if unreachable:
        obs.append(f"Unreachable projects: {', '.join(unreachable)}.")

    if s.total_operations == 0:
        obs.append(
            "No agent operations recorded in this period. "
            "The audit trail may be new or the agent was not invoked."
        )

    return obs


def _build_html(data: ReportData, llm_insights: str | None) -> str:
    """Build HTML report for email."""
    s = data.audit_summary
    d = data.diagnostics
    w = data.window

    # Project rows
    project_rows = []
    for snap in data.project_snapshots:
        health_color = "#27ae60" if snap.health_ok else ("#c0392b" if snap.health_ok is False else "#888")
        health_str = _health_icon(snap.health_ok)
        latency_str = f"{snap.health_latency_ms:.0f}ms" if snap.health_latency_ms else "—"
        smoke_str = (
            f"{snap.smoke_passed}/{snap.smoke_total}"
            if snap.smoke_total > 0
            else "—"
        )
        project_rows.append(
            f'<tr>'
            f'<td style="padding:4px 8px;">{snap.project}</td>'
            f'<td style="padding:4px 8px;">T{snap.tier}</td>'
            f'<td style="padding:4px 8px;color:{health_color};font-weight:bold;">{health_str}</td>'
            f'<td style="padding:4px 8px;">{latency_str}</td>'
            f'<td style="padding:4px 8px;">{smoke_str}</td>'
            f'<td style="padding:4px 8px;">{snap.deployment_count}</td>'
            f'<td style="padding:4px 8px;">{snap.current_status}</td>'
            f'</tr>'
        )

    # Observations
    observations = _build_observations(data)
    obs_html = "".join(f"<li>{o}</li>" for o in observations) if observations else "<li>No notable issues.</li>"

    # LLM section
    llm_section = ""
    if llm_insights:
        # Convert newlines to <br> for simple rendering
        llm_html = llm_insights.replace("\n", "<br>")
        llm_section = f"""
<h3>AI-Generated Reliability Insights</h3>
<div style="background:#f8f9fa;padding:12px;border-radius:4px;border-left:3px solid #3498db;">
{llm_html}
</div>"""

    # Warnings
    warnings_html = ""
    if d.warnings:
        w_items = "".join(f"<li>{w}</li>" for w in d.warnings)
        warnings_html = f"<p style='color:#e67e22;'>Warnings:<ul>{w_items}</ul></p>"

    return f"""<html><body style="font-family:-apple-system,system-ui,sans-serif;max-width:700px;margin:auto;">
<h2>Weekly Reliability Report</h2>
<p><strong>Period</strong>: {w.start.strftime('%Y-%m-%d %H:%M')} to {w.end.strftime('%Y-%m-%d %H:%M')} UTC ({w.days} days)<br>
<strong>Generated</strong>: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}</p>

<h3>Deployment Activity</h3>
<table style="border-collapse:collapse;width:100%;">
<tr><td style="padding:4px 8px;font-weight:bold;">Agent operations</td><td style="padding:4px 8px;">{s.total_operations}</td></tr>
<tr><td style="padding:4px 8px;font-weight:bold;">Validate-deploy runs</td><td style="padding:4px 8px;">{s.validate_deploy_count}</td></tr>
<tr><td style="padding:4px 8px;font-weight:bold;">Rollbacks</td><td style="padding:4px 8px;">{s.rollback_count} ({s.rollback_succeeded} OK / {s.rollback_failed} failed)</td></tr>
<tr><td style="padding:4px 8px;font-weight:bold;">Smoke runs</td><td style="padding:4px 8px;">{s.smoke_runs} ({s.smoke_failures} failures)</td></tr>
<tr><td style="padding:4px 8px;font-weight:bold;">Health checks</td><td style="padding:4px 8px;">{s.health_checks} ({s.health_failures} failures)</td></tr>
</table>

<h3>Project Health Snapshot</h3>
<table style="border-collapse:collapse;width:100%;">
<tr style="background:#f1f3f5;">
<th style="padding:4px 8px;text-align:left;">Project</th>
<th style="padding:4px 8px;">Tier</th>
<th style="padding:4px 8px;">Health</th>
<th style="padding:4px 8px;">Latency</th>
<th style="padding:4px 8px;">Smoke</th>
<th style="padding:4px 8px;">Deploys</th>
<th style="padding:4px 8px;">Status</th>
</tr>
{"".join(project_rows)}
</table>

<h3>Reliability Observations</h3>
<ul>{obs_html}</ul>

<h3>Environment Coverage</h3>
<ul>
<li><strong>Production</strong>: Tracked via Railway GraphQL + live health checks</li>
<li><strong>Staging</strong>: Not configured for automated tracking</li>
</ul>

{llm_section}

<h3 style="color:#888;">Report Diagnostics</h3>
<p style="color:#888;font-size:12px;">
Projects checked: {d.projects_checked} |
Unreachable: {d.projects_unreachable} |
Data sources: {', '.join(d.data_sources)} |
Generation time: {d.generation_time_ms:.0f}ms
</p>
{warnings_html}

</body></html>"""


# ---------------------------------------------------------------------------
# 3. Report orchestration
# ---------------------------------------------------------------------------


def _get_llm_insights(markdown_report: str) -> str | None:
    """Get LLM-generated reliability insights. Fail-open."""
    system = (
        "You are a senior DevOps engineer reviewing a weekly reliability report. "
        "Analyze the data and provide:\n"
        "1. Key reliability themes and patterns\n"
        "2. Recurring issues that need attention\n"
        "3. Specific recommendations for improvement\n"
        "4. Risk assessment for the coming week\n\n"
        "Be specific — reference project names and concrete data points. "
        "Keep it concise (under 300 words). Don't repeat the raw data."
    )

    result = call_openrouter(
        system_prompt=system,
        user_message=f"Here is this week's reliability report:\n\n{markdown_report}",
        model="anthropic/claude-haiku-4.5",
        max_tokens=800,
    )

    if not result.ok:
        logger.warning("LLM insights failed (fail-open): %s", result.message)
        return None

    return result.response_text


def run_report(
    days: int = 7,
    use_llm: bool = False,
    send: bool = False,
    dry_run: bool = False,
    no_live_checks: bool = False,
) -> ReportResult:
    """Full report pipeline: collect -> [optional LLM] -> format -> [optional email].

    Args:
        days: Reporting period in days (default 7).
        use_llm: Add LLM-generated insights via OpenRouter.
        send: Send report via email.
        dry_run: Generate but don't send (overrides send).
        no_live_checks: Skip health/smoke checks.
    """
    t0 = time.monotonic()

    # Collect
    data = collect_report_data(days=days, run_live_checks=not no_live_checks)

    # Format (first pass — deterministic, for LLM input)
    markdown_draft, _ = build_report_views(data)

    # Optional LLM
    llm_insights = None
    if use_llm:
        llm_insights = _get_llm_insights(markdown_draft)

    # Format (final pass — with LLM insights if available)
    markdown, html = build_report_views(data, llm_insights=llm_insights)

    total_ms = round((time.monotonic() - t0) * 1000)
    data.diagnostics.generation_time_ms = total_ms

    # Email
    email_sent = False
    if send and not dry_run:
        subject = (
            f"[DevOps] Weekly Reliability Report — "
            f"{data.window.start.strftime('%b %d')} to {data.window.end.strftime('%b %d, %Y')}"
        )
        email_result = send_email(
            subject=subject,
            body_text=markdown,
            body_html=html,
        )
        email_sent = email_result.ok
        if not email_result.ok:
            data.diagnostics.warnings.append(f"Email send failed: {email_result.message}")
            logger.warning("Report email failed: %s", email_result.message)

    return ReportResult(
        ok=True,
        code=ErrorCode.OK,
        message=f"Weekly report generated ({data.diagnostics.projects_checked} projects, {days}-day window)",
        report_markdown=markdown,
        report_html=html,
        period_days=days,
        diagnostics=data.diagnostics,
        email_sent=email_sent,
    )
