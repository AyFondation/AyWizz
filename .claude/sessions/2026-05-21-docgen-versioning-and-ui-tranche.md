<!-- =============================================================================
File: 2026-05-21-docgen-versioning-and-ui-tranche.md
Version: 1
Path: .claude/sessions/2026-05-21-docgen-versioning-and-ui-tranche.md
============================================================================= -->

# Session — 2026-05-21 — DocGen versioning + 6-feature UI tranche + V1/V2 boundary

## Context

Operator-driven UI tranche on the working-area DocGen surface, in the
agreed order (the operator confirmed "do all six in order"). Six
features, each carried end-to-end (backend + UI + tests). Closed with a
V1 completion review and the decision to move to V2 scoping.

## Work delivered (6 features)

1. **Drag-and-drop tree relocation** — `file-tree.tsx` v3 : rows
   `draggable`, folders + an implicit root act as drop targets,
   `onMove(sourcePath, destDir)` with no-op/cycle rejection. "Move to…"
   removed from both context menus (`working-area` v7).
2. **Per-AI-response versioning** — live-docs only. C3 mints one
   `response_turn_id` per turn → `X-Turn-Id` → C4 embeds `[turn:<id>]`
   in the Gitea commit. `ArtifactNode.version` = count of DISTINCT turn
   ids in the file's history (N writes in one response = one bump).
   Tree renders `name (vN)` (`file-tree.tsx` v4).
3. **Version-history viewer** — Gitea `get_file_at_ref`
   (`contents?ref=`), C4 `read_document_at_ref`, `git/commits?path`
   filter, `GET /documents/{path}?ref=<sha>`. UI : "View history…"
   context action → revision panel → loads content at a SHA with a
   "revision <sha> · back to latest" banner. MinIO keeps latest only ;
   history reads come from Gitea.
4. **Chain-of-thought inline detail** — `done_event.arguments`
   (size-capped via `_safe_tool_args`, `content` truncated). Inline
   tool rows are expandable (step/round + arguments + summary).
5. **Versioned "open in working area (vN)" links below the response** —
   `DocumentRef.version` (C4 computes post-write) → `done_event.version`
   → `InlineEvent.version`. New `<ModifiedDocsLinks>` renders one
   compact versioned link per modified doc, BELOW the response. The
   per-tool deep-link was removed from the inline log (now pure CoT).
6. **Full-width resizable working area** — dropped `max-w-7xl` ; flex
   3-pane row with WAI-ARIA window-splitter handles (pointer + keyboard)
   ; left/right widths persisted per-user in prefs
   (`workingAreaPaneWidths`), restored on load.

## Contract changes (§8.4)

- `ArtifactNode.version: int | None` (additive ; consumers ignore).
- `DocumentRef.version: int | None` (create/update responses).
- `InlineEvent` gains `arguments` + `version` (UI type).
- `_FakeGiteaClient` made faithful : `list_commits(path=...)` filter +
  `get_file_at_ref` + per-commit path/content snapshots.

Contract registry name-set unchanged → coherence/contract tests stay
green without edits.

## Verification

- Backend : `run_tests.sh ci` → ruff OK, mypy OK, **1528 passed, 2
  skipped** (k8s/nats optional deps), coverage **86.96 %**.
- UI : 122 Vitest green, Biome lint clean, `tsc` clean.
- **Environment caveats** (NOT code defects) : `next build` and the
  Next generated-types regeneration fail because Turbopack rejects the
  `node_modules -> /opt/ui-deps` symlink (out of FS root) ; the stale
  `.next/types/validator.ts` was removed to unblock local typecheck.
  Pointer-drag (#1, #6) not browser-verified here — code-reviewed +
  typed + lint only ; logic + persistence covered by tests.

## V1/V2 boundary (reviewed with operator)

- **V1 functional remainder agreed** : C6 stubs #3/#8 ; LiteLLM proxy
  deploy + per-agent routing (Q-100-021) ; prod K8s/CI/HTTPS +
  K8sDispatcher wiring. Optional UX backlog (project-creation, members,
  admin, run resume/retry) is non-blocking.
- **V2 = the two libraries discussed** : OpenHands (`generate` agentic
  harness, encapsulated `pipeline/generate_engine.py`, gated on POC
  Q13) and Graphiti (bi-temporal memory, KG L2/L3, D-016). Neither is a
  dependency today ; both are forward-looking per
  `references/aywiz-architecture-synthesis-v4.md`.
- Next : V2 scoping (sequencing OpenHands vs Graphiti).

## Files touched

Backend : `c2_auth/gitea_client.py`, `c3_conversation/{service.py,
document_tools.py}`, `c4_orchestrator/{artifacts_models.py,
artifacts_service.py,documents_router.py,artifacts_router.py}`.
Tests : new `tests/unit/c4_orchestrator/test_doc_versioning.py`,
`tests/unit/c2_auth/test_gitea_client_contents.py` ; extended
`test_document_tools.py`, `test_documents_api.py`,
`test_gitea_provisioning.py` (fake).
UI : `components/{file-tree.tsx,inline-log.tsx,chat-sidebar.tsx,
file-tree-context-menu.tsx}`, `app/(protected)/projects/[pid]/
{working-area/page.tsx,conversations/[cid]/page.tsx}`,
`lib/{types.ts,apiClient.ts,preferences.ts}` ; new UI tests
(`file-tree-dnd`, `file-tree-version`, `inline-log`, `preferences`,
extended `apiClient`).
