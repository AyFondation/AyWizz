# =============================================================================
# File: traversal.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/absorption/traversal.py
# Description: The impact DAG — 310-SPEC §4.8 (R-310-141 … R-310-144).
#
#              WHY THIS IS ARITHMETIC AND NOT JUDGEMENT. R-310-141 forbids
#              an agent from producing, extending or filtering an impact
#              set, because an agent asked "what does this change affect?"
#              returns a plausible and incomplete answer whose omission is
#              undetectable. So the walk lives here, in a module with no
#              model call, no database and no I/O of its own: it takes a
#              lookup callable and returns a set. Agents qualify impact
#              (R-310-148); they do not discover it.
#
#              TWO PHASES, ON PURPOSE.
#
#              Phase 1 reaches every node once, expanding each at most
#              once, so the work is bounded by the subgraph and the
#              round-trip count is the DAG's depth rather than its node
#              count.
#
#              Phase 2 enumerates the paths over the now-known edge set.
#              It is separate because path enumeration is exponential in
#              the worst case while reachability is not — and because
#              R-310-146's closure gate depends on nodes and links only,
#              never on path count, so a capped path list can never make
#              a ticket wrongly closable.
#
#              A CYCLE IS A DEFECT, NOT A TRAVERSAL CASE (R-310-142). An
#              AQL traversal with `uniqueVertices` would silently dedupe
#              one; a cycle in a coverage graph is a real modelling error
#              — two containers each claiming to answer the other — and
#              swallowing it hides it forever. Kahn's algorithm finds that
#              a cycle exists; a DFS over the residual names a concrete
#              one, because "there is a cycle somewhere" is not actionable.
#
#              IMPACT FLOWS AGAINST THE EDGES. A coverage link is stored
#              `object -> target`: the object answers the target. Impact
#              travels the other way, from the changed target to whatever
#              answers it, and on up the cycle. `ImpactEdge` is therefore
#              already expressed in impact direction, so this module never
#              has to reason about which way round a stored edge was.
#
#              WHY PYDANTIC AND NOT A DATACLASS (v2). These types are the
#              stored and exposed shape of an impact set, not an internal
#              convenience: the ticket persists them and the REST surface
#              returns them. Defining them once here and declaring the
#              contract is what §8.4 asks for; a dataclass here plus a
#              Pydantic twin in `models.py` would be the parallel
#              definition the coherence check exists to catch. The walk's
#              own working state stays plain dicts and sets, so validation
#              runs once per emitted object rather than per visit.
#
# @relation implements:R-310-141
# @relation implements:R-310-142
# @relation implements:R-310-143
# @relation implements:R-310-144
# =============================================================================

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

#: How many distinct paths one node retains before the list is capped.
#: Generous for a realistic cycle (three or four container layers with
#: modest fan-in) and bounded for a pathological one.
DEFAULT_MAX_PATHS = 64


class GraphDefectError(RuntimeError):
    """Raised when the coverage graph cannot be traversed as a DAG.

    Carries a concrete cycle rather than only the fact that one exists,
    because the holder has to go and fix two specific links.
    """

    def __init__(self, cycle: tuple[str, ...]) -> None:
        self.cycle = cycle
        super().__init__(
            "coverage graph contains a cycle and was not traversed: "
            + " -> ".join(cycle)
            + ". Two nodes each claim to answer the other, which makes "
            "'what does this change affect?' unanswerable; fix the links "
            "rather than re-running the traversal (R-310-142)"
        )


class ImpactEdge(BaseModel):
    """One propagation step, expressed in impact direction.

    Attributes:
        source_id: The node whose change propagates.
        node_id: The node impacted by it — the one that answers `source_id`.
        container: The container `node_id` lives in.
        pinned_version: The version of `source_id` the link pinned, which
            is what lets the closure gate of `R-310-146` tell a reviewed
            link from a stale one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    container: str = Field(min_length=1)
    pinned_version: int = Field(default=1, ge=1)


class ImpactPath(BaseModel):
    """One route from the change to an impacted node, seed first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[str, ...]

    @property
    def depth(self) -> int:
        """How many propagation steps this route took."""
        return len(self.nodes) - 1

    def __str__(self) -> str:
        return " -> ".join(self.nodes)


