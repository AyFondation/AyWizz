# Session 2026-04-28 — Gap-fill : functional coverage 100% du catalog

## Trigger

Question utilisateur en début de session : "Tu me confirmes qu'il y a
bien des tests e2e permettant de vérifier le bon fonctionnement de
tous les call API FastAPI ?".

Réponse honnête : auth-matrix exhaustive sur les 5 dimensions auth
(anonymous, role gate, isolation, backend-state, auth-modes) pour
les 72 endpoints du catalog, mais le **comportement métier** (le
"call API fonctionne pour de vrai") n'est garanti que pour les
endpoints qui ont aussi un test fonctionnel. Pas de mesure de ce
gap.

Plan validé par l'utilisateur : "On fait tout ce que tu as proposé
dans l'ordre proposé, mais pour la partie K8s j'ai besoin de
spécifier des choses, demande-moi à ce moment-là et sans la partie
Next.js que l'on fera à part". Donc gap-fill d'abord, puis F.2,
Ryuk, K8s (en demandant), pas de Next.js cette fois.

## Décisions actées

1. **Audit script reproductible** plutôt qu'un one-shot manuel.
   `scripts/checks/audit_functional_coverage.py` est versionné et
   peut être relancé à chaque ajout d'endpoint.
2. **Définition opérationnelle de "functional coverage"** : un
   endpoint est "functional-tested" ssi au moins UN fichier de test
   en dehors de `tests/e2e/auth_matrix/` réfère à son path (extraction
   regex de littéraux URL, segment-by-segment match avec
   `{placeholder}` matching anything) ET contient la méthode HTTP
   correspondante (`.{method}(` ou `.request(`). C'est conservateur
   (peut produire des faux négatifs si un test bizarrement construit
   est non détecté), mais zéro faux positif. Bug-conservative.
3. **Coherence test pour pinner l'invariant** : ajouter une
   `EndpointSpec` au catalog SHALL forcer l'ajout d'un test
   fonctionnel ailleurs — sinon `test_functional_coverage` échoue
   en CI. Ce safeguard est l'analogue de `test_route_catalog` pour
   la dimension fonctionnelle (catalog ↔ tests fonctionnels), tandis
   que `test_route_catalog` pin catalog ↔ code.
4. **Smoke tests pour les 4 endpoints "stub-by-design"**. C6 health,
   C7 health, C5 entity versions (501 stub), C7 memory refresh
   (501 stub) ont un contrat trivial mais OBSERVABLE par le client.
   Un smoke test minimal pin ce contrat (200/{ok} ou 501/{detail}),
   sinon "fixed the stub by returning 200" peut shipper en silence.
5. **Test E2E pour C2 DELETE project** — le seul gap fonctionnel
   réel (5e gap initial). DELETE avec cascade des grants member
   (par re-création du project_id et inspection des claims du JWT
   du membre) est non-trivial et mérite un test dédié.

## Fichiers livrés

- `scripts/checks/audit_functional_coverage.py` (NEW v1) — audit
  script. URL extraction regex + segment-by-segment match avec
  placeholder. Flags `--summary-only` et `--auth-only`. Output
  classifie chaque endpoint en "functional" / "auth-only".
- `tests/integration/c2_auth/test_tenant_project_lifecycle.py`
  (EXTENDED) — nouveau test
  `test_delete_project_cascades_member_grants_and_404s_on_re_delete`.
  tenant_manager crée 2 tenants, admin grant project_editor à un
  user, cross-tenant DELETE → 404 (pas de leak), real DELETE → 204,
  re-DELETE → 404, GET projects ne contient plus le projet, et
  cascade vérifié par re-création du project_id + inspection des
  claims du JWT du membre (grant supprimé).
- `tests/integration/_smoke/__init__.py` (NEW, empty).
- `tests/integration/_smoke/test_v1_contract_pin.py` (NEW v1) —
  4 smoke tests : C6 health 200/ok, C7 health 200/ok, C5 entity
  versions 501/detail, C7 memory refresh status 501/detail. 3 mini
  apps (c5_app, c6_app, c7_app) avec testcontainers Arango+MinIO
  partagés.
- `tests/coherence/test_functional_coverage.py` (NEW v1) —
  coherence test qui pin l'invariant "tout EndpointSpec a au moins
  un test fonctionnel hors auth_matrix". Inline la logique de
  matching de l'audit script. Échoue avec un message listant
  méthode + path + composant des endpoints non couverts.

## Trajectoire de l'audit (4 itérations)

| Itération | Méthode de matching | Faux positifs |
|---|---|---|
| 1 | Substring `path in text` | beaucoup (tout test C5 matchait `/api/v1/projects/`) |
| 2 | Regex compilée du path | 28 auth-only (regex cassé sur `/`) |
| 3 | Extraction URL + segment match | 5 auth-only (vrais gaps) |
| 4 | + query-string strip | 5 auth-only (stable) |

Final : 67/72 → **72/72** après livraison des 5 gap-fill tests.

## Tests CI

- 1152 → 1153 (le coherence test).
- `run_tests.sh ci` : ruff OK / mypy OK / pytest 1153 passed in 137s.

## Ce qui reste

- Aucun gap fonctionnel sur le catalog actuel. L'invariant est pinné
  par CI.
- Suite plan post-v1 : **F.2 hybrid retrieval** (graph traversal lors
  du retrieve, consommation du graphe peuplé par F.1) → Ryuk
  devcontainer → K8s manifests (avec questions à l'utilisateur).
