# =============================================================================
# File: service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/baseline/service.py
# Description: Taking a baseline — 310-SPEC §4.11 (R-310-200, R-310-201).
#
#              THE GATE COLLECTS EVERY REFUSAL, like the change-closure gate
#              of R-310-146. Three preconditions, and a reviewer told only
#              the first comes back twice more. `readiness` evaluates them
#              without side effects so the workbench can show what stands in
#              the way before anyone tries.
#
#              A BASELINE INCLUDES ONLY ACCEPTED OBJECTS. `R-310-201` does
#              not say so in those words, but it refuses a baseline while
#              any link is `stale` or a rated requirement has a gap — and a
#              `proposed` object is, by construction, content nobody has
#              agreed to. Including it would make the photograph a record of
#              what an agent wrote rather than of what the project decided.
#              `auto-accepted` IS included: it is acceptance, granted by
#              cluster review (`R-310-007`), and the manifest records the
#              state per entry so an audit can still separate the two.
#
#              THE MANIFEST IS VERIFIED ON READ, not merely trusted. Each
#              entry carries a content hash; `resolve` re-hashes what it
#              reads and refuses a mismatch. A reference nobody checks is a
#              reference that silently rots.
#
# @relation implements:R-310-200
# @relation implements:R-310-201
# @relation implements:R-310-204
# =============================================================================

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ..coverage.models import ACCEPTED_STATES
from ..objects.models import DocObjectPublic, ReviewState
from .models import (
    BaselineBlocker,
    BaselineManifest,
    BaselineReadiness,
    BaselineRefusal,
    ManifestLink,
    ManifestObject,
    content_hash,
)
from .repository import BaselineRepository
from .storage import BaselineStorage

#: (project_id) -> the identifiers of change tickets still open.
OpenTicketLookup = Callable[[str], Awaitable[Sequence[str]]]

#: (project_id) -> the coverage links whose target moved past its pin.
SuspectLinkLookup = Callable[[str], Awaitable[Sequence[dict[str, object]]]]

#: (project_id) -> `(requirement_id, criticality)` for every rated
#: requirement that is not answered.
RatedGapLookup = Callable[[str], Awaitable[Sequence[tuple[str, str]]]]

#: (tenant_id, project_id, cycle_id) -> the resolved cycle's version and
#: its container slugs in cascade order. The TENANT is part of the
#: question because a cycle is published at tenant level and tailored per
#: project (R-310-046) — an earlier draft of this lookup omitted it and
#: could not have resolved anything.
CycleLookup = Callable[[str, str, str], Awaitable[tuple[int, Sequence[str]]]]

#: (project_id, container) -> that container's objects, in document order.
#: Typed on the SERVICE shape (`DocObjectPublic`) rather than the storage
#: one: this service composes other services, and `list_container` is
#: what it will be handed.
ContainerLookup = Callable[[str, str], Awaitable[Sequence[DocObjectPublic]]]

#: (project_id, container) -> the coverage links recorded on its objects.
LinkLookup = Callable[[str, str], Awaitable[Sequence[dict[str, object]]]]


class BaselineRefusedError(RuntimeError):
    """Raised when `R-310-201` refuses a baseline.

    Carries the readiness verdict, so the caller can present every
    outstanding precondition rather than the first.
    """

    def __init__(self, readiness: BaselineReadiness) -> None:
        self.readiness = readiness
        super().__init__(readiness.explain())


class BaselineNotFoundError(RuntimeError):
    """Raised when a baseline tag was never taken."""


class ManifestIntegrityError(RuntimeError):
    """Raised when a manifest entry no longer matches what it names."""


class NoPublishedCycleError(RuntimeError):
    """Raised when a project runs on no published cycle.

    A baseline photographs a corpus structured by a cycle; without one
    there are no containers to walk and no document to render. Surfaced as
    its own error rather than as an empty manifest, which would be a
    baseline asserting that the project contains nothing.
    """


def renderable(obj: DocObjectPublic) -> tuple[str, str]:
    """Return an object's `(kind, content)` pair for hashing and rendering.

    `R-310-008` makes prose and figures exclusive: a paragraph carries
    `body`, a figure carries `notation`. Returning the pair rather than a
    bare string is what lets `content_hash` distinguish them, and what
    lets a renderer know whether it holds text or a figure description.
    """
    if obj.notation is not None:
        return ("notation", obj.notation)
    return ("body", obj.body or "")


