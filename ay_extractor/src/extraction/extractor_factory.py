# src/extraction/extractor_factory.py — v2
"""Factory: instantiate extractor from document format/extension.

See spec §4 pipeline step 1b.
"""

from __future__ import annotations

from ayextractor.extraction.base_extractor import BaseExtractor
from ayextractor.extraction.docx_extractor import DocxExtractor
from ayextractor.extraction.epub_extractor import EpubExtractor
from ayextractor.extraction.image_input_extractor import ImageInputExtractor
from ayextractor.extraction.md_extractor import MdExtractor
from ayextractor.extraction.pdf_extractor import PdfExtractor
from ayextractor.extraction.txt_extractor import TxtExtractor

# Registry maps extension → extractor class.
_EXTRACTOR_REGISTRY: dict[str, type[BaseExtractor]] = {}


def _register_defaults() -> None:
    """Register built-in extractors."""
    for cls in [TxtExtractor, MdExtractor, PdfExtractor, EpubExtractor,
                DocxExtractor, ImageInputExtractor]:
        instance = cls()
        for ext in instance.supported_extensions:
            _EXTRACTOR_REGISTRY[ext.lower()] = cls


_register_defaults()


class UnsupportedFormatError(ValueError):
    """Raised when no extractor is available for a format."""


def create_extractor(extension: str) -> BaseExtractor:
    """Create an extractor for the given file extension.

    Args:
        extension: File extension including dot (e.g. ".pdf", ".md").

    Returns:
        BaseExtractor instance.

    Raises:
        UnsupportedFormatError: If no extractor is registered.
    """
    ext = extension.lower()
    if not ext.startswith("."):
        ext = f".{ext}"

    cls = _EXTRACTOR_REGISTRY.get(ext)
    if cls is None:
        raise UnsupportedFormatError(
            f"No extractor for format {ext!r}. "
            f"Supported: {', '.join(sorted(_EXTRACTOR_REGISTRY))}"
        )
    return cls()


def register_extractor(extension: str, cls: type[BaseExtractor]) -> None:
    """Register a custom extractor for an extension."""
    _EXTRACTOR_REGISTRY[extension.lower()] = cls


def supported_extensions() -> list[str]:
    """Return list of supported file extensions."""
    return sorted(_EXTRACTOR_REGISTRY.keys())
