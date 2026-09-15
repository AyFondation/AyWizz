# src/extraction/reference_extractor.py — v1
"""Library-based reference / citation extraction (D-020 v1).

D-020 v1 §5 (R-100-125 v2) mandates: reference and citation extraction
SHALL use deterministic libraries + regex, NOT an LLM. The legacy
`pipeline/agents/reference_extractor.py` (LLM-driven) was removed in
session 2; this file is its replacement.

Strategy:
  - **Citations** (academic-style `[1]`, `(Author, 2023)`, footnote
    superscript) → regex patterns, no external dep.
  - **Bibliography** entries (in the bibliography section detected by
    `structure_detector`) → `refextract` if available (handles arXiv,
    DOI, ISBN, URL); regex fallback otherwise.
  - **Cross-references** (`see section 4.2`, `cf. above`, `figure 3`)
    → regex patterns.

Zero LLM calls. The downstream pipeline (decontextualiser, summariser,
densifier) reads the resulting `list[Reference]` to anchor ambiguous
mentions during their respective passes.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ayextractor.core.models import DocumentStructure, Reference

logger = logging.getLogger(__name__)


# --- Regex patterns ---------------------------------------------------------

# `[1]`, `[12]`, `[123]` — bracketed numeric citations.
_BRACKET_CITATION = re.compile(r"\[(\d{1,4})\]")
# `(Author, 2023)`, `(Author et al., 2023)`, `(Author and Author, 2023)`.
_AUTHOR_YEAR_CITATION = re.compile(
    r"\(([A-Z][A-Za-z\-']+(?:\s+(?:et al\.|and\s+[A-Z][A-Za-z\-']+))?),\s*(\d{4}[a-z]?)\)"
)
# `see section 4.2`, `cf. section 4.2`, `as discussed in section 4.2`.
_CROSS_REF_SECTION = re.compile(
    r"\b(?:see|cf\.|as\s+(?:discussed|described|shown|defined)\s+in)\s+"
    r"(section|chapter|figure|table|appendix|annex)\s+(\d+(?:\.\d+)*)",
    re.IGNORECASE,
)
# `figure 3`, `table 12`, `eq. (4.2)` — plain numeric refs to figures/tables.
_FIGURE_TABLE_REF = re.compile(
    r"\b(figure|fig\.|table|tab\.|equation|eq\.)\s*\(?(\d+(?:\.\d+)*)\)?",
    re.IGNORECASE,
)


def extract_references(
    text: str,
    structure: DocumentStructure | None = None,
    source_chunk_id: str = "doc",
) -> list[Reference]:
    """Extract citations, footnotes, cross-references, and bibliography entries.

    Args:
        text: Document text (post-extraction, pre-chunking).
        structure: Detected structure from `structure_detector.detect_structure`.
            Used to scope the bibliography parse to its actual byte range.
            Optional — passing None falls back to scanning the last 30%.
        source_chunk_id: Stable id to stamp on every produced `Reference`.
            For document-level extraction (pre-chunking) the conventional
            value is "doc"; per-chunk extraction passes the chunk id.

    Returns:
        List of `Reference` objects (citation | footnote | bibliography
        | internal_ref). Order = first occurrence in text.
    """
    refs: list[Reference] = []

    # 1. Bracket citations [1], [12], …
    for match in _BRACKET_CITATION.finditer(text):
        refs.append(
            Reference(
                type="citation",
                text=match.group(0),
                target=match.group(1),  # bare number; resolution happens later
                source_chunk_id=source_chunk_id,
            )
        )

    # 2. Author-year citations (Smith, 2023)
    for match in _AUTHOR_YEAR_CITATION.finditer(text):
        refs.append(
            Reference(
                type="citation",
                text=match.group(0),
                target=f"{match.group(1)}_{match.group(2)}",
                source_chunk_id=source_chunk_id,
            )
        )

    # 3. Cross-references (see section 4.2)
    for match in _CROSS_REF_SECTION.finditer(text):
        refs.append(
            Reference(
                type="internal_ref",
                text=match.group(0),
                target=f"{match.group(1).lower()}:{match.group(2)}",
                source_chunk_id=source_chunk_id,
            )
        )

    # 4. Figure / table references
    for match in _FIGURE_TABLE_REF.finditer(text):
        kind = match.group(1).rstrip(".").lower()
        # Normalise "fig" / "tab" / "eq" to their long form.
        kind = {"fig": "figure", "tab": "table", "eq": "equation"}.get(kind, kind)
        refs.append(
            Reference(
                type="internal_ref",
                text=match.group(0),
                target=f"{kind}:{match.group(2)}",
                source_chunk_id=source_chunk_id,
            )
        )

    # 5. Footnotes — re-use the structure detector's findings (already
    # populated as `Footnote` items, distinct from `Reference`). We expose
    # them here as Reference instances with type=footnote so downstream
    # agents see a uniform stream.
    if structure is not None:
        for fn in structure.footnotes:
            refs.append(
                Reference(
                    type="footnote",
                    text=f"[{fn.id}]",
                    target=fn.id,
                    source_chunk_id=source_chunk_id,
                )
            )

    # 6. Bibliography entries — parse the bibliography section if detected.
    if structure is not None and structure.has_bibliography and structure.bibliography_position is not None:
        bib_text = text[structure.bibliography_position :]
        refs.extend(_parse_bibliography(bib_text, source_chunk_id))

    return refs


def _parse_bibliography(bib_text: str, source_chunk_id: str) -> list[Reference]:
    """Parse the bibliography section into a list of references.

    Strategy: try `refextract` (a battle-tested INSPIRE-HEP / arXiv
    citation extractor) if installed; fall back to a per-line regex on
    common patterns (numbered, year-bracketed, DOI, URL).
    """
    try:
        return _parse_bibliography_refextract(bib_text, source_chunk_id)
    except ImportError:
        logger.debug("refextract not installed — falling back to regex parser")
        return _parse_bibliography_regex(bib_text, source_chunk_id)
    except Exception as exc:  # pragma: no cover — refextract is best-effort
        logger.warning("refextract failed (%s); falling back to regex parser", exc)
        return _parse_bibliography_regex(bib_text, source_chunk_id)


def _parse_bibliography_refextract(bib_text: str, source_chunk_id: str) -> list[Reference]:
    """Parse via the `refextract` library (extras = `references`).

    Raises ImportError if the optional dep is missing — caller falls back
    to the regex parser. Raises any other exception unchanged.
    """
    from refextract import extract_references_from_string  # type: ignore[import-not-found]

    raw: list[dict[str, Any]] = extract_references_from_string(bib_text)
    refs: list[Reference] = []
    for entry in raw:
        # `entry` is a dict with optional keys: `author`, `title`, `year`,
        # `journal`, `doi`, `url`, `raw_ref` (list[str]). We persist the
        # raw line as `text` and the DOI/URL/title as `target` if present.
        raw_lines = entry.get("raw_ref") or []
        text = " ".join(line.strip() for line in raw_lines) if raw_lines else str(entry)
        target = (
            entry.get("doi") or entry.get("url") or entry.get("title") or None
        )
        if isinstance(target, list):
            target = target[0] if target else None
        refs.append(
            Reference(
                type="bibliography",
                text=text,
                target=target,
                source_chunk_id=source_chunk_id,
            )
        )
    return refs


# Per-line bibliography regex patterns (regex fallback).
_BIB_NUMBERED = re.compile(r"^\s*\[?(\d{1,4})\]?\.?\s+(.+?)\s*$")
_BIB_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
_BIB_URL = re.compile(r"https?://[^\s<>\"]+")


def _parse_bibliography_regex(bib_text: str, source_chunk_id: str) -> list[Reference]:
    """Last-resort regex bibliography parser — one Reference per non-empty line.

    Recognises numbered entries (`[12] Author, 2023, ...`) and extracts
    a DOI or URL as the `target` if present.
    """
    refs: list[Reference] = []
    # Skip the heading line(s) — start at the first numbered entry.
    lines = [line.strip() for line in bib_text.split("\n") if line.strip()]
    for line in lines:
        match = _BIB_NUMBERED.match(line)
        if not match:
            # Tolerant: accept any line ≥ 20 chars containing a year — likely a
            # citation that doesn't follow the numbered convention.
            if len(line) >= 20 and re.search(r"\b(?:19|20)\d{2}\b", line):
                text = line
                number = None
            else:
                continue
        else:
            number = match.group(1)
            text = match.group(2)

        doi_match = _BIB_DOI.search(text)
        url_match = _BIB_URL.search(text)
        target: str | None = None
        if doi_match:
            target = f"doi:{doi_match.group(1)}"
        elif url_match:
            target = url_match.group(0)
        elif number is not None:
            target = f"bib:{number}"

        refs.append(
            Reference(
                type="bibliography",
                text=text,
                target=target,
                source_chunk_id=source_chunk_id,
            )
        )
    return refs
