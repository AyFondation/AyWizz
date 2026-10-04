# =============================================================================
# File: storage.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/objects/storage.py
# Description: MinIO persistence for the object-grain document model
#              (310-SPEC-DOC-TRACEABILITY §4.1, §4.11). MinIO is the source of
#              truth (R-310-001); ArangoDB is a rebuildable index (R-310-002).
#
#              Three path families, three lifetimes:
#                - the CURRENT object   objects/<oid>.json          overwritten
#                - the VERSION chain    objects/_versions/<oid>@vN.json.gz
#                                       append-only, never deleted
#                - the WORKING DRAFT    objects/<oid>.draft.json    overwritten,
#                                       removed on resolution
#
#              There is deliberately NO API to delete or compact a version
#              snapshot: R-310-202 mandates full retention and R-310-204
#              forbids deleting a baselined version. The absence of the
#              operation is the enforcement.
#
#              Composes RequirementsStorage rather than re-implementing the
#              MinIO plumbing (async wrappers over the sync client).
#
# @relation implements:R-310-001
# @relation implements:R-310-003
# @relation implements:R-310-009
# @relation implements:R-310-202
# @relation implements:R-310-203
# @relation implements:R-310-204
# @relation implements:R-310-205
# =============================================================================

from __future__ import annotations

import gzip
import re

from pydantic import ValidationError

from ..storage.minio_storage import RequirementsStorage, StorageError
from .models import DocObject, WorkingDraft, is_valid_object_id

_JSON = "application/json"
_GZIP = "application/gzip"

# Container slugs come from the published cycle (R-310-021). They are used as
# path segments, so they are constrained to a safe alphabet — no separators,
# no relative segments.
_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Project ids are already validated upstream by C2/C5, but this module builds
# paths from them, so it re-checks rather than trusting its caller.
_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_VERSION_SUFFIX_RE = re.compile(r"@v(\d+)\.json\.gz$")


class ObjectPathError(ValueError):
    """Raised when a path component would escape the object namespace."""


def validate_scope(project_id: str, container: str) -> None:
    """Reject a project id or container slug that would escape the namespace.

    Exposed because the guard must bind at the service boundary, not only on
    the paths that reach MinIO: a read served entirely from the derived index
    would otherwise accept a malformed slug and answer with an empty list
    instead of refusing it.

    Args:
        project_id: Owning project.
        container: Container slug from the published cycle.

    Raises:
        ObjectPathError: On a malformed component.
    """
    _check_project(project_id)
    _check_container(container)


def _check_project(project_id: str) -> str:
    if not _PROJECT_RE.match(project_id):
        raise ObjectPathError(f"Invalid project id {project_id!r}")
    return project_id


def _check_container(container: str) -> str:
    if not _CONTAINER_RE.match(container):
        raise ObjectPathError(f"Invalid container slug {container!r}")
    return container


def _check_object_id(object_id: str) -> str:
    if not is_valid_object_id(object_id):
        raise ObjectPathError(f"Invalid object id {object_id!r} (R-310-005)")
    return object_id


