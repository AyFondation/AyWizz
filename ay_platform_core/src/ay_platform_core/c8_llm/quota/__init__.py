# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/quota/__init__.py
# Description: Global LLM usage-quota subsystem (LLM-governance Lot 3). A single
#              platform-wide QuotaPolicy (parametrable rolling windows, limits in
#              cost AND/OR tokens) applied uniformly to every tenant; usage is
#              summed from the `llm_calls` ledger. Soft (warn) → hard (429 block)
#              enforcement at the C8 gateway. Owned by tenant_manager (E-100-002
#              v3 platform operator).
# =============================================================================
