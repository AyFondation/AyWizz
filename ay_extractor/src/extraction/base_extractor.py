# src/extraction/base_extractor.py — v1
"""Abstract extractor interface for document formats.

See spec §30.1 for full documentation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ayextractor.core.models import ExtractionResult


class BaseExtractor(ABC):
    """Unified interface for document format extractors."""

    @property
    @abstractmethod
    def supported_extensions(self) -> list[str]:
        """File extensions this extractor handles (e.g., ['.pdf'])."""

    @abstractmethod
    async def extract(self, content: bytes | str | Path) -> ExtractionResult:
        """Extract text, images, and tables from document."""

    @property
    def requires_vision(self) -> bool:
        """Whether this extractor needs LLM Vision (e.g., image_input_extractor)."""
        return False
