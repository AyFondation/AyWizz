#!/usr/bin/env bash
# =============================================================================
# File: k8s_reseed_c2.sh
# Version: 2
# Path: infra/scripts/k8s_reseed_c2.sh
# Description: DEV-only re-seed of C2 identity. Truncates the C2 identity
#              collections (`c2_users`, `c2_role_assignments`) in the running
#              ArangoDB, then restarts c2-auth so its demo-seed lifespan
#              re-creates the seed users with the CURRENT role names.
#
#              Rationale: the demo-seed is idempotent (skips existing users),
#              so a role RENAME (e.g. tenant_manager -> platform_manager,
#              E-100-002 v5) is NOT picked up by a plain restart — the stored
#              user docs keep the old role string. This wipes them so the
#              NEW code re-seeds cleanly. DESTRUCTIVE for C2 identity only:
#              any hand-created users + role grants are lost (dev accounts
#              are re-seeded; hand-made ones are not). NEVER run against a
#              cluster you care about — this is a local-dev convenience.
#
#              v2: uses a one-shot Job (arangosh) instead of `kubectl exec`.
#              docker-desktop's CRI exec transport is unreliable ("HTTP
#              response to HTTPS client"); the Job path mirrors the proven
#              `arangodb-init` Job and only needs `kubectl apply`.
#
#              Wrapper around denied `kubectl apply` / `rollout` (per
#              `.claude/settings.json`); the wrapper is the explicit,
#              auditable entry point per CLAUDE.md §5.3.
#
#              Pre-req: the NEW c2-auth image (with the renamed role) is
#              already deployed. Run AFTER `k8s_build_images.sh` +
#              `run.sh dev --restart`.
#
#              Usage (from monorepo root or anywhere):
#                infra/scripts/k8s_reseed_c2.sh
# =============================================================================

set -euo pipefail

NS="aywizz"
JOB="c2-reseed-wipe"

echo "==> kubectl context: $(kubectl config current-context)"
echo "==> Applying wipe Job (truncate c2_users + c2_role_assignments)"

kubectl delete job "${JOB}" -n "${NS}" --ignore-not-found

kubectl apply -f - <<'YAML'
apiVersion: batch/v1
kind: Job
metadata:
  name: c2-reseed-wipe
  namespace: aywizz
spec:
  backoffLimit: 3
  ttlSecondsAfterFinished: 120
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: wipe
          image: arangodb/arangodb:3.12
          imagePullPolicy: IfNotPresent
          envFrom:
            - configMapRef:
                name: aywizz-config
            - secretRef:
                name: aywizz-secrets
          command:
            - /bin/sh
            - -c
            - |
              set -e
              arangosh \
                --server.endpoint tcp://arangodb:8529 \
                --server.database "$ARANGO_DB" \
                --server.username "$ARANGO_ROOT_USERNAME" \
                --server.password "$ARANGO_ROOT_PASSWORD" \
                --javascript.execute-string 'for (const c of ["c2_users","c2_role_assignments"]) { if (db._collection(c)) { db._collection(c).truncate(); print("truncated "+c); } else { print("absent "+c); } }'
YAML

echo "==> Waiting for the wipe Job to complete"
kubectl wait --for=condition=complete "job/${JOB}" -n "${NS}" --timeout=120s
kubectl logs -n "${NS}" "job/${JOB}" || true
kubectl delete job "${JOB}" -n "${NS}" --ignore-not-found

echo "==> Restarting c2-auth so the demo-seed re-creates users with current roles"
kubectl rollout restart deployment/c2-auth -n "${NS}"
kubectl rollout status deployment/c2-auth -n "${NS}" --timeout=120s

echo "==> C2 re-seed OK — log in again (superroot / dev-superroot)."
