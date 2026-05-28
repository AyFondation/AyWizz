# =============================================================================
# File: checks.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c6_validation/domains/code/checks.py
# Description: Implementation of the 9 MUST checks of the `code` domain
#              (D-006, R-700-020..R-700-028). Each check is a pure function
#              returning a list of Finding.
#
#              v2 (D-017): #8 `data-model-drift` (R-700-027) promoted from
#              STUB — it compares a Pydantic model's fields against an `E-*`
#              `fields:`/`model_name:` declaration. Only #3
#              `interface-signature-drift` (R-700-022) remains a stub.
#
# @relation implements:R-700-020
# @relation implements:R-700-021
# @relation implements:R-700-022
# @relation implements:R-700-023
# @relation implements:R-700-024
# @relation implements:R-700-025
# @relation implements:R-700-026
# @relation implements:R-700-027
# @relation implements:R-700-028
# =============================================================================

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime

from ay_platform_core.c6_validation.domains.code.parsers import (
    extract_markers,
    is_exempt_module,
    is_exempt_test_file,
)
from ay_platform_core.c6_validation.models import (
    CheckContext,
    Finding,
    RelationMarker,
    RelationVerb,
    Severity,
)

DOMAIN = "code"


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


def _collect_all_markers(context: CheckContext) -> list[RelationMarker]:
    """Return the union of markers passed pre-parsed in the context plus any
    markers that can be freshly parsed from artifacts NOT yet represented.

    The service pre-populates `context.markers`; tests may bypass this and
    pass artifacts only. We tolerate both shapes.
    """
    if context.markers:
        return list(context.markers)
    out: list[RelationMarker] = []
    for artifact in context.artifacts:
        parsed, _ = extract_markers(artifact)
        out.extend(parsed)
    return out


def _approved_requirement_ids(context: CheckContext) -> set[str]:
    """Entity ids of requirements with ``status == 'approved'`` and relevant
    types (R / E). Deprecated, superseded, draft entries are excluded.
    """
    ids: set[str] = set()
    for row in context.requirements:
        if row.get("status") != "approved":
            continue
        entity_id = row.get("entity_id") or row.get("id")
        if not isinstance(entity_id, str):
            continue
        etype = row.get("type")
        if etype in {"R", "E", None}:
            ids.add(entity_id)
    return ids


def _known_entity_ids(context: CheckContext) -> set[str]:
    ids: set[str] = set()
    for row in context.requirements:
        entity_id = row.get("entity_id") or row.get("id")
        if isinstance(entity_id, str):
            ids.add(entity_id)
    return ids


def _deprecated_entity_ids(context: CheckContext) -> set[str]:
    out: set[str] = set()
    for row in context.requirements:
        if row.get("status") == "deprecated":
            entity_id = row.get("entity_id") or row.get("id")
            if isinstance(entity_id, str):
                out.add(entity_id)
    return out


def _make_finding(
    run_id: str,
    check_id: str,
    severity: Severity,
    message: str,
    *,
    artifact_ref: str | None = None,
    location: str | None = None,
    entity_id: str | None = None,
    fix_hint: str | None = None,
) -> Finding:
    return Finding(
        finding_id=_new_id(),
        run_id=run_id,
        check_id=check_id,
        domain=DOMAIN,
        severity=severity,
        artifact_ref=artifact_ref,
        location=location,
        entity_id=entity_id,
        message=message,
        fix_hint=fix_hint,
        created_at=_now(),
    )


# ---------------------------------------------------------------------------
# #1 — req-without-code (R-700-020)
# ---------------------------------------------------------------------------


def check_req_without_code(run_id: str, context: CheckContext) -> list[Finding]:
    approved = _approved_requirement_ids(context)
    implemented: set[str] = set()
    for marker in _collect_all_markers(context):
        if marker.verb != RelationVerb.IMPLEMENTS:
            continue
        for target in marker.targets:
            implemented.add(target.split("@", 1)[0])

    return [
        _make_finding(
            run_id,
            "req-without-code",
            Severity.BLOCKING,
            message=f"Requirement {req_id} is approved but no implementing code references it.",
            entity_id=req_id,
            fix_hint=f"Add `@relation implements:{req_id}` to the implementing module.",
        )
        for req_id in sorted(approved - implemented)
    ]


