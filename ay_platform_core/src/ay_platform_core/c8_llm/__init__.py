# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/__init__.py
# Description: C8 LLM Gateway package marker. C8 itself is LiteLLM deployed
#              as a Kubernetes proxy (R-800-001). This Python package
#              contains the client-side abstractions and config-side helpers
#              that internal components use when talking to the proxy, plus
#              the cost-tracker callback packaged into the LiteLLM image.
# =============================================================================

from ay_platform_core.c8_llm.models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    CostSummary,
)

__all__ = [
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatMessage",
    "CostSummary",
]
