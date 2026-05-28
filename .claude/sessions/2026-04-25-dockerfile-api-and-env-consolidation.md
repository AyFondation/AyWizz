# Session 2026-04-25 — Dockerfile.api refactor + env single-source + DB user hardening

## Trigger

Reprise après rebuild devcontainer. Objectif déclaré : faire passer le compose stack au vert pour exercer les e2e (étape "Next planned action" §5 du SESSION-STATE).

Le `up` initial a échoué : `arangodb`, `ollama` et `c12` (n8n) restaient `unhealthy` malgré 6 minutes de boot. La cascade `dependency failed to start` empêchait `c12_workflow_seed` puis l'ensemble du stack d'aboutir.

## Diagnostics enchaînés

1. **arangodb / ollama** : healthchecks `curl -sf …` rejetés à `curl: not found`. Les deux images officielles n'embarquent pas curl. ArangoDB embarque `arangosh` ; Ollama embarque le binaire `ollama` lui-même. → §10.3 cas B (test defect — outil absent).
2. **c12 (n8n)** : `wget --spider http://localhost:5678/healthz` retournait `Connection refused`. La cause s'est révélée double : (a) `start_period: 15s` insuffisant, n8n migrait SQLite from scratch en ~60-90s ; (b) variable `N8N_HOST=c12` qui force le bind sur l'interface DNS Docker `c12` au lieu de `0.0.0.0`, donc localhost ne répond pas. → R-100-118 / spec gap : ces options n8n ne devaient pas être laissées à leur valeur "production".
3. **Erreur plus profonde côté Python** : tous les services Python sortaient `[HTTP 404] database not found` au lifespan. Les repos faisaient `client.db(name)` SANS jamais appeler `ensure_database` ; un volume Arango neuf ne contient que `_system`. → §10.3 cas A (implementation gap), bouché côté infra par un init container.
4. **Workflow seed n8n** : `n8n import:workflow --input=<file>` rejetait `workflows.map is not a function` — n8n 1.74 attend un array ; le fichier était un objet single. Switch vers `--separate --input=/workflows`.

## Décisions structurantes prises pendant la session

### B1 — "monorepo de code, microservices d'exécution"

L'utilisateur questionnait le pattern. Après inventaire :

- `ay_platform_core/` est UN package Python ; chaque sous-module `cN_xxx/` expose son propre `app: FastAPI`.
- L'ancien `Dockerfile.python-service` baked `ARG COMPONENT_MODULE` au build, produisant 8 images distinctes au tag près (`ay-c2-auth:local`, `ay-c3-conversation:local`, …). Mêmes layers, 8 tags : pure dette.

**Choix** : ONE image `ay-api:local`, N containers, `COMPONENT_MODULE` lue **à runtime** depuis l'env. Le `CMD` est production-ready (pas de `--reload`) ; le compose dev surcharge `command:` pour ajouter `--reload`. Migration vers K8s = 1 Deployment par composant pointant la même image, différenciés par leur ConfigMap.

Spec : R-100-114 v1 → v2, ajout R-100-117 (tier-Dockerfiles `infra/docker/Dockerfile.<tier>`), nouvelle §10.4 (topologie local-vs-prod). CLAUDE.md v15 → v16 §4.5 amendée pour autoriser le pattern tier-Dockerfile en complément du per-component.

### Env-var single-source — pas de duplication interne

`.env.test` v1 avait `ARANGO_HOST=arangodb` × 6, `ARANGO_USER=root` × 6, `MINIO_ENDPOINT=minio:9000` × 4, etc. — 60+ lignes pour une dizaine de facts réelles. En plus, drift de noms : C2 utilisait `ARANGO_URL`/`ARANGO_USERNAME` ; C3-C7 utilisaient `ARANGO_HOST`+`ARANGO_PORT`/`ARANGO_USER`. Et `.env.test` (`ARANGO_DB=cN`) divergeait de `.env.example` (`ARANGO_DB=platform`).

**Choix** : 1 fichier env par environnement, chaque variable y apparaît une fois. Shared facts (Arango URL/DB/creds, MinIO endpoint/creds, Ollama URL, `PLATFORM_ENVIRONMENT`) sont **sans préfixe**, lues par chaque Settings via `validation_alias`. Per-component facts (caps, timeouts, MinIO bucket, JWT, embedding model) gardent `C{N}_` parce qu'ils diffèrent légitimement.

