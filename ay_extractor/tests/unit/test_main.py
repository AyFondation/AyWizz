# tests/unit/test_main.py — v1
"""Tests for main.py — CLI entry point."""

from __future__ import annotations

from pathlib import Path

import pytest
from ayextractor.main import (
    _build_parser,
    _detect_format,
    main,
)

# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestBuildParser:
    def test_version_flag(self, capsys):
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0

    def test_analyze_subcommand(self):
        parser = _build_parser()
        args = parser.parse_args(["analyze", "test.pdf", "-o", "/tmp/out"])
        assert args.command == "analyze"
        assert args.file == Path("test.pdf")
        assert args.output == Path("/tmp/out")

    # D-020 v1 strip removed the `batch` subcommand (C12/n8n owns batch
    # ingestion now); the CLI keeps only `analyze` + `stats`.

    def test_stats_subcommand(self):
        parser = _build_parser()
        args = parser.parse_args(["stats", "/output"])
        assert args.command == "stats"
        assert args.output_dir == Path("/output")

    def test_analyze_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["analyze", "doc.md"])
        assert args.output == Path("./output")
        assert args.doc_type == "report"
        assert args.language is None
        assert args.resume_run is None


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

class TestDetectFormat:
    @pytest.mark.parametrize("suffix,expected", [
        (".pdf", "pdf"),
        (".epub", "epub"),
        (".docx", "docx"),
        (".md", "md"),
        (".txt", "txt"),
        (".png", "image"),
        (".jpg", "image"),
        (".jpeg", "image"),
        (".webp", "image"),
        (".xlsx", None),
        (".doc", None),
    ])
    def test_format_detection(self, suffix: str, expected: str | None):
        assert _detect_format(Path(f"test{suffix}")) == expected


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------

class TestMain:
    def test_no_command_returns_1(self):
        assert main([]) == 1

    def test_analyze_missing_file_returns_1(self, tmp_path: Path):
        result = main(["analyze", str(tmp_path / "nonexistent.pdf")])
        assert result == 1

    def test_analyze_unsupported_format(self, tmp_path: Path):
        f = tmp_path / "data.xlsx"
        f.write_text("data")
        result = main(["analyze", str(f)])
        assert result == 1

    def test_stats_command(self, tmp_path: Path):
        # Create some fake document dirs
        (tmp_path / "doc1" / "run1").mkdir(parents=True)
        (tmp_path / "doc2" / "run1").mkdir(parents=True)
        (tmp_path / "doc2" / "run2").mkdir(parents=True)
        result = main(["stats", str(tmp_path)])
        assert result == 0

    def test_stats_nonexistent_dir(self):
        result = main(["stats", "/nonexistent/path"])
        assert result == 1
