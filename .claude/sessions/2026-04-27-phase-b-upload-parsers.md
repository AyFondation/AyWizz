# Session 2026-04-27 — Phase B v1 plan : Upload + parsers

## Trigger

Phase B du plan v1 fonctionnel. Sans elle, l'utilisateur n'a aucune
façon d'envoyer un fichier réel — seul l'endpoint
`POST /api/v1/memory/projects/{p}/sources` (texte pre-parsé JSON) marche.
La cible : multipart/form-data → blob MinIO + parse → chunks/embed
indexés (le pipeline downstream est déjà en place depuis Phase C).

## Décisions actées

1. **Endpoint dédié multipart**, séparé de l'endpoint JSON existant :
   `POST /api/v1/memory/projects/{project_id}/sources/upload`. Garde
   la rétrocompat C12 (webhook texte) et clarifie le contrat.
2. **Parsers activés v1** : `text/plain`, `text/markdown`,
   `text/html` (BeautifulSoup), `application/pdf` (pypdf), DOCX
   (python-docx). **Out-of-scope v1** : OCR images (`image/png`,
   `image/jpeg` retirés de la `Literal[...]` du contrat — réservés
   v1.5+).
3. **Blob persistence MinIO** : path
   `sources/{tenant_id}/{project_id}/{source_id}{.ext}` (extension
   inférée du MIME). Bucket = `c7_minio_bucket` (default `memory`).
4. **Cap de taille** : `C7_MAX_UPLOAD_BYTES` = 50 MiB par défaut →
   413 si dépassé. Configurable.

## Code livré

### Nouveau

- [`c7_memory/storage/minio_storage.py`](ay_platform_core/src/ay_platform_core/c7_memory/storage/minio_storage.py)
  v1 — `MemorySourceStorage` async wrapper sur python-minio (pattern
  identique à C5/C6) : `put_source_blob`, `get_source_blob`,
  `delete_source_blob`, `ensure_bucket`. Path déterministe
  scope-tenant.
- [`tests/integration/c7_memory/test_upload_pipeline.py`](ay_platform_core/tests/integration/c7_memory/test_upload_pipeline.py)
  — 7 tests : 1 par parser (text/plain, MD, HTML, PDF, DOCX) +
  unsupported-mime → 415 + corrupt-PDF → 422. Chaque test asserte :
  - HTTP 201 sur l'upload ;
  - chunks Arango présents ;
  - phrase clé extraite par le parser dans le contenu indexé ;
  - blob MinIO round-trip byte-exact.

### Modifié

- [`c7_memory/ingestion/parser.py`](ay_platform_core/src/ay_platform_core/c7_memory/ingestion/parser.py)
  v1→v2 — registry étendu :
  - `text/html` (BeautifulSoup, lxml prefer + html.parser fallback,
    drop `<script>`/`<style>`/`<noscript>`).
  - `application/pdf` (pypdf, encrypted → ParseFailureError, malformed
    page → graceful `[page N unreadable: TYPE]`).
  - DOCX (python-docx, paragraphs + tables flattened cell par cell).
  - Image MIMEs **retirés** du registry. Nouvelle exception
    `ParseFailureError` (vs `UnsupportedMimeError`).
- [`c7_memory/service.py`](ay_platform_core/src/ay_platform_core/c7_memory/service.py) :
  - Refactor : pipeline post-parse extrait dans
    `_index_parsed_source(...)` partagé entre `ingest_source`
    (string-based, C12 webhook) et `ingest_uploaded_source`
    (bytes-based, multipart).
  - Nouveau `ingest_uploaded_source(...)` : check size cap,
    parse(bytes), MinIO put, then index.
  - `MemoryService.__init__` accepte `storage: MemorySourceStorage |
    None`. Tests qui n'exercent pas l'upload peuvent omettre.
- [`c7_memory/router.py`](ay_platform_core/src/ay_platform_core/c7_memory/router.py) :
  nouvel endpoint `POST .../sources/upload` (multipart, Form +
  UploadFile + role gate identique à `POST .../sources`).
- [`c7_memory/main.py`](ay_platform_core/src/ay_platform_core/c7_memory/main.py)
  v2→v3 : Minio client + MemorySourceStorage construits, lifespan
  appelle `storage.ensure_bucket()`.
- [`c7_memory/config.py`](ay_platform_core/src/ay_platform_core/c7_memory/config.py) :
  +`max_upload_bytes` (50 MiB default).
- [`c7_memory/models.py`](ay_platform_core/src/ay_platform_core/c7_memory/models.py) :
  `SourceIngestRequest.mime_type` `Literal[...]` étendu pour
  text/html + DOCX, image/png et image/jpeg retirés.
