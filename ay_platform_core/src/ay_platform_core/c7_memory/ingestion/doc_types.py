# =============================================================================
# File: doc_types.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/ingestion/doc_types.py
# Description: Document-type -> ingestion-strategy classifier (R-400-231, D-021).
#              The single seam that both ingestion paths (heavy upload / light
#              live-docs) dispatch through so a `.py`, a cascading-requirements
#              spec, an Excel sheet and a prose note each get the right
#              treatment. v1 strategies: PROSE (default), REQUIREMENTS
#              (structural traceability KG), CODE (AST KG), TABULAR (deferred
#              to v2 — falls back to PROSE). An unknown type NEVER drops the
#              document; it degrades to PROSE.
#
# @relation implements:R-400-231
# =============================================================================

from __future__ import annotations

import re
from enum import StrEnum

# Code file extensions → the AST / structural-code strategy. Python is the only
# fully-wired language in v1 (`extract_structural_kg(kind="code")`); the others
# are classified as CODE so the registry has the seam, and fall back to a
# code-aware chunk when no language extractor exists yet.
_CODE_EXTS = frozenset({
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".rb", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".kt", ".scala",
    ".sh", ".sql", ".php", ".swift", ".m", ".mm",
})

# Tabular / structured — the DEFERRED (v2) strategy (R-400-233).
_TABULAR_EXTS = frozenset({
    ".xlsx", ".xls", ".csv", ".tsv", ".parquet", ".pptx", ".ods",
})

# A markdown/text doc is REQUIREMENTS when it carries spec entity blocks:
# an `id: <PREFIX>-NNN-XXX` line together with a traceability relation.
_PROSE_EXTS = frozenset({".md", ".markdown", ".txt", ".rst", ""})
_REQ_ID_RE = re.compile(r"(?m)^\s*id:\s*[A-Z]-\d", re.MULTILINE)
_REQ_REL_RE = re.compile(r"(?m)^\s*(derives-from|impacts):", re.MULTILINE)


class DocType(StrEnum):
    """Ingestion strategy a document maps to (R-400-231)."""

    PROSE = "prose"
    REQUIREMENTS = "requirements"
    CODE = "code"
    TABULAR = "tabular"


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def classify(path: str, content: str | None = None) -> DocType:
    """Map a document (by path, optionally content) to its ingestion strategy.

    Extension decides CODE / TABULAR. For prose-ish extensions, the CONTENT
    (when supplied) decides REQUIREMENTS (spec entity blocks) vs PROSE. Unknown
    extensions default to PROSE — never dropped.
    """
    ext = _ext(path)
    if ext in _CODE_EXTS:
        return DocType.CODE
    if ext in _TABULAR_EXTS:
        return DocType.TABULAR
    # Requirements corpus: a prose-ish file whose content carries at least one
    # entity `id:` AND a traceability relation (derives-from / impacts) — the
    # cascading-requirements shape.
    if (
        ext in _PROSE_EXTS
        and content is not None
        and _REQ_ID_RE.search(content)
        and _REQ_REL_RE.search(content)
    ):
        return DocType.REQUIREMENTS
    return DocType.PROSE
