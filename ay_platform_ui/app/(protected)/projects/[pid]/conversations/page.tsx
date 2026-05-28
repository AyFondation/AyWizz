// =============================================================================
// File: page.tsx
// Version: 5
// Path: ay_platform_ui/app/(protected)/projects/[pid]/conversations/page.tsx
//
// v5 (2026-05-19): the v4 auto-`router.replace` resume was a TRAP —
// the list became unreachable (breadcrumb / URL edit bounced right
// back into the conversation ; a Working-area-created conversation
// also hijacked it). Replaced by a user-initiated "↩ Resume last
// conversation" header link. The list is always a real destination.
//
// v4 (2026-05-19): [reverted] auto-resume redirect.
//
// Description: Conversations list (Phase D). One row per conversation
//              the caller owns, scoped to the active project. A single
//              "New conversation" button creates a placeholder
//              conversation and navigates to the chat ; the chat page
//              auto-renames it from the first user message so the
//              operator never types the same thing twice.
//
//              v3 : replaces the upfront title-input form with a
//              one-click button. Rationale : earlier UX asked for a
//              title BEFORE the chat, which operators repeatedly
//              filled with their actual question — then landed on an
//              empty composer and had to retype it. The chat page
//              now owns the title (auto-rename via PATCH on the
//              first send).
//
//              v2 (2026-05-11) : full implementation, replaces the
//              Phase D placeholder.
// =============================================================================

"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { useProjectUi } from "@/app/(protected)/workspace-store";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { Conversation } from "@/lib/types";

import { useConfigState } from "../../../../providers";

/** Placeholder title shipped with every new conversation. The chat
 *  page rewrites it via PATCH the first time the user sends a
 *  message ; until then it acts as a visible "untitled" marker in
 *  the list. Kept short on purpose — anything longer competes for
 *  attention with the actual conversation contents. */
const DEFAULT_NEW_CONVERSATION_TITLE = "New conversation";

type ListState =
  | { status: "loading" }
  | { status: "ready"; items: Conversation[] }
  | { status: "error"; message: string };