class ObjectStorage:
    """Source-of-truth persistence for document objects, drafts and versions.

    Args:
        storage: The shared C5 MinIO facade. Composed rather than subclassed:
            the corpus store and the object store address different path
            families within the same bucket.
    """

    def __init__(self, storage: RequirementsStorage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Path helpers — E-310-001
    # ------------------------------------------------------------------

    @staticmethod
    def objects_prefix(project_id: str, container: str) -> str:
        """Return the prefix holding a container's current objects."""
        return (
            f"projects/{_check_project(project_id)}/docs/"
            f"{_check_container(container)}/objects/"
        )

    @classmethod
    def object_path(cls, project_id: str, container: str, object_id: str) -> str:
        """Return the path of the current version of one object."""
        return (
            f"{cls.objects_prefix(project_id, container)}"
            f"{_check_object_id(object_id)}.json"
        )

    @classmethod
    def draft_path(cls, project_id: str, container: str, object_id: str) -> str:
        """Return the path of one object's working draft (R-310-009)."""
        return (
            f"{cls.objects_prefix(project_id, container)}"
            f"{_check_object_id(object_id)}.draft.json"
        )

    @classmethod
    def versions_prefix(cls, project_id: str, container: str, object_id: str) -> str:
        """Return the prefix holding one object's retained version chain."""
        return (
            f"{cls.objects_prefix(project_id, container)}_versions/"
            f"{_check_object_id(object_id)}"
        )

    @classmethod
    def version_path(
        cls, project_id: str, container: str, object_id: str, version: int
    ) -> str:
        """Return the path of one retained object version (R-310-202).

        Args:
            project_id: Owning project.
            container: Container slug from the published cycle.
            object_id: Object identifier.
            version: Version number, 1-based.

        Returns:
            The gzip-compressed snapshot path.

        Raises:
            ObjectPathError: On a malformed component or a non-positive version.
        """
        if version < 1:
            raise ObjectPathError(f"Version SHALL be >= 1, got {version}")
        return f"{cls.versions_prefix(project_id, container, object_id)}@v{version}.json.gz"

    # ------------------------------------------------------------------
    # Current object
    # ------------------------------------------------------------------

    async def put_object(self, obj: DocObject) -> None:
        """Persist an object and append its version snapshot.

        The snapshot is written first: a current object visible without its
        retained version would break R-310-202, whereas a snapshot without a
        current object is merely an orphan the reindex ignores.

        Args:
            obj: The object to persist.
        """
        payload = obj.model_dump_json().encode("utf-8")
        await self._storage.put_document(
            self.version_path(obj.project_id, obj.container, obj.object_id, obj.version),
            gzip.compress(payload),
            _GZIP,
        )
        await self._storage.put_document(
            self.object_path(obj.project_id, obj.container, obj.object_id),
            payload,
            _JSON,
        )

    async def get_object(
        self, project_id: str, container: str, object_id: str
    ) -> DocObject:
        """Read the current version of an object.

        Raises:
            FileNotFoundError: When no such object exists.
            StorageError: When the stored payload is not a valid object.
        """
        path = self.object_path(project_id, container, object_id)
        raw = await self._storage.get_document(path)
        return _parse(DocObject, raw, path)

    async def get_version(
        self, project_id: str, container: str, object_id: str, version: int
    ) -> DocObject:
        """Read one retained version of an object (R-310-202).

        Raises:
            FileNotFoundError: When that version was never written.
            StorageError: When the stored payload is not a valid object.
        """
        path = self.version_path(project_id, container, object_id, version)
        raw = gzip.decompress(await self._storage.get_document(path))
        return _parse(DocObject, raw, path)

    async def list_versions(
        self, project_id: str, container: str, object_id: str
    ) -> list[int]:
        """Return every retained version number for an object, ascending."""
        prefix = self.versions_prefix(project_id, container, object_id)
        versions: list[int] = []
        for meta in await self._storage.list_objects(prefix):
            match = _VERSION_SUFFIX_RE.search(meta.path)
            # A sibling object whose id starts with the same characters shares
            # the prefix (OBJ-11 vs OBJ-110), so the stem is compared exactly.
            if match and meta.path[: match.start()] == prefix:
                versions.append(int(match.group(1)))
        return sorted(versions)

    async def list_object_ids(self, project_id: str, container: str) -> list[str]:
        """Return the identifiers of a container's current objects, sorted.

        Drafts and the retained version chain are excluded: only published
        objects are listed.
        """
        prefix = self.objects_prefix(project_id, container)
        ids: list[str] = []
        for meta in await self._storage.list_objects(prefix):
            tail = meta.path[len(prefix) :]
            if "/" in tail or not tail.endswith(".json") or tail.endswith(".draft.json"):
                continue
            ids.append(tail.removesuffix(".json"))
        return sorted(ids)

    # ------------------------------------------------------------------
    # Working draft — R-310-009 / R-310-203
    # ------------------------------------------------------------------

    async def put_draft(self, draft: WorkingDraft) -> None:
        """Write (overwriting) an object's working draft.

        Writing a draft creates no object version and touches no version
        snapshot — that is the whole point of R-310-009.
        """
        await self._storage.put_document(
            self.draft_path(draft.project_id, draft.container, draft.object_id),
            draft.model_dump_json().encode("utf-8"),
            _JSON,
        )

    async def get_draft(
        self, project_id: str, container: str, object_id: str
    ) -> WorkingDraft | None:
        """Return the unresolved working draft, or None when there is none."""
        path = self.draft_path(project_id, container, object_id)
        try:
            raw = await self._storage.get_document(path)
        except FileNotFoundError:
            return None
        return _parse(WorkingDraft, raw, path)

    async def delete_draft(
        self, project_id: str, container: str, object_id: str
    ) -> None:
        """Discard a working draft once its negotiation resolves (R-310-203).

        Idempotent: discarding an absent draft is not an error, so that a
        resolution path may run twice without failing.
        """
        try:
            await self._storage.delete_document(
                self.draft_path(project_id, container, object_id)
            )
        except FileNotFoundError:
            return


def _parse[T: (DocObject, WorkingDraft)](model: type[T], raw: bytes, path: str) -> T:
    """Validate a stored payload, attributing a corrupt one to its path.

    Args:
        model: The expected model type.
        raw: The stored bytes.
        path: The MinIO path, used in the error message.

    Returns:
        The parsed model.

    Raises:
        StorageError: When the payload does not validate.
    """
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise StorageError(
            f"Corrupt {model.__name__} payload at {path}: {exc}"
        ) from exc
