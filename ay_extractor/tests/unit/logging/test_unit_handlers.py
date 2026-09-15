# tests/unit/logging/test_handlers.py — v1
"""Tests for logging/handlers.py — file rotation handler."""

from __future__ import annotations

import pytest

from ayextractor.logging.handlers import _parse_size, create_rotating_handler


class TestParseSize:
    def test_mb(self):
        assert _parse_size("10MB") == 10 * 1024 * 1024

    def test_kb(self):
        assert _parse_size("512KB") == 512 * 1024

    def test_gb(self):
        assert _parse_size("1GB") == 1024 * 1024 * 1024

    def test_case_insensitive(self):
        assert _parse_size("10mb") == 10 * 1024 * 1024

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid size"):
            _parse_size("10bytes")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            _parse_size("")


class TestCreateRotatingHandler:
    def test_creates_handler(self, tmp_path):
        log_file = str(tmp_path / "test.log")
        handler = create_rotating_handler(log_file, rotation="1MB", retention=5)
        assert handler.maxBytes == 1024 * 1024
        assert handler.backupCount == 5

    def test_creates_parent_dirs(self, tmp_path):
        log_file = str(tmp_path / "subdir" / "deep" / "test.log")
        handler = create_rotating_handler(log_file)
        assert (tmp_path / "subdir" / "deep").exists()
