# =============================================================================
# File: service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/process/service.py
# Description: Draft → publish lifecycle and tailoring resolution for cycles
#              and workflows — 310-SPEC §4.2 / §4.3.
#
#              THE RULES THIS MODULE ENFORCES:
#                - a published version is immutable; editing one creates a
#                  NEW draft version (R-310-023, R-310-045);
#                - publishing supersedes the previously approved version at
#                  the same scope, in the index only;
#                - `resolve_*` answers "which definition applies to this
#                  project?" — the project's own tailoring when it has one,
#                  otherwise the tenant catalogue (R-310-022, R-310-046).
#
#              Tailoring REPLACES rather than merges. `R-310-046` allows a
#              project to disable, restrict or override; a field-level merge
#              between a tenant cycle and a project override would produce a
#              definition no one authored and no one can review. Replacement
#              with a mandatory rationale is auditable; a merge is not.
#
# @relation implements:R-310-022
# @relation implements:R-310-023
# @relation implements:R-310-045
# @relation implements:R-310-046
# @relation implements:R-310-047
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

from .models import (
    ActivityBinding,
    ActivityDenialReason,
    ContainerSpec,
    CycleDefinition,
    EntityStatus,
    WorkflowCheck,
    WorkflowDefinition,
    WorkflowPublic,
    WorkflowStep,
)
from .repository import ProcessRepository
from .storage import AlreadyPublishedError, ProcessStorage


class ProcessNotFoundError(LookupError):
    """Raised when a cycle or workflow version does not exist."""


