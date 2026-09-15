# src/pipeline/registry.py — v1
"""Agent registry — dynamic loading and management of pipeline agents.

Loads agent classes from AGENT_REGISTRY config, validates dependencies,
and provides lookup. Supports runtime enable/disable of optional agents.

See spec §25.4 for full documentation.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from ayextractor.config.agents import AGENT_REGISTRY
from ayextractor.pipeline.plugin_kit.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class RegistryError(Exception):
    """Raised when agent loading or validation fails."""


class AgentRegistry:
    """Registry of all available pipeline agents.

    Agents are loaded from the AGENT_REGISTRY config list and
    instantiated lazily. The registry validates that all declared
    dependencies are satisfiable.
    """

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._disabled: set[str] = set()

    @property
    def agents(self) -> dict[str, BaseAgent]:
        """Return mapping of agent_name -> agent instance."""
        return dict(self._agents)

    @property
    def agent_names(self) -> list[str]:
        """Return sorted list of registered agent names."""
        return sorted(self._agents.keys())

    def load_all(self, disabled: set[str] | None = None) -> None:
        """Load all agents from AGENT_REGISTRY config.

        Args:
            disabled: Set of agent names to skip loading.
        """
        self._disabled = disabled or set()

        for class_path in AGENT_REGISTRY:
            try:
                agent = _import_agent(class_path)
                if agent.name in self._disabled:
                    logger.info("Skipping disabled agent: %s", agent.name)
                    continue
                self._agents[agent.name] = agent
                logger.debug("Loaded agent: %s v%s", agent.name, agent.version)
            except Exception as exc:
                logger.warning("Failed to load agent %s: %s", class_path, exc)

        logger.info(
            "Registry loaded %d agents (%d disabled)",
            len(self._agents),
            len(self._disabled),
        )

    def register(self, agent: BaseAgent) -> None:
        """Manually register an agent instance."""
        if agent.name in self._agents:
            logger.warning("Overwriting existing agent: %s", agent.name)
        self._agents[agent.name] = agent

    def get(self, name: str) -> BaseAgent | None:
        """Get agent by name, or None if not registered."""
        return self._agents.get(name)

    def get_or_raise(self, name: str) -> BaseAgent:
        """Get agent by name, raise if not found."""
        agent = self._agents.get(name)
        if agent is None:
            raise RegistryError(f"Agent '{name}' not found in registry")
        return agent

    def validate_dependencies(self) -> list[str]:
        """Validate that all agent dependencies are satisfiable.

        Returns:
            List of error messages (empty if valid).
        """
        errors: list[str] = []
        for name, agent in self._agents.items():
            for dep in agent.dependencies:
                if dep not in self._agents:
                    errors.append(
                        f"Agent '{name}' depends on '{dep}' which is not registered"
                    )
        return errors

    def get_dependency_map(self) -> dict[str, list[str]]:
        """Return agent_name -> list of dependency names."""
        return {
            name: list(agent.dependencies)
            for name, agent in self._agents.items()
        }


def _import_agent(class_path: str) -> BaseAgent:
    """Import and instantiate an agent from a dotted class path.

    Args:
        class_path: e.g. 'ayextractor.pipeline.agents.summarizer.SummarizerAgent'

    Returns:
        Instantiated BaseAgent subclass.
    """
    parts = class_path.rsplit(".", 1)
    if len(parts) != 2:
        raise RegistryError(f"Invalid class path: {class_path}")
    module_path, class_name = parts

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise RegistryError(f"Cannot import module {module_path}: {exc}") from exc

    cls = getattr(module, class_name, None)
    if cls is None:
        raise RegistryError(f"Class {class_name} not found in {module_path}")

    if not isinstance(cls, type) or not issubclass(cls, BaseAgent):
        raise RegistryError(f"{class_path} is not a BaseAgent subclass")

    return cls()
