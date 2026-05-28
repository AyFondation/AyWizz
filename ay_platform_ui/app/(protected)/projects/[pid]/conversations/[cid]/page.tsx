// =============================================================================
// File: page.tsx
// Version: 16
// Path: ay_platform_ui/app/(protected)/projects/[pid]/conversations/[cid]/page.tsx
//
// v16 (2026-05-19): Increment 3b.2 — SSE loop delegated to the
// provider's `send`. A generation now survives navigating AWAY from
// this `[cid]` route (changing tab / leaving via breadcrumb). A
// turnSeq effect refetches the canonical rows, flashes the
// "terminée" cue and refocuses the composer on completion ; rt.error
// is surfaced via the existing error slot.
//
// v15 (2026-05-19): composer draft is per-conversation (store
// `setDraft(conversationId, …)`) — no bleed across conversations.
// Breadcrumb `onClick` clears removed : the list no longer
// auto-redirects (trap fixed), so the marker must persist for the
// list's "↩ Resume last conversation" link.
//
// v14 (2026-05-19): Increment 3a — marks this conversation active
// in the cross-nav store (so re-entering the Conversations tab
// resumes it via the list page) + persists/restores the composer
// draft. Both breadcrumbs clear `activeConversationId` so the list
// is still reachable on purpose.
//
// v13 (2026-05-19): messages pane is now CONTENT-HEIGHT (capped +
// scroll), not viewport-filling. v12's justify-end only moved the
// empty void from below the last message to above the first ; the
// fix is to not stretch the pane at all — composer sits right under
// the content, no void either side, scrolls only when long.
//
// v12 (2026-05-19): two UX asks — (1) [superseded by v13] ;
// (2) explicit "Génération terminée — à vous" cue + composer
// auto-refocus on turn end (the spinner just vanishing was
// ambiguous about regaining control).
// Description: Chat view — message history + SSE-streamed assistant
//              replies. The composer at the bottom POSTs to C3's
//              `/messages` endpoint ; chunks land via the streaming
//              ApiClient method and are appended to a transient
//
//              v11 (2026-05-19): unified inline pipeline. The bespoke
//              StageEvent/ToolCallEvent state, the PipelineChip /
//              StageTimelineFull widgets and the separate amber strip
//              are all replaced by ONE <InlineLog> fed by the single
//              `onInlineEvent` stream (live turn, accumulated) AND by
//              each message's persisted `events` audit ledger
//              (re-rendered identically on reload).
//              "live" message. When the stream terminates ([DONE]),
//              the message is persisted and the conversation refetched
//              so the server-side id/timestamp take over.
//
//              v10 : explicit "Génération en cours…" indicator while
//              streaming (the in-flight dots alone read as frozen
//              during a long non-streaming DocGen tool loop).
//
//              v9 : the tool-call strip accumulates across the
//              conversation's turns (no per-send reset) so the full
//              document-tool trail stays visible — was previously
//              wiped on every new message, hiding earlier rounds.
//
//              v8 : DocGen tool-call inline strip. The dedicated
//              Conversations view now mirrors the ChatSidebar amber
//              "Document tools" panel — `event: tool_call` SSE events
//              for the in-flight turn render with ⏳/✅/❌ glyphs so
//              create/update/delete_document activity is visible here
//              too (previously only in the Working area sidebar).
//              create / update events additionally render an "Open
//              in Working area →" deep-link (`?conv=&path=`) so the
//              operator jumps straight to the affected document with
//              the originating conversation pre-selected.
//
//              v7 : the pipeline timeline now persists server-side
//              and is read back from `Message.stages` on navigation /
//              reload. Earlier the chip + panel only existed for the
//              freshly-streamed turn (live SSE state) and vanished
//              when the operator clicked another conversation and
//              came back.
//
//              v6 : two layout fixes on top of the v5 chip refactor.
//              (1) The trigram, pipeline chip and bubble are now on
//              the SAME LINE in the collapsed state (was two-stacked
//              before — pipeline above bubble — wasting vertical
//              space). Expanding the chip switches the row to the
//              previous stacked layout so the full timeline gets the
//              full row width.  (2) The streaming "thinking" dots are
//              folded directly into the live-row bubble instead of
//              being a separate `ThinkingBubble` element that
//              duplicated the live row for the first second of every
//              turn.
//
//              v5 : kills the 1-frame duplicate flash where both the
//              live row and the freshly-fetched server assistant row
//              briefly co-existed — a snapshot of the pre-send
//              message count gates the live row out as soon as the
//              server pair has landed. The pipeline timeline also
//              collapses to a tiny "+ 12.4s" chip when idle (full
//              panel still pops via click) instead of the previous
//              full-width header row.
//
//              v4 : the chat page is now responsible for naming a
//              freshly-created conversation. The conversations list
//              page creates it with a placeholder title ; on the
//              FIRST user message the chat PATCHes the conversation
//              with a derived title (first 60 chars of the message,
//              trimmed at a word boundary). Avoids the earlier UX
//              trap where the operator typed their question into
//              the "title" prompt and landed on an empty composer.
//              Also formats pipeline durations in seconds (e.g.
//              `1.4 s`) rather than raw milliseconds so the operator
//              gets an immediately interpretable timing.
//
//              v3 : user bubbles are right-aligned (avatar on the
//              right, tinted with the per-user `user_color` from C2
//              prefs), assistant bubbles stay left-aligned with the
//              neutral brand palette. The live `event: stage` SSE
//              stream renders next to the assistant avatar — visible
//              while running, collapsed behind a `+` toggle after
//              `[DONE]` so it does not consume vertical real estate
//              once the reply is on screen.
//
//              v2 : fetches user preferences (effective user_prompt)
//              and the active project (effective project system_prompt)
//              at mount, forwards both on every chat message so C3 can
//              prepend them ahead of the RAG context.
// =============================================================================

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useConvRuntime, useProjectUi, useWorkspaceSend } from "@/app/(protected)/workspace-store";
import { Avatar, ThinkingDots } from "@/components/avatar";
import { InlineLog, ModifiedDocsLinks } from "@/components/inline-log";
import { ApiClient, ApiError } from "@/lib/apiClient";
import { fullNameForTooltip, getEffectiveTrigram } from "@/lib/preferences";
import type { Conversation, InlineEvent, Message, MessageRole } from "@/lib/types";