class ActivityNotPermittedError(RuntimeError):
    """Raised when no activity may start on a container (R-310-025).

    Carries a machine-readable reason so a caller can tell the user what is
    missing — a published cycle, a workflow binding, or the publication of
    the bound workflow — rather than reporting an undifferentiated refusal.
    """

    def __init__(self, reason: ActivityDenialReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class NotADraftError(RuntimeError):
    """Raised when an operation that requires a draft is given a published version.

    This is the business-level face of R-310-023. Editing a published
    version is not an error to be worked around: the caller creates a new
    draft version instead.
    """


class ProcessService:
    """Author, publish and resolve cycles and workflows.

    Args:
        storage: MinIO source of truth.
        repository: Derived Arango index.
    """

    def __init__(self, storage: ProcessStorage, repository: ProcessRepository) -> None:
        self._storage = storage
        self._repo = repository

    # ------------------------------------------------------------------
    # Cycles — authoring
    # ------------------------------------------------------------------

    async def create_cycle_draft(
        self,
        tenant_id: str,
        project_id: str | None,
        *,
        cycle_id: str,
        title: str,
        containers: tuple[ContainerSpec, ...],
        actor: str,
        tailoring_of: str | None = None,
        tailoring_rationale: str | None = None,
        now: datetime | None = None,
    ) -> CycleDefinition:
        """Start a new draft version of a cycle.

        The version number is the next free one at this scope, so editing a
        published cycle naturally produces a new version rather than an
        attempt to rewrite the old one.
        """
        moment = now or datetime.now(UTC)
        version = await self._repo.next_version(
            tenant_id, project_id, cycle_id, is_cycle=True
        )
        draft = CycleDefinition(
            cycle_id=cycle_id,
            title=title,
            containers=containers,
            version=version,
            status=EntityStatus.DRAFT,
            tenant_id=tenant_id,
            project_id=project_id,
            tailoring_of=tailoring_of,
            tailoring_rationale=tailoring_rationale,
            created_at=moment,
            created_by=actor,
            updated_at=moment,
            updated_by=actor,
        )
        await self._storage.put_draft(draft)
        await self._repo.upsert_cycle(draft)
        return draft

    async def update_cycle_draft(
        self,
        tenant_id: str,
        project_id: str | None,
        cycle_id: str,
        version: int,
        *,
        title: str,
        containers: tuple[ContainerSpec, ...],
        actor: str,
        now: datetime | None = None,
    ) -> CycleDefinition:
        """Replace the content of an unpublished cycle draft.

        Raises:
            ProcessNotFoundError: When that version does not exist.
            NotADraftError: When that version is published.
        """
        current = await self._read_cycle(tenant_id, project_id, cycle_id, version)
        self._require_draft(current.status, f"{cycle_id}@v{version}")
        moment = now or datetime.now(UTC)
        updated = current.model_copy(
            update={
                "title": title,
                "containers": containers,
                "updated_at": moment,
                "updated_by": actor,
            }
        )
        # Re-validate: model_copy bypasses validators, and a caller could
        # otherwise install duplicate slugs or an empty container list.
        updated = CycleDefinition.model_validate(updated.model_dump())
        await self._storage.put_draft(updated)
        await self._repo.upsert_cycle(updated)
        return updated

    async def publish_cycle(
        self,
        tenant_id: str,
        project_id: str | None,
        cycle_id: str,
        version: int,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> CycleDefinition:
        """Seal a cycle draft as the approved version at this scope.

        Raises:
            ProcessNotFoundError: When that version does not exist.
            NotADraftError: When that version is already published.
            AlreadyPublishedError: When the sealed slot is occupied.
        """
        draft = await self._read_cycle(tenant_id, project_id, cycle_id, version)
        self._require_draft(draft.status, f"{cycle_id}@v{version}")
        moment = now or datetime.now(UTC)
        approved = CycleDefinition.model_validate(
            draft.model_copy(
                update={
                    "status": EntityStatus.APPROVED,
                    "updated_at": moment,
                    "updated_by": actor,
                }
            ).model_dump()
        )
        # Seal first: the index may be rebuilt from MinIO, so an index row
        # claiming an approved version MinIO does not hold would be a lie,
        # while the reverse is repaired by a re-index.
        await self._storage.seal(approved)
        await self._repo.supersede_approved(
            tenant_id, project_id, cycle_id, is_cycle=True
        )
        await self._repo.upsert_cycle(approved)
        return approved

    # ------------------------------------------------------------------
    # Workflows — authoring
    # ------------------------------------------------------------------

    async def create_workflow_draft(
        self,
        tenant_id: str,
        project_id: str | None,
        *,
        workflow_id: str,
        intent: str,
        steps: tuple[WorkflowStep, ...],
        checks: tuple[WorkflowCheck, ...] = (),
        inputs: dict[str, str] | None = None,
        outputs: dict[str, str] | None = None,
        constraints: tuple[str, ...] = (),
        examples: tuple[str, ...] = (),
        actor: str,
        tailoring_of: str | None = None,
        tailoring_rationale: str | None = None,
        now: datetime | None = None,
    ) -> WorkflowDefinition:
        """Start a new draft version of a workflow.

        A draft may have no checks — it is being written. The publication
        gate of R-310-041 binds at `publish_workflow`.
        """
        moment = now or datetime.now(UTC)
        version = await self._repo.next_version(
            tenant_id, project_id, workflow_id, is_cycle=False
        )
        draft = WorkflowDefinition(
            workflow_id=workflow_id,
            intent=intent,
            steps=steps,
            checks=checks,
            inputs=inputs or {},
            outputs=outputs or {},
            constraints=constraints,
            examples=examples,
            version=version,
            status=EntityStatus.DRAFT,
            tenant_id=tenant_id,
            project_id=project_id,
            tailoring_of=tailoring_of,
            tailoring_rationale=tailoring_rationale,
            created_at=moment,
            created_by=actor,
            updated_at=moment,
            updated_by=actor,
        )
        await self._storage.put_draft(draft)
        await self._repo.upsert_workflow(draft)
        return draft

    async def update_workflow_draft(
        self,
        tenant_id: str,
        project_id: str | None,
        workflow_id: str,
        version: int,
        *,
        actor: str,
        now: datetime | None = None,
        **fields: object,
    ) -> WorkflowDefinition:
        """Replace fields of an unpublished workflow draft.

        Raises:
            ProcessNotFoundError: When that version does not exist.
            NotADraftError: When that version is published.
        """
        current = await self._read_workflow(
            tenant_id, project_id, workflow_id, version
        )
        self._require_draft(current.status, f"{workflow_id}@v{version}")
        moment = now or datetime.now(UTC)
        merged = current.model_copy(
            update={**fields, "updated_at": moment, "updated_by": actor}
        )
        updated = WorkflowDefinition.model_validate(merged.model_dump())
        await self._storage.put_draft(updated)
        await self._repo.upsert_workflow(updated)
        return updated

    async def publish_workflow(
        self,
        tenant_id: str,
        project_id: str | None,
        workflow_id: str,
        version: int,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> WorkflowDefinition:
        """Seal a workflow draft as the approved version at this scope.

        A draft with no checks cannot be published: the model refuses to
        construct an approved workflow without acceptance criteria
        (R-310-041), and that refusal surfaces here.

        Raises:
            ProcessNotFoundError: When that version does not exist.
            NotADraftError: When that version is already published.
            ValueError: When the draft cannot be approved as it stands.
        """
        draft = await self._read_workflow(
            tenant_id, project_id, workflow_id, version
        )
        self._require_draft(draft.status, f"{workflow_id}@v{version}")
        moment = now or datetime.now(UTC)
        approved = WorkflowDefinition.model_validate(
            draft.model_copy(
                update={
                    "status": EntityStatus.APPROVED,
                    "updated_at": moment,
                    "updated_by": actor,
                }
            ).model_dump()
        )
        await self._storage.seal(approved)
        await self._repo.supersede_approved(
            tenant_id, project_id, workflow_id, is_cycle=False
        )
        await self._repo.upsert_workflow(approved)
        return approved

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_cycle(
        self, tenant_id: str, project_id: str | None, cycle_id: str, version: int
    ) -> CycleDefinition:
        """Return one cycle version."""
        return await self._read_cycle(tenant_id, project_id, cycle_id, version)

    async def get_workflow(
        self, tenant_id: str, project_id: str | None, workflow_id: str, version: int
    ) -> WorkflowDefinition:
        """Return one workflow version."""
        return await self._read_workflow(tenant_id, project_id, workflow_id, version)

    async def list_cycle_versions(
        self, tenant_id: str, project_id: str | None, cycle_id: str | None = None
    ) -> list[dict[str, object]]:
        """Return index rows for cycles at this scope."""
        return await self._repo.list_versions(
            tenant_id, project_id, cycle_id, is_cycle=True
        )

    async def list_workflow_versions(
        self, tenant_id: str, project_id: str | None, workflow_id: str | None = None
    ) -> list[dict[str, object]]:
        """Return index rows for workflows at this scope."""
        return await self._repo.list_versions(
            tenant_id, project_id, workflow_id, is_cycle=False
        )

    # ------------------------------------------------------------------
    # Tailoring resolution — R-310-022 / R-310-046
    # ------------------------------------------------------------------

    async def resolve_cycle(
        self, tenant_id: str, project_id: str, cycle_id: str
    ) -> CycleDefinition | None:
        """Return the cycle that applies to a project, or None.

        The project's own approved tailoring wins outright; absent one, the
        tenant catalogue applies. There is no field-level merge — see the
        module docstring.
        """
        tailored = await self._approved_cycle(tenant_id, project_id, cycle_id)
        if tailored is not None:
            return tailored
        return await self._approved_cycle(tenant_id, None, cycle_id)

    async def resolve_workflow(
        self, tenant_id: str, project_id: str, workflow_id: str
    ) -> WorkflowDefinition | None:
        """Return the workflow that applies to a project, or None."""
        tailored = await self._approved_workflow(tenant_id, project_id, workflow_id)
        if tailored is not None:
            return tailored
        return await self._approved_workflow(tenant_id, None, workflow_id)

    async def approved_cycle(
        self, tenant_id: str, project_id: str | None, cycle_id: str
    ) -> CycleDefinition | None:
        """Return the approved cycle at exactly this scope, without fallback."""
        return await self._approved_cycle(tenant_id, project_id, cycle_id)

    async def approved_workflow(
        self, tenant_id: str, project_id: str | None, workflow_id: str
    ) -> WorkflowDefinition | None:
        """Return the approved workflow at exactly this scope, without fallback."""
        return await self._approved_workflow(tenant_id, project_id, workflow_id)

    # ------------------------------------------------------------------
    # Phase binding — R-310-025
    # ------------------------------------------------------------------

    async def resolve_activity(
        self, tenant_id: str, project_id: str, cycle_id: str, container: str
    ) -> ActivityBinding:
        """Return the activity permitted on a container, or refuse.

        This is the precondition of R-310-025, expressed as a function the
        caller must pass through: without the binding it returns, an
        activity has no published workflow to run, and "workflows are
        applied systematically" would degrade to "usually".

        Everything is resolved through the project's lens (R-310-022): the
        project's published cycle tailoring when it has one, and likewise
        for the workflow, so a project that tailored its authoring activity
        runs its own version and not the tenant's.

        Args:
            tenant_id: Owning tenant.
            project_id: The project the activity would run in.
            cycle_id: The cycle whose container is being worked on.
            container: Container slug.

        Returns:
            The resolved binding — holding it is the evidence the
            precondition is met.

        Raises:
            ActivityNotPermittedError: With a machine-readable reason.
        """
        cycle = await self.resolve_cycle(tenant_id, project_id, cycle_id)
        if cycle is None:
            raise ActivityNotPermittedError(
                ActivityDenialReason.NO_CYCLE,
                f"no published cycle {cycle_id!r} applies to {project_id!r}",
            )

        spec = cycle.container(container)
        if spec is None:
            raise ActivityNotPermittedError(
                ActivityDenialReason.UNKNOWN_CONTAINER,
                f"{cycle_id}@v{cycle.version} declares no container "
                f"{container!r}",
            )

        if spec.workflow_id is None:
            raise ActivityNotPermittedError(
                ActivityDenialReason.NO_WORKFLOW_BOUND,
                f"container {container!r} binds no workflow in "
                f"{cycle_id}@v{cycle.version} (R-310-025)",
            )

        workflow = await self.resolve_workflow(
            tenant_id, project_id, spec.workflow_id
        )
        if workflow is None:
            raise ActivityNotPermittedError(
                ActivityDenialReason.WORKFLOW_NOT_PUBLISHED,
                f"workflow {spec.workflow_id!r} is bound to {container!r} but "
                "has no published version at this scope (R-310-025)",
            )

        return ActivityBinding(
            container=container,
            cycle_id=cycle.cycle_id,
            cycle_version=cycle.version,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            workflow=WorkflowPublic.from_definition(workflow),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _require_draft(status: EntityStatus, label: str) -> None:
        if status is not EntityStatus.DRAFT:
            raise NotADraftError(
                f"{label} is {status.value}; a published version is immutable — "
                "create a new draft version instead (R-310-023)"
            )

    async def _read_cycle(
        self, tenant_id: str, project_id: str | None, cycle_id: str, version: int
    ) -> CycleDefinition:
        try:
            return await self._storage.get_cycle(
                tenant_id, project_id, cycle_id, version
            )
        except FileNotFoundError as exc:
            raise ProcessNotFoundError(f"{cycle_id}@v{version}") from exc

    async def _read_workflow(
        self, tenant_id: str, project_id: str | None, workflow_id: str, version: int
    ) -> WorkflowDefinition:
        try:
            return await self._storage.get_workflow(
                tenant_id, project_id, workflow_id, version
            )
        except FileNotFoundError as exc:
            raise ProcessNotFoundError(f"{workflow_id}@v{version}") from exc

    async def _approved_cycle(
        self, tenant_id: str, project_id: str | None, cycle_id: str
    ) -> CycleDefinition | None:
        row = await self._repo.approved_version(
            tenant_id, project_id, cycle_id, is_cycle=True
        )
        if row is None:
            return None
        return await self._read_cycle(
            tenant_id, project_id, cycle_id, int(row["version"])
        )

    async def _approved_workflow(
        self, tenant_id: str, project_id: str | None, workflow_id: str
    ) -> WorkflowDefinition | None:
        row = await self._repo.approved_version(
            tenant_id, project_id, workflow_id, is_cycle=False
        )
        if row is None:
            return None
        return await self._read_workflow(
            tenant_id, project_id, workflow_id, int(row["version"])
        )


__all__ = [
    "ActivityNotPermittedError",
    "AlreadyPublishedError",
    "NotADraftError",
    "ProcessNotFoundError",
    "ProcessService",
]
