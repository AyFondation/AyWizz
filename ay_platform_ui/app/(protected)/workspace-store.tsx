// =============================================================================
// File: workspace-store.tsx
// Version: 5
// Path: ay_platform_ui/app/(protected)/workspace-store.tsx
//
// v5 (2026-05-19): sessionStorage schema-migration safety.
// STORAGE_KEY bumped to v2 (the v4 split `composerDraft: string` →
// `composerDrafts: Record`  made any browser still carrying v1 data
// crash on `ui.composerDrafts[cid]` against `undefined`). loadAll
// now normalises each project slice on read so an unexpected shape
// (legacy schema, partial data) never throws — non-critical UI
// state must NOT take down the page.
//
// v4 (2026-05-19): composer draft is now PER-CONVERSATION
// (`composerDrafts` keyed by conversation id) — a project-level
// single draft bled into freshly-created / other conversations.
// New `setDraft(projectId, convId, text)` action : functional
// setState + string-compare no-op (no stale closure, no persist
// loop). Consumers key restore/persist by the active conversation.
//
// v3 (2026-05-19): Increment 3b.1 — the provider now ALSO owns the
// per-conversation SSE send-loop (`send`) + a runtime external store
// (`useConvRuntime`, useSyncExternalStore so a streamed token does
// NOT re-render the whole protected subtree). A live generation
// keeps running + observable when the chat surface unmounts on tab
// nav. UI-state map (v2) is unchanged.
// Description: Increment 3 (phase 3a) — Tier-1 per-project UI-state
//              store. Mounted in `(protected)/layout.tsx` ABOVE the
//              Next router outlet, so it survives navigation between
//              tabs (Working area <-> Conversations <-> Documents):
//              switching tab no longer loses the selected run/doc,
//              the active conversation, or the composer draft.
//
//              Two-tier model (operator's explicit split):
//                - THIS store = local UI state only (ephemeral,
//                  presentation). Hydrated from `sessionStorage`
//                  (per project) so it ALSO survives a hard refresh.
//                - The processing-flow / audit trail (tool calls,
//                  pipeline) is NOT here — it is persisted server
//                  side as `MessagePublic.events` and re-rendered
//                  from the DB (traceability by construction).
//
//              v2 : idiomatic `useState`-backed map (the v1 ref +
//              version-bump hack fought the exhaustive-deps lint and
//              was non-standard). The no-op guard returns the SAME
//              map reference so React bails the render — no storm.
//
//              Phase 3b (deferred) will move the SSE send-loop into
//              this provider so a LIVE generation keeps running when
//              the operator navigates away and back. Not in 3a — the
//              audit trail already survives reload via the DB ledger.
// =============================================================================

"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { ApiClient, ApiError } from "@/lib/apiClient";
import type { InlineEvent, PlatformConfig, PromptReference } from "@/lib/types";

/** Ephemeral per-project UI state. Presentation only — never the
 *  source of truth for anything persisted server-side. */
export interface ProjectUiState {
  /** Conversation selected in Working area / last opened. */
  activeConversationId: string | null;
  /** Artifact run selected in the Working area tree panel. */
  selectedRunId: string | null;
  /** Document path open in the Working area viewer. */
  selectedPath: string | null;
  /** Unsent composer text PER CONVERSATION (keyed by conversation
   *  id) so a half-typed message survives a tab switch / F5 WITHOUT
   *  bleeding into another (or a freshly-created) conversation. */
  composerDrafts: Record<string, string>;
}

const EMPTY: ProjectUiState = {
  activeConversationId: null,
  selectedRunId: null,
  selectedPath: null,
  composerDrafts: {},
};

/** Live runtime of one conversation's in-flight turn. Owned by the
 *  provider (Increment 3b) so the SSE loop keeps running — and its
 *  state stays observable — even when the chat surface unmounts on
 *  tab navigation. Per-conversation so switching conversations just
 *  reads a different slice (no manual reset). */
