#!/usr/bin/env python3
# =============================================================================
# File: gen_k8s_c1_configmap.py
# Version: 1
# Path: infra/c1_gateway/scripts/gen_k8s_c1_configmap.py
# Description: Generate the C1 Traefik dynamic-config ConfigMap so the SAME
#              routing policy feeds docker-compose (mounted from
#              `infra/c1_gateway/dynamic/`) AND Kubernetes (this ConfigMap).
#
#              Sources — two shared, one environment-specific:
#                infra/c1_gateway/dynamic/routers.yml      SHARED (28 rules)
#                infra/c1_gateway/dynamic/middlewares.yml  SHARED (3 mw)
#                infra/c1_gateway/dynamic-k8s/services.yml K8s backend binding
#
#              This replaces the IngressRoute / Middleware CRDs, which were a
#              hand-maintained mirror of the same rules. With the file
#              provider, Traefik makes NO Kubernetes API call at all: no
#              ServiceAccount rights, no Role, no ClusterRole, and no CRD to
#              install (installing CRDs is itself a cluster-admin operation).
#
#              Why a generated, committed manifest and not a Kustomize
#              configMapGenerator: `kubectl kustomize` runs RootOnly, which
#              forbids reading files outside the kustomization tree (these
#              live under infra/c1_gateway/, not infra/k8s/). Same pattern as
#              gen_k8s_c8_configmaps.py (c8) and gen_k8s_workflow_configmap.py
#              (c12).
#
#              Usage:  python3 infra/c1_gateway/scripts/gen_k8s_c1_configmap.py
#              Output: infra/k8s/base/c1_gateway/c1-dynamic-configmap.yaml
# =============================================================================

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DYNAMIC = _REPO_ROOT / "infra" / "c1_gateway" / "dynamic"
_DYNAMIC_K8S = _REPO_ROOT / "infra" / "c1_gateway" / "dynamic-k8s"

# (source path, key under ConfigMap `data`) — the key becomes the filename
# inside the mounted /etc/traefik/dynamic directory.
_SOURCES: list[tuple[Path, str]] = [
    (_DYNAMIC / "routers.yml", "routers.yml"),
    (_DYNAMIC / "middlewares.yml", "middlewares.yml"),
    (_DYNAMIC_K8S / "services.yml", "services.yml"),
]

_OUT = _REPO_ROOT / "infra" / "k8s" / "base" / "c1_gateway" / "c1-dynamic-configmap.yaml"

_HEADER = (
    "# =============================================================================\n"
    "# File: c1-dynamic-configmap.yaml\n"
    "# Path: infra/k8s/base/c1_gateway/c1-dynamic-configmap.yaml\n"
    "# Description: GENERATED — do not edit by hand. Traefik file-provider\n"
    "#              dynamic configuration, mounted at /etc/traefik/dynamic.\n"
    "#              Sources (routers + middlewares are SHARED with compose):\n"
    "#                infra/c1_gateway/dynamic/routers.yml\n"
    "#                infra/c1_gateway/dynamic/middlewares.yml\n"
    "#                infra/c1_gateway/dynamic-k8s/services.yml\n"
    "#              Regenerate with:\n"
    "#                python3 infra/c1_gateway/scripts/gen_k8s_c1_configmap.py\n"
    "# ============================================================================="
)


def _yaml_block(content: str, indent: str) -> str:
    """Render `content` as a YAML literal block scalar body, each line
    prefixed by `indent` (blank lines stay empty for valid YAML)."""
    return "\n".join(
        f"{indent}{line}".rstrip() if line else "" for line in content.splitlines()
    )


def main() -> int:
    missing = [str(src) for src, _ in _SOURCES if not src.is_file()]
    if missing:
        print("missing source(s): " + ", ".join(missing), file=sys.stderr)
        return 1

    parts: list[str] = [
        _HEADER,
        "---",
        "apiVersion: v1",
        "kind: ConfigMap",
        "metadata:",
        "  name: c1-traefik-dynamic",
        "  labels:",
        "    app.kubernetes.io/name: c1-traefik-dynamic",
        "    app.kubernetes.io/component: c1",
        "    app.kubernetes.io/part-of: aywizz-platform",
        "data:",
    ]
    for src, key in _SOURCES:
        parts.append(f"  {key}: |-")
        parts.append(_yaml_block(src.read_text(encoding="utf-8").rstrip("\n"), "    "))

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {_OUT.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
