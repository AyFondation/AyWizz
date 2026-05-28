# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_sub_agent/__init__.py
# Description: Sub-agent runtime entrypoint module (R-200-030..033). Lives
#              in the SAME `ay_platform_core` package as the orchestrator so
#              the K8sDispatcher pod template reuses the `ay-api:local`
#              image with `COMPONENT_MODULE=_sub_agent`. See `runtime.py`
#              for the entry function ; `__main__.py` is the
#              `python -m ay_platform_core._sub_agent` shim.
#
#              The underscore prefix marks this as an INTERNAL execution
#              target (like `_mock_llm`, `_obs`) — not part of the public
#              REST surface, not routed through C1 Traefik.
#
# @relation implements:R-200-030
# @relation implements:R-200-033
# =============================================================================
