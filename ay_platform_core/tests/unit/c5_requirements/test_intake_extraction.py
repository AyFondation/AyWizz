# =============================================================================
# File: test_intake_extraction.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_intake_extraction.py
# Description: Unit tests for the format adapters of R-300-080 v2 and
#              310-SPEC §4.4.
#
#              The load-bearing assertion is that EVERY anchor resolves: a
#              record whose text is not the slice its interval names cannot
#              be constructed, and an `Extraction` re-verifies all of them.
#              An adapter that computes offsets wrongly would otherwise ship
#              silently broken provenance, and provenance is the first thing
#              a contract review asks for.
#
#              Real files are built here — a real DOCX via python-docx, a
#              real XLSX via stdlib zip, real ReqIF XML — rather than mocked,
#              because the offsets are the thing under test and a mock would
#              simply agree with whatever the code did.
#
# @relation validates:R-310-060
# @relation validates:R-310-061
# @relation validates:R-310-062
# =============================================================================

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime

import pytest

from ay_platform_core.c5_requirements.baseline.models import (
    BaselineManifest,
    ManifestObject,
    content_hash,
)
from ay_platform_core.c5_requirements.baseline.render import render_pdf
from ay_platform_core.c5_requirements.baseline.service import ResolvedObject
from ay_platform_core.c5_requirements.intake.extraction import (
    DEFAULT_MODALS,
    ExtractedRecord,
    Extraction,
    ExtractionError,
    extract,
    extract_docx,
    extract_markdown,
    extract_pdf_pages,
    extract_reqif,
    extract_xlsx,
    has_modal,
)
from ay_platform_core.c5_requirements.intake.intervals import TextInterval
from ay_platform_core.c5_requirements.intake.models import (
    SourceClass,
    SourceFormat,
)

_TIMING = "The system shall reduce deceleration to zero within 250 ms."
_RAMP = "The torque ramp must not exceed 140 Nm/s."
_PROSE = "This chapter describes the braking subsystem."


# ---------------------------------------------------------------------------
# The structural guarantee
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnchorsAlwaysResolve:
    def test_a_record_whose_text_is_not_its_slice_is_refused(self) -> None:
        """R-310-062 as a fact about the data, not a promise about the code."""
        record = ExtractedRecord(
            location="¶1", interval=TextInterval(0, 5), text="wrong"
        )
        with pytest.raises(ExtractionError, match="does not resolve"):
            record.verify_against("hello world")

    def test_an_extraction_reverifies_every_record(self) -> None:
        bad = ExtractedRecord(
            location="¶1", interval=TextInterval(0, 5), text="wrong"
        )
        with pytest.raises(ExtractionError, match="does not resolve"):
            Extraction(
                source_format=SourceFormat.MD,
                source_class=SourceClass.STRUCTURED,
                document_text="hello world",
                records=(bad,),
            )

    def test_the_error_quotes_both_sides(self) -> None:
        # A reviewer has to see what the anchor points at versus what it
        # claims, or the failure is unactionable.
        record = ExtractedRecord(
            location="p.42", interval=TextInterval(0, 5), text="wrong"
        )
        with pytest.raises(ExtractionError, match=re.escape("p.42")):
            record.verify_against("hello world")


@pytest.mark.unit
class TestModalDetection:
    def test_obligations_are_recognised(self) -> None:
        for text in (_TIMING, _RAMP, "It should log the event."):
            assert has_modal(text), text

    def test_descriptive_prose_is_not(self) -> None:
        for text in (_PROSE, "The actuator is mounted on the caliper.", ""):
            assert not has_modal(text), text

    def test_detection_is_case_insensitive(self) -> None:
        assert has_modal("The system SHALL brake.")

    def test_negated_modals_count(self) -> None:
        assert has_modal("The ramp shall not exceed 140 Nm/s.")

    def test_a_restricted_modal_set_is_honoured(self) -> None:
        # The set is a parameter because "what counts as a requirement" is an
        # implementation default, not a truth.
        only_shall = frozenset({"shall"})
        assert has_modal(_TIMING, only_shall)
        assert not has_modal(_RAMP, only_shall)


