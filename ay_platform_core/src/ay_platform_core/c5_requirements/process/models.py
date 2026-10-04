# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/process/models.py
# Description: Pydantic v2 contracts for the engineering cycle (`C-`) and the
#              workflow (`WF-`) — 310-SPEC-DOC-TRACEABILITY §4.2 / §4.3,
#              E-310-002 / E-310-003.
#
#              Three invariants are structural here, not left to the service:
#
#              1. A workflow with an empty `checks` list cannot reach
#                 `approved` (R-310-041). Without machine-checkable
#                 acceptance criteria an activity is a named prompt: not
#                 verifiable, so not capitalisable, and it rots in silence.
#
#              2. Control flow is an ordered sequence of typed steps plus,
#                 on a human-gate only, a return target naming an EARLIER
#                 step (R-310-042, R-310-043). There is no field in which a
#                 conditional, a loop or a variable could be expressed — the
#                 absence is the enforcement. A workflow language that grows
#                 branches becomes an unauditable scheduler nobody authors.
#
#              3. A tailored entity carries a rationale (R-310-022,
#                 R-310-046). An override without a stated reason is
#                 indistinguishable from a mistake six months later.
#
# @relation implements:R-310-020
# @relation implements:R-310-021
# @relation implements:R-310-022
# @relation implements:R-310-040
# @relation implements:R-310-041
# @relation implements:R-310-042
# @relation implements:R-310-043
# @relation implements:R-310-046
# =============================================================================

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

