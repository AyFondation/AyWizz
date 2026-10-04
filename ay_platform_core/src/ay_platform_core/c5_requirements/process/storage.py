# =============================================================================
# File: storage.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/process/storage.py
# Description: MinIO persistence for cycle (`C-`) and workflow (`WF-`)
#              definitions — 310-SPEC §4.2 / §4.3.
#
#              Immutability of a published version (R-310-023, R-310-045) is
#              enforced HERE, by the shape of the API rather than by a caller
#              remembering a flag:
#
#                - `put_draft`  overwrites freely, and REFUSES a definition
#                               whose status is not `draft`;
#                - `seal`       writes an approved version write-once, and
#                               REFUSES to overwrite one that already exists.
#
#              There is no third method able to replace a sealed version. An
#              API that can rewrite published history will eventually be
#              called, and the `produced_by: WF-nnn@vN` stamp every object
#              carries would then mean nothing.
#
#              Each version is its own object: unlike document objects there
#              is no "current" file, because "current" is a question about
#              which version is approved — answered by the derived index.
#
# @relation implements:R-310-020
# @relation implements:R-310-023
# @relation implements:R-310-040
# @relation implements:R-310-045
# =============================================================================

from __future__ import annotations

import re

from pydantic import ValidationError

from ..storage.minio_storage import RequirementsStorage, StorageError
from .models import (
    CycleDefinition,
    EntityStatus,
    WorkflowDefinition,
    is_valid_cycle_id,
    is_valid_workflow_id,
)

_JSON = "application/json"

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION_FILE_RE = re.compile(r"/v(\d+)\.json$")


class ProcessPathError(ValueError):
    """Raised when a scope component would escape the process namespace."""


class AlreadyPublishedError(RuntimeError):
    """Raised when sealing a version that is already sealed.

    A published version is immutable (R-310-023). Editing one creates a new
    draft version; it never rewrites the old.
    """


def _check_scope(value: str, label: str) -> str:
    if not _SCOPE_RE.match(value):
        raise ProcessPathError(f"Invalid {label} {value!r}")
    return value


