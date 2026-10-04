# =============================================================================
# File: storage.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/intake/storage.py
# Description: MinIO persistence for supplied requirement drops — 310-SPEC
#              §4.4 / §4.5.
#
#              THE POINT OF THIS MODULE. Step 4.3 made an anchor resolvable
#              at extraction time; this one makes it stay resolvable. The
#              EXTRACTED DOCUMENT TEXT is stored beside the requirements, and
#              `get_requirement` re-verifies the anchor against it on every
#              read. An anchor that only resolved while the original file was
#              on somebody's disk is not provenance.
#
#              The original payload is kept too: a contract review asks what
#              the customer actually sent, and an extraction is our reading
#              of that, not the thing itself.
#
#              Supplied requirements are NEVER overwritten (R-310-090). They
#              are a contractual artefact belonging to their issuer; a new
#              drop that changes one creates a new drop, and the change is
#              detected by diffing drops (R-310-140), not by mutating the old.
#
# @relation implements:R-310-062
# @relation implements:R-310-090
# @relation implements:R-310-095
# =============================================================================

from __future__ import annotations

import re

from pydantic import ValidationError

from ..storage.minio_storage import RequirementsStorage, StorageError
from .extraction import ExtractionError
from .models import (
    Fragment,
    QualityFinding,
    ReworkRequest,
    SplitProposal,
    SuppliedRequirement,
)

_JSON = "application/json"
_TEXT = "text/plain; charset=utf-8"

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_CRITERION_RE = re.compile(r"^CRIT-[A-Z]{2,5}-[0-9]{3}$")


class IntakePathError(ValueError):
    """Raised when a path component would escape the intake namespace."""


class SuppliedRequirementImmutableError(RuntimeError):
    """Raised when a write would change a requirement as received (R-310-090)."""


def _check(value: str, label: str, pattern: re.Pattern[str]) -> str:
    if not pattern.match(value):
        raise IntakePathError(f"Invalid {label} {value!r}")
    return value


