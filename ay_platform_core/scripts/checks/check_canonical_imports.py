#!/usr/bin/env python3
# =============================================================================
# File: check_canonical_imports.py
# Version: 1
# Path: ay_platform_core/scripts/checks/check_canonical_imports.py
# Description: Coherence check — AST scan of src/ to verify that every absolute
#              import of a registered contract type uses the canonical module path.
#              Relative imports (within-package) are exempt.
#              Designed to catch cross-component copy-paste and bad re-exports.
#              Run from ay_platform_core/: python scripts/checks/check_canonical_imports.py
# =============================================================================

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).parent.parent.parent / "src"
MONOREPO_ROOT = Path(__file__).parent.parent.parent

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tests.fixtures.contract_registry import get_registry


def check() -> list[str]:
    issues: list[str] = []
    registry = get_registry()

    if not registry:
        print("  (no contracts registered yet — trivially OK)")
        return issues

    # type name → canonical module path
    canonical: dict[str, str] = {}
    for c in registry:
        if isinstance(c.schema, type):
            canonical[c.schema.__name__] = c.schema.__module__

    for py_file in SRC_ROOT.rglob("*.py"):
        rel_path = py_file.relative_to(MONOREPO_ROOT)

        # Derive dotted module name of this file
        rel = py_file.relative_to(SRC_ROOT)
        module_dot = str(rel).replace("/", ".").replace("\\", ".").removesuffix(".py")

        # Skip the canonical modules themselves
        canonical_mods = set(canonical.values())
        if module_dot in canonical_mods:
            continue

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue

            # Skip relative imports (fine within a package)
            if node.level > 0:
                continue

            from_module = node.module or ""

            for alias in node.names:
                name = alias.name
                if name not in canonical:
                    continue

                expected_module = canonical[name]
                if from_module != expected_module:
                    issues.append(
                        f"  {rel_path}:{node.lineno} imports '{name}' from "
                        f"'{from_module}' — expected canonical '{expected_module}'"
                    )

    return issues


if __name__ == "__main__":
    issues = check()
    if issues:
        print("FAIL: Non-canonical contract imports detected:")
        for line in issues:
            print(line)
        sys.exit(1)
    n_files = len(list(SRC_ROOT.rglob("*.py")))
    print(f"OK: all contract imports are canonical across {n_files} source files")
