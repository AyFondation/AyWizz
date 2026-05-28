# Session 2026-04-29 — UX validation pipeline + URL preservation cross-reauth

## Trigger

Suite Phase 4a (auth-aware shell livrée). Deux objectifs combinés :
(1) feature UX produit : préserver l'URL/state quand le token de
session est perdu, l'utilisateur doit pouvoir se reloguer et
revenir sur la page où il était comme si rien ne s'était passé ;
(2) valider end-to-end la pipeline UX (lint + typecheck + Vitest +
Playwright) sur l'environnement dev container après rebuild.

## Décisions actées

1. **`?redirect=<path>` round-trip** — quand `ProtectedLayout`
   bounce un anonymous user, il appelle
   `/login?redirect=<encodeURIComponent(pathname+search)>`. La
   `LoginPage` lit le param, le passe par `sanitizeRedirect()`, et
   l'utilise comme destination post-login (et comme bounce
   already-auth). Default fallback `/dashboard` si null/sanitized-out.
2. **`sanitizeRedirect()` anti open-redirect** — accepte uniquement
   les paths commençant par UN `/` non suivi de `/` ou `\`. Rejette :
   null/undefined/non-string, paths sans `/` initial, protocol-
   relative `//evil.com`, schemes `http://`/`javascript:`/`data:`,
   et le quirk legacy `/\evil` (certains parsers IE/Edge le
   résolvaient cross-origin).
3. **Watchdog 60s sur exp côté client** — `<AuthProvider>` v2 ajoute
   un `setInterval` qui ré-évalue `isTokenExpired(claims)` toutes
   les 60s sur l'état `authenticated`. À expiration : drop vers
   `anonymous`, ProtectedLayout détecte, redirect avec `?redirect=`.
   Évite d'attendre le prochain 401 backend.
4. **ProtectedLayout v3 gate aussi sur config** — bug réel surfacé
   par les tests : AuthProvider hydrate sync depuis localStorage,
   ConfigProvider fetch async ; race window où auth=`authenticated`
   pendant que config=`loading` faisait crasher Navbar/Dashboard
   sur `useReadyConfig: bootstrap not complete`. Layout v3 affiche
   "Loading…" tant qu'au moins un des deux n'est pas ready.
5. **Composants défensifs Navbar/Dashboard v2** — passent à
   `useConfigState()` (qui ne throw pas) au lieu de
   `useReadyConfig()`. Belt-and-braces : layout gate déjà en
   production, mais les tests qui montent ces composants hors
   layout n'explosent plus.
6. **Bake+symlink Docker pattern pour deps Node** — Dockerfile
   v1.8.0 `COPY ay_platform_ui/package*.json /opt/ui-deps/` puis
   `npm install` à cet emplacement (HORS bind-mount workspace).
   `devcontainer.json` v7 `postStartCommand` symlinks
   `${localWorkspaceFolder}/ay_platform_ui/node_modules` vers
   `/opt/ui-deps/.../node_modules` UNIQUEMENT si le host n'a pas de
   `node_modules` réel (host wins). `chown` au `postCreateCommand`
   après `updateRemoteUserUID`. Mirror fonctionnel du pattern Python
   v1.6.0 (`pip install -e` avec `.pth`).
7. **Playwright Chromium pré-baked** — Dockerfile v1.9.0 ajoute
   `ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers` puis
   `npx playwright install --with-deps chromium`. ~250 MB sur
   l'image, mais zéro téléchargement au premier `npm run test:e2e`.
8. **`.dockerignore` v1** — denylist couvrant VCS/IDE/`.claude`,
   specs (`requirements`, `infra`, `*.md`), `.devcontainer`,
   `.env*`, caches Python, artefacts Node. Le build context du
   Dockerfile ne COPY que 3 paths ; tout le reste est gaspillage.
9. **Turbopack incompatible avec symlink hors-projet** —
   découverte : `next dev --turbo` panic
   `Symlink [project]/node_modules is invalid, it points out of
   the filesystem root`. Workaround : `next dev --webpack` dans le
   `dev` script (package.json v6). Webpack suit les symlinks
   correctement. Vitest, tsc, et Playwright ne sont pas affectés.
   Trade-off : HMR webpack plus lent que Turbopack mais
   fonctionnel ; perf dev acceptable. Long-term decision : Q-100-019
   ouverte (accepter webpack permanent vs basculer vers `npm
   install` au postCreate pour récupérer Turbopack).
10. **Biome `tailwindDirectives: true`** — par défaut Biome 2.4
    refuse `@theme`/`@apply` (Tailwind v4 syntax). Activé dans
    `biome.json` pour que `npm run format` ne plante pas sur
    `app/globals.css`.
