# =============================================================================
# File: test_doc_types.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_doc_types.py
# Description: Unit tests for the document-type -> ingestion-strategy classifier
#              (R-400-231, D-021): code by extension, tabular by extension,
#              requirements by content (spec entity blocks), prose as the safe
#              default, and unknown extensions never dropped.
#
# @relation validates:R-400-231
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c7_memory.ingestion.doc_types import DocType, classify

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "path",
    ["a.py", "src/mod.py", "app/Main.ts", "x.go", "q.sql", "s.rs", "H.java"],
)
def test_code_by_extension(path: str) -> None:
    assert classify(path) is DocType.CODE


@pytest.mark.parametrize("path", ["data.xlsx", "sheet.csv", "deck.pptx", "t.tsv"])
def test_tabular_by_extension(path: str) -> None:
    assert classify(path) is DocType.TABULAR


def test_requirements_by_content() -> None:
    content = (
        "# Spec\n\n#### R-400-100\n\n```yaml\nid: R-400-100\nversion: 1\n"
        "derives-from: [D-021]\n```\n\nThe system SHALL do X.\n"
    )
    assert classify("docs/spec.md", content) is DocType.REQUIREMENTS


def test_markdown_without_entity_blocks_is_prose() -> None:
    assert classify("notes.md", "# Meeting notes\n\nJust prose, no ids.") is DocType.PROSE


def test_requirements_needs_both_id_and_relation() -> None:
    # An `id:` alone (no derives-from/impacts) is NOT the requirements shape.
    assert classify("x.md", "id: R-400-1\nsome text") is DocType.PROSE


def test_prose_is_default_for_unknown_extension() -> None:
    assert classify("weird.xyz") is DocType.PROSE
    assert classify("README") is DocType.PROSE


def test_content_none_never_misclassifies_prose_ext_as_requirements() -> None:
    # Without content, a .md is prose (content-based requirements detection off).
    assert classify("spec.md") is DocType.PROSE
