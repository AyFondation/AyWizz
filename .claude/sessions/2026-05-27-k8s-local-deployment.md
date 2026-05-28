<!-- =============================================================================
File: 2026-05-27-k8s-local-deployment.md
Version: 1
Path: .claude/sessions/2026-05-27-k8s-local-deployment.md
Description: Session journal — first-ever full platform bring-up on LOCAL
             Kubernetes (Docker Desktop), driven from the devcontainer
             (mode (a) : reachable cluster). Surfaced + fixed 8 real manifest
             bugs, wired host access, achieved a working browser-testable
             stack. Also: started the Documents file-manager feature
             (apiClient + spec), UI build deferred.
============================================================================= -->

# Session — Platform on local Kubernetes (Docker Desktop) (2026-05-27)

## Context

Operator reset Docker Desktop, enabled its Kubernetes, and asked to run the
WHOLE platform on local k8s "en conditions réelles" (k8s is the final target,
CLAUDE.md). The devcontainer's kubectl reaches `docker-desktop` (node Ready,
v1.34.1) → I drove the bring-up + debugged via read-only kubectl + the
allowlisted wrappers. This was the FIRST real apply of the k8s overlays — they
had never been exercised end-to-end, so they had accumulated drift.

## Images

`e2e_stack.sh build` (NEW subcommand, v8) builds `ay-api:local` + `ay-ui:local`
WITHOUT starting compose (the UI build lives in the dev override, so `build`
layers it). Tagged both as `ghcr.io/sorriso/aywizz-*:latest` (operator's image
naming, choice A) so docker-desktop's shared image store serves them with
`imagePullPolicy: IfNotPresent` — no registry push needed for local.

## 8 manifest bugs found + fixed (each would have blocked ANY deploy, incl. prod)

1. **dev overlay namespace conflict** — `namespace: aywizz` renamed the
   separate `c4-workers` Namespace → two `aywizz` Namespaces → kustomize ID
   conflict. Fix: list base components EXPLICITLY (like overlays/system-test),
   OMIT c4_workers (only needed when `C4_DISPATCHER=k8s` ; default is
   in-process). overlays/dev/kustomization v2.
2. **c4-orchestrator ServiceAccount missing** — the `c4-orchestrator` SA was
   mis-located in `c4_workers/serviceaccount.yaml` (excluded) → pod creation
   forbidden. Fix: MOVED the SA to `base/c4_orchestrator/serviceaccount.yaml`
   (travels with its Deployment). c4_workers SA file v2.
