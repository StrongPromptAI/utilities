"""Click CLI entry point for the DevOps agent.

Commands:
    devops list-projects     Show configured projects from TOML
    devops status <project>  Railway deployment status
    devops health <project>  Run health check
    devops send-test-email   Verify SMTP config
    devops rollback <proj>   Roll back deployment
    devops validate-deploy   Post-deploy validation pipeline
    devops smoke <project>   Run smoke tests
    devops report            Weekly reliability report

Global options:
    --json       Machine-readable JSON output
    --verbose    Increase log level to DEBUG
"""

import json
import sys

import click

from .config import get_config, get_project
from .errors import (
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_GENERAL,
    EXIT_HEALTH_FAILED,
    EXIT_OK,
    EXIT_PROVIDER_UNAVAILABLE,
    ConfigError,
    ErrorCode,
)
from .health import check_health
from .logging_setup import setup_logging
from .models import OperationResult
from .notify import send_email
from .railway import discover_projects, get_deployment_status
from .regression import run_regression
from .report import run_report
from .second_opinion import review_file
from .rollback import rollback_with_notification, validate_deploy
from .smoke import run_smoke_tests

# Map error codes to CLI exit codes
_EXIT_MAP = {
    ErrorCode.OK: EXIT_OK,
    ErrorCode.CONFIG_ERROR: EXIT_CONFIG,
    ErrorCode.AUTH_ERROR: EXIT_AUTH,
    ErrorCode.TIMEOUT: EXIT_PROVIDER_UNAVAILABLE,
    ErrorCode.PROVIDER_DOWN: EXIT_PROVIDER_UNAVAILABLE,
    ErrorCode.NETWORK_ERROR: EXIT_PROVIDER_UNAVAILABLE,
    ErrorCode.GRAPHQL_ERROR: EXIT_PROVIDER_UNAVAILABLE,
    ErrorCode.SMTP_ERROR: EXIT_PROVIDER_UNAVAILABLE,
    ErrorCode.APP_UNHEALTHY: EXIT_HEALTH_FAILED,
    ErrorCode.ROLLBACK_ERROR: EXIT_GENERAL,
    ErrorCode.NO_ROLLBACK_TARGET: EXIT_GENERAL,
    ErrorCode.LLM_ERROR: EXIT_PROVIDER_UNAVAILABLE,
}


def _output(result: OperationResult, *, as_json: bool) -> None:
    """Print result to stdout in human or JSON format."""
    if as_json:
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(result.to_display())
        if result.details:
            for key, val in result.details.items():
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            parts = [f"{k}={v}" for k, v in item.items()]
                            click.echo(f"  {', '.join(parts)}")
                        else:
                            click.echo(f"  {item}")
                else:
                    click.echo(f"  {key}: {val}")


def _exit_code(result: OperationResult) -> int:
    return _EXIT_MAP.get(result.code, EXIT_GENERAL)


@click.group()
@click.option("--json", "json_output", is_flag=True, help="Machine-readable JSON output")
@click.option("--verbose", is_flag=True, help="Debug-level logging")
@click.pass_context
def cli(ctx: click.Context, json_output: bool, verbose: bool) -> None:
    """DevOps Agent — human-governed deployment operations."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    setup_logging(verbose=verbose)


@cli.command("list-projects")
@click.pass_context
def list_projects_cmd(ctx: click.Context) -> None:
    """Show configured projects from TOML."""
    as_json = ctx.obj["json"]
    try:
        config = get_config()
    except ConfigError as e:
        result = OperationResult(ok=False, code=ErrorCode.CONFIG_ERROR, message=str(e))
        _output(result, as_json=as_json)
        sys.exit(_exit_code(result))

    projects_list = []
    for name, proj in sorted(config.projects.items()):
        projects_list.append({
            "name": name,
            "display_name": proj.display_name,
            "repo": proj.repo,
            "tier": proj.tier,
            "has_staging": proj.has_staging,
            "railway_project_id": proj.railway_project_id,
        })

    result = OperationResult(
        ok=True,
        code=ErrorCode.OK,
        message=f"{len(projects_list)} projects configured",
        details={"projects": projects_list},
    )
    _output(result, as_json=as_json)
    sys.exit(EXIT_OK)


@cli.command("status")
@click.argument("project")
@click.pass_context
def status_cmd(ctx: click.Context, project: str) -> None:
    """Show Railway deployment status for a project."""
    as_json = ctx.obj["json"]
    result = get_deployment_status(project)
    _output(result, as_json=as_json)
    sys.exit(_exit_code(result))


@cli.command("health")
@click.argument("project")
@click.pass_context
def health_cmd(ctx: click.Context, project: str) -> None:
    """Run health check against a project's configured URL."""
    as_json = ctx.obj["json"]
    result = check_health(project)
    _output(result, as_json=as_json)
    sys.exit(_exit_code(result))


