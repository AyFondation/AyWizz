# Session 2026-04-29 — UX bootstrap config + Next.js frontend scaffold

## Trigger

Backend mature post gap-fill UX. Démarrage du frontend Next.js avec
deux contraintes utilisateur :
1. UX s'auto-configure via API call SANS rebuild Next.js.
2. URL serveur paramétrable SANS rebuild.

→ Pattern runtime-config 2 niveaux : statique JSON (deployment-time
ConfigMap) + dynamique HTTP (server-time env vars).

## Décisions actées

### Phase 1 — Backend `GET /ux/config`

1. **Endpoint sur C2 plutôt que nouveau composant** — C2 sert déjà
   `/auth/config` public, sibling logique. `c2_auth/ux_router.py`
   nouveau, mounted sous `/ux`. Auth.OPEN, no forward-auth.
2. **Schema `UXConfigResponse`** : `api_version`, `auth_mode`,
   `brand` (BrandConfig), `features` (FeatureFlags). 7 fields env-
   tunables sur AuthConfig (`C2_UX_BRAND_*` + `C2_UX_FEATURE_*`).
3. **Catalog auth-matrix entry** Auth.OPEN. Traefik routing `/ux/*`
   → c2-auth ajouté en compose (`routers.yml`) + K8s (`ingressroutes
   .yaml`). AuthGuardMiddleware exempt list pour C2 étendue à
   `/ux/config`.
4. **Whitelist `/ux` dans coherence + audit script** :
   `test_functional_coverage.py` + `audit_functional_coverage.py`
   filtraient les URLs sur `/auth /admin /api/v1` ; ajouté `/ux`.

### Phase 2 — Next.js scaffold

5. **Évolution v0→v3 du scaffold existant** plutôt que rewrite.
   Versions bumpées (user explicit request) : Next.js 15.1→^16.0,
   React ^19.0, TypeScript ^5.8, Tailwind ^4.0, Biome 1.9→^2.0,
   Node engines >=25 (`.nvmrc` + `package.json engines`).
6. **2 niveaux de config** :
   - **Stage 1** static — `public/runtime-config.json`. Mountable
     K8s ConfigMap. `{ apiBaseUrl, publicBaseUrl }`. Empty
     `apiBaseUrl` = relative URLs (same-origin via Traefik prod, dev
     rewrites dev).
   - **Stage 2** dynamic — fetch `<apiBaseUrl>/ux/config` sur C2.
     Brand + feature flags + auth_mode.
7. **`<ConfigProvider>` Client Component** bloque le render tant
   que bootstrap pas complet. `useConfigState()` retourne union
   loading/ready/error pour gérer les 3 cas dans pages.
   `useReadyConfig()` throw pour pages qui sont garanties post-
   bootstrap (login, etc.).
8. **`output: "standalone"` activé** dans next.config.ts (requis
   par Dockerfile.ui multi-stage).
9. **`lib/platform.ts` (v0) supprimé** — bug `mode` vs `auth_mode`,
   remplacé par les 3 nouveaux libs (types, runtimeConfig,
   apiClient).
10. **JWT en localStorage** (v1 trade-off) plutôt que HTTP-only
    cookie. CSRF-protégé via Authorization header. Cookie path =
    v1.5 hardening (server-side `/auth/login` doit alors set le
    cookie).
11. **snake_case dans les types TS** pour matcher le wire format
    Python directement, zéro mapping layer.

### Phase 3 — Dockerfile.ui + K8s

12. **`infra/docker/Dockerfile.ui`** multi-stage Next.js standalone,
    Node 25 alpine, OCI labels. Build context = monorepo root (per
    CLAUDE.md §4.5). USER node (uid 1000) ships in alpine.
13. **`infra/k8s/base/ay_platform_ui/`** : Deployment + Service +
    ConfigMap (runtime-config.json). Mount subPath sur
    `/app/public/runtime-config.json` overlays le fichier shipped
    dans l'image. Trade-off : ConfigMap update ne propage pas
    auto, pod restart requis.
14. **IngressRoute catch-all `/`** ajouté priority 1 → ay-platform-ui
    sans forward-auth. Toutes les API prefixes (priorities 10/50/
    100) match avant ; seuls les paths non-claimed land sur l'UI.
    L'UI gère elle-même les redirects login (lit auth_mode depuis
    /ux/config).
15. **`ci-build-images.yml` v3** : nouveau job `build-ui` parallèle
    à `build-api`. Cache scope distinct (`scope=ui` vs `scope=api`)
    pour ne pas se polluer mutuellement.
16. **`run_k8s_system_tests.sh` v2** : build+load `aywizz-ui:test`,
    attend `ay-platform-ui` Deployment.
17. **Image override dans 2 overlays** : `dev` (newTag latest) +
    `system-test` (newName/newTag local `aywizz-ui:test`).

## Fichiers livrés

