# =============================================================================
# File: extraction.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/intake/extraction.py
# Description: Format adapters for supplied requirement drops — R-300-080 v2,
#              310-SPEC §4.4 (R-310-060 / R-310-061 / R-310-062).
#
#              THE STRUCTURAL GUARANTEE. Every adapter returns the document
#              text it extracted PLUS spans into it, and `ExtractedRecord`
#              refuses to exist unless its text IS the slice its interval
#              names. An adapter therefore cannot report an anchor that does
#              not resolve — which is what makes R-310-062 a fact about the
#              data rather than a promise about the code.
#
#              NO NEW DEPENDENCY. ReqIF is XML (stdlib), XLSX is a zip of XML
#              (stdlib `zipfile` + `xml.etree`), and `python-docx` / `pypdf`
#              are already main dependencies, declared for the C7 source
#              parsers. Checked against pyproject.toml before planning.
#
#              WHAT COUNTS AS A REQUIREMENT in prose formats is an
#              implementation ambiguity (§8.1), resolved here as: a paragraph
#              is a candidate when it contains an RFC-2119 modal. It is a
#              DEFAULT, not a truth — the intake quality review (R-310-063)
#              is what catches both the noise it admits and the requirements
#              it misses, and the modal set is a parameter.
#
# @relation implements:R-310-060
# @relation implements:R-310-061
# @relation implements:R-310-062
# =============================================================================

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

from .intervals import TextInterval
from .models import SourceClass, SourceFormat, default_source_class

#: RFC-2119 modals that mark a paragraph as a requirement candidate.
DEFAULT_MODALS: frozenset[str] = frozenset(
    {"shall", "must", "should", "shall not", "must not", "should not"}
)

_MODAL_RE = re.compile(
    r"\b(?:shall|must|should)\b(?:\s+not\b)?", re.IGNORECASE
)

_REQIF_NS = {"r": "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd"}
_XLSX_NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}


class ExtractionError(RuntimeError):
    """Raised when a supplied drop cannot be read as its declared format."""


@dataclass(frozen=True, slots=True)
class ExtractedRecord:
    """One requirement candidate and where it came from.

    Refuses construction unless `text` is exactly the slice `interval`
    names in the document text it was extracted from. That check is the
    whole point: an anchor that does not resolve is not an anchor
    (R-310-062), and an adapter that computes offsets wrongly would
    otherwise ship silently broken provenance.
    """

    location: str
    interval: TextInterval
    text: str
    native_id: str | None = None

    def verify_against(self, document_text: str) -> None:
        """Raise when this record's anchor does not resolve.

        Args:
            document_text: The text the interval indexes into.

        Raises:
            ExtractionError: When the slice and the text disagree.
        """
        actual = self.interval.slice_of(document_text)
        if actual != self.text:
            raise ExtractionError(
                f"anchor at {self.location!r} does not resolve: interval "
                f"[{self.interval.start}, {self.interval.end}) holds "
                f"{actual[:40]!r} but the record says {self.text[:40]!r}"
            )


