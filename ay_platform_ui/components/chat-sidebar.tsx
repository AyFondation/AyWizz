// =============================================================================
// File: chat-sidebar.tsx
// Version: 7
// Path: ay_platform_ui/components/chat-sidebar.tsx
//
// v7 (2026-05-19): composer draft is per-conversation (keyed by
// active conversation id via the store's setDraft) — fixes a draft
// bleeding into a freshly-created / other conversation. Loaded on
// conversation switch ; empty for a conversation with no draft.
//
// v6 (2026-05-19): Increment 3b.1 — the SSE send-loop + its live
// state (streaming / liveAssistant / liveEvents) are now
// PROVIDER-owned per conversation (`useWorkspaceSend` +
// `useConvRuntime`). A generation keeps running and stays visible
// when this sidebar unmounts on tab nav ; a turnSeq effect refetches
// the canonical rows on completion (even after a remount).
//
// v5 (2026-05-19): Increment 3a follow-on — composer draft + active
// conversation mirrored into the cross-nav WorkspaceProvider store
// (restore-once on hydration, persist on change). A half-typed
// message and the open conversation survive a tab switch / F5.
// Description: Slim chat panel for the DocGen workspace 3-pane layout
//
//              v4 (2026-05-19): unified inline pipeline. The bespoke
//              tool-call strip is replaced by <InlineLog> fed by the
//              single `onInlineEvent` stream (live turn) AND by each
//              assistant message's persisted `events` audit ledger
//              (re-rendered on reload). One formatter, one channel.
//
//              v3 (2026-05-18): explicit "Génération en cours…"
//              indicator while streaming. The DocGen tool loop is
//              non-streaming until the very end, so the bare cursor
//              made the panel look frozen for the whole turn.
//
//              v2 (2026-05-18): the "Document tools" inline strip now
//              accumulates across the conversation's turns instead of
//              resetting on every send — the operator wants the full
//              tool trail, not only the latest turn. Reset is moved
//              to the conversation-switch effect (per-session
//              telemetry is meaningless for another conversation).
//
//              (tree / viewer / chat). Uses C3's existing
//              `POST /conversations/{cid}/messages` SSE endpoint via
//              `apiClient.sendMessageStream` but renders without the
//              heavier visual scaffolding of the full /conversations
//              page (no per-stage chips, no auto-rename, no per-user
//              prompt forwarding — those land on the dedicated
//              conversation page).
//
//              Surface :
//                - Conversation picker (header) : project conversations
//                  list + "+ New" button to create one in place.
//                - Messages list : full history of the active
//                  conversation. Live streaming chunk appended in
//                  place while the assistant types.
//                - Composer : textarea + Send. A `quoted` prop slot
//                  lets the parent inject a snippet (file selection)
//                  prepended as a markdown blockquote on the next
//                  message.
//
//              Profile-agnostic in code, but only mounted by the
//              DocGen workspace today.
// =============================================================================

"use client";

import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useConvRuntime, useProjectUi, useWorkspaceSend } from "@/app/(protected)/workspace-store";
import { InlineLog } from "@/components/inline-log";
import { ReferenceTray } from "@/components/reference-tray";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { Conversation, Message, PlatformConfig, PromptReference } from "@/lib/types";

const _DEFAULT_TITLE = "New conversation";

export interface QuotedSnippet {
  path: string;
  text: string;
}

export interface ChatSidebarProps {
  cfg: PlatformConfig;
  projectId: string;
  /** Quoted snippet injected by the viewer pane on text selection.
   *  When non-null, rendered as a chip above the input and prepended
   *  to the next outgoing message as a markdown blockquote. Cleared
   *  on send. Parent owns the state (so the same quote can be cleared
   *  by clicking elsewhere in the viewer). */
  quoted: QuotedSnippet | null;
  onClearQuote: () => void;
  /** Optional pre-selected conversation id (sourced from the URL
   *  `?conv=<id>` param by the working-area page). When the id is
   *  present in the project's conversation list, it becomes the
   *  active conversation on mount instead of the most-recent.
   *  Ignored if the id is unknown or belongs to another project. */
  initialConversationId?: string | null;
  /** Fired after a tool call that MUTATED the document corpus
   *  (create / update / delete) succeeds, so the parent can refresh
   *  the document tree without a manual reload. */
  onDocsMutated?: () => void;
  /** Operator-attached prompt references (R-200-180 / R-500-012).
   *  Parent owns the list ; sidebar renders the tray + forwards them
   *  to `send` ; `onClearReferences` is called after a successful
   *  send so the parent can drop them. */
  references?: PromptReference[];
  onClearReferences?: () => void;
  onRemoveReference?: (index: number) => void;
}

interface State {
  status: "loading" | "ready" | "error";
  conversations?: Conversation[];
  activeId?: string | null;
  messages?: Message[];
  error?: string;
}