@cli.command("send-test-email")
@click.option("--to", default=None, help="Recipient (defaults to DEVOPS_NOTIFY_TO or FASTMAIL_FROM)")
@click.pass_context
def send_test_email_cmd(ctx: click.Context, to: str | None) -> None:
    """Send a test email to verify SMTP config."""
    as_json = ctx.obj["json"]
    result = send_email(
        to=to,
        subject="[DevOps Agent] Test Email",
        body_text="This is a test email from the DevOps Agent.\n\nIf you received this, SMTP is configured correctly.",
        body_html="<h3>DevOps Agent Test</h3><p>SMTP is configured correctly.</p>",
    )
    _output(result, as_json=as_json)
    sys.exit(_exit_code(result))


@cli.command("discover")
@click.pass_context
def discover_cmd(ctx: click.Context) -> None:
    """Discover all projects in the Railway workspace via GraphQL."""
    as_json = ctx.obj["json"]
    result = discover_projects()
    _output(result, as_json=as_json)
    sys.exit(_exit_code(result))


@cli.command("rollback")
@click.argument("project")
@click.option("--reason", default="Manual rollback", help="Reason for rollback")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing")
@click.option("--force", is_flag=True, help="Override 7-day rollback age limit")
@click.option("--use-llm", is_flag=True, help="Use OpenRouter for email body drafting")
@click.pass_context
def rollback_cmd(
    ctx: click.Context,
    project: str,
    reason: str,
    dry_run: bool,
    force: bool,
    use_llm: bool,
) -> None:
    """Roll back a project's deployment to the previous successful version."""
    as_json = ctx.obj["json"]
    result = rollback_with_notification(
        project,
        reason,
        use_llm=use_llm,
        dry_run=dry_run,
        force=force,
    )
    _output(result, as_json=as_json)
    sys.exit(_exit_code(result))


@cli.command("validate-deploy")
@click.argument("project")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing")
@click.option("--use-llm", is_flag=True, help="Use OpenRouter for email body drafting")
@click.option(
    "--smoke-mode",
    type=click.Choice(["observe", "enforce"]),
    default="observe",
    help="observe: log smoke failures without rollback; enforce: treat as deploy failure",
)
@click.pass_context
def validate_deploy_cmd(
    ctx: click.Context,
    project: str,
    dry_run: bool,
    use_llm: bool,
    smoke_mode: str,
) -> None:
    """Validate a deployment: health check, smoke tests, rollback on failure, notify on success."""
    as_json = ctx.obj["json"]
    result = validate_deploy(
        project,
        use_llm=use_llm,
        dry_run=dry_run,
        smoke_mode=smoke_mode,
    )
    _output(result, as_json=as_json)
    sys.exit(_exit_code(result))


@cli.command("smoke")
@click.argument("project")
@click.pass_context
def smoke_cmd(ctx: click.Context, project: str) -> None:
    """Run smoke tests for a project's configured endpoints."""
    as_json = ctx.obj["json"]
    result = run_smoke_tests(project)
    _output(result, as_json=as_json)
    sys.exit(_exit_code(result))


