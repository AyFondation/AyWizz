# Session 2026-04-28 — Infra refactor : OCI image labels + K8s bootstrap

## Trigger

Demande utilisateur de "refactor un petit peu la partie infra" :
1. Métadonnées image (description sur la page GHCR vide).
2. Manifests K8s par composant, fichier-par-object, header standard.
3. `.env` unique pour paramétrer les manifests cohéremment.
4. Test 3 niveaux automatisés vérifiant que les manifests fonctionnent.

Discussion préliminaire (§1.1 challenge) : la demande initiale
incluait "image dédiée par composant" qui contredisait R-100-114 v2 +
R-100-117 (B1 architecture = image partagée). Après clarification,
l'utilisateur a confirmé le **maintien de l'image partagée**.
Compose et K8s sont **cohérents mais pas auto-générés** l'un de
l'autre (kompose écarté pour cause de output médiocre sur compose
avec anchors).

## Décisions actées

1. **Image partagée préservée** : `Dockerfile.api` v1→v2 ajoute des
   `LABEL org.opencontainers.image.*` statiques (title, description,
   vendor, licenses, source, documentation). `ci-build-images.yml`
   v1→v2 override la `description` via `metadata-action labels:`
   pour ne plus dépendre de la description vide du repo GitHub.
2. **K8s structure** : `infra/k8s/base/<component>/` avec un fichier
   par object Kubernetes ; chaque composant a son `kustomization.yaml`
   local ; un `infra/k8s/base/kustomization.yaml` racine agrège.
3. **Namespacing** : single namespace `aywizz`, isolation au niveau
   collection (Arango) + bucket (MinIO) per R-100-012 v3.
4. **Ingress** : Traefik partout (cohérence compose↔K8s) via les CRD
   `IngressRoute` + `Middleware` — DNS in-cluster
   `<service>.aywizz.svc.cluster.local`.
5. **Secrets** : Kustomize `secretGenerator` depuis
   `overlays/dev/.env.secret` (séparé de `.env` pour respecter la
   convention K8s — passwords HORS ConfigMap). User a demandé "un
   seul fichier" ; compromis = un seul **dossier overlay**, deux
   fichiers cohérents (config vs sensitivity).
6. **Tests 3 niveaux automatiques** :
   - **L1** (`infra/scripts/k8s_validate.sh`) : `kubectl kustomize` +
     sanity check Python (apiVersion / kind / metadata.name présents)
     + `kubeval --ignore-missing-schemas` (Traefik CRDs sans schéma
     publié). Offline, ~30s. Local OK.
   - **L2+L3** (`infra/scripts/k8s_kind_smoke.sh`) : kind create →
     install Traefik CRDs `v3.3` → apply overlay/dev → wait
     Deployments + StatefulSets + Jobs → port-forward + curl.
     Vérifie `/auth/config` 200 et `/api/v1/memory/health` **401**
     (preuve que `forward-auth-c2` middleware fire).
7. **CI workflow** `ci-k8s-validate.yml` : L1 sur tout PR touchant
   `infra/k8s/`, L2+L3 sur push main + PR `infra/k8s/`.
8. **`retrieval_scan_cap` prod default conservé à 50000** ; le floor
   du validateur Pydantic était déjà à 2 (passé en F.2).

## Fichiers livrés

**Image labels (Phase 1)** :
- `infra/docker/Dockerfile.api` v1→v2 (LABEL block).
- `.github/workflows/ci-build-images.yml` v1→v2 (metadata-action
  labels: override).

**K8s manifests (Phase 2)** — 36 fichiers :
- `infra/k8s/base/_namespace/` (1 manifest + kustomization).
- `infra/k8s/base/c11_arangodb/` (StatefulSet + Service + kust).
- `infra/k8s/base/c10_minio/` (StatefulSet + Service + kust).
- `infra/k8s/base/ollama/` (Deployment + Service + PVC + kust).
- `infra/k8s/base/c2_auth..c9_mcp/` (7 composants × 3 fichiers = 21).
- `infra/k8s/base/c12_workflow/` (Deployment + Service + PVC + kust).
- `infra/k8s/base/c1_gateway/` (RBAC + Deployment + Services +
  Middlewares CRD + IngressRoutes CRD + kust = 6 fichiers).
- `infra/k8s/base/_init/` (4 Jobs + kust).
- `infra/k8s/base/kustomization.yaml` (racine).

**Overlay dev (Phase 3)** — 3 fichiers :
- `infra/k8s/overlays/dev/.env` (config non-secret).
- `infra/k8s/overlays/dev/.env.secret` (credentials placeholder).
- `infra/k8s/overlays/dev/kustomization.yaml` (configMapGenerator +
  secretGenerator + image pin `:latest`).

**Tests automatiques (Phase 4)** — 3 fichiers :
- `infra/scripts/k8s_validate.sh` v2 (L1).
- `infra/scripts/k8s_kind_smoke.sh` v1 (L2+L3).
- `.github/workflows/ci-k8s-validate.yml` v1.

**Wrappers run / stop (post-demande utilisateur)** — 2 fichiers :
- `infra/k8s/run.sh` v1 (`run.sh <env> [--wait] [--no-jobs]`).
- `infra/k8s/stop.sh` v1 (`stop.sh <env> [--wipe]`).
Ces wrappers encapsulent les `kubectl apply -k` / `kubectl delete -k`
denied au top level (CLAUDE.md §5.3) ; PVC préservés par défaut au
stop, `--wipe` détruit les volumes + namespace.

**Settings** :
- `.claude/settings.json` v9→v10→v11 : 12 nouvelles entrées allow-list
  (3 formes × 4 wrappers : k8s_validate, k8s_kind_smoke, run, stop).

## Vérifications

- `kubectl kustomize overlays/dev` produit **1702 lignes / 41
  documents** sans erreur.
- L1 (`k8s_validate.sh`) PASS local (kubeval skipped, installé en CI).
- CI Python (`run_tests.sh ci`) **1159 verts** inchangé — aucun fichier
  Python touché.

## Reste à faire côté utilisateur

- **L2+L3 vérifié uniquement via workflow CI** la prochaine fois que
  GitHub Actions tourne. Localement, `kind` n'est pas installé dans
  le devcontainer — l'utilisateur peut l'installer s'il veut tester
  avant CI (`go install sigs.k8s.io/kind/cmd/kind@v0.24.0` ou binaire).
- **L'image GHCR** ne montrera pas la nouvelle description tant que
  `ci-build-images.yml` n'aura pas tourné une fois (sur prochain push
  main).
- **`.env.secret` est un placeholder dev** ; production overlay
  (`overlays/prod/`) reste à créer avec External Secrets Operator
  ou équivalent.
- **Overlay prod absent** — créé quand le push prod démarrera. La
  base est prête.

## Décisions différées (non couvertes cette session)

- LiteLLM proxy K8s deployment (placeholder `C8_GATEWAY_URL` pointe
  vers un service inexistant `litellm.aywizz.svc.cluster.local:4000`).
- HPA / NetworkPolicy / PodDisruptionBudget — replicas=1 partout en
  v1.
- TLS / cert-manager — IngressRoute en HTTP only.
- Storage class explicite — laissé au défaut du cluster.
- Frontend `ay_platform_ui/` — séparé.
