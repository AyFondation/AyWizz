# src/logging/handlers.py — v1
"""File rotation handler for log files.

See spec §23.2 for rotation and retention configuration.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _parse_size(size_str: str) -> int:
    """Parse size string like '10MB' into bytes.

    Supported suffixes: KB, MB, GB (case-insensitive).
    """
    match = re.match(r"^(\d+)\s*(KB|MB|GB)$", size_str.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid size format: {size_str!r}. Use e.g. '10MB'.")
    value = int(match.group(1))
    unit = match.group(2).upper()
    multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}
    return value * multipliers[unit]


def create_rotating_handler(
    log_file: str,
    rotation: str = "10MB",
    retention: int = 30,
) -> RotatingFileHandler:
    """Create a rotating file handler.

    Args:
        log_file: Path to log file.
        rotation: Max file size before rotation (e.g. "10MB").
        retention: Number of backup files to keep.

    Returns:
        Configured RotatingFileHandler.
    """
    path = Path(log_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    max_bytes = _parse_size(rotation)

    handler = RotatingFileHandler(
        filename=str(path),
        maxBytes=max_bytes,
        backupCount=retention,
        encoding="utf-8",
    )
    return handler
