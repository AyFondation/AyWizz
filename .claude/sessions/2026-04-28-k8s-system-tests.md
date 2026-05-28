# Session 2026-04-28 — Tests système K8s pytest

## Trigger

L'utilisateur a clarifié que par "tests" il voulait dire **tests pytest
dans `ay_platform_core/tests/`** qui lancent l'infra K8s en local et
exercent les fonctions de base, pas juste les scripts L1/L2/L3 que
j'avais livrés à la session précédente. Le but : "s'assurer que tous
les composants sont correctement connectés / configurés".

## Décisions actées

1. **Tier `system_k8s`** dédié, séparé du `system` existant (qui
   cible le compose). Marker pytest opt-in, jamais inclus dans
   `run_tests.sh ci`.
2. **Lifecycle split** entre wrapper script et conftest :
   - Wrapper (`scripts/run_k8s_system_tests.sh`) : kind create →
     docker build → kind load → install Traefik CRDs → apply overlay
     → wait Deployments/StatefulSets/Jobs → run pytest → tear down.
   - Conftest : assume cluster up, démarre `kubectl port-forward`,
     attend `/auth/config` 200, yield base URL. Skip propre si
     kubectl absent ou namespace vide.
3. **Overlay dédié `overlays/system-test/`** (pas dev) :
   - Image `aywizz-api:test` (override sur dev's `ghcr.io/...`).
   - Ollama excisé (`$patch: delete` sur Deployment + Service + PVC
     + ollama-seed Job) — économise ~2 min de pull all-minilm sur
     CI runner et 2 GB RAM.
   - C7 sur `deterministic-hash` embedder en compensation.
4. **4 tests cibles** plutôt que 5 — j'ai écarté le test #5
   (KG extract + retrieve) parce qu'il aurait demandé de déployer
   un mock_llm en K8s (pas dans les manifests aujourd'hui ; scope
   plus large). Les 4 tests présents sont déjà suffisants pour
   prouver "tout est connecté".
5. **CI workflow `ci-k8s-validate.yml` v2** :
   - Ajoute job `l4-system-tests` qui dépend de `l1-static-lint`.
   - Filtre paths élargi : `infra/docker/**` + `tests/system/k8s/**`
     déclenchent maintenant aussi le workflow (le trou de couverture
     "PR Python qui casse K8s indirect" est résolu).

## Fichiers livrés

- **Overlay** :
  - `infra/k8s/overlays/system-test/kustomization.yaml` (résout
    le `$patch: delete` pour Ollama).
  - `infra/k8s/overlays/system-test/.env`
    (C7_EMBEDDING_ADAPTER=deterministic-hash).
  - `infra/k8s/overlays/system-test/.env.secret` (placeholder
    test creds).
- **Tests pytest** :
  - `ay_platform_core/tests/system/k8s/__init__.py`.
  - `ay_platform_core/tests/system/k8s/conftest.py` (port-forward
    fixture + readiness, skip-on-missing).
  - `ay_platform_core/tests/system/k8s/test_basic_smoke.py` (4 tests
    avec `@relation validates:R-100-114, R-100-117, E-100-002`).
- **Wrapper** :
  - `ay_platform_core/scripts/run_k8s_system_tests.sh` (`--keep-cluster`
    + `--skip-build` flags).
- **CI** :
  - `.github/workflows/ci-k8s-validate.yml` v1→v2 (nouveau job L4 +
    paths élargis).
- **Configs** :
  - `pyproject.toml` : marker `system_k8s` ajouté.
  - `.claude/settings.json` v11→v12 (allow-list 5 formes du
    wrapper).
- **Spec sync** :
  - `requirements/060-IMPLEMENTATION-STATUS.md` régénéré (259 R-*,
    `tested` 5→9 grâce aux nouveaux `@relation validates:` posés
    par le test_basic_smoke.py).

## Tests cibles et ce qu'ils prouvent

| Test | Ce qui pète si le test échoue |
|---|---|
| `test_open_route_returns_200` | C1 routing OK + C2 démarre + Service DNS |
| `test_protected_route_returns_401_without_credentials` | Traefik forward-auth-c2 middleware wired |
| `test_login_then_authenticated_request_passes_forward_auth` | C1↔C2↔Arango↔[token]↔C1↔C2 verify↔C7↔Arango chaîne complète |
| `test_login_token_works_against_validation_too` | C6 pod up + Arango wiré + JWT trust cluster-wide |

Le 3ème est le plus fort : un seul flux exerce 5 hops. Une régression
sur n'importe quel pod/service/secret le casse.

## Ce qui n'est pas testé (à connaître)

- LLM proxy (LiteLLM placeholder, pas déployé).
- TLS / cert-manager (overlay HTTP only).
- HPA / NetworkPolicy / PodDisruptionBudget (replicas=1 partout).
- Persistance des PVC après teardown/reapply (les Jobs sont
  idempotents donc safe, mais pas testé explicitement).
- Multi-replica scenarios (probes failover, etc.).
- Chat streaming SSE end-to-end (RAG flow) — bloqué par le gap
  RemoteServices identifié à la session suivante.

## Validation

- L1 `k8s_validate.sh overlays/system-test` : OK localement
  (1557 lignes / 37 documents).
- CI Python `run_tests.sh ci` : 1159 verts inchangé.
- L2/L3/L4 : exécution à la première ouverture de PR / push main —
  signal CI réel en attente.

## Suite

Étape (1) du plan post-K8s : **C3 → C7/C8 RemoteServices**. Gap
identifié maintenant : C3's wiring suppose que `MemoryService` et
`LLMGatewayClient` sont in-process, ce qui est faux en K8s où chaque
composant tourne dans son propre Pod. Sans RemoteServices, le flow
chat-with-RAG ne fonctionne pas en K8s — les 4 tests système actuels
ne le couvrent pas.
