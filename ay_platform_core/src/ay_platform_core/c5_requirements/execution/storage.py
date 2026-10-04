# =============================================================================
# File: storage.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/execution/storage.py
# Description: MinIO persistence for treatment plans and reports —
#              310-SPEC §4.9 (R-310-175, R-310-176).
#
#              EVERY PLAN VERSION IS KEPT. R-310-173 lets a ratified plan
#              be amended mid-flight, and R-310-175 makes ratification an
#              attributable record against a specific version. If an
#              amendment overwrote its predecessor, the evidence of what
#              was actually approved would be destroyed by the act of
#              changing it — which is precisely the record a regulated
#              audit asks for. So versions are append-only and the current
#              plan is a separate object, the same shape as
#              `objects/storage.py`.
#
#              AND THERE IS NO DELETE API, for the same reason as DV-08: an
#              operation that can destroy an approval trail will eventually
#              be called.
#
# @relation implements:R-310-170
# @relation implements:R-310-173
# @relation implements:R-310-175
# @relation implements:R-310-176
# =============================================================================

from __future__ import annotations

import re

from pydantic import ValidationError

from ..storage.minio_storage import RequirementsStorage, StorageError
from .models import TreatmentPlan, TreatmentReport

_JSON = "application/json"

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLAN_RE = re.compile(r"^[A-Z]{2,4}-[A-Za-z0-9._-]{1,64}$")


class ExecutionPathError(ValueError):
    """Raised when a path component would escape the execution namespace."""


def _check(value: str, label: str, pattern: re.Pattern[str]) -> str:
    if not pattern.match(value):
        raise ExecutionPathError(f"Invalid {label} {value!r}")
    return value


class ExecutionStorage:
    """Source-of-truth persistence for treatment plans and their reports.

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
        """Return the prefix holding every plan of a project."""
        return f"projects/{_check(project_id, 'project id', _SCOPE_RE)}/plans/"

    @classmethod
    def plan_path(cls, project_id: str, plan_id: str) -> str:
        """Return the path of a plan's current version."""
        return (
            f"{cls.project_prefix(project_id)}"
            f"{_check(plan_id, 'plan id', _PLAN_RE)}.json"
        )

    @classmethod
    def version_path(cls, project_id: str, plan_id: str, version: int) -> str:
        """Return the path of one historical plan version."""
        if version < 1:
            raise ExecutionPathError(f"Invalid plan version {version!r}")
        return (
            f"{cls.project_prefix(project_id)}_versions/"
            f"{_check(plan_id, 'plan id', _PLAN_RE)}@v{version}.json"
        )

    @classmethod
    def report_path(cls, project_id: str, plan_id: str) -> str:
        """Return the path of a plan's treatment report."""
        return (
            f"{cls.project_prefix(project_id)}_reports/"
            f"{_check(plan_id, 'plan id', _PLAN_RE)}.json"
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def put_plan(self, plan: TreatmentPlan) -> None:
        """Write a plan, snapshotting the version first.

        The version snapshot is written BEFORE the current object so a
        failure between the two leaves a redundant snapshot rather than a
        current plan whose approved form was never recorded.
        """
        payload = plan.model_dump_json(indent=2).encode("utf-8")
        await self._storage.put_document(
            self.version_path(plan.project_id, plan.plan_id, plan.version),
            payload,
            _JSON,
        )
        await self._storage.put_document(
            self.plan_path(plan.project_id, plan.plan_id), payload, _JSON
        )

    async def put_report(self, report: TreatmentReport) -> None:
        """Write a plan's treatment report (`R-310-176`)."""
        await self._storage.put_document(
            self.report_path(report.project_id, report.plan_id),
            report.model_dump_json(indent=2).encode("utf-8"),
            _JSON,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_plan(
        self, project_id: str, plan_id: str
    ) -> TreatmentPlan | None:
        """Return a plan's current version, or None when absent."""
        path = self.plan_path(project_id, plan_id)
        raw = await self._read(path)
        return _parse_plan(raw, path) if raw is not None else None

    async def get_plan_version(
        self, project_id: str, plan_id: str, version: int
    ) -> TreatmentPlan | None:
        """Return one historical plan version, or None when absent.

        This is what makes a ratification auditable after an amendment:
        the approved terms are still readable at the version they were
        approved at (`R-310-175`).
        """
        path = self.version_path(project_id, plan_id, version)
        raw = await self._read(path)
        return _parse_plan(raw, path) if raw is not None else None

    async def get_report(
        self, project_id: str, plan_id: str
    ) -> TreatmentReport | None:
        """Return a plan's treatment report, or None when not yet produced."""
        path = self.report_path(project_id, plan_id)
        raw = await self._read(path)
        if raw is None:
            return None
        try:
            return TreatmentReport.model_validate_json(raw)
        except ValidationError as exc:
            raise StorageError(f"Corrupt TreatmentReport at {path}: {exc}") from exc

    async def list_plan_ids(self, project_id: str) -> tuple[str, ...]:
        """Return the identifiers of every live plan in a project."""
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

    async def list_versions(self, project_id: str, plan_id: str) -> tuple[int, ...]:
        """Return the versions stored for one plan, ascending."""
        prefix = f"{self.project_prefix(project_id)}_versions/"
        marker = f"{_check(plan_id, 'plan id', _PLAN_RE)}@v"
        found = await self._storage.list_objects(prefix)
        versions: list[int] = []
        for meta in found:
            name = meta.path.removeprefix(prefix)
            if not name.startswith(marker) or not name.endswith(".json"):
                continue
            versions.append(int(name[len(marker) : -len(".json")]))
        return tuple(sorted(versions))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _read(self, path: str) -> bytes | None:
        try:
            return await self._storage.get_document(path)
        except FileNotFoundError:
            return None


def _parse_plan(raw: bytes, path: str) -> TreatmentPlan:
    """Parse a stored plan, refusing a corrupt one loudly.

    Raises:
        StorageError: When the stored bytes are not a valid plan.
    """
    try:
        return TreatmentPlan.model_validate_json(raw)
    except ValidationError as exc:
        raise StorageError(f"Corrupt TreatmentPlan at {path}: {exc}") from exc