class ProcessStorage:
    """Source-of-truth persistence for cycles and workflows.

    Args:
        storage: The shared C5 MinIO facade, composed rather than subclassed.
    """

    def __init__(self, storage: RequirementsStorage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @staticmethod
    def scope_prefix(tenant_id: str, project_id: str | None) -> str:
        """Return the root prefix for a tenant-level or project-level entity.

        Tenant-level is where the standard catalogue lives (R-310-022,
        R-310-046); project-level is where a tailored override lives.
        """
        if project_id is None:
            return f"tenants/{_check_scope(tenant_id, 'tenant id')}/process/"
        _check_scope(tenant_id, "tenant id")
        return f"projects/{_check_scope(project_id, 'project id')}/process/"

    @classmethod
    def cycle_prefix(
        cls, tenant_id: str, project_id: str | None, cycle_id: str
    ) -> str:
        """Return the prefix holding every version of one cycle."""
        if not is_valid_cycle_id(cycle_id):
            raise ProcessPathError(f"Invalid cycle id {cycle_id!r}")
        return f"{cls.scope_prefix(tenant_id, project_id)}cycles/{cycle_id}/"

    @classmethod
    def workflow_prefix(
        cls, tenant_id: str, project_id: str | None, workflow_id: str
    ) -> str:
        """Return the prefix holding every version of one workflow."""
        if not is_valid_workflow_id(workflow_id):
            raise ProcessPathError(f"Invalid workflow id {workflow_id!r}")
        return f"{cls.scope_prefix(tenant_id, project_id)}workflows/{workflow_id}/"

    @classmethod
    def cycle_path(
        cls, tenant_id: str, project_id: str | None, cycle_id: str, version: int
    ) -> str:
        """Return the path of one cycle version."""
        return f"{cls.cycle_prefix(tenant_id, project_id, cycle_id)}{_v(version)}"

    @classmethod
    def workflow_path(
        cls, tenant_id: str, project_id: str | None, workflow_id: str, version: int
    ) -> str:
        """Return the path of one workflow version."""
        return (
            f"{cls.workflow_prefix(tenant_id, project_id, workflow_id)}"
            f"{_v(version)}"
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def put_draft(self, definition: CycleDefinition | WorkflowDefinition) -> None:
        """Write (overwriting) a draft version.

        Raises:
            AlreadyPublishedError: When the definition is not a draft, or when
                a sealed version already occupies that slot.
        """
        if definition.status is not EntityStatus.DRAFT:
            raise AlreadyPublishedError(
                "put_draft writes drafts only; use seal() to publish "
                "(R-310-023)"
            )
        path = self._path_of(definition)
        if await self._stored_status(definition) is EntityStatus.APPROVED:
            raise AlreadyPublishedError(
                f"{path} holds a published version; a draft SHALL NOT "
                "overwrite it (R-310-023)"
            )
        await self._storage.put_document(
            path, definition.model_dump_json().encode("utf-8"), _JSON
        )

    async def seal(self, definition: CycleDefinition | WorkflowDefinition) -> None:
        """Publish an approved version over its own draft, write-once.

        A version slot holds the draft while it is being written and the
        sealed definition once published — promotion in place is the normal
        lifecycle, not a violation. What is immutable is a version that is
        ALREADY approved: sealing over one is refused.

        Raises:
            AlreadyPublishedError: When the definition is not approved, or
                when that version is already sealed.
        """
        if definition.status is not EntityStatus.APPROVED:
            raise AlreadyPublishedError("seal() publishes approved versions only")
        path = self._path_of(definition)
        if await self._stored_status(definition) is EntityStatus.APPROVED:
            raise AlreadyPublishedError(
                f"{path} is already published; a published version is "
                "immutable (R-310-023)"
            )
        await self._storage.put_document(
            path, definition.model_dump_json().encode("utf-8"), _JSON
        )

    async def _stored_status(
        self, definition: CycleDefinition | WorkflowDefinition
    ) -> EntityStatus | None:
        """Return the status of what currently occupies this version slot.

        Parsed rather than matched as a substring: a container scope
        statement could legitimately contain the word "approved", and a
        publication guard that can be defeated by prose is not a guard.
        """
        path = self._path_of(definition)
        raw = await self._read_raw(path)
        if raw is None:
            return None
        # Branched rather than selecting the model class into a variable: the
        # common base of the two would widen the type parameter and lose the
        # narrowing mypy needs.
        if isinstance(definition, CycleDefinition):
            return _parse(CycleDefinition, raw, path).status
        return _parse(WorkflowDefinition, raw, path).status

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_cycle(
        self, tenant_id: str, project_id: str | None, cycle_id: str, version: int
    ) -> CycleDefinition:
        """Read one cycle version.

        Raises:
            FileNotFoundError: When that version was never written.
        """
        path = self.cycle_path(tenant_id, project_id, cycle_id, version)
        return _parse(CycleDefinition, await self._storage.get_document(path), path)

    async def get_workflow(
        self, tenant_id: str, project_id: str | None, workflow_id: str, version: int
    ) -> WorkflowDefinition:
        """Read one workflow version.

        Raises:
            FileNotFoundError: When that version was never written.
        """
        path = self.workflow_path(tenant_id, project_id, workflow_id, version)
        return _parse(WorkflowDefinition, await self._storage.get_document(path), path)

    async def list_cycle_versions(
        self, tenant_id: str, project_id: str | None, cycle_id: str
    ) -> list[int]:
        """Return every stored version number of a cycle, ascending."""
        return await self._versions(self.cycle_prefix(tenant_id, project_id, cycle_id))

    async def list_workflow_versions(
        self, tenant_id: str, project_id: str | None, workflow_id: str
    ) -> list[int]:
        """Return every stored version number of a workflow, ascending."""
        return await self._versions(
            self.workflow_prefix(tenant_id, project_id, workflow_id)
        )

    async def list_cycle_ids(
        self, tenant_id: str, project_id: str | None
    ) -> list[str]:
        """Return the cycle identifiers stored at this scope, sorted."""
        return await self._ids(f"{self.scope_prefix(tenant_id, project_id)}cycles/")

    async def list_workflow_ids(
        self, tenant_id: str, project_id: str | None
    ) -> list[str]:
        """Return the workflow identifiers stored at this scope, sorted."""
        return await self._ids(f"{self.scope_prefix(tenant_id, project_id)}workflows/")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _path_of(self, definition: CycleDefinition | WorkflowDefinition) -> str:
        if isinstance(definition, CycleDefinition):
            return self.cycle_path(
                definition.tenant_id,
                definition.project_id,
                definition.cycle_id,
                definition.version,
            )
        return self.workflow_path(
            definition.tenant_id,
            definition.project_id,
            definition.workflow_id,
            definition.version,
        )

    async def _read_raw(self, path: str) -> bytes | None:
        try:
            return await self._storage.get_document(path)
        except FileNotFoundError:
            return None

    async def _versions(self, prefix: str) -> list[int]:
        found: list[int] = []
        for meta in await self._storage.list_objects(prefix):
            match = _VERSION_FILE_RE.search(meta.path)
            # A sibling id sharing a character prefix (WF-002 vs WF-0021)
            # would otherwise bleed in, so the stem is compared exactly.
            if match and meta.path[: match.start() + 1] == prefix:
                found.append(int(match.group(1)))
        return sorted(found)

    async def _ids(self, prefix: str) -> list[str]:
        ids: set[str] = set()
        for meta in await self._storage.list_objects(prefix):
            tail = meta.path[len(prefix) :]
            head, _, rest = tail.partition("/")
            if head and rest:
                ids.add(head)
        return sorted(ids)


def _v(version: int) -> str:
    if version < 1:
        raise ProcessPathError(f"Version SHALL be >= 1, got {version}")
    return f"v{version}.json"


def _parse[D: (CycleDefinition, WorkflowDefinition)](
    model: type[D], raw: bytes, path: str
) -> D:
    """Validate a stored payload, attributing a corrupt one to its path.

    Args:
        model: Expected definition type.
        raw: Stored bytes.
        path: MinIO path, used in the error message.

    Returns:
        The parsed definition.

    Raises:
        StorageError: When the payload does not validate.
    """
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise StorageError(f"Corrupt {model.__name__} payload at {path}: {exc}") from exc