import { useAuth } from "../../../../../auth-provider";
import { useConfigState } from "../../../../../providers";

/** Placeholder title set by the conversations list page when the
 *  operator clicks "+ New conversation". The chat page replaces it on
 *  the first user message — see `deriveTitleFromMessage` below. Must
 *  stay in sync with the constant of the same name in the list page. */
const DEFAULT_NEW_CONVERSATION_TITLE = "New conversation";

type LoadState =
  | { status: "loading" }
  | { status: "ready"; conversation: Conversation; messages: Message[] }
  | { status: "not-found" }
  | { status: "error"; message: string };

/** Local rendering model — server messages + an optional live (in-flight) one. */
interface DisplayMessage {
  role: MessageRole;
  content: string;
  /** Stable key for React. Server messages use their id ; the live
   *  streamed one uses "live". */
  key: string;
  /** Unified inline-activity ledger for this message — live events
   *  for the in-flight turn, or the persisted `events` audit ledger
   *  for server-restored messages. Rendered via <InlineLog>. */
  events?: InlineEvent[];
  /** True while the SSE is open. */
  inFlight?: boolean;
}

export default function ChatPage() {
  const params = useParams<{ pid: string; cid: string }>();
  const projectId = decodeURIComponent(params.pid);
  const conversationId = decodeURIComponent(params.cid);
  const configState = useConfigState();
  const { state: authState } = useAuth();
  // Cross-nav store (Increment 3a). Mark THIS conversation active so
  // returning to the Conversations tab resumes it (the list page
  // reads `activeConversationId`). Composer draft persisted too.
  const { ui, setUi, setDraft } = useProjectUi(projectId);
  const composerRestoredRef = useRef(false);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [composer, setComposer] = useState("");
  // Increment 3b.2 : SSE + its live state are PROVIDER-owned per
  // conversation, so a generation keeps running and stays observable
  // when the operator navigates AWAY from this `[cid]` route. We
  // read the runtime here ; `liveEvents` accumulates per-conv inside
  // the provider (audit trail still the server `events` ledger).
  const send = useWorkspaceSend();
  const rt = useConvRuntime(conversationId);
  const streaming = rt.streaming;
  const liveAssistant = rt.liveAssistant;
  const liveEvents = rt.liveEvents;
  const lastTurnSeqRef = useRef(0);
  const [error, setError] = useState<string | null>(null);
  // Behavioural prompts resolved by C2 — fetched once on mount and
  // forwarded on every chat message. `null` means "not yet loaded" ;
  // empty string means "loaded but no effective text" (skip).
  const [userPrompt, setUserPrompt] = useState<string | null>(null);
  const [projectPrompt, setProjectPrompt] = useState<string | null>(null);
  const [userColor, setUserColor] = useState<string | null>(null);
  // Brief "turn finished — control is back to you" cue. Set when a
  // send completes, auto-cleared after a few seconds. Complements
  // the in-flight spinner so the operator gets an explicit end
  // signal (the spinner just vanishing read as ambiguous).
  const [justFinished, setJustFinished] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  // Snapshot of `state.messages.length` taken just BEFORE the
  // optimistic user row is pushed. We use it to suppress the live
  // assistant row in the brief window between `loadAll()` returning
  // (state has the server-persisted user + assistant ; messages.length
  // == base + 2) and `setLiveAssistant(null)` clearing the transient
  // row — without this guard, the assistant reply rendered twice for
  // a frame, visually flashing.
  const messageCountAtSendRef = useRef(0);

  // User identity → trigram + tooltip name. Memoised so toggling
  // chat state doesn't reread localStorage on every render.
  const userIdentity = useMemo(() => {
    if (authState.status !== "authenticated") {
      return { trigram: "USR", fullName: "Anonymous" };
    }
    return {
      trigram: getEffectiveTrigram(authState.claims),
      fullName: fullNameForTooltip(authState.claims),
    };
  }, [authState]);

  // Brand name for the assistant trigram + tooltip — pulled from
  // /ux/config so a deployment re-branding flows through to chat.
  const assistantIdentity = useMemo(() => {
    if (configState.status !== "ready") {
      return { trigram: "AYW", fullName: "AyWizz assistant" };
    }
    const short = configState.config.ux.brand.short_name;
    const tri =
      short
        .replace(/[^A-Za-z0-9]/g, "")
        .slice(0, 3)
        .toUpperCase() || "AYW";
    return {
      trigram: tri,
      fullName: `${configState.config.ux.brand.name} assistant`,
    };
  }, [configState]);

  const apiClient = useMemo(() => {
    if (configState.status !== "ready") return null;
    return new ApiClient(configState.config);
  }, [configState]);

  const loadAll = useCallback(async (): Promise<void> => {
    if (!apiClient) return;
    try {
      const conv = await apiClient.getConversation(conversationId);
      const msgs = await apiClient.listMessages(conversationId);
      setState({
        status: "ready",
        conversation: conv,
        messages: msgs.messages,
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setState({ status: "not-found" });
        return;
      }
      const message = err instanceof Error ? err.message : String(err);
      setState({ status: "error", message });
    }
  }, [apiClient, conversationId]);

  useEffect(() => {
    let cancelled = false;
    if (apiClient && !cancelled) loadAll();
    return () => {
      cancelled = true;
    };
  }, [apiClient, loadAll]);

  // Resolve the effective behavioural prompts + user colour once.
  // Either call may fail (auth-mode='none' deployments don't expose
  // user prefs) — we swallow errors and fall back to defaults rather
  // than blocking chat.
  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    apiClient
      .getUserPreferences()
      .then((p) => {
        if (cancelled) return;
        setUserPrompt(p.user_prompt);
        setUserColor(p.user_color);
      })
      .catch(() => {
        if (!cancelled) {
          setUserPrompt("");
          setUserColor(null);
        }
      });
    apiClient
      .getProject(projectId)
      .then((p) => {
        if (!cancelled) setProjectPrompt(p.system_prompt);
      })
      .catch(() => {
        if (!cancelled) setProjectPrompt("");
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId]);

  // Auto-scroll to bottom whenever the message list grows OR the
  // live-streamed chunk advances. Biome can't see the deps are read
  // (we rely on them only as triggers) ; the suppression is on
  // purpose.
  // biome-ignore lint/correctness/useExhaustiveDependencies: state + liveAssistant + liveEvents are scroll triggers
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [state, liveAssistant, liveEvents]);

  // Auto-dismiss the "turn finished" cue after a few seconds so it
  // doesn't linger ; cleared early on the next send.
  useEffect(() => {
    if (!justFinished) return;
    const t = setTimeout(() => setJustFinished(false), 4000);
    return () => clearTimeout(t);
  }, [justFinished]);

  // Mark this conversation as the "last active" one so the
  // Conversations list can offer a user-initiated resume link (no
  // auto-redirect — that was a trap).
  useEffect(() => {
    setUi({ activeConversationId: conversationId });
  }, [conversationId, setUi]);

  // RESTORE this conversation's OWN draft once the store hydrates
  // (only if the operator hasn't started typing). PERSIST under the
  // conversation id so it never bleeds into another conversation.
  useEffect(() => {
    if (composerRestoredRef.current) return;
    const stored = ui.composerDrafts[conversationId] ?? "";
    if (stored && composer === "") {
      composerRestoredRef.current = true;
      setComposer(stored);
    }
  }, [ui.composerDrafts, conversationId, composer]);
  useEffect(() => {
    setDraft(conversationId, composer);
  }, [conversationId, composer, setDraft]);

  // Provider-driven end-of-turn : when a turn completes for this
  // conversation (turnSeq bumps), refetch the canonical rows, flash
  // the "terminée — à vous" cue and refocus the composer. Fires on
  // remount too if a turn completed while we were on another route.
  useEffect(() => {
    if (rt.turnSeq === lastTurnSeqRef.current) return;
    lastTurnSeqRef.current = rt.turnSeq;
    if (rt.turnSeq === 0) return;
    void loadAll();
    setJustFinished(true);
    composerRef.current?.focus();
  }, [rt.turnSeq, loadAll]);

  // Surface provider-side send errors (401/429/network) so the
  // operator sees them in the existing error slot.
  useEffect(() => {
    if (rt.error) setError(`Send failed: ${rt.error}`);
  }, [rt.error]);

  function onSend(e: FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    if (!apiClient || configState.status !== "ready") return;
    if (!composer.trim() || streaming) return;
    if (state.status !== "ready") return;

    const userText = composer.trim();
    setComposer("");
    setError(null);
    // Snapshot the message count BEFORE the optimistic add — used by
    // the render path to know when the server-persisted (user +
    // assistant) pair has arrived and the live row should be hidden.
    messageCountAtSendRef.current = state.messages.length;

    // First-message auto-rename : best-effort, fire-and-forget. The
    // PATCH runs even if this page unmounts (httpx in flight) — UI
    // catches up on the next mount via getConversation.
    const isPlaceholderTitle = state.conversation.title === DEFAULT_NEW_CONVERSATION_TITLE;
    if (isPlaceholderTitle) {
      const derived = deriveTitleFromMessage(userText);
      void apiClient
        .updateConversation(conversationId, { title: derived })
        .then((updated) => {
          setState((prev) => {
            if (prev.status !== "ready") return prev;
            return { ...prev, conversation: updated };
          });
        })
        .catch(() => {
          // Rename failure is benign — the conversation keeps its
          // placeholder title and the chat continues. No UX surface.
        });
    }

    // Optimistic user row (cosmetic — the turnSeq effect refetches
    // the canonical server rows on completion ; if this page
    // unmounts mid-turn it's irrelevant, the server has the real
    // message and the provider keeps the SSE alive).
    const optimisticUser: Message = {
      id: `optimistic-${Date.now()}`,
      conversation_id: conversationId,
      role: "user",
      content: userText,
      timestamp: new Date().toISOString(),
    };
    setState({
      ...state,
      messages: [...state.messages, optimisticUser],
    });
    setJustFinished(false);

    // PROVIDER-owned SSE loop : survives this route unmounting.
    // Not awaited — the runtime drives the live UI ; the turnSeq
    // effect refocuses + refetches messages on completion.
    void send({
      cfg: configState.config,
      conversationId,
      payload: userText,
      userPrompt,
      projectPrompt,
    });
  }

  if (state.status === "loading") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-neutral-500">Loading conversation…</p>
      </main>
    );
  }

  if (state.status === "not-found") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <h2 className="text-2xl font-semibold">Conversation not found</h2>
        <Link
          href={`/projects/${encodeURIComponent(projectId)}/conversations`}
          className="mt-6 inline-block rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
        >
          ← Back to conversations
        </Link>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-red-700" role="alert">
          Failed to load: {state.message}
        </p>
      </main>
    );
  }

  // Build the visible row list. The most recent assistant row that
  // *exists in state.messages* receives the captured stage timeline
  // (if we have one). The live in-flight row receives the growing
  // tokens. After [DONE] but before loadAll resolves, both can
  // overlap briefly ; once loadAll finishes we drop the live row
  // (via setLiveAssistant(null)) and the timeline naturally moves
  // onto the persisted assistant message.
  const lastAssistantIdx = (() => {
    for (let i = state.messages.length - 1; i >= 0; i -= 1) {
      if (state.messages[i].role === "assistant") return i;
    }
    return -1;
  })();

  const display: DisplayMessage[] = state.messages.map((m, idx) => {
    // Events source precedence : the live-accumulated inline log for
    // the latest assistant row WHEN this is the freshly-streamed turn
    // (not yet refetched) ; the server-persisted `events` audit
    // ledger otherwise. The persisted copy survives navigation /
    // reload ; the live array covers the [DONE]→loadAll() window.
    const liveCandidate =
      idx === lastAssistantIdx && liveEvents.length > 0 && !streaming ? liveEvents : undefined;
    return {
      role: m.role,
      content: m.content,
      key: m.id,
      events: liveCandidate ?? m.events ?? undefined,
    };
  });
  // Live (in-flight) assistant row : show ONLY while the server's
  // persisted assistant hasn't landed yet. After `loadAll()` resolves
  // post-[DONE], state.messages contains the server-side user +
  // assistant pair (length jumps by 2 from the pre-send snapshot) and
  // we suppress the live row to avoid a 1-frame duplicate flash.
  const serverAssistantArrived = state.messages.length >= messageCountAtSendRef.current + 2;
  if (liveAssistant !== null && !serverAssistantArrived) {
    display.push({
      role: "assistant",
      content: liveAssistant,
      key: "live",
      events: liveEvents.length > 0 ? liveEvents : undefined,
      inFlight: streaming,
    });
  }

  return (
    <main className="flex w-full flex-col px-6 py-6" data-testid="chat-view">
      <header className="flex items-baseline justify-between gap-3">
        <div>
          <nav className="text-xs text-neutral-500" aria-label="Breadcrumb">
            <Link
              href={`/projects/${encodeURIComponent(projectId)}/conversations`}
              className="hover:underline"
            >
              ← Conversations
            </Link>
          </nav>
          <h2 className="mt-1 text-xl font-semibold tracking-tight">{state.conversation.title}</h2>
        </div>
        <span className="text-xs text-neutral-500">
          {state.conversation.message_count} message
          {state.conversation.message_count === 1 ? "" : "s"}
        </span>
      </header>

      {/* Content-height (not viewport-filling) so there is NO empty
          void — neither below the last message nor above the first.
          The pane grows with the conversation and only starts to
          scroll internally once it would exceed the cap ; the
          composer always sits directly under the content. */}
      <section
        className="mt-4 max-h-[calc(100vh-16rem)] overflow-y-auto rounded-lg border border-neutral-200 bg-neutral-50 p-4"
        data-testid="messages-pane"
      >
        <div className="flex flex-col gap-3">
          {display.length === 0 ? (
            <p className="text-sm text-neutral-500">
              No messages yet. Ask anything below — replies are augmented with this project's RAG
              sources.
            </p>
          ) : (
            <ul className="space-y-3">
              {display.map((m) => (
                <li key={m.key}>
                  <MessageBubble
                    role={m.role}
                    content={m.content}
                    user={userIdentity}
                    assistant={assistantIdentity}
                    userColor={userColor}
                    events={m.events}
                    inFlight={m.inFlight}
                    projectId={projectId}
                    conversationId={conversationId}
                  />
                </li>
              ))}
            </ul>
          )}
          {streaming ? (
            // Explicit "working" indicator. In DocGen mode the tool
            // loop is non-streaming until the end, so the in-flight
            // bubble's dots alone read as "frozen" for a long turn.
            <div
              className="flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700"
              data-testid="chat-generating"
            >
              <span
                className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-blue-300 border-t-blue-700"
                aria-hidden="true"
              />
              <span>Génération en cours… (l'assistant réfléchit et utilise ses outils)</span>
            </div>
          ) : justFinished ? (
            // Explicit end-of-turn signal : the spinner just vanishing
            // was ambiguous ("is it done? do I have control back?").
            <div
              className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
              data-testid="chat-finished"
            >
              <span aria-hidden="true">✓</span>
              <span>Génération terminée — à vous (le champ est de nouveau actif).</span>
            </div>
          ) : null}
          <div ref={bottomRef} />
        </div>
      </section>

      {error ? (
        <p className="mt-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}

      <form onSubmit={onSend} className="mt-3 flex items-end gap-2" data-testid="composer">
        <textarea
          ref={composerRef}
          value={composer}
          onChange={(e) => {
            setComposer(e.target.value);
          }}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter inserts a newline (standard
            // chat UX). Cmd/Ctrl+Enter kept as a secondary trigger
            // for keyboard-power users used to other apps.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              (e.currentTarget.form as HTMLFormElement | null)?.requestSubmit();
            }
          }}
          rows={2}
          placeholder="Ask a question (Enter to send, Shift+Enter for a new line)…"
          className="flex-1 resize-y rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2"
          disabled={streaming}
          data-testid="composer-input"
        />
        <button
          type="submit"
          disabled={streaming || !composer.trim()}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="composer-send"
        >
          {streaming ? "Streaming…" : "Send"}
        </button>
      </form>
    </main>
  );
}

