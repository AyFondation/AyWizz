#!/usr/bin/env python3
# =============================================================================
# File: audit_functional_coverage.py
# Version: 1
# Path: ay_platform_core/scripts/checks/audit_functional_coverage.py
# Description: For every EndpointSpec in
#              `tests/e2e/auth_matrix/_catalog.py`, determine whether
#              there is at least ONE non-auth_matrix test file that
#              references the endpoint's path. Output a
#              functional-coverage report classifying each endpoint:
#
#                - "functional"  → path appears in tests/integration/*
#                                  or tests/e2e/* (excluding auth_matrix/).
#                - "auth-only"   → path appears ONLY in auth_matrix
#                                  tests (auth/role/isolation only).
#
#              Auth-only is NOT a bug per se — auth-matrix DOES verify
#              "anonymous → 401, role gate works" for every endpoint.
#              But it does NOT verify the endpoint's business behaviour.
#              This audit surfaces the gap so we can prioritise filling
#              it for critical paths.
# =============================================================================

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_SUBPROJECT_ROOT = _HERE.parents[2]
sys.path.insert(0, str(_SUBPROJECT_ROOT))

from tests.e2e.auth_matrix._catalog import (  # noqa: E402
    ENDPOINTS,
    Auth,
    EndpointSpec,
)


_TEST_DIRS = [
    _SUBPROJECT_ROOT / "tests" / "integration",
    _SUBPROJECT_ROOT / "tests" / "e2e",
    _SUBPROJECT_ROOT / "tests" / "system",
]
# Excluded — auth_matrix is the auto-paramétré dimension; we want to
# know which endpoints have FUNCTIONAL tests on top.
_EXCLUDED_DIRS = [
    _SUBPROJECT_ROOT / "tests" / "e2e" / "auth_matrix",
]


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


# Regex extracting every quoted URL-shaped string from a test file.
# Captures both regular strings and f-strings; strips the f"" wrapper.
# Character class is permissive (URL-safe chars + query string chars +
# f-string braces); query strings are stripped post-match.
_URL_LITERAL_RE = re.compile(
    r"""(?:f|rf|fr)?["']                              # opening
        (/[A-Za-z0-9_\-/{}.?=&%@,:;!*'+]+)            # URL path + optional query
        ["']                                           # closing
    """,
    re.VERBOSE,
)


def _extract_path_literals(file_text: str) -> set[str]:
    """Pull every URL-shaped literal out of a test file. F-string
    placeholders `{var}` are kept as-is so we can match against the
    catalog template by normalising both sides. Query strings
    (`?key=value`) are stripped before comparison."""
    out: set[str] = set()
    for match in _URL_LITERAL_RE.finditer(file_text):
        url = match.group(1)
        if not url.startswith(("/auth", "/admin", "/api/v1", "/ux")):
            continue
        # Drop query string for catalog matching (catalog paths never
        # contain query params; tests routinely append filters).
        path_only = url.split("?", 1)[0]
        out.add(path_only)
    return out


_PLACEHOLDER_RE = re.compile(r"^\{[^}]+\}$")


def _segments(path: str) -> list[str]:
    """Split a URL path into segments, dropping leading and trailing
    empties so `/foo/bar/` and `/foo/bar` compare equal."""
    return [s for s in path.split("/") if s]


def _segment_match(catalog_seg: str, test_seg: str) -> bool:
    """Two path segments match iff one is a `{placeholder}` (catalog
    template) OR they are byte-equal (literal-vs-literal). A test
    segment that is itself an f-string placeholder (`{cid}`) also
    matches anything."""
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


def _functional_test_files_for(
    spec: EndpointSpec, files: list[Path], cache: dict[Path, set[str]]
) -> list[Path]:
    """A test file `f` covers `spec` iff at least one URL literal in
    `f` matches the spec's path template segment-by-segment AND the
    spec's HTTP method appears in the file (`client.<method>(`).
    """
    method_marker = f".{spec.method.lower()}("
    out: list[Path] = []
    for f in files:
        # Quick reject: HTTP method must appear at all.
        text = f.read_text(encoding="utf-8")
        if method_marker not in text and ".request(" not in text:
            continue
        urls = cache.get(f)
        if urls is None:
            urls = _extract_path_literals(text)
            cache[f] = urls
        if any(_path_matches(spec.path, u) for u in urls):
            out.append(f.relative_to(_SUBPROJECT_ROOT))
    return out


def _classify(
    spec: EndpointSpec, files: list[Path], cache: dict[Path, set[str]]
) -> tuple[str, list[Path]]:
    matches = _functional_test_files_for(spec, files, cache)
    if matches:
        return ("functional", matches)
    return ("auth-only", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print counts only, not the per-endpoint list.",
    )
    parser.add_argument(
        "--auth-only",
        action="store_true",
        help="Print ONLY the endpoints classified as auth-only.",
    )
    args = parser.parse_args()

    files = _list_test_files()
    literal_cache: dict[Path, set[str]] = {}

    by_status: dict[str, list[tuple[EndpointSpec, list[Path]]]] = {
        "functional": [],
        "auth-only": [],
    }
    for spec in ENDPOINTS:
        status, matches = _classify(spec, files, literal_cache)
        by_status[status].append((spec, matches))

    total = len(ENDPOINTS)
    n_func = len(by_status["functional"])
    n_auth = len(by_status["auth-only"])

    if args.summary_only:
        print(f"total endpoints catalogued : {total}")
        print(f"  functional-tested         : {n_func}")
        print(f"  auth-only                 : {n_auth}")
        return 0

    if not args.auth_only:
        print(f"=== {n_func} endpoints with FUNCTIONAL tests ===\n")
        for spec, matches in sorted(
            by_status["functional"],
            key=lambda x: (x[0].component, x[0].method, x[0].path),
        ):
            print(f"  {spec.component:18s} {spec.method:6s} {spec.path}")
            for f in sorted({str(m) for m in matches}):
                print(f"      ↳ {f}")
        print()

    print(f"=== {n_auth} endpoints with AUTH-ONLY coverage (no functional test) ===\n")
    for spec, _matches in sorted(
        by_status["auth-only"],
        key=lambda x: (x[0].component, x[0].method, x[0].path),
    ):
        gate = (
            f"role={','.join(spec.accept_roles + spec.accept_global_roles)}"
            if spec.auth == Auth.ROLE_GATED
            else spec.auth.value
        )
        print(f"  {spec.component:18s} {spec.method:6s} {spec.path}  ({gate})")
    print()

    print(f"Summary: {n_func}/{total} functional-tested, {n_auth} auth-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
