# =============================================================================
# File: models.py
# Version: 6
# Path: ay_platform_core/src/ay_platform_core/c6_validation/models.py
# Description: Pydantic v2 models for C6 — public contracts (Finding,
#              ValidationRun, CheckSpec, PluginDescriptor) and internal
#              structures (RelationMarker, CodeArtifact, CheckContext,
#              CheckResult) used by plugins.
#
#              v6 (2026-10-09): every public field carries a
#              `Field(description=...)`, so the generated OpenAPI document
#              explains the contract instead of listing its types. 58
#              fields. C6 went first because the distinctions that most
#              need stating live here — a `Finding` is binary and a
#              `Verdict` is graded, and a verdict's `method` is what
#              separates a computed number from a model's opinion.
#
#              v3 (D-017): adds the graded `Verdict` + `VerdictMethod`
#              (R-700-030) and a `verdict` field on `ValidationRun`
#              (R-700-031, the T1 deterministic grade).
#              v4 (D-017): adds the optional `judged_verdict` field on
#              `ValidationRun` (R-700-032, the T3 LLM-as-judge grade — null
#              unless the opt-in judge ran and succeeded).
#
# @relation implements:R-700-001
# @relation implements:R-700-030
# @relation implements:E-700-001
# @relation implements:E-700-002
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Severity(StrEnum):
    """Finding severity. `blocking` fails the run gate; others are advisory."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"
    INFO = "info"


class VerdictMethod(StrEnum):
    """Evaluation tier that produced a graded Verdict (D-017 / R-700-030)."""

    DETERMINISTIC = "deterministic"  # T1 — reference-free (compile/tests/lint…)
    REFERENCE = "reference"  # T2 — comparison against held-out golden datasets
    JUDGED = "judged"  # T3 — LLM-as-judge with rubrics


class FindingStatus(StrEnum):
    """Lifecycle of a finding — v1 only emits `open`; lifecycle moves come v2."""

    OPEN = "open"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class RunStatus(StrEnum):
    """Lifecycle of a validation run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RelationVerb(StrEnum):
    """Closed set of verbs accepted by the v1 marker parser (R-700-040)."""

    IMPLEMENTS = "implements"
    VALIDATES = "validates"
    USES = "uses"
    DERIVES_FROM = "derives-from"


# ---------------------------------------------------------------------------
# Internal structures used by plugins (defined first: referenced in requests)
# ---------------------------------------------------------------------------


class CodeArtifact(BaseModel):
    """A single code-domain artifact available for inspection during a run.

    v1: passed directly into the run by the orchestrator or a test. v2 will
    fetch artifacts from C10 (MinIO) via the artifact store adapter.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description=(
            "Repository-relative path. It is the matching key against "
            "`baseline_artifacts`, so a renamed file reads as a new one."
        )
    )
    content: str = Field(description="The file's full text.")
    is_test: bool = Field(
        default=False,
        description=(
            "Whether this artifact is a test. Checks that measure coverage "
            "or forbid a pattern in production code need the distinction, "
            "and inferring it from the path would make the result depend on "
            "a naming convention the caller may not share."
        ),
    )


class RelationMarker(BaseModel):
    """One parsed `@relation <verb>:<target>` marker found in an artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_path: str
    line: int
    verb: RelationVerb
    targets: list[str]


# ---------------------------------------------------------------------------
# Public contracts
# ---------------------------------------------------------------------------


class CheckSpec(BaseModel):
    """Declaration of one check registered by a plugin."""

    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(
        description="Identifier a `Finding` cites in its own `check_id`."
    )
    title: str = Field(description="Short name, for a list.")
    severity_default: Severity = Field(
        description=(
            "The severity findings from this check carry unless the check "
            "overrides it per finding."
        )
    )
    description: str = Field(description="What the check asserts, and why.")