export function ChatSidebar({
  cfg,
  projectId,
  quoted,
  onClearQuote,
  initialConversationId,
  onDocsMutated,
  references,
  onClearReferences,
  onRemoveReference,
}: ChatSidebarProps) {
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);
  // Cross-tab-nav UI store (Increment 3a). Mirror pattern : local
  // state stays authoritative ; restore once on hydration, persist
  // on change. Survives tab switch + F5 (sessionStorage). UI-only —
  // the audit trail stays the server-side `events` ledger.
  const { ui, setUi, setDraft } = useProjectUi(projectId);
  const loadedDraftForRef = useRef<string | null>(null);
  const convRestoredRef = useRef(false);
  const [state, setState] = useState<State>({ status: "loading" });
  const [composer, setComposer] = useState("");
  // Increment 3b : the SSE loop + its live state are PROVIDER-owned
  // (keyed by conversation), so a generation keeps running and stays
  // observable when this sidebar unmounts on tab nav. We only read
  // the runtime here ; `liveEvents` accumulates per-conversation in
  // the provider so switching conversation needs no manual reset.
  const send = useWorkspaceSend();
  const activeId = state.status === "ready" ? (state.activeId ?? null) : null;
  const rt = useConvRuntime(activeId ?? "");
  const streaming = rt.streaming;
  const liveAssistant = rt.liveAssistant;
  const liveEvents = rt.liveEvents;
  const lastTurnSeqRef = useRef(0);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  // Load conversations on mount. NO auto-create — that masks the
  // empty state and creates phantom conversations on stale mounts.
  // The operator clicks "+ New" explicitly when they want one.
  // The list is the SAME data source the dedicated /conversations
  // page reads (same `listConversations()` + same project_id filter)
  // so a conversation created in either place appears in both.
  useEffect(() => {
    let cancelled = false;
    apiClient
      .listConversations()
      .then((resp) => {
        if (cancelled) return;
        const own = resp.conversations.filter((c) => c.project_id === projectId);
        // Honour `initialConversationId` when it points to one of the
        // project's conversations ; otherwise fall back to the most
        // recent (own[0]) so navigating to /working-area without a
        // `?conv=` still lands on something useful.
        const preselected =
          initialConversationId && own.some((c) => c.id === initialConversationId)
            ? initialConversationId
            : own.length > 0
              ? own[0].id
              : null;
        setState({
          status: "ready",
          conversations: own,
          activeId: preselected,
        });
      })
      .catch((err) => {
        if (cancelled) return;
        setState({
          status: "error",
          error:
            err instanceof ApiError ? `Could not list conversations (${err.status})` : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, initialConversationId]);

  // Whenever the active conversation changes, refresh its messages.
  // (Inline-activity is per-conversation in the provider runtime —
  // switching conversation just reads the other slice, no reset.)
  useEffect(() => {
    if (state.status !== "ready" || !state.activeId) return;
    let cancelled = false;
    apiClient
      .listMessages(state.activeId)
      .then((resp) => {
        if (cancelled) return;
        setState((prev) => (prev.status === "ready" ? { ...prev, messages: resp.messages } : prev));
      })
      .catch(() => {
        if (cancelled) return;
        setState((prev) => (prev.status === "ready" ? { ...prev, messages: [] } : prev));
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, state.status, state.activeId]);

  // LOAD the active conversation's own draft : on conversation
  // switch (and once after hydration for the current one). A new /
  // other conversation has no entry → empty composer (fixes the
  // draft bleeding across conversations). `loadedDraftForRef`
  // ensures we don't clobber what the operator is typing.
  useEffect(() => {
    if (!activeId) return;
    const stored = ui.composerDrafts[activeId] ?? "";
    if (loadedDraftForRef.current !== activeId) {
      loadedDraftForRef.current = activeId;
      setComposer(stored);
    } else if (composer === "" && stored !== "") {
      // Hydration landed after we loaded an empty draft and the
      // operator hasn't typed yet — adopt the restored draft.
      setComposer(stored);
    }
  }, [activeId, ui.composerDrafts, composer]);

  // PERSIST the draft under the active conversation's id.
  useEffect(() => {
    if (activeId) setDraft(activeId, composer);
  }, [activeId, composer, setDraft]);

  // RESTORE the last active conversation once : only when there is
  // no explicit `?conv=` URL pre-selection, the stored id still
  // exists in the loaded list, and it differs from the current
  // pick. Guarded so it never fights a later manual switch.
  useEffect(() => {
    if (convRestoredRef.current) return;
    if (state.status !== "ready" || initialConversationId) return;
    const stored = ui.activeConversationId;
    if (!stored) return;
    convRestoredRef.current = true;
    if (stored !== state.activeId && state.conversations?.some((c) => c.id === stored)) {
      setState((prev) => (prev.status === "ready" ? { ...prev, activeId: stored } : prev));
    }
  }, [state, ui.activeConversationId, initialConversationId]);

  // PERSIST the active conversation.
  useEffect(() => {
    if (state.status === "ready" && state.activeId) {
      setUi({ activeConversationId: state.activeId });
    }
  }, [state, setUi]);

  // Auto-scroll to bottom on new content.
  // biome-ignore lint/correctness/useExhaustiveDependencies: state.messages + liveAssistant are scroll triggers
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [state.messages, liveAssistant]);

  const createNewConversation = useCallback(async () => {
    if (state.status !== "ready") return;
    try {
      const conv = await apiClient.createConversation({
        title: _DEFAULT_TITLE,
        project_id: projectId,
      });
      setState((prev) =>
        prev.status === "ready"
          ? {
              ...prev,
              conversations: [conv, ...(prev.conversations ?? [])],
              activeId: conv.id,
              messages: [],
            }
          : prev,
      );
    } catch (err) {
      setState((prev) =>
        prev.status === "ready"
          ? {
              ...prev,
              error:
                err instanceof ApiError
                  ? `Could not create conversation (${err.status})`
                  : String(err),
            }
          : prev,
      );
    }
  }, [apiClient, projectId, state.status]);

  const onSend = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (state.status !== "ready" || !state.activeId) return;
      const trimmed = composer.trim();
      if (!trimmed || streaming) return;
      const conversationId = state.activeId;
      const quotePrefix = quoted
        ? `> from \`${quoted.path}\`\n${quoted.text
            .split("\n")
            .map((l) => `> ${l}`)
            .join("\n")}\n\n`
        : "";
      const payload = `${quotePrefix}${trimmed}`;
      setComposer("");
      onClearQuote();
      // Optimistic user row (cosmetic — the turnSeq refetch effect
      // replaces it with the canonical server rows). If this sidebar
      // unmounts mid-turn it's irrelevant ; the server has the real
      // message and the provider keeps the SSE running.
      const optimisticUser: Message = {
        id: `opt-${Date.now()}`,
        conversation_id: conversationId,
        role: "user",
        content: payload,
        timestamp: new Date().toISOString(),
      };
      setState((prev) =>
        prev.status === "ready"
          ? { ...prev, messages: [...(prev.messages ?? []), optimisticUser] }
          : prev,
      );
      // PROVIDER-owned SSE loop : survives this component unmounting.
      // Not awaited — the runtime (`rt`) drives the live UI ; the
      // turnSeq effect refetches the canonical rows on completion.
      void send({
        cfg,
        conversationId,
        payload,
        references: references && references.length > 0 ? references : undefined,
        onMutatingTool: onDocsMutated,
      });
      if (references && references.length > 0 && onClearReferences) {
        onClearReferences();
      }
    },
    [
      composer,
      quoted,
      onClearQuote,
      state,
      streaming,
      send,
      cfg,
      onDocsMutated,
      references,
      onClearReferences,
    ],
  );

  // Post-turn refresh : when the provider finishes a turn for the
  // active conversation (turnSeq bumps), pull the canonical server
  // rows (replacing the optimistic user + the cleared live row).
  // Fires on remount too if a turn completed while we were away.
  useEffect(() => {
    if (state.status !== "ready" || !state.activeId) return;
    if (rt.turnSeq === lastTurnSeqRef.current) return;
    lastTurnSeqRef.current = rt.turnSeq;
    if (rt.turnSeq === 0) return;
    const cid = state.activeId;
    let cancelled = false;
    apiClient
      .listMessages(cid)
      .then((resp) => {
        if (cancelled) return;
        setState((prev) => (prev.status === "ready" ? { ...prev, messages: resp.messages } : prev));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [rt.turnSeq, state, apiClient]);

  if (state.status === "loading") {
    return <p className="px-4 py-6 text-sm text-neutral-500">Loading conversations…</p>;
  }
  if (state.status === "error") {
    return (
      <p className="px-4 py-6 text-sm text-red-600" role="alert">
        {state.error}
      </p>
    );
  }

  const active = state.conversations?.find((c) => c.id === state.activeId);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="docgen-chat-sidebar">
      <header className="flex items-center gap-2 border-b border-neutral-200 px-3 py-2 dark:border-neutral-700">
        <select
          value={state.activeId ?? ""}
          onChange={(e) =>
            setState((prev) =>
              prev.status === "ready" ? { ...prev, activeId: e.target.value } : prev,
            )
          }
          disabled={(state.conversations?.length ?? 0) === 0}
          className="min-w-0 flex-1 truncate rounded border border-neutral-300 bg-white px-2 py-1 text-xs dark:border-neutral-600 dark:bg-neutral-800 disabled:opacity-50"
          aria-label="Active conversation"
        >
          {(state.conversations?.length ?? 0) === 0 ? (
            <option value="" disabled>
              No conversation
            </option>
          ) : (
            state.conversations?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))
          )}
        </select>
        <button
          type="button"
          onClick={createNewConversation}
          className="shrink-0 rounded border border-neutral-300 bg-white px-2 py-1 text-xs hover:bg-neutral-50 dark:border-neutral-600 dark:bg-neutral-800 dark:hover:bg-neutral-700"
          title="Start a new conversation"
        >
          + New
        </button>
      </header>

      {(state.conversations?.length ?? 0) === 0 && (
        <div className="border-b border-neutral-200 bg-neutral-50 px-3 py-3 text-center text-xs text-neutral-600 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
          No conversation on this project yet. Click <span className="font-semibold">+ New</span>{" "}
          above to start one.
        </div>
      )}

      <ol className="flex-1 min-h-0 overflow-y-auto px-3 py-3" data-testid="chat-messages">
        {(state.messages ?? []).map((m) => (
          <MessageRow key={m.id} message={m} />
        ))}
        {liveEvents.length > 0 && (
          <li className="mb-2 list-none">
            <InlineLog events={liveEvents} />
          </li>
        )}
        {liveAssistant !== null && liveAssistant.length > 0 && (
          <MessageRow
            message={{
              id: "live",
              conversation_id: state.activeId ?? "",
              role: "assistant",
              content: `${liveAssistant}▍`,
              timestamp: new Date().toISOString(),
            }}
          />
        )}
        {streaming && (liveAssistant === null || liveAssistant.length === 0) && (
          // Explicit "working" indicator. The DocGen tool loop is
          // non-streaming until the very end (the model + each tool
          // round take seconds), so without this the panel looked
          // frozen for the whole turn. The amber strip above also
          // ticks as tools run, but this is the unambiguous signal.
          <li
            className="my-2 flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300"
            data-testid="chat-generating"
          >
            <span
              className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-blue-300 border-t-blue-700"
              aria-hidden="true"
            />
            <span>Génération en cours… (l'assistant réfléchit et utilise ses outils)</span>
          </li>
        )}
        <div ref={messagesEndRef} />
      </ol>

      {quoted && (
        <div className="border-t border-neutral-200 bg-blue-50 px-3 py-2 text-xs dark:border-neutral-700 dark:bg-blue-950">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate font-mono text-blue-700 dark:text-blue-300">
              Quoting <span className="font-semibold">{quoted.path}</span>
            </span>
            <button
              type="button"
              onClick={onClearQuote}
              className="text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
              title="Discard quoted snippet"
            >
              ×
            </button>
          </div>
          <pre className="mt-1 max-h-24 overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-blue-300 pl-2 text-[11px] text-neutral-800 dark:text-neutral-200">
            {quoted.text}
          </pre>
        </div>
      )}

      <form
        onSubmit={onSend}
        className="border-t border-neutral-200 bg-white p-3 dark:border-neutral-700 dark:bg-neutral-900"
      >
        {references && references.length > 0 && (
          <ReferenceTray references={references} onRemove={(idx) => onRemoveReference?.(idx)} />
        )}
        <textarea
          value={composer}
          onChange={(e) => setComposer(e.target.value)}
          rows={3}
          disabled={streaming || !active}
          placeholder={
            active ? "Ask the assistant to create or update a document…" : "No conversation yet"
          }
          className="w-full resize-none rounded-md border border-neutral-300 bg-white p-2 text-sm dark:border-neutral-600 dark:bg-neutral-800 dark:text-neutral-100"
          data-testid="chat-input"
        />
        <div className="mt-2 flex justify-end">
          <button
            type="submit"
            disabled={!composer.trim() || streaming || !active}
            className="rounded-md bg-blue-600 px-3 py-1 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-neutral-400"
            data-testid="chat-send"
          >
            {streaming ? "Sending…" : "Send"}
          </button>
        </div>
      </form>
    </div>
  );
}

function MessageRow({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return (
    <li
      className={[
        "mb-2 max-w-full rounded-md px-3 py-2 text-sm",
        isUser
          ? "ml-4 bg-blue-100 text-blue-900 dark:bg-blue-900 dark:text-blue-100"
          : "mr-4 bg-neutral-100 text-neutral-900 dark:bg-neutral-800 dark:text-neutral-100",
      ].join(" ")}
      data-testid={`chat-msg-${message.role}`}
    >
      <pre className="whitespace-pre-wrap break-words font-sans">{message.content}</pre>
      {/* Persisted inline-activity ledger (audit) — re-rendered from
          the server on reload, identical to the live render. */}
      {!isUser && message.events && message.events.length > 0 ? (
        <div className="mt-2">
          <InlineLog events={message.events} />
        </div>
      ) : null}
    </li>
  );
}