_CYCLE_ID_RE = re.compile(r"^C-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
_WORKFLOW_ID_RE = re.compile(r"^WF-[0-9]{3,}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SCOPE_ID_RE = re.compile(r"^SC-[0-9]{3,}$")
_STEP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_CHECK_ID_RE = re.compile(r"^CRIT-[A-Z]{2,5}-[0-9]{3}$")


def is_valid_cycle_id(value: str) -> bool:
    """Return True for a well-formed cycle identifier such as `C-AUTOMOTIVE`."""
    return len(value) <= 64 and _CYCLE_ID_RE.match(value) is not None


def is_valid_workflow_id(value: str) -> bool:
    """Return True for a well-formed workflow identifier such as `WF-002`."""
    return len(value) <= 16 and _WORKFLOW_ID_RE.match(value) is not None


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EntityStatus(StrEnum):
    """Publication lifecycle of a `C-` or `WF-` entity (R-310-020, R-310-040)."""

    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class LinkKind(StrEnum):
    """Edge kinds a cycle may permit between containers (R-310-021)."""

    COVERS = "covers"
    DERIVES_FROM = "derives-from"


class StepKind(StrEnum):
    """The only three step kinds (R-310-042).

    Adding a fourth would require a spec amendment — the closed set is what
    keeps a workflow readable by a reviewer who is not its author.
    """

    AGENT = "agent"
    CHECK = "check"
    HUMAN_GATE = "human-gate"


# ---------------------------------------------------------------------------
# Shared publication behaviour
# ---------------------------------------------------------------------------


class _Publishable(BaseModel):
    """Fields and rules shared by `C-` and `WF-` entities."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    status: EntityStatus = EntityStatus.DRAFT
    tenant_id: str
    project_id: str | None = None
    tailoring_of: str | None = None
    tailoring_rationale: str | None = None
    created_at: datetime
    created_by: str
    updated_at: datetime
    updated_by: str

    @model_validator(mode="after")
    def _tailoring_needs_a_project_and_a_reason(self) -> _Publishable:
        # R-310-022 / R-310-046: tailoring happens at project level and states
        # why. An override with no stated reason cannot be reviewed, and six
        # months later is indistinguishable from an accident.
        if self.tailoring_of is not None:
            if self.project_id is None:
                raise ValueError(
                    "a tailored entity SHALL be project-scoped (R-310-022)"
                )
            if not (self.tailoring_rationale or "").strip():
                raise ValueError(
                    "tailoring SHALL carry a rationale (R-310-046)"
                )
        elif self.tailoring_rationale is not None:
            raise ValueError(
                "tailoring_rationale is meaningless without tailoring_of"
            )
        return self


# ---------------------------------------------------------------------------
# Cycle — E-310-002
# ---------------------------------------------------------------------------


class ContainerSpec(BaseModel):
    """One document type declared by a cycle (R-310-021)."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    ordinal: int = Field(ge=0)
    scope_id: str
    scope: str
    coverage_obligatory: bool = True
    links_out: tuple[LinkKind, ...] = ()
    links_in: tuple[LinkKind, ...] = ()
    workflow_id: str | None = None
    """The activity that produces this container (R-310-025).

    Optional on the cycle: a container may legitimately have no automated
    activity. The precondition binds when an activity STARTS, not when the
    cycle is published — a cycle that declares a container nobody automates
    is a valid cycle.
    """

    @field_validator("workflow_id")
    @classmethod
    def _valid_workflow_binding(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not is_valid_workflow_id(v):
            raise ValueError(
                f"Invalid workflow binding {v!r}: expected 'WF-002' form"
            )
        return v

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(f"Invalid container slug {v!r}")
        return v

    @field_validator("scope_id")
    @classmethod
    def _valid_scope_id(cls, v: str) -> str:
        if not _SCOPE_ID_RE.match(v):
            raise ValueError(f"Invalid scope_id {v!r}: expected 'SC-001' form")
        return v

    @field_validator("scope")
    @classmethod
    def _scope_is_substantive(cls, v: str) -> str:
        # The scope statement is the allocation prompt (R-310-066): an agent
        # cites it to justify every allocation, and a document owner cites it
        # to refuse one. A blank or token scope makes both impossible.
        if len(v.strip()) < 20:
            raise ValueError(
                "a container scope SHALL be a substantive statement — it is "
                "what an allocation cites (R-310-021, R-310-066)"
            )
        return v


class CycleDefinition(_Publishable):
    """The engineering process: containers and their permitted links."""

    cycle_id: str
    title: str
    containers: tuple[ContainerSpec, ...]

    @field_validator("cycle_id")
    @classmethod
    def _valid_cycle_id(cls, v: str) -> str:
        if not is_valid_cycle_id(v):
            raise ValueError(f"Invalid cycle id {v!r}: expected 'C-AUTOMOTIVE' form")
        return v

    @model_validator(mode="after")
    def _containers_are_coherent(self) -> CycleDefinition:
        if not self.containers:
            raise ValueError("a cycle SHALL declare at least one container")

        slugs = [c.slug for c in self.containers]
        if len(set(slugs)) != len(slugs):
            raise ValueError("container slugs SHALL be unique within a cycle")

        scope_ids = [c.scope_id for c in self.containers]
        if len(set(scope_ids)) != len(scope_ids):
            # An allocation cites a scope_id (R-310-066); a duplicate would
            # make the citation ambiguous, which defeats the point.
            raise ValueError("scope_ids SHALL be unique within a cycle")

        ordinals = [c.ordinal for c in self.containers]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError(
                "container ordinals SHALL be unique — they define reading order"
            )
        return self

    def container(self, slug: str) -> ContainerSpec | None:
        """Return the container with this slug, or None.

        Args:
            slug: Container slug.

        Returns:
            The declared container, or None when the cycle does not have one.
        """
        for spec in self.containers:
            if spec.slug == slug:
                return spec
        return None

    @property
    def ordered_containers(self) -> tuple[ContainerSpec, ...]:
        """Containers in reading order."""
        return tuple(sorted(self.containers, key=lambda c: c.ordinal))


# ---------------------------------------------------------------------------
# Workflow — E-310-003
# ---------------------------------------------------------------------------


class WorkflowCheck(BaseModel):
    """One machine-checkable acceptance criterion (R-310-041)."""

    model_config = ConfigDict(extra="forbid")

    check_id: str
    statement: str

    @field_validator("check_id")
    @classmethod
    def _valid_check_id(cls, v: str) -> str:
        if not _CHECK_ID_RE.match(v):
            raise ValueError(
                f"Invalid check id {v!r}: expected 'CRIT-COV-001' form "
                "(E-310-005)"
            )
        return v

    @field_validator("statement")
    @classmethod
    def _statement_is_substantive(cls, v: str) -> str:
        if len(v.strip()) < 15:
            raise ValueError("a check SHALL state what it asserts, not a label")
        return v


class WorkflowStep(BaseModel):
    """One step of a workflow (R-310-042, R-310-043).

    There is deliberately no `condition`, `loop`, `when` or `variables`
    field. Control flow is the declared order plus, on a human-gate, a
    return target. Anything richer becomes a scheduler nobody can audit.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str
    kind: StepKind
    name: str = ""
    role: str | None = None
    action: str | None = None
    checks: tuple[str, ...] = ()
    on_reject: str | None = None
    reject_scope: str | None = None

    @field_validator("step_id", "on_reject")
    @classmethod
    def _valid_step_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _STEP_ID_RE.match(v):
            raise ValueError(f"Invalid step id {v!r}: expected lowercase snake_case")
        return v

    @model_validator(mode="after")
    def _kind_determines_the_shape(self) -> WorkflowStep:
        if self.kind is StepKind.AGENT:
            if not (self.role or "").strip() or not (self.action or "").strip():
                raise ValueError("an agent step SHALL declare a role and an action")
        elif self.kind is StepKind.CHECK:
            if not self.checks:
                raise ValueError("a check step SHALL reference at least one check")
            if self.role is not None or self.action is not None:
                raise ValueError("a check step SHALL NOT declare a role or action")
        elif self.role is not None or self.action is not None:
            raise ValueError("a human-gate SHALL NOT declare a role or action")

        # R-310-043: the return target is the ONLY backward edge, and it
        # belongs to a human gate. Allowing it elsewhere would make an agent
        # step able to loop on itself without a human ever seeing the result.
        if self.on_reject is not None and self.kind is not StepKind.HUMAN_GATE:
            raise ValueError(
                "only a human-gate may declare a return target (R-310-043)"
            )
        if self.reject_scope is not None and self.kind is not StepKind.HUMAN_GATE:
            raise ValueError("reject_scope belongs to a human-gate step")
        return self


class WorkflowDefinition(_Publishable):
    """An activity: what it does, how, and how its output is checked."""

    workflow_id: str
    intent: str
    inputs: dict[str, str] = Field(default_factory=dict)
    steps: tuple[WorkflowStep, ...]
    constraints: tuple[str, ...] = ()
    outputs: dict[str, str] = Field(default_factory=dict)
    checks: tuple[WorkflowCheck, ...] = ()
    examples: tuple[str, ...] = ()

    @field_validator("workflow_id")
    @classmethod
    def _valid_workflow_id(cls, v: str) -> str:
        if not is_valid_workflow_id(v):
            raise ValueError(f"Invalid workflow id {v!r}: expected 'WF-002' form")
        return v

    @field_validator("intent")
    @classmethod
    def _intent_is_substantive(cls, v: str) -> str:
        if len(v.strip()) < 20:
            raise ValueError("a workflow SHALL state what it accomplishes")
        return v

    @model_validator(mode="after")
    def _steps_and_checks_are_coherent(self) -> WorkflowDefinition:
        if not self.steps:
            raise ValueError("a workflow SHALL declare at least one step")

        ids = [s.step_id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("step ids SHALL be unique within a workflow")

        position = {sid: i for i, sid in enumerate(ids)}
        for index, step in enumerate(self.steps):
            if step.on_reject is None:
                continue
            target = position.get(step.on_reject)
            if target is None:
                raise ValueError(
                    f"step {step.step_id!r} returns to unknown step "
                    f"{step.on_reject!r}"
                )
            if target >= index:
                # A forward or self return is a loop in disguise, which
                # R-310-043 exists to exclude.
                raise ValueError(
                    f"step {step.step_id!r} SHALL return to an EARLIER step "
                    "(R-310-043)"
                )

        declared = {c.check_id for c in self.checks}
        for step in self.steps:
            unknown = set(step.checks) - declared
            if unknown:
                raise ValueError(
                    f"step {step.step_id!r} references undeclared checks: "
                    f"{sorted(unknown)}"
                )

        # R-310-041: the publication gate. A draft may be incomplete while it
        # is being written; an approved workflow without acceptance criteria
        # would be a named prompt masquerading as an engineering activity.
        if self.status is EntityStatus.APPROVED and not self.checks:
            raise ValueError(
                "a workflow with no checks SHALL NOT be approved (R-310-041)"
            )
        return self


# ---------------------------------------------------------------------------
# Public projections
# ---------------------------------------------------------------------------


class CyclePublic(BaseModel):
    """Cycle as exposed through the REST API."""

    model_config = ConfigDict(extra="forbid")

    cycle_id: str
    title: str
    version: int
    status: EntityStatus
    project_id: str | None = None
    tailoring_of: str | None = None
    tailoring_rationale: str | None = None
    containers: tuple[ContainerSpec, ...] = ()
    updated_at: datetime
    updated_by: str

    @classmethod
    def from_definition(cls, definition: CycleDefinition) -> CyclePublic:
        """Project a stored cycle onto the API contract."""
        return cls(
            cycle_id=definition.cycle_id,
            title=definition.title,
            version=definition.version,
            status=definition.status,
            project_id=definition.project_id,
            tailoring_of=definition.tailoring_of,
            tailoring_rationale=definition.tailoring_rationale,
            containers=definition.ordered_containers,
            updated_at=definition.updated_at,
            updated_by=definition.updated_by,
        )


class WorkflowPublic(BaseModel):
    """Workflow as exposed through the REST API."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    intent: str
    version: int
    status: EntityStatus
    project_id: str | None = None
    tailoring_of: str | None = None
    tailoring_rationale: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    steps: tuple[WorkflowStep, ...] = ()
    constraints: tuple[str, ...] = ()
    outputs: dict[str, str] = Field(default_factory=dict)
    checks: tuple[WorkflowCheck, ...] = ()
    examples: tuple[str, ...] = ()
    updated_at: datetime
    updated_by: str

    @classmethod
    def from_definition(cls, definition: WorkflowDefinition) -> WorkflowPublic:
        """Project a stored workflow onto the API contract."""
        return cls(
            workflow_id=definition.workflow_id,
            intent=definition.intent,
            version=definition.version,
            status=definition.status,
            project_id=definition.project_id,
            tailoring_of=definition.tailoring_of,
            tailoring_rationale=definition.tailoring_rationale,
            inputs=definition.inputs,
            steps=definition.steps,
            constraints=definition.constraints,
            outputs=definition.outputs,
            checks=definition.checks,
            examples=definition.examples,
            updated_at=definition.updated_at,
            updated_by=definition.updated_by,
        )


# ---------------------------------------------------------------------------
# Authoring request bodies — R-310-047
# ---------------------------------------------------------------------------


class _TailoringFields(BaseModel):
    """Tailoring declaration shared by the project-scope create bodies."""

    model_config = ConfigDict(extra="forbid")

    tailoring_of: str | None = None
    tailoring_rationale: str | None = None


class CycleDraftCreate(_TailoringFields):
    """Body for starting a new cycle draft.

    The version is assigned by the service (the next free one at this
    scope), never by the caller: letting a client choose would allow it to
    aim at a published slot.
    """

    cycle_id: str
    title: str
    containers: tuple[ContainerSpec, ...]

    @field_validator("cycle_id")
    @classmethod
    def _valid_cycle_id(cls, v: str) -> str:
        if not is_valid_cycle_id(v):
            raise ValueError(f"Invalid cycle id {v!r}: expected 'C-AUTOMOTIVE' form")
        return v


class CycleDraftUpdate(BaseModel):
    """Body for replacing the content of a cycle draft."""

    model_config = ConfigDict(extra="forbid")

    title: str
    containers: tuple[ContainerSpec, ...]


class WorkflowDraftCreate(_TailoringFields):
    """Body for starting a new workflow draft.

    `checks` may be empty here — a draft is being written. The publication
    gate of R-310-041 binds at publish time, not at creation.
    """

    workflow_id: str
    intent: str
    steps: tuple[WorkflowStep, ...]
    checks: tuple[WorkflowCheck, ...] = ()
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()

    @field_validator("workflow_id")
    @classmethod
    def _valid_workflow_id(cls, v: str) -> str:
        if not is_valid_workflow_id(v):
            raise ValueError(f"Invalid workflow id {v!r}: expected 'WF-002' form")
        return v


class WorkflowDraftUpdate(BaseModel):
    """Body for replacing fields of a workflow draft."""

    model_config = ConfigDict(extra="forbid")

    intent: str
    steps: tuple[WorkflowStep, ...]
    checks: tuple[WorkflowCheck, ...] = ()
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


class ActivityDenialReason(StrEnum):
    """Why an activity may not start on a container (R-310-025).

    A code rather than prose: the workbench must be able to say precisely
    what is missing — a cycle, a binding, or a publication — and a message
    string cannot be branched on.
    """

    NO_CYCLE = "no-published-cycle"
    UNKNOWN_CONTAINER = "unknown-container"
    NO_WORKFLOW_BOUND = "no-workflow-bound"
    WORKFLOW_NOT_PUBLISHED = "workflow-not-published"


class ActivityBinding(BaseModel):
    """The activity permitted on one container, fully resolved.

    Returned only when an activity MAY start: holding this object is the
    evidence that the precondition of R-310-025 is met.
    """

    model_config = ConfigDict(extra="forbid")

    container: str
    cycle_id: str
    cycle_version: int
    workflow_id: str
    workflow_version: int
    workflow: WorkflowPublic


class ProcessVersionRow(BaseModel):
    """One index row of the version listing."""

    model_config = ConfigDict(extra="allow")

    entity_id: str
    version: int
    status: EntityStatus


class ProcessVersionListResponse(BaseModel):
    """Versions of the cycles or workflows visible at one scope."""

    model_config = ConfigDict(extra="forbid")

    versions: list[ProcessVersionRow] = Field(default_factory=list)
