#!/usr/bin/env python3
# =============================================================================
# File: check_no_parallel_definitions.py
# Version: 3
# Path: ay_platform_core/scripts/checks/check_no_parallel_definitions.py
# Description: Coherence check — AST scan of src/ to detect classes that shadow
#              a registered contract name or share >= OVERLAP_THRESHOLD field names
#              with a registered contract (potential copy-paste drift).
#              Run from ay_platform_core/: python scripts/checks/check_no_parallel_definitions.py
#              v3: conventional scoping and audit columns are excluded from
#                  the field-overlap count. Every persisted entity in this
#                  platform carries project_id / tenant_id / created_at /
#                  created_by / updated_at / updated_by / version by
#                  convention, so their co-occurrence is guaranteed and
#                  carries NO information about copy-paste. Counting them
#                  made every new model module fail the build until one of
#                  its models was registered as a contract — which pushed
#                  contract registration ahead of the route that serves it.
#                  Detection power is unchanged on domain fields: a real
#                  duplicate shares entity_id / status / category / title /
#                  body, none of which are excluded.
#              v2: dataclasses decorated with @dataclass (even frozen) are
#                  excluded from field-overlap detection. They are by
#                  construction internal value objects (dispatch envelopes,
#                  report structures) whose id-carrying fields legitimately
#                  mirror public-contract identifiers without being parallel
#                  definitions.
# =============================================================================

from __future__ import annotations

import ast
import sys
from pathlib import Path

from pydantic import BaseModel

SRC_ROOT = Path(__file__).parent.parent.parent / "src"
MONOREPO_ROOT = Path(__file__).parent.parent.parent
OVERLAP_THRESHOLD = 3  # ≥ this many shared DOMAIN field names is suspicious

#: Fields every persisted entity carries by platform convention. Sharing
#: them is evidence of following the convention, not of copying a contract,
#: so they are removed before the overlap is counted. Keep this list short
#: and justified: each entry is a field the codebase attaches structurally
#: (tenant/project scoping, the audit quartet, the version counter), never a
#: domain concept.
CONVENTIONAL_FIELDS: frozenset[str] = frozenset(
    {
        "project_id",
        "tenant_id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "version",
    }
)


def significant_overlap(
    class_fields: set[str], contract_fields: set[str]
) -> set[str]:
    """Return the shared fields that actually suggest a parallel definition.

    Args:
        class_fields: Annotated fields of the class under inspection.
        contract_fields: Fields of a registered contract.

    Returns:
        The intersection, minus the conventional scoping and audit columns.
    """
    return (class_fields & contract_fields) - CONVENTIONAL_FIELDS

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tests.fixtures.contract_registry import get_registry


def _annotated_fields(cls_node: ast.ClassDef) -> set[str]:
    """Return annotated assignment names directly in the class body."""
    fields: set[str] = set()
    for node in cls_node.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            fields.add(node.target.id)
    return fields


def _is_dataclass(cls_node: ast.ClassDef) -> bool:
    """True when the class is decorated with @dataclass (bare or parameterised).

    Dataclasses in this codebase are internal value objects (dispatch
    envelopes, report structures) — they legitimately share
    id-carrying fields with public Pydantic contracts without being
    parallel definitions.
    """
    for decorator in cls_node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "dataclass":
            return True
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
            if decorator.func.id == "dataclass":
                return True
    return False


def _canonical_modules() -> set[str]:
    """Return the __module__ of every registered contract schema."""
    modules: set[str] = set()
    for c in get_registry():
        if isinstance(c.schema, type):
            modules.add(c.schema.__module__)
    return modules


def check() -> list[str]:
    issues: list[str] = []
    registry = get_registry()

    if not registry:
        print("  (no contracts registered yet — trivially OK)")
        return issues

    canonical_mods = _canonical_modules()

    # contract name → canonical module
    contract_names: dict[str, str] = {}
    # contract name → field set
    contract_fields: dict[str, set[str]] = {}

    for c in registry:
        if isinstance(c.schema, type) and issubclass(c.schema, BaseModel):
            contract_names[c.schema.__name__] = c.schema.__module__
            contract_fields[c.schema.__name__] = set(c.schema.model_fields.keys())

    for py_file in SRC_ROOT.rglob("*.py"):
        rel = py_file.relative_to(SRC_ROOT)
        module_dot = str(rel).replace("/", ".").replace("\\", ".").removesuffix(".py")

        # Skip canonical modules themselves
        if module_dot in canonical_mods:
            continue

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            cls_name = node.name
            rel_path = py_file.relative_to(MONOREPO_ROOT)

            # Check 1: class name matches a registered contract
            if cls_name in contract_names:
                issues.append(
                    f"  {rel_path}:{node.lineno} class '{cls_name}' shadows "
                    f"registered contract in '{contract_names[cls_name]}'"
                )
                continue

            # Check 2: field overlap with registered contracts.
            # Dataclasses are internal value objects by convention; skip them
            # so legitimate envelopes (DispatchRequest, ReconcileReport, …)
            # don't trigger false positives on shared id fields.
            if _is_dataclass(node):
                continue

            cls_fields = _annotated_fields(node)
            if len(cls_fields) < OVERLAP_THRESHOLD:
                continue

            for contract_name, reg_fields in contract_fields.items():
                overlap = significant_overlap(cls_fields, reg_fields)
                if len(overlap) >= OVERLAP_THRESHOLD:
                    issues.append(
                        f"  {rel_path}:{node.lineno} class '{cls_name}' shares "
                        f"{len(overlap)} fields with contract '{contract_name}': "
                        f"{sorted(overlap)} — possible parallel definition"
                    )

    return issues


if __name__ == "__main__":
    issues = check()
    if issues:
        print("FAIL: Parallel contract definitions detected:")
        for line in issues:
            print(line)
        sys.exit(1)
    n_files = len(list(SRC_ROOT.rglob("*.py")))
    print(f"OK: no parallel definitions found across {n_files} source files")