# ---------------------------------------------------------------------------
# Markdown / plain text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMarkdown:
    def test_only_obligations_become_candidates(self) -> None:
        extraction = extract_markdown(f"{_PROSE}\n{_TIMING}\n{_RAMP}\n")
        assert extraction.candidate_count == 2
        assert [r.text for r in extraction.records] == [_TIMING, _RAMP]

    def test_offsets_index_into_the_stored_document_text(self) -> None:
        extraction = extract_markdown(f"{_PROSE}\n{_TIMING}\n")
        record = extraction.records[0]
        assert record.interval.slice_of(extraction.document_text) == _TIMING

    def test_the_location_names_the_line(self) -> None:
        extraction = extract_markdown(f"{_PROSE}\n{_TIMING}\n")
        assert extraction.records[0].location == "line 2"

    def test_blank_lines_are_skipped_without_breaking_offsets(self) -> None:
        extraction = extract_markdown(f"{_PROSE}\n\n\n{_TIMING}\n\n{_RAMP}")
        for record in extraction.records:
            assert record.interval.slice_of(extraction.document_text) == record.text

    def test_a_drop_with_no_obligation_yields_nothing(self) -> None:
        extraction = extract_markdown(_PROSE)
        assert extraction.candidate_count == 0
        assert extraction.document_text == _PROSE

    def test_the_class_is_structured(self) -> None:
        assert extract_markdown(_TIMING).source_class is SourceClass.STRUCTURED


# ---------------------------------------------------------------------------
# DOCX — a real file, written by python-docx
# ---------------------------------------------------------------------------


def _docx_bytes(paragraphs: list[str]) -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.unit
class TestDocx:
    def test_obligations_are_extracted_with_resolvable_anchors(self) -> None:
        extraction = extract_docx(_docx_bytes([_PROSE, _TIMING, _RAMP]))
        assert extraction.candidate_count == 2
        for record in extraction.records:
            assert record.interval.slice_of(extraction.document_text) == record.text

    def test_the_location_names_the_paragraph(self) -> None:
        extraction = extract_docx(_docx_bytes([_PROSE, _TIMING]))
        assert extraction.records[0].location == "¶2"

    def test_the_class_is_textual(self) -> None:
        # A text layer, but the structure is inferred — hence not `structured`.
        assert extract_docx(
            _docx_bytes([_TIMING])
        ).source_class is SourceClass.TEXTUAL

    def test_a_non_docx_payload_is_refused(self) -> None:
        with pytest.raises(ExtractionError, match="not a readable DOCX"):
            extract_docx(b"this is not a docx")


# ---------------------------------------------------------------------------
# PDF — page texts injected, so offsets are testable without authoring a PDF
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPdfPages:
    def test_paragraphs_are_split_per_page(self) -> None:
        extraction = extract_pdf_pages(
            [f"{_PROSE}\n\n{_TIMING}", f"{_RAMP}"]
        )
        assert [r.location for r in extraction.records] == ["p.1 ¶2", "p.2 ¶1"]

    def test_offsets_resolve_across_pages(self) -> None:
        extraction = extract_pdf_pages([f"{_PROSE}\n\n{_TIMING}", _RAMP])
        for record in extraction.records:
            assert record.interval.slice_of(extraction.document_text) == record.text

    def test_a_text_layer_is_textual(self) -> None:
        assert extract_pdf_pages([_TIMING]).source_class is SourceClass.TEXTUAL

    def test_ocr_makes_it_degraded(self) -> None:
        """R-310-061 — OCR shifts character offsets, so the split check would
        pass against corrupted text and lose a clause invisibly."""
        extraction = extract_pdf_pages([_TIMING], needed_ocr=True)
        assert extraction.source_class is SourceClass.DEGRADED


# ---------------------------------------------------------------------------
# ReqIF — real XML
# ---------------------------------------------------------------------------