11. **Discipline tests : 3 categories de défaillances corrigées
    correctement (CLAUDE.md §10.3)** :
    - **A. implementation defect** : ProtectedLayout race auth/config
      → fix code (gate v3).
    - **B. test defects** :
      - "loading" placeholder test : useEffect sync flush l'état
        transient, non observable → réécrit avec `vi.doMock` pour
        tester la branche en isolation.
      - watchdog 60s : `vi.useFakeTimers()` était appelé APRÈS
        registration du `setInterval` → l'interval était dans la
        queue réelle, jamais avancée. Fake timers déplacés AVANT
        render.
      - E2E `getByRole("alert")` matchait le `__next-route-announcer__`
        injecté par Next.js 16 → scope au formulaire
        (`page.locator("form").getByRole("alert")`).
      - E2E `getByText("tenant-test")` matchait navbar+claims-panel
        → `{ exact: true }`.
      - E2E logout assertion `toHaveURL("/login")` → regex
        `/login(\?.*)?$/` (logout depuis /dashboard inclut maintenant
        `?redirect=%2Fdashboard`).

## Fichiers livrés / modifiés

**Source** :
- `ay_platform_ui/lib/auth.ts` v2 — `sanitizeRedirect()` (déjà
  ajouté début de session précédente, validé ici par 8 tests unit).
- `ay_platform_ui/app/auth-provider.tsx` v2 — watchdog 60s
  `setInterval` sur expiration.
- `ay_platform_ui/app/(protected)/layout.tsx` v3 — gate sur
  config + capture `pathname + searchParams` → `?redirect=`
  encoded.
- `ay_platform_ui/app/(protected)/dashboard/page.tsx` v2 —
  défensif via `useConfigState`.
- `ay_platform_ui/components/navbar.tsx` v2 — défensif via
  `useConfigState`.
- `ay_platform_ui/app/login/page.tsx` v3 — lit + sanitize
  `?redirect=`, l'utilise pour post-login + bounce already-auth.
- `ay_platform_ui/app/providers.tsx` — useTemplate fix (Biome).

**Tests** :
- `ay_platform_ui/tests/unit/lib/auth.test.ts` v2 — suite
  `sanitizeRedirect` (8 cas).
- `ay_platform_ui/tests/integration/auth-provider.test.tsx` v2 —
  watchdog suite (2 tests, fake timers AVANT render).
- `ay_platform_ui/tests/integration/protected-layout.test.tsx` v3 —
  loading test réécrit (vi.doMock isolation), 2 nouveaux tests
  pour `?redirect=` query encoding + preservation.
- `ay_platform_ui/tests/integration/login.test.tsx` v2 — 4
  nouveaux tests `?redirect=` (post-login, bounce auth, fallback,
  rejet `//evil.com`).
- `ay_platform_ui/tests/e2e/auth-gate.spec.ts` v2 — round-trip
  re-auth complet + open-redirect rejection E2E.
- `ay_platform_ui/tests/e2e/login.spec.ts` v2 — alert scoping +
  exact match fixes.

**Tooling** :
- `.devcontainer/Dockerfile` v1.7.0 → v1.9.0 — bake `/opt/ui-deps`
  + Playwright `/opt/playwright-browsers`.
- `.devcontainer/devcontainer.json` v6 → v8 — symlink
  postStartCommand, chown postCreateCommand.
- `.dockerignore` (NEW) v1 — denylist build context.
- `ay_platform_ui/package.json` v4 → v6 — `dev: next dev --webpack`,
  `lint:fix` script.
- `ay_platform_ui/biome.json` — `tailwindDirectives: true`.
- `.claude/settings.json` v12 → v13 — allowlist `npm run` UX
  scripts (lint/typecheck/test/build/format/ci) avec variantes
  `--prefix ay_platform_ui`.

## Validation pipeline UX

| Étape | Résultat |
|---|---|
| `npm run lint` | ✓ 30 fichiers |
| `npm run typecheck` | ✓ |
| `npm run test:coverage` | ✓ 88/88 tests, **90.56% line coverage** (gate 80%) |
| `npm run test:e2e` | ✓ 10/10 tests Playwright Chromium |

Backend Python intact : `ci-tests.yml` non touché, **1208 tests
verts** maintenu (pas re-run cette session car aucune modification
backend).

## Suivi (backlog)

- **Q-100-019 (NEW)** : décision long-terme Turbopack vs bake+symlink.
  Court terme : webpack-only en dev marche. Moyen terme :
  alternatives à évaluer si Turbopack devient critique (HMR speed,
  React 19 features) — soit `npm install` au `postCreateCommand`
  (perd le bake), soit `cp -r` au start (lent mais Turbopack-friendly),
  soit attendre patch Turbopack pour symlinks externes.
- **`app/page.tsx` à 0% coverage Vitest** — la landing anonyme est
  déjà couverte par E2E `landing.spec.ts`, mais pas testée en
  integration. Pas bloquant (root path triviale).
- **HMR webpack plus lent** : à monitorer. Si gênant, basculer
  vers une autre solution (Q-100-019).

## Git

Pas de commit / push effectué (per CLAUDE.md §5.2 le user commits).
État working tree : `git status` montre les fichiers UI modifiés +
`.devcontainer/Dockerfile` + `.devcontainer/devcontainer.json` +
`.dockerignore` (NEW) + `.claude/settings.json` + nouveaux tests.

---

**Phase 4b/c/d** (project management, file flows, chat with RAG SSE)
prête à démarrer.
