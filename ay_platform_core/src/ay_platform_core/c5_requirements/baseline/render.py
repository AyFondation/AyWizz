# =============================================================================
# File: render.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/baseline/render.py
# Description: DOCX and PDF rendering from a baseline manifest —
#              310-SPEC §4.11 (R-310-207).
#
#              RENDERED FROM THE MANIFEST, NEVER FROM THE LIVE CORPUS.
#              R-310-207 says "from a baseline manifest", and the reason is
#              the whole point of baselining: a document generated from
#              today's objects under yesterday's tag is a lie with a version
#              number on it. This module takes resolved entries — manifest
#              entry plus the content it NAMES — and has no access to the
#              store at all, so it cannot drift to the live corpus even by
#              mistake.
#
#              WHY THE PDF IS HAND-WRITTEN, and the honest cost. DOCX comes
#              from `python-docx`, already a main dependency. For PDF there
#              is no generator available: `pypdf` READS and manipulates
#              PDFs, it does not lay out text, and `reportlab` / `fpdf2`
#              are not dependencies — adding one is forbidden without an
#              explicit request (CLAUDE.md §5.2). So this writes a minimal
#              single-font PDF with the standard library.
#
#              That is a deliberate, bounded risk and it is MITIGATED BY
#              VERIFICATION, not by confidence: every test round-trips the
#              output through `pypdf` and asserts the text extracts. A real
#              PDF parser accepting the bytes is the only evidence worth
#              having for a hand-rolled serialiser, and `pypdf` being
#              available for reading is what makes the approach defensible.
#              Helvetica is used because it is one of the base-14 fonts a
#              reader supplies itself — no font embedding, which is where a
#              home-made writer would genuinely be out of its depth.
#
#              If a PDF library is ever permitted, this module is the only
#              thing to replace: `render_pdf` is one function behind one
#              signature.
#
#              THE BASE TEMPLATE IS CODE, with a documented seam for a file.
#              R-310-207 asks for a "platform-supplied base template"; the
#              styles, title page and per-page footer here ARE that
#              template. `docx_template_path` lets a branded `.docx` replace
#              it without touching the assembly, which is where a tenant's
#              letterhead belongs.
#
# @relation implements:R-310-207
# =============================================================================

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

from .models import BaselineManifest, RenderFormat
from .service import ResolvedObject

#: Object types rendered as document headings rather than body text.
_HEADING_LEVELS: dict[str, int] = {"heading": 1, "subheading": 2}

#: Page geometry for the hand-written PDF, in PostScript points (A4).
_PAGE_WIDTH = 595
_PAGE_HEIGHT = 842
_MARGIN = 56
_LEADING = 14
_BODY_SIZE = 10
_TITLE_SIZE = 18

#: Conservative average glyph width as a fraction of the font size for
#: Helvetica. Used ONLY to decide where to wrap: under-estimating would
#: overflow the margin, so the figure errs high.
_WIDTH_RATIO = 0.52


class RenderError(RuntimeError):
    """Raised when a baseline cannot be rendered as requested."""


@dataclass(frozen=True, slots=True)
class Rendering:
    """One rendered document."""

    fmt: RenderFormat
    payload: bytes
    page_count: int | None = None
    """Known for PDF, which this module paginates itself. None for DOCX,
    where pagination is the reader's job and any number here would be a
    guess presented as a fact."""


# ---------------------------------------------------------------------------
# Shared assembly
# ---------------------------------------------------------------------------


def _auto_accepted_note(entries: Sequence[ResolvedObject]) -> str:
    """Return the auto-accepted disclosure for a rendered document.

    The same rule as `R-500-019` in the UI, applied to the export: a
    document stating coverage without disclosing how much of it was granted
    by cluster review reads as a review result and is not one. Rendered even
    at zero, so its absence can never be mistaken for "none".
    """
    auto = sum(1 for entry in entries if entry.is_auto_accepted)
    total = len(entries)
    return (
        f"{auto} of {total} included objects were auto-accepted — granted by "
        "cluster review, without individual examination (R-310-007)."
    )


def _ordered(entries: Sequence[ResolvedObject]) -> list[ResolvedObject]:
    """Return entries in container-then-document order.

    The cycle's cascade is what makes a generated document readable, so the
    container ordinal leads and the object ordinal follows.
    """
    return sorted(
        entries,
        key=lambda item: (item.entry.ordinal, item.entry.container, item.entry.object_id),
    )


def _by_container(
    manifest: BaselineManifest, entries: Sequence[ResolvedObject]
) -> list[tuple[str, list[ResolvedObject]]]:
    """Group entries by container, in the manifest's container order."""
    index: dict[str, list[ResolvedObject]] = {}
    for item in entries:
        index.setdefault(item.entry.container, []).append(item)
    return [
        (container, _ordered(index[container]))
        for container in manifest.containers
        if container in index
    ]


