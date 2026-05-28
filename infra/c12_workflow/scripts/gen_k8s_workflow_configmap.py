#!/usr/bin/env python3
# =============================================================================
# File: gen_k8s_workflow_configmap.py
# Version: 1
# Path: infra/c12_workflow/scripts/gen_k8s_workflow_configmap.py
# Description: Generate the `c12-workflow-files` ConfigMap manifest from the
#              single source of truth — `infra/c12_workflow/workflows/*.json`
#              — so the SAME workflow files feed both the docker-compose
#              bootstrap (mounted dir + c12_workflow_seed one-shot) AND the
#              Kubernetes bootstrap (this ConfigMap, mounted at /workflows by
#              the c12-workflow Deployment + imported by the c12-workflow-seed
#              Job).
#
#              Why a generated, committed manifest instead of a Kustomize
#              `configMapGenerator`: `kubectl kustomize` / `kubectl apply -k`
#              run with the default RootOnly load-restrictor, which forbids a
#              configMapGenerator from reading files outside the kustomization
#              tree (the workflows live under infra/c12_workflow/, not under
#              infra/k8s/). Generating an in-tree manifest keeps a single
#              source of workflow files while staying RootOnly-safe and
#              reproducible: re-run this script whenever a workflow changes.
#
#              Usage:  python3 infra/c12_workflow/scripts/gen_k8s_workflow_configmap.py
#              Output: infra/k8s/base/c12_workflow/c12-workflow-configmap.yaml
# =============================================================================

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOWS_DIR = _REPO_ROOT / "infra" / "c12_workflow" / "workflows"
_OUT = _REPO_ROOT / "infra" / "k8s" / "base" / "c12_workflow" / "c12-workflow-configmap.yaml"

_HEADER = (
    "# =============================================================================\n"
    "# File: c12-workflow-configmap.yaml\n"
    "# Path: infra/k8s/base/c12_workflow/c12-workflow-configmap.yaml\n"
    "# Description: GENERATED — do not edit by hand. The `c12-workflow-files`\n"
    "#              ConfigMap holds every n8n workflow JSON (single source:\n"
    "#              infra/c12_workflow/workflows/). Mounted at /workflows by the\n"
    "#              c12-workflow Deployment and imported by the c12-workflow-seed\n"
    "#              Job. Regenerate with:\n"
    "#                python3 infra/c12_workflow/scripts/gen_k8s_workflow_configmap.py\n"
    "# =============================================================================\n"
)


def _yaml_block(content: str, indent: str) -> str:
    """Render `content` as a YAML literal block scalar body, each line
    prefixed by `indent`. Trailing newline handled by the `|`/`|-` chomp."""
    lines = content.splitlines()
    return "\n".join(f"{indent}{line}".rstrip() if line else "" for line in lines)


def main() -> int:
    files = sorted(_WORKFLOWS_DIR.glob("*.json"))
    if not files:
        print(f"no workflow JSON found under {_WORKFLOWS_DIR}", file=sys.stderr)
        return 1

    parts: list[str] = [
        _HEADER,
        "apiVersion: v1",
        "kind: ConfigMap",
        "metadata:",
        "  name: c12-workflow-files",
        "  namespace: aywizz",
        "  labels:",
        "    app.kubernetes.io/name: c12-workflow-files",
        "    app.kubernetes.io/component: c12",
        "    app.kubernetes.io/part-of: aywizz-platform",
        "data:",
    ]
    for f in files:
        raw = f.read_text(encoding="utf-8")
        # Validate it is JSON before shipping (fail loud on a broken workflow).
        json.loads(raw)
        # `|-` literal block (strip final newline) keeps the JSON readable in
        # the manifest and byte-faithful when mounted as a file.
        parts.append(f"  {f.name}: |-")
        parts.append(_yaml_block(raw.rstrip("\n"), "    "))

    _OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {_OUT.relative_to(_REPO_ROOT)} ({len(files)} workflow(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
