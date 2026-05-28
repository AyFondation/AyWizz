#!/usr/bin/env python3
# =============================================================================
# File: check_schema_isolation.py
# Version: 1
# Path: ay_platform_core/scripts/checks/check_schema_isolation.py
# Description: Coherence check — public schemas must not expose private/internal
#              fields from their internal counterparts. Extensible: add new pairs
#              as new components are implemented.
#              Run from ay_platform_core/: python scripts/checks/check_schema_isolation.py
# =============================================================================

from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class IsolationRule(NamedTuple):
    public_model: type[BaseModel]
    internal_model: type[BaseModel]
    private_fields: frozenset[str]
    label: str


def _build_rules() -> list[IsolationRule]:
    rules: list[IsolationRule] = []

    # C2: UserPublic must not expose UserInternal private fields
    from ay_platform_core.c2_auth.models import UserInternal, UserPublic

    c2_private = frozenset(UserInternal.model_fields) - frozenset(UserPublic.model_fields)
    rules.append(
        IsolationRule(
            public_model=UserPublic,
            internal_model=UserInternal,
            private_fields=c2_private,
            label="C2: UserPublic vs UserInternal",
        )
    )

    # Add future rules here as new components are implemented.
    # Example pattern:
    #   from ay_platform_core.c3_conversation.models import ConversationPublic, ConversationInternal
    #   c3_private = frozenset(ConversationInternal.model_fields) - frozenset(ConversationPublic.model_fields)
    #   rules.append(IsolationRule(ConversationPublic, ConversationInternal, c3_private, "C3: ..."))

    return rules


def check() -> list[str]:
    issues: list[str] = []
    rules = _build_rules()

    for rule in rules:
        public_fields = frozenset(rule.public_model.model_fields)

        # Check private fields don't leak into the public model
        leaked = rule.private_fields & public_fields
        if leaked:
            issues.append(
                f"  {rule.label}: public model leaks private fields: {sorted(leaked)}"
            )

        # Check internal model contains all public fields (supertype consistency)
        internal_fields = frozenset(rule.internal_model.model_fields)
        orphaned = public_fields - internal_fields
        if orphaned:
            issues.append(
                f"  {rule.label}: public model has fields absent in internal model: {sorted(orphaned)}"
            )

    return issues


if __name__ == "__main__":
    issues = check()
    if issues:
        print("FAIL: Schema isolation violations:")
        for line in issues:
            print(line)
        sys.exit(1)
    rules = _build_rules()
    total_private = sum(len(r.private_fields) for r in rules)
    print(f"OK: {len(rules)} isolation rule(s) pass — {total_private} private field(s) correctly contained")
