"""OpenRouter API client for LLM-assisted analysis.

Strictly optional — behind --use-llm flag. Never blocks rollback or
notification flows. Hard 8s timeout, no retries for LLM calls.

Used for:
- Drafting human-readable notification email bodies
- (Future) Classifying ambiguous health check failures
"""

import logging
import os
import time

import httpx

from .errors import ErrorCode
from .models import AnalyzeResult

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"
LLM_TIMEOUT = 8.0  # Hard timeout per Codex review


def _get_api_key() -> str:
    """Get OpenRouter API key. Fail-fast on missing."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY env var not set")
    return key


def call_openrouter(
    *,
    system_prompt: str,
    user_message: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_tokens: int = 1000,
) -> AnalyzeResult:
    """Call OpenRouter API with a prompt.

    Hard 8s timeout, no retries. Returns AnalyzeResult with response
    text, model used, and cost. Never sends secrets in prompts.
    """
    t0 = time.monotonic()

    try:
        api_key = _get_api_key()
    except ValueError as e:
        return AnalyzeResult(
            ok=False,
            code=ErrorCode.CONFIG_ERROR,
            message=str(e),
            model=model,
        )

    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=LLM_TIMEOUT,
        )
    except httpx.TimeoutException:
        return AnalyzeResult(
            ok=False,
            code=ErrorCode.TIMEOUT,
            message=f"OpenRouter timeout after {LLM_TIMEOUT}s",
            model=model,
        )
    except httpx.ConnectError as e:
        return AnalyzeResult(
            ok=False,
            code=ErrorCode.PROVIDER_DOWN,
            message=f"OpenRouter unreachable: {e}",
            model=model,
        )

    latency_ms = (time.monotonic() - t0) * 1000

    if resp.status_code == 401:
        return AnalyzeResult(
            ok=False,
            code=ErrorCode.AUTH_ERROR,
            message="OpenRouter: unauthorized (check API key)",
            model=model,
        )
    if resp.status_code != 200:
        return AnalyzeResult(
            ok=False,
            code=ErrorCode.LLM_ERROR,
            message=f"OpenRouter HTTP {resp.status_code}: {resp.text[:200]}",
            model=model,
        )

    try:
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        cost = usage.get("cost")
    except (KeyError, IndexError, ValueError) as e:
        return AnalyzeResult(
            ok=False,
            code=ErrorCode.LLM_ERROR,
            message=f"OpenRouter response parse error: {e}",
            model=model,
        )

    logger.info(
        "call_openrouter model=%s latency_ms=%.0f tokens_in=%s tokens_out=%s",
        model,
        latency_ms,
        usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
    )

    return AnalyzeResult(
        ok=True,
        code=ErrorCode.OK,
        message="LLM analysis complete",
        model=model,
        response_text=content,
        cost=cost,
    )


def draft_rollback_email_llm(context: dict) -> tuple[str, str] | None:
    """LLM-enhanced rollback email body.

    Returns (subject, body_html) or None on any failure.
    Deterministic template in templates.py is the fallback.
    """
    system = (
        "You are a DevOps notification writer. Write a clear, concise rollback "
        "notification email body in HTML. Include all the structured data provided. "
        "Use a professional tone. Keep it under 500 words."
    )
    user = (
        f"Write a rollback notification email for this event:\n\n"
        f"Project: {context.get('project', 'unknown')}\n"
        f"Service: {context.get('service', 'unknown')}\n"
        f"Environment: {context.get('environment', 'production')}\n"
        f"Failed Deployment: {context.get('failed_deployment_id', 'unknown')}\n"
        f"Rolled Back To: {context.get('rollback_deployment_id', 'unknown')}\n"
        f"Rollback Status: {context.get('rollback_status', 'unknown')}\n"
        f"Reason: {context.get('reason', 'unknown')}\n"
        f"Health Evidence: {context.get('health_evidence', 'N/A')}\n"
    )

    result = call_openrouter(system_prompt=system, user_message=user)
    if not result.ok:
        logger.warning("LLM draft failed, using deterministic template: %s", result.message)
        return None

    subject = f"[DevOps] Rollback: {context.get('project', 'unknown')}/{context.get('service', 'unknown')}"
    return subject, result.response_text


def draft_deploy_email_llm(context: dict) -> tuple[str, str] | None:
    """LLM-enhanced deploy success email body.

    Returns (subject, body_html) or None on any failure.
    """
    system = (
        "You are a DevOps notification writer. Write a clear, concise deployment "
        "success notification email body in HTML. Include all the structured data "
        "provided. Use a professional, reassuring tone. Keep it under 300 words."
    )
    user = (
        f"Write a deployment success notification for:\n\n"
        f"Project: {context.get('project', 'unknown')}\n"
        f"Service: {context.get('service', 'unknown')}\n"
        f"Environment: {context.get('environment', 'production')}\n"
        f"Deployment ID: {context.get('deployment_id', 'unknown')}\n"
        f"Health Check: {context.get('health_result', 'N/A')}\n"
    )

    result = call_openrouter(system_prompt=system, user_message=user)
    if not result.ok:
        logger.warning("LLM draft failed, using deterministic template: %s", result.message)
        return None

    subject = f"[DevOps] Deploy OK: {context.get('project', 'unknown')}/{context.get('service', 'unknown')}"
    return subject, result.response_text