interface Identity {
  trigram: string;
  fullName: string;
}

function MessageBubble({
  role,
  content,
  user,
  assistant,
  userColor,
  events,
  inFlight,
  projectId,
  conversationId,
}: {
  role: MessageRole;
  content: string;
  user: Identity;
  assistant: Identity;
  userColor: string | null;
  events?: InlineEvent[];
  inFlight?: boolean;
  projectId?: string;
  conversationId?: string;
}) {
  const isUser = role === "user";
  const id = isUser ? user : assistant;
  const inlineEvents = !isUser && events ? events : [];

  // Per-user bubble tint — inline RGB string built from the hex so
  // Tailwind's JIT doesn't need to know about user-supplied colours.
  const userBubbleStyle: React.CSSProperties | undefined =
    isUser && userColor && /^#[0-9a-fA-F]{6}$/.test(userColor)
      ? {
          backgroundColor: rgba(userColor, 0.12),
          borderColor: rgba(userColor, 0.35),
          color: shade(userColor, 0.55),
        }
      : undefined;
  const bubbleClasses = [
    "rounded-lg border px-4 py-2 text-sm whitespace-pre-wrap",
    isUser
      ? userColor
        ? ""
        : "border-blue-200 bg-blue-50 text-blue-900"
      : "border-neutral-200 bg-white text-neutral-900",
  ].join(" ");
  // Empty assistant bubble while inFlight → animated ThinkingDots.
  const showThinkingDots = !isUser && inFlight === true && (content === "" || content == null);
  const bubbleContent = content ? content : isUser ? "" : showThinkingDots ? <ThinkingDots /> : "…";

  return (
    <div
      className={["flex items-start gap-3", isUser ? "flex-row-reverse" : "flex-row"].join(" ")}
      data-testid={`message-${role}`}
    >
      <Avatar
        trigram={id.trigram}
        fullName={id.fullName}
        variant={isUser ? "user" : "assistant"}
        color={isUser ? userColor : null}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className={bubbleClasses} style={userBubbleStyle}>
          {bubbleContent}
        </div>
        {/* Unified inline-activity log (stages + tool calls + future
            kinds) — live for the in-flight turn, persisted audit
            ledger on reload. One formatter registry. */}
        {inlineEvents.length > 0 ? <InlineLog events={inlineEvents} /> : null}
        {/* #5 — below the response, a compact versioned deep-link per
            modified document (the inline log itself no longer carries
            per-tool links). */}
        <ModifiedDocsLinks
          events={inlineEvents}
          projectId={projectId}
          conversationId={conversationId}
        />
      </div>
    </div>
  );
}

