# =============================================================================
# File: test_parsers.py
# Version: 1
# Path: ay_platform_core/tests/unit/c6_validation/test_parsers.py
# Description: Unit tests for the `@relation` marker parser + exemption
#              helpers. Exercises both valid markers and the full set of
#              syntax-error branches, plus the exemption rules used by
#              checks #2 and #5.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c6_validation.domains.code.parsers import (
    artifact_contains_sentinel,
    extract_markers,
    is_exempt_module,
    is_exempt_test_file,
)
from ay_platform_core.c6_validation.models import CodeArtifact, RelationVerb


def _artifact(content: str, *, path: str = "src/example.py", is_test: bool = False) -> CodeArtifact:
    return CodeArtifact(path=path, content=content, is_test=is_test)


@pytest.mark.unit
class TestExtractMarkers:
    def test_single_implements_marker(self) -> None:
        art = _artifact("# @relation implements:R-300-100\n")
        markers, errors = extract_markers(art)
        assert errors == []
        assert len(markers) == 1
        m = markers[0]
        assert m.verb == RelationVerb.IMPLEMENTS
        assert m.targets == ["R-300-100"]
        assert m.line == 1

    def test_multiple_targets(self) -> None:
        art = _artifact("# @relation implements:R-300-100, R-300-200\n")
        markers, errors = extract_markers(art)
        assert errors == []
        assert markers[0].targets == ["R-300-100", "R-300-200"]

    def test_version_pinned_target(self) -> None:
        art = _artifact("# @relation implements:R-300-100@v2\n")
        markers, errors = extract_markers(art)
        assert errors == []
        assert markers[0].targets == ["R-300-100@v2"]

    def test_all_known_verbs_accepted(self) -> None:
        art = _artifact(
            "# @relation implements:R-100-001\n"
            "# @relation validates:R-100-002\n"
            "# @relation uses:E-100-003\n"
            "# @relation derives-from:R-100-004\n"
        )
        markers, errors = extract_markers(art)
        assert errors == []
        assert [m.verb for m in markers] == [
            RelationVerb.IMPLEMENTS,
            RelationVerb.VALIDATES,
            RelationVerb.USES,
            RelationVerb.DERIVES_FROM,
        ]

    def test_unknown_verb_yields_syntax_error(self) -> None:
        art = _artifact("# @relation mumbles:R-300-100\n")
        markers, errors = extract_markers(art)
        assert markers == []
        assert len(errors) == 1
        assert "Unknown verb" in errors[0].reason

    def test_bad_entity_id_yields_syntax_error(self) -> None:
        art = _artifact("# @relation implements:BAD-ID\n")
        markers, errors = extract_markers(art)
        assert markers == []
        assert "Invalid entity reference" in errors[0].reason

    def test_empty_targets_yields_syntax_error(self) -> None:
        art = _artifact("# @relation implements:\n")
        markers, errors = extract_markers(art)
        assert markers == []
        assert "Empty target list" in errors[0].reason

    def test_sentinel_marker_not_reported(self) -> None:
        art = _artifact("# @relation ignore-module\n")
        markers, errors = extract_markers(art)
        assert markers == []
        assert errors == []

    def test_lines_without_marker_ignored(self) -> None:
        art = _artifact("just a regular comment\ndef foo(): pass\n")
        markers, errors = extract_markers(art)
        assert markers == []
        assert errors == []


@pytest.mark.unit
class TestExemptModule:
    def test_init_py_exempt(self) -> None:
        assert is_exempt_module(_artifact("", path="src/foo/__init__.py"))

    def test_tests_path_exempt(self) -> None:
        assert is_exempt_module(_artifact("", path="tests/unit/test_foo.py"))

    def test_is_test_flag_exempt(self) -> None:
        assert is_exempt_module(
            _artifact("", path="src/foo.py", is_test=True)
        )

    def test_ignore_module_sentinel_exempt(self) -> None:
        assert is_exempt_module(
            _artifact("# @relation ignore-module\n", path="src/foo.py")
        )

    def test_plain_module_not_exempt(self) -> None:
        assert not is_exempt_module(_artifact("print('hi')\n", path="src/foo.py"))


@pytest.mark.unit
class TestExemptTestFile:
    def test_fixtures_exempt(self) -> None:
        assert is_exempt_test_file(
            _artifact("", path="tests/fixtures/conftest.py", is_test=True)
        )

    def test_conftest_exempt(self) -> None:
        assert is_exempt_test_file(
            _artifact("", path="tests/conftest.py", is_test=True)
        )

    def test_sentinel_exempt(self) -> None:
        assert is_exempt_test_file(
            _artifact(
                "# @relation ignore-test-file\n",
                path="tests/unit/test_foo.py",
                is_test=True,
            )
        )

    def test_plain_test_not_exempt(self) -> None:
        assert not is_exempt_test_file(
            _artifact("", path="tests/unit/test_foo.py", is_test=True)
        )


@pytest.mark.unit
class TestSentinelHelper:
    def test_contains_sentinel_true(self) -> None:
        art = _artifact("# some comment\n# @relation ignore-module\n")
        assert artifact_contains_sentinel(art, "@relation ignore-module")

    def test_contains_sentinel_false(self) -> None:
        art = _artifact("# plain\n")
        assert not artifact_contains_sentinel(art, "@relation ignore-module")
