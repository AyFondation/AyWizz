# Session 2026-04-27 — Auth matrix Phase 2 + Docker cleanup wrapper

## Trigger

Phase 1 (catalog + framework + anonymous + coherence + spec/doc) livrée
plus tôt dans la journée. Phase 2 demandée : `test_role_matrix.py`,
`test_isolation.py`, `test_backend_state.py`, `test_auth_modes.py`,
plus mitigation d'un problème de leak Docker découvert pendant
l'itération.

## Décisions de cadrage validées

1. **Wrapper `docker_test_cleanup.sh` allowlisté** — exécution OK.
2. **Fix #1 (`loop_scope="session"`)** — choisi sur Plan B (function
   scope) pour résoudre le hang.

## Cleanup wrapper (R-cleanup)

Nouveau script
[`ay_platform_core/scripts/docker_test_cleanup.sh`](ay_platform_core/scripts/docker_test_cleanup.sh)
v1 :

- Pattern-match : `arangodb/arangodb`, `minio/minio`, `grafana/loki`,
  `docker.elastic.co/elasticsearch`, `ollama/ollama`,
  `testcontainers/ryuk`. Aucun autre conteneur n'est touché.
- Modes `--dry-run` (liste sans agir) et execution réelle.
- Allowlisted dans `.claude/settings.json` v8 → v9 (5 forms).

**Cause racine du leak diagnostiquée :** Ryuk sidecar absent
(`testcontainers/ryuk:*` jamais pullée dans le devcontainer). Sans
Ryuk, testcontainers compte uniquement sur le `with X as container:`
context manager pour cleanup — bypass quand pytest est tué via
`timeout`/SIGKILL. Cumul observé : 4 minio + 4 arango après 4
itérations interrompues du `test_role_matrix.py` qui hangs.

8 conteneurs orphelins stoppés et supprimés en début de session ;
`docker ps -a` post-CI montre **0 conteneur testcontainer-spawned**
restant — le `with` du fixture session-scoped fonctionne quand pytest
termine proprement.

## Hang `test_role_matrix.py` : root cause + fix

**Diagnostic statique** : `auth_matrix_stack` était
`pytest_asyncio.fixture(scope="session")` SANS `loop_scope`. Avec
`asyncio_default_test_loop_scope=function` au niveau pyproject, la
fixture est construite dans le loop éphémère du **premier test** ;
les tests suivants accèdent à un stack dont le loop est **fermé** —
hang silencieux après ~20 tests (saturation interne des coroutines
pendantes).

**Fix appliqué** :

```python
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def auth_matrix_stack(...): ...
```

Plus, sur chaque test_*.py auth_matrix :

```python
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
```

et chaque `@pytest.mark.asyncio` test devient
`@pytest.mark.asyncio(loop_scope="session")`.

**Fix collatéral** : `make_asgi_client` a maintenant
`raise_app_exceptions=False` — sans ça les exceptions du handler
(p. ex. arango DocumentUpdateError sur DELETE
`/auth/sessions/{nonexistent}`) propageaient dans la frame du test au
lieu de devenir un 500. La matrice est un test black-box auth — tout
non-401/403 prouve que le gate a clearé.

## Phase 2 — fichiers livrés

### `test_role_matrix.py` (63 tests)

Auto-paramétré sur `_catalog.role_gated()` (25 endpoints). Trois
dimensions :

- `test_insufficient_role_is_rejected` — `user` baseline → not 2xx.
- `test_accepted_role_clears_gate` — premier rôle accepté → not
  401/403.
- `test_tenant_manager_excluded_from_content` — sur les endpoints
  qui listent `tenant_manager` dans `excluded_global_roles` → not
  2xx (E-100-002 v2 separation of duties).

### `test_isolation.py` (27 tests)

Filtré aux **item-level endpoints** (path se termine par `_id}`,
`{slug}`, `{version}`). Les list endpoints retournent légitimement
`200 []` pour un foreign tenant — ce n'est pas un leak ; cette
dimension sera couverte par `test_backend_state.py` v2 après seeding.

- `test_cross_tenant_attempt_returns_no_data` — même rôle, foreign
  tenant_id → not 2xx.
- `test_cross_project_attempt_returns_no_data` — même tenant, rôle
  sur project_b mais path targets project_a → not 2xx.

### `test_backend_state.py` (4 tests)

Hand-written par resource type (le contenu du body et la composition
du `_key` sont endpoint-specific). Helpers Arango/MinIO dans
[_backend.py](ay_platform_core/tests/e2e/auth_matrix/_backend.py).

- C5 documents : POST → assert `_key={p}:{slug}` dans `req_documents` ;
  DELETE → assert absent.
- C7 sources : POST → assert row par `source_id` dans `memory_sources`.
- C2 users : POST `/auth/users` (Bearer JWT admin) → assert dans
  `c2_users` + password_hash ≠ plaintext.

Slugs C5 doivent matcher `^[0-9]{3}-[A-Z]+-[A-Z0-9-]+$` (validator
`_DOCUMENT_SLUG`) — fixtures utilisent `700-TEST-BS-DOC-XXXXXX`.

### `test_auth_modes.py` (5 tests)

