# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c3_conversation/__init__.py
# Description: C3 Conversation Service package. Registers public contracts
#              at import time. Exposes router and service factory.
# @relation R-100-003
# =============================================================================

from __future__ import annotations

from ay_platform_core.c3_conversation.models import ConversationPublic, MessagePublic
from ay_platform_core.c3_conversation.router import router as conversation_router
from ay_platform_core.c3_conversation.service import ConversationService, get_service

__all__ = [
    "ConversationPublic",
    "ConversationService",
    "MessagePublic",
    "conversation_router",
    "get_service",
]

# Contract registration is handled in tests/fixtures/contract_registry.py
# to keep the source package free of test infrastructure dependencies.
