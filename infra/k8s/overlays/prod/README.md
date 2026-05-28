<!-- =============================================================================
File: README.md
Version: 2
Path: infra/k8s/overlays/prod/README.md
Description: Operator handoff for the production K8s overlay (P3.b).
             Read this before running `kubectl apply -k`.
============================================================================= -->

# Production overlay — deploy guide

Production deployment of the AyWizz platform on a real K8s cluster
(non-Docker-Desktop). The overlay shares the same `infra/k8s/base/`
manifests as dev ; it differs only in image pinning, replica counts,
config hashing, and credential discipline.

## Pre-deploy checklist

1. **Build + push images** with a SHA tag (NOT `:latest`).
   The `ci-build-images` workflow produces `ghcr.io/ayfondation/aywizz-api:sha-<commit>`.
   ```
   docker buildx imagetools inspect ghcr.io/ayfondation/aywizz-api:sha-<sha>
   ```

2. **Author `.env` and `.env.secret`** in this directory. Both files
   are git-ignored (Tier-2 per CLAUDE.md §4.6) — operator-authored
   from Vault/KMS at deploy time, never committed.
   - `.env` : non-secret runtime config. Mirror `/.env.example`
     structure but with prod values (ArangoDB URL, MinIO endpoint,
     Gitea URL, NATS URL when wired, C8 gateway URL, etc.).
   - `.env.secret` : credentials (ArangoDB password, MinIO secret
     access key, Gitea root password, C8 bearer tokens, etc.).

3. **Replace SHA placeholder** in `kustomization.yaml` :
   ```yaml
   images:
     - name: ghcr.io/ayfondation/aywizz-api
       newTag: sha-<actual-sha>
   ```

4. **Validate the rendered manifest** before applying :
   ```
   kustomize build infra/k8s/overlays/prod | kubeval --strict
   ```

5. **Apply** :
   ```
   kubectl apply -k infra/k8s/overlays/prod
   ```

6. **Wait for rollout** :
   ```
   kubectl -n aywizz rollout status deployment/c2-auth
   kubectl -n aywizz rollout status deployment/c3-conversation
   # ... per Deployment
   ```

## What's intentionally NOT in this overlay (deferred)

Each of these is a separate operator decision. They are NOT v1
showstoppers — the overlay above produces a runnable platform — but
SHALL be addressed before exposing the platform to real users :

- **TLS termination + cert-manager**. The base `c1_gateway` Traefik
  config does not handle TLS in prod ; add a cert-manager ClusterIssuer
  + Ingress annotation set in a follow-up overlay PR.
- **External Secrets Operator / Sealed Secrets**. The current
  `.env.secret` approach is the simplest possible ; for a real prod
  rotation story, swap the `secretGenerator` to an `ExternalSecret`
  CR. Tracked as Q-100-020.
- **HorizontalPodAutoscaler**. The base manifests set static replica
  counts. HPA on the API tier (CPU + memory) needs operator-decided
  thresholds. Q-100-050.
- **PodDisruptionBudgets**. For the API tier to survive node drains
  during cluster maintenance, add a `minAvailable: 1` PDB per Deployment.
- **Persistent volume sizing**. ArangoDB / MinIO / Gitea StatefulSets
  use the base PVC defaults ; prod sizing depends on tenant load.
- **NetworkPolicy hardening**. `c4_workers` ships a default-deny
  egress policy ; the `aywizz` namespace itself has none in base.
  Consider a deny-by-default ingress policy + explicit allows for
  C1 Traefik → other components.

## Rollback

The overlay uses `disableNameSuffixHash: false` ; every `.env` change
generates a fresh ConfigMap / Secret name and rolls deployments. To
roll back, `kubectl rollout undo deployment/<name>` for each affected
Deployment.
