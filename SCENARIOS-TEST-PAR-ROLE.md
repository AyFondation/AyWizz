---
title: Scénarios de test manuels par profil utilisateur
version: 1
path: SCENARIOS-TEST-PAR-ROLE.md
---

# Scénarios de test par profil utilisateur

Guide de test manuel de bout en bout, organisé par **rôle**. Objectif : vérifier
que chaque profil peut faire ce qu'il doit (chemin nominal) **et** se voit refuser
ce qui ne le concerne pas (garde-fou). Volontairement en grandes lignes — chaque
scénario dit *quoi* tester et *le résultat attendu*, pas le clic-à-clic.

> Convention de lecture : ✅ = doit réussir · ⛔ = doit être refusé (403/404) ·
> 👁️ = lecture seule.

---

## 0. Rôles (rappel — E-100-002)

| Rôle | Portée | Rôle en une phrase |
|---|---|---|
| `platform_manager` | Global / tenant | Gouvernance : tenants, utilisateurs, quotas. **Aveugle au contenu projet** (séparation des pouvoirs). |
| `tenant_admin` (`admin`) | Tenant | Administration du tenant. **Également exclu du contenu projet.** |
| `project_owner` | Projet | Contrôle total d'un projet : réglages, membres, contenu, suppression. |
| `project_editor` | Projet | Lit **et** écrit le contenu (docs, sources, exigences, pipeline). |
| `project_viewer` | Projet | Lecture seule du contenu du projet. |
| `user` (baseline) | — | Authentifié mais sans aucun grant → sert aux tests négatifs. |
| *anonyme* | — | Aucune identité → rejeté partout sauf endpoints ouverts. |

---

## 1. Transversal — à vérifier avant les profils

Ces cas cadrent la sécurité de base ; ils ne dépendent d'aucun rôle métier.

