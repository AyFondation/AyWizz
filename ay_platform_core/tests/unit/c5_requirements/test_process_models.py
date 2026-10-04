# =============================================================================
# File: test_process_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_process_models.py
# Description: Unit tests for the cycle (`C-`) and workflow (`WF-`) contracts
#              of 310-SPEC §4.2 / §4.3.
#
#              The three load-bearing assertions:
#                - an approved workflow with no checks is impossible
#                  (R-310-041) — otherwise an activity is a named prompt;
#                - control flow cannot express a loop (R-310-043) — a return
#                  target is human-gate-only and must point BACKWARD;
#                - a tailored entity without a rationale is impossible
#                  (R-310-046).
#
# @relation validates:R-310-020
# @relation validates:R-310-021
# @relation validates:R-310-022
# @relation validates:R-310-040
# @relation validates:R-310-041
# @relation validates:R-310-042
# @relation validates:R-310-043
# @relation validates:R-310-046
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.process.models import (
    ContainerSpec,
    CycleDefinition,
    CyclePublic,
    EntityStatus,
    LinkKind,
    StepKind,
    WorkflowCheck,
    WorkflowDefinition,
    WorkflowPublic,
    WorkflowStep,
    is_valid_cycle_id,
    is_valid_workflow_id,
)

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)

_AUDIT: dict[str, Any] = {
    "tenant_id": "acme",
    "created_at": NOW,
    "created_by": "o.mathieu",
    "updated_at": NOW,
    "updated_by": "o.mathieu",
}

_SCOPE_FD = (
    "Observable system behaviour at vehicle level. Excludes component "
    "partitioning and signal-level detail."
)
_SCOPE_AD = (
    "Component partitioning, allocation of behaviour to components, "
    "redundancy and independence arguments."
)


def _container(**overrides: Any) -> ContainerSpec:
    payload: dict[str, Any] = {
        "slug": "010-FUNCTIONAL-DESCRIPTION",
        "ordinal": 10,
        "scope_id": "SC-001",
        "scope": _SCOPE_FD,
        "links_in": (LinkKind.COVERS,),
    }
    payload.update(overrides)
    return ContainerSpec.model_validate(payload)


def _cycle(**overrides: Any) -> CycleDefinition:
    payload: dict[str, Any] = {
        **_AUDIT,
        "cycle_id": "C-AUTOMOTIVE",
        "title": "Automotive systems engineering",
        "version": 1,
        "containers": (
            _container(),
            _container(
                slug="030-ARCHITECTURE-DESIGN",
                ordinal=30,
                scope_id="SC-002",
                scope=_SCOPE_AD,
            ),
        ),
    }
    payload.update(overrides)
    return CycleDefinition.model_validate(payload)


def _check(check_id: str = "CRIT-COV-001") -> WorkflowCheck:
    return WorkflowCheck(
        check_id=check_id,
        statement="Every allocated requirement has at least one covering object.",
    )


def _workflow(**overrides: Any) -> WorkflowDefinition:
    payload: dict[str, Any] = {
        **_AUDIT,
        "workflow_id": "WF-002",
        "version": 1,
        "intent": "Produce a container so every allocated requirement is covered.",
        "steps": (
            WorkflowStep(
                step_id="s1", kind=StepKind.AGENT, role="doc-author",
                action="draft_objects",
            ),
            WorkflowStep(step_id="s2", kind=StepKind.CHECK, checks=("CRIT-COV-001",)),
            WorkflowStep(
                step_id="s3", kind=StepKind.HUMAN_GATE, name="Per-object review",
                on_reject="s1", reject_scope="object",
            ),
        ),
        "checks": (_check(),),
    }
    payload.update(overrides)
    return WorkflowDefinition.model_validate(payload)


@pytest.mark.unit
class TestIdentifiers:
    def test_cycle_ids(self) -> None:
        for good in ("C-AUTOMOTIVE", "C-ASPICE-V2", "C-X"):
            assert is_valid_cycle_id(good), good
        for bad in ("C-automotive", "AUTOMOTIVE", "C_AUTOMOTIVE", "C-", ""):
            assert not is_valid_cycle_id(bad), bad

    def test_workflow_ids(self) -> None:
        for good in ("WF-001", "WF-0042"):
            assert is_valid_workflow_id(good), good
        for bad in ("WF-1", "wf-001", "WF001", "WF-", ""):
            assert not is_valid_workflow_id(bad), bad

    def test_cycle_rejects_a_bad_id(self) -> None:
        with pytest.raises(ValidationError, match="Invalid cycle id"):
            _cycle(cycle_id="automotive")

    def test_workflow_rejects_a_bad_id(self) -> None:
        with pytest.raises(ValidationError, match="Invalid workflow id"):
            _workflow(workflow_id="WF-2")