**Phase 1 (backend)** — 9 fichiers :
- `c2_auth/{models,config,service,main,ux_router}.py` (3 modifs + 1 NEW)
- `tests/integration/c2_auth/test_ux_config.py` (NEW, 4 tests)
- `tests/e2e/auth_matrix/_catalog.py` (catalog entry)
- `tests/coherence/test_route_catalog.py` (router scan)
- `tests/coherence/test_functional_coverage.py` (whitelist `/ux`)
- `scripts/checks/audit_functional_coverage.py` (whitelist `/ux`)
- `infra/c1_gateway/dynamic/routers.yml` (compose route)
- `infra/k8s/base/c1_gateway/ingressroutes.yaml` (K8s route + catch-all UI)
- 4 env files (.env.example, .env.test, dev/.env, system-test/.env)

**Phase 2 (frontend)** — 9 fichiers + 1 supprimé :
- `ay_platform_ui/package.json` v3 (Next 16 + libs latest, Node 25)
- `ay_platform_ui/biome.json` v2 ($schema)
- `ay_platform_ui/next.config.ts` v2 (rewrites + standalone)
- `ay_platform_ui/.env.example` v2 (clarifie BUILD vs RUNTIME)
- `ay_platform_ui/public/runtime-config.json` (NEW)
- `ay_platform_ui/lib/{types,runtimeConfig,apiClient}.ts` (NEW)
- `ay_platform_ui/app/{layout,page}.tsx` v2
- `ay_platform_ui/app/providers.tsx` (NEW)
- `ay_platform_ui/app/login/page.tsx` (NEW)
- `ay_platform_ui/lib/platform.ts` SUPPRIMÉ

**Phase 3 (infra)** — 8 fichiers :
- `infra/docker/Dockerfile.ui` (NEW)
- `infra/k8s/base/ay_platform_ui/{deployment,service,configmap-runtime,kustomization}.yaml` (NEW)
- `infra/k8s/base/kustomization.yaml` (ajout component)
- `infra/k8s/overlays/{dev,system-test}/kustomization.yaml` (resources + image overrides)
- `.github/workflows/ci-build-images.yml` v3 (build-ui job)
- `ay_platform_core/scripts/run_k8s_system_tests.sh` v2 (UI image build)

**Spec sync** : `requirements/060-IMPLEMENTATION-STATUS.md`
régénéré.

## Tests CI

- Phase 1 : 4 tests intégration + 1 catalog auto-paramétré.
- CI Python : 1196 → **1208 verts** (1196 + 4 + 1 catalog + 7 catalog
  auto = +12).
- L1 K8s : OK sur dev (44 documents, +3 vs 41) et system-test
  (40 documents, +1 vs 39).
- Phase 2 : pas de tests automatisés du frontend en CI Python (le
  frontend a son propre `npm run lint` + `npm run typecheck` mais
  pas wirés au gate principal — ils tourneront à la première
  exécution `npm install` côté développeur).
- Phase 3 : workflow YAML valide. Premier build UI réel = au prochain
  push main (ci-tests success → ci-build-images:build-ui).

## Trajectoire de mise au point

| Itération | Échec | Fix |
|---|---|---|
| 1 | coherence test_route_catalog | router c2_ux_router pas dans `_ROUTERS` | ajouté avec prefix `/ux` |
| 2 | coherence test_functional_coverage | URL `/ux/config` filtrée par whitelist `/auth /admin /api/v1` | étendu à `/ux` |
| 3 | green | — | — |

## Reste à faire post-session

- **Premier `npm install`** côté utilisateur pour résoudre les nouvelles
  versions Next.js 16 etc. — peut révéler des incompatibilités API
  qu'on découvrira à ce moment.
- **system_k8s test extension** : le test `test_basic_smoke.py` n'a
  pas (encore) de scenario qui hit `/ux/config`. À ajouter quand la
  CI L4 tournera et qu'on saura ce qui passe vraiment.
- **Tests frontend** : aucun test automatisé du Next.js scaffold en
  v1. Pour valider le bootstrap il faut soit :
  - Vitest + React Testing Library (preview).
  - Playwright en système-test contre le cluster K8s.
  - Pour l'instant, validation manuelle au premier `npm run dev`.
- **Hardening v1.5** : HTTP-only cookies pour le JWT (côté backend
  + frontend) ; CSRF tokens si form-encoded ; CORS middleware sur
  C2 si UX hosted on different origin.
- **Component-specific lazy-loaded config** : `/ux/config` actuel
  ne contient que les basics (auth_mode, brand, features). Quand
  l'UX aura besoin des LLM models / project list / etc., chaque
  composant aura son propre endpoint `/api/v1/<comp>/...` que l'UX
  hit lazily.

## Suite proposée

Les manifests K8s + Dockerfile.ui sont prêts mais **jamais exécutés**
(kind absent du devcontainer ; CI K8s tournera au prochain push).
Au push :
- `ci-build-images.yml` v3 → push `aywizz-api:latest` ET
  `aywizz-ui:latest` sur GHCR.
- `ci-k8s-validate.yml` → L1+L2+L3+L4 (kind cluster + manifests).

Si tout passe : démarrer l'UX réelle (chat, requirements, file
manager) sur la base scaffold actuelle. Sinon itérer depuis les
logs CI.
