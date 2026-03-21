"""Railway GraphQL client.

All Railway interaction uses the GraphQL API per railway-patterns.md.
No CLI, no plugin. The only exception is `railway up` for static sites.
"""

import logging
import time

import httpx

from .config import get_config, get_project
from .errors import ErrorCode, RailwayAPIError
from .models import RailwayResult
from .retry import retry

logger = logging.getLogger(__name__)

RAILWAY_GRAPHQL = "https://backboard.railway.com/graphql/v2"

# Cloudflare blocks default Python user-agents on Railway's API
_HEADERS_BASE = {"Content-Type": "application/json", "User-Agent": "curl/8.0"}


def _gql(query: str, variables: dict | None = None, token: str | None = None) -> dict:
    """Execute a GraphQL query against Railway. Returns the data dict.

    Inspects both HTTP status and GraphQL errors field.
    Uses GraphQL variables when provided (safer than string interpolation).
    Raises RailwayAPIError on failure.
    """
    if token is None:
        token = get_config().railway_token

    headers = {**_HEADERS_BASE, "Authorization": f"Bearer {token}"}
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    def _do_request() -> httpx.Response:
        return httpx.post(
            RAILWAY_GRAPHQL,
            json=payload,
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
        )

    try:
        resp = retry(
            _do_request,
            max_retries=2,
            base_delay=1.0,
            retry_on=(httpx.TimeoutException, httpx.ConnectError),
            operation="railway_graphql",
        )
    except httpx.TimeoutException as e:
        raise RailwayAPIError(f"Railway API timeout: {e}", ErrorCode.TIMEOUT)
    except httpx.ConnectError as e:
        raise RailwayAPIError(
            f"Railway API unreachable: {e}", ErrorCode.PROVIDER_DOWN
        )

    if resp.status_code == 401:
        raise RailwayAPIError("Railway API: unauthorized (check token)", ErrorCode.AUTH_ERROR)
    if resp.status_code != 200:
        raise RailwayAPIError(
            f"Railway API returned HTTP {resp.status_code}: {resp.text[:200]}",
            ErrorCode.PROVIDER_DOWN,
        )

    body = resp.json()
    if "errors" in body:
        msg = body["errors"][0].get("message", "Unknown GraphQL error")
        raise RailwayAPIError(f"Railway GraphQL error: {msg}")

    return body.get("data", {})


def discover_projects() -> RailwayResult:
    """List all projects in the workspace."""
    t0 = time.monotonic()
    try:
        data = _gql(
            "{ projects { edges { node { id name "
            "services { edges { node { id name } } } "
            "environments { edges { node { id name } } } "
            "} } } }"
        )
        projects = []
        for edge in data.get("projects", {}).get("edges", []):
            node = edge["node"]
            projects.append({
                "id": node["id"],
                "name": node["name"],
                "services": [
                    {"id": s["node"]["id"], "name": s["node"]["name"]}
                    for s in node.get("services", {}).get("edges", [])
                ],
                "environments": [
                    {"id": e["node"]["id"], "name": e["node"]["name"]}
                    for e in node.get("environments", {}).get("edges", [])
                ],
            })
        return RailwayResult(
            ok=True,
            code=ErrorCode.OK,
            message=f"Found {len(projects)} projects",
            details={"projects": projects},
        )
    except RailwayAPIError as e:
        return RailwayResult(ok=False, code=e.code, message=str(e))
    finally:
        logger.info(
            "discover_projects duration_ms=%.0f", (time.monotonic() - t0) * 1000
        )


