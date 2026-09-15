# src/tracking/call_logger.py — v1
"""LLM call logging — records every call for cost tracking.

Writes LLMCallRecord entries for post-run analysis.
See spec §20.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ayextractor.llm.models import LLMResponse
from ayextractor.tracking.models import LLMCallRecord

logger = logging.getLogger(__name__)


class CallLogger:
    """Accumulates LLM call records during a pipeline run."""

    def __init__(self) -> None:
        self._records: list[LLMCallRecord] = []

    def record(
        self,
        agent: str,
        step: str,
        response: LLMResponse,
        status: str = "success",
        retry_count: int = 0,
    ) -> LLMCallRecord:
        """Record an LLM call.

        Args:
            agent: Agent name (e.g. "summarizer").
            step: Step identifier (e.g. "refine_chunk_003").
            response: LLM response with token usage.
            status: Call status (success, retry, failed).
            retry_count: Number of retries before this result.

        Returns:
            The recorded LLMCallRecord.
        """
        record = LLMCallRecord(
            call_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            agent=agent,
            step=step,
            provider=response.provider,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            total_tokens=response.input_tokens + response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            latency_ms=response.latency_ms,
            status=status,
            retry_count=retry_count,
        )
        self._records.append(record)
        return record

    @property
    def records(self) -> list[LLMCallRecord]:
        """All recorded calls."""
        return list(self._records)

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed across all calls."""
        return sum(r.total_tokens for r in self._records)

    @property
    def total_calls(self) -> int:
        """Total number of LLM calls."""
        return len(self._records)

    def save(self, path: Path) -> None:
        """Save all records to a JSON Lines file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for record in self._records:
                f.write(json.dumps(record.model_dump(), default=str) + "\n")