_REQIF = f"""<?xml version="1.0" encoding="UTF-8"?>
<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd">
  <CORE-CONTENT><REQ-IF-CONTENT><SPEC-OBJECTS>
    <SPEC-OBJECT IDENTIFIER="REQ-SYS-118">
      <VALUES><ATTRIBUTE-VALUE-STRING THE-VALUE="{_TIMING}"/></VALUES>
    </SPEC-OBJECT>
    <SPEC-OBJECT IDENTIFIER="REQ-SYS-203">
      <VALUES><ATTRIBUTE-VALUE-XHTML><THE-VALUE>{_RAMP}</THE-VALUE>
      </ATTRIBUTE-VALUE-XHTML></VALUES>
    </SPEC-OBJECT>
  </SPEC-OBJECTS></REQ-IF-CONTENT></CORE-CONTENT>
</REQ-IF>""".encode()


@pytest.mark.unit
class TestReqif:
    def test_every_spec_object_is_taken(self) -> None:
        # Nothing to guess: ReqIF is one object per requirement, which is why
        # the class is `structured`.
        extraction = extract_reqif(_REQIF)
        assert extraction.candidate_count == 2
        assert extraction.source_class is SourceClass.STRUCTURED

    def test_the_issuers_own_identifier_is_kept(self) -> None:
        extraction = extract_reqif(_REQIF)
        assert [r.native_id for r in extraction.records] == [
            "REQ-SYS-118", "REQ-SYS-203",
        ]

    def test_anchors_resolve(self) -> None:
        extraction = extract_reqif(_REQIF)
        for record in extraction.records:
            assert record.interval.slice_of(extraction.document_text) == record.text

    def test_descriptive_objects_are_not_filtered_out(self) -> None:
        # A ReqIF SPEC-OBJECT is a requirement by declaration, so the modal
        # heuristic deliberately does not apply here.
        payload = _REQIF.replace(_TIMING.encode(), _PROSE.encode())
        assert extract_reqif(payload).candidate_count == 2

    def test_malformed_xml_is_refused(self) -> None:
        with pytest.raises(ExtractionError, match="not readable ReqIF"):
            extract_reqif(b"<REQ-IF>")


# ---------------------------------------------------------------------------
# XLSX — a real file, written with stdlib zip
# ---------------------------------------------------------------------------


def _xlsx_bytes(rows: list[tuple[str, str]]) -> bytes:
    """Build a minimal inline-string XLSX with stdlib only."""
    cells = []
    for index, (identifier, text) in enumerate(rows, start=1):
        cells.append(
            f'<row r="{index}">'
            f'<c r="A{index}" t="inlineStr"><is><t>{identifier}</t></is></c>'
            f'<c r="B{index}" t="inlineStr"><is><t>{text}</t></is></c>'
            f"</row>"
        )
    sheet = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>' + "".join(cells) + "</sheetData></worksheet>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


