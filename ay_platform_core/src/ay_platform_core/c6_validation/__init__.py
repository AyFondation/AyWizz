# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c6_validation/__init__.py
# Description: C6 Validation Pipeline Registry — public package.
#              Importing the package triggers registration of the built-in
#              `code` domain plugin (D-012, R-700-002: build-time discovery).
#
# @relation implements:R-100-016
# @relation implements:R-700-001
# @relation implements:R-700-002
# =============================================================================

from __future__ import annotations

# Import the built-in code domain plugin to trigger its registration via the
# @register_plugin side effect at import time. This is the v1 plugin-loading
# mechanism (R-700-002).
from ay_platform_core.c6_validation.domains.code import plugin as _code_plugin  # noqa: F401