- **Anonyme** : appeler n'importe quelle page/endpoint non-ouvert sans session → ⛔ (jamais de 2xx). Seuls `/health`, login et pages publiques répondent.
- **`user` sans grant** : connecté mais sans rôle → voit son profil/préférences, mais ⛔ sur toute ressource tenant ou projet.
- **Isolation tenant** : un utilisateur du tenant A qui vise une ressource du tenant B → ⛔ (403/404, sans fuite d'existence).
- **Isolation projet** : un membre du projet P1 qui vise le projet P2 (même tenant) sans grant sur P2 → ⛔.
- **Modes d'auth** (`local` / `none` / `entraid`) : la même identité produit les mêmes droits en aval quel que soit le mode configuré.

---

## 2. `platform_manager` (gouvernance)

**Doit pouvoir (✅)**
- Gérer les **tenants** : créer / lister / configurer.
- Gérer les **utilisateurs** : créer, assigner des rôles, désactiver.
- Gérer les **quotas** (global / tenant / projet / utilisateur) et les voir appliqués.
- Déclarer / configurer les **registres application** : fournisseurs et catalogues **LLM** et **embeddings** au niveau application.

**Doit être refusé (⛔) — séparation des pouvoirs**
- Ouvrir le **contenu** d'un projet : conversations, exigences, sources, documents/working-area, pipeline, validation → ⛔ (rôle aveugle au contenu).
- Écrire ou supprimer un artefact projet.

---

## 3. `tenant_admin` / `admin` (administration tenant)

**Doit pouvoir (✅)**
- Administrer son **tenant** : utilisateurs et projets du tenant, réglages tenant.
- Mettre à disposition au niveau **tenant** les modèles LLM/embeddings sélectionnés depuis le catalogue application.
- Consulter la **consommation / quotas** du tenant.

**Doit être refusé (⛔)**
- Le **contenu** des projets (même exclusion que `platform_manager` : conversations, sources, docs, exigences) → ⛔.
- Toute action hors de son tenant → ⛔ (isolation).

---

## 4. `project_owner` (propriétaire de projet)

**Doit pouvoir (✅)**
- **Réglages projet** : renommer, configurer, choisir le modèle d'embedding du projet, gérer les **membres** (inviter editor/viewer).
- Tout ce qu'un `project_editor` peut faire (voir §5).
- **Sélectionner** le modèle d'embedding et **déclencher un ré-embedding** du projet ; vérifier que les vecteurs sont recalculés **sans** re-traiter les documents.
- **Supprimer** des ressources projet et, in fine, le projet.

**Doit être refusé (⛔)**
- Administrer d'autres projets où il n'a pas de grant → ⛔.
- Fonctions de gouvernance plateforme/tenant (tenants, utilisateurs globaux) → ⛔.

---

## 5. `project_editor` (contributeur)

C'est le profil « au cœur » des chaînes RAG et documentaires.

**Doit pouvoir (✅)**
- **Conversations / chat** : dialoguer avec l'agent ; vérifier que la réponse s'appuie sur le **RAG** (sources + conversations + live-docs).
- **Exigences** : créer / éditer / versionner des exigences.
- **Sources (chemin lourd)** : uploader un document → ingestion (extraction + chunk + embed) → il devient **récupérable** en RAG. Vérifier l'extraction **KG structurel** pour code/exigences.
- **Working-area / documents (chemin léger)** : créer, **renommer, déplacer, supprimer** fichiers et dossiers ; **voir et éditer** un document.
- **Génération par l'IA** : demander à l'agent de créer/modifier un document dans l'arborescence → il apparaît et devient **indexé en RAG** (léger, sans passer par le chemin lourd).
- **Pipeline** : lancer les phases de génération autorisées à un editor.

**Doit être refusé (⛔)**
- Réglages projet sensibles / gestion des membres / suppression du projet (réservés `owner`) → ⛔.
- Tout projet sans grant, tout autre tenant → ⛔.

---

## 6. `project_viewer` (observateur)

**Doit pouvoir (👁️)**
- **Lire** : conversations, exigences, sources, arborescence de documents, artefacts, résultats de validation.

**Doit être refusé (⛔)**
- Toute **écriture** : créer/éditer/renommer/déplacer/supprimer un document, uploader une source, éditer une exigence, lancer le pipeline → ⛔.

---

## 7. Chaînes RAG & documentaires — validation bout-en-bout

Transversal aux rôles `owner`/`editor` (écriture) et `viewer` (lecture). Ce sont
les chaînes récemment livrées : à dérouler une fois complètement.

1. **RAG vectoriel — chemin lourd (upload)** : upload d'une source → ingestion → une question en chat retrouve le passage. ✅
2. **RAG par graphe (KG)** : après ingestion d'un code Python / d'exigences, le **graphe structurel** est peuplé (entités + relations) et enrichit la récupération.
3. **RAG — chemin léger (live-docs)** : créer/éditer un document de l'arborescence → indexé directement (débouncé, sans C13) → retrouvable en chat.
4. **Type-dispatch** : vérifier le traitement selon le type — prose, **exigences** (cascade `id:`/`derives-from:`), **code** Python, tabulaire (repli prose, `reduced_fidelity`).
5. **KG des live-docs** : éditer un `.py` en renommant une fonction → le **nœud KG** de l'ancienne fonction disparaît (ré-index idempotent) ; supprimer le doc → sa contribution KG est purgée.
6. **`kg_indexed` réel** : les métadonnées d'un fichier source reflètent son appartenance réelle au KG (plus `null` par défaut).
7. **Ré-embedding** : changer le modèle d'embedding du projet → recalcul des vecteurs de **tous** les éléments (sources, conversations, live-docs) sans re-traitement des données.
8. **Édition par l'IA** : l'agent génère un document dans l'arborescence → visible + éditable par l'utilisateur + indexé RAG.

---

*Dis-moi si tu veux que je précise un scénario (étapes détaillées, endpoint exact,
ou état backend à observer) ou que je l'adapte à un mode d'auth particulier.*
