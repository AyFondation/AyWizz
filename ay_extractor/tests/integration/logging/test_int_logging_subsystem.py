# tests/integration/logging/test_int_logging_subsystem.py — v1
"""Integration tests for the logging subsystem.

Covers: logging/logger.py, logging/handlers.py, logging/context.py
No Docker required.
"""
from __future__ import annotations
import json, logging, sys
from pathlib import Path
import pytest


class TestLoggingContext:
    def test_set_document_context(self):
        from ayextractor.logging.context import clear_context, get_context, set_document_context
        clear_context()
        set_document_context(document_id="doc123", run_id="run456")
        ctx = get_context()
        assert ctx.document_id == "doc123"
        assert ctx.run_id == "run456"

    def test_set_agent_context(self):
        from ayextractor.logging.context import clear_context, get_context, set_agent_context
        clear_context()
        set_agent_context(agent="summarizer", step="phase2")
        ctx = get_context()
        assert ctx.agent == "summarizer"
        assert ctx.step == "phase2"

    def test_combined_context(self):
        from ayextractor.logging.context import clear_context, get_context, set_agent_context, set_document_context
        clear_context()
        set_document_context("doc_A", "run_X")
        set_agent_context("critic", "validation")
        ctx = get_context()
        assert ctx.document_id == "doc_A" and ctx.agent == "critic"

    def test_clear_context(self):
        from ayextractor.logging.context import clear_context, get_context, set_document_context
        set_document_context("doc1", "run1")
        clear_context()
        ctx = get_context()
        assert ctx.document_id is None and ctx.agent is None

    def test_context_as_dict(self):
        from ayextractor.logging.context import clear_context, get_context, set_document_context
        clear_context()
        set_document_context("d1", "r1")
        d = get_context().as_dict()
        assert d["document_id"] == "d1" and "agent" not in d

    def test_context_overwrite(self):
        from ayextractor.logging.context import clear_context, get_context, set_document_context
        clear_context()
        set_document_context("doc_A", "run_A")
        set_document_context("doc_B", "run_B")
        assert get_context().document_id == "doc_B"

    def test_log_context_dataclass(self):
        from ayextractor.logging.context import LogContext
        ctx = LogContext(document_id="d", run_id="r", agent="a", step="s")
        assert len(ctx.as_dict()) == 4


class TestLoggingHandlers:
    def test_parse_size_mb(self):
        from ayextractor.logging.handlers import _parse_size
        assert _parse_size("10MB") == 10 * 1024 * 1024

    def test_parse_size_kb(self):
        from ayextractor.logging.handlers import _parse_size
        assert _parse_size("512KB") == 512 * 1024

    def test_parse_size_gb(self):
        from ayextractor.logging.handlers import _parse_size
        assert _parse_size("1GB") == 1024 ** 3

    def test_parse_size_case_insensitive(self):
        from ayextractor.logging.handlers import _parse_size
        assert _parse_size("10mb") == _parse_size("10MB")

    def test_parse_size_invalid_raises(self):
        from ayextractor.logging.handlers import _parse_size
        with pytest.raises(ValueError):
            _parse_size("10TB")

    def test_create_rotating_handler(self, tmp_path):
        from ayextractor.logging.handlers import create_rotating_handler
        h = create_rotating_handler(str(tmp_path / "test.log"), rotation="1MB", retention=3)
        h.close()

    def test_handler_writes_to_file(self, tmp_path):
        from ayextractor.logging.handlers import create_rotating_handler
        log_file = str(tmp_path / "write.log")
        h = create_rotating_handler(log_file, rotation="1MB")
        lg = logging.getLogger("test_hw")
        lg.addHandler(h); lg.setLevel(logging.DEBUG)
        lg.info("Test message"); h.flush(); h.close()
        assert "Test message" in Path(log_file).read_text()

    def test_handler_creates_parent_dirs(self, tmp_path):
        from ayextractor.logging.handlers import create_rotating_handler
        h = create_rotating_handler(str(tmp_path / "sub" / "deep" / "t.log"))
        h.close()
        assert (tmp_path / "sub" / "deep").exists()


class TestJsonFormatter:
    def test_json_format_valid(self):
        from ayextractor.logging.logger import JsonFormatter
        r = logging.LogRecord("test", logging.INFO, "t.py", 1, "Test msg", None, None)
        data = json.loads(JsonFormatter().format(r))
        assert data["level"] == "INFO" and data["message"] == "Test msg"

    def test_json_format_with_context(self):
        from ayextractor.logging.context import clear_context, set_agent_context, set_document_context
        from ayextractor.logging.logger import JsonFormatter
        clear_context(); set_document_context("doc_f", "run_f"); set_agent_context("critic")
        r = logging.LogRecord("t", logging.WARNING, "t.py", 10, "Warn", None, None)
        data = json.loads(JsonFormatter().format(r))
        assert data["context"]["document_id"] == "doc_f"
        clear_context()

    def test_json_format_with_exception(self):
        from ayextractor.logging.logger import JsonFormatter
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
        r = logging.LogRecord("t", logging.ERROR, "t.py", 1, "Err", None, exc_info)
        data = json.loads(JsonFormatter().format(r))
        assert "ValueError" in data["exception"]


class TestTextFormatter:
    def test_text_format_basic(self):
        from ayextractor.logging.context import clear_context
        from ayextractor.logging.logger import TextFormatter
        clear_context()
        r = logging.LogRecord("t", logging.INFO, "t.py", 1, "Hello", None, None)
        output = TextFormatter().format(r)
        assert "INFO" in output and "Hello" in output

    def test_text_format_with_agent(self):
        from ayextractor.logging.context import clear_context, set_agent_context
        from ayextractor.logging.logger import TextFormatter
        clear_context(); set_agent_context("summarizer", "phase2")
        r = logging.LogRecord("t", logging.DEBUG, "t.py", 1, "Processing", None, None)
        output = TextFormatter().format(r)
        assert "summarizer" in output
        clear_context()


class TestLoggerFactory:
    def test_get_logger(self):
        from ayextractor.logging.logger import get_logger
        lg = get_logger("test_module")
        assert lg.name == "ayextractor.test_module"

    def test_setup_logging_json(self):
        from ayextractor.logging.logger import setup_logging
        setup_logging(level="DEBUG", log_format="json")
        assert logging.getLogger("ayextractor").level == logging.DEBUG

    def test_setup_logging_text(self):
        from ayextractor.logging.logger import setup_logging
        setup_logging(level="WARNING", log_format="text")
        assert logging.getLogger("ayextractor").level == logging.WARNING

    def test_setup_logging_with_file(self, tmp_path):
        from ayextractor.logging.logger import setup_logging
        log_file = str(tmp_path / "app.log")
        setup_logging(level="INFO", log_file=log_file)
        logging.getLogger("ayextractor").info("file test")
        for h in logging.getLogger("ayextractor").handlers:
            h.flush()
        assert Path(log_file).exists()
