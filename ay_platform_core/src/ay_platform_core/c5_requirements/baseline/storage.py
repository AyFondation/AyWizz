# =============================================================================
# File: storage.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/baseline/storage.py
# Description: MinIO persistence for baseline manifests — 310-SPEC §4.11
#              (R-310-200, R-310-204).
#
#              WRITE-ONCE, AND NO DELETE API. A baseline is the artefact an
#              audit reconstructs. One that could be overwritten would
#              answer a question about the past with a statement about the
#              present, and one that could be deleted would take the
#              referenced object versions' reason for being retained with it
#              (`R-310-204`). So `put_manifest` REFUSES an existing tag
#              rather than replacing it, and there is no delete — the same
#              structural guarantee as `objects/storage.py` (DV-08).
#
#              RENDERINGS ARE STORED BESIDE THE MANIFEST, not instead of it.
#              A DOCX is a projection; the manifest is the record. Keeping
#              the rendering is a convenience so a second download does not
#              re-render, and it is explicitly NOT authoritative — which is
#              why a re-render overwrites it freely while the manifest
#              cannot be touched.
#
# @relation implements:R-310-200
# @relation implements:R-310-204
# @relation implements:R-310-207
# =============================================================================

from __future__ import annotations

import re

from pydantic import ValidationError

from ..storage.minio_storage import RequirementsStorage, StorageError
from .models import BaselineManifest, RenderFormat

_JSON = "application/json"
_MEDIA: dict[RenderFormat, str] = {
    RenderFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    RenderFormat.PDF: "application/pdf",
}

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class BaselinePathError(ValueError):
    """Raised when a path component would escape the baseline namespace."""


class BaselineExistsError(RuntimeError):
    """Raised when a write would replace an existing baseline (`R-310-204`)."""


def _check(value: str, label: str, pattern: re.Pattern[str]) -> str:
    if not pattern.match(value):
        raise BaselinePathError(f"Invalid {label} {value!r}")
    return value


class BaselineStorage:
    """Source-of-truth persistence for baseline manifests and renderings.

    Args:
        storage: The shared C5 MinIO facade, composed rather than subclassed.
    """

    def __init__(self, storage: RequirementsStorage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @staticmethod
    def project_prefix(project_id: str) -> str:
        """Return the prefix holding every baseline of a project."""
        return f"projects/{_check(project_id, 'project id', _SCOPE_RE)}/baselines/"

    @classmethod
    def manifest_path(cls, project_id: str, tag: str) -> str:
        """Return the path of one baseline manifest."""
        return (
            f"{cls.project_prefix(project_id)}"
            f"{_check(tag, 'baseline tag', _TAG_RE)}.json"
        )

    @classmethod
    def rendering_path(
        cls, project_id: str, tag: str, fmt: RenderFormat
    ) -> str:
        """Return the path of one stored rendering of a baseline."""
        return (
            f"{cls.project_prefix(project_id)}_renderings/"
            f"{_check(tag, 'baseline tag', _TAG_RE)}.{fmt.value}"
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def put_manifest(self, manifest: BaselineManifest) -> None:
        """Write a manifest, refusing to replace an existing tag.

        Raises:
            BaselineExistsError: When that tag is already taken. Replacing
                it would rewrite history, and a baseline exists precisely
                so history cannot be rewritten (`R-310-204`).
        """
        path = self.manifest_path(manifest.project_id, manifest.tag)
        if await self._read(path) is not None:
            raise BaselineExistsError(
                f"baseline {manifest.tag!r} already exists in "
                f"{manifest.project_id!r}; a baseline is immutable, so take a "
                "new tag rather than replacing this one (R-310-204)"
            )
        await self._storage.put_document(
            path, manifest.model_dump_json(indent=2).encode("utf-8"), _JSON
        )

    async def put_rendering(
        self, project_id: str, tag: str, fmt: RenderFormat, payload: bytes
    ) -> str:
        """Store a rendering of a baseline and return its path.

        Overwrites freely, unlike the manifest: a rendering is a projection
        of the record, never the record.
        """
        path = self.rendering_path(project_id, tag, fmt)
        await self._storage.put_document(path, payload, _MEDIA[fmt])
        return path

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_manifest(
        self, project_id: str, tag: str
    ) -> BaselineManifest | None:
        """Return one manifest, or None when that tag was never taken."""
        path = self.manifest_path(project_id, tag)
        raw = await self._read(path)
        if raw is None:
            return None
        try:
            return BaselineManifest.model_validate_json(raw)
        except ValidationError as exc:
            raise StorageError(f"Corrupt BaselineManifest at {path}: {exc}") from exc

    async def get_rendering(
        self, project_id: str, tag: str, fmt: RenderFormat
    ) -> bytes | None:
        """Return a stored rendering, or None when it has not been produced."""
        return await self._read(self.rendering_path(project_id, tag, fmt))

    async def list_tags(self, project_id: str) -> tuple[str, ...]:
        """Return the tags taken in a project, alphabetically."""
        prefix = self.project_prefix(project_id)
        found = await self._storage.list_objects(prefix)
        return tuple(
            sorted(
                meta.path.removeprefix(prefix).removesuffix(".json")
                for meta in found
                if meta.path.endswith(".json")
                and "/" not in meta.path.removeprefix(prefix)
            )
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _read(self, path: str) -> bytes | None:
        try:
            return await self._storage.get_document(path)
        except FileNotFoundError:
            return None