@pytest.mark.unit
class TestContainerSpec:
    def test_scope_must_be_substantive(self) -> None:
        # R-310-066: an allocation cites this text to justify itself. A token
        # scope makes every citation meaningless.
        with pytest.raises(ValidationError, match="substantive statement"):
            _container(scope="architecture")

    def test_scope_id_format_is_enforced(self) -> None:
        with pytest.raises(ValidationError, match="Invalid scope_id"):
            _container(scope_id="SCOPE-1")

    def test_slug_format_is_enforced(self) -> None:
        with pytest.raises(ValidationError, match="Invalid container slug"):
            _container(slug="../escape")

    def test_coverage_is_obligatory_by_default(self) -> None:
        assert _container().coverage_obligatory is True


@pytest.mark.unit
class TestCycleCoherence:
    def test_a_cycle_needs_a_container(self) -> None:
        with pytest.raises(ValidationError, match="at least one container"):
            _cycle(containers=())

    def test_duplicate_slugs_rejected(self) -> None:
        with pytest.raises(ValidationError, match="slugs SHALL be unique"):
            _cycle(containers=(_container(), _container(ordinal=20, scope_id="SC-002")))

    def test_duplicate_scope_ids_rejected(self) -> None:
        # A duplicate would make an allocation citation ambiguous (R-310-066).
        with pytest.raises(ValidationError, match="scope_ids SHALL be unique"):
            _cycle(
                containers=(
                    _container(),
                    _container(slug="030-AD", ordinal=30, scope="x" * 40),
                )
            )

    def test_duplicate_ordinals_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ordinals SHALL be unique"):
            _cycle(
                containers=(
                    _container(),
                    _container(slug="030-AD", scope_id="SC-002", scope="x" * 40),
                )
            )

    def test_container_lookup(self) -> None:
        cycle = _cycle()
        assert cycle.container("030-ARCHITECTURE-DESIGN") is not None
        assert cycle.container("999-NOPE") is None

    def test_ordered_containers_follow_the_ordinal(self) -> None:
        cycle = _cycle(
            containers=(
                _container(slug="030-AD", ordinal=30, scope_id="SC-002", scope="y" * 40),
                _container(),
            )
        )
        assert [c.ordinal for c in cycle.ordered_containers] == [10, 30]


@pytest.mark.unit
class TestWorkflowStepShape:
    def test_agent_step_needs_role_and_action(self) -> None:
        with pytest.raises(ValidationError, match="role and an action"):
            WorkflowStep(step_id="s1", kind=StepKind.AGENT)

    def test_check_step_needs_a_check(self) -> None:
        with pytest.raises(ValidationError, match="at least one check"):
            WorkflowStep(step_id="s1", kind=StepKind.CHECK)

    def test_check_step_rejects_a_role(self) -> None:
        with pytest.raises(ValidationError, match="NOT declare a role"):
            WorkflowStep(
                step_id="s1", kind=StepKind.CHECK, checks=("CRIT-COV-001",),
                role="doc-author",
            )

    def test_human_gate_rejects_a_role(self) -> None:
        with pytest.raises(ValidationError, match="NOT declare a role"):
            WorkflowStep(step_id="s1", kind=StepKind.HUMAN_GATE, role="reviewer")

    def test_step_id_must_be_snake_case(self) -> None:
        with pytest.raises(ValidationError, match="Invalid step id"):
            WorkflowStep(step_id="S1", kind=StepKind.HUMAN_GATE)

    def test_no_conditional_field_exists(self) -> None:
        # R-310-043: the absence IS the enforcement. A workflow language that
        # grows branches becomes a scheduler nobody can audit.
        forbidden = {"condition", "when", "loop", "repeat", "variables", "expr"}
        assert not forbidden & set(WorkflowStep.model_fields)

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowStep.model_validate(
                {"step_id": "s1", "kind": "human-gate", "condition": "x > 1"}
            )


@pytest.mark.unit
class TestWorkflowControlFlow:
    def test_only_a_human_gate_may_return(self) -> None:
        with pytest.raises(ValidationError, match="only a human-gate"):
            WorkflowStep(
                step_id="s2", kind=StepKind.AGENT, role="r", action="a",
                on_reject="s1",
            )

    def test_return_target_must_exist(self) -> None:
        with pytest.raises(ValidationError, match="unknown step"):
            _workflow(
                steps=(
                    WorkflowStep(
                        step_id="s1", kind=StepKind.AGENT, role="r", action="a"
                    ),
                    WorkflowStep(
                        step_id="s2", kind=StepKind.HUMAN_GATE, on_reject="s9"
                    ),
                )
            )

    def test_return_target_must_be_earlier(self) -> None:
        # A forward or self return is a loop in disguise.
        with pytest.raises(ValidationError, match="return to an EARLIER step"):
            _workflow(
                steps=(
                    WorkflowStep(
                        step_id="s1", kind=StepKind.HUMAN_GATE, on_reject="s1"
                    ),
                )
            )

    def test_forward_return_rejected(self) -> None:
        with pytest.raises(ValidationError, match="return to an EARLIER step"):
            _workflow(
                steps=(
                    WorkflowStep(
                        step_id="s1", kind=StepKind.HUMAN_GATE, on_reject="s2"
                    ),
                    WorkflowStep(
                        step_id="s2", kind=StepKind.AGENT, role="r", action="a"
                    ),
                )
            )

    def test_backward_return_accepted(self) -> None:
        assert _workflow().steps[2].on_reject == "s1"

    def test_duplicate_step_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="step ids SHALL be unique"):
            _workflow(
                steps=(
                    WorkflowStep(
                        step_id="s1", kind=StepKind.AGENT, role="r", action="a"
                    ),
                    WorkflowStep(
                        step_id="s1", kind=StepKind.AGENT, role="r", action="b"
                    ),
                )
            )


