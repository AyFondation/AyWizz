# tests/unit/logging/test_logger.py — v1
"""Tests for logging/logger.py — logger factory and formatters."""

from __future__ import annotations

import json
import logging

from ayextractor.logging.context import clear_context, set_document_context
from ayextractor.logging.logger import JsonFormatter, TextFormatter, get_logger, setup_logging


class TestJsonFormatter:
    def setup_method(self):
        clear_context()

    def teardown_method(self):
        clear_context()

    def test_format_basic(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Hello", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "Hello"
        assert "timestamp" in parsed

    def test_format_with_context(self):
        set_document_context("doc1", "run1")
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test msg", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["context"]["document_id"] == "doc1"


class TestTextFormatter:
    def test_format_basic(self):
        formatter = TextFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Hello text", args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "Hello text" in output
        assert "INFO" in output


class TestGetLogger:
    def test_returns_logger(self):
        logger = get_logger("test_module")
        assert logger.name == "ayextractor.test_module"


class TestSetupLogging:
    def test_setup_json(self):
        setup_logging(level="DEBUG", log_format="json")
        root = logging.getLogger("ayextractor")
        assert root.level == logging.DEBUG
        assert len(root.handlers) >= 1

    def test_setup_text(self):
        setup_logging(level="INFO", log_format="text")
        root = logging.getLogger("ayextractor")
        assert root.level == logging.INFO
