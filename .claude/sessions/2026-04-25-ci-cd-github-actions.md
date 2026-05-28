# Session 2026-04-25 — CI/CD initial via GitHub Actions + GHCR

## Trigger

Demande utilisateur : run automatique des tests à chaque push sur `main`,
affichage OK/NOK + couverture sur la page principale du projet GitHub ;
build automatique des images à chaque commit.

## Décisions

1. **Plateforme CI** : GitHub Actions (intégré, sans coût additionnel).
2. **Registry images** : GHCR (`ghcr.io/<owner>/aywizz-api`). Auth via
   `secrets.GITHUB_TOKEN` ; pas de service tiers à provisionner.
3. **Trigger tests** : `push` sur `main` uniquement (pas de PR pour
   l'instant — à élargir plus tard).
4. **Build conditionné** : `ci-build-images.yml` est déclenché par
   `workflow_run` de `ci-tests` (success). Aucune image `:latest`
   publiée depuis un commit cassé.
5. **Test orchestration** : invocation via `bash ay_platform_core/scripts/run_tests.sh ci`
   (CLAUDE.md §8.3) — cohérent avec la chaîne locale et produit
   `ay_platform_core/reports/latest/`.
6. **Coherence checks** : job parallèle dans `ci-tests.yml`, bloquant.
7. **Affichage couverture** :
   - Job summary GitHub Actions (% extrait de `coverage.xml`).
   - Artefact `test-reports-<sha>` (rétention 14 jours).
   - Badge gist via `schneegans/dynamic-badges-action` (setup ponctuel
     `secrets.GIST_SECRET` + `vars.COVERAGE_GIST_ID`, step skippée
     silencieusement si manquant).
8. **Pas de Dockerfile.ui pour l'instant** : `infra/docker/Dockerfile.ui`
   absent (CLAUDE.md §4.5 — scaffold UI futur).

## Traces specs

- **D-014** ajoutée dans `999-SYNTHESIS.md` §5 (v4→v5).
- **R-100-123** ajoutée dans `100-SPEC-ARCHITECTURE.md` §5 NFR (v10→v11),
  `derives-from` frontmatter étendu de `D-014`.

## Fichiers livrés

- `.github/workflows/ci-tests.yml` v1 (nouveau)
- `.github/workflows/ci-build-images.yml` v1 (nouveau)
- `requirements/999-SYNTHESIS.md` v4 → v5 (D-014 ajoutée)
- `requirements/100-SPEC-ARCHITECTURE.md` v10 → v11 (R-100-123 ajoutée)
- `.claude/SESSION-STATE.md` v19 → v20

## Setup ponctuel restant côté utilisateur

1. (Optionnel) badge couverture : créer un gist + PAT scope `gist`,
   ajouter `secrets.GIST_SECRET` et `vars.COVERAGE_GIST_ID` dans
   les settings du repo.
2. Visibilité du package GHCR à ajuster après le premier push si besoin.
3. Snippet badges README à coller dans un `README.md` au root (à créer
   par l'utilisateur, pas par Claude per §5.2).
