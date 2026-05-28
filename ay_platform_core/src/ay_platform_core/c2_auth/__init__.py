# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c2_auth/__init__.py
# Description: C2 Auth Service package. Provides pluggable authentication
#              (none/local/sso) and RBAC-based authorization.
#
# @relation implements:R-100-030
# @relation implements:E-100-001
# @relation implements:E-100-002
# =============================================================================

from __future__ import annotations

from ay_platform_core.c2_auth.router import router as auth_router
from ay_platform_core.c2_auth.service import AuthService

__all__ = ["AuthService", "auth_router"]
