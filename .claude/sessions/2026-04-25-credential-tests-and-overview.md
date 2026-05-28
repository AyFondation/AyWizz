# Session 2026-04-25 — Credential usability tests + architecture overview

## Trigger

Demande utilisateur : "tests pour vérifier que les comptes admin et app sont bien utilisable" + go pour la suite proposée §5 (audit spec, observability v2, C2 local admin tests, logs JSON + traceparent).

Triage de scope : la suite §5 fait facilement 3-4 sessions. Cette session se concentre sur (a) tests credentials qui couvrent 80 % du point 3 de §5 et adressent la demande explicite, et (b) page d'archi `050-ARCHITECTURE-OVERVIEW.md` qui couvre le point 1 de §5. Logs structurés + traceparent (point 4) et `_observability` v2 (point 2) sont reportés à des sessions dédiées.

## Tests credentials

`tests/integration/_credentials/` — 8 nouveaux tests :

**Arango (`test_arango_ay_app.py`)** — 4 tests :

- Bootstrap fixture replays `arangodb_init` en Python : `sys_db.create_database` + `sys_db.create_user` + `sys_db.update_permission(rw on db + rw on '*' collection)`.
- `test_can_create_collection_and_round_trip_a_document` — connect comme `ay_app`, create coll, insert/get/delete doc.
- `test_can_run_aql_against_granted_database` — AQL avec bind vars sur DB granted.
- `test_cannot_access_a_foreign_database` — sibling DB sans grants → `ArangoServerError` à la première opération privilégiée.
- `test_wrong_password_is_rejected` — mauvais password → erreur immédiate.

**MinIO (`test_minio_ay_app.py`)** — 4 tests :

- Bootstrap fixture utilise `minio.minioadmin.MinioAdmin` (équivalent Python du `mc admin`) : `policy_add` + `user_add` + `policy_set`. La policy autorise `s3:*` sur 4 buckets de test.
- `test_can_round_trip_an_object_in_a_granted_bucket` — `put_object` / `get_object` / `list_objects` / `remove_object`.
- `test_can_access_every_granted_bucket` — `bucket_exists` sur les 4.
- `test_cannot_access_a_foreign_bucket` — bucket hors-policy → `S3Error` code `AccessDenied`.
- `test_wrong_secret_key_is_rejected` — mauvaise clé secrète → erreur (codes acceptés : `SignatureDoesNotMatch` / `InvalidAccessKeyId` / `AccessDenied` selon la version MinIO).

**C2 local admin (`tests/integration/c2_auth/test_local_admin_bootstrap.py`)** — 4 tests :

- `test_admin_user_is_created_from_env` — appel direct de `_ensure_local_admin(repo, cfg)` avec `auth_mode=local` et `local_admin_*` ; vérifie l'enregistrement Arango (username, role admin, hash argon2id ≠ plaintext).
- `test_bootstrap_is_idempotent` — appel ×2 ; même `user_id`, même hash (pas de re-hash).
- `test_no_op_when_auth_mode_is_not_local` — `auth_mode=none` ne bootstrape pas, même quand les creds sont présentes.
- `test_admin_can_login_after_bootstrap` — bootstrap manuel + montage app FastAPI in-process avec router C2 + `dependency_overrides[get_service]` ; POST `/auth/login` correct → 200 + JWT (token_type=bearer) ; mauvais password → 401.

**Détail d'implem nuances** :

- Le 1er essai du test login utilisait `create_app(cfg)` avec lifespan auto-bootstrap, mais `httpx.ASGITransport` n'invoque pas le lifespan. Refactor vers le pattern existant des tests c2 (montage manuel router + service + override `get_service`) — plus rapide à exécuter et aligné avec le reste.
- `MinioAdmin.policy_add` attend un PATH disque (pas un objet Python) ; helper `_write_temp_json` matérialise le doc.
- `python-arango` v8 : la création d'user passe par `sys_db.create_user(username, password, active)` et les grants par `sys_db.update_permission(username, permission, database, collection)`. Distinct de la doc plus ancienne.

**Total** : 12 tests verts (durée 10 s — chaque test reuse le testcontainer session-scoped).

