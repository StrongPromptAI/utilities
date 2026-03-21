"""Click CLI entry point for the DevOps agent.

Commands:
    devops list-projects     Show configured projects from TOML
    devops status <project>  Railway deployment status
    devops health <project>  Run health check
    devops send-test-email   Verify SMTP config

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
from .rollback import rollback_with_notification, validate_deploy

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
@click.pass_context
def validate_deploy_cmd(
    ctx: click.Context,
    project: str,
    dry_run: bool,
    use_llm: bool,
) -> None:
    """Validate a deployment: health check, rollback on failure, notify on success."""
    as_json = ctx.obj["json"]
    result = validate_deploy(
        project,
        use_llm=use_llm,
        dry_run=dry_run,
    )
    _output(result, as_json=as_json)
    sys.exit(_exit_code(result))