# ---------------------------------------------------------------------------
# DOCX — R-310-207
# ---------------------------------------------------------------------------


def render_docx(
    manifest: BaselineManifest,
    entries: Sequence[ResolvedObject],
    *,
    docx_template_path: str | None = None,
) -> Rendering:
    """Render a baseline to DOCX.

    Args:
        manifest: The baseline being rendered.
        entries: Its resolved entries — never read from the live corpus.
        docx_template_path: A branded `.docx` to build on. When omitted the
            code-defined base template is used; this is the seam a tenant's
            letterhead plugs into.

    Returns:
        The rendered document.

    Raises:
        RenderError: When `python-docx` is unavailable or the template
            cannot be opened.
    """
    try:
        import docx  # noqa: PLC0415 — declared main dependency, imported lazily
    except ImportError as exc:  # pragma: no cover - declared main dependency
        raise RenderError("python-docx is required to render DOCX") from exc

    try:
        document = (
            docx.Document(docx_template_path) if docx_template_path else docx.Document()
        )
    except Exception as exc:
        raise RenderError(f"cannot open DOCX template: {exc}") from exc

    document.add_heading(f"{manifest.project_id} — baseline {manifest.tag}", level=0)
    document.add_paragraph(
        f"Cycle {manifest.cycle_id} v{manifest.cycle_version} · "
        f"taken by {manifest.created_by} on "
        f"{manifest.created_at.isoformat(timespec='seconds')}"
    )
    document.add_paragraph(
        f"{manifest.object_count} object versions, {manifest.link_count} "
        "coverage pins."
    )
    document.add_paragraph(_auto_accepted_note(entries))
    if manifest.note:
        document.add_paragraph(manifest.note)

    for container, items in _by_container(manifest, entries):
        document.add_heading(container, level=1)
        for item in items:
            heading_level = _HEADING_LEVELS.get(_type_of(item))
            if heading_level is not None:
                document.add_heading(item.content, level=min(heading_level + 1, 4))
                continue
            if item.is_figure:
                # A figure is a textual notation (R-310-011): rendered as a
                # labelled block, never silently as prose.
                document.add_paragraph(f"Figure — {item.content}")
            else:
                document.add_paragraph(item.content)
            pins = manifest.links_of(item.entry.object_id)
            if pins:
                document.add_paragraph(
                    "Answers: "
                    + ", ".join(f"{p.target_id} (v{p.pinned_version})" for p in pins),
                    style="Intense Quote" if _has_style(document) else None,
                )

    buffer = io.BytesIO()
    document.save(buffer)
    return Rendering(fmt=RenderFormat.DOCX, payload=buffer.getvalue())


def _type_of(item: ResolvedObject) -> str:
    """Return the object type a resolved entry represents.

    The manifest entry does not carry the object type — it carries what is
    needed to IDENTIFY a version — so headings are recognised from the
    `kind`/content shape the resolver supplies. A figure is a notation; a
    heading is short prose with no terminal punctuation. Stated as a
    heuristic rather than dressed up as structure, because inventing a type
    field on the manifest would have been duplication of its own.
    """
    if item.is_figure:
        return "figure"
    text = item.content.strip()
    if text and len(text) <= 80 and not text.endswith((".", "!", "?", ":", ";")):
        return "heading"
    return "paragraph"


def _has_style(document: object) -> bool:
    """Return True when the template defines the quote style we reach for."""
    try:
        styles = document.styles  # type: ignore[attr-defined]
        return "Intense Quote" in {style.name for style in styles}
    except Exception:  # pragma: no cover - defensive against template variety
        return False


# ---------------------------------------------------------------------------
# PDF — R-310-207, hand-written (see the module header for why)
# ---------------------------------------------------------------------------


