# src/chunking/chunker_factory.py — v1
"""Factory for chunker instantiation from settings.

See spec §30.8 — ChunkerFactory row.
"""

from __future__ import annotations

from ayextractor.chunking.base_chunker import BaseChunker
from ayextractor.config.settings import Settings


def create_chunker(settings: Settings | None = None) -> BaseChunker:
    """Instantiate the configured chunker.

    Args:
        settings: Application settings. Defaults to structural strategy.

    Returns:
        Configured BaseChunker implementation.
    """
    strategy = "structural" if settings is None else settings.chunking_strategy

    if strategy == "semantic":
        from ayextractor.chunking.semantic_chunker import SemanticChunker
        return SemanticChunker(settings=settings)

    # Default: structural
    from ayextractor.chunking.structural_chunker import StructuralChunker
    return StructuralChunker(settings=settings)