@cli.command("regress")
@click.argument("project")
@click.option(
    "--env",
    type=click.Choice(["staging", "production"]),
    default="production",
    help="Environment to test (staging runs all tests; production is read-only)",
)
@click.pass_context
def regress_cmd(ctx: click.Context, project: str, env: str) -> None:
    """Run regression and security tests for a project."""
    as_json = ctx.obj["json"]
    result = run_regression(project, env=env)
    if as_json:
        _output(result, as_json=True)
    else:
        for t in result.test_results:
            status = "PASS" if t.passed else "FAIL"
            if t.error and t.error.startswith("SKIP"):
                status = "SKIP"
            tag = " [staging-only]" if t.staging_only else ""
            err = f"  {t.error}" if t.error and not t.error.startswith("SKIP") else ""
            latency = f" ({t.latency_ms:.0f}ms)" if t.latency_ms else ""
            click.echo(f"  [{status}] {t.name}{tag}{latency}{err}")
        click.echo()
        click.echo(result.to_display())
    sys.exit(_exit_code(result))


@cli.command("report")
@click.option("--days", default=7, help="Reporting period in days (default 7)")
@click.option("--send", is_flag=True, help="Send report via email")
@click.option("--dry-run", is_flag=True, help="Generate and display without sending")
@click.option("--use-llm", is_flag=True, help="Add LLM-generated reliability insights")
@click.option("--no-live-checks", is_flag=True, help="Skip health/smoke checks (safe mode)")
@click.pass_context
def report_cmd(
    ctx: click.Context,
    days: int,
    send: bool,
    dry_run: bool,
    use_llm: bool,
    no_live_checks: bool,
) -> None:
    """Generate weekly reliability report across all projects."""
    as_json = ctx.obj["json"]
    result = run_report(
        days=days,
        use_llm=use_llm,
        send=send,
        dry_run=dry_run,
        no_live_checks=no_live_checks,
    )
    if as_json:
        _output(result, as_json=True)
    else:
        # Display the markdown report to stdout
        if result.report_markdown:
            click.echo(result.report_markdown)
            click.echo()
        click.echo(result.to_display())
        if result.email_sent:
            click.echo("  Email sent successfully.")
    sys.exit(_exit_code(result))


@cli.command("second-opinion")
@click.argument("file_path")
@click.option("--model", default="openai/gpt-5.3-codex", help="OpenRouter model for review")
@click.option(
    "--purpose",
    type=click.Choice(["plan", "security", "architecture", "general"]),
    default="plan",
    help="Review type (selects reviewer persona)",
)
@click.option("--context", default="", help="Additional context string for the reviewer")
@click.option("--context-file", default=None, help="File with additional context to prepend")
@click.option("--max-tokens", default=3000, help="Max response length")
@click.pass_context
def second_opinion_cmd(
    ctx: click.Context,
    file_path: str,
    model: str,
    purpose: str,
    context: str,
    context_file: str | None,
    max_tokens: int,
) -> None:
    """Send a document to another LLM for critical review."""
    as_json = ctx.obj["json"]

    # Build context from string and/or file
    ctx_parts = []
    if context:
        ctx_parts.append(context)
    if context_file:
        from pathlib import Path
        cf = Path(context_file).expanduser()
        if cf.exists():
            ctx_parts.append(cf.read_text())
        else:
            click.echo(f"Context file not found: {context_file}", err=True)
            sys.exit(EXIT_CONFIG)
    full_context = "\n\n".join(ctx_parts)

    result = review_file(
        file_path,
        context=full_context,
        purpose=purpose,
        model=model,
        max_tokens=max_tokens,
    )

    if as_json:
        _output(result, as_json=True)
    else:
        if result.ok and result.response_text:
            click.echo(result.response_text)
            click.echo(f"\n--- {result.model} | cost: ${result.cost:.4f}" if result.cost else f"\n--- {result.model}")
        else:
            click.echo(result.to_display())
    sys.exit(_exit_code(result))
