# =============================================================================
# File: test_functional_coverage.py
# Version: 1
# Path: ay_platform_core/tests/coherence/test_functional_coverage.py
# Description: Coherence test pinning the functional-coverage of every
#              catalogued endpoint. Complements the auth-matrix:
#              auth-matrix verifies the AUTH dimension (anonymous,
#              role gate, isolation) for every endpoint; this test
#              verifies that EVERY endpoint also has at least one
#              FUNCTIONAL test outside the auth_matrix tier (something
#              that exercises its business behaviour with valid input).
#
#              Adding a new EndpointSpec without ALSO adding a
#              functional test fails the build here. This is the gap
#              CLAUDE.md §13 leaves implicit; this test makes it
#              explicit.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.e2e.auth_matrix._catalog import ENDPOINTS, EndpointSpec

pytestmark = pytest.mark.coherence


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_DIRS = [
    _REPO_ROOT / "tests" / "integration",
    _REPO_ROOT / "tests" / "e2e",
    _REPO_ROOT / "tests" / "system",
]
_EXCLUDED_DIRS = [_REPO_ROOT / "tests" / "e2e" / "auth_matrix"]

_URL_LITERAL_RE = re.compile(
    r"""(?:f|rf|fr)?["']
        (/[A-Za-z0-9_\-/{}.?=&%@,:;!*'+]+)
        ["']
    """,
    re.VERBOSE,
)
_PLACEHOLDER_RE = re.compile(r"^\{[^}]+\}$")


def _list_test_files() -> list[Path]:
    out: list[Path] = []
    for d in _TEST_DIRS:
        if not d.exists():
            continue
        for f in d.rglob("test_*.py"):
            if any(str(f).startswith(str(ex)) for ex in _EXCLUDED_DIRS):
                continue
            out.append(f)
    return out


def _extract_path_literals(text: str) -> set[str]:
    out: set[str] = set()
    for match in _URL_LITERAL_RE.finditer(text):
        url = match.group(1).split("?", 1)[0]
        if url.startswith(("/auth", "/admin", "/api/v1", "/ux")):
            out.add(url)
    return out


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _segment_match(catalog_seg: str, test_seg: str) -> bool:
    if _PLACEHOLDER_RE.fullmatch(catalog_seg):
        return True
    if _PLACEHOLDER_RE.fullmatch(test_seg):
        return True
    return catalog_seg == test_seg


def _path_matches(catalog_path: str, test_url: str) -> bool:
    cat = _segments(catalog_path)
    tst = _segments(test_url)
    if len(cat) != len(tst):
        return False
    return all(_segment_match(c, t) for c, t in zip(cat, tst, strict=True))


def _is_covered(spec: EndpointSpec, files_with_text: list[tuple[Path, str]]) -> bool:
    method_marker = f".{spec.method.lower()}("
    for _f, text in files_with_text:
        if method_marker not in text and ".request(" not in text:
            continue
        urls = _extract_path_literals(text)
        if any(_path_matches(spec.path, u) for u in urls):
            return True
    return False


def test_every_endpoint_has_a_functional_test() -> None:
    """Every EndpointSpec in `_catalog.py` SHALL be exercised by at
    least one test outside `tests/e2e/auth_matrix/`. The auth-matrix
    proves the auth gates fire; functional tests prove the endpoint's
    behaviour is correct.

    Failure mode: a contributor adds a new route + EndpointSpec but
    skips the functional test. This test fails at CI; the fix is to
    add (at minimum) a smoke test that posts/gets the endpoint and
    asserts on the success status + a key field of the response."""
    files_with_text = [
        (f, f.read_text(encoding="utf-8")) for f in _list_test_files()
    ]
    uncovered: list[str] = []
    for spec in ENDPOINTS:
        if not _is_covered(spec, files_with_text):
            uncovered.append(f"{spec.method:6s} {spec.path} ({spec.component})")
    assert not uncovered, (
        f"{len(uncovered)} endpoint(s) catalogued in _catalog.py have NO "
        f"functional test outside the auth_matrix tier. Auth-matrix covers "
        f"the auth dimension exhaustively but does NOT verify business "
        f"behaviour. Add at minimum a smoke test for each:\n  "
        + "\n  ".join(uncovered)
    )
