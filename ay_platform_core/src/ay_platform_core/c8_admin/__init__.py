# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_admin/__init__.py
# Description: C8 admin tier — the user-facing FastAPI app of the C8 LLM
#              governance domain (distinct from the internal cost receiver in
#              `c8_llm.main`, which has no forward-auth). Hosts the platform
#              LLM registry admin surface (platform_manager). Selected at runtime
#              via `COMPONENT_MODULE=c8_admin` → `ay_platform_core.c8_admin.main:app`
#              (R-100-114). The registry domain logic lives in
#              `c8_llm.registry`; this package is the thin app/wiring layer.
# =============================================================================