export interface ConvRuntime {
  streaming: boolean;
  /** Accumulating assistant text for the in-flight turn ; null when
   *  idle / just-cleared. */
  liveAssistant: string | null;
  /** Unified inline-activity, accumulated across the conversation's
   *  turns (audit trail still the server-side `events` ledger). */
  liveEvents: InlineEvent[];
  /** Bumps when a turn fully completes — consumers refetch messages
   *  off this (works even if they were unmounted mid-turn). */
  turnSeq: number;
  error: string | null;
}

const EMPTY_RT: ConvRuntime = {
  streaming: false,
  liveAssistant: null,
  liveEvents: [],
  turnSeq: 0,
  error: null,
};

/** Args for `send` — everything the SSE loop needs, surface-agnostic
 *  (ChatSidebar and the `[cid]` page both call this). */
export interface SendArgs {
  cfg: PlatformConfig;
  conversationId: string;
  payload: string;
  userPrompt?: string | null;
  projectPrompt?: string | null;
  /** Prompt-attached references (R-200-180). Resolved server-side
   *  and inlined as a separate system block in the LLM context ;
   *  surfaced in the persisted `MessagePublic.references` (metadata
   *  only). 32K-token cap enforced server-side (413 on overflow). */
  references?: PromptReference[];
  /** Fired after a mutating DocGen tool (`create/update/delete_
   *  document`) completes — Working area refreshes its tree on it. */
  onMutatingTool?: () => void;
}

interface WorkspaceStore {
  /** Read the (possibly empty) UI slice for a project. */
  get: (projectId: string) => ProjectUiState;
  /** Shallow-merge a patch into a project's UI slice. */
  patch: (projectId: string, patch: Partial<ProjectUiState>) => void;
  /** Set the composer draft for ONE conversation. Functional +
   *  string-compare no-op (no stale closure, no persist loop). */
  setDraft: (projectId: string, conversationId: string, text: string) => void;
  /** Subscribe to runtime changes (any conversation). */
  subscribeRt: (cb: () => void) => () => void;
  /** Current runtime snapshot for a conversation (stable ref until
   *  it actually changes — safe for `useSyncExternalStore`). */
  getRt: (conversationId: string) => ConvRuntime;
  /** Run the SSE send-loop for a conversation. Provider-owned so it
   *  survives the caller unmounting. No-ops if already streaming. */
  send: (args: SendArgs) => Promise<void>;
}

const Ctx = createContext<WorkspaceStore | null>(null);

// STORAGE_KEY is **versioned** : a schema-breaking change (e.g. the
// 2026-05-19 split of `composerDraft: string` into `composerDrafts:
// Record<string, string>`) bumps this key so stale browser data from
// the previous schema is silently ignored — never crash a page on
// `undefined.someProp` because of a hydration mismatch.
const STORAGE_KEY = "aywizz.workspace.ui.v2";

type UiMap = Record<string, ProjectUiState>;

/** Coerce a possibly-foreign object (e.g. from an older schema in
 *  sessionStorage) into a valid `ProjectUiState`. Anything missing
 *  or of the wrong shape is replaced by the EMPTY default — this is
 *  what makes hydration crash-proof. */
function normaliseProjectSlice(raw: unknown): ProjectUiState {
  if (!raw || typeof raw !== "object") return EMPTY;
  const o = raw as Record<string, unknown>;
  const drafts = o.composerDrafts;
  return {
    activeConversationId:
      typeof o.activeConversationId === "string" ? o.activeConversationId : null,
    selectedRunId: typeof o.selectedRunId === "string" ? o.selectedRunId : null,
    selectedPath: typeof o.selectedPath === "string" ? o.selectedPath : null,
    composerDrafts:
      drafts && typeof drafts === "object" && !Array.isArray(drafts)
        ? (drafts as Record<string, string>)
        : {},
  };
}