- [`pyproject.toml`](ay_platform_core/pyproject.toml) : nouvelles deps
  core (pypdf, beautifulsoup4, python-docx ; lxml en transitif via
  python-docx).
- [`tests/e2e/auth_matrix/_catalog.py`](ay_platform_core/tests/e2e/auth_matrix/_catalog.py) :
  +1 endpoint catalogué (`backend=BOTH`, MinIO + Arango).
- [`tests/e2e/auth_matrix/_stack.py`](ay_platform_core/tests/e2e/auth_matrix/_stack.py) :
  `_build_c7` accepte minio + bucket pour wirer le storage.
- [`tests/integration/c7_memory/conftest.py`](ay_platform_core/tests/integration/c7_memory/conftest.py) :
  fixtures `c7_storage`, `c7_upload_service`, `c7_upload_app`.
- `.env.example` + `.env.test` : `C7_MAX_UPLOAD_BYTES=52428800`.
- Tests obsolètes mis à jour :
  - `tests/integration/c7_memory/test_retrieval_flow.py` :
    `test_pdf_ingest_returns_501_without_extra` → renommé +
    asserte 422 (parser activé).
  - `tests/unit/c7_memory/test_parser.py` : tests
    NotImplementedError → ParseFailureError (PDF) +
    UnsupportedMimeError (image, retirée du registry).

## Validation

`run_tests.sh ci` : **1133 verts en 130s**, 0 conteneur orphelin.

Sub-suite directe (Phase B) :

```
tests/integration/c7_memory/test_upload_pipeline.py  7 passed
  - text_plain               → Voyager 1 phrase indexée + blob round-trip
  - text_markdown            → Eiffel Tower, frontmatter NOT in chunks
  - text_html                → Helvetica typeface OK, script/style stripped
  - application_pdf          → Honeybees extracted
  - corrupt_pdf              → 422 invalid PDF
  - application_docx         → Pyrenees extracted
  - unsupported_mime         → 415
```

## Lessons

- **PDF authoring from pypdf en test** : pypdf fournit `PdfWriter`
  pour écrire mais pas de helper text-rendering. Pour produire un
  PDF parseable round-trip, il faut un content stream `BT /F1 12 Tf
  X Y Td (text) Tj ET` + un Resources/Font dict. Pattern
  réutilisable pour tous les tests PDF.
- **Lazy imports pour cold-start** : pypdf, bs4+lxml, python-docx
  sont plusieurs MB chacun. Importer au top-level les charge dans
  TOUS les processes C7 (mock LLM, etc.) au démarrage. Imports en
  fonction → cold-start préservé. ruff PLC0415 demande un noqa
  justifié — c'est valide.
- **MIME `Literal[...]` contract drift** : `image/png` /
  `image/jpeg` étaient déclarés dans `SourceIngestRequest.mime_type`
  v0 mais aucun parser actif. Phase B nettoie : on retire les MIMEs
  fantômes du contrat. Convention : le `Literal[...]` du modèle
  Pydantic SHALL refléter la registry actuelle, pas une intention
  future.
- **Refactor "shared post-parse pipeline"** : quand 2 entry points
  (texte / bytes) divergent uniquement sur le parsing, extraire la
  pipeline partagée comme méthode privée évite la duplication. Les
  tests d'integration des deux entry points exercent le même code.

## Suite

État du plan v1 fonctionnel à fin Phase B :

- ✅ Phase A — Tenant + Project lifecycle
- ✅ Phase C — Embeddings réels (Ollama)
- ✅ Phase B — Upload + parsers (PDF/MD/HTML/DOCX)
- ⏳ **Phase D** — Chat-with-RAG dans C3 (~2 sessions) ← prochaine
- ⏳ Phase E — Conversation → memory loop
- ⏳ Phase F — KG extraction (F.1 only)

Phase D va consommer les chunks indexés par Phase B + l'embedder
Phase C : `POST /api/v1/conversations/{id}/messages` qui retrieve
C7 → augment prompt → C8 LLM → stream SSE → persist.

## Rollback

Branche `main` HEAD avant : commit post-Phase C. Rollback safe via
`git revert` :
- Nouveaux fichiers (storage module, test file) : suppression nette.
- Modèles (`SourceIngestRequest.mime_type`) : breaking change SI un
  consumer envoie image/* (none au monorepo).
- Deps pyproject : pypdf/bs4/python-docx en plus — sans impact sur
  les composants qui ne les importent pas.
- Tests obsolètes mis à jour : pas restaurer les anciens, ils
  testaient des stubs activés.
