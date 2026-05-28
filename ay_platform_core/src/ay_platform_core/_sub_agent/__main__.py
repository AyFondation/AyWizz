# =============================================================================
# File: __main__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_sub_agent/__main__.py
# Description: Entrypoint shim for `python -m ay_platform_core._sub_agent`.
#              The K8sDispatcher's Pod spec sets
#              `command: ["python", "-m", "ay_platform_core._sub_agent"]`,
#              which lands here ; we delegate to `runtime.main()` and
#              exit with its return code.
#
#              Kept deliberately empty of business logic — keeps the
#              pod-side surface area minimal AND makes the runtime
#              testable in-process without going through __main__.
#
# @relation implements:R-200-030
# =============================================================================

from __future__ import annotations

import sys

from ay_platform_core._sub_agent.runtime import main

if __name__ == "__main__":
    sys.exit(main())
