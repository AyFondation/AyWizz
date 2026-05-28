# Session 2026-04-27 — Auth × Role × Scope test matrix framework

## Trigger

Demande utilisateur : tests e2e exhaustifs et systématiques verifiant le
comportement de l'API en fonction de (a) mode d'authentification,
(b) profil utilisateur, avec vérification backend (ArangoDB/MinIO) et
maintien dans la durée comme règle de bonnes pratiques. Plus :
documentation matricielle expliquant la stratégie de test.

## Décisions de cadrage validées

1. **5 dimensions** (au lieu de 2 demandées) : auth mode, role, tenant
   isolation, project isolation, backend state.
2. **Hiérarchie de rôles à 5 niveaux** précisée par l'utilisateur :
   - `tenant_manager` — super-root, **content-blind** (création/listing
     de tenants + grant des admins, pas d'accès au contenu).
   - `admin` (= `tenant_admin`) — admin tenant-scoped (crée projets +
     grant project_owners + accède au contenu de SON tenant).
   - `project_owner`, `project_editor`, `project_viewer`.
3. **Coherence script obligatoire** — drift catalogue ↔ routes échoue le
   build.
4. **`entraid` mode** testé full-flow avec mock JWKS, à basculer sur
   l'intégration C2 réelle quand disponible.
5. **Documentation matricielle** auto-générée dans
   `requirements/065-TEST-MATRIX.md`.

## Architecture livrée

### Catalog-driven test framework

Source unique de vérité :
[`ay_platform_core/tests/e2e/auth_matrix/_catalog.py`](ay_platform_core/tests/e2e/auth_matrix/_catalog.py)
— une `EndpointSpec` par route HTTP de la plateforme (62 entries
couvrant C2/C3/C4/C5/C6/C7/C9). Champs : component, method, path,
auth requirement, scope (none/tenant/project), accept_roles +
accept_global_roles, excluded_global_roles (pour tenant_manager
content-blindness), backend (arango/minio), backend_collection,
success_status, notes.

### Stack composé

[`_stack.py`](ay_platform_core/tests/e2e/auth_matrix/_stack.py) compose
les 7 apps FastAPI partageant 1 ArangoDB + 1 MinIO via testcontainers.
B1 architecture compatible : C2/C3/C4/C5/C6/C7 directement, C9 via
RemoteRequirementsService/RemoteValidationService pointing aux
ASGITransports de C5/C6. Scripted LLM mock pour C4 (pas de provider
key en CI).

### Tests parametrés

[`test_anonymous_access.py`](ay_platform_core/tests/e2e/auth_matrix/test_anonymous_access.py)
— 62 tests auto-paramétrés depuis le catalog. Pour chaque endpoint :

- non-OPEN : SHALL NOT retourner 2xx sans identité (codes acceptés
  401/403/404/422 — la garantie est "pas de leak").
- OPEN : SHALL être joignable sans auth (pas de 401/403).

### Coherence script

[`tests/coherence/test_route_catalog.py`](ay_platform_core/tests/coherence/test_route_catalog.py)
— 3 tests :

1. `test_catalog_matches_live_routes` — drift bidirectionnel
   detection (route en code mais pas en catalog → fail ; entry en
   catalog mais pas en code → fail).
2. `test_catalog_entries_have_consistent_role_gates` — ROLE_GATED
   doit déclarer ≥1 rôle ; OPEN/AUTHENTICATED ne doivent en déclarer
   aucun ; pas de `tenant_manager` dans accept_global_roles ET
   excluded_global_roles simultanément.
3. `test_each_endpoint_appears_exactly_once` — sanité contre
   duplicatas.

### Documentation auto-générée

[`requirements/065-TEST-MATRIX.md`](requirements/065-TEST-MATRIX.md)
— rendu Markdown du catalog : stratégie de test (5 dimensions),
hiérarchie de rôles, table par composant (62 lignes), contrat de
maintenance.

Script générateur :
[`ay_platform_core/scripts/checks/generate_test_matrix_doc.py`](ay_platform_core/scripts/checks/generate_test_matrix_doc.py)
avec `--write` (régénère) et `--check` (drift detection).

## Spec / governance deltas

- **`E-100-002` v1 → v2** dans
  [100-SPEC-ARCHITECTURE.md](requirements/100-SPEC-ARCHITECTURE.md) v12
  → v13. Hiérarchie 5-rôles formalisée + clause de vérification
  référençant le test matrix. `tenant_manager` content-blindness
  spelled out.
- **`RBACGlobalRole`** v1 → v2 ajoute `TENANT_MANAGER`. `ADMIN` et
  `TENANT_ADMIN` documentés comme synonymes.
- **`requirements/065-TEST-MATRIX.md`** v1 (nouveau). Auto-généré.
- **`CLAUDE.md` v19 → v20** : nouvelle §13 "Auth × Role × Scope Test
  Matrix" — contrat de maintenance, couplage avec §8.4 + §10.

## Files

Nouveau module test :

- `ay_platform_core/tests/e2e/auth_matrix/_catalog.py` v1
- `ay_platform_core/tests/e2e/auth_matrix/_stack.py` v1
- `ay_platform_core/tests/e2e/auth_matrix/_clients.py` v1
- `ay_platform_core/tests/e2e/auth_matrix/conftest.py` v1
- `ay_platform_core/tests/e2e/auth_matrix/test_anonymous_access.py` v1
- `ay_platform_core/tests/e2e/auth_matrix/__init__.py` v1

Coherence + doc :

- `ay_platform_core/tests/coherence/test_route_catalog.py` v1
- `ay_platform_core/scripts/checks/generate_test_matrix_doc.py` v1
- `requirements/065-TEST-MATRIX.md` v1

Modifications :

- `ay_platform_core/src/ay_platform_core/c2_auth/models.py` v1 → v2
  (`RBACGlobalRole.TENANT_MANAGER`).
- `requirements/100-SPEC-ARCHITECTURE.md` v12 → v13 (E-100-002 v2).
- `CLAUDE.md` v19 → v20 (§13).
- `ay_platform_core/tests/contract/c2_auth/test_rbac_schema.py` v2 → v3
  (4 rôles attendus).
- `ay_platform_core/tests/unit/c2_auth/test_rbac_models.py` (4 rôles).

## Validation

`ay_platform_core/scripts/run_tests.sh ci` :

```
==> Running ruff check     → ruff: OK
==> Running mypy           → mypy: OK
==> Running pytest         → pytest: OK (985 passed in 134s)
==> All stages OK
```

62 nouveaux tests anonymous_access + 3 coherence = 65 tests nouveaux.
923 tests existants inchangés (post-mise à jour des contrats `test_rbac_*`
qui attendaient 3 rôles).

## Phase 2 — différé prochaine session

Le framework et le catalog sont en place ; les 4 fichiers de tests
restants se branchent dessus avec coverage automatique des 62
endpoints :

- **`test_role_matrix.py`** — pour chaque endpoint ROLE_GATED :
  rôle insuffisant → 403 ; rôle accepté → not 401/403 ; rôle exclu
  (`tenant_manager` sur content) → 403. Auto-paramétré sur le
  catalog × les profils. ~150 tests générés.
- **`test_isolation.py`** — cross-tenant et cross-project : même rôle,
  mauvais X-Tenant-Id ou wrong path project_id → 403/404 (pas de
  leak). Auto-paramétré sur les endpoints scope=tenant/project. ~60
  tests générés.
- **`test_backend_state.py`** — write/delete vérifiés en
  ArangoDB/MinIO : assertion directe sur les collections / buckets
  après l'appel HTTP. Hand-written par type de ressource (~30
  tests, un helper par resource type).