3. **minio `MINIO_ROOT_USER`** — statefulset sourced this non-secret username
   from a secret key the `.env.secret` never declared → CreateContainerConfigError
   → cascaded to c5/c6/c7 (they crash if MinIO DNS doesn't resolve at startup).
   Fix: literal `value: minioadmin` (compose convention). c10_minio statefulset v2.
4. **litellm OOMKilled** — 768Mi limit too low under k8s cgroup enforcement.
   Fix: 2Gi. litellm-deployment v2/v3.
5. **litellm cost-forwarder mount** — SAME bug as compose : LiteLLM resolves a
   `callbacks` entry as a FILE next to the config (`/app/cost_forwarder.py`),
   NOT via PYTHONPATH. The deployment mounted it under `/app/callbacks/` +
   PYTHONPATH. Fix: mount at `/app/cost_forwarder.py`, drop PYTHONPATH.
   litellm-deployment v3.
6. **c1 Traefik `--ping`** — liveness/readiness probes hit `/ping:8080` but the
   args lacked `--ping` → 404 → restart loop. Fix: `--ping=true` +
   `--ping.entryPoint=traefik` ; removed the unused `--providers.kubernetesingress`.
   c1_gateway deployment v2.
7. **c1 Traefik ClusterRole missing `nodes`** — the Traefik v3 shared informer
   watches `nodes` ; without the RBAC the informer cache never syncs and the
   KubernetesCRD provider publishes ZERO routers → EVERY request 404s (even
   though the IngressRoute existed). Fix: add `nodes` (get/list/watch).
   c1_gateway serviceaccount v2. (This was the subtle one — pods all Ready,
   but no routes.)
8. **IngressRoute drift vs routers.yml v7** — the k8s mirror routed ALL
   `/api/v1/projects` → C5 and was MISSING 7 routes (C4 artifacts/git/documents,
   `/api/v1/admin/projects`, C2 `/admin`, the C2 GENERIC `/api/v1/projects`
   that serves the project list, C2 preferences, and the C5 `requirements`
   regexp). Result: "Failed to load projects: HTTP 404". Fix: full re-sync of
   `base/c1_gateway/ingressroutes.yaml` (v2) with the priority-based subpath
   routing of `infra/c1_gateway/dynamic/routers.yml` v7. 19 routers now load.

## Host access (operator security constraint : "only via the ingress")

`kubectl port-forward` binds INSIDE the devcontainer → unreachable from the
host browser (ERR_CONNECTION_REFUSED). Fix (dev overlay patch): set ONLY the
`c1-gateway` (Traefik) web Service to `type: LoadBalancer` → docker-desktop
binds it on the host at `localhost:56000` (EXTERNAL-IP `localhost`). Every
other service stays ClusterIP → access is still "only via the ingress" (the
prod-faithful pattern : a LB fronts Traefik). Dashboard stays ClusterIP.

## Demo parity

The dev overlay configmap lacked the demo flags → no demo logins shown, no demo
data. Fix: added `C2_DEMO_SEED_ENABLED=true` + `C2_UX_DEV_MODE_ENABLED=true` as
configMapGenerator literals (overlays/dev/.env is Tier-2 ; literals avoid
editing it). Restarting c2-auth ran the demo seed → 4 demo accounts seeded +
surfaced on /login. Operator added `ANTHROPIC_API_KEY` + `C8_GATEWAY_API_KEY`
to overlays/dev/.env.secret → re-applied (Secret regenerated) + rolled
litellm/c3/c4/c7 to pick them up → chat chain complete.

## Tooling

- `e2e_stack.sh` v8 : `build` subcommand (images only, no containers).
- `infra/k8s/run.sh` v2 : `--crds` flag (installs Traefik v3.3 CRDs before apply).
- In-place update gotchas captured : `--no-jobs` path uses a `python3` pipe that
  SIGPIPEs in this env (avoid) ; Jobs are immutable (revert Job edits before a
  full apply) ; a StatefulSet RollingUpdate WON'T recreate a never-Ready pod
  (`kubectl scale 0 → 1` forces it) ; configmap/secret changes need a
  `rollout restart` of consumers (disableNameSuffixHash = stable names).

## Verification

`run dev --crds` → 15 runtime pods Ready + init Jobs Completed. Traefik: 19 CRD
routers. `/ux/config` 200, `/api/v1/projects` routes to C2 (401 unauth → 200
with session). Operator confirmed in-browser: login OK, projects list OK.
LLM chat pending operator confirmation (keys now in .env.secret).

## Documents file-manager feature (started, UI build DEFERRED)

Operator asked for a file manager in the Documents tab (default root, file
CRUD+edit, folder CRUD). Mapped: backend 100% ready (create/update/mkdir/
rename/move/delete, Gitea-versioned) ; the Working area ALREADY has the tree +
context menu (R-500-010 draft) ; the Documents tab is read-only by design
(D-015 v1). Operator chose BOTH tabs + 3 gaps (root/empty-state, content
editor, blank-file). DONE this session: apiClient `createDocument`(POST) +
`updateDocument`(PUT) + `DocumentRef` type (typecheck green) ; spec R-500-010
bumped to v2 (extends to the Documents tab + the 3 gaps ; supersedes D-015
§7.2 read-only for v1.5). DEFERRED (operator's call, given session length): the
~2000-LOC UI build (shared file-manager component + both tabs + editor) as a
dedicated effort.

## Files

- `infra/k8s/overlays/dev/kustomization.yaml` (v2 — explicit components, demo
  flags, LoadBalancer patch)
- `infra/k8s/base/c1_gateway/ingressroutes.yaml` (v2) · `deployment.yaml` (v2)
  · `serviceaccount.yaml` (v2)
- `infra/k8s/base/c4_orchestrator/serviceaccount.yaml` (v1, NEW) ·
  `kustomization.yaml`
- `infra/k8s/base/c4_workers/serviceaccount.yaml` (v2)
- `infra/k8s/base/c10_minio/statefulset.yaml` (v2)
- `infra/k8s/base/_init/minio-init.yaml` (touched, reverted)
- `infra/k8s/base/c8_gateway/litellm-deployment.yaml` (v3)
- `infra/k8s/run.sh` (v2) · `ay_platform_core/scripts/e2e_stack.sh` (v8)
- `ay_platform_ui/lib/apiClient.ts` · `lib/types.ts` (DocumentRef)
- `requirements/500-SPEC-UI-UX.md` (v4 — R-500-010 v2)

## Next

- **File-manager UI build** (dedicated): shared component (tree + context menu
  + New file/folder + content editor + empty-state/root), wired into BOTH the
  Documents tab and the Working area ; frontend tests ; npm lint/typecheck/build.
- **Q-100-023**: `overlays/prod` still references `../../base` → same namespace
  conflict latent ; before prod, move c4_workers to a standalone opt-in layer
  (the proper fix) so prod/system-test also build clean.
- Backport the relevant k8s fixes' INTENT where compose differs (the cost-
  forwarder mount + Traefik nodes-RBAC are k8s-only ; minio user convention
  matches compose).