/** Derive a conversation title from the user's first message. Trims
 *  whitespace, collapses inner runs of whitespace, and clips to 60
 *  characters at a word boundary (with an ellipsis when truncated).
 *  Falls back to the placeholder when the message yields nothing
 *  printable so we never persist an empty title. */
function deriveTitleFromMessage(message: string): string {
  const cleaned = message.trim().replace(/\s+/g, " ");
  if (cleaned.length === 0) return DEFAULT_NEW_CONVERSATION_TITLE;
  const MAX = 60;
  if (cleaned.length <= MAX) return cleaned;
  // Truncate at the last word boundary within the budget so we don't
  // chop mid-word. If no whitespace under MAX (rare : a long URL or
  // identifier), hard-cut at MAX-1 + ellipsis.
  const window = cleaned.slice(0, MAX);
  const lastSpace = window.lastIndexOf(" ");
  const cut = lastSpace > MAX / 2 ? lastSpace : MAX - 1;
  return `${cleaned.slice(0, cut).trimEnd()}…`;
}

// ---------------------------------------------------------------------------
// Color helpers — duplicated from `components/avatar.tsx` on purpose
// (avoiding a lib/colors module for two consumers each with their own
// alpha needs).
// ---------------------------------------------------------------------------

function rgba(hex: string, alpha: number): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex);
  if (!m) return hex;
  const n = Number.parseInt(m[1], 16);
  const r = (n >> 16) & 0xff;
  const g = (n >> 8) & 0xff;
  const b = n & 0xff;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function shade(hex: string, ratio: number): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex);
  if (!m) return hex;
  const n = Number.parseInt(m[1], 16);
  const r = Math.round(((n >> 16) & 0xff) * (1 - ratio));
  const g = Math.round(((n >> 8) & 0xff) * (1 - ratio));
  const b = Math.round((n & 0xff) * (1 - ratio));
  return `rgb(${r}, ${g}, ${b})`;
}
