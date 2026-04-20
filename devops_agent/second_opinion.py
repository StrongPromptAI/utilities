"""Second-opinion review of plans, designs, and code via OpenRouter.

Sends a document to an external LLM for critical review. The reviewer
model is chosen to be different from the model doing the work — that's
the point. A Claude session sends to GPT for review, or vice versa.

Default model: openai/gpt-5.3-codex (strong at architecture review).
Override with --model for different reviewer profiles.
"""

import logging
import sys
import time
from pathlib import Path

from .analyze import call_openrouter
from .errors import ErrorCode
from .models import AnalyzeResult

logger = logging.getLogger(__name__)

# Reviewer personas keyed by purpose
_REVIEW_PROMPTS = {
    "plan": (
        "You are a senior engineer reviewing a plan before implementation. "
        "Your job:\n"
        "1. Identify design gaps, missed edge cases, or architectural concerns\n"
        "2. Suggest concrete improvements (not vague advice)\n"
        "3. Flag scope that should be cut or deferred\n"
        "4. Confirm what looks solid\n\n"
        "Be direct. Don't repeat the plan back. Add value."
    ),
    "security": (
        "You are a senior security engineer reviewing code or configuration "
        "for vulnerabilities. Focus on:\n"
        "1. Auth/session weaknesses\n"
        "2. Input validation gaps\n"
        "3. Information leakage\n"
        "4. Misconfigurations that create attack surface\n\n"
        "Be specific — name the file/function/config and the exact risk. "
        "Rate findings as CRITICAL / HIGH / MEDIUM / LOW."
    ),
    "architecture": (
        "You are a principal engineer reviewing a system design. Focus on:\n"
        "1. Does the architecture match the stated requirements?\n"
        "2. Where will this break at 10x scale?\n"
        "3. What coupling or complexity will cause pain later?\n"
        "4. What's the simplest version that could work?\n\n"
        "Be opinionated. If something is over-engineered, say so."
    ),
    "general": (
        "You are a sharp technical reviewer. Read the document carefully, "
        "then provide:\n"
        "1. What's strong and should proceed as-is\n"
        "2. What needs improvement (with specific suggestions)\n"
        "3. What's missing that should be addressed\n"
        "4. Any risks or concerns\n\n"
        "Be concise and direct."
    ),
}

DEFAULT_MODEL = "openai/gpt-5.3-codex"


def get_second_opinion(
    content: str,
    *,
    context: str = "",
    purpose: str = "plan",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 3000,
) -> AnalyzeResult:
    """Send content to an external LLM for critical review.

    Args:
        content: The document text to review.
        context: Optional additional context (project background, etc.).
        purpose: Review type — 'plan', 'security', 'architecture', 'general'.
        model: OpenRouter model ID for the reviewer.
        max_tokens: Max response length.

    Returns:
        AnalyzeResult with the review text.
    """
    system = _REVIEW_PROMPTS.get(purpose, _REVIEW_PROMPTS["general"])

    user_parts = []
    if context:
        user_parts.append(f"## Context\n\n{context}\n")
    user_parts.append(f"## Document to Review\n\n{content}")
    user_message = "\n".join(user_parts)

    return call_openrouter(
        system_prompt=system,
        user_message=user_message,
        model=model,
        temperature=0.3,
        max_tokens=max_tokens,
        timeout=120.0,
    )


def review_file(
    file_path: str,
    *,
    context: str = "",
    purpose: str = "plan",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 3000,
) -> AnalyzeResult:
    """Read a file and send it for second-opinion review.

    Args:
        file_path: Path to the document (markdown, code, config, etc.).
        context: Optional additional context.
        purpose: Review type.
        model: OpenRouter model ID.
        max_tokens: Max response length.
    """
    path = Path(file_path).expanduser()
    if not path.exists():
        return AnalyzeResult(
            ok=False,
            code=ErrorCode.CONFIG_ERROR,
            message=f"File not found: {path}",
            model=model,
        )

    content = path.read_text()
    if not content.strip():
        return AnalyzeResult(
            ok=False,
            code=ErrorCode.CONFIG_ERROR,
            message=f"File is empty: {path}",
            model=model,
        )

    return get_second_opinion(
        content,
        context=context,
        purpose=purpose,
        model=model,
        max_tokens=max_tokens,
    )
