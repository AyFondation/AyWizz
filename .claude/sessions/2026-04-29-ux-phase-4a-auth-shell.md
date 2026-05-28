# Session 2026-04-29 — UX Phase 4a : Auth-aware shell

## Trigger

Suite Phase 3 (Dockerfile.ui + K8s manifests). La shell UX a un
`<ConfigProvider>` mais aucune notion d'auth state. Phase 4a livre
le minimum pour que toute UX-feature suivante (projects, files, chat)
fonctionne : auth context propagé, route group protected, login flow
end-to-end, navbar + logout.

## Décisions actées

1. **JWT décode côté client manuel** (base64url + atob) plutôt
   que dep `jwt-decode`. Zéro dep, ~1 KB de code. Signature pas
   vérifiée côté client — la vérification reste serveur sur chaque
   request.
2. **Skew 30s sur expiration** — `isTokenExpired()` traite le token
   expiré 30s avant son `exp` réel pour compenser le clock drift
   client/serveur.
3. **`<AuthProvider>` Client Component** distinct de `<ConfigProvider>`.
   Hydrate depuis localStorage en `useEffect` au mount.
   3 états : `loading` (initial mount, render skeleton, NE PAS
   redirect) | `authenticated` | `anonymous`.
4. **AuthProvider OWNS persistence** — `apiClient.login()` retourne
   le token sans le stocker ; le caller (login page) appelle
   `auth.setToken(token)` qui décode + writeStoredToken() + setState.
   Évite la double source de vérité (state React vs localStorage).
5. **Route group `(protected)/`** Next.js App Router pour gate :
   parenthèses = scope sans path segment (URL `/dashboard` reste
   `/dashboard`, pas `/protected/dashboard`). Layout du group fait
   le redirect anonymous → `/login` via `useEffect` (pas dans render).
6. **Logout = clear localStorage + setState anonymous + router push
   `/login`**. Pas de logout serveur en v1 (token blacklist sur C2 =
   v1.5). Token expire de toute façon dans 1h.
7. **Refresh token** différé v1.5 — quand exp passe, l'utilisateur
   se reconnecte. Pour v1 c'est acceptable (user dev, sessions
   courtes).
8. **Navbar dans `(protected)/layout.tsx`** uniquement. Pages
   anonymes (landing, login) n'ont pas de navbar. La login page
   redirige authenticated users vers `/dashboard` pour ne pas
   afficher le formulaire à un user déjà connecté.

## Fichiers livrés

- `ay_platform_ui/lib/auth.ts` (NEW) — `decodeJWT`, `isTokenExpired`,
  `JWTClaims`. Décodage manuel base64url, browser+Node compat.
- `ay_platform_ui/app/auth-provider.tsx` (NEW) — `<AuthProvider>` +
  `useAuth()` hook. State machine 3 états.
- `ay_platform_ui/lib/apiClient.ts` v2 — `login()` retourne le token
  sans persister ; `logout()` retiré (responsabilité AuthProvider).
- `ay_platform_ui/components/navbar.tsx` (NEW) — header avec brand
  (config) + user info (claims) + Sign out.
- `ay_platform_ui/app/(protected)/layout.tsx` (NEW) — route group
  layout avec auth gate.
- `ay_platform_ui/app/(protected)/dashboard/page.tsx` (NEW) —
  placeholder qui affiche les claims JWT décodés (proof auth state
  propagation end-to-end).
- `ay_platform_ui/app/layout.tsx` v3 — wrap dans
  `<ConfigProvider><AuthProvider>`.
- `ay_platform_ui/app/page.tsx` v3 — redirige authenticated users
  vers `/dashboard`.
- `ay_platform_ui/app/login/page.tsx` v2 — `apiClient.login()` →
  `auth.setToken(token)` → redirect `/dashboard`. Auth-redirect
  bidirectionnel (déjà connecté → bounce).

## Pattern final

```
Boot                 : AuthProvider mount
                     → readStoredToken() from localStorage
                     → decodeJWT() + isTokenExpired() check
                     → setState authenticated | anonymous

Login flow           : /login form → apiClient.login() → token
                     → auth.setToken(token)
                         ├─ decodeJWT() → claims
                         ├─ writeStoredToken() → localStorage
                         └─ setState authenticated
                     → router.push("/dashboard")

Protected page-load  : (protected)/layout → useAuth()
                     → if anonymous : router.replace("/login")
                     → if loading   : render spinner
                     → if authenticated : Navbar + children

Logout               : Navbar.handleLogout
                     → clearAuth() → clearStoredToken + setState anonymous
                     → router.push("/login")
```

## Validation locale

- CI Python : **1208 verts inchangé** (Phase 4a = frontend only).
- Validation runtime UI : impossible côté Claude (npm install denied).
  L'utilisateur doit faire `cd ay_platform_ui && npm install &&
  npm run dev` pour valider que Next.js 16 + React 19 + tous les
  deps installés sont compatibles avec mon code.

## Reste à faire post-session

- **`npm install` côté utilisateur** — peut révéler des
  incompatibilités API qu'on n'a pas vues.
- **`npm run typecheck` + `npm run lint`** — biome 2 / TS 5.8 peuvent
  flagger du code que je pense correct.
- **`npm run dev`** — vérifier le flow login → dashboard end-to-end
  contre le compose stack ou le K8s system-test.
- **Push CI** — `ci-build-images.yml` v3 va build `aywizz-ui:latest`
  pour la première fois sur GHCR.
- **Phases suivantes (4b/c/d)** sur la base auth shell : project
  management UI, file flows, chat with RAG SSE.

## Suite proposée

Recommandation forte : **valider Phase 4a runtime** avant 4b/c/d.
Sinon on empile 3-4 phases sur du code jamais exécuté. Si runtime
OK → enchaîner par Phase 4b (project management UI, le plus simple
des 3 restants — full CRUD via APIs déjà existantes).
