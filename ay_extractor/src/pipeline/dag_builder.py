# src/pipeline/dag_builder.py — v1
"""DAG builder — build execution graph from agent dependencies.

Produces a topologically sorted execution plan. Detects cycles
and validates that all dependencies are resolvable.

See spec §25.5 for full documentation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class DAGError(Exception):
    """Raised when DAG construction fails (cycle, missing dep)."""


@dataclass
class ExecutionPlan:
    """Ordered execution plan for pipeline agents.

    stages is a list of "levels" — agents within the same level can
    run concurrently (no mutual dependencies). Levels execute sequentially.
    """

    stages: list[list[str]] = field(default_factory=list)
    total_agents: int = 0

    @property
    def flat_order(self) -> list[str]:
        """Return a flat topological ordering (no concurrency info)."""
        return [agent for stage in self.stages for agent in stage]


def build_dag(dependency_map: dict[str, list[str]]) -> ExecutionPlan:
    """Build an execution DAG from agent dependency declarations.

    Uses Kahn's algorithm for topological sort with level detection.
    Each level contains agents whose dependencies are fully resolved
    by previous levels.

    Args:
        dependency_map: agent_name -> list of dependency agent names.

    Returns:
        ExecutionPlan with staged execution order.

    Raises:
        DAGError: If a cycle is detected or a dependency is missing.
    """
    if not dependency_map:
        return ExecutionPlan()

    # Validate all dependencies exist
    all_agents = set(dependency_map.keys())
    for agent, deps in dependency_map.items():
        for dep in deps:
            if dep not in all_agents:
                raise DAGError(
                    f"Agent '{agent}' depends on '{dep}' which is not registered"
                )

    # Build adjacency and in-degree
    in_degree: dict[str, int] = {a: 0 for a in all_agents}
    dependents: dict[str, list[str]] = {a: [] for a in all_agents}

    for agent, deps in dependency_map.items():
        for dep in deps:
            dependents[dep].append(agent)
            in_degree[agent] += 1

    # Kahn's algorithm with level tracking
    stages: list[list[str]] = []
    queue: list[str] = sorted([a for a, d in in_degree.items() if d == 0])
    processed = 0

    while queue:
        # All agents in current queue have in_degree 0 → same level
        stages.append(sorted(queue))
        next_queue: list[str] = []
        for agent in queue:
            processed += 1
            for dependent in dependents[agent]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_queue.append(dependent)
        queue = sorted(next_queue)

    if processed != len(all_agents):
        remaining = [a for a in all_agents if in_degree[a] > 0]
        raise DAGError(
            f"Cycle detected involving agents: {remaining}"
        )

    plan = ExecutionPlan(stages=stages, total_agents=processed)
    logger.info(
        "DAG built: %d agents in %d stages → %s",
        plan.total_agents,
        len(plan.stages),
        plan.flat_order,
    )
    return plan