class IntakeStorage:
    """Source-of-truth persistence for drops, supplied requirements and splits.

    Args:
        storage: The shared C5 MinIO facade, composed rather than subclassed.
    """

    def __init__(self, storage: RequirementsStorage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @staticmethod
    def drop_prefix(project_id: str, drop_id: str) -> str:
        """Return the prefix holding everything about one supplied drop."""
        return (
            f"projects/{_check(project_id, 'project id', _SCOPE_RE)}/intake/"
            f"{_check(drop_id, 'drop id', _SCOPE_RE)}/"
        )

    @classmethod
    def payload_path(cls, project_id: str, drop_id: str, filename: str) -> str:
        """Return the path of the original file as the issuer sent it."""
        return (
            f"{cls.drop_prefix(project_id, drop_id)}source/"
            f"{_check(filename, 'file name', _SCOPE_RE)}"
        )

    @classmethod
    def extracted_text_path(cls, project_id: str, drop_id: str) -> str:
        """Return the path of the text every anchor in this drop indexes into."""
        return f"{cls.drop_prefix(project_id, drop_id)}extracted.txt"

    @classmethod
    def requirement_path(
        cls, project_id: str, drop_id: str, requirement_id: str
    ) -> str:
        """Return the path of one supplied requirement."""
        return (
            f"{cls.drop_prefix(project_id, drop_id)}requirements/"
            f"{_check(requirement_id, 'requirement id', _ID_RE)}.json"
        )

    @classmethod
    def finding_path(
        cls, project_id: str, drop_id: str, requirement_id: str, criterion_id: str
    ) -> str:
        """Return the path of one finding against one requirement.

        Keyed by criterion: a criterion either fails or it does not, so a
        later verdict replaces the earlier one rather than accumulating a
        history nobody reads.
        """
        return (
            f"{cls.drop_prefix(project_id, drop_id)}findings/"
            f"{_check(requirement_id, 'requirement id', _ID_RE)}@"
            f"{_check(criterion_id, 'criterion id', _CRITERION_RE)}.json"
        )

    @classmethod
    def rework_path(cls, project_id: str, drop_id: str, requirement_id: str) -> str:
        """Return the path of one rework request owed to the issuer."""
        return (
            f"{cls.drop_prefix(project_id, drop_id)}rework/"
            f"{_check(requirement_id, 'requirement id', _ID_RE)}.json"
        )

    @classmethod
    def split_path(cls, project_id: str, drop_id: str, requirement_id: str) -> str:
        """Return the path of one requirement's split proposal."""
        return (
            f"{cls.drop_prefix(project_id, drop_id)}splits/"
            f"{_check(requirement_id, 'requirement id', _ID_RE)}.json"
        )

    # ------------------------------------------------------------------
    # The drop
    # ------------------------------------------------------------------

    async def put_payload(
        self,
        project_id: str,
        drop_id: str,
        filename: str,
        payload: bytes,
        content_type: str,
    ) -> None:
        """Store the original file exactly as received.

        Kept because an extraction is OUR reading of what the customer sent,
        and a contract review asks about the thing itself.
        """
        await self._storage.put_document(
            self.payload_path(project_id, drop_id, filename), payload, content_type
        )

    async def put_extracted_text(
        self, project_id: str, drop_id: str, document_text: str
    ) -> None:
        """Store the text every anchor in this drop resolves against."""
        await self._storage.put_document(
            self.extracted_text_path(project_id, drop_id),
            document_text.encode("utf-8"),
            _TEXT,
        )

    async def get_extracted_text(self, project_id: str, drop_id: str) -> str:
        """Return the stored extraction text.

        Raises:
            FileNotFoundError: When the drop has no stored extraction.
        """
        raw = await self._storage.get_document(
            self.extracted_text_path(project_id, drop_id)
        )
        return raw.decode("utf-8")

    # ------------------------------------------------------------------
    # Supplied requirements — R-310-090
    # ------------------------------------------------------------------

    async def put_requirement(
        self, requirement: SuppliedRequirement, *, allow_update: bool = False
    ) -> None:
        """Store a supplied requirement, refusing to change one already stored.

        Args:
            requirement: The requirement as received.
            allow_update: Permitted only for the human verification of a
                degraded source (R-310-061), which confirms the extracted
                text rather than changing what the issuer wrote.

        Raises:
            SuppliedRequirementImmutableError: When the stored text differs
                and this is not a verification.
        """
        path = self.requirement_path(
            requirement.project_id, requirement.drop_id, requirement.requirement_id
        )
        existing = await self._read(path)
        if existing is not None:
            stored = _parse_requirement(existing, path)
            if not allow_update and stored.text != requirement.text:
                raise SuppliedRequirementImmutableError(
                    f"{requirement.requirement_id!r} is already stored with "
                    "different text; a supplied requirement is a contractual "
                    "artefact and is never rewritten (R-310-090) — a changed "
                    "requirement arrives as a new drop"
                )
        await self._storage.put_document(
            path, requirement.model_dump_json().encode("utf-8"), _JSON
        )

    async def get_requirement(
        self, project_id: str, drop_id: str, requirement_id: str, *, verify: bool = True
    ) -> SuppliedRequirement:
        """Read a supplied requirement, re-verifying its anchor by default.

        Args:
            project_id: Owning project.
            drop_id: The drop it arrived in.
            requirement_id: The issuer's identifier.
            verify: When True, confirm the anchor still resolves against the
                stored extraction. The default is True because an anchor that
                is not checked is a claim, not provenance.

        Raises:
            FileNotFoundError: When no such requirement is stored.
            ExtractionError: When the anchor no longer resolves.
        """
        path = self.requirement_path(project_id, drop_id, requirement_id)
        requirement = _parse_requirement(
            await self._storage.get_document(path), path
        )
        if verify:
            await self._verify_anchor(requirement)
        return requirement

    async def _verify_anchor(self, requirement: SuppliedRequirement) -> None:
        try:
            document_text = await self.get_extracted_text(
                requirement.project_id, requirement.drop_id
            )
        except FileNotFoundError as exc:
            raise ExtractionError(
                f"drop {requirement.drop_id!r} has no stored extraction, so "
                f"{requirement.requirement_id!r}'s anchor cannot be resolved "
                "(R-310-062)"
            ) from exc
        actual = requirement.anchor.interval.slice_of(document_text)
        if actual != requirement.text:
            raise ExtractionError(
                f"{requirement.requirement_id!r}'s anchor no longer resolves: "
                f"{requirement.anchor.location} holds {actual[:40]!r} but the "
                f"requirement says {requirement.text[:40]!r} (R-310-062)"
            )

    async def list_requirement_ids(
        self, project_id: str, drop_id: str
    ) -> list[str]:
        """Return the identifiers stored for one drop, sorted."""
        prefix = f"{self.drop_prefix(project_id, drop_id)}requirements/"
        ids: list[str] = []
        for meta in await self._storage.list_objects(prefix):
            tail = meta.path[len(prefix) :]
            if tail.endswith(".json") and "/" not in tail:
                ids.append(tail.removesuffix(".json"))
        return sorted(ids)

    # ------------------------------------------------------------------
    # Quality findings — R-310-063
    # ------------------------------------------------------------------

    async def put_finding(
        self, project_id: str, drop_id: str, finding: QualityFinding
    ) -> None:
        """Store one quality finding."""
        await self._storage.put_document(
            self.finding_path(
                project_id, drop_id, finding.requirement_id, finding.criterion_id
            ),
            finding.model_dump_json().encode("utf-8"),
            _JSON,
        )

    async def list_findings(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> tuple[QualityFinding, ...]:
        """Return every finding recorded against one requirement."""
        prefix = f"{self.drop_prefix(project_id, drop_id)}findings/"
        stem = f"{_check(requirement_id, 'requirement id', _ID_RE)}@"
        findings: list[QualityFinding] = []
        for meta in await self._storage.list_objects(prefix + stem):
            raw = await self._read(meta.path)
            if raw is None:  # pragma: no cover - listed then vanished
                continue
            try:
                findings.append(QualityFinding.model_validate_json(raw))
            except ValidationError as exc:
                raise StorageError(
                    f"Corrupt QualityFinding at {meta.path}: {exc}"
                ) from exc
        return tuple(sorted(findings, key=lambda f: f.criterion_id))

    # ------------------------------------------------------------------
    # Rework owed to the issuer — R-310-094
    # ------------------------------------------------------------------

    async def put_rework(self, request: ReworkRequest) -> None:
        """Store one rework request owed to the issuing party."""
        await self._storage.put_document(
            self.rework_path(
                request.project_id, request.drop_id, request.requirement_id
            ),
            request.model_dump_json().encode("utf-8"),
            _JSON,
        )

    async def list_rework(
        self, project_id: str, drop_id: str
    ) -> tuple[ReworkRequest, ...]:
        """Return every rework request this drop owes its issuer."""
        prefix = f"{self.drop_prefix(project_id, drop_id)}rework/"
        requests: list[ReworkRequest] = []
        for meta in await self._storage.list_objects(prefix):
            raw = await self._read(meta.path)
            if raw is None:  # pragma: no cover
                continue
            try:
                requests.append(ReworkRequest.model_validate_json(raw))
            except ValidationError as exc:
                raise StorageError(
                    f"Corrupt ReworkRequest at {meta.path}: {exc}"
                ) from exc
        return tuple(sorted(requests, key=lambda r: r.requirement_id))

    # ------------------------------------------------------------------
    # Splits — R-310-090 / R-310-092
    # ------------------------------------------------------------------

    async def put_split(self, proposal: SplitProposal) -> None:
        """Store a split proposal.

        The proposal's own validator has already established that it tiles
        the source and is justified by a failed atomicity check, so an
        unstorable split cannot exist in the first place.
        """
        await self._storage.put_document(
            self.split_path(
                proposal.project_id, proposal.drop_id, proposal.parent_id
            ),
            proposal.model_dump_json().encode("utf-8"),
            _JSON,
        )

    async def get_split(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> SplitProposal | None:
        """Return a requirement's split proposal, or None."""
        path = self.split_path(project_id, drop_id, requirement_id)
        raw = await self._read(path)
        if raw is None:
            return None
        try:
            return SplitProposal.model_validate_json(raw)
        except ValidationError as exc:
            raise StorageError(f"Corrupt SplitProposal at {path}: {exc}") from exc

    async def list_fragments(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> tuple[Fragment, ...]:
        """Return a requirement's fragments, or an empty tuple when unsplit."""
        proposal = await self.get_split(project_id, drop_id, requirement_id)
        return proposal.fragments if proposal else ()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _read(self, path: str) -> bytes | None:
        try:
            return await self._storage.get_document(path)
        except FileNotFoundError:
            return None


def _parse_requirement(raw: bytes, path: str) -> SuppliedRequirement:
    try:
        return SuppliedRequirement.model_validate_json(raw)
    except ValidationError as exc:
        raise StorageError(
            f"Corrupt SuppliedRequirement at {path}: {exc}"
        ) from exc
