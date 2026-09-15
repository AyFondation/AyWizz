# src/pipeline/plugin_kit/models.py — v1
"""Agent plugin models: AgentMetadata, AgentOutput.

See spec §25.2 for full documentation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentMetadata(BaseModel):
    """Metadata about an agent execution, attached to every AgentOutput."""

    agent_name: str
    agent_version: str
    execution_time_ms: int
    llm_calls: int
    tokens_used: int
    prompt_hash: str | None = None


class AgentOutput(BaseModel):
    """Standard return type for all BaseAgent.execute() calls."""

    data: dict[str, Any]
    confidence: float
    metadata: AgentMetadata
    warnings: list[str] = Field(default_factory=list)
