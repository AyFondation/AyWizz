# src/logging/logger.py — v1
"""Logger factory with JSON and text formatters.

See spec §23 for log format and configuration.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from ayextractor.logging.context import get_context


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter matching spec §23.3 format."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = get_context()
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context_dict = ctx.as_dict()
        if context_dict:
            log_entry["context"] = context_dict

        # Extra data passed via record.__dict__
        if hasattr(record, "data") and record.data:  # type: ignore[attr-defined]
            log_entry["data"] = record.data  # type: ignore[attr-defined]

        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable text formatter for development."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = get_context()
        parts = [
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            f"[{record.levelname:8s}]",
            record.name,
        ]
        if ctx.agent:
            parts.append(f"[{ctx.agent}]")
        if ctx.step:
            parts.append(f"({ctx.step})")
        parts.append(f"— {record.getMessage()}")
        return " ".join(parts)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger. Configuration is applied by setup_logging()."""
    return logging.getLogger(f"ayextractor.{name}")


def setup_logging(
    level: str = "INFO",
    log_format: str = "json",
    log_file: str | None = None,
    rotation: str = "10MB",
    retention: int = 30,
) -> None:
    """Configure root ayextractor logger.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        log_format: Output format ("json" or "text").
        log_file: Path to log file (None = stdout only).
        rotation: Max file size before rotation (e.g. "10MB").
        retention: Number of days to keep rotated files.
    """
    root_logger = logging.getLogger("ayextractor")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates on re-init
    root_logger.handlers.clear()

    formatter: logging.Formatter
    if log_format == "json":
        formatter = JsonFormatter()
    else:
        formatter = TextFormatter()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        from ayextractor.logging.handlers import create_rotating_handler

        file_handler = create_rotating_handler(
            log_file, rotation=rotation, retention=retention
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