class PluginDescriptor(BaseModel):
    """Plugin registration metadata. Exposed via GET /validation/plugins."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(
        description=(
            "The production domain this plugin validates. Pass it as "
            "`domain` when triggering a run."
        )
    )
    name: str = Field(description="Plugin name, for display.")
    version: str = Field(description="Plugin version, for display.")
    artifact_formats: list[str] = Field(
        description="Artifact formats its checks can read (e.g. `markdown`, `python`)."
    )
    checks: list[CheckSpec] = Field(
        description=(
            "Every check it registers. A run with an empty `check_ids` "
            "executes all of them."
        )
    )


class Finding(BaseModel):
    """E-700-001 projection — single validation result row."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(description="Stable identifier of this finding.")
    run_id: str = Field(description="The run that produced it.")
    check_id: str = Field(
        description=(
            "The check that fired, as its plugin registered it. Look it up "
            "in `GET /validation/plugins` to read what the check asserts."
        )
    )
    domain: str = Field(
        description="Production domain whose plugin owns the check (e.g. `code`)."
    )
    severity: Severity = Field(
        description=(
            "`blocking` fails the run gate; `advisory` and `info` do not. "
            "Three levels rather than a numeric score because the only "
            "decision this drives is binary."
        )
    )
    status: FindingStatus = FindingStatus.OPEN
    """Lifecycle. v1 only ever emits `open` — `resolved` and `suppressed`
    exist for the lifecycle moves that come with v2, so a client should
    handle them rather than assume `open`."""
    artifact_ref: str | None = Field(
        default=None,
        description=(
            "The artifact the check was reading, when it was reading one. "
            "Null for a check that evaluates the corpus as a whole."
        ),
    )
    location: str | None = Field(
        default=None,
        description=(
            "Where inside that artifact, as `path:line` when the check can "
            "be that precise. Null when the finding is about the artifact "
            "as a whole."
        ),
    )
    entity_id: str | None = Field(
        default=None,
        description=(
            "The requirement or object the finding is about, when it is "
            "about one — this is what lets a UI select the subject from the "
            "finding instead of making the reader search for it."
        ),
    )
    message: str = Field(
        description="What is wrong, in one sentence, for a human to act on."
    )
    fix_hint: str | None = Field(
        default=None,
        description=(
            "What to do about it, when the check knows. Null is normal: a "
            "check that detects a problem does not always know the remedy."
        ),
    )
    created_at: datetime = Field(description="When the check fired (UTC).")


class Verdict(BaseModel):
    """Graded, provenance-tagged evaluation verdict (D-017 / R-700-030).
    Extends the binary `Finding` with a score + epistemic provenance, so a
    deterministic coverage number and an LLM-judged clarity score are
    distinguishable objects."""

    model_config = ConfigDict(extra="forbid")

    verdict_id: str = Field(description="Stable identifier of this verdict.")
    run_id: str = Field(description="The run it grades.")
    domain: str = Field(description="Production domain whose plugin produced it.")
    method: VerdictMethod = Field(
        description=(
            "WHICH TIER produced the grade, and therefore how much it is "
            "worth: `deterministic` is computed from the artifacts, "
            "`reference` compares against a held-out golden dataset, and "
            "`judged` is an LLM's opinion. Carried explicitly so a computed "
            "number and a model's judgement never read as the same claim."
        )
    )
    score: float = Field(
        ge=0.0, le=1.0, description="The grade, 0.0 (worst) to 1.0 (best)."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How much the method trusts its own score. A `judged` verdict "
            "can be confidently wrong, so read this with `method`, not "
            "instead of it."
        ),
    )
    rationale: str = Field(description="Why this score, in the method's own terms.")
    evidence: list[str] = Field(
        default_factory=list,
        description="What the grade was derived from — finding ids, paths, metrics.",
    )
    created_at: datetime = Field(description="When the grade was produced (UTC).")


class RunSummaryCounts(BaseModel):
    """Aggregated counts by severity."""

    model_config = ConfigDict(extra="forbid")

    blocking: int = Field(
        default=0, description="Findings that fail the run gate."
    )
    advisory: int = Field(
        default=0, description="Findings worth acting on that do not fail the gate."
    )
    info: int = Field(default=0, description="Observations, carrying no verdict.")