@dataclass(frozen=True, slots=True)
class ResolvedObject:
    """A manifest entry resolved to the content it names.

    The manifest holds no content (`R-310-200`); this is the pairing a
    renderer works from, assembled at read time and never stored.
    """

    entry: ManifestObject
    kind: str
    """`"body"` for prose, `"notation"` for a figure (`R-310-008`)."""
    content: str

    @property
    def is_figure(self) -> bool:
        """True when this entry is a figure rather than prose."""
        return self.kind == "notation"

    @property
    def is_auto_accepted(self) -> bool:
        """True when acceptance came from cluster review (`R-310-007`)."""
        return self.entry.review_state == ReviewState.AUTO_ACCEPTED.value


class BaselineService:
    """Evaluate the gate, take baselines, and resolve them for rendering.

    Args:
        storage: Source-of-truth persistence for manifests.
        repository: The queryable index.
        open_tickets: Change tickets still open (`R-310-201`).
        suspect_links: Coverage links past their pin (`R-310-145`).
        rated_gaps: Criticality-rated requirements with a coverage gap.
        resolve_cycle: The cycle version and containers to photograph.
        container_objects: A container's objects.
        container_links: A container's coverage links.
    """

    def __init__(
        self,
        storage: BaselineStorage,
        repository: BaselineRepository,
        open_tickets: OpenTicketLookup,
        suspect_links: SuspectLinkLookup,
        rated_gaps: RatedGapLookup,
        resolve_cycle: CycleLookup,
        container_objects: ContainerLookup,
        container_links: LinkLookup,
    ) -> None:
        self._storage = storage
        self._repo = repository
        self._open_tickets = open_tickets
        self._suspect = suspect_links
        self._gaps = rated_gaps
        self._cycle = resolve_cycle
        self._objects = container_objects
        self._links = container_links

    # ------------------------------------------------------------------
    # The gate — R-310-201
    # ------------------------------------------------------------------

    async def readiness(self, project_id: str) -> BaselineReadiness:
        """Evaluate `R-310-201` without taking a baseline.

        Every precondition is checked, not just until the first failure:
        a reviewer shown one obstacle at a time comes back twice more.
        """
        refusals: list[BaselineRefusal] = []

        for ticket_id in await self._open_tickets(project_id):
            refusals.append(
                BaselineRefusal(
                    blocker=BaselineBlocker.OPEN_CHANGE_TICKET,
                    subject=ticket_id,
                    detail=(
                        "a supplied change has not been absorbed end to end; "
                        "baselining now would photograph a corpus that is "
                        "mid-change (R-310-201)"
                    ),
                )
            )

        for requirement_id, criticality in await self._gaps(project_id):
            refusals.append(
                BaselineRefusal(
                    blocker=BaselineBlocker.CRITICAL_COVERAGE_GAP,
                    subject=requirement_id,
                    detail=(
                        f"rated {criticality} and answered by nothing; a "
                        "baseline asserting coverage it does not have is the "
                        "document an audit reads (R-310-201)"
                    ),
                )
            )

        for row in await self._suspect(project_id):
            object_id = str(row.get("object_id", "?"))
            target_id = str(row.get("target_id", "?"))
            refusals.append(
                BaselineRefusal(
                    blocker=BaselineBlocker.STALE_COVERAGE_LINK,
                    subject=f"{object_id}->{target_id}",
                    detail=(
                        f"pins v{row.get('pinned_version')} of {target_id}, now "
                        f"at v{row.get('current_version')}: the pin would record "
                        "an answer to a version that no longer exists"
                    ),
                )
            )

        return BaselineReadiness(project_id=project_id, refusals=tuple(refusals))

    # ------------------------------------------------------------------
    # Taking a baseline — R-310-200
    # ------------------------------------------------------------------

    async def create(
        self,
        tenant_id: str,
        project_id: str,
        tag: str,
        *,
        cycle_id: str,
        actor: str,
        note: str = "",
        now: datetime | None = None,
    ) -> BaselineManifest:
        """Take a baseline, if and only if `R-310-201` permits it.

        Raises:
            BaselineRefusedError: When any precondition fails; the verdict
                it carries names every one.
            NoPublishedCycleError: When the project runs on no published
                cycle — a baseline of an undeclared process has no
                containers to photograph and no meaning.
            BaselineExistsError: When that tag is already taken.
        """
        verdict = await self.readiness(project_id)
        if not verdict.is_ready:
            raise BaselineRefusedError(verdict)

        cycle_version, containers = await self._cycle(
            tenant_id, project_id, cycle_id
        )
        entries: list[ManifestObject] = []
        links: list[ManifestLink] = []

        for container in containers:
            included: set[str] = set()
            for obj in await self._objects(project_id, container):
                # Only decided content belongs in a photograph of what the
                # project decided — see the module docstring.
                if obj.review_state not in ACCEPTED_STATES:
                    continue
                included.add(obj.object_id)
                entries.append(
                    ManifestObject(
                        object_id=obj.object_id,
                        container=container,
                        version=obj.version,
                        ordinal=obj.ordinal,
                        review_state=obj.review_state.value,
                        content_hash=content_hash(*renderable(obj)),
                    )
                )
            for row in await self._links(project_id, container):
                object_id = str(row.get("object_id", ""))
                # A pin on an excluded object would resolve to nothing, and
                # the manifest validator refuses it — so filter here rather
                # than letting construction fail on data that is merely
                # incomplete.
                if object_id not in included:
                    continue
                links.append(
                    ManifestLink(
                        object_id=object_id,
                        container=container,
                        target_id=str(row.get("target_id", "")),
                        pinned_version=int(str(row.get("pinned_version", 1))),
                        strength=str(row.get("strength", "covered")),
                        state=str(row.get("state", "accepted")),
                    )
                )

        manifest = BaselineManifest(
            tag=tag,
            project_id=project_id,
            cycle_id=cycle_id,
            cycle_version=cycle_version,
            created_by=actor,
            created_at=now or datetime.now(UTC),
            objects=tuple(entries),
            links=tuple(links),
            note=note,
        )
        await self._storage.put_manifest(manifest)
        await self._repo.put_baseline(manifest)
        return manifest

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, project_id: str, tag: str) -> BaselineManifest:
        """Return one manifest.

        Raises:
            BaselineNotFoundError: When that tag was never taken.
        """
        manifest = await self._storage.get_manifest(project_id, tag)
        if manifest is None:
            raise BaselineNotFoundError(
                f"no baseline {tag!r} in project {project_id!r}"
            )
        return manifest

    async def list_tags(self, project_id: str) -> tuple[str, ...]:
        """Return the tags taken in a project."""
        return await self._storage.list_tags(project_id)

    async def resolve(
        self, project_id: str, tag: str, *, verify: bool = True
    ) -> tuple[ResolvedObject, ...]:
        """Resolve a baseline's entries to the content they name.

        Args:
            project_id: Owning project.
            tag: The baseline to resolve.
            verify: Re-hash each resolved object and refuse a mismatch. On
                by default: a reference nobody checks is a reference that
                silently rots, and the hash is in the manifest precisely so
                it can be checked.

        Raises:
            BaselineNotFoundError: When the tag was never taken.
            ManifestIntegrityError: When an entry no longer matches what it
                names, or names a version that cannot be read.
        """
        manifest = await self.get(project_id, tag)
        resolved: list[ResolvedObject] = []

        for container in manifest.containers:
            wanted = {e.object_id: e for e in manifest.entries_of(container)}
            for obj in await self._objects(project_id, container):
                entry = wanted.pop(obj.object_id, None)
                if entry is None:
                    continue
                if obj.version != entry.version:
                    # The baseline names a HISTORICAL version; the current
                    # object has moved on. That is normal, and the stored
                    # version is what must be read — surfaced rather than
                    # silently rendering today's text under yesterday's tag.
                    raise ManifestIntegrityError(
                        f"baseline {tag!r} names {obj.object_id!r} at v"
                        f"{entry.version}, but only v{obj.version} was read; "
                        "resolve the stored version rather than the current one"
                    )
                kind, content = renderable(obj)
                if verify and content_hash(kind, content) != entry.content_hash:
                    raise ManifestIntegrityError(
                        f"{obj.object_id!r} v{entry.version} no longer hashes to "
                        f"{entry.content_hash}; the baseline's record and the "
                        "stored content disagree (R-310-204)"
                    )
                resolved.append(
                    ResolvedObject(entry=entry, kind=kind, content=content)
                )
            if wanted:
                raise ManifestIntegrityError(
                    f"baseline {tag!r} names {sorted(wanted)} in {container!r}, "
                    "which could not be read; a version a baseline references "
                    "SHALL NOT be deleted (R-310-204)"
                )

        return tuple(resolved)