Refactor :
- 6 `config.py` + 1 inline (c3) + 1 inline (c9) refondus.
- Noms uniformisés côté code : `arango_url` (au lieu de `arango_host`+`arango_port`), `arango_username` (au lieu de `arango_user`), `arango_db` (au lieu de `arango_db_name` chez C2).
- Call sites adaptés (`create_app`, `from_config`, `service.py`).
- `.env.example` et `.env.test` réécrits.
- Tests `tests/coherence/test_env_completeness.py` passent sans modification — il gère déjà `validation_alias` partagé.

Spec : R-100-110 v1 → v2, R-100-111 v1 → v2.

### DB partagée + collections préfixées

R-100-012 v2 disait "C5 owns the requirements collections, C7 owns the embeddings collections, …" — niveau **collection**. Mais `.env.test` v1 avait dérivé en "1 DB par composant". Aligné sur le modèle collection : 1 DB `platform`, isolation par préfixe (`c2_users`, `c4_runs`, `c5_requirements`, `c7_chunks`, etc.).

`arangodb_init` simplifié : 1 DB au lieu de 6, et création du user `ay_app` avec `rw` sur cette DB.

Spec : R-100-012 v2 → v3.

### Pas de root au runtime

Demande explicite utilisateur : "utilisateur dedie uniquement full owner autorise sur la base de donnees de l application et non pas le user root". Étendu à MinIO et n8n.

- **Arango** : user `ay_app` créé par `arangodb_init` ; `root` réservé au bootstrap.
- **MinIO** : nouveau service `minio_init` (image `minio/mc`) crée user `ay_app`, déclare la policy `ay-app-readwrite` scopée sur les 4 buckets (orchestrator/requirements/validation/memory), attache. `minioadmin` réservé au bootstrap.
- **n8n** : déjà gated par Traefik forward-auth ; pas de creds inter-composants.

Idempotence vérifiée : les init containers utilisent `--ignore-existing` (`mc mb`), guards `users.exists()` (Arango), `mc admin policy create … || true`.

Spec : nouveau R-100-118 (credential hardening + bootstrap responsibility).

## Validation

- 560/560 unit + contract + coherence tests verts.
- `e2e_stack.sh down && up` : tous les init containers `Exited (0)`, tous les services persistants `healthy`, smoke tests through Traefik OK (200 / 401 / 401 / 200 sur `auth/config`, `conversations/health`, `orchestrator/health`, dashboard).
- Logs `arangodb_init` : "created platform / created user ay_app / granted rw on platform.* to ay_app".
- Logs `minio_init` : 4 buckets créés / policy créée / user créé / policy attachée.

## Spec / governance deltas

- `requirements/100-SPEC-ARCHITECTURE.md` v4 → v5 (R-100-012 v2 → v3 ; R-100-110 v1 → v2 ; R-100-111 v1 → v2 ; R-100-114 v1 → v2 ; nouveaux R-100-117 + R-100-118 ; nouvelle §10.4 ; nouvelle §10.5).
- `CLAUDE.md` v15 → v16 (§4.5 tier-Dockerfiles formalisés).
- `infra/docker/Dockerfile.python-service` supprimé ; `infra/docker/Dockerfile.api` créé.
- `ay_platform_core/tests/docker-compose.yml` v2 → v5.
- `ay_platform_core/tests/.env.test` v1 → v2. `.env.example` v1 → v2.
- 6 `config.py` (c2/c4/c5/c6/c7) + 2 inline (c3/c9) refactorés.

## Lessons (candidats `/capture-lesson`)

- **Healthchecks consument leur propre image** : `curl -sf` ne marche que si l'image officielle l'embarque. Toujours préférer un binaire spécifique au domaine (`arangosh`, `ollama list`, `mc admin info`) ou un `wget --spider` (plus largement présent dans les images alpine).
- **n8n `N8N_HOST`** : ne PAS le forcer si on veut que le healthcheck local-loopback marche. La var sert aux URLs publiques, pas au bind ; pour les URLs publiques, `WEBHOOK_URL` suffit.
- **`docker exec`** est en deny-list par CLAUDE.md ; pour diagnostiquer un container il faut passer par `docker inspect <name> --format '{{json .State.Health}}'` (read-only) — utilisable en parallèle.
- **`COMPONENT_MODULE` build-arg vs runtime-env** : un fait runtime ne devrait jamais être baked dans l'image quand l'image elle-même est runtime-pluggable. Règle générale pour les Dockerfiles tier-shared.

## Rollback

Branche `main` HEAD avant cette session : commit `f402b71` (`pre-alpha-002`). Pour annuler : `git reset --hard f402b71` + `git clean -fd` (supprime les fichiers nouveaux comme `Dockerfile.api`, `arangodb_init` block, etc.). À utiliser UNIQUEMENT sur instruction explicite — la session contient des décisions architecturales validées par l'utilisateur.