@dataclass(frozen=True, slots=True)
class Extraction:
    """Everything one adapter read out of one supplied file."""

    source_format: SourceFormat
    source_class: SourceClass
    document_text: str
    records: tuple[ExtractedRecord, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for record in self.records:
            record.verify_against(self.document_text)

    @property
    def candidate_count(self) -> int:
        """How many requirement candidates this extraction found."""
        return len(self.records)


def has_modal(text: str, modals: frozenset[str] = DEFAULT_MODALS) -> bool:
    """Return True when the text carries an RFC-2119 modal.

    Args:
        text: Candidate paragraph.
        modals: Accepted modal phrases, lowercased.

    Returns:
        True when the paragraph reads as an obligation.
    """
    match = _MODAL_RE.search(text)
    return match is not None and " ".join(match.group(0).lower().split()) in modals


# ---------------------------------------------------------------------------
# Prose formats — paragraphs joined, offsets tracked
# ---------------------------------------------------------------------------


def _from_blocks(
    blocks: list[tuple[str, str]],
    source_format: SourceFormat,
    source_class: SourceClass,
    modals: frozenset[str],
) -> Extraction:
    """Assemble an extraction from `(location, text)` blocks.

    The document text is the blocks joined by newlines, so every recorded
    interval indexes into something the platform actually stored — which is
    what keeps the anchor resolvable after the original file is gone.
    """
    document_parts: list[str] = []
    records: list[ExtractedRecord] = []
    cursor = 0
    for location, raw in blocks:
        text = raw.strip()
        if not text:
            continue
        document_parts.append(text)
        if has_modal(text, modals):
            records.append(
                ExtractedRecord(
                    location=location,
                    interval=TextInterval(cursor, cursor + len(text)),
                    text=text,
                )
            )
        cursor += len(text) + 1  # the joining newline
    return Extraction(
        source_format=source_format,
        source_class=source_class,
        document_text="\n".join(document_parts),
        records=tuple(records),
    )


def extract_markdown(
    text: str, *, needed_ocr: bool = False,
    modals: frozenset[str] = DEFAULT_MODALS,
) -> Extraction:
    """Extract requirement candidates from a Markdown or plain-text drop."""
    blocks = [
        (f"line {index}", line)
        for index, line in enumerate(text.splitlines(), start=1)
    ]
    return _from_blocks(
        blocks,
        SourceFormat.MD,
        default_source_class(SourceFormat.MD, needed_ocr=needed_ocr),
        modals,
    )


def extract_docx(
    payload: bytes, *, needed_ocr: bool = False,
    modals: frozenset[str] = DEFAULT_MODALS,
) -> Extraction:
    """Extract requirement candidates from a DOCX drop.

    Raises:
        ExtractionError: When the payload is not a readable DOCX.
    """
    try:
        import docx  # noqa: PLC0415 — optional at import time, present at runtime
    except ImportError as exc:  # pragma: no cover - declared main dependency
        raise ExtractionError("python-docx is required to read DOCX drops") from exc
    try:
        document = docx.Document(io.BytesIO(payload))
    except Exception as exc:
        raise ExtractionError(f"not a readable DOCX: {exc}") from exc
    blocks = [
        (f"¶{index}", paragraph.text)
        for index, paragraph in enumerate(document.paragraphs, start=1)
    ]
    return _from_blocks(
        blocks,
        SourceFormat.DOCX,
        default_source_class(SourceFormat.DOCX, needed_ocr=needed_ocr),
        modals,
    )


def extract_pdf_pages(
    pages: list[str], *, needed_ocr: bool = False,
    modals: frozenset[str] = DEFAULT_MODALS,
) -> Extraction:
    """Extract requirement candidates from already-read PDF page texts.

    The page texts are passed in rather than read here so the paragraph and
    offset logic is testable without authoring a PDF — `read_pdf` is the
    thin boundary that calls the library.

    Args:
        pages: One string per page, in order.
        needed_ocr: True when the text came from OCR, which makes the source
            DEGRADED and gates everything downstream (R-310-061).
        modals: Accepted modal phrases.
    """
    blocks: list[tuple[str, str]] = []
    for number, page in enumerate(pages, start=1):
        for index, paragraph in enumerate(page.split("\n\n"), start=1):
            blocks.append((f"p.{number} ¶{index}", paragraph))
    return _from_blocks(
        blocks,
        SourceFormat.PDF,
        default_source_class(SourceFormat.PDF, needed_ocr=needed_ocr),
        modals,
    )


def read_pdf(payload: bytes) -> list[str]:
    """Return one text string per PDF page.

    Raises:
        ExtractionError: When the payload is not a readable PDF.
    """
    try:
        from pypdf import PdfReader  # noqa: PLC0415 — declared main dependency
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("pypdf is required to read PDF drops") from exc
    try:
        reader = PdfReader(io.BytesIO(payload))
        return [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise ExtractionError(f"not a readable PDF: {exc}") from exc


# ---------------------------------------------------------------------------
# Structured formats — identifiers are native, so anchors are exact
# ---------------------------------------------------------------------------


def extract_reqif(payload: bytes, *, needed_ocr: bool = False) -> Extraction:
    """Extract requirements from a ReqIF 1.2 drop.

    ReqIF carries its own identifiers and one object per requirement, so
    every candidate is taken: there is nothing to guess, which is why the
    class is `structured`.

    Raises:
        ExtractionError: When the payload is not readable ReqIF.
    """
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ExtractionError(f"not readable ReqIF XML: {exc}") from exc

    objects = root.findall(".//r:SPEC-OBJECT", _REQIF_NS)
    if not objects:
        objects = [e for e in root.iter() if _localname(e.tag) == "SPEC-OBJECT"]

    document_parts: list[str] = []
    records: list[ExtractedRecord] = []
    cursor = 0
    for index, obj in enumerate(objects, start=1):
        native_id = obj.get("IDENTIFIER")
        text = _reqif_text(obj)
        if not text:
            continue
        document_parts.append(text)
        records.append(
            ExtractedRecord(
                location=f"SPEC-OBJECT {native_id or index}",
                interval=TextInterval(cursor, cursor + len(text)),
                text=text,
                native_id=native_id,
            )
        )
        cursor += len(text) + 1
    return Extraction(
        source_format=SourceFormat.REQIF,
        source_class=default_source_class(
            SourceFormat.REQIF, needed_ocr=needed_ocr
        ),
        document_text="\n".join(document_parts),
        records=tuple(records),
    )


def _reqif_text(obj: ElementTree.Element) -> str:
    """Return the concatenated attribute text of one SPEC-OBJECT."""
    parts: list[str] = []
    for element in obj.iter():
        name = _localname(element.tag)
        if name in {"THE-VALUE", "ATTRIBUTE-VALUE-STRING"}:
            value = element.get("THE-VALUE") or "".join(element.itertext())
            if value and value.strip():
                parts.append(value.strip())
        elif name == "ATTRIBUTE-VALUE-XHTML":
            value = " ".join("".join(element.itertext()).split())
            if value:
                parts.append(value)
    return " ".join(parts).strip()


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_xlsx(
    payload: bytes, *, text_column: str = "B", id_column: str | None = "A",
    needed_ocr: bool = False,
) -> Extraction:
    """Extract requirements from an XLSX drop, one per row.

    Read with stdlib `zipfile` + `xml.etree`: an xlsx is a zip of XML, so
    this needs no spreadsheet library and therefore no new dependency.

    Args:
        payload: The .xlsx bytes.
        text_column: Column letter holding the requirement text.
        id_column: Column letter holding the issuer's own identifier, if any.

    Raises:
        ExtractionError: When the payload is not a readable XLSX.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ExtractionError(f"not a readable XLSX: {exc}") from exc

    shared = _xlsx_shared_strings(archive)
    sheet_name = next(
        (n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")), None
    )
    if sheet_name is None:
        raise ExtractionError("XLSX contains no worksheet")

    try:
        sheet = ElementTree.fromstring(archive.read(sheet_name))
    except ElementTree.ParseError as exc:
        raise ExtractionError(f"unreadable XLSX worksheet: {exc}") from exc

    document_parts: list[str] = []
    records: list[ExtractedRecord] = []
    cursor = 0
    for row in sheet.iter():
        if _localname(row.tag) != "row":
            continue
        cells = {
            _column_of(c.get("r") or ""): _xlsx_cell_text(c, shared)
            for c in row
            if _localname(c.tag) == "c"
        }
        text = (cells.get(text_column) or "").strip()
        if not text:
            continue
        native_id = (cells.get(id_column) or "").strip() if id_column else None
        document_parts.append(text)
        records.append(
            ExtractedRecord(
                location=f"row {row.get('r')} col {text_column}",
                interval=TextInterval(cursor, cursor + len(text)),
                text=text,
                native_id=native_id or None,
            )
        )
        cursor += len(text) + 1
    return Extraction(
        source_format=SourceFormat.XLSX,
        source_class=default_source_class(
            SourceFormat.XLSX, needed_ocr=needed_ocr
        ),
        document_text="\n".join(document_parts),
        records=tuple(records),
    )


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except ElementTree.ParseError:
        return []
    return [
        "".join(
            t.text or ""
            for t in item.iter()
            if _localname(t.tag) == "t"
        )
        for item in root
        if _localname(item.tag) == "si"
    ]


def _xlsx_cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    raw = ""
    for child in cell:
        name = _localname(child.tag)
        if name == "v":
            raw = child.text or ""
        elif name == "is":
            raw = "".join(
                t.text or "" for t in child.iter() if _localname(t.tag) == "t"
            )
    if cell.get("t") == "s" and raw.isdigit():
        index = int(raw)
        return shared[index] if 0 <= index < len(shared) else ""
    return raw


def _column_of(reference: str) -> str:
    return "".join(ch for ch in reference if ch.isalpha())


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def extract(
    payload: bytes,
    source_format: SourceFormat,
    *,
    needed_ocr: bool = False,
    modals: frozenset[str] = DEFAULT_MODALS,
) -> Extraction:
    """Extract requirement candidates from one supplied drop.

    Args:
        payload: The file bytes.
        source_format: Declared format of the drop.
        needed_ocr: Whether the text came from OCR. Honoured for EVERY
            format, not only PDF: the reason the class matters is that
            character offsets become unreliable, and that holds whatever
            container the OCR output arrived in. Silently ignoring a
            caller's integrity flag would be the worse failure, and
            `degraded` is the conservative class.
        modals: Accepted modal phrases for prose formats.

    Returns:
        The extraction, with every anchor verified to resolve.

    Raises:
        ExtractionError: When the payload cannot be read as that format.
    """
    if source_format is SourceFormat.MD:
        return extract_markdown(
            payload.decode("utf-8", errors="replace"),
            needed_ocr=needed_ocr,
            modals=modals,
        )
    if source_format is SourceFormat.REQIF:
        return extract_reqif(payload, needed_ocr=needed_ocr)
    if source_format is SourceFormat.XLSX:
        return extract_xlsx(payload, needed_ocr=needed_ocr)
    if source_format is SourceFormat.DOCX:
        return extract_docx(payload, needed_ocr=needed_ocr, modals=modals)
    return extract_pdf_pages(read_pdf(payload), needed_ocr=needed_ocr, modals=modals)
