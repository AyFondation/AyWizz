# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/callbacks/__init__.py
# Description: LiteLLM callback package. Modules in this package are loaded
#              by LiteLLM in its own process (inside the proxy container),
#              not by platform backend services. Keep their external
#              dependencies minimal so the container image stays light.
# =============================================================================