/** Read the whole map from sessionStorage. Defensive : any parse
 *  failure, non-browser context, or schema mismatch yields an empty
 *  / normalised map (UI state is non-critical — never throw over it). */
function loadAll(): UiMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out: UiMap = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      out[k] = normaliseProjectSlice(v);
    }
    return out;
  } catch {
    return {};
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [map, setMap] = useState<UiMap>({});

  // Hydrate once from sessionStorage (survives F5). In an effect so
  // SSR / first paint is deterministic.
  useEffect(() => {
    const stored = loadAll();
    if (Object.keys(stored).length > 0) setMap(stored);
  }, []);

  // Persist (best-effort) on every change.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(map));
    } catch {
      // Quota / disabled storage — UI state is non-critical, ignore.
    }
  }, [map]);

  const patch = useCallback((projectId: string, p: Partial<ProjectUiState>) => {
    setMap((prev) => {
      const cur = prev[projectId] ?? EMPTY;
      const next = { ...cur, ...p };
      // No-op guard : return the SAME map reference when nothing
      // actually changed so React bails the re-render (controlled
      // inputs call setters with unchanged values constantly).
      if (
        next.activeConversationId === cur.activeConversationId &&
        next.selectedRunId === cur.selectedRunId &&
        next.selectedPath === cur.selectedPath &&
        next.composerDrafts === cur.composerDrafts
      ) {
        return prev;
      }
      return { ...prev, [projectId]: next };
    });
  }, []);

  const setDraft = useCallback((projectId: string, conversationId: string, text: string) => {
    setMap((prev) => {
      const cur = prev[projectId] ?? EMPTY;
      if ((cur.composerDrafts[conversationId] ?? "") === text) return prev;
      return {
        ...prev,
        [projectId]: {
          ...cur,
          composerDrafts: { ...cur.composerDrafts, [conversationId]: text },
        },
      };
    });
  }, []);

  const get = useCallback((projectId: string): ProjectUiState => map[projectId] ?? EMPTY, [map]);

  // --- Per-conversation SSE runtime (Increment 3b) ----------------
  // External store : runtimes live in a ref + a listener set, NOT in
  // React state, so a streamed token doesn't re-render the entire
  // protected subtree (the provider wraps every page). Consumers
  // subscribe to ONE conversation via `useConvRuntime` /
  // useSyncExternalStore ; unchanged conversations keep the same
  // snapshot reference so React bails their re-render.
  const runtimesRef = useRef<Record<string, ConvRuntime>>({});
  const rtListeners = useRef<Set<() => void>>(new Set());

  const setRt = useCallback((cid: string, fn: (r: ConvRuntime) => ConvRuntime) => {
    const prev = runtimesRef.current[cid] ?? EMPTY_RT;
    const next = fn(prev);
    if (next === prev) return;
    runtimesRef.current = { ...runtimesRef.current, [cid]: next };
    for (const l of rtListeners.current) l();
  }, []);

  const subscribeRt = useCallback((cb: () => void) => {
    rtListeners.current.add(cb);
    return () => {
      rtListeners.current.delete(cb);
    };
  }, []);

  const getRt = useCallback((cid: string): ConvRuntime => runtimesRef.current[cid] ?? EMPTY_RT, []);

  const send = useCallback(
    async (args: SendArgs) => {
      const {
        cfg,
        conversationId,
        payload,
        userPrompt,
        projectPrompt,
        references,
        onMutatingTool,
      } = args;
      if ((runtimesRef.current[conversationId] ?? EMPTY_RT).streaming) return;
      setRt(conversationId, (r) => ({
        ...r,
        streaming: true,
        liveAssistant: "",
        error: null,
      }));
      const client = new ApiClient(cfg);
      let buffer = "";
      try {
        await client.sendMessageStream(
          conversationId,
          payload,
          (chunk) => {
            buffer += chunk;
            setRt(conversationId, (r) => ({ ...r, liveAssistant: buffer }));
          },
          {
            userPrompt,
            projectPrompt,
            references,
            onInlineEvent: (evt) => {
              setRt(conversationId, (r) => {
                // Collapse a stage `done` onto its matching `running`
                // (same kind+name) so a phase shows one in-place row ;
                // everything else (tool_call, future kinds) appends.
                let evts = r.liveEvents;
                if (evt.kind === "stage" && evt.status === "done") {
                  const idx = evts.findIndex(
                    (e) => e.kind === "stage" && e.name === evt.name && e.status === "running",
                  );
                  if (idx !== -1) {
                    evts = evts.slice();
                    evts[idx] = evt;
                  } else {
                    evts = [...evts, evt];
                  }
                } else {
                  evts = [...evts, evt];
                }
                return { ...r, liveEvents: evts };
              });
              if (
                evt.kind === "tool_call" &&
                evt.status === "done" &&
                (evt.name === "create_document" ||
                  evt.name === "update_document" ||
                  evt.name === "delete_document")
              ) {
                onMutatingTool?.();
              }
            },
          },
        );
      } catch (err) {
        const msg = err instanceof ApiError ? `Send failed (${err.status})` : String(err);
        setRt(conversationId, (r) => ({ ...r, error: msg }));
      } finally {
        // Clear the transient text + bump turnSeq so a (possibly
        // remounted) consumer refetches the canonical server rows.
        setRt(conversationId, (r) => ({
          ...r,
          streaming: false,
          liveAssistant: null,
          turnSeq: r.turnSeq + 1,
        }));
      }
    },
    [setRt],
  );

  const store = useMemo<WorkspaceStore>(
    () => ({ get, patch, setDraft, subscribeRt, getRt, send }),
    [get, patch, setDraft, subscribeRt, getRt, send],
  );

  return <Ctx.Provider value={store}>{children}</Ctx.Provider>;
}

