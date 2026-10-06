# =============================================================================
# File: test_dockerignore_spares_source.py
# Version: 2
# Path: ay_platform_core/tests/coherence/test_dockerignore_spares_source.py
# Description: Refuses a `.dockerignore` pattern that would exclude SOURCE
#              from a build context.
#
#              WHY THIS EXISTS. `.dockerignore` carried `**/coverage`,
#              written to drop vitest's report directory. It also matched
#              `src/ay_platform_core/c5_requirements/coverage/` — a real
#              Python package — so the api image shipped without it and C5
#              died at import with
#              `ModuleNotFoundError: No module named
#              '...c5_requirements.coverage'`. The service was
#              un-deployable from increment 3 of 310-SPEC onward and 3953
#              tests stayed green throughout, because the suite runs
#              against the source tree and nothing exercises the IMAGE.
#
#              That is the worst shape a defect can take: invisible to
#              every gate, fatal in production, and introduced by a
#              one-line convenience in an unrelated file. A coherence test
#              is the right home for it — the failure is mechanical, the
#              check is cheap, and no amount of reviewer attention would
#              reliably catch the next over-broad glob.
#
#              WHAT THIS DOES NOT DO. It does not simulate Docker's matcher
#              in full (last-match-wins, negations). It answers one narrow
#              question — would any pattern drop a directory that contains
#              Python source we ship? — which is the question that bit us.
#
#              NO `@relation validates:` MARKER HERE, deliberately. The
#              first version of this file claimed `R-100-117`, and
#              `test_relation_markers` refused it within minutes —
#              correctly: this test imports nothing that leads to the
#              modules implementing that requirement, so the claim would
#              have made `060-IMPLEMENTATION-STATUS.md` report a
#              requirement `tested` on the strength of a comment. This file
#              guards a DEFECT CLASS, not a requirement, and a test may
#              legitimately do that.
# =============================================================================

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

pytestmark = pytest.mark.coherence

_REPO = Path(__file__).resolve().parents[3]
_DOCKERIGNORE = _REPO / ".dockerignore"
_SRC = _REPO / "ay_platform_core" / "src"


def _patterns() -> list[str]:
    """Return the exclusion patterns, ignoring comments and negations.

    A `!`-prefixed line RE-INCLUDES, so it can only ever make the context
    larger — it is not a way to lose source and is skipped.
    """
    lines = _DOCKERIGNORE.read_text().splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith(("#", "!"))
    ]


def _source_dirs() -> list[str]:
    """Return every directory under `src/` holding Python we ship.

    Posix-relative to the repo root, which is how a `.dockerignore` pattern
    is matched.
    """
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        found.add(path.parent.relative_to(_REPO).as_posix())
    return sorted(found)


def _excludes(pattern: str, candidate: str) -> bool:
    """Return True when `pattern` would drop `candidate` or an ancestor.

    Docker excludes a directory's whole subtree, so a pattern hitting any
    ancestor removes the leaf too — which is exactly how `**/coverage`
    removed a package rather than a file.
    """
    segments = candidate.split("/")
    for depth in range(1, len(segments) + 1):
        prefix = "/".join(segments[:depth])
        if fnmatch.fnmatch(prefix, pattern):
            return True
        # `**/x` must match `x` at any depth, which fnmatch does not do for
        # a single segment.
        if pattern.startswith("**/") and fnmatch.fnmatch(
            segments[depth - 1], pattern[3:]
        ):
            return True
    return False


def test_no_pattern_excludes_a_python_source_directory() -> None:
    """A build-exclusion pattern SHALL NOT be able to delete source.

    The failure mode is silent: the image builds, every test passes, and
    the container dies on import.
    """
    findings: list[str] = []
    for directory in _source_dirs():
        for pattern in _patterns():
            if _excludes(pattern, directory):
                findings.append(f"{pattern!r} excludes {directory}")

    assert not findings, (
        ".dockerignore pattern(s) would drop Python source from the build "
        "context. The image would build, the test suite would stay green, "
        "and the container would die at import — the exact shape of the "
        "`**/coverage` defect that made C5 un-deployable.\n\n"
        "Anchor the pattern to the directory it is actually for (e.g. "
        "`ay_platform_ui/coverage`, not `**/coverage`).\n\n  "
        + "\n  ".join(findings)
    )


# ---------------------------------------------------------------------------
# The matcher itself, so a false negative cannot hide behind a green test
# ---------------------------------------------------------------------------