def _escape(text: str) -> str:
    """Escape a string for a PDF literal string."""
    return (
        text.replace("\\", r"\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _latin1(text: str) -> str:
    """Return text reduced to what a base-14 font can show.

    Helvetica is a Latin-1 font; a character outside it would silently
    render as nothing. Replacing it with `?` makes the loss visible in the
    document instead of leaving a hole a reader cannot account for.
    """
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _wrap(text: str, size: int, width: int) -> list[str]:
    """Wrap text to a pixel width using a conservative glyph estimate."""
    limit = max(1, int(width / (size * _WIDTH_RATIO)))
    words = _latin1(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= limit:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


@dataclass
class _Line:
    text: str
    size: int
    bold: bool = False


def _paginate(lines: Sequence[_Line]) -> list[list[_Line]]:
    """Break lines into pages at the bottom margin."""
    usable = _PAGE_HEIGHT - 2 * _MARGIN
    per_page = max(1, usable // _LEADING)
    pages: list[list[_Line]] = []
    for start in range(0, len(lines), per_page):
        pages.append(list(lines[start : start + per_page]))
    return pages or [[]]


def _content_stream(page: Sequence[_Line], footer: str) -> bytes:
    """Return one page's content stream."""
    parts: list[str] = ["BT"]
    y = _PAGE_HEIGHT - _MARGIN
    for line in page:
        font = "/F2" if line.bold else "/F1"
        parts.append(f"{font} {line.size} Tf")
        parts.append(f"1 0 0 1 {_MARGIN} {y} Tm")
        parts.append(f"({_escape(line.text)}) Tj")
        y -= _LEADING
    parts.append("/F1 8 Tf")
    parts.append(f"1 0 0 1 {_MARGIN} {_MARGIN - 20} Tm")
    parts.append(f"({_escape(footer)}) Tj")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def render_pdf(
    manifest: BaselineManifest, entries: Sequence[ResolvedObject]
) -> Rendering:
    """Render a baseline to PDF.

    A minimal single-font PDF written with the standard library — see the
    module header for why, and for the verification that makes it
    defensible. Every test reads the output back with `pypdf`.

    Args:
        manifest: The baseline being rendered.
        entries: Its resolved entries.

    Returns:
        The rendered document, with its page count.
    """
    width = _PAGE_WIDTH - 2 * _MARGIN
    lines: list[_Line] = [
        _Line(f"{manifest.project_id} - baseline {manifest.tag}", _TITLE_SIZE, True),
        _Line("", _BODY_SIZE),
        _Line(
            f"Cycle {manifest.cycle_id} v{manifest.cycle_version} - taken by "
            f"{manifest.created_by} on "
            f"{manifest.created_at.isoformat(timespec='seconds')}",
            _BODY_SIZE,
        ),
        _Line(
            f"{manifest.object_count} object versions, {manifest.link_count} "
            "coverage pins.",
            _BODY_SIZE,
        ),
    ]
    for chunk in _wrap(_auto_accepted_note(entries), _BODY_SIZE, width):
        lines.append(_Line(chunk, _BODY_SIZE))
    if manifest.note:
        lines.append(_Line("", _BODY_SIZE))
        for chunk in _wrap(manifest.note, _BODY_SIZE, width):
            lines.append(_Line(chunk, _BODY_SIZE))

    for container, items in _by_container(manifest, entries):
        lines.append(_Line("", _BODY_SIZE))
        lines.append(_Line(container, 13, True))
        for item in items:
            prefix = "Figure - " if item.is_figure else ""
            bold = _type_of(item) == "heading"
            for chunk in _wrap(prefix + item.content, _BODY_SIZE, width):
                lines.append(_Line(chunk, _BODY_SIZE, bold))
            pins = manifest.links_of(item.entry.object_id)
            if pins:
                answered = "Answers: " + ", ".join(
                    f"{pin.target_id} (v{pin.pinned_version})" for pin in pins
                )
                for chunk in _wrap(answered, 9, width):
                    lines.append(_Line(chunk, 9))

    pages = _paginate(lines)
    footer_base = f"{manifest.project_id} / {manifest.tag}"
    return Rendering(
        fmt=RenderFormat.PDF,
        payload=_assemble_pdf(pages, footer_base),
        page_count=len(pages),
    )


def _assemble_pdf(pages: Sequence[Sequence[_Line]], footer_base: str) -> bytes:
    """Assemble the PDF file, tracking byte offsets for the xref table.

    The xref offsets are the one part a hand-written PDF gets wrong
    silently, so they are computed from the buffer as it is built rather
    than predicted — and the tests read the result back with `pypdf`, which
    parses the xref and would reject a wrong one.
    """
    objects: list[bytes] = []
    page_count = len(pages)
    # Object numbering: 1 catalog, 2 pages tree, 3 F1, 4 F2, then per page a
    # page object and a content stream.
    first_page_obj = 5
    kids = " ".join(
        f"{first_page_obj + 2 * index} 0 R" for index in range(page_count)
    )

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Count {page_count} /Kids [{kids}] >>".encode("latin-1")
    )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
        b"/Encoding /WinAnsiEncoding >>"
    )

    for index, page in enumerate(pages):
        page_obj = first_page_obj + 2 * index
        stream_obj = page_obj + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} "
                f"{_PAGE_HEIGHT}] /Resources << /Font << /F1 3 0 R /F2 4 0 R "
                f">> >> /Contents {stream_obj} 0 R >>"
            ).encode("latin-1")
        )
        stream = _content_stream(page, f"{footer_base} - page {index + 1} of {page_count}")
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def render(
    manifest: BaselineManifest,
    entries: Sequence[ResolvedObject],
    fmt: RenderFormat,
    *,
    docx_template_path: str | None = None,
) -> Rendering:
    """Render a baseline in the requested format (`R-310-207`)."""
    if fmt is RenderFormat.DOCX:
        return render_docx(manifest, entries, docx_template_path=docx_template_path)
    return render_pdf(manifest, entries)
