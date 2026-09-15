# tests/e2e/conftest_functional.py — v1
"""Fixtures for functional E2E tests — golden files + expected outputs.

Loads test documents (PDF, DOCX, PNG) and expected output schemas
from tests/e2e/fixtures/ and tests/e2e/expected/ directories.

These fixtures are deterministic and do NOT require any LLM or Docker
for the extraction/chunking tests. LLM-dependent tests (triplets)
use the provider-agnostic e2e_llm fixture from conftest.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Resolve fixture directories relative to this file
_E2E_DIR = Path(__file__).parent
_FIXTURES_DIR = _E2E_DIR / "fixtures"
_EXPECTED_DIR = _E2E_DIR / "expected"


# =====================================================================
#  FIXTURE FILES — golden input documents
# =====================================================================

@pytest.fixture(scope="session")
def nis2_pdf_path() -> Path:
    """Path to the NIS2 test PDF (3 pages: text + table + diagram)."""
    p = _FIXTURES_DIR / "nis2_test_document.pdf"
    assert p.exists(), f"Missing fixture: {p}"
    return p


@pytest.fixture(scope="session")
def nis2_pdf_bytes(nis2_pdf_path: Path) -> bytes:
    """Raw bytes of the NIS2 test PDF."""
    return nis2_pdf_path.read_bytes()


@pytest.fixture(scope="session")
def nis2_docx_path() -> Path:
    """Path to the NIS2 test DOCX (same content as PDF)."""
    p = _FIXTURES_DIR / "nis2_test_document.docx"
    assert p.exists(), f"Missing fixture: {p}"
    return p


@pytest.fixture(scope="session")
def nis2_docx_bytes(nis2_docx_path: Path) -> bytes:
    """Raw bytes of the NIS2 test DOCX."""
    return nis2_docx_path.read_bytes()


@pytest.fixture(scope="session")
def nis2_table_png_path() -> Path:
    """Path to PNG screenshot of page 2 (table)."""
    p = _FIXTURES_DIR / "nis2_test_page2_table.png"
    assert p.exists(), f"Missing fixture: {p}"
    return p


@pytest.fixture(scope="session")
def nis2_diagram_png_path() -> Path:
    """Path to PNG screenshot of page 3 (diagram)."""
    p = _FIXTURES_DIR / "nis2_test_page3_diagram.png"
    assert p.exists(), f"Missing fixture: {p}"
    return p


# =====================================================================
#  EXPECTED OUTPUTS — golden assertions
# =====================================================================

@pytest.fixture(scope="session")
def expected_extraction() -> dict:
    """Expected extraction assertions (text fragments, sections, tables)."""
    p = _EXPECTED_DIR / "nis2_extraction.json"
    assert p.exists(), f"Missing expected: {p}"
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def expected_chunks() -> dict:
    """Expected chunk structural assertions."""
    p = _EXPECTED_DIR / "nis2_chunks.json"
    assert p.exists(), f"Missing expected: {p}"
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def expected_triplets() -> dict:
    """Expected triplet semantic assertions."""
    p = _EXPECTED_DIR / "nis2_triplets_schema.json"
    assert p.exists(), f"Missing expected: {p}"
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def expected_image_input() -> dict:
    """Expected assertions for image-as-document input."""
    p = _EXPECTED_DIR / "nis2_image_input.json"
    assert p.exists(), f"Missing expected: {p}"
    return json.loads(p.read_text())