## Architecture overview document

`requirements/050-ARCHITECTURE-OVERVIEW.md` v1, ~150 lignes, 9 sections :

1. **Big picture** — diagramme ASCII 3-tiers (ingress / API+UI / backend).
2. **Python tier en 60 secondes** — 1 image, N processes, COMPONENT_MODULE runtime ; "monorepo de code + microservices d'exécution".
3. **Configuration — un env file, 3 classes de creds** — table récap + bootstrap responsibilities + tests location.
4. **ArangoDB & MinIO — single namespaces** — collection-level / bucket-level isolation.
5. **Resource limits** — caps + tableau budget.
6. **Test-tier observability** — `_observability` endpoints + R-100-121 guard.
7. **Compose stack — entry points** — `e2e_stack.sh` commands.
8. **Where to look next** — table de navigation vers les specs détaillées.
9. **Implemented vs. specified — quick map** — table 11 entrées qui dit pour chaque spec area si c'est `implemented`, `MVP`, `NOT implemented`, etc.

**Intégrations** :

- `CLAUDE.md` §3 (navigation map des specs) : ajout du doc en TÊTE de table avec note "Read first.".
- `CLAUDE.md` §9.3 (session bootstrap reading order) : étape 3 de la séquence reading.
- `CLAUDE.md` v16 → v17.

L'objectif : une session fraîche peut lire **CLAUDE.md → SESSION-STATE.md → 050-ARCHITECTURE-OVERVIEW.md** et avoir tout le contexte nécessaire en moins de 5 min, avant de plonger dans les specs détaillées sur demande.

## Spec / governance deltas

- `requirements/050-ARCHITECTURE-OVERVIEW.md` (nouveau, v1).
- `CLAUDE.md` v16 → v17 (§3 + §9.3 mis à jour).
- `tests/integration/_credentials/__init__.py` (nouveau).
- `tests/integration/_credentials/test_arango_ay_app.py` (nouveau).
- `tests/integration/_credentials/test_minio_ay_app.py` (nouveau).
- `tests/integration/c2_auth/test_local_admin_bootstrap.py` (nouveau).
- `.claude/SESSION-STATE.md` (date + §5 + §6).

## Validation

- 604/604 unit + contract + coherence verts (inchangé).
- 12/12 nouveaux tests integration credentials verts (durée 10 s).
- Page d'archi vérifiée : tous les liens internes valides (`100-SPEC-…`, `200-SPEC-…`, etc. existent dans `requirements/`).

## Lessons (candidats `/capture-lesson`)

- **`MinioAdmin` Python** : équivalent du CLI `mc` pour les opérations admin, dispo dans le SDK `minio>=7.0`. Pas largement documenté mais suffisant pour bootstrapper users + policies en test.
- **`httpx.ASGITransport` n'invoque pas le lifespan FastAPI**. Soit utiliser `asgi_lifespan.LifespanManager` (dépendance externe), soit appeler manuellement le code de lifespan avant le test. Le pattern existant des tests c2 (router + service + dependency_overrides) contourne le problème en évitant `create_app()`.
- **Une page d'archi 1-page accélère vraiment l'onboarding** : la spec 100- fait 1500+ lignes ; un nouveau collaborateur (humain ou agent) y noie. Le doc 050- (~150 lignes) lui donne 90 % du contexte en 5 min puis l'oriente vers le détail. Pattern à reproduire pour les futurs domaines.

## Rollback

Branche `main` HEAD avant les sessions de la journée : commit `f402b71` (`pre-alpha-002`). Rollback global : `git reset --hard f402b71` + `git clean -fd`. À utiliser UNIQUEMENT sur instruction explicite.

## What's next (per SESSION-STATE §5)

1. **Logs structurés JSON + traceparent** (R-100-104, R-100-105) — workstream substantiel à part, ~1h30-2h.
2. **`_observability` v2** (Docker events subscription) — petit, ~30 min.
3. **Audit spec ↔ implém ligne-par-ligne** — session dédiée ; `050-ARCHITECTURE-OVERVIEW.md` §9 donne déjà la version agrégée.