- `test_local_mode_login_issues_jwt` — round-trip /auth/login →
  token → /auth/verify → claims.
- `test_local_mode_wrong_password_returns_401`.
- `test_none_mode_refused_in_production_environments`
  parametrisé sur `production` + `staging` (R-100-032 startup guard).
- `test_sso_mode_login_returns_501` — l'implémentation SSO actuelle
  est un stub (`SSOMode.authenticate` raise 501). Future migration
  vers full-flow JWKS-mocked documentée dans le docstring : quand
  C2 SSO sera implémenté avec validation JWKS, ce test SHALL
  basculer sur un mock JWKS round-trip et asserter l'équivalence
  des claims propagés vs. local mode.

Pas d'`entraid` mode JWKS-mocked ici — il n'y a actuellement aucune
surface JWKS à mocker (le stub 501 n'attend rien).

## Files modifiés / créés

Nouveau :
- `ay_platform_core/scripts/docker_test_cleanup.sh` v1
- `ay_platform_core/tests/e2e/auth_matrix/_backend.py` v1
- `ay_platform_core/tests/e2e/auth_matrix/test_role_matrix.py` v1
- `ay_platform_core/tests/e2e/auth_matrix/test_isolation.py` v1
- `ay_platform_core/tests/e2e/auth_matrix/test_backend_state.py` v1
- `ay_platform_core/tests/e2e/auth_matrix/test_auth_modes.py` v1

Modifié :
- `.claude/settings.json` v8 → v9 (allowlist du wrapper, 5 forms)
- `ay_platform_core/tests/e2e/auth_matrix/conftest.py` (loop_scope=session)
- `ay_platform_core/tests/e2e/auth_matrix/_clients.py` (insert_session
  pour Bearer flow + raise_app_exceptions=False sur make_asgi_client)
- `ay_platform_core/tests/e2e/auth_matrix/test_anonymous_access.py`
  (loop_scope marker)

## Validation

`ay_platform_core/scripts/run_tests.sh ci` :

```
==> Running ruff check     → ruff: OK
==> Running mypy           → mypy: OK
==> Running pytest         → pytest: OK (1084 passed in 122s)
==> All stages OK
```

Conteneurs orphelins post-run : **0**.

Couverture Phase 2 (auto-paramétrage du catalog 62 endpoints) :
- 63 tests role_matrix
- 27 tests isolation (item endpoints uniquement)
- 4 tests backend_state (C5/C7/C2 representatifs)
- 5 tests auth_modes
- **99 nouveaux tests** + 62 anonymous (Phase 1) + 3 coherence = **164
  tests dédiés à la matrice auth × role × scope**.

## Lessons (candidats `/capture-lesson`)

- **pytest-asyncio session fixture loop_scope** : `scope="session"`
  sur la fixture **doit** être accompagné de `loop_scope="session"`
  ET de `pytest.mark.asyncio(loop_scope="session")` sur les tests
  consommateurs ; sinon hang silencieux après ~20 tests cumulatifs.
  Ce piège est récurrent dans la doc pytest-asyncio mais facile à
  rater.
- **httpx ASGITransport raise_app_exceptions** : pour des tests
  matriciels black-box, mettre `raise_app_exceptions=False` sur la
  transport — sinon les exceptions du handler propagent dans la
  frame du test au lieu de devenir une réponse HTTP, faussant le
  signal du test.
- **Testcontainers Ryuk absent en devcontainer** : le sidecar n'est
  pas auto-pullé. Sans Ryuk, le seul cleanup vient du `with X as
  container:` qui ne s'exécute pas si pytest est SIGKILL. Mitigation
  durable : pull `testcontainers/ryuk:0.5.x` au build du
  devcontainer, OU script wrapper `docker_test_cleanup.sh` pour
  nettoyer post-mortem.
- **Item endpoints vs list endpoints isolation** : les list endpoints
  qui retournent `200 []` pour un foreign tenant ne sont PAS un
  leak (filtre tenant correct, juste rien à montrer). Distinguer
  par pattern de path (`_id}` à la fin = item) — l'isolation
  status-code-based ne s'applique qu'aux item endpoints.

## Suite

- **R-100-060** — production K8s manifests (consomme R-100-124).
- **Q-100-017** — sampling/rétention prod (Loki/ES).
- **Q-100-016** — trace propagation dans C15 Jobs.
- **Mock JWKS pour SSO mode** — quand `SSOMode` ne sera plus un
  stub (`oauth2-proxy` variant A déployé). Test `test_auth_modes`
  basculera vers full-flow JWKS-mocked à ce moment.
- **Body templates par endpoint** dans le catalog pour des role-gate
  tests plus précis (au lieu de tolérer 422 comme "auth gate
  cleared but body invalid").
- **Ryuk sidecar** dans le devcontainer pour cleanup fiable des
  testcontainers même sur SIGKILL.

## Rollback

Branche `main` HEAD avant cette session : commit le plus récent
post-Phase 1 auth_matrix. Rollback safe via `git revert` — additif
pur (4 nouveaux fichiers de tests + helpers) sauf pour
`.claude/settings.json` v8→v9 (allowlist du wrapper, ajout pur). Pas
de breaking change.
