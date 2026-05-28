# Session 2026-04-27 — Q-100-015 résolu (adapter layer Loki + ES)

## Trigger

Suite §5 du SESSION-STATE — Q-100-015 (workflow synthesis sur log store
externe) priorisé après le CLAUDE.md v19 §12 cleanup. Les sous-questions
"sampling/retention" et "dashboard layer" ont été externalisées
(Q-100-017 / Q-100-018) — elles relèvent du déploiement, pas de la
couche adapter elle-même.

## Décisions de cadrage

User validations preliminary :

1. **Ship les DEUX adapters (Loki + Elasticsearch)** — pas de pari sur
   un seul écosystème K8s ; coût marginal faible (~150 LoC chacun).
2. **Librairie montable** plutôt que nouveau composant `c_obs_prod` —
   `make_workflow_router(source)` retourne un `APIRouter` que n'importe
   quelle FastAPI app (test ou prod) peut monter.
3. **Integration testcontainers requis** — pas que des unit tests
   `httpx.MockTransport`, mais aussi des tests end-to-end contre du
   vrai Loki et du vrai Elasticsearch.

## R-100-124 — Production Workflow Synthesis Service

Nouvelle requirement formalisant :

- **`SpanSource` Protocol** (async `fetch_for_trace`,
  `fetch_recent`, `aclose`) — `runtime_checkable`, storage-agnostic.
- **3 adapters concrets** :
  - `BufferSpanSource` — wrap le `LogRingBuffer` de `_observability`
    (test-tier).
  - `LokiSpanSource` — `GET /loki/api/v1/query_range` avec pipeline
    `| json | event="span_summary"` (whitespace-tolerant — voir
    "écueils" plus bas).
  - `ElasticsearchSpanSource` — `POST /<index>/_search` avec
    `bool/filter` sur `event=span_summary` + filter optionnel
    `trace_id.keyword` ; Basic Auth optionnelle.
- **`make_workflow_router(source)`** — FastAPI `APIRouter` avec
  `GET /workflows/{trace_id}` (404 vide / 400 malformed) et
  `GET /workflows?recent=N`. Identique pour test-tier et prod.
- **`WorkflowSourceSettings`** (Pydantic, `env_prefix="obs_"`) —
  `OBS_SPAN_SOURCE` ∈ {buffer, loki, elasticsearch}, URL/credentials
  par backend, `OBS_QUERY_WINDOW_HOURS`, `OBS_FETCH_LIMIT`,
  `OBS_REQUEST_TIMEOUT_SECONDS`.
- **`create_span_source(settings, buffer=, client=)`** factory —
  dispatch sur `settings.span_source`, `ValueError` si `buffer`
  demandé sans buffer fourni.

`_observability/main.py` v2 — réutilise `make_workflow_router` avec
`BufferSpanSource(buffer)`. Wire shape inchangé ; un seul code path
pour la synthèse.

## Files

Nouveau module production-tier (sans underscore prefix, R-100-121
respecté) :

- `ay_platform_core/src/ay_platform_core/observability/workflow/__init__.py` —
  surface publique.
- `ay_platform_core/src/ay_platform_core/observability/workflow/config.py` —
  `WorkflowSourceSettings`.
- `ay_platform_core/src/ay_platform_core/observability/workflow/sources.py` —
  `SpanSource` Protocol + 3 adapters + factory.
- `ay_platform_core/src/ay_platform_core/observability/workflow/router.py` —
  `make_workflow_router`.

Refactor :

- `_observability/synthesis.py` v2 — `span_from_dict(obj)` exporté
  (parse path partagé : Loki passe par `parse_span_summary`, ES
  appelle directement `span_from_dict` sur les `_source` documents).
- `_observability/main.py` v2 — délègue `/workflows*` au router
  partagé.

Tests (33 unit + 6 integration nouveaux) :

- `tests/unit/observability/workflow/_fixtures.py` — helpers
  `make_span_summary(_line)`.
- `tests/unit/observability/workflow/test_sources_loki.py` —
  `httpx.MockTransport`, monkeypatch `_now_utc` pour bornes
  temporelles déterministes.
- `tests/unit/observability/workflow/test_sources_es.py` — pareil
  pour ES, plus auth + lifecycle.
- `tests/unit/observability/workflow/test_sources_buffer_and_factory.py` —
  BufferSpanSource + dispatch factory + Settings env precedence.
- `tests/unit/observability/workflow/test_router.py` — mounts the
  router on a stub source, exercises the HTTP surface.
- `tests/fixtures/observability_containers.py` — session-scoped
  Loki + ES testcontainers fixtures (avec `LogMessageWaitStrategy`
  non-déprécié).
- `tests/integration/observability/workflow/test_loki_integration.py` —
  push via `/loki/api/v1/push`, poll until queryable, fetch via
  adapter, assert envelope.
- `tests/integration/observability/workflow/test_elasticsearch_integration.py` —
  bulk index `?refresh=wait_for`, fetch, assert envelope (incl.
  router 200 sur trace error → verdict=error).

Spec :

- `requirements/100-SPEC-ARCHITECTURE.md` v11 → v12 — nouvelle
  R-100-124, Q-100-015 **resolved**, Q-100-017 (sampling/retention)
  + Q-100-018 (dashboard) ouvertes.
- `requirements/060-IMPLEMENTATION-STATUS.md` — regenerated, 259
  R-* indexés, R-100-124 → **tested**.

Env files :