class ImpactNode(BaseModel):
    """One impacted node, with every reason it is in the set.

    `causes` is always complete: it is bounded by the graph, and it is
    what a reviewer needs in order to see the whole reason a node
    changed rather than half of it (`R-310-143`). `paths` is the readable
    provenance and is the only thing a cap can shorten — and when it
    does, `paths_truncated` says so rather than letting the list look
    exhaustive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    container: str = Field(min_length=1)
    causes: tuple[ImpactEdge, ...]
    paths: tuple[ImpactPath, ...]
    paths_truncated: bool = False

    @property
    def depth(self) -> int:
        """The shortest number of steps from the change to this node."""
        return min((path.depth for path in self.paths), default=0)

    @property
    def path_count(self) -> int:
        """How many distinct routes are retained for this node."""
        return len(self.paths)


class ImpactSet(BaseModel):
    """Everything one change reaches.

    The seed is deliberately NOT a member. The set answers "what is
    impacted", and the changed requirement is not impacted by itself —
    it is the change. Including it would ask a reviewer to disposition
    the modification they just received.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed_id: str = Field(min_length=1)
    nodes: tuple[ImpactNode, ...] = ()

    @property
    def node_count(self) -> int:
        """The closure denominator: distinct nodes, never paths.

        Counting paths would inflate the denominator past anything the
        numerator can reach, and a gate that can never be satisfied
        cannot be closed (`R-310-144`).
        """
        return len(self.nodes)

    @property
    def node_ids(self) -> frozenset[str]:
        """The identifiers in the set."""
        return frozenset(node.node_id for node in self.nodes)

    @property
    def is_empty(self) -> bool:
        """True when nothing answers the changed requirement yet."""
        return not self.nodes

    def node(self, node_id: str) -> ImpactNode:
        """Return one node of the set.

        Raises:
            KeyError: When the node is not in the set.
        """
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"{node_id!r} is not in the impact set of {self.seed_id!r}")


#: Returns the edges leading out of a layer, in impact direction. Given
#: the ids of one layer, it yields every `ImpactEdge` whose `source_id`
#: is in it. One call per layer, so the walk costs depth round trips.
LayerLookup = Callable[[frozenset[str]], Awaitable[Sequence[ImpactEdge]]]


async def traverse(
    seed_id: str,
    successors: LayerLookup,
    *,
    max_paths: int = DEFAULT_MAX_PATHS,
) -> ImpactSet:
    """Compute the impact set of a change, deterministically.

    Args:
        seed_id: The changed requirement or object.
        successors: Batched lookup of one layer's outgoing impact edges.
        max_paths: How many paths one node retains before truncation is
            recorded. Does not affect membership or the closure gate.

    Returns:
        Every reachable node exactly once, each carrying all of its
        direct causes and its retained paths.

    Raises:
        GraphDefectError: When the reachable subgraph contains a cycle.
        ValueError: When `max_paths` is not positive.
    """
    if max_paths < 1:
        raise ValueError("max_paths must be positive")

    containers, incoming = await _reach(seed_id, successors)

    cycle = _detect_cycle(seed_id, incoming)
    if cycle is not None:
        raise GraphDefectError(cycle)

    paths = _enumerate_paths(seed_id, incoming, max_paths)
    nodes = tuple(
        ImpactNode(
            node_id=node_id,
            container=containers[node_id],
            causes=tuple(incoming[node_id]),
            paths=paths[node_id][0],
            paths_truncated=paths[node_id][1],
        )
        for node_id in sorted(incoming)
    )
    return ImpactSet(seed_id=seed_id, nodes=nodes)


# ---------------------------------------------------------------------------
# Phase 1 — reachability
# ---------------------------------------------------------------------------


