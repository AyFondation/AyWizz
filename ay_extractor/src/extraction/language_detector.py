# src/extraction/language_detector.py — v2
"""Language detection at document and chunk level.

Uses lingua-py for accurate detection. Falls back to simple heuristics
if lingua is not installed.
See spec §9 for documentation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_lingua_available: bool | None = None


def _check_lingua() -> bool:
    global _lingua_available
    if _lingua_available is None:
        try:
            import lingua  # noqa: F401

            _lingua_available = True
        except ImportError:
            _lingua_available = False
            logger.info("lingua-py not installed, using fallback language detection")
    return _lingua_available


@dataclass
class LanguageResult:
    """Result of language detection."""

    primary_language: str
    secondary_languages: list[str]
    is_multilingual: bool
    confidence: float


def detect_document_language(
    text: str, metadata_language: str | None = None, override: str | None = None,
) -> str:
    """Detect the primary language of a document.

    If override or metadata.language is provided, it takes priority (spec §9.1).

    Args:
        text: Extracted document text.
        metadata_language: Language override from metadata.
        override: Explicit language override (highest priority).

    Returns:
        ISO 639-1 language code (e.g. "en", "fr", "de").
    """
    if override:
        return override
    if metadata_language:
        return metadata_language

    if _check_lingua():
        return _detect_lingua(text)
    return _detect_fallback(text)


def detect_chunk_language(text: str) -> LanguageResult:
    """Detect language(s) in a single chunk.

    Detects secondary languages when a passage of >50 tokens
    appears in a different language (spec §9.2).

    Args:
        text: Chunk text content.

    Returns:
        LanguageResult with primary/secondary languages.
    """
    if _check_lingua():
        return _detect_chunk_lingua(text)
    primary = _detect_fallback(text)
    return LanguageResult(
        primary_language=primary,
        secondary_languages=[],
        is_multilingual=False,
        confidence=0.5,
    )


def _detect_lingua(text: str) -> str:
    """Detect language using lingua-py."""
    from lingua import Language, LanguageDetectorBuilder

    detector = LanguageDetectorBuilder.from_all_languages().build()
    lang = detector.detect_language_of(text[:5000])
    if lang is None:
        return "en"
    return lang.iso_code_639_1.name.lower()


def _detect_chunk_lingua(text: str) -> LanguageResult:
    """Detect chunk-level languages using lingua-py."""
    from lingua import Language, LanguageDetectorBuilder

    detector = LanguageDetectorBuilder.from_all_languages().build()

    # Detect primary
    primary_lang = detector.detect_language_of(text)
    if primary_lang is None:
        return LanguageResult("en", [], False, 0.3)

    primary_code = primary_lang.iso_code_639_1.name.lower()

    # Detect per-sentence for secondary languages
    sentences = re.split(r"[.!?]\s+", text)
    secondary: set[str] = set()
    for sentence in sentences:
        if len(sentence.split()) < 10:
            continue
        lang = detector.detect_language_of(sentence)
        if lang is not None:
            code = lang.iso_code_639_1.name.lower()
            if code != primary_code:
                secondary.add(code)

    confidence_values = detector.compute_language_confidence_values(text[:3000])
    top_confidence = confidence_values[0].value if confidence_values else 0.5

    return LanguageResult(
        primary_language=primary_code,
        secondary_languages=sorted(secondary),
        is_multilingual=len(secondary) > 0,
        confidence=top_confidence,
    )


def _detect_fallback(text: str) -> str:
    """Simple heuristic language detection (no dependencies)."""
    sample = text[:3000].lower()

    # French markers (including word boundaries for short texts)
    fr_markers = ["les", "des", "une", "dans", "pour", "avec", "est", "sont", "cette",
                  "par", "qui", "que", "sur", "pas", "aux", "ses", "entre"]
    fr_score = sum(1 for m in fr_markers if re.search(rf"\b{m}\b", sample))

    # French accented characters are a strong signal
    accent_chars = sum(1 for c in sample if c in "àâäéèêëïîôùûüÿçœæ")
    if accent_chars >= 2:
        fr_score += accent_chars * 2

    # Apostrophe contractions typical of French: l', d', n', s', qu', j'
    fr_contractions = len(re.findall(r"\b[lLdDnNsS]'|qu'|j'", text[:3000]))
    fr_score += fr_contractions * 3

    # German markers
    de_markers = ["der", "die", "und", "ist", "ein", "den", "das", "nicht", "sich"]
    de_score = sum(1 for m in de_markers if re.search(rf"\b{m}\b", sample))

    # English markers
    en_markers = ["the", "and", "is", "for", "that", "with", "this", "are", "from"]
    en_score = sum(1 for m in en_markers if re.search(rf"\b{m}\b", sample))

    scores = {"en": en_score, "fr": fr_score, "de": de_score}
    return max(scores, key=scores.get)  # type: ignore[arg-type]