# ---------------------------------------------------------------------------
# #2 — code-without-requirement (R-700-021)
# ---------------------------------------------------------------------------


def check_code_without_requirement(
    run_id: str, context: CheckContext
) -> list[Finding]:
    findings: list[Finding] = []
    for artifact in context.artifacts:
        if is_exempt_module(artifact):
            continue
        markers, _ = extract_markers(artifact)
        if not markers:
            findings.append(
                _make_finding(
                    run_id,
                    "code-without-requirement",
                    Severity.BLOCKING,
                    message=(
                        f"Module {artifact.path} has no `@relation` marker — "
                        "every non-test production module must reference at "
                        "least one entity."
                    ),
                    artifact_ref=artifact.path,
                    fix_hint=(
                        "Add a `@relation implements:<entity_id>` comment at "
                        "the top of the module."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# #3 — interface-signature-drift (R-700-022) — STUB
# ---------------------------------------------------------------------------


def check_interface_signature_drift(
    run_id: str, context: CheckContext
) -> list[Finding]:
    return [
        _make_finding(
            run_id,
            "interface-signature-drift",
            Severity.INFO,
            message=(
                "Interface signature drift detection is a v1 stub. "
                "Full implementation requires machine-readable E-* signatures."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# #4 — test-absent-for-requirement (R-700-023)
# ---------------------------------------------------------------------------


def check_test_absent_for_requirement(
    run_id: str, context: CheckContext
) -> list[Finding]:
    approved = _approved_requirement_ids(context)
    validated: set[str] = set()
    for marker in _collect_all_markers(context):
        # A test artifact can `validate` or `implement` a requirement; both
        # are accepted as coverage.
        if marker.verb not in {RelationVerb.VALIDATES, RelationVerb.IMPLEMENTS}:
            continue
        # Only markers authored in test artifacts count for this check.
        for artifact in context.artifacts:
            if artifact.path == marker.artifact_path and artifact.is_test:
                for target in marker.targets:
                    validated.add(target.split("@", 1)[0])
                break

    return [
        _make_finding(
            run_id,
            "test-absent-for-requirement",
            Severity.BLOCKING,
            message=(
                f"Approved requirement {req_id} has no test referencing it "
                "via `@relation validates:` or `implements:`."
            ),
            entity_id=req_id,
            fix_hint=f"Add a test file with `@relation validates:{req_id}`.",
        )
        for req_id in sorted(approved - validated)
    ]


# ---------------------------------------------------------------------------
# #5 — orphan-test (R-700-024)
# ---------------------------------------------------------------------------


def check_orphan_test(run_id: str, context: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    for artifact in context.artifacts:
        if not artifact.is_test or is_exempt_test_file(artifact):
            continue
        markers, _ = extract_markers(artifact)
        has_coverage = any(
            m.verb in {RelationVerb.VALIDATES, RelationVerb.IMPLEMENTS}
            for m in markers
        )
        if not has_coverage:
            findings.append(
                _make_finding(
                    run_id,
                    "orphan-test",
                    Severity.BLOCKING,
                    message=(
                        f"Test {artifact.path} does not reference any "
                        "requirement via `@relation validates:` or "
                        "`implements:`."
                    ),
                    artifact_ref=artifact.path,
                    fix_hint="Add a `@relation validates:<entity_id>` marker.",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# #6 — obsolete-reference (R-700-025)
# ---------------------------------------------------------------------------


def check_obsolete_reference(
    run_id: str, context: CheckContext
) -> list[Finding]:
    known = _known_entity_ids(context)
    deprecated = _deprecated_entity_ids(context)

    findings: list[Finding] = []
    for marker in _collect_all_markers(context):
        for target in marker.targets:
            base = target.split("@", 1)[0]
            if base not in known:
                findings.append(
                    _make_finding(
                        run_id,
                        "obsolete-reference",
                        Severity.BLOCKING,
                        message=(
                            f"Marker `@relation {marker.verb.value}:{target}` "
                            f"in {marker.artifact_path}:{marker.line} points "
                            "to an unknown entity."
                        ),
                        artifact_ref=marker.artifact_path,
                        location=f"{marker.artifact_path}:{marker.line}",
                        entity_id=base,
                        fix_hint=(
                            f"Remove the marker, or create entity {base} in C5."
                        ),
                    )
                )
            elif base in deprecated:
                findings.append(
                    _make_finding(
                        run_id,
                        "obsolete-reference",
                        Severity.BLOCKING,
                        message=(
                            f"Marker `@relation {marker.verb.value}:{target}` "
                            f"in {marker.artifact_path}:{marker.line} points "
                            "to a DEPRECATED entity."
                        ),
                        artifact_ref=marker.artifact_path,
                        location=f"{marker.artifact_path}:{marker.line}",
                        entity_id=base,
                        fix_hint="Update the marker to reference the superseding entity.",
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# #7 — version-drift (R-700-026)
# ---------------------------------------------------------------------------


def _current_entity_versions(context: CheckContext) -> dict[str, int]:
    """Map entity_id → current version from the context requirements list."""
    out: dict[str, int] = {}
    for row in context.requirements:
        entity_id = row.get("entity_id") or row.get("id")
        version = row.get("version")
        if isinstance(entity_id, str) and isinstance(version, int):
            out[entity_id] = version
    return out


def check_version_drift(run_id: str, context: CheckContext) -> list[Finding]:
    """For every version-pinned marker (``R-NNN-NNN@vK``), verify K matches
    the entity's current version per C5. Pinned markers that lag behind
    produce blocking findings so CI stops when a referenced entity has
    been updated without touching the implementing code.
    """
    current_versions = _current_entity_versions(context)
    findings: list[Finding] = []
    for marker in _collect_all_markers(context):
        for target in marker.targets:
            if "@v" not in target:
                # Un-pinned marker — out of scope for this check. The
                # absence of a pin is covered by the obsolete-reference
                # check when the target is unknown or deprecated.
                continue
            base, _, pin_str = target.partition("@v")
            try:
                pinned = int(pin_str)
            except ValueError:
                # Malformed pin — handled by the marker-syntax check.
                continue
            current = current_versions.get(base)
            if current is None:
                # Unknown entity — handled by obsolete-reference.
                continue
            if pinned != current:
                findings.append(
                    _make_finding(
                        run_id,
                        "version-drift",
                        Severity.BLOCKING,
                        message=(
                            f"Marker `@relation {marker.verb.value}:{target}` "
                            f"in {marker.artifact_path}:{marker.line} pins "
                            f"v{pinned} but entity {base} is currently at "
                            f"v{current}."
                        ),
                        artifact_ref=marker.artifact_path,
                        location=f"{marker.artifact_path}:{marker.line}",
                        entity_id=base,
                        fix_hint=(
                            f"Update the marker to @v{current} after "
                            "confirming the implementation still satisfies "
                            f"{base}."
                        ),
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# #8 — data-model-drift (R-700-027)
# ---------------------------------------------------------------------------


def _model_field_names(source: str, class_name: str) -> set[str] | None:
    """Direct annotated field names of `class_name` in `source`, or None when
    the class is absent / the source doesn't parse. Inherited fields are out
    of scope (v1, R-700-027)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
            }
    return None


def check_data_model_drift(run_id: str, context: CheckContext) -> list[Finding]:
    """#8 (R-700-027) — compare a Pydantic model's field set against the
    canonical `fields:` declared on the `E-*` it implements (`model_name:`
    names the class). Opt-in : an entity without both `fields:` and
    `model_name:` is skipped (never false-positives)."""
    findings: list[Finding] = []
    for row in context.requirements:
        fields = row.get("fields")
        model_name = row.get("model_name")
        if not isinstance(fields, list) or not isinstance(model_name, str):
            continue
        declared = {str(f) for f in fields}
        eid_raw = row.get("entity_id") or row.get("id")
        entity_id = eid_raw if isinstance(eid_raw, str) else None

        actual: set[str] | None = None
        located_in: str | None = None
        for artifact in context.artifacts:
            found = _model_field_names(artifact.content, model_name)
            if found is not None:
                actual, located_in = found, artifact.path
                break

        if actual is None:
            findings.append(
                _make_finding(
                    run_id, "data-model-drift", Severity.BLOCKING,
                    message=(
                        f"Model `{model_name}` declared by {entity_id} was not "
                        "found in the run's code artifacts."
                    ),
                    entity_id=entity_id,
                    fix_hint=(
                        f"Add `{model_name}` or correct `model_name` on "
                        f"{entity_id}."
                    ),
                )
            )
            continue

        missing = sorted(declared - actual)
        extra = sorted(actual - declared)
        if missing or extra:
            parts: list[str] = []
            if missing:
                parts.append(f"missing {missing}")
            if extra:
                parts.append(f"extra {extra}")
            findings.append(
                _make_finding(
                    run_id, "data-model-drift", Severity.BLOCKING,
                    message=(
                        f"`{model_name}` drifts from {entity_id}: "
                        f"{'; '.join(parts)}."
                    ),
                    entity_id=entity_id,
                    artifact_ref=located_in,
                    fix_hint=(
                        f"Align `{model_name}` fields with {entity_id}'s "
                        "`fields:` declaration."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# #9 — cross-layer-coherence (R-700-028)
# ---------------------------------------------------------------------------


def check_cross_layer_coherence(
    run_id: str, context: CheckContext
) -> list[Finding]:
    """Defence in depth on top of C5's write-time guard (R-M100-070).

    Every project-level entity that declares ``tailoring-of: <parent>`` MUST
    also set ``override: true`` with a rationale. If an entity leaks through
    without the override (data corruption, admin import bypass), we surface
    it as a blocking finding here so the validation run refuses to pass.
    """
    findings: list[Finding] = []
    for row in context.requirements:
        tailoring_of = row.get("tailoring_of") or row.get("tailoring-of")
        if not isinstance(tailoring_of, str) or not tailoring_of:
            continue
        override = row.get("override")
        if override is True:
            continue
        entity_id_raw = row.get("entity_id") or row.get("id")
        entity_id = entity_id_raw if isinstance(entity_id_raw, str) else None
        findings.append(
            _make_finding(
                run_id,
                "cross-layer-coherence",
                Severity.BLOCKING,
                message=(
                    f"Entity {entity_id} declares tailoring-of:{tailoring_of} "
                    "but missing `override: true`. Project-level overrides of "
                    "a platform parent MUST be explicit."
                ),
                entity_id=entity_id,
                fix_hint=(
                    "Either set `override: true` with a rationale, or remove "
                    "`tailoring-of:` if no override is intended."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Dispatcher used by the plugin
# ---------------------------------------------------------------------------

_DISPATCH = {
    "req-without-code": check_req_without_code,
    "code-without-requirement": check_code_without_requirement,
    "interface-signature-drift": check_interface_signature_drift,
    "test-absent-for-requirement": check_test_absent_for_requirement,
    "orphan-test": check_orphan_test,
    "obsolete-reference": check_obsolete_reference,
    "version-drift": check_version_drift,
    "data-model-drift": check_data_model_drift,
    "cross-layer-coherence": check_cross_layer_coherence,
}


def dispatch(check_id: str, run_id: str, context: CheckContext) -> list[Finding]:
    """Invoke the named check. Raises ``KeyError`` if unknown."""
    return _DISPATCH[check_id](run_id, context)


def available_check_ids() -> list[str]:
    return list(_DISPATCH.keys())