@pytest.mark.unit
class TestXlsx:
    def test_one_requirement_per_row(self) -> None:
        extraction = extract_xlsx(
            _xlsx_bytes([("REQ-SYS-118", _TIMING), ("REQ-SYS-203", _RAMP)])
        )
        assert extraction.candidate_count == 2
        assert [r.native_id for r in extraction.records] == [
            "REQ-SYS-118", "REQ-SYS-203",
        ]

    def test_anchors_resolve_and_name_the_cell(self) -> None:
        extraction = extract_xlsx(_xlsx_bytes([("REQ-1", _TIMING)]))
        record = extraction.records[0]
        assert record.location == "row 1 col B"
        assert record.interval.slice_of(extraction.document_text) == _TIMING

    def test_empty_text_cells_are_skipped(self) -> None:
        extraction = extract_xlsx(
            _xlsx_bytes([("REQ-1", _TIMING), ("REQ-2", ""), ("REQ-3", _RAMP)])
        )
        assert extraction.candidate_count == 2

    def test_the_class_is_structured(self) -> None:
        assert extract_xlsx(
            _xlsx_bytes([("REQ-1", _TIMING)])
        ).source_class is SourceClass.STRUCTURED

    def test_a_non_xlsx_payload_is_refused(self) -> None:
        with pytest.raises(ExtractionError, match="not a readable XLSX"):
            extract_xlsx(b"not a zip")

    def test_a_zip_without_a_worksheet_is_refused(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("unrelated.txt", "x")
        with pytest.raises(ExtractionError, match="no worksheet"):
            extract_xlsx(buffer.getvalue())


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistry:
    def test_every_format_is_routed(self) -> None:
        # A format with no adapter would fall through to whichever branch
        # happened to be last, which is how a drop gets misread.
        payloads = {
            SourceFormat.MD: _TIMING.encode(),
            SourceFormat.REQIF: _REQIF,
            SourceFormat.XLSX: _xlsx_bytes([("REQ-1", _TIMING)]),
            SourceFormat.DOCX: _docx_bytes([_TIMING]),
        }
        for fmt, payload in payloads.items():
            assert extract(payload, fmt).source_format is fmt

    def test_the_modal_set_reaches_the_adapters(self) -> None:
        only_shall = frozenset({"shall"})
        extraction = extract(
            f"{_TIMING}\n{_RAMP}".encode(), SourceFormat.MD, modals=only_shall
        )
        assert extraction.candidate_count == 1

    def test_the_default_modal_set_is_the_rfc_2119_trio(self) -> None:
        assert {"shall", "must", "should"} <= DEFAULT_MODALS

# ---------------------------------------------------------------------------
# The paths a CUSTOMER'S FILE actually takes
#
# Added 2026-10-04 after a coverage-relevance audit. The suite already
# covered `extract_pdf_pages` and `extract_xlsx` — but by handing them a
# list of strings the test wrote itself, and an XLSX in the inline-string
# form a LIBRARY writes. The two forms a real drop arrives in — PDF bytes
# through `read_pdf`, and the shared-string XLSX that Excel writes by
# default — were never exercised, so the reader was substituted for by the
# test and the most likely spreadsheet took an untested branch.
# ---------------------------------------------------------------------------


def _pdf_bytes(lines: list[str]) -> bytes:
    """Produce real PDF bytes, using the platform's own writer.

    Not a fixture file: a committed binary rots silently and tells a reader
    nothing. Generating it means the test also proves the export and the
    import agree — a baseline rendered to PDF can be read back in, which is
    a real round trip an operator will perform.
    """
    entries = tuple(
        ResolvedObject(
            entry=ManifestObject(
                object_id=f"AD-{index:03d}",
                container="030-ARCH",
                version=1,
                ordinal=index,
                review_state="accepted",
                content_hash=content_hash("body", text),
            ),
            kind="body",
            content=text,
        )
        for index, text in enumerate(lines, start=1)
    )
    manifest = BaselineManifest(
        tag="B-ROUNDTRIP",
        project_id="adas",
        cycle_id="C-AUTO",
        cycle_version=1,
        created_by="o.mathieu",
        created_at=datetime(2026, 10, 4, tzinfo=UTC),
        objects=tuple(item.entry for item in entries),
    )
    return render_pdf(manifest, entries).payload


@pytest.mark.unit
class TestRealPdfBytes:
    def test_a_real_pdf_is_read_through_pypdf_and_yields_its_requirements(
        self,
    ) -> None:
        """The path a customer's PDF takes, end to end.

        Previously the suite called `extract_pdf_pages` with strings it had
        written, which tested the paragraph splitter while bypassing the
        reader entirely.
        """
        payload = _pdf_bytes([_TIMING, _RAMP])
        extraction = extract(payload, SourceFormat.PDF)

        assert extraction.source_format is SourceFormat.PDF
        assert extraction.candidate_count >= 1
        # Every anchor must resolve against the stored extraction text
        # (R-310-062) — `Extraction.__post_init__` re-verifies, so reaching
        # here at all proves the offsets the PDF reader produced are sound.
        assert extraction.document_text

    def test_an_ocr_declared_pdf_is_degraded(self) -> None:
        payload = _pdf_bytes([_TIMING])
        extraction = extract(payload, SourceFormat.PDF, needed_ocr=True)
        assert extraction.source_class is SourceClass.DEGRADED

    def test_a_pdf_without_ocr_is_textual(self) -> None:
        extraction = extract(_pdf_bytes([_TIMING]), SourceFormat.PDF)
        assert extraction.source_class is SourceClass.TEXTUAL

    def test_bytes_that_are_not_a_pdf_are_refused_with_the_format_named(
        self,
    ) -> None:
        with pytest.raises(ExtractionError, match="not a readable PDF"):
            extract(b"this is not a PDF at all", SourceFormat.PDF)

    def test_an_empty_pdf_yields_no_candidates_rather_than_failing(self) -> None:
        extraction = extract(_pdf_bytes([]), SourceFormat.PDF)
        assert extraction.candidate_count == 0


def _xlsx_shared_strings(rows: list[tuple[str, str]]) -> bytes:
    """Build an XLSX the way EXCEL writes one: shared strings, `t="s"`.

    This is the form a customer's spreadsheet arrives in. The pre-existing
    fixture used `t="inlineStr"`, which is what a writing LIBRARY produces —
    a different branch of `_cell_text`, and the less likely one.
    """
    table: list[str] = []
    cells: list[str] = []
    for index, (identifier, text) in enumerate(rows, start=1):
        first, second = len(table), len(table) + 1
        table.extend([identifier, text])
        cells.append(
            f'<row r="{index}">'
            f'<c r="A{index}" t="s"><v>{first}</v></c>'
            f'<c r="B{index}" t="s"><v>{second}</v></c>'
            f"</row>"
        )
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = (
        '<?xml version="1.0"?>'
        f'<worksheet xmlns="{ns}"><sheetData>' + "".join(cells) + "</sheetData></worksheet>"
    )
    shared = (
        '<?xml version="1.0"?>'
        f'<sst xmlns="{ns}" count="{len(table)}" uniqueCount="{len(table)}">'
        + "".join(f"<si><t>{value}</t></si>" for value in table)
        + "</sst>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", shared)
    return buffer.getvalue()


@pytest.mark.unit
class TestXlsxSharedStrings:
    def test_excels_shared_string_form_resolves_to_its_text(self) -> None:
        extraction = extract(
            _xlsx_shared_strings([("REQ-SYS-118", _TIMING), ("REQ-SYS-203", _RAMP)]),
            SourceFormat.XLSX,
        )
        assert extraction.candidate_count == 2
        assert _TIMING in extraction.document_text
        assert _RAMP in extraction.document_text

    def test_the_issuers_own_identifier_survives_the_shared_string_table(
        self,
    ) -> None:
        # A contract review speaks in the customer's numbering, so the id
        # must come through the indirection intact.
        extraction = extract(
            _xlsx_shared_strings([("REQ-SYS-118", _TIMING)]), SourceFormat.XLSX
        )
        assert extraction.records[0].native_id == "REQ-SYS-118"

    def test_a_dangling_shared_string_index_yields_empty_not_a_crash(self) -> None:
        """A malformed spreadsheet must not take the intake path down."""
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        sheet = (
            '<?xml version="1.0"?>'
            f'<worksheet xmlns="{ns}"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="s"><v>99</v></c></row>'
            "</sheetData></worksheet>"
        )
        shared = (
            '<?xml version="1.0"?>'
            f'<sst xmlns="{ns}"><si><t>REQ-SYS-118</t></si></sst>'
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
            archive.writestr("xl/sharedStrings.xml", shared)
        extraction = extract(buffer.getvalue(), SourceFormat.XLSX)
        assert extraction.candidate_count == 0

    def test_a_workbook_with_no_shared_string_table_still_reads(self) -> None:
        # `_shared_strings` returns [] when the part is absent; an inline
        # workbook must keep working.
        extraction = extract(
            _xlsx_bytes([("REQ-SYS-118", _TIMING)]), SourceFormat.XLSX
        )
        assert extraction.candidate_count == 1