export default function ConversationsListPage() {
  const params = useParams<{ pid: string }>();
  const router = useRouter();
  const projectId = decodeURIComponent(params.pid);
  const configState = useConfigState();
  const [state, setState] = useState<ListState>({ status: "loading" });
  const [refreshCounter, setRefreshCounter] = useState(0);
  // Cross-nav store (Increment 3a). Coming back to the Conversations
  // tab offers a USER-INITIATED "Resume last conversation" link
  // (the previous auto-`router.replace` was a trap : the list became
  // unreachable — clicking the breadcrumb / editing the URL bounced
  // straight back in). The list is now always a real destination.
  const { ui } = useProjectUi(projectId);

  const apiClient = useMemo(() => {
    if (configState.status !== "ready") return null;
    return new ApiClient(configState.config);
  }, [configState]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: refresh trigger
  useEffect(() => {
    if (!apiClient) return;
    let cancelled = false;
    setState({ status: "loading" });
    apiClient
      .listConversations()
      .then((resp) => {
        if (cancelled) return;
        // Filter to project-scoped conversations (C3's list endpoint
        // returns ALL conversations owned by the caller across
        // projects ; we narrow client-side).
        const projectItems = resp.conversations.filter((c) => c.project_id === projectId);
        setState({ status: "ready", items: projectItems });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof ApiError ? `HTTP ${err.status}` : String(err);
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, refreshCounter]);

  // Last conversation still present in the list → expose a resume
  // link in the header (rendered below). Purely informational here ;
  // no auto-navigation (that was the trap).
  const resumableId =
    state.status === "ready" &&
    ui.activeConversationId &&
    state.items.some((c) => c.id === ui.activeConversationId)
      ? ui.activeConversationId
      : null;

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Conversations</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Chat with the platform's RAG-augmented assistant over this project's sources.
          </p>
        </div>
        <div className="flex items-center gap-4">
          {resumableId ? (
            <Link
              href={`/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(resumableId)}`}
              className="rounded-md border border-blue-300 bg-blue-50 px-3 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-100"
              data-testid="resume-last-conversation"
            >
              ↩ Resume last conversation
            </Link>
          ) : null}
          {state.status === "ready" ? (
            <p className="text-sm text-neutral-500" data-testid="conversations-count">
              {state.items.length} conversation{state.items.length === 1 ? "" : "s"}
            </p>
          ) : null}
        </div>
      </header>

      <NewConversationCard
        projectId={projectId}
        apiClient={apiClient}
        onCreated={(cid) => {
          router.push(
            `/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(cid)}`,
          );
        }}
      />

      <section className="mt-8">
        {state.status === "loading" ? (
          <p className="text-neutral-500">Loading conversations…</p>
        ) : state.status === "error" ? (
          <p className="text-red-700" role="alert">
            Failed to load: {state.message}
          </p>
        ) : state.items.length === 0 ? (
          <div
            className="rounded-lg border border-dashed border-neutral-300 p-10 text-center"
            data-testid="conversations-empty-state"
          >
            <p className="text-neutral-600">No conversations yet.</p>
            <p className="mt-1 text-sm text-neutral-500">
              Start one above to chat over the project's source corpus.
            </p>
          </div>
        ) : (
          <ConversationsList
            conversations={state.items}
            projectId={projectId}
            apiClient={apiClient}
            onChanged={() => setRefreshCounter((n) => n + 1)}
          />
        )}
      </section>
    </main>
  );
}

function NewConversationCard({
  projectId,
  apiClient,
  onCreated,
}: {
  projectId: string;
  apiClient: ApiClient | null;
  onCreated: (cid: string) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onStart(): Promise<void> {
    if (!apiClient) return;
    setSubmitting(true);
    setError(null);
    try {
      const conv = await apiClient.createConversation({
        title: DEFAULT_NEW_CONVERSATION_TITLE,
        project_id: projectId,
      });
      onCreated(conv.id);
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section
      className="mt-6 rounded-lg border border-neutral-200 bg-white p-5"
      data-testid="new-conversation-card"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">
            Start a new conversation
          </h3>
          <p className="mt-1 text-xs text-neutral-500">
            The conversation will be auto-named from your first question.
          </p>
        </div>
        <button
          type="button"
          onClick={onStart}
          disabled={submitting || !apiClient}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="new-conversation-submit"
        >
          {submitting ? "Creating…" : "+ New conversation"}
        </button>
      </div>
      {error ? (
        <p className="mt-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function ConversationsList({
  conversations,
  projectId,
  apiClient,
  onChanged,
}: {
  conversations: Conversation[];
  projectId: string;
  apiClient: ApiClient | null;
  onChanged: () => void;
}) {
  return (
    <ul className="space-y-2" data-testid="conversations-list">
      {conversations.map((c) => (
        <ConversationRow
          key={c.id}
          conversation={c}
          projectId={projectId}
          apiClient={apiClient}
          onChanged={onChanged}
        />
      ))}
    </ul>
  );
}

function ConversationRow({
  conversation,
  projectId,
  apiClient,
  onChanged,
}: {
  conversation: Conversation;
  projectId: string;
  apiClient: ApiClient | null;
  onChanged: () => void;
}) {
  const router = useRouter();
  const [deleting, setDeleting] = useState(false);
  const [renaming, setRenaming] = useState(false);

  async function onDelete(e: React.MouseEvent): Promise<void> {
    e.preventDefault();
    e.stopPropagation();
    if (!apiClient) return;
    if (!window.confirm(`Delete conversation "${conversation.title}"?`)) return;
    setDeleting(true);
    try {
      await apiClient.deleteConversation(conversation.id);
      onChanged();
    } catch (err) {
      window.alert(`Delete failed: ${String(err)}`);
      setDeleting(false);
    }
  }

  function onOpenInWorkingArea(e: React.MouseEvent): void {
    e.preventDefault();
    e.stopPropagation();
    router.push(
      `/projects/${encodeURIComponent(projectId)}/working-area?conv=${encodeURIComponent(conversation.id)}`,
    );
  }

  async function onRename(e: React.MouseEvent): Promise<void> {
    e.preventDefault();
    e.stopPropagation();
    if (!apiClient) return;
    const next = window.prompt("New title", conversation.title);
    if (next === null) return; // user cancelled
    const trimmed = next.trim();
    if (!trimmed || trimmed === conversation.title) return;
    setRenaming(true);
    try {
      await apiClient.updateConversation(conversation.id, { title: trimmed });
      onChanged();
    } catch (err) {
      window.alert(`Rename failed: ${String(err)}`);
    } finally {
      setRenaming(false);
    }
  }

  return (
    <li>
      <Link
        href={`/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversation.id)}`}
        className="flex items-center justify-between gap-3 rounded-md border border-neutral-200 bg-white px-4 py-3 transition-colors hover:bg-neutral-50"
        data-testid={`conversation-row-${conversation.id}`}
      >
        <div className="min-w-0">
          <p className="truncate font-medium text-neutral-900">{conversation.title}</p>
          <p className="mt-0.5 text-xs text-neutral-500">
            {conversation.message_count} message
            {conversation.message_count === 1 ? "" : "s"} ·{" "}
            {new Date(conversation.updated_at).toLocaleString()}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onRename}
            disabled={renaming}
            className="rounded-md border border-neutral-200 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
            data-testid={`conversation-rename-${conversation.id}`}
            title="Rename this conversation"
          >
            {renaming ? "…" : "Rename"}
          </button>
          <button
            type="button"
            onClick={onOpenInWorkingArea}
            className="rounded-md border border-blue-200 px-2 py-1 text-xs text-blue-700 hover:bg-blue-50"
            data-testid={`conversation-open-working-${conversation.id}`}
            title="Open this conversation in the Working area (3-pane workspace)"
          >
            Open in Working area
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={deleting}
            className="rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
            data-testid={`conversation-delete-${conversation.id}`}
          >
            {deleting ? "…" : "Delete"}
          </button>
        </div>
      </Link>
    </li>
  );
}
