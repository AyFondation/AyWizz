#!/usr/bin/env python3
# =============================================================================
# File: audit_validation_reachability.py
# Version: 1
# Path: ay_platform_core/scripts/checks/audit_validation_reachability.py
# Description: Coherence audit — a test that CLAIMS to validate a requirement
#              must actually reach the code that implements it.
#
#              THE GAP THIS CLOSES. `audit_implementation_status.py` counts
#              `@relation implements:` markers in `src/` and
#              `@relation validates:` markers in `tests/`, then reports a
#              requirement as `tested` when BOTH exist. The two sets are
#              never cross-checked: a test file can CLAIM to validate a
#              requirement while importing nothing from the module that
#              claims to implement it. The requirement then reads `tested` in
#              `060-IMPLEMENTATION-STATUS.md` on the strength of a comment
#              alone — which is the traceability equivalent of the coverage
#              number counting a test that asserts nothing.
#
#              WHAT IT PROVES, AND WHAT IT DOES NOT. Reachability through the
#              import graph is a NECESSARY condition, not a sufficient one: a
#              test that imports the implementing module may still assert
#              nothing useful about it (that is §10's territory, and no static
#              analysis settles it). But the converse is decisive — a test
#              that cannot even reach the module cannot be validating it, and
#              that is a mechanical fact worth failing a build on.
#
#              METHOD. Static import graph over `ay_platform_core.*`, walked
#              transitively from each validating test: a test that imports a
#              service which imports the repository reaches the repository.
#              Depth is bounded because the graph is small and acyclic enough
#              in practice; cycles are handled by the visited set.
#
# Usage:  python ay_platform_core/scripts/checks/audit_validation_reachability.py
#         Exit code 1 if any requirement has validators that reach no
#         implementer.
# =============================================================================

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REQUIREMENTS = ROOT / "requirements"
SRC = ROOT / "ay_platform_core" / "src"
TESTS = ROOT / "ay_platform_core" / "tests"
PKG = "ay_platform_core"

_RELATION_RE = re.compile(r"@relation\s+(implements|validates):\s*([^\n]+)")
_R_ID_RE = re.compile(r"R-\d+-\d+")


def _scan_markers() -> tuple[dict[str, set[Path]], dict[str, set[Path]]]:
    """(implementers, validators) keyed by requirement id."""
    impls: dict[str, set[Path]] = defaultdict(set)
    vals: dict[str, set[Path]] = defaultdict(set)
    for root, bucket in ((SRC, impls), (TESTS, vals)):
        kind = "implements" if root is SRC else "validates"
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in _RELATION_RE.finditer(text):
                if match.group(1) != kind:
                    continue
                for rid in _R_ID_RE.findall(match.group(2)):
                    bucket[rid].add(path)
    return impls, vals


def _module_name(path: Path) -> str | None:
    """Dotted `ay_platform_core.*` name for a file under src/, else None."""
    try:
        rel = path.relative_to(SRC)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or None


def _imports_of(path: Path) -> set[str]:
    """Dotted `ay_platform_core.*` modules a file imports, directly."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PKG):
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — resolved below by prefix
                continue
            if node.module and node.module.startswith(PKG):
                out.add(node.module)
                # `from pkg.mod import name` may name a SUBMODULE, not a symbol.
                for alias in node.names:
                    out.add(f"{node.module}.{alias.name}")
    return out


def _build_src_graph() -> dict[str, set[str]]:
    """module -> modules it imports, for every file under src/."""
    graph: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        name = _module_name(path)
        if name:
            graph[name] = _imports_of(path)
    return graph


def _seed_modules(test_path: Path) -> set[str]:
    """Modules a test reaches directly — its own imports PLUS every
    `conftest.py` above it.

    Pytest injects fixtures by NAME, with no import statement in the test
    file, so a graph rooted at the test alone sees nothing a fixture built.
    `tests/integration/c4_orchestrator/test_documents_structural_ops.py`
    reads as importing only `httpx` and `pytest` while actually exercising
    the whole documents router through an app its conftest assembled. Every
    conftest from the test's directory up to `tests/` is therefore part of
    the test's reachable surface — which is exactly pytest's own fixture
    resolution order.
    """
    seeds = _imports_of(test_path)
    directory = test_path.parent
    while True:
        conftest = directory / "conftest.py"
        if conftest.exists():
            seeds |= _imports_of(conftest)
        if directory == TESTS or TESTS not in directory.parents:
            break
        directory = directory.parent
    return seeds


def _reachable(seeds: set[str], graph: dict[str, set[str]]) -> set[str]:
    """Transitive closure of `seeds` over the source import graph."""
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        stack.extend(graph.get(mod, ()))
    return seen


def check() -> list[str]:
    """Issue strings, empty when every `validates:` marker reaches its code.

    Exposed with this name and shape so `tests/coherence/` can call it in
    process, like every other check under `scripts/checks/`.
    """
    impls, vals = _scan_markers()
    graph = _build_src_graph()

    findings: list[str] = []
    for rid in sorted(set(impls) & set(vals)):
        impl_modules = {m for p in impls[rid] if (m := _module_name(p))}
        if not impl_modules:
            continue
        # BLACK-BOX TIERS ARE EXCLUDED, not exempted by accident. Tests under
        # `tests/system/` drive a deployed stack over HTTP through Traefik;
        # importing the implementation would defeat their purpose. For them
        # the import graph says nothing, so counting them as unreachable
        # would make this audit cry wolf — and a check that cries wolf gets
        # switched off, which costs more than it ever caught.
        in_process = {p for p in vals[rid] if (TESTS / "system") not in p.parents}
        if not in_process:
            continue
        reached: set[str] = set()
        for test_path in in_process:
            reached |= _reachable(_seed_modules(test_path), graph)
        if not (reached & impl_modules):
            tests = ", ".join(
                sorted(str(p.relative_to(ROOT)) for p in in_process)
            )
            mods = ", ".join(sorted(impl_modules))
            findings.append(
                f"{rid}: validating test(s) reach no implementing module\n"
                f"    implements: {mods}\n"
                f"    validates : {tests}"
            )

    return findings


def main() -> int:
    findings = check()
    print(f"unreachable validations: {len(findings)}\n")
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
