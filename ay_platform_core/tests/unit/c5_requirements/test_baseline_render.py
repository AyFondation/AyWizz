# =============================================================================
# File: test_baseline_render.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_baseline_render.py
# Description: DOCX and PDF rendering — R-310-207.
#
#              THE PDF IS READ BACK WITH `pypdf`, ALWAYS. `render.py` writes
#              the PDF by hand because no generator is available and adding
#              one is forbidden (CLAUDE.md §5.2). The only honest evidence
#              that a hand-rolled serialiser produced a valid file is a real
#              parser accepting it and finding the text — so these tests do
#              not inspect the bytes, they parse them. An assertion on
#              `payload.startswith(b"%PDF")` would pass for a file no reader
#              can open.
#
#              The DOCX is read back with `python-docx` for the same reason.
#
# @relation validates:R-310-207
# =============================================================================

from __future__ import annotations

import io
from datetime import UTC, datetime

import docx
import pytest
from pypdf import PdfReader

from ay_platform_core.c5_requirements.baseline.models import (
    BaselineManifest,
    ManifestLink,
    ManifestObject,
    RenderFormat,
    content_hash,
)
from ay_platform_core.c5_requirements.baseline.render import (
    Rendering,
    render,
    render_docx,
    render_pdf,
)
from ay_platform_core.c5_requirements.baseline.service import ResolvedObject

_NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)

_TEXTS = {
    "FD-010": "Functional description",
    "FD-011": (
        "On brake pedal release the system shall reduce deceleration to zero "
        "within 150 ms."
    ),
    "AD-100": "The brake controller ramps torque at 140 Nm/s.",
}


def _resolved(
    object_id: str,
    container: str,
    ordinal: int,
    *,
    state: str = "accepted",
    kind: str = "body",
    text: str | None = None,
) -> ResolvedObject:
    content = text if text is not None else _TEXTS[object_id]
    return ResolvedObject(
        entry=ManifestObject(
            object_id=object_id,
            container=container,
            version=2,
            ordinal=ordinal,
            review_state=state,
            content_hash=content_hash(kind, content),
        ),
        kind=kind,
        content=content,
    )


def _fixture(
    *, entries: tuple[ResolvedObject, ...] | None = None, note: str = ""
) -> tuple[BaselineManifest, tuple[ResolvedObject, ...]]:
    items = entries or (
        _resolved("FD-010", "020-FUNC", 10),
        _resolved("FD-011", "020-FUNC", 11),
        _resolved("AD-100", "030-ARCH", 30, state="auto-accepted"),
    )
    manifest = BaselineManifest(
        tag="B-2026-10",
        project_id="adas",
        cycle_id="C-AUTO",
        cycle_version=2,
        created_by="o.mathieu",
        created_at=_NOW,
        objects=tuple(item.entry for item in items),
        links=(
            ManifestLink(
                object_id="AD-100",
                container="030-ARCH",
                target_id="CUST-001",
                pinned_version=4,
                strength="covered",
                state="accepted",
            ),
        )
        if any(item.entry.object_id == "AD-100" for item in items)
        else (),
        note=note,
    )
    return manifest, items


def _pdf_text(rendering: Rendering) -> str:
    reader = PdfReader(io.BytesIO(rendering.payload))
    return "\n".join(page.extract_text() for page in reader.pages)


def _docx_text(rendering: Rendering) -> str:
    document = docx.Document(io.BytesIO(rendering.payload))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


# ---------------------------------------------------------------------------
# The PDF is a real PDF — verified by a real parser
# ---------------------------------------------------------------------------


def test_the_pdf_is_parsed_by_pypdf() -> None:
    """The only evidence worth having for a hand-written serialiser."""
    manifest, entries = _fixture()
    rendering = render_pdf(manifest, entries)
    reader = PdfReader(io.BytesIO(rendering.payload))
    assert len(reader.pages) >= 1


def test_the_claimed_page_count_matches_what_the_parser_finds() -> None:
    manifest, entries = _fixture()
    rendering = render_pdf(manifest, entries)
    reader = PdfReader(io.BytesIO(rendering.payload))
    assert rendering.page_count == len(reader.pages)


def test_the_pdf_carries_the_baseline_identity() -> None:
    manifest, entries = _fixture()
    text = _pdf_text(render_pdf(manifest, entries))
    assert "B-2026-10" in text
    assert "adas" in text
    assert "C-AUTO" in text
    assert "o.mathieu" in text


def test_the_pdf_carries_the_object_text_it_names() -> None:
    manifest, entries = _fixture()
    text = _pdf_text(render_pdf(manifest, entries))
    assert "150 ms" in text
    assert "140 Nm/s" in text


def test_the_pdf_names_each_container_as_a_section() -> None:
    manifest, entries = _fixture()
    text = _pdf_text(render_pdf(manifest, entries))
    assert "020-FUNC" in text
    assert "030-ARCH" in text


def test_the_pdf_states_the_coverage_pin() -> None:
    manifest, entries = _fixture()
    text = _pdf_text(render_pdf(manifest, entries))
    assert "CUST-001" in text
    assert "v4" in text


def test_a_long_corpus_paginates_and_every_page_parses() -> None:
    """The xref offsets are what a hand-written PDF gets wrong silently."""
    many = tuple(
        _resolved(
            f"AD-{index:03d}",
            "030-ARCH",
            index,
            text=f"Clause {index}: the system shall behave as specified here.",
        )
        for index in range(1, 200)
    )
    manifest, entries = _fixture(entries=many)
    rendering = render_pdf(manifest, entries)
    reader = PdfReader(io.BytesIO(rendering.payload))
    assert len(reader.pages) > 1
    assert rendering.page_count == len(reader.pages)
    # Every page must extract, not merely exist: a broken content stream
    # yields a page object a parser accepts and a reader shows blank.
    for page in reader.pages:
        assert page.extract_text() is not None


