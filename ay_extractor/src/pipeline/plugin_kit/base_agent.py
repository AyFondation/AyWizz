# src/pipeline/plugin_kit/base_agent.py — v1
"""Standard agent interface for pipeline plugins.

See spec §25.3 for full documentation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ayextractor.pipeline.plugin_kit.models import AgentOutput

if TYPE_CHECKING:
    from ayextractor.llm.base_client import BaseLLMClient


class BaseAgent(ABC):
    """Standard interface for all pipeline agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique agent identifier (e.g., 'summarizer', 'fact_checker')."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Agent version (semver)."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this agent does."""

    @property
    @abstractmethod
    def input_schema(self) -> type[BaseModel]:
        """Pydantic model defining required input fields from PipelineState."""

    @property
    @abstractmethod
    def output_schema(self) -> type[BaseModel]:
        """Pydantic model defining output fields added to PipelineState."""

    @property
    def dependencies(self) -> list[str]:
        """List of agent names that must run before this one."""
        return []

    @property
    def prompt_file(self) -> str | None:
        """Path to prompt template file."""
        return None

    @abstractmethod
    async def execute(self, state: object, llm: BaseLLMClient) -> AgentOutput:
        """Execute the agent's logic.

        Args:
            state: PipelineState (typed as object to avoid circular import).
            llm: LLM client for this agent.

        Returns:
            AgentOutput with data, confidence, and metadata.
        """

    def validate_output(self, output: AgentOutput) -> float:
        """Self-validation returning confidence score (0.0-1.0).

        Override for custom validation logic.
        """
        return 1.0