/** Hook : the UI-state slice for one project + a typed patcher.
 *  Safe to call outside the provider (returns an inert EMPTY slice +
 *  no-op patch) so a page can't crash if the provider is missing. */
export function useProjectUi(projectId: string): {
  ui: ProjectUiState;
  setUi: (patch: Partial<ProjectUiState>) => void;
  /** Per-conversation composer draft setter (no stale closure /
   *  persist loop — uses the store's functional setDraft). */
  setDraft: (conversationId: string, text: string) => void;
} {
  const store = useContext(Ctx);
  const ui = store ? store.get(projectId) : EMPTY;
  const setUi = useCallback(
    (p: Partial<ProjectUiState>) => store?.patch(projectId, p),
    [store, projectId],
  );
  const setDraft = useCallback(
    (conversationId: string, text: string) => store?.setDraft(projectId, conversationId, text),
    [store, projectId],
  );
  return { ui, setUi, setDraft };
}

/** Subscribe to ONE conversation's live SSE runtime (provider-owned,
 *  Increment 3b). Re-renders only when THIS conversation's runtime
 *  changes (useSyncExternalStore + stable per-conv snapshot ref).
 *  Returns the inert EMPTY runtime outside the provider / on SSR. */
export function useConvRuntime(conversationId: string): ConvRuntime {
  const store = useContext(Ctx);
  const subscribe = useCallback(
    (cb: () => void) => (store ? store.subscribeRt(cb) : () => {}),
    [store],
  );
  const snapshot = useCallback(
    () => (store ? store.getRt(conversationId) : EMPTY_RT),
    [store, conversationId],
  );
  const serverSnapshot = useCallback(() => EMPTY_RT, []);
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}

/** The provider-owned SSE send-loop. Survives the caller unmounting
 *  (tab navigation) — that is the whole point of Increment 3b. */
export function useWorkspaceSend(): (args: SendArgs) => Promise<void> {
  const store = useContext(Ctx);
  return useCallback(
    async (args: SendArgs) => {
      await store?.send(args);
    },
    [store],
  );
}
