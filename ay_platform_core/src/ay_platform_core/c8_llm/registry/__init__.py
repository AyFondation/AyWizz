# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/__init__.py
# Description: Platform LLM registry sub-package (increment #2 of the LLM
#              governance feature). Holds the data-driven model registry that
#              replaces the hand-maintained prices + static API-key wiring:
#              models + AES-256-GCM-encrypted provider keys (via
#              `crypto.SecretCipher`) + capabilities + cost + default quality.
#              Spec (800-SPEC-LLM-ABSTRACTION) is authored AFTER the workflow
#              stabilises (operator decision: code-first), hence no
#              `@relation implements:` marker yet.
# =============================================================================