async def _reach(
    seed_id: str, successors: LayerLookup
) -> tuple[dict[str, str], dict[str, list[ImpactEdge]]]:
    """Walk the graph layer by layer, expanding each node at most once.

    Returns:
        The container of each reached node, and its incoming impact edges.
    """
    containers: dict[str, str] = {}
    incoming: dict[str, list[ImpactEdge]] = {}
    recorded: set[ImpactEdge] = set()
    seen = {seed_id}
    frontier = frozenset({seed_id})

    while frontier:
        following: set[str] = set()
        for edge in await successors(frontier):
            # A lookup that over-returns must not be able to grow the set
            # beyond what the graph says: R-310-141 is only a guarantee if
            # the walk is the authority on its own shape.
            if edge.source_id not in frontier or edge in recorded:
                continue
            recorded.add(edge)
            incoming.setdefault(edge.node_id, []).append(edge)
            containers.setdefault(edge.node_id, edge.container)
            if edge.node_id not in seen:
                seen.add(edge.node_id)
                following.add(edge.node_id)
        frontier = frozenset(following)

    for edges in incoming.values():
        edges.sort(key=lambda edge: (edge.source_id, edge.container))
    return containers, incoming


# ---------------------------------------------------------------------------
# Phase 2a — the cycle check (R-310-142)
# ---------------------------------------------------------------------------


def _detect_cycle(
    seed_id: str, incoming: dict[str, list[ImpactEdge]]
) -> tuple[str, ...] | None:
    """Return a concrete cycle in the reached subgraph, or None.

    Kahn's algorithm establishes whether one exists; `_find_cycle` names
    one, because a reviewer told only that "a cycle exists" has nothing
    to act on.
    """
    nodes = {seed_id} | set(incoming)
    outgoing: dict[str, list[str]] = {node: [] for node in nodes}
    indegree: dict[str, int] = dict.fromkeys(nodes, 0)
    for node, edges in incoming.items():
        for edge in edges:
            outgoing.setdefault(edge.source_id, []).append(node)
            indegree[node] += 1

    queue = [node for node in sorted(nodes) if indegree[node] == 0]
    settled = 0
    while queue:
        node = queue.pop()
        settled += 1
        for successor in outgoing[node]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)

    if settled == len(nodes):
        return None
    residual = {node for node in nodes if indegree[node] > 0}
    return _find_cycle(residual, outgoing)


def _find_cycle(residual: set[str], outgoing: dict[str, list[str]]) -> tuple[str, ...]:
    """Return one cycle from the nodes Kahn's algorithm could not settle."""
    visited: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()

    def walk(node: str) -> tuple[str, ...] | None:
        visited.add(node)
        stack.append(node)
        on_stack.add(node)
        for successor in outgoing[node]:
            if successor not in residual:
                continue
            if successor in on_stack:
                start = stack.index(successor)
                return (*stack[start:], successor)
            if successor not in visited:
                found = walk(successor)
                if found is not None:
                    return found
        stack.pop()
        on_stack.discard(node)
        return None

    for node in sorted(residual):
        if node not in visited:
            found = walk(node)
            if found is not None:
                return found
    # Unreachable: Kahn leaves a node unsettled only when it sits on or
    # behind a cycle, so the DFS above always finds one.
    return tuple(sorted(residual))  # pragma: no cover


# ---------------------------------------------------------------------------
# Phase 2b — the paths (R-310-143)
# ---------------------------------------------------------------------------


def _enumerate_paths(
    seed_id: str, incoming: dict[str, list[ImpactEdge]], max_paths: int
) -> dict[str, tuple[tuple[ImpactPath, ...], bool]]:
    """Enumerate every retained route to each node, memoised.

    Memoised so the cost is the size of the output rather than the number
    of walks, and capped so a pathological fan-in cannot exhaust memory.
    Truncation propagates downstream: a node whose ancestor was truncated
    does not get to claim a complete path list.
    """
    memo: dict[str, tuple[tuple[ImpactPath, ...], bool]] = {
        seed_id: ((ImpactPath(nodes=(seed_id,)),), False)
    }

    def build(node_id: str) -> tuple[tuple[ImpactPath, ...], bool]:
        cached = memo.get(node_id)
        if cached is not None:
            return cached
        routes: list[ImpactPath] = []
        truncated = False
        for edge in incoming[node_id]:
            parent_routes, parent_truncated = build(edge.source_id)
            truncated = truncated or parent_truncated
            routes.extend(
                ImpactPath(nodes=(*route.nodes, node_id)) for route in parent_routes
            )
            if len(routes) > max_paths:
                break
        if len(routes) > max_paths:
            routes = routes[:max_paths]
            truncated = True
        memo[node_id] = (tuple(routes), truncated)
        return memo[node_id]

    return {node_id: build(node_id) for node_id in incoming}