def get_deployments(
    project_name: str,
    service_id: str | None = None,
    environment_id: str | None = None,
    limit: int = 5,
) -> RailwayResult:
    """Get recent deployments for a project's service.

    Returns list of deployments with id, status, createdAt.
    Uses the project's health_service_id and production_env_id by default.
    """
    t0 = time.monotonic()
    try:
        proj = get_project(project_name)
    except Exception as e:
        return RailwayResult(ok=False, code=ErrorCode.CONFIG_ERROR, message=str(e))

    sid = service_id or proj.health_service_id
    eid = environment_id or proj.production_env_id
    if not sid:
        return RailwayResult(
            ok=False,
            code=ErrorCode.CONFIG_ERROR,
            message=f"No service ID configured for {project_name}",
            project=project_name,
        )

    query = """
    query Deployments($projectId: String!, $environmentId: String!, $serviceId: String!, $limit: Int!) {
        deployments(first: $limit, input: {
            projectId: $projectId,
            environmentId: $environmentId,
            serviceId: $serviceId
        }) {
            edges { node { id status createdAt } }
        }
    }
    """
    variables = {
        "projectId": proj.railway_project_id,
        "environmentId": eid,
        "serviceId": sid,
        "limit": limit,
    }

    try:
        data = _gql(query, variables)
        deployments = []
        for edge in data.get("deployments", {}).get("edges", []):
            node = edge["node"]
            deployments.append({
                "id": node["id"],
                "status": node["status"],
                "created_at": node["createdAt"],
            })

        return RailwayResult(
            ok=True,
            code=ErrorCode.OK,
            message=f"{proj.display_name}: {len(deployments)} deployments found",
            project=project_name,
            service=sid,
            environment="production",
            details={"deployments": deployments},
        )
    except RailwayAPIError as e:
        return RailwayResult(
            ok=False, code=e.code, message=str(e), project=project_name
        )
    finally:
        logger.info(
            "get_deployments project=%s limit=%d duration_ms=%.0f",
            project_name,
            limit,
            (time.monotonic() - t0) * 1000,
        )


def execute_rollback_mutation(deployment_id: str) -> RailwayResult:
    """Execute deploymentRollback GraphQL mutation.

    Uses GraphQL variables (not string interpolation) per Codex review.
    The deployment_id is the target deployment to roll back TO.
    """
    t0 = time.monotonic()
    query = """
    mutation Rollback($id: String!) {
        deploymentRollback(id: $id)
    }
    """

    try:
        data = _gql(query, {"id": deployment_id})
        success = data.get("deploymentRollback", False)
        return RailwayResult(
            ok=success,
            code=ErrorCode.OK if success else ErrorCode.ROLLBACK_ERROR,
            message=f"Rollback {'accepted' if success else 'rejected'} by Railway",
            details={
                "target_deployment_id": deployment_id,
                "railway_accepted": success,
            },
        )
    except RailwayAPIError as e:
        return RailwayResult(
            ok=False,
            code=e.code,
            message=f"Rollback mutation failed: {e}",
        )
    finally:
        logger.info(
            "execute_rollback_mutation deployment_id=%s duration_ms=%.0f",
            deployment_id,
            (time.monotonic() - t0) * 1000,
        )


def get_deployment_status(project_name: str) -> RailwayResult:
    """Get current deployment status for a project's primary service.

    Queries the most recent 3 deployments for the project's
    health_service_id in the production environment.
    """
    t0 = time.monotonic()
    try:
        proj = get_project(project_name)
    except Exception as e:
        return RailwayResult(ok=False, code=ErrorCode.CONFIG_ERROR, message=str(e))

    if not proj.health_service_id:
        return RailwayResult(
            ok=False,
            code=ErrorCode.CONFIG_ERROR,
            message=f"No health_service_id configured for {project_name}",
            project=project_name,
        )

    query = """
    query DeploymentStatus($projectId: String!, $environmentId: String!, $serviceId: String!) {
        deployments(first: 3, input: {
            projectId: $projectId,
            environmentId: $environmentId,
            serviceId: $serviceId
        }) {
            edges { node { id status createdAt } }
        }
    }
    """
    variables = {
        "projectId": proj.railway_project_id,
        "environmentId": proj.production_env_id,
        "serviceId": proj.health_service_id,
    }

    try:
        data = _gql(query, variables)
        deployments = []
        for edge in data.get("deployments", {}).get("edges", []):
            node = edge["node"]
            deployments.append({
                "id": node["id"],
                "status": node["status"],
                "created_at": node["createdAt"],
            })

        current = deployments[0] if deployments else {}
        status_str = current.get("status", "UNKNOWN")
        return RailwayResult(
            ok=True,
            code=ErrorCode.OK,
            message=f"{proj.display_name}: {status_str}",
            project=project_name,
            service=proj.health_service_id,
            environment="production",
            details={"deployments": deployments, "current_status": status_str},
        )
    except RailwayAPIError as e:
        return RailwayResult(
            ok=False, code=e.code, message=str(e), project=project_name
        )
    finally:
        logger.info(
            "get_deployment_status project=%s duration_ms=%.0f",
            project_name,
            (time.monotonic() - t0) * 1000,
        )