@pytest.mark.unit
class TestWorkflowChecks:
    #: Steps that reference no check — required to isolate the publication
    #: gate, since the referential rule would otherwise fire first.
    _NO_CHECK_STEPS = (
        WorkflowStep(step_id="s1", kind=StepKind.AGENT, role="r", action="a"),
        WorkflowStep(step_id="s2", kind=StepKind.HUMAN_GATE, on_reject="s1"),
    )

    def test_approved_workflow_without_checks_is_impossible(self) -> None:
        """R-310-041 — the line between an activity and a named prompt."""
        with pytest.raises(ValidationError, match="no checks SHALL NOT be approved"):
            _workflow(
                status=EntityStatus.APPROVED, checks=(), steps=self._NO_CHECK_STEPS
            )

    def test_draft_workflow_without_checks_is_allowed(self) -> None:
        # A draft is being written; the gate binds at publication.
        draft = _workflow(
            status=EntityStatus.DRAFT, checks=(), steps=self._NO_CHECK_STEPS
        )
        assert draft.checks == ()

    def test_approved_workflow_with_checks_is_allowed(self) -> None:
        assert _workflow(status=EntityStatus.APPROVED).status is EntityStatus.APPROVED

    def test_step_cannot_reference_an_undeclared_check(self) -> None:
        with pytest.raises(ValidationError, match="undeclared checks"):
            _workflow(
                steps=(
                    WorkflowStep(
                        step_id="s1", kind=StepKind.CHECK, checks=("CRIT-XXX-999",)
                    ),
                )
            )

    def test_check_id_format_is_enforced(self) -> None:
        with pytest.raises(ValidationError, match="Invalid check id"):
            WorkflowCheck(check_id="COV-1", statement="something long enough here")

    def test_check_statement_must_assert_something(self) -> None:
        with pytest.raises(ValidationError, match="state what it asserts"):
            WorkflowCheck(check_id="CRIT-COV-001", statement="coverage")


@pytest.mark.unit
class TestTailoring:
    def test_tailoring_needs_a_rationale(self) -> None:
        with pytest.raises(ValidationError, match="SHALL carry a rationale"):
            _cycle(cycle_id="C-ACME", tailoring_of="C-AUTOMOTIVE", project_id="p1")

    def test_tailoring_needs_a_project(self) -> None:
        with pytest.raises(ValidationError, match="SHALL be project-scoped"):
            _cycle(
                cycle_id="C-ACME",
                tailoring_of="C-AUTOMOTIVE",
                tailoring_rationale="Security analysis is handled by the OEM.",
            )

    def test_rationale_without_tailoring_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="meaningless without tailoring_of"):
            _cycle(tailoring_rationale="why?")

    def test_valid_tailoring_accepted(self) -> None:
        tailored = _cycle(
            cycle_id="C-ACME",
            project_id="p1",
            tailoring_of="C-AUTOMOTIVE",
            tailoring_rationale="Security analysis is handled by the OEM.",
        )
        assert tailored.project_id == "p1"

    def test_workflow_tailoring_follows_the_same_rule(self) -> None:
        with pytest.raises(ValidationError, match="SHALL carry a rationale"):
            _workflow(workflow_id="WF-900", tailoring_of="WF-002", project_id="p1")


@pytest.mark.unit
class TestPublicProjections:
    def test_cycle_public_orders_containers(self) -> None:
        cycle = _cycle(
            containers=(
                _container(slug="030-AD", ordinal=30, scope_id="SC-002", scope="y" * 40),
                _container(),
            )
        )
        public = CyclePublic.from_definition(cycle)
        assert [c.ordinal for c in public.containers] == [10, 30]

    def test_cycle_public_withholds_tenant_id(self) -> None:
        # The API scopes by the forward-auth tenant header; echoing it back
        # would duplicate it on the wire.
        assert "tenant_id" not in CyclePublic.model_fields

    def test_workflow_public_carries_the_checks(self) -> None:
        # The checks ARE the contract of an activity (R-310-041); hiding them
        # would leave a reviewer unable to see what the workflow guarantees.
        public = WorkflowPublic.from_definition(_workflow())
        assert [c.check_id for c in public.checks] == ["CRIT-COV-001"]

    def test_workflow_public_round_trips(self) -> None:
        public = WorkflowPublic.from_definition(_workflow())
        assert WorkflowPublic.model_validate_json(public.model_dump_json()) == public
