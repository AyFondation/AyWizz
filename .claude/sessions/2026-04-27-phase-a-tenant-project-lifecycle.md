# Session 2026-04-27 — Phase A v1 plan : Tenant + Project lifecycle

## Trigger

Premier coup de pioche du **plan v1 fonctionnel** (6 phases, ~8-10
sessions au total) validé par l'utilisateur. Phase A = endpoints
tenant/project/member-grant absents avant cette session ; aucune façon
réelle de matérialiser un tenant ou un projet via API.

## Scope livré

### 8 nouveaux endpoints (E-100-002 v2)

**Tenant lifecycle** (admin_router, mounted `/admin`, `tenant_manager`
strict — content-blind) :

- `POST /admin/tenants` (201) — `tenant_manager`
- `GET /admin/tenants` (200) — `tenant_manager`
- `DELETE /admin/tenants/{tenant_id}` (204) — `tenant_manager`

**Project lifecycle** (projects_router, mounted `/api/v1/projects`,
forward-auth headers) :

- `POST /api/v1/projects` (201) — `admin` / `tenant_admin`,
  `tenant_manager` exclu
- `GET /api/v1/projects` (200) — any authenticated user (filtré par
  X-Tenant-Id), `tenant_manager` exclu
- `DELETE /api/v1/projects/{project_id}` (204) — `admin` /
  `tenant_admin`, `tenant_manager` exclu

**Project membership** (projects_router) :

- `POST /api/v1/projects/{project_id}/members/{user_id}` (204) —
  `admin` / `tenant_admin` / `project_owner`, `tenant_manager` exclu
- `DELETE /api/v1/projects/{project_id}/members/{user_id}` (204) —
  même

### Backbone code

- `c2_auth/models.py` v2→v3 : `TenantCreate`, `TenantPublic`,
  `TenantList`, `ProjectCreate`, `ProjectPublic`, `ProjectList`,
  `ProjectMemberGrant`.
- `c2_auth/db/repository.py` v1→v2 :
  - Nouvelle collection `c2_projects` (créée par
    `_ensure_collections_sync`).
  - 11 nouvelles méthodes async : `insert/get/list/delete_tenant`,
    `insert/get/list/delete_project`, `grant/revoke_project_role`.
  - `delete_project` cascade les `c2_role_assignments` du projet
    (sinon grants stales survivent un re-create).
- `c2_auth/service.py` : 8 nouvelles méthodes
  (`create_tenant`/`list_tenants`/`delete_tenant`,
  `create_project`/`list_projects`/`delete_project`,
  `grant_project_member`/`revoke_project_member`). Validations métier :
  duplicate key → 409, tenant absent → 404 sur create_project,
  cross-tenant project lookup → 404 (pas de leak), grant user
  cross-tenant → 400.
- `c2_auth/admin_router.py` v1 (nouveau, 80 LoC) — `tenant_manager`
  required via Bearer JWT.
- `c2_auth/projects_router.py` v1 (nouveau, 145 LoC) — forward-auth
  headers ; `_require_role_intersect` rejette `tenant_manager`
  explicitement avec un message E-100-002 v2 référencé.
- `c2_auth/main.py` v2→v3 : monte les 2 nouveaux routers en plus de
  `/auth`.

### Tests

- **Auto-paramétrés** sur le catalog (anonymous + role_matrix +
  isolation) : les 8 nouveaux endpoints sont automatiquement couverts
  par les fichiers existants — `test_anonymous_access` (8 tests),
  `test_role_matrix` (8 insufficient + 8 accepted, dont 6 avec
  `tenant_manager` exclu = 6 tests excluded_tenant_manager),
  `test_isolation` (item endpoints scope=project).
  Nouveaux passes auto : **27 tests sur les 8 endpoints**.
- **Dirigés** : nouveau fichier
  [`tests/integration/c2_auth/test_tenant_project_lifecycle.py`](ay_platform_core/tests/integration/c2_auth/test_tenant_project_lifecycle.py)
  — 6 tests round-trip :
  1. tenant_manager crée/liste/supprime un tenant ; re-create → 409 ;
     re-delete → 404.
  2. admin (sans tenant_manager) refusé sur POST /admin/tenants → 403.
  3. **End-to-end full lifecycle** : tenant_manager crée tenant →
     admin (forward-auth) crée projet → admin grant project_editor à
     un user → user login (issue_token) → JWT contient
     `project_scopes={project_id: ["project_editor"]}`. Boucle
     fermée : la grant DB se reflète dans la JWT au prochain login.
  4. `test_project_listing_filtered_by_tenant` — tenant_a et tenant_b
     ont chacun un projet ; chaque admin ne voit QUE son tenant.
  5. `test_tenant_manager_cannot_list_tenant_projects` — la
     content-blindness E-100-002 v2 est enforcée même sur le GET
     authenticated-only (interdit tenant_manager).
  6. `test_grant_user_in_other_tenant_returns_400` — tenter de
     binder un user de tenant_b sur un projet de tenant_a → 400.

### Catalog + doc

- `tests/e2e/auth_matrix/_catalog.py` : 62 → **70 endpoints**.
- `tests/coherence/test_route_catalog.py` : nouveaux routers
  enregistrés (admin_router avec prefix `/admin`, projects_router
  avec prefix `/api/v1/projects`).
- `requirements/065-TEST-MATRIX.md` régénéré (70 endpoints documentés,
  6 lignes nouvelles avec `excluded: tenant_manager`).