- `.env.example` + `ay_platform_core/tests/.env.test` — 10 nouvelles
  variables `OBS_SPAN_SOURCE` / `OBS_LOKI_*` / `OBS_ELASTICSEARCH_*` /
  `OBS_QUERY_WINDOW_HOURS` / `OBS_REQUEST_TIMEOUT_SECONDS` /
  `OBS_FETCH_LIMIT`. Test stack reste sur `SPAN_SOURCE=buffer` (les
  champs Loki/ES documentent les défauts prod, inertes en mode buffer).

## Écueils techniques

### LogQL whitespace tolerance

Première implémentation utilisait un substring filter
`|= "\"event\":\"span_summary\""`. Échec en integration test : les
lignes pushées via `_span_summary()` (helper test) sont produites par
`json.dumps()` qui par défaut sépare clé/valeur par `": "` (espace
après le colon). Le filter substring-exact ne matche pas. **Pareil
en production** — `observability/formatter.py` utilise aussi
`json.dumps(payload, ensure_ascii=False, default=str)` (séparateurs
par défaut).

Fix : pipeline LogQL `| json | event="span_summary"` — le parser JSON
de Loki extrait les fields structurés et les compare en équivalence,
ce qui est whitespace-insensible. C'est aussi plus idiomatique LogQL.

### testcontainers `wait_for_logs` déprécié

L'ancien helper `wait_for_logs(container, "started", timeout=180)`
warning DeprecationWarning, traité comme erreur par
`filterwarnings = ["error"]` dans pyproject. Switch vers la nouvelle
API structurée :

```python
.waiting_for(LogMessageWaitStrategy("started").with_startup_timeout(180))
```

### `httpx.AsyncClient.post(auth=None)` rejected by mypy

`auth` paramètre attend `tuple[str|bytes, str|bytes] | Callable | Auth |
UseClientDefault`, pas `None`. Fix : skip le paramètre quand l'auth
n'est pas configurée (deux branches dans `_search`).

## Validation

`ay_platform_core/scripts/run_tests.sh ci` :

```
==> Running ruff check     → ruff: OK
==> Running mypy           → mypy: OK
==> Running pytest         → pytest: OK (919 passed in 127s)
==> All stages OK
```

Coverage globale 90.23% (gate 80%). Le module nouveau
`observability/workflow/sources.py` est à 87.5% (les branches
non-couvertes sont les paths d'erreur / edge cases inertes de
`_parse_*_response` quand l'upstream renvoie un payload mal
formé — défensif).

## Spec / governance deltas

- `requirements/100-SPEC-ARCHITECTURE.md` v11 → v12 (R-100-124,
  Q-100-015 resolved, Q-100-017/018 ouverts).
- `requirements/060-IMPLEMENTATION-STATUS.md` regenerated.
- `ay_platform_core/src/ay_platform_core/observability/workflow/`
  (nouveau, 4 fichiers v1).
- `ay_platform_core/src/ay_platform_core/_observability/synthesis.py`
  v1 → v2 (`span_from_dict` exposé).
- `ay_platform_core/src/ay_platform_core/_observability/main.py` v1
  → v2 (délègue au router partagé).
- `ay_platform_core/tests/conftest.py` v3 → v4 (registers
  observability_containers fixtures globally).
- `ay_platform_core/tests/fixtures/observability_containers.py` v1
  (Loki + ES session-scoped fixtures).
- 5 nouveaux fichiers de tests (4 unit + 2 integration) +
  `_fixtures.py` partagé.
- `.env.example` + `ay_platform_core/tests/.env.test` (10 nouvelles
  vars OBS_* documentées).

## Lessons (candidats `/capture-lesson`)

- **LogQL substring filters et JSON whitespace** : `json.dumps()`
  par défaut écrit `": "` (espace) ; un substring filter
  `|= "\"key\":\"val\""` ne matche pas. Toujours utiliser
  `| json | key="val"` quand on sait qu'on parse du JSON
  structuré côté upstream. Plus robuste, plus idiomatique.
- **testcontainers wait strategies modern API** :
  `wait_for_logs(container, "x")` → DeprecationWarning →
  `pyproject` filterwarnings=error → CI fail. Utiliser
  `LogMessageWaitStrategy(...)` via `container.waiting_for(...)`.
- **mypy + httpx auth=None** : passer `None` au paramètre `auth`
  d'une méthode httpx ne compile pas. Brancher sur la valeur
  pour ne pas passer le kwarg quand l'auth est désactivée.
- **`SpanSource` Protocol vs ABC** : Protocol + `runtime_checkable`
  permet aux adapters concrets d'être de simples classes sans
  hériter formellement, et permet l'`isinstance(x, SpanSource)`
  utile en factory. Choix par défaut pour les interfaces
  storage-agnostic.

## Suite

- **Q-100-016** — trace propagation dans C15 Jobs (avec C15
  sub-agent runtime).
- **Q-100-017** — sampling rate + rétention en prod (déploiement K8s).
- **Q-100-018** — dashboard Grafana ou UI dédiée (différé).
- **R-100-060** — production K8s manifests (Deployment / Service /
  Ingress pour le workflow synthesis service avec
  `OBS_SPAN_SOURCE=loki` ou `elasticsearch`).
- **SESSION-STATE.md trim** — proche de la limite 150 lignes ;
  archivage des entrées 2026-04-23/24/25 au prochain ajout.

## Rollback

Branche `main` HEAD avant cette session : commit le plus récent
post-CLAUDE.md v19. Rollback safe via `git revert` — additif pur
(nouveau module + nouveaux tests + nouveaux R-100-124 / Q-100-017 /
Q-100-018) ; le seul refactor existant
(`_observability/main.py` délégation au router) est wire-compatible
(URL paths inchangés, shape JSON identique).
