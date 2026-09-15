# src/chunking/base_chunker.py — v1
"""Abstract chunker interface.

See spec §30.2 for full documentation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ayextractor.core.models import Chunk, DocumentStructure


class BaseChunker(ABC):
    """Unified interface for chunking strategies."""

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """Strategy identifier (e.g., 'structural', 'semantic')."""

    @abstractmethod
    async def chunk(
        self,
        text: str,
        structure: DocumentStructure | None = None,
        target_size: int = 2000,
        overlap: int = 0,
    ) -> list[Chunk]:
        """Split text into chunks respecting atomic blocks."""