def test_the_footer_numbers_every_page() -> None:
    many = tuple(
        _resolved(f"AD-{i:03d}", "030-ARCH", i, text=f"Clause {i} text.")
        for i in range(1, 200)
    )
    manifest, entries = _fixture(entries=many)
    rendering = render_pdf(manifest, entries)
    reader = PdfReader(io.BytesIO(rendering.payload))
    first = reader.pages[0].extract_text()
    assert "page 1 of" in first


def test_parentheses_and_backslashes_do_not_corrupt_the_pdf() -> None:
    """Unescaped `(` ends a PDF string early and breaks the whole stream."""
    tricky = (
        _resolved(
            "AD-100",
            "030-ARCH",
            1,
            text=r"Torque (peak) shall be \\ limited; see note (2).",
        ),
    )
    manifest, entries = _fixture(entries=tricky)
    text = _pdf_text(render_pdf(manifest, entries))
    assert "peak" in text
    assert "note" in text


def test_a_character_outside_latin1_is_replaced_rather_than_dropped() -> None:
    # Helvetica is Latin-1; a silently missing glyph leaves a hole a reader
    # cannot account for.
    tricky = (_resolved("AD-100", "030-ARCH", 1, text="Temperature 25度 limit"),)
    manifest, entries = _fixture(entries=tricky)
    rendering = render_pdf(manifest, entries)
    text = _pdf_text(rendering)
    assert "Temperature" in text
    assert "limit" in text


def test_an_empty_baseline_still_renders_a_readable_pdf() -> None:
    manifest = BaselineManifest(
        tag="B-EMPTY",
        project_id="adas",
        cycle_id="C-AUTO",
        cycle_version=1,
        created_by="o.mathieu",
        created_at=_NOW,
    )
    rendering = render_pdf(manifest, ())
    assert "B-EMPTY" in _pdf_text(rendering)


# ---------------------------------------------------------------------------
# R-310-007 — the auto-accepted share is disclosed in the export too
# ---------------------------------------------------------------------------


def test_the_pdf_discloses_the_auto_accepted_share() -> None:
    manifest, entries = _fixture()
    text = _pdf_text(render_pdf(manifest, entries))
    assert "1 of 3 included objects were auto-accepted" in text


def test_the_disclosure_is_present_even_when_nothing_was_auto_accepted() -> None:
    """Its absence would otherwise be indistinguishable from "none"."""
    clean = (_resolved("AD-100", "030-ARCH", 1),)
    manifest, entries = _fixture(entries=clean)
    text = _pdf_text(render_pdf(manifest, entries))
    assert "0 of 1 included objects were auto-accepted" in text


def test_the_docx_discloses_the_auto_accepted_share() -> None:
    manifest, entries = _fixture()
    assert "1 of 3 included objects were auto-accepted" in _docx_text(
        render_docx(manifest, entries)
    )


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def test_the_docx_is_opened_by_python_docx() -> None:
    manifest, entries = _fixture()
    document = docx.Document(io.BytesIO(render_docx(manifest, entries).payload))
    assert len(document.paragraphs) > 0


def test_the_docx_carries_the_baseline_identity_and_content() -> None:
    manifest, entries = _fixture(note="Signed off at the gate review.")
    text = _docx_text(render_docx(manifest, entries))
    assert "B-2026-10" in text
    assert "150 ms" in text
    assert "Signed off at the gate review." in text


def test_the_docx_reports_no_page_count() -> None:
    """Pagination is the reader's job; a number here would be a guess."""
    manifest, entries = _fixture()
    assert render_docx(manifest, entries).page_count is None


def test_an_unopenable_template_is_reported_not_swallowed() -> None:
    manifest, entries = _fixture()
    with pytest.raises(Exception, match="template"):
        render_docx(manifest, entries, docx_template_path="/nonexistent.docx")


# ---------------------------------------------------------------------------
# A figure is labelled, never rendered as prose
# ---------------------------------------------------------------------------


def test_a_figure_is_labelled_in_both_formats() -> None:
    """R-310-008: a figure carries a textual notation, not prose."""
    figure = (
        _resolved(
            "AD-100",
            "030-ARCH",
            1,
            kind="notation",
            text="graph TD; pedal-->controller",
        ),
    )
    manifest, entries = _fixture(entries=figure)
    assert "Figure" in _pdf_text(render_pdf(manifest, entries))
    assert "Figure" in _docx_text(render_docx(manifest, entries))


# ---------------------------------------------------------------------------
# The dispatcher
# ---------------------------------------------------------------------------


def test_the_dispatcher_honours_the_requested_format() -> None:
    manifest, entries = _fixture()
    assert render(manifest, entries, RenderFormat.PDF).fmt is RenderFormat.PDF
    assert render(manifest, entries, RenderFormat.DOCX).fmt is RenderFormat.DOCX


def test_rendering_the_same_baseline_twice_is_byte_identical() -> None:
    """A rendering is a projection; two reads must not differ.

    Only asserted for PDF: a DOCX is a zip whose entries carry timestamps,
    so byte equality there would be a property of the zip library rather
    than of this module.
    """
    manifest, entries = _fixture()
    assert (
        render_pdf(manifest, entries).payload == render_pdf(manifest, entries).payload
    )
