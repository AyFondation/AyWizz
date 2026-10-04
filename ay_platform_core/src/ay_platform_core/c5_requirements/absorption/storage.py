# =============================================================================
# File: storage.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/absorption/storage.py
# Description: MinIO persistence for change tickets — 310-SPEC §4.8.
#
#              A TICKET IS WRITTEN WHOLE, AND NEVER DELETED. Its content —
#              impact set, qualifications, dispositions — only ever grows,
#              and a closed ticket is the evidence that a supplied
#              modification was absorbed end to end (`R-310-150`). So this
#              module has no delete API at all, for the same reason
#              `objects/storage.py` has none: an operation that cannot be
#              requested cannot be requested by mistake.
#
#              EACH CLOSURE IS SNAPSHOTTED. Writing the closed form beside
#              the live one costs a few kilobytes and buys the ability to
#              answer "what did this ticket look like when it was closed?"
#              after the graph has moved on — which is the question a
#              regulatory audit actually asks, since the impact set of a
#              reopened ticket will not match the one that was signed off.
#
# @relation implements:R-310-140
# @relation implements:R-310-146
# @relation implements:R-310-150
# =============================================================================

from __future__ import annotations

import re

from pydantic import ValidationError

from ..storage.minio_storage import RequirementsStorage, StorageError
from .models import ChangeTicket

_JSON = "application/json"

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")


class AbsorptionPathError(ValueError):
    """Raised when a path component would escape the absorption namespace."""


def _check(value: str, label: str, pattern: re.Pattern[str]) -> str:
    if not pattern.match(value):
        raise AbsorptionPathError(f"Invalid {label} {value!r}")
    return value


class AbsorptionStorage:
    """Source-of-truth persistence for change tickets.

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
        """Return the prefix holding every change ticket of a project."""
        return f"projects/{_check(project_id, 'project id', _SCOPE_RE)}/changes/"

    @classmethod
    def ticket_path(cls, project_id: str, drop_id: str, requirement_id: str) -> str:
        """Return the path of one change ticket.

        Keyed by drop and requirement rather than by a minted id, so the
        path itself enforces `R-310-140`'s "exactly one ticket per
        requirement": a second open for the same pair overwrites rather
        than multiplying.
        """
        return (
            f"{cls.project_prefix(project_id)}"
            f"{_check(drop_id, 'drop id', _SCOPE_RE)}/"
            f"{_check(requirement_id, 'requirement id', _ID_RE)}.json"
        )

    @classmethod
    def closure_path(cls, project_id: str, drop_id: str, requirement_id: str) -> str:
        """Return the path of a ticket's closure snapshot."""
        return (
            f"{cls.project_prefix(project_id)}_closed/"
            f"{_check(drop_id, 'drop id', _SCOPE_RE)}/"
            f"{_check(requirement_id, 'requirement id', _ID_RE)}.json"
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def put_ticket(self, ticket: ChangeTicket) -> None:
        """Write a ticket, snapshotting it first when it closes.

        The snapshot is written BEFORE the live object so a failure
        between the two leaves a redundant snapshot rather than a closure
        with no record of what was closed — the same ordering rule as
        `objects/storage.py`.
        """
        path = self.ticket_path(
            ticket.project_id, ticket.drop_id, ticket.requirement_id
        )
        payload = ticket.model_dump_json(indent=2).encode("utf-8")
        if ticket.closed_at is not None:
            await self._storage.put_document(
                self.closure_path(
                    ticket.project_id, ticket.drop_id, ticket.requirement_id
                ),
                payload,
                _JSON,
            )
        await self._storage.put_document(path, payload, _JSON)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_ticket(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> ChangeTicket | None:
        """Return one ticket, or None when none was opened."""
        path = self.ticket_path(project_id, drop_id, requirement_id)
        raw = await self._read(path)
        return _parse(raw, path) if raw is not None else None

    async def get_closure_snapshot(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> ChangeTicket | None:
        """Return a ticket as it stood when closed, or None."""
        path = self.closure_path(project_id, drop_id, requirement_id)
        raw = await self._read(path)
        return _parse(raw, path) if raw is not None else None

    async def list_ticket_paths(self, project_id: str) -> tuple[str, ...]:
        """Return the paths of every live ticket in a project.

        The index (`req_changes`) is the query surface; this exists so the
        index can be rebuilt from the source of truth (`R-310-002`).
        """
        prefix = self.project_prefix(project_id)
        found = await self._storage.list_objects(prefix)
        return tuple(
            sorted(
                meta.path
                for meta in found
                if meta.path.endswith(".json")
                and not meta.path.startswith(f"{prefix}_closed/")
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


def _parse(raw: bytes, path: str) -> ChangeTicket:
    """Parse a stored ticket, refusing a corrupt one loudly.

    Raises:
        StorageError: When the stored bytes are not a valid ticket.
    """
    try:
        return ChangeTicket.model_validate_json(raw)
    except ValidationError as exc:
        raise StorageError(f"Corrupt ChangeTicket at {path}: {exc}") from exc
