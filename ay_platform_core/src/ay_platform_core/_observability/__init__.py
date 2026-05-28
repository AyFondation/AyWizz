# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_observability/__init__.py
# Description: Test-tier observability collector. Subscribes to live
#              Docker log streams from every `ay-*` container in the
#              compose stack, buffers per service in a ring buffer, and
#              exposes simple HTTP endpoints for inspection.
#
#              R-100-120 / R-100-121: NOT a platform component. The
#              underscore prefix marks the module as test/dev-only;
#              the production K8s manifests SHALL NOT deploy it.
# =============================================================================