class ValidationRun(BaseModel):
    """E-700-002 projection — run metadata + aggregate result."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(description="Stable identifier of this run.")
    project_id: str = Field(description="The project whose artifacts were validated.")
    domain: str = Field(description="Production domain whose plugin ran.")
    check_ids: list[str] = Field(
        default_factory=list,
        description=(
            "The checks this run was asked to execute. Empty means every "
            "check the domain's plugin registers."
        ),
    )
    status: RunStatus = Field(
        description=(
            "`pending` → `running` → `completed` or `failed`. Poll until it "
            "leaves the first two; findings are only complete afterwards."
        )
    )
    findings_count: RunSummaryCounts = Field(
        default_factory=RunSummaryCounts,
        description=(
            "Findings per severity. There is deliberately no single total: "
            "one blocking finding and forty info findings are not "
            "forty-one of the same thing."
        ),
    )
    verdict: Verdict | None = Field(
        default=None,
        description=(
            "The deterministic graded verdict (D-017 / R-700-031): a "
            "reference-free coherence grade derived from the findings. Null "
            "until the run completes."
        ),
    )
    judged_verdict: Verdict | None = Field(
        default=None,
        description=(
            "An optional LLM-as-judge verdict (D-017 / R-700-032). Null "
            "unless the opt-in judge ran AND returned a parseable grade — a "
            "judge failure is silent and leaves `verdict` untouched, so "
            "null here says nothing about the run's outcome."
        ),
    )
    started_at: datetime = Field(description="When the run was accepted (UTC).")
    completed_at: datetime | None = Field(
        default=None,
        description="When it reached `completed` or `failed`. Null while running.",
    )
    snapshot_uri: str | None = Field(
        default=None,
        description=(
            "Object-store URI of the artifacts as validated, so a finding "
            "can be re-read against what the check actually saw rather than "
            "against the corpus as it stands now."
        ),
    )


class RunTriggerRequest(BaseModel):
    """POST /validation/runs body.

    v1: the caller passes the corpus (``requirements``) and the code artifacts
    (``artifacts``) directly. v2 will fetch these from C5 + C10 when the
    orchestrator integration is wired. Keeping them here makes the endpoint
    self-contained for integration testing and for MCP (C9) consumption.
    """

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(
        description=(
            "Which plugin to run, by its `domain`. List the installed ones "
            "with `GET /validation/plugins`."
        )
    )
    project_id: str = Field(
        description=(
            "MUST equal the `project_id` in the path. Sent in both places "
            "for the benefit of non-HTTP callers (C9/MCP); a mismatch is "
            "refused with 422 rather than silently resolved, because "
            "guessing which one the caller meant is how a run validates the "
            "wrong project."
        )
    )
    check_ids: list[str] = Field(
        default_factory=list,
        description="Run only these checks. Empty runs every check the plugin has.",
    )
    requirements: list[dict[str, object]] = Field(
        default_factory=list,
        description=(
            "The requirement corpus to validate against, passed inline. v1 "
            "is deliberately self-contained; a later version fetches it "
            "from C5 instead."
        ),
    )
    artifacts: list[CodeArtifact] = Field(
        default_factory=list, description="The artifacts to validate, passed inline."
    )
    baseline_artifacts: list[CodeArtifact] = Field(
        default_factory=list,
        description=(
            "The same artifacts at their PREVIOUS version, matched by "
            "`path`, for the checks that compare public signatures across "
            "versions (R-700-022). Empty — a first generation, say — makes "
            "those checks a no-op rather than a failure."
        ),
    )


class RunTriggerResponse(BaseModel):
    """POST /validation/runs response (HTTP 202)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(
        description="Poll `GET /validation/runs/{run_id}` with this to follow the run."
    )
    status: RunStatus = Field(
        description="`pending` on acceptance — the run has not executed yet."
    )


class FindingPage(BaseModel):
    """Paginated findings listing."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(description="The run these findings belong to.")
    total: int = Field(
        description=(
            "Findings in the whole run, not in this page — so a client "
            "knows whether to ask for more."
        )
    )
    items: list[Finding] = Field(description="This page's findings.")


class DomainList(BaseModel):
    """Response wrapper for GET /validation/domains.

    Wrapped (rather than ``list[str]``) so the response passes the
    monorepo's router-typing coherence check: every REST response model
    must be a Pydantic ``BaseModel`` (coherence test, scripts/checks/
    check_router_typing).
    """

    model_config = ConfigDict(extra="forbid")

    domains: list[str] = Field(
        default_factory=list,
        description="Domains with an installed plugin, i.e. what `domain` accepts.",
    )


# ---------------------------------------------------------------------------
# Internal runtime context / result (consumed by plugins; not REST-exposed)
# ---------------------------------------------------------------------------


class CheckContext(BaseModel):
    """Inputs available to a plugin's `run_check()`."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    project_id: str
    domain: str
    requirements: list[dict[str, object]] = Field(default_factory=list)
    artifacts: list[CodeArtifact] = Field(default_factory=list)
    markers: list[RelationMarker] = Field(default_factory=list)
    # R-700-022: previous-version artifacts (matched by path) for the
    # interface-signature-drift check. Empty → no baseline to compare.
    baseline_artifacts: list[CodeArtifact] = Field(default_factory=list)


class CheckResult(BaseModel):
    """Output of a plugin's `run_check()`."""

    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(default_factory=list)
    # ``error_message`` is populated iff the check raised; the service
    # translates this into a ``severity=info`` finding of
    # ``check_id=<check>:error``.
    error_message: str | None = None
