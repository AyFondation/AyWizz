# src/extraction/image_analyzer.py — v1
"""Image analysis via LLM Vision for embedded document images.

Classifies image type then dispatches to appropriate analysis prompt.
See spec §7.1 for classification and prompt strategies.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from ayextractor.core.models import ImageAnalysis
from ayextractor.llm.models import ImageInput, Message

if TYPE_CHECKING:
    from ayextractor.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)

_CLASSIFICATION_PROMPT = """Analyze this image from a document. Respond with a JSON object:
{
  "type": one of "diagram", "chart", "table_image", "photo", "screenshot", "decorative",
  "description": detailed factual description of the image content,
  "entities": list of named entities visible (people, organizations, concepts, labels),
  "is_table": true if this is a table that should be reconstructed as markdown
}

If type is "table_image", also include:
  "markdown_table": the table content reconstructed as a markdown table

Process content in its original language without translation."""

_DECORATIVE_TYPES = {"decorative"}


async def analyze_image(
    image_data: bytes,
    media_type: str,
    image_id: str,
    llm: BaseLLMClient,
    source_page: int | None = None,
) -> ImageAnalysis:
    """Analyze a single embedded image via LLM Vision.

    Args:
        image_data: Raw image bytes.
        media_type: MIME type (image/png, image/jpeg, etc.).
        image_id: Unique image identifier (e.g. img_001).
        llm: Vision-capable LLM client.
        source_page: Source page number (if known).

    Returns:
        ImageAnalysis with type, description, and extracted entities.
    """
    if not llm.supports_vision:
        logger.warning("LLM client does not support vision, skipping image %s", image_id)
        return ImageAnalysis(
            id=image_id,
            type="decorative",
            description="[Image analysis skipped — no vision support]",
            source_page=source_page,
        )

    image_input = ImageInput(data=image_data, media_type=media_type, source_id=image_id)

    # A factual image caption + small JSON fits well under 512 output tokens ;
    # output is the expensive axis ($15/M on Sonnet, $4/M on Haiku), so cap it
    # tight. (Input image tokens are already capped server-side by Anthropic's
    # ≤1568px downscale, so no client resize is needed.)
    response = await llm.complete_with_vision(
        messages=[Message(role="user", content=_CLASSIFICATION_PROMPT)],
        images=[image_input],
        system="You are a document image analyzer. Return only valid JSON.",
        max_tokens=512,
    )

    try:
        result = json.loads(response.content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse image analysis JSON for %s", image_id)
        return ImageAnalysis(
            id=image_id,
            type="photo",
            description=response.content[:500],
            source_page=source_page,
        )

    return ImageAnalysis(
        id=image_id,
        type=result.get("type", "photo"),
        description=result.get("description", ""),
        entities=result.get("entities", []),
        source_page=source_page,
    )


async def analyze_images(
    images: list[tuple[str, bytes, str, int | None]],
    llm: BaseLLMClient,
) -> list[ImageAnalysis]:
    """Analyze multiple images sequentially.

    Args:
        images: List of (image_id, data, media_type, source_page) tuples.
        llm: Vision-capable LLM client.

    Returns:
        List of ImageAnalysis results.
    """
    results: list[ImageAnalysis] = []
    for image_id, data, media_type, page in images:
        analysis = await analyze_image(data, media_type, image_id, llm, page)
        results.append(analysis)
    return results
