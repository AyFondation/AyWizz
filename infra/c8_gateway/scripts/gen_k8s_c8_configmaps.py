#!/usr/bin/env python3
# =============================================================================
# File: gen_k8s_c8_configmaps.py
# Version: 1
# Path: infra/c8_gateway/scripts/gen_k8s_c8_configmaps.py
# Description: Generate the C8 K8s ConfigMaps from their single sources of
#              truth so the SAME files feed docker-compose (mounted) AND
#              Kubernetes (these ConfigMaps) :
#                - `c8-litellm-config`  ← infra/c8_gateway/config/litellm-config.yaml
#                - `c8-litellm-forwarder` ← infra/c8_gateway/callbacks/cost_forwarder.py
#
#              Why a generated, committed manifest (not a Kustomize
#              configMapGenerator): `kubectl kustomize` runs RootOnly, which
#              forbids reading files outside the kustomization tree (these
#              live under infra/c8_gateway/, not infra/k8s/). Same pattern as
#              gen_k8s_workflow_configmap.py (c12).
#
#              Usage:  python3 infra/c8_gateway/scripts/gen_k8s_c8_configmaps.py
#              Output: infra/k8s/base/c8_gateway/c8-configmaps.yaml
# =============================================================================

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG = _REPO_ROOT / "infra" / "c8_gateway" / "config" / "litellm-config.yaml"
_FORWARDER = _REPO_ROOT / "infra" / "c8_gateway" / "callbacks" / "cost_forwarder.py"
_OUT = _REPO_ROOT / "infra" / "k8s" / "base" / "c8_gateway" / "c8-configmaps.yaml"

_HEADER = (
    "# =============================================================================\n"
    "# File: c8-configmaps.yaml\n"
    "# Path: infra/k8s/base/c8_gateway/c8-configmaps.yaml\n"
    "# Description: GENERATED — do not edit by hand. Two ConfigMaps for the C8\n"
    "#              tier : `c8-litellm-config` (the LiteLLM proxy config) and\n"
    "#              `c8-litellm-forwarder` (the cost forwarder mounted into the\n"
    "#              off-the-shelf proxy, §4.5). Single sources :\n"
    "#                infra/c8_gateway/config/litellm-config.yaml\n"
    "#                infra/c8_gateway/callbacks/cost_forwarder.py\n"
    "#              Regenerate with:\n"
    "#                python3 infra/c8_gateway/scripts/gen_k8s_c8_configmaps.py\n"
    "# ============================================================================="
)


def _yaml_block(content: str, indent: str) -> str:
    """Render `content` as a YAML literal block scalar body, each line
    prefixed by `indent` (blank lines stay empty for valid YAML)."""
    return "\n".join(
        f"{indent}{line}".rstrip() if line else "" for line in content.splitlines()
    )


def _configmap(name: str, component: str, filename: str, body: str) -> list[str]:
    return [
        "---",
        "apiVersion: v1",
        "kind: ConfigMap",
        "metadata:",
        f"  name: {name}",
        "  namespace: aywizz",
        "  labels:",
        f"    app.kubernetes.io/name: {name}",
        f"    app.kubernetes.io/component: {component}",
        "    app.kubernetes.io/part-of: aywizz-platform",
        "data:",
        f"  {filename}: |-",
        _yaml_block(body.rstrip("\n"), "    "),
    ]


def main() -> int:
    if not _CONFIG.is_file() or not _FORWARDER.is_file():
        print(f"missing source(s): {_CONFIG} / {_FORWARDER}", file=sys.stderr)
        return 1
    parts: list[str] = [_HEADER]
    parts += _configmap(
        "c8-litellm-config", "c8", "config.yaml", _CONFIG.read_text(encoding="utf-8"),
    )
    parts += _configmap(
        "c8-litellm-forwarder",
        "c8",
        "cost_forwarder.py",
        _FORWARDER.read_text(encoding="utf-8"),
    )
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {_OUT.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