- **`test_auth_modes.py`** — boundary C2 : login flow `local` /
  `entraid` (mock JWKS) / `none`, vérifie que les claims JWT émis
  sont équivalents et que les downstream acceptent les forward-auth
  headers identiquement.

Plus :

- **Body templates** par endpoint pour rendre les tests POST/PUT/PATCH
  plus précis (actuellement `{}` envoyé, peut donner 422 au lieu de
  401/403 — accepté pour anonymous mais imprécis pour role gates).

## Lessons (candidats `/capture-lesson`)

- **Catalog-driven test design** : pour des matrices testant N×M×P
  combinaisons, la source unique de vérité = liste de specs
  pythoniques (frozen dataclass), parametrize les tests dessus, un
  coherence test qui pin "specs ↔ code". Adding a row covers all
  dimensions. Réutilisable pour tout test matriciel à venir.
- **`tenant_manager` separation of duties** : le mécanisme
  `excluded_global_roles` dans l'EndpointSpec laisse la matrice
  enforcer "ce rôle SHALL be rejected" au-delà de "qui SHALL be
  accepted" — deux directions complémentaires. Pattern utile pour
  toute role-based access avec rôles à pouvoir étendu.
- **C9 in-process testing via remote services** : les `Remote*Service`
  prévues pour parler à C5/C6 par HTTP s'enroulent autour d'un
  `httpx.AsyncClient(transport=ASGITransport(app=c5_app))` pour des
  tests in-process — on garde le code production unchanged et on
  obtient un test stack avec C9 plein.
- **`× → x` ruff/RUF003** : utiliser `x` (LATIN SMALL LETTER X) dans
  les commentaires/docstrings. La multiplication sign `×` (U+00D7)
  trigger RUF003 même dans les comments.

## Suite

- **Phase 2** (next session) : 4 fichiers de tests restants +
  body templates pour role_matrix précis.
- **R-100-060** — production K8s manifests (consomme R-100-124).
- **C5 import endpoint** (R-300-080) v2 implem.
- **Q-100-016** — trace dans C15 Jobs (avec C15 sub-agent runtime).

## Rollback

Branche `main` HEAD avant cette session : commit le plus récent
post-Q-100-015. Rollback safe via `git revert` — additif pur sauf
pour l'enum `RBACGlobalRole` (ajout de `TENANT_MANAGER`,
backwards-compatible) et `E-100-002` v1 → v2 (élargissement, pas de
breaking change). Les 2 contract tests modifiés (3 rôles → 4 rôles)
sont la seule trace dans des fichiers existants.
