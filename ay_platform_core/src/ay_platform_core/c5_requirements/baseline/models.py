# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/baseline/models.py
# Description: The baseline manifest — 310-SPEC §4.11 (R-310-200, R-310-201),
#              E-310-006's `req_baselines`.
#
#              A MANIFEST NAMES CONTENT, IT NEVER COPIES IT. `R-310-200`
#              says "SHALL NOT duplicate object content", and the reason is
#              not storage: a manifest holding its own copy of a paragraph
#              creates a SECOND answer to "what did this baseline contain?",
#              and the two will disagree the first time one is migrated or
#              re-encoded. So an entry carries an object id, a version and a
#              content hash — and nothing else about the content. The hash
#              is what makes the reference verifiable rather than merely
#              hopeful: reading a baseline re-hashes what it resolves and
#              refuses a mismatch.
#
#              A `content` FIELD IS STRUCTURALLY ABSENT, not merely unused.
#              A test asserts its absence, because the natural thing for a
#              future renderer to want is "just inline the text so the
#              export needs one read" — which is exactly the duplication the
#              requirement forbids.
#
#              THE TAG IS IMMUTABLE AND THE MANIFEST IS WRITE-ONCE. A
#              baseline is the artefact an audit reconstructs
#              (`R-310-204`); one that could be edited after the fact would
#              answer a question about the past with a statement about the
#              present.
#
# @relation implements:R-310-200
# @relation implements:R-310-201
# @relation implements:R-310-204
# =============================================================================

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: A baseline tag: human-chosen, so constrained enough to be a path segment
#: and a document title at once.
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def content_hash(kind: str, content: str) -> str:
    """Return the canonical content hash of an object's renderable text.

    SHA-256 over `<kind>\x00<content>` in UTF-8, prefixed so the algorithm
    is readable in the stored manifest. An unprefixed digest is
    indistinguishable from a digest of another algorithm, which matters the
    day one is replaced.

    The KIND is folded in because `R-310-008` makes prose and figures
    exclusive — a paragraph carries `body`, a figure carries `notation` —
    and hashing the text alone would let a paragraph and a figure with the
    same string collide. Hashing an empty string for every figure, which
    an earlier draft of this did, would have collided ALL of them.

    Args:
        kind: `"body"` for prose, `"notation"` for a figure.
        content: The renderable text.
    """
    payload = f"{kind}\x00{content}".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class BaselineBlocker(StrEnum):
    """Why a baseline cannot be created (`R-310-201`)."""

    OPEN_CHANGE_TICKET = "open-change-ticket"
    CRITICAL_COVERAGE_GAP = "critical-coverage-gap"
    STALE_COVERAGE_LINK = "stale-coverage-link"


class ManifestObject(BaseModel):
    """One object version included in a baseline.

    Named `ManifestObject` rather than `ObjectEntry` because C7's MinIO
    storage already owns that name for a listing entry — two unrelated
    concepts under one name is a real cost, and the newcomer yields.
    `ManifestObject` / `ManifestLink` also match the manifest's own
    `objects` / `links` fields, which reads better than either.

    Note what is NOT here: the object's text. See the module docstring —
    the absence is the requirement, and `test_a_manifest_entry_cannot
    _carry_content` pins it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: str = Field(min_length=1)
    container: str = Field(min_length=1)
    version: int = Field(ge=1)
    ordinal: int = Field(ge=0)
    review_state: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ManifestLink(BaseModel):
    """One coverage link included in a baseline, with its pinned version.

    The pin is what makes a baseline reproducible: "this object answered
    THAT version of that requirement" is the claim an audit checks, and
    without the pinned version the claim degrades to "answered it at some
    point".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: str = Field(min_length=1)
    container: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    pinned_version: int = Field(ge=1)
    strength: str = Field(min_length=1)
    state: str = Field(min_length=1)