def test_the_matcher_catches_the_original_defect() -> None:
    """`**/coverage` versus the package it actually removed."""
    assert _excludes(
        "**/coverage",
        "ay_platform_core/src/ay_platform_core/c5_requirements/coverage",
    )


def test_an_anchored_pattern_spares_the_package() -> None:
    assert not _excludes(
        "ay_platform_ui/coverage",
        "ay_platform_core/src/ay_platform_core/c5_requirements/coverage",
    )


def test_a_pattern_hitting_an_ancestor_removes_the_subtree() -> None:
    # Docker drops the whole subtree, so an ancestor match must count.
    assert _excludes("**/c5_requirements", "a/b/c5_requirements/coverage")


def test_an_unrelated_pattern_matches_nothing() -> None:
    assert not _excludes("**/node_modules", "a/b/c5_requirements/coverage")


def test_the_file_level_patterns_do_not_count_as_directory_exclusions() -> None:
    assert not _excludes("**/*.log", "ay_platform_core/src/ay_platform_core")


def test_there_is_source_to_check() -> None:
    """Guards against the test passing because it inspected nothing."""
    dirs = _source_dirs()
    assert len(dirs) > 50
    assert any(d.endswith("c5_requirements/coverage") for d in dirs)


# ---------------------------------------------------------------------------
# The licence must travel with the conveyed work (AGPL-3.0-or-later)
# ---------------------------------------------------------------------------

_DOCKERFILES = (
    _REPO / "infra" / "docker" / "Dockerfile.api",
    _REPO / "infra" / "docker" / "Dockerfile.ui",
)
_LICENSE = _REPO / "LICENSE"


def test_the_repository_carries_the_agpl_text() -> None:
    """A copyleft licence with no licence text is not a licence.

    Checks the canonical markers rather than a hash: the FSF text is fixed,
    but a well-meant reformat (line endings, a wrapper header) would break a
    hash while leaving a valid licence, and that failure would teach people
    to delete the test.
    """
    assert _LICENSE.is_file(), f"{_LICENSE} is missing"
    text = _LICENSE.read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 19 November 2007" in text
    # §13 is the whole reason AGPL was chosen over GPLv2: it is the clause
    # that reaches a NETWORK deployment, which GPLv2's distribution trigger
    # does not. Its absence would mean somebody swapped in plain GPLv3.
    assert (
        "13. Remote Network Interaction; Use with the GNU General Public"
        " License." in text
    ), (
        "the LICENSE text has no §13 — that clause is the only thing that "
        "makes a hosted deployment subject to copyleft, and it is why this "
        "project is AGPL rather than GPL"
    )
    assert "END OF TERMS AND CONDITIONS" in text, "the text is truncated"


@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.name)
def test_every_tier_image_copies_the_licence(dockerfile: Path) -> None:
    """An image is a CONVEYANCE, so it SHALL carry the licence text.

    Neither tier image shipped one before the relicensing: `.dockerignore`
    drops `*.md` and `requirements/`, and the COPY lists named only code. The
    obligation would therefore have been breached by the BUILD rather than by
    an operator — the kind of compliance gap nobody notices because nothing
    fails.
    """
    assert dockerfile.is_file(), dockerfile
    body = dockerfile.read_text(encoding="utf-8")
    copies = [
        line.strip()
        for line in body.splitlines()
        if line.strip().startswith("COPY") and "LICENSE" in line
    ]
    assert copies, (
        f"{dockerfile.name} never copies LICENSE into the image. The "
        "platform is AGPL-3.0-or-later; conveying it without the licence "
        "text is a breach of §4. Add `COPY LICENSE …` to the RUNTIME stage."
    )


def test_the_build_context_is_not_excluding_the_licence() -> None:
    """`.dockerignore` SHALL NOT drop LICENSE from the build context.

    The `COPY LICENSE` above fails the build loudly if the file is excluded,
    so this is not about catching a silent failure — it is about catching it
    HERE, in a one-second test, rather than in an image build, which is the
    same reasoning that made `**/coverage` so expensive to find.
    """
    offenders = [p for p in _patterns() if _excludes(p, "LICENSE")]
    assert not offenders, (
        f"these .dockerignore pattern(s) exclude LICENSE: {offenders}. The "
        "image would fail to build on `COPY LICENSE`, or worse, silently "
        "ship without it if that COPY is ever removed."
    )
