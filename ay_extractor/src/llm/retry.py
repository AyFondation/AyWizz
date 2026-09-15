# src/llm/retry.py — v1
"""Per-agent retry policy with exponential backoff.

See spec §16.1 for retry strategy per error type.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class LLMRetryExhausted(Exception):
    """All retries exhausted for an LLM call."""

    def __init__(self, agent: str, error_type: str, attempts: int, last_error: Exception):
        self.agent = agent
        self.error_type = error_type
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Agent '{agent}' failed after {attempts} attempts ({error_type}): {last_error}"
        )


@dataclass(frozen=True)
class RetryConfig:
    """Retry configuration for a specific error type."""

    max_retries: int
    base_delay_s: float
    backoff_factor: float = 2.0
    jitter: bool = True


DEFAULT_RETRY_CONFIGS: dict[str, RetryConfig] = {
    "rate_limit": RetryConfig(max_retries=3, base_delay_s=2.0),
    "timeout": RetryConfig(max_retries=2, base_delay_s=1.0, backoff_factor=1.0),
    "server_error": RetryConfig(max_retries=3, base_delay_s=5.0),
    "parse_error": RetryConfig(max_retries=2, base_delay_s=1.0, backoff_factor=1.0),
    "token_limit": RetryConfig(max_retries=1, base_delay_s=0.0, backoff_factor=1.0),
}


def classify_error(error: Exception) -> str:
    """Classify an exception into a retry error type."""
    msg = str(error).lower()
    name = type(error).__name__.lower()

    if "429" in msg or "rate" in msg:
        return "rate_limit"
    if "timeout" in name or "timeout" in msg:
        return "timeout"
    if any(c in msg for c in ("500", "502", "503", "504", "server")):
        return "server_error"
    if "json" in msg or "parse" in msg or "decode" in msg:
        return "parse_error"
    if "token" in msg and ("limit" in msg or "exceed" in msg):
        return "token_limit"
    return "unknown"


def _compute_delay(config: RetryConfig, attempt: int) -> float:
    """Compute delay for a given attempt (0-based)."""
    delay = config.base_delay_s * (config.backoff_factor ** attempt)
    if config.jitter:
        delay *= 0.5 + random.random()  # noqa: S311
    return delay


async def with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    agent: str = "unknown",
    retry_configs: dict[str, RetryConfig] | None = None,
    **kwargs: Any,
) -> Any:
    """Execute an async function with retry logic.

    Raises:
        LLMRetryExhausted: If all retries are exhausted.
    """
    configs = retry_configs or DEFAULT_RETRY_CONFIGS
    attempts = 0

    while True:
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            error_type = classify_error(e)
            attempts += 1
            config = configs.get(error_type)

            if config is None or attempts > config.max_retries:
                raise LLMRetryExhausted(agent, error_type, attempts, e) from e

            delay = _compute_delay(config, attempts - 1)
            logger.warning(
                "Agent '%s' — %s (attempt %d/%d), retrying in %.1fs",
                agent, error_type, attempts, config.max_retries, delay,
            )
            await asyncio.sleep(delay)