- `tests/e2e/auth_matrix/_clients.py` `needs_bearer()` mis à jour pour
  router les endpoints `/api/v1/projects/*` vers les forward-auth
  headers (pas Bearer).

### pyproject.toml

- Étendu `[tool.ruff.lint.per-file-ignores]` avec
  `"**/*_router.py" = ["B008"]` pour couvrir `admin_router.py` et
  `projects_router.py` sans devoir les renommer.

## Validation

`ay_platform_core/scripts/run_tests.sh ci` :

```
==> Running ruff check     → ruff: OK
==> Running mypy           → mypy: OK
==> Running pytest         → pytest: OK (1121 passed in 137s)
==> All stages OK
```

**+37 tests** depuis hier (1084 → 1121). Conteneurs orphelins
post-CI : 0 (le wrapper docker_test_cleanup.sh n'a pas été
re-déclenché — pas de SIGKILL pendant cette session).

## Files modifiés / créés

Créés :
- `ay_platform_core/src/ay_platform_core/c2_auth/admin_router.py` v1
- `ay_platform_core/src/ay_platform_core/c2_auth/projects_router.py` v1
- `ay_platform_core/tests/integration/c2_auth/test_tenant_project_lifecycle.py` v1

Modifiés :
- `ay_platform_core/src/ay_platform_core/c2_auth/models.py` v2→v3
- `ay_platform_core/src/ay_platform_core/c2_auth/db/repository.py` v1→v2
- `ay_platform_core/src/ay_platform_core/c2_auth/service.py` (+ 8 méthodes)
- `ay_platform_core/src/ay_platform_core/c2_auth/main.py` v2→v3
- `ay_platform_core/tests/e2e/auth_matrix/_catalog.py` (+8 EndpointSpec)
- `ay_platform_core/tests/e2e/auth_matrix/_clients.py` (`needs_bearer`)
- `ay_platform_core/tests/e2e/auth_matrix/_stack.py` (mount routers)
- `ay_platform_core/tests/coherence/test_route_catalog.py` (register routers)
- `ay_platform_core/pyproject.toml` (per-file-ignores)
- `requirements/065-TEST-MATRIX.md` (auto-generated, 70 endpoints)

## Décisions actées

- **Project CRUD dans C2** (pas nouveau composant) — C2 est propriétaire
  RBAC, naturel pour project lifecycle qui EST un access-control
  resource.
- **`/api/v1/projects` en forward-auth** (pas Bearer JWT) — alignement
  avec C3/C5/C6/C7 ; le `/admin/*` reste en Bearer (admin surface).
- **Pas d'endpoint explicite "grant tenant_admin"** — `PATCH
  /auth/users/{uid}` modifie déjà le champ `roles` ; redondant.
  Documenté dans la session précédente.
- **`tenant_manager` exclu de TOUTES les opérations content-touching** :
  même `GET /api/v1/projects` (lecture passive de la liste) le rejette.
  Stricte content-blindness E-100-002 v2.
- **Cross-tenant DELETE/GET project → 404** (pas 403) pour ne pas
  leaker l'existence du projet.
- **`delete_project` cascade les role_assignments** — sinon une
  re-création de projet hériterait des grants stales.

## Lessons (candidats `/capture-lesson`)

- **Pattern multi-router pour un composant** : C2 a maintenant 3
  routers (`router.py` auth flow, `admin_router.py` tenant lifecycle,
  `projects_router.py` projects). Chacun monté avec son propre
  prefix dans le main.py app factory. Coherence test enregistre les
  3 séparément. Pattern réutilisable quand un composant accumule des
  surfaces avec des modèles d'auth différents.
- **B008 ruff per-file-ignores pour FastAPI** : le pattern existant
  `**/router.py` ne match PAS `*_router.py` (filename-only glob).
  Étendre avec `**/*_router.py` pour couvrir les variantes
  `admin_router.py` / `projects_router.py` / etc.
- **`needs_bearer()` dispatch** : un composant qui mixte Bearer JWT
  (admin endpoints) et forward-auth headers (downstream-style) sur
  des endpoints différents nécessite que la matrice de test sache
  lequel choisir par path. Pattern : function de dispatch path-based
  dans `_clients.py`, pas dans le catalog.

## Suite (Phase B-F restantes)

Plan v1 fonctionnel — état à fin Phase A :

- ✅ **Phase A** — Tenant + Project lifecycle (livrée)
- ⏳ **Phase C** — Embeddings réels via Ollama (~1 session)
- ⏳ **Phase B** — Upload + parsers PDF/MD/HTML (~2 sessions)
- ⏳ **Phase D** — Chat-with-RAG dans C3 (~2 sessions)
- ⏳ **Phase E** — Conversation → memory loop (~1 session)
- ⏳ **Phase F** — Knowledge graph extraction F.1 only (~1-2 sessions)

Prochaine session naturelle : **Phase C** — switch
`C7_EMBEDDING_ADAPTER=ollama` par défaut, validation retrieval réelle
sur tests integration C7.

## Rollback

Branche `main` HEAD avant cette session : commit le plus récent
post-Phase 2 auth-matrix. Rollback safe via `git revert` — additif pur
(2 nouveaux modules + 1 fichier test + extensions models/service/repo).
La nouvelle collection `c2_projects` est créée à la volée par
`_ensure_collections_sync` lors du premier startup ; aucune migration
de données existante.
