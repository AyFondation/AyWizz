# Session 2026-04-25 — Credentials hardening + resource limits + observability collector

## Trigger

Suite directe de la session `2026-04-25-dockerfile-api-and-env-consolidation`. Trois axes annoncés au utilisateur (Axe 1 dedicated DB users, Axe 2 CPU/RAM limits, Axe 3 observability collector). Pendant l'exécution, l'utilisateur a ajouté deux remarques de fond : (a) le fichier env devait contenir aussi les credentials admin de bootstrap des backends (root/minioadmin), pas seulement les app users ; (b) il manquait un compte admin **applicatif** pour C2 en mode `local`. Ces remarques ont restructuré l'Axe 1 en "trois classes de credentials".

## Trois classes de credentials

| Classe | Variables | Consommé par | Lectorat |
|---|---|---|---|
| (a) Backend bootstrap admin | `ARANGO_ROOT_USERNAME` / `ARANGO_ROOT_PASSWORD` ; `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | image `arangodb` (first-boot init), image `minio` (root config), `arangodb_init`, `minio_init` | aucun module Python |
| (b) App runtime | `ARANGO_USERNAME=ay_app` / `ARANGO_PASSWORD` ; `MINIO_ACCESS_KEY=ay_app` / `MINIO_SECRET_KEY` | tous les Settings via `validation_alias` | tous les composants C2..C9 |
| (c) Application admin | `C2_LOCAL_ADMIN_USERNAME` / `C2_LOCAL_ADMIN_PASSWORD` | C2 lifespan quand `auth_mode == "local"` | C2 uniquement |

**Mécanique d'injection** :

- (a) → compose lit via `${VAR}` substitution → `e2e_stack.sh` v3 passe `--env-file ay_platform_core/tests/.env.test` à chaque `docker compose` invocation. Les services `arangodb` et `minio` reçoivent leur ROOT_PASSWORD via `environment:`. Les init containers reçoivent les vars via `env_file:`.
- (b) → ajoutées dans chaque `BaseSettings` avec `validation_alias=ARANGO_*` / `MINIO_*` (pas de prefix `C{N}_`) ; lues identiquement par 6+ composants.
- (c) → ajoutées dans `AuthConfig` avec préfixe `C2_LOCAL_ADMIN_*` ; le lifespan de `c2_auth/main.py` appelle une nouvelle fonction `_ensure_local_admin(repo, cfg)` qui crée l'user avec hash argon2id + role `ADMIN` si absent (idempotent). Toujours présentes dans le fichier env (R-100-110 v2 → "exhaustif"), ignorées si `auth_mode != "local"`.

**Test de cohérence env étendu** (`test_env_completeness.py` v2) :

- Whitelist `_INFRA_BOOTSTRAP_VARS = {"ARANGO_ROOT_USERNAME", "ARANGO_ROOT_PASSWORD", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"}` — vars autorisées dans les `.env*` sans Settings field correspondant.
- Le test `test_every_env_entry_corresponds_to_a_settings_field` accepte désormais `parsed.keys() ⊆ Settings_fields ∪ _INFRA_BOOTSTRAP_VARS`.
- Le test `test_every_settings_field_has_env_entry` reste strict (chaque Settings field DOIT avoir une ligne).

**Healthcheck `arangodb` adapté** : utilise `$$ARANGO_ROOT_PASSWORD` (escaped) au lieu de `password` hardcoded. La var est injectée dans le container via `environment:`.

## Limites CPU/RAM

Compose v6 ajoute `deploy.resources.{limits,reservations}` sur tous les services long-running. Init containers exemptés (éphémères). Budget appliqué :

| Service | CPU limit | Mem limit | Reservation |
|---|---|---|---|
| Python (c2..c9 + mock_llm via anchor) | 0.4 | 512M | 0.1 / 128M |
| c1 Traefik | 0.3 | 256M | 0.05 / 64M |
| arangodb | 1.5 | 1.5G | 0.5 / 512M |
| minio | 0.5 | 512M | 0.1 / 128M |
| ollama | 2.0 | 2G | 0.5 / 1G |
| n8n (c12) | 0.5 | 1G | 0.1 / 256M |

Vérifié `docker stats` après stack-up : RAM 12-43 % sur tous, CPU < 25 % au repos. Sous les caps R-100-106 v2 (4 vCPU / 8 GB internal tier ; 8 vCPU / 16 GB platform-wide).

## Observability collector

Module `ay_platform_core/_observability/` :

- **`buffer.py`** — `LogRingBuffer` : per-service deque borné (default 5000), thread-safe via `threading.Lock`. Méthodes `append`, `tail` (filtres service/since/min_severity/limit), `services`, `clear`, `digest`.
- **`parser.py`** — `parse_severity(line) -> str` : best-effort sur JSON / token (`level=…`) / prefix (`ERROR …`) / Python tracebacks. Fallback `INFO`. `is_at_least(severity, minimum)` pour les filtres.
- **`collector.py`** — `LogCollector` : import `docker` SDK lazy dans `start()` pour ne pas plomber le module-load. Un thread daemon par container `ay-*`, attaché via `container.logs(stream=True, follow=True, timestamps=True)`. Strip du timestamp Docker, parse de la severity, append au buffer.
- **`main.py`** — FastAPI : `ObservabilityConfig` (env_prefix `obs_`) + endpoints `/health`, `/logs`, `/errors`, `/digest`, `/services`, `/clear`.

Tests unitaires : 13 sur le buffer + 26 sur le parser = 39 nouveaux tests (tous verts).

Compose service `_obs` :

- Image partagée `ay-api:local`, `COMPONENT_MODULE=_observability`.
- Mount `/var/run/docker.sock:/var/run/docker.sock:ro`.
- Port `8002:8000` sur le host (test infra, pas routé par Traefik).
- **`user: "0:0"`** : le user `app` du Dockerfile n'a pas accès au socket Docker (`Permission denied`). Override en root, **acceptable** parce que (i) test-tier per R-100-121, (ii) socket :ro = daemon refuse les writes même avec root, (iii) code restreint à `containers.list()` + `container.logs()` (pas de `exec`/`kill`/`run`).

Limitation MVP : `containers.list()` à `start()` ne capture que les containers DÉJÀ en cours. Les services Python qui démarrent après `_obs` (typique au boot du stack) sont ratés. Fix follow-up : s'abonner aux Docker `events` pour attacher aux nouveaux containers.

## Spec / governance deltas

- `requirements/100-SPEC-ARCHITECTURE.md` v5 → v7 :
  - **R-100-118 v1 → v2** : 3 classes de creds explicites + bootstrap responsibilities + production hardening.
  - **R-100-106 v1 → v2** : caps internal-tier + platform-wide.
  - **R-100-119** (nouveau) : limits + reservations obligatoires.
  - **R-100-120** (nouveau) : test-tier observability collector.
  - **R-100-121** (nouveau) : interdiction de déployer les `_*` modules en staging/production.
- `ay_platform_core/tests/coherence/test_env_completeness.py` v1 → v2 (whitelist `_INFRA_BOOTSTRAP_VARS`).
- `ay_platform_core/tests/.env.test` v2 → v3 (3 classes de creds + OBS_*).
- `.env.example` v2 → v3 (idem).
- `ay_platform_core/tests/docker-compose.yml` v5 → v6 (limits + `_obs` service + ${VAR} pour root creds + healthcheck arangodb).
- `ay_platform_core/scripts/e2e_stack.sh` v2 → v3 (`--env-file` injecté).
- `ay_platform_core/pyproject.toml` v8 → v9 (dep `docker>=7.1,<8.0`).
- `ay_platform_core/src/ay_platform_core/c2_auth/config.py` v3 → v4 (ajout local_admin_*).
- `ay_platform_core/src/ay_platform_core/c2_auth/main.py` v2 → v3 (`_ensure_local_admin` + lifespan).
- `ay_platform_core/src/ay_platform_core/_observability/` (nouveau) : `__init__.py`, `parser.py`, `buffer.py`, `collector.py`, `main.py`.
- `ay_platform_core/tests/unit/_observability/` (nouveau) : `test_parser.py`, `test_buffer.py`.

## Validation

- 604/604 unit + contract + coherence tests verts (560 + 39 _observability + 5 nouveaux Settings fields).
- `e2e_stack.sh down && up` : tous les services healthy (12 long-running + 4 one-shot exited 0).
- `_obs` smoke : `/health` ok, `/services` liste 7 services, `/digest` confirme le stream.
- `arangodb_init` log : `created platform / created user ay_app / granted rw`.
- `minio_init` log : 4 buckets / policy / user / attach OK.

## Lessons (candidats `/capture-lesson`)

- **Le `--env-file` de Compose ne charge pas le container env mais les variables pour la `${VAR}` substitution**. Distinct de `env_file:` au niveau service. Pour qu'une var atterrisse à la fois dans la substitution Compose ET dans le container, il faut les deux mécanismes.
- **`docker.DockerClient(base_url=…)` connecte au constructor**. Ne pas instancier dans `__init__` si le module est importé en discovery (test_env_completeness traverse tous les `main.py`). Lazy init dans `start()` est obligatoire.
- **Permissions du socket Docker dans un container Python non-root** : par défaut le user `app` (UID 1000+) ne peut pas lire `/var/run/docker.sock` (root:root sur le socket d'un Docker Desktop). Pour un test-tier accepter `user: "0:0"` ; pour de la prod, `group_add` avec le GID du group propriétaire du socket — mais ce GID varie entre hosts (Linux daemon, macOS Docker Desktop, etc.) donc pas portable.
- **Le `validation_alias` Pydantic-settings shareable entre Settings classes** est pleinement supporté ; le test de cohérence l'avait anticipé. C'est ce qui permet l'env single-source sans hack.

## Rollback

Branche `main` HEAD avant les deux sessions de la journée : commit `f402b71` (`pre-alpha-002`). Rollback global : `git reset --hard f402b71` + `git clean -fd`. À utiliser UNIQUEMENT sur instruction explicite — la session a livré 604 tests verts, l'archi B1 validée + 3 axes hardening. Rollback partiel possible en cherry-pickant les fichiers à conserver.
