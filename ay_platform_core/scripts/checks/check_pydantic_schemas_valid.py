#!/usr/bin/env python3
# =============================================================================
# File: check_pydantic_schemas_valid.py
# Version: 1
# Path: ay_platform_core/scripts/checks/check_pydantic_schemas_valid.py
# Description: Coherence check — every contract schema in the registry is a
#              valid Pydantic BaseModel, JSON-serialisable, and fully typed.
#              Run from ay_platform_core/: python scripts/checks/check_pydantic_schemas_valid.py
# =============================================================================

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, get_args, get_origin

# Add ay_platform_core/ to path so tests.* is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pydantic import BaseModel

from tests.fixtures.contract_registry import get_registry


def _is_bare_any(annotation: Any) -> bool:
    """True if the annotation is literally typing.Any (not e.g. Any inside Optional)."""
    return annotation is Any


def check() -> list[str]:
    issues: list[str] = []
    registry = get_registry()

    if not registry:
        print("  (no contracts registered yet — trivially OK)")
        return issues

    for contract in registry:
        label = f"{contract.producer}.{contract.name}"
        schema = contract.schema

        # 1. Must be a Pydantic BaseModel subclass
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            issues.append(f"  {label}: schema {schema!r} is not a Pydantic BaseModel")
            continue

        # 2. model_json_schema() must not raise
        try:
            schema.model_json_schema()
        except Exception as exc:
            issues.append(f"  {label}: model_json_schema() raised {type(exc).__name__}: {exc}")
            continue

        # 3. No field with bare Any annotation
        for field_name, field_info in schema.model_fields.items():
            ann = field_info.annotation
            if ann is None:
                issues.append(f"  {label}.{field_name}: untyped field (annotation is None)")
            elif _is_bare_any(ann):
                issues.append(f"  {label}.{field_name}: typed as bare Any")

    return issues


if __name__ == "__main__":
    issues = check()
    if issues:
        print("FAIL: Pydantic schema validation issues:")
        for line in issues:
            print(line)
        sys.exit(1)
    registry = get_registry()
    print(f"OK: {len(registry)} registered schemas are valid Pydantic BaseModels")