class BaselineManifest(BaseModel):
    """A named, immutable photograph of the corpus (`R-310-200`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tag: str
    project_id: str = Field(min_length=1)
    cycle_id: str = Field(min_length=1)
    cycle_version: int = Field(ge=1)
    created_by: str = Field(min_length=1)
    created_at: datetime
    objects: tuple[ManifestObject, ...] = ()
    links: tuple[ManifestLink, ...] = ()
    note: str = Field(default="", max_length=2000)

    @field_validator("tag")
    @classmethod
    def _valid_tag(cls, value: str) -> str:
        if not _TAG_RE.match(value):
            raise ValueError(
                f"Invalid baseline tag {value!r}: a tag is both a path segment "
                "and a document title, so it is limited to letters, digits, "
                "dot, dash and underscore"
            )
        return value

    @model_validator(mode="after")
    def _is_coherent(self) -> BaselineManifest:
        """Refuse a manifest that could not describe a real corpus."""
        keys = [(entry.object_id, entry.version) for entry in self.objects]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "an object version appears twice in the manifest; a baseline "
                "names each version once, and a duplicate would make "
                "'what did this contain?' ambiguous"
            )
        by_object = [entry.object_id for entry in self.objects]
        if len(set(by_object)) != len(by_object):
            raise ValueError(
                "two versions of the same object are in one baseline; a "
                "photograph holds one state per object (R-310-200)"
            )
        known = {entry.object_id for entry in self.objects}
        for link in self.links:
            if link.object_id not in known:
                raise ValueError(
                    f"link from {link.object_id!r} names an object the baseline "
                    "does not include; the pin would resolve to nothing"
                )
        return self

    @property
    def object_count(self) -> int:
        """How many object versions this baseline names."""
        return len(self.objects)

    @property
    def link_count(self) -> int:
        """How many coverage pins this baseline names."""
        return len(self.links)

    @property
    def containers(self) -> tuple[str, ...]:
        """The containers represented, in first-seen order of ordinal."""
        seen: dict[str, int] = {}
        for entry in self.objects:
            seen.setdefault(entry.container, entry.ordinal)
        return tuple(sorted(seen, key=lambda name: (seen[name], name)))

    def entries_of(self, container: str) -> tuple[ManifestObject, ...]:
        """Return one container's entries in document order."""
        return tuple(
            sorted(
                (e for e in self.objects if e.container == container),
                key=lambda entry: (entry.ordinal, entry.object_id),
            )
        )

    def entry(self, object_id: str) -> ManifestObject:
        """Return one entry.

        Raises:
            KeyError: When the baseline does not name that object.
        """
        for item in self.objects:
            if item.object_id == object_id:
                return item
        raise KeyError(f"{object_id!r} is not in baseline {self.tag!r}")

    def links_of(self, object_id: str) -> tuple[ManifestLink, ...]:
        """Return the coverage pins recorded for one object."""
        return tuple(link for link in self.links if link.object_id == object_id)


class BaselineRefusal(BaseModel):
    """One reason a baseline cannot be created, naming what failed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocker: BaselineBlocker
    subject: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class BaselineReadiness(BaseModel):
    """The verdict of the creation gate (`R-310-201`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = Field(min_length=1)
    refusals: tuple[BaselineRefusal, ...] = ()

    @property
    def is_ready(self) -> bool:
        """True when nothing blocks a baseline."""
        return not self.refusals

    @property
    def blockers(self) -> frozenset[BaselineBlocker]:
        """The distinct kinds of blocker standing in the way."""
        return frozenset(refusal.blocker for refusal in self.refusals)

    def explain(self) -> str:
        """Return a reader-facing account of why a baseline was refused."""
        if self.is_ready:
            return f"{self.project_id}: ready to baseline"
        lines = [f"{self.project_id}: cannot baseline"]
        lines.extend(
            f"  - {refusal.blocker.value} [{refusal.subject}]: {refusal.detail}"
            for refusal in self.refusals
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# REST bodies
# ---------------------------------------------------------------------------


class CreateBaselineRequest(BaseModel):
    """Take a baseline of the corpus as it stands."""

    model_config = ConfigDict(extra="forbid")

    cycle_id: str = Field(min_length=1)
    note: str = Field(default="", max_length=2000)


class BaselineSummary(BaseModel):
    """A baseline as the listing shows it, without its entries."""

    model_config = ConfigDict(extra="forbid")

    tag: str
    project_id: str
    cycle_id: str
    cycle_version: int
    created_by: str
    created_at: datetime
    object_count: int = Field(ge=0)
    link_count: int = Field(ge=0)
    note: str = ""


class BaselineListResponse(BaseModel):
    """A page of baselines."""

    model_config = ConfigDict(extra="forbid")

    baselines: tuple[BaselineSummary, ...]
    count: int = Field(ge=0)


class RenderFormat(StrEnum):
    """The formats `R-310-207` requires."""

    DOCX = "docx"
    PDF = "pdf"
