<!-- =============================================================================
File: 2026-05-19-increment-3-cross-nav-state-and-sse-ownership.md
Version: 1
Path: .claude/sessions/2026-05-19-increment-3-cross-nav-state-and-sse-ownership.md
Description: Append-only session journal entry. Immutable once written;
             corrections go in a new entry referencing this one.
============================================================================= -->

# Session — Increment 3 (cross-nav UI store + provider-owned SSE loop)

**Date:** 2026-05-19 (addendum to
`2026-05-19-docgen-2c-and-llm-provider-migration.md` — same calendar
day, same DocGen workstream).
**Outcome:** Increment 3 (a + b) DONE and **operator-validated end to
end** on both chat surfaces. CI carried from the prior entry stays
green (1332 passed, cov 87.95%).

---

## 1. What shipped

### 1.1 Increment 3a — Tier-1 cross-nav UI store

`WorkspaceProvider` mounted ABOVE the Next router outlet in
`app/(protected)/layout.tsx` (route-group layouts don't unmount on
navigation between sibling routes — perfect anchor). Per-project
ephemeral UI state hydrated from `sessionStorage` so it survives a
F5 too :

- `activeConversationId`, `selectedRunId`, `selectedPath`
- `composerDrafts: Record<conversationId, string>` (NOT a single
  project-level draft — operator-reported bug fix : a single field
  bled into freshly-created conversations).
- Targeted store action `setDraft(projectId, convId, text)` —
  functional setState + string-compare no-op breaks the persist-loop
  / stale-closure pitfalls of a generic `setUi({...})`.

Surfaces wired (mirror pattern : local state stays authoritative,
restore-once on hydration + persist-on-change) :

- Working area run/doc viewer (selected run + selected path).
- ChatSidebar composer draft + active conversation.
- Conversations `[cid]` page composer draft.
- Conversations list page : the **`[cid]` page records the active
  conversation** so the list can offer a user-initiated
  "↩ Resume last conversation" link.

### 1.2 Increment 3b — provider owns the SSE loop

The SSE send-loop + per-conversation live runtime (`streaming`,
`liveAssistant`, `liveEvents`, `turnSeq`, `error`) live in the
provider behind :

- `useConvRuntime(conversationId)` — `useSyncExternalStore` with a
  stable per-conv snapshot ref (a streamed token re-renders ONLY the
  active chat, NOT the whole protected subtree).
- `useWorkspaceSend()` returning the provider's `send(args)` which
  runs `apiClient.sendMessageStream` internally, merges stage
  `running→done` events, calls `onMutatingTool` on a successful
  DocGen mutation, surfaces 4xx/5xx as `rt.error`, and bumps
  `turnSeq` on completion.

Consumer pattern (ChatSidebar + Conversations `[cid]`) :

1. read `rt.streaming`/`rt.liveAssistant`/`rt.liveEvents` from
   `useConvRuntime` ;
2. call `send({cfg, conversationId, payload, userPrompt?,
   projectPrompt?, onMutatingTool?})` from `onSend` (no `await` —
   the runtime drives the live UI) ;
3. on `turnSeq` change, refetch `listMessages` (replaces the
   optimistic user + the cleared live row) and trigger the
   "✓ Génération terminée" cue / refocus the composer.

A live generation now continues when the operator changes tab (left
nav) **or** route (Conversations `[cid]` → Working area / list).
Audit trail still the server-side `MessagePublic.events` ledger
(survives reload regardless of 3b).

### 1.3 Operator-reported bugs caught during the increment

- **List auto-resume trap (revert)** : my first 3a attempt had the
  Conversations list page `router.replace` to the stored conversation
  on mount. It became inescapable — breadcrumb + URL-edit bounced
  right back, and a Working-area-created conversation also hijacked
  it. Replaced by a user-initiated header link "↩ Resume last
  conversation". The list is now always a real destination.
- **Draft bleed** : a single project-level `composerDraft` was
  inherited by a newly-created conversation. Fixed by keying drafts
  per conversation id (see 1.1).
- **`sessionStorage` schema mismatch** : the v4 split
  `composerDraft: string` → `composerDrafts: Record` made any
  browser holding pre-v4 data crash on `ui.composerDrafts[cid]`
  against `undefined`. Fix : **versioned STORAGE_KEY**
  (`aywizz.workspace.ui.v2`) + `normaliseProjectSlice` on read.
  Lesson : non-critical UI state must NEVER take the page down on a
  hydration shape mismatch — bump the key and normalise.

---

## 2. Discipline notes (saved as memory for next sessions)

- **Shell `&&` chaining is banned** (CLAUDE.md §5.7) — split
  format-then-check-then-typecheck into separate Bash tool calls.
  Operator invoked `recalibre §5.7` after I repeatedly chained
  `biome --write && biome check && tsc`. Saved as feedback memory
  `feedback_shell_no_and_chain.md`.

---

## 3. Reserves / known limitations

- The "Resume last conversation" link in the Conversations list is
  scoped to the conversation **the operator last opened in the
  list/[cid] surface** (3a recording). It does NOT track a
  conversation created in Working area to "the conversations tab"
  by design — those are two separate surfaces ; cross-surface sync
  would re-introduce surprise.
- Increment 3b makes **navigation** survive a live generation, NOT
  a hard refresh (a F5 kills the JS context — the SSE fetch dies
  with it). The audit `events` ledger keeps the completed state
  visible after reload regardless ; only an in-flight turn would
  be lost on F5, which is unchanged from before.

---

## 4. Pointers

- Provider : `ay_platform_ui/app/(protected)/workspace-store.tsx` v5.
- Layout mount : `ay_platform_ui/app/(protected)/layout.tsx` v5.
- Consumers : `components/chat-sidebar.tsx` v7 ; pages
  `conversations/page.tsx` v5 + `conversations/[cid]/page.tsx` v16 ;
  `working-area/page.tsx` v4.
