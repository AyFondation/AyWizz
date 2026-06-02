# =============================================================================
# File: test_ui_contract_snapshot.py
# Version: 1
# Path: ay_platform_core/tests/coherence/test_ui_contract_snapshot.py
# Description: Pins the UI contract snapshot
#              (`ay_platform_ui/tests/contract/backend-routes.json`) to the
#              auth-matrix catalog. The snapshot is consumed by the
#              TypeScript contract test
#              (`ay_platform_ui/tests/contract/api-surface.test.ts`) to
#              assert every endpoint `lib/apiClient.ts` calls exists in the
#              backend. This test fails the build if the snapshot drifts
#              from the catalog — keeping the chain honest:
#              live routers → _catalog.py (test_route_catalog.py)
#              → backend-routes.json (this test) → UI client (api-surface.test.ts).
#
#              Regenerate the snapshot when this fails:
#                python ay_platform_core/scripts/checks/export_route_catalog_json.py \
#                    --write ay_platform_ui/tests/contract/backend-routes.json
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

pytestmark = pytest.mark.coherence

_HERE = Path(__file__).resolve()
_MONOREPO_ROOT = _HERE.parents[3]
_SCRIPT = (
    _MONOREPO_ROOT
    / "ay_platform_core"
    / "scripts"
    / "checks"
    / "export_route_catalog_json.py"
)
_SNAPSHOT = _MONOREPO_ROOT / "ay_platform_ui" / "tests" / "contract" / "backend-routes.json"


def _load_render() -> Callable[[], str]:
    """Import the generator module by file path and return its `render`."""
    spec = importlib.util.spec_from_file_location("export_route_catalog_json", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("Callable[[], str]", module.render)


def test_snapshot_exists() -> None:
    assert _SNAPSHOT.exists(), (
        f"missing UI contract snapshot: {_SNAPSHOT}. Generate it via "
        f"export_route_catalog_json.py --write."
    )


def test_snapshot_matches_catalog() -> None:
    render = _load_render()
    expected = render()
    actual = _SNAPSHOT.read_text(encoding="utf-8")
    assert actual == expected, (
        "UI contract snapshot drift: backend-routes.json no longer matches the "
        "auth-matrix catalog. Regenerate via "
        "`python ay_platform_core/scripts/checks/export_route_catalog_json.py "
        "--write ay_platform_ui/tests/contract/backend-routes.json`."
    )
