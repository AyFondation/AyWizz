#!/usr/bin/env python3
# =============================================================================
# File: export_route_catalog_json.py
# Version: 1
# Path: ay_platform_core/scripts/checks/export_route_catalog_json.py
# Description: Exports the authoritative HTTP-route surface from
#              `tests/e2e/auth_matrix/_catalog.py` as a language-neutral
#              JSON snapshot consumed by the UI contract test
#              (`ay_platform_ui/tests/contract/api-surface.test.ts`).
#
#              The snapshot is the bridge that lets the TypeScript test
#              assert every endpoint `lib/apiClient.ts` calls exists in
#              the backend, WITHOUT the UI re-declaring the route list.
#              `_catalog.py` is itself pinned to the live FastAPI routers
#              by `tests/coherence/test_route_catalog.py`; this snapshot
#              is pinned to `_catalog.py` by
#              `tests/coherence/test_ui_contract_snapshot.py`. The chain
#              is: live routers → catalog → snapshot → UI client.
#
# Usage:
#   python ay_platform_core/scripts/checks/export_route_catalog_json.py \
#       --write ay_platform_ui/tests/contract/backend-routes.json
#
#   # CI / pre-commit: assert no drift without writing
#   python ay_platform_core/scripts/checks/export_route_catalog_json.py \
#       --check ay_platform_ui/tests/contract/backend-routes.json
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Tests live outside the installed package (`ay_platform_core/tests/` is
# sibling to `ay_platform_core/src/`). Add the sub-project root to sys.path
# so `tests.e2e.auth_matrix._catalog` resolves whether the script is run
# from the monorepo root or from `ay_platform_core/`.
_HERE = Path(__file__).resolve()
_SUBPROJECT_ROOT = _HERE.parents[2]  # .../ay_platform_core/
sys.path.insert(0, str(_SUBPROJECT_ROOT))

from tests.e2e.auth_matrix._catalog import ENDPOINTS  # noqa: E402


def render() -> str:
    """Build the deterministic JSON snapshot from the catalog.

    Only the fields the UI contract test needs are emitted (method, path,
    component, auth) so the snapshot stays stable across catalog metadata
    churn (role/scope/backend edits do not rewrite it). Sorted by
    (path, method) for byte-stable output.
    """
    routes = sorted(
        (
            {
                "method": spec.method.upper(),
                "path": spec.path,
                "component": spec.component,
                "auth": spec.auth.value,
            }
            for spec in ENDPOINTS
        ),
        key=lambda r: (r["path"], r["method"]),
    )
    doc = {
        "_comment": (
            "AUTO-GENERATED from ay_platform_core/tests/e2e/auth_matrix/"
            "_catalog.py. Do not hand-edit. Regenerate via "
            "ay_platform_core/scripts/checks/export_route_catalog_json.py "
            "--write. Pinned by tests/coherence/test_ui_contract_snapshot.py."
        ),
        "route_count": len(routes),
        "routes": routes,
    }
    # Trailing newline so the file is POSIX-clean and diff-friendly.
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--write", type=Path, help="Write the snapshot to PATH.")
    grp.add_argument(
        "--check",
        type=Path,
        help="Compare PATH to the rendered snapshot; exit 1 if they differ.",
    )
    args = parser.parse_args()

    rendered = render()

    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.write} ({len(ENDPOINTS)} endpoints)")
        return 0

    target: Path = args.check
    if not target.exists():
        print(f"missing: {target}", file=sys.stderr)
        return 1
    if target.read_text(encoding="utf-8") != rendered:
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
