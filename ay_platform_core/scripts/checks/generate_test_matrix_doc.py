#!/usr/bin/env python3
# =============================================================================
# File: generate_test_matrix_doc.py
# Version: 1
# Path: ay_platform_core/scripts/checks/generate_test_matrix_doc.py
# Description: Renders `requirements/065-TEST-MATRIX.md` from the
#              authoritative `tests/e2e/auth_matrix/_catalog.py`. Run after
#              every catalog change so the documented matrix matches the
#              tests. Idempotent: a second run with no catalog change
#              produces a byte-identical file.
#
# Usage:
#   python ay_platform_core/scripts/checks/generate_test_matrix_doc.py \
#       --write requirements/065-TEST-MATRIX.md
#
#   # CI / pre-commit: assert no drift without writing
#   python ay_platform_core/scripts/checks/generate_test_matrix_doc.py \
#       --check requirements/065-TEST-MATRIX.md
# =============================================================================

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Tests live outside the installed package (`ay_platform_core/tests/` is
# sibling to `ay_platform_core/src/`). Add the sub-project root to sys.path
# so `tests.e2e.auth_matrix._catalog` resolves whether the script is run
# from the monorepo root or from `ay_platform_core/`.
_HERE = Path(__file__).resolve()
_SUBPROJECT_ROOT = _HERE.parents[2]  # .../ay_platform_core/
sys.path.insert(0, str(_SUBPROJECT_ROOT))

from tests.e2e.auth_matrix._catalog import (  # noqa: E402
    ALL_GLOBAL_ROLES,
    ALL_PROJECT_ROLES,
    ENDPOINTS,
    Auth,
    Backend,
    EndpointSpec,
    Scope,
)


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|")


def _accepted_label(spec: EndpointSpec) -> str:
    """Human-readable accepted-roles cell for the matrix table."""
    if spec.auth == Auth.OPEN:
        return "*(open)*"
    if spec.auth == Auth.AUTHENTICATED:
        return "any authenticated"
    parts: list[str] = []
    if spec.accept_global_roles:
        parts.extend(f"`{r}`" for r in spec.accept_global_roles)
    if spec.accept_roles:
        parts.extend(f"`{r}`" for r in spec.accept_roles)
    return " · ".join(parts) if parts else "—"


def _excluded_label(spec: EndpointSpec) -> str:
    if not spec.excluded_global_roles:
        return ""
    return " · ".join(f"`{r}`" for r in spec.excluded_global_roles)


def _scope_label(scope: Scope) -> str:
    return {Scope.NONE: "—", Scope.TENANT: "tenant", Scope.PROJECT: "project"}[scope]


def _backend_label(spec: EndpointSpec) -> str:
    if spec.backend == Backend.NONE:
        return "—"
    parts = [spec.backend.value]
    if spec.backend_collection:
        parts.append(f"`{spec.backend_collection}`")
    if spec.backend_bucket:
        parts.append(f"bucket `{spec.backend_bucket}`")
    return " · ".join(parts)


def render() -> str:
    """Build the Markdown document content from the catalog."""
    lines: list[str] = []
    lines.append("---")
    lines.append("document: 065-TEST-MATRIX")
    lines.append("version: 1")
    lines.append("path: requirements/065-TEST-MATRIX.md")
    lines.append("language: en")
    lines.append("status: approved")
    lines.append("derives-from: [E-100-002]")
    lines.append("---")
    lines.append("")
    lines.append("# Auth × Role × Scope Test Matrix")
    lines.append("")
    lines.append(
        "> **Auto-generated.** Source of truth: "
        "[`tests/e2e/auth_matrix/_catalog.py`]"
        "(../ay_platform_core/tests/e2e/auth_matrix/_catalog.py). "
        "Regenerate via "
        "`python ay_platform_core/scripts/checks/generate_test_matrix_doc.py "
        "--write requirements/065-TEST-MATRIX.md`."
    )
    lines.append("")
    lines.append("## 1. Test strategy")
    lines.append("")
    lines.append(
        "Every HTTP route exposed by any platform component is exercised "
        "along **five dimensions** (E-100-002 v2 verification clause):"
    )
    lines.append("")
    lines.append(
        "1. **Anonymous access** — no identity headers, no Bearer JWT. "
        "Endpoint MUST NOT return a 2xx. "
        "(`tests/e2e/auth_matrix/test_anonymous_access.py`)"
    )
    lines.append(
        "2. **Role gate** — for every ROLE_GATED endpoint, an authenticated "
        "user lacking the required role MUST receive 403; a user holding "
        "any of the accepted roles MUST clear the gate. "
        "(`tests/e2e/auth_matrix/test_role_matrix.py`)"
    )
    lines.append(
        "3. **Cross-tenant isolation** — same role, wrong `X-Tenant-Id`. "
        "MUST return 403/404 (no leak). "
        "(`tests/e2e/auth_matrix/test_isolation.py`)"
    )
    lines.append(
        "4. **Cross-project isolation** — correct tenant, role granted on "
        "a DIFFERENT project. MUST return 403/404. "
        "(`tests/e2e/auth_matrix/test_isolation.py`)"
    )
    lines.append(
        "5. **Backend state** — write/delete endpoints SHALL be observable "
        "in ArangoDB / MinIO after a successful call; the matrix asserts "
        "directly on the persistence layer. "
        "(`tests/e2e/auth_matrix/test_backend_state.py`)"
    )
    lines.append("")
    lines.append(
        "Authentication-mode coverage (`local` / `entraid` / `none`) is "
        "tested at the C2 boundary in "
        "`tests/e2e/auth_matrix/test_auth_modes.py` — the modes only "
        "differ in HOW the JWT is minted; downstream components consume "
        "the same forward-auth headers regardless."
    )
    lines.append("")
    lines.append("## 2. Role hierarchy (E-100-002 v2)")
    lines.append("")
    lines.append("**Global roles**:")
    lines.append("")
    for role in ALL_GLOBAL_ROLES:
        if role == "tenant_manager":
            note = "super-root, content-blind. Tenant lifecycle ONLY."
        elif role in ("admin", "tenant_admin"):
            note = "tenant-scoped admin (synonyms in v2)."
        else:
            note = "baseline authenticated user."
        lines.append(f"- `{role}` — {note}")
    lines.append("")
    lines.append("**Project-scoped roles** (per-project, in JWT `project_scopes`):")
    lines.append("")
    for role in ALL_PROJECT_ROLES:
        lines.append(f"- `{role}`")
    lines.append("")
    lines.append("## 3. Endpoint catalog")
    lines.append("")
    lines.append(
        f"**{len(ENDPOINTS)} endpoints** across "
        f"{len({e.component for e in ENDPOINTS})} components. Order: "
        f"by component, method, path."
    )
    lines.append("")

    components_seen: set[str] = set()
    for spec in ENDPOINTS:
        if spec.component not in components_seen:
            components_seen.add(spec.component)
            lines.append(f"### {spec.component}")
            lines.append("")
            lines.append(
                "| Method | Path | Auth | Scope | Accepted roles | Excluded | "
                "Backend | Status |"
            )
            lines.append(
                "|---|---|---|---|---|---|---|---|"
            )
        lines.append(
            "| `{m}` | `{p}` | {a} | {s} | {ar} | {ex} | {bk} | {sc} |".format(
                m=spec.method,
                p=_md_escape(spec.path),
                a=spec.auth.value,
                s=_scope_label(spec.scope),
                ar=_accepted_label(spec),
                ex=_excluded_label(spec) or "—",
                bk=_backend_label(spec),
                sc=spec.success_status,
            )
        )
        # Add a blank line after the last row of a component when the
        # next iteration is a different component. We detect this by
        # peeking; simpler: emit a blank line after the row when the
        # next spec has a different component.
    # Trailing blank line for the last block.
    lines.append("")

    lines.append("## 4. Maintenance contract")
    lines.append("")
    lines.append(
        "Adding a new HTTP route to any component is a **two-step** change:"
    )
    lines.append("")
    lines.append(
        "1. Implement the route in the component's `router.py` with the "
        "appropriate `_require_role(...)` gate."
    )
    lines.append(
        "2. Add an `EndpointSpec` row to `tests/e2e/auth_matrix/_catalog.py` "
        "describing the route's auth, scope, accepted roles, and backend. "
        "Re-run "
        "`python ay_platform_core/scripts/checks/generate_test_matrix_doc.py "
        "--write requirements/065-TEST-MATRIX.md` to refresh this document."
    )
    lines.append("")
    lines.append(
        "The coherence test "
        "[`tests/coherence/test_route_catalog.py`]"
        "(../ay_platform_core/tests/coherence/test_route_catalog.py) "
        "fails the build if step 2 is skipped."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**End of 065-TEST-MATRIX.md v1.**")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--write", type=Path, help="Write the rendered doc to PATH.")
    grp.add_argument(
        "--check",
        type=Path,
        help="Compare PATH to the rendered output; exit 1 if they differ.",
    )
    args = parser.parse_args()

    rendered = render()

    if args.write:
        args.write.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.write} ({len(ENDPOINTS)} endpoints)")
        return 0

    target: Path = args.check
    if not target.exists():
        print(f"missing: {target}", file=sys.stderr)
        return 1
    actual = target.read_text(encoding="utf-8")
    if actual != rendered:
        print(
            f"drift detected: {target} does not match the catalog. "
            f"Run --write to refresh.",
            file=sys.stderr,
        )
        return 1
    print(f"{target}: OK ({len(ENDPOINTS)} endpoints)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
