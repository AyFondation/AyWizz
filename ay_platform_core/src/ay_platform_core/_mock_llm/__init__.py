# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_mock_llm/__init__.py
# Description: Test-only deployable mock of the C8 LiteLLM proxy. Ships as a
#              FastAPI service speaking the OpenAI /v1/chat/completions
#              subset that C4 uses. Responses are pre-scripted via an admin
#              endpoint. The leading underscore in the package name
#              signals "NOT a platform component" to convention checks.
# =============================================================================
