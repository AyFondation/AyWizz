# src/extraction/table_extractor.py — v2
"""Structured table extraction from all document formats.

Tables are normalized to Markdown and injected via <<<TABLE_CONTENT>>> blocks.
See spec §11 for sources and output format.
"""

from __future__ import annotations

import re

from ayextractor.core.models import TableData


def extract_tables_from_text(text: str) -> list[TableData]:
    """Extract Markdown tables already present in text.

    Args:
        text: Document text that may contain markdown tables.

    Returns:
        List of detected TableData entries.
    """
    tables: list[TableData] = []
    # Split into lines and find contiguous blocks of table rows (lines starting with |)
    lines = text.split("\n")
    i = 0
    table_idx = 0
    while i < len(lines):
        # Look for start of a table block
        if lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
            block_lines: list[str] = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("|") and lines[j].strip().endswith("|"):
                block_lines.append(lines[j].strip())
                j += 1
            # Must have at least 2 rows and contain a separator row (|---|)
            if len(block_lines) >= 2:
                content = "\n".join(block_lines)
                if re.search(r"^\|[\s\-:|]+\|$", content, re.MULTILINE):
                    table_idx += 1
                    tables.append(
                        TableData(
                            id=f"tbl_{table_idx:03d}",
                            content_markdown=content,
                            source_page=None,
                            origin="structured",
                        )
                    )
            i = j
        else:
            i += 1

    return tables


def extract_tables_from_pdf_page(
    page_tables: list[list[list[str]]], page_number: int
) -> list[TableData]:
    """Convert raw table data (from PDF parser) to TableData.

    Args:
        page_tables: List of tables, each a list of rows (list of cell strings).
        page_number: Source page number.

    Returns:
        List of TableData with Markdown content.
    """
    tables: list[TableData] = []
    for i, raw_table in enumerate(page_tables):
        if not raw_table or len(raw_table) < 2:
            continue
        md = _rows_to_markdown(raw_table)
        tables.append(
            TableData(
                id=f"tbl_p{page_number}_{i + 1:02d}",
                content_markdown=md,
                source_page=page_number,
                origin="structured",
            )
        )
    return tables


def _rows_to_markdown(rows: list[list[str]]) -> str:
    """Convert a list of rows to a Markdown table string."""
    if not rows:
        return ""

    # Normalize column count
    max_cols = max(len(r) for r in rows)
    normalized = [r + [""] * (max_cols - len(r)) for r in rows]

    lines: list[str] = []
    # Header row
    header = normalized[0]
    lines.append("| " + " | ".join(cell.strip() for cell in header) + " |")
    # Separator
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    # Data rows
    for row in normalized[1:]:
        lines.append("| " + " | ".join(cell.strip() for cell in row) + " |")

    return "\n".join(lines)
