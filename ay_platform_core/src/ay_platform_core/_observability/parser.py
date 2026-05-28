# =============================================================================
# File: parser.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_observability/parser.py
# Description: Log-line parser. Identifies severity from a log line by
#              looking for (in order): a JSON object with a `level` /
#              `severity` field; a `level=…` / `severity=…` token; a
#              level prefix (`ERROR: …`); a Python traceback marker.
#
# @relation implements:R-100-120
# =============================================================================

from __future__ import annotations

import json
import re
from typing import Final

# Severity rank used to filter logs by minimum severity. Higher = more severe.
SEVERITY_RANK: Final[dict[str, int]] = {
    "DEBUG": 0,
    "INFO": 1,
    "WARNING": 2,
    "ERROR": 3,
    "CRITICAL": 4,
}

# Aliases the parser normalises to one of the ranks above.
_NORMALISE: Final[dict[str, str]] = {
    "WARN": "WARNING",
    "FATAL": "CRITICAL",
    "ERR": "ERROR",
}

_LEVEL_TOKEN_RE = re.compile(
    r"(?i)\b(?:level|severity|log_level)\s*[:=]\s*\"?(DEBUG|INFO|WARN|WARNING|ERROR|ERR|CRITICAL|FATAL)\"?"
)
_LEVEL_PREFIX_RE = re.compile(
    r"^\s*(DEBUG|INFO|WARN|WARNING|ERROR|ERR|CRITICAL|FATAL)\b"
)

_TRACEBACK_MARKER = "Traceback (most recent call last):"


def normalise_severity(severity: str) -> str:
    """Map a raw severity token to the canonical SEVERITY_RANK key.

    Unknown tokens default to ``"INFO"`` — the parser is lenient by design;
    nobody wants the test stack to crash because a third-party library
    invented its own log level.
    """
    upper = severity.upper()
    upper = _NORMALISE.get(upper, upper)
    if upper not in SEVERITY_RANK:
        return "INFO"
    return upper


def parse_severity(line: str) -> str:
    """Best-effort extraction of the log severity from one line.

    Order of attempts:
      1. JSON: parse the line as JSON and read ``level`` / ``severity`` /
         ``log_level``.
      2. Token: look for ``level=ERROR`` / ``severity:"WARN"`` style.
      3. Prefix: look for a leading ``ERROR ``, ``WARN: `` etc.
      4. Traceback: a line containing the Python traceback marker is
         classified as ERROR.

    Falls back to ``"INFO"`` when no signal is found.
    """
    stripped = line.strip()

    # 1. JSON
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                for key in ("level", "severity", "log_level"):
                    raw = obj.get(key)
                    if isinstance(raw, str):
                        return normalise_severity(raw)
        except (json.JSONDecodeError, ValueError):
            pass

    # 2. Token (level=…, severity=…)
    m = _LEVEL_TOKEN_RE.search(line)
    if m:
        return normalise_severity(m.group(1))

    # 3. Prefix (ERROR …, WARN: …)
    m = _LEVEL_PREFIX_RE.match(stripped)
    if m:
        return normalise_severity(m.group(1))

    # 4. Python traceback
    if _TRACEBACK_MARKER in line:
        return "ERROR"

    return "INFO"


def is_at_least(severity: str, minimum: str) -> bool:
    """``True`` when ``severity`` is at least as severe as ``minimum``."""
    return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(minimum, 0)
