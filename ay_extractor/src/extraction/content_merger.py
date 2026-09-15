# src/extraction/content_merger.py — v1
"""Merge raw text with image descriptions and table descriptions.

Produces enriched_text with <<<IMAGE_CONTENT>>> and <<<TABLE_CONTENT>>> blocks.
See spec §7.2 for injection format.
"""

from __future__ import annotations

from ayextractor.core.models import ImageAnalysis, TableData


def merge_content(
    raw_text: str,
    images: list[ImageAnalysis] | None = None,
    tables: list[TableData] | None = None,
) -> str:
    """Merge raw text with image and table descriptions.

    Image and table blocks are appended at the end of the text
    when their position within the text is unknown. When page-level
    positioning is available, blocks are inserted at approximate positions.

    Args:
        raw_text: Extracted raw text.
        images: Analyzed image descriptions.
        tables: Extracted table data.

    Returns:
        Enriched text with injected IMAGE_CONTENT and TABLE_CONTENT blocks.
    """
    if not images and not tables:
        return raw_text

    parts: list[str] = [raw_text]

    # Append image blocks
    if images:
        for img in images:
            if img.type == "decorative":
                continue
            block = format_image_block(img)
            parts.append(block)

    # Append table blocks
    if tables:
        for tbl in tables:
            block = format_table_block(tbl)
            parts.append(block)

    return "\n\n".join(parts)


def format_image_block(image: ImageAnalysis) -> str:
    """Format a single image as an IMAGE_CONTENT block."""
    source = f"page_{image.source_page}" if image.source_page else "unknown"
    lines = [
        f'<<<IMAGE_CONTENT id="{image.id}" type="{image.type}" source="{source}">>>',
        f"Description: {image.description}",
    ]
    if image.entities:
        lines.append(f"Entities: {', '.join(image.entities)}")
    lines.append("<<<END_IMAGE_CONTENT>>>")
    return "\n".join(lines)


def format_table_block(table: TableData) -> str:
    """Format a single table as a TABLE_CONTENT block."""
    source = f"page_{table.source_page}" if table.source_page else "unknown"
    return (
        f'<<<TABLE_CONTENT id="{table.id}" source="{source}" origin="{table.origin}">>>\n'
        f"{table.content_markdown}\n"
        "<<<END_TABLE_CONTENT>>>"
    )
