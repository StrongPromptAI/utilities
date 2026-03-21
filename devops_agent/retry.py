"""Shared retry logic with exponential backoff and jitter."""

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    operation: str = "",
) -> T:
    """Execute fn with exponential backoff + jitter on failure.

    Args:
        fn: Zero-arg callable to execute.
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay: Base delay in seconds (doubles each retry).
        retry_on: Exception types that trigger a retry.
        operation: Human-readable name for logging.

    Returns:
        The return value of fn on success.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retry_on as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
            logger.warning(
                "Retry %d/%d for %s: %s (waiting %.1fs)",
                attempt + 1,
                max_retries,
                operation or "operation",
                exc,
                delay,
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]
