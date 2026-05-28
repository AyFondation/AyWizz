<!-- =============================================================================
File: 2026-05-12-ux-chat-finalisation.md
Version: 1
Path: .claude/sessions/2026-05-12-ux-chat-finalisation.md
Description: Multi-day session covering the UX chat finalisation work
             that happened between SESSION-STATE.md v34 (2026-04-29)
             and the start of the Artifacts pass (2026-05-12). Records
             every decision and feature that landed so a future
             session can pick up context without reading commit
             history.
============================================================================= -->

# UX chat finalisation — 2026-05-09 → 2026-05-12

## Tooling / infra deltas

- **`BUILD_VERSION` stamp** baked into both `Dockerfile.api` and `Dockerfile.ui` via build-arg ; ISO timestamp injected by `e2e_stack.sh dev` each rebuild. Exposed via `/ux/config.build_version` (API) + `NEXT_PUBLIC_BUILD_VERSION` (UI). Displayed as a 2-line block (`ui <stamp>` / `api <stamp>`) in the navbar so the operator can confirm a rebuild took effect without log-diving.
- **Traefik route `c2-preferences`** added to `infra/c1_gateway/dynamic/routers.yml` v4 for `PathPrefix("/api/v1/users/me/preferences")` with forward-auth.

## C2 — Per-user preferences + per-project LLM tuning

- **New collection `c2_user_preferences`** keyed by `user_id`. Stores `trigram`, `user_prompt`, `user_color` (all overrides, absent = use default).
- **New `UserPreferencesUpdate` / `UserPreferencesResponse`** : empty-string value clears the override (server `UNSET`s the field) ; `null`/missing = no change.
- **New endpoints** `GET /api/v1/users/me/preferences` + `PUT /api/v1/users/me/preferences` (any tenant member ; tenant_manager rejected by E-100-002 v2). Mounted via new `c2_auth/preferences_router.py` and `c2-preferences@file` Traefik rule.
- **Project `system_prompt`** : `ProjectPublic` now exposes the EFFECTIVE prompt (override OR `C2_DEFAULT_PROJECT_PROMPT`) + `system_prompt_is_default` flag. `ProjectUpdate.system_prompt` semantics : `""` clears, non-empty sets, `null`/missing no-op.
- **New endpoint `PATCH /api/v1/projects/{pid}`** + **`GET /api/v1/projects/{pid}`** (one-shot read for the settings page).
- **Env vars** `C2_DEFAULT_USER_PROMPT` (default = "Do not invent things…") + `C2_DEFAULT_PROJECT_PROMPT` (default empty) declared in `.env.example` and `.env.test`.
- **Session-revoked redirect funnel** : `ApiClient` calls `_notifySessionRevoked()` on any 401 with a stored token ; `AuthProvider` registers a handler that clears the token + flips to `anonymous` ; `(protected)` gate redirects to `/login`. Replaces every per-page "API 401" surface.

## C3 — Chat pipeline transparency + persistence

- **`MessageRequest.user_prompt` + `project_prompt`** : UX forwards the effective C2-resolved prompts per turn ; service prepends them (user → project → RAG) ahead of the RAG context block.
- **SSE protocol extended with named `event: stage` events** : `retrieve` / `generate` / `done` phases each emit a `running` then a `done` event with `duration_ms` + `stats`. Default `message` events keep carrying tokens (legacy clients keep working).
- **No-hits prompt fallback** (`_GENERAL_SYSTEM_PROMPT`) : when `hits_relevant == 0` the system prompt drops the "Retrieved context" framing entirely — fixes the qwen2.5:3b hallucination bridge where the model confabulated answers tying user questions to an empty/irrelevant context block ("Paris as capital of the Hispanic kingdom" failure mode).
- **Stage timeline persistence** : `done` stage payloads collected during the stream and persisted via `append_message(stages=...)`. `MessagePublic.stages` reads them back. Survives navigation + refresh.
- **Auto-rename on first message** : `POST /api/v1/conversations` creates with placeholder title `"New conversation"` ; on the first `send_message`, UX `PATCH`es the conversation with a derived title (first 60 chars trimmed at word boundary). Replaces the previous UX trap where the title prompt was confused with the composer.

## UI — Chat + preferences pages

- **Avatar component v2** : optional `color` hex override drives an inline-style bubble tint (alpha blend) ; assistant variant stays neutral grey.
- **Chat MessageBubble** : user bubbles RIGHT-aligned (`flex-row-reverse`) tinted with `user_color` ; assistant bubbles LEFT-aligned neutral. Pipeline timeline rendered as either a tiny inline chip `+ 12.4s` (collapsed) or a full panel above the bubble (expanded). Single-line layout when collapsed : `[avatar] [chip] [bubble]` on one row, big vertical-space win. Expanding switches to the 2-row stacked layout.
- **No more duplicate-display flash** : `messageCountAtSendRef` snapshots `state.messages.length` pre-send ; live row hidden once `state.messages.length >= snapshot + 2` (server pair arrived). `ThinkingBubble` removed — dots folded into the live-row bubble.
- **Duration formatting** : `< 1 s` shows 3 decimals (`0.029 s`) so retrieve doesn't display as `0 s` ; `>= 1 s` shows 1 decimal (`1.4 s`).
- **Preferences page v2** : 3 sections (Trigram avatar / LLM user prompt / Bubble colour). Each shows current value + "Reset to default" only when an override is stored. Trigram save also write-throughs to localStorage so the navbar avatar repaints instantly.
- **Project settings page v2** : per-project `system_prompt` editor. Editable by admin / tenant_admin / project_owner ; read-only for others with a hint pointing to the owner. Empty-string save clears the override (revert to C2 default).
- **Conversations list page v3** : one-click "+ New conversation" button (no title prompt). Replaces the trap where operators typed their question into the title field.

## Tests

- **Backend regression** : `tests/unit/c3_conversation/test_service.py` v2 covers `_assemble_system_prompt` × 4 branches (RAG variant / general fallback / preamble order / empty preambles dropped). `tests/integration/c3_conversation/test_rag_chat_flow.py` v? asserts ≥2 `event: stage` SSE blocks emitted AND stages persisted with `status='done'` + `duration_ms` set.
- **Backend integration** : `tests/integration/c2_auth/test_tenant_project_lifecycle.py` v2 covers GET `/api/v1/projects/{pid}`, PATCH with system_prompt set/clear cycle, user preferences round-trip (incl. hex colour validation 400).
- **Catalog drift** : `tests/coherence/test_route_catalog.py` updated to mount `preferences_router` ; auth-matrix `_catalog.py` adds `GET /{pid}`, `PATCH /{pid}`, GET/PUT prefs.
- **Backend CI green** : 1243 passed, only the 6 pre-existing Loki/ES testcontainer errors remain (infra in this devcontainer can't reach those images).
- **UI** : lint OK, typecheck OK, vitest 99/99.

## Operational notes

- The dev stack rebuild via `e2e_stack.sh dev` now stamps every image with an ISO timestamp ; `e2e_stack.sh` script bumped to v6.
- Docker desktop disk fills fast during iterative rebuilds. Workaround used : `docker system prune -af` to free ~10 GB when overlayfs hits 100%. Document in §3 of SESSION-STATE.
- The 4 demo accounts on the login page auto-fill still works ; tested end-to-end with `tenant-admin` / `dev-tenant`.
