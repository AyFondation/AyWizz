# =============================================================================
# File: setup.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/setup.py
# Description: One-call entry point components invoke from their
#              `main.py` to install the JSON / text formatter on the
#              root logger. Idempotent: subsequent calls replace the
#              handler rather than stacking duplicates.
#
# @relation implements:R-100-104
# =============================================================================

from __future__ import annotations

import logging
import sys

from ay_platform_core.observability.config import LoggingSettings
from ay_platform_core.observability.formatter import JSONFormatter, TextFormatter

# Marker attribute on our handler so we can recognise + replace it on
# repeated `configure_logging()` calls without leaking handlers.
_AY_HANDLER_MARK = "_ay_observability_handler"


def configure_logging(
    component: str, settings: LoggingSettings | None = None
) -> None:
    """Install the platform's structured-logging handler on the root logger.

    Parameters
    ----------
    component:
        Identifier injected into every log line (`"c2_auth"`,
        `"c4_orchestrator"`, `"_observability"`, …). Conventionally the
        Python module name under `ay_platform_core` — same value as
        `COMPONENT_MODULE`.
    settings:
        Optional override; default reads from the env (`LOG_LEVEL`,
        `LOG_FORMAT`, `TRACE_SAMPLE_RATE`).

    Behaviour
    ---------
    * Removes any prior handler installed by an earlier call (idempotent).
    * Sets the root logger level to `settings.log_level`.
    * Installs a stdout `StreamHandler` with the JSON formatter (default)
      or text formatter when `LOG_FORMAT=text`.
    * Quiets uvicorn's default access log to avoid double-logging — the
      ASGI middleware already covers request observability via traces.
    """
    cfg = settings or LoggingSettings()

    if cfg.log_format == "json":
        formatter: logging.Formatter = JSONFormatter(component=component)
    else:
        formatter = TextFormatter(component=component)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    setattr(handler, _AY_HANDLER_MARK, True)

    root = logging.getLogger()
    # Drop our previous handler if any; leave others untouched (caller
    # may have attached test handlers).
    for existing in list(root.handlers):
        if getattr(existing, _AY_HANDLER_MARK, False):
            root.removeHandler(existing)

    root.addHandler(handler)
    root.setLevel(cfg.log_level)

    # uvicorn's `uvicorn.access` logger emits free-form text; route it
    # through the same handler so its lines also become structured.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        sub = logging.getLogger(name)
        sub.handlers = [handler]
        sub.propagate = False
        sub.setLevel(cfg.log_level)
