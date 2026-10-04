// =============================================================================
// File: conversation-rail.tsx
// Version: 1
// Path: ay_platform_ui/components/workbench/conversation-rail.tsx
// Description: The workbench's conversation composer — 500-SPEC R-500-016,
//              realising 310-SPEC R-310-221.
//
//              THE SELECTION IS SUBMITTED, NOT RETYPED. The composer reads
//              the shared selection and sends it alongside the message, so
//              the user never restates an identifier already on screen —
//              which is the cost R-310-221 exists to remove. The selection
//              is also SHOWN above the input, because a context the user
//              cannot see is a context they will restate anyway out of
//              doubt.
//
//              This rail is the composer and the turn list only. It does not
//              own the LLM round trip: the page passes `onSend`, so the same
//              component works against the existing chat surface without
//              this module acquiring a second copy of that plumbing.
//
// @relation implements:R-500-016
// =============================================================================

"use client";

import { useState } from "react";

import { useSelection } from "./selection-context";

export interface RailTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
  /** The selection this turn was sent with, so the transcript records what
   *  the agent was pointed at rather than only what was typed. */
  context?: string;
}

export function ConversationRail({
  turns,
  onSend,
  busy = false,
}: {
  turns: RailTurn[];
  onSend: (message: string, context: string) => void;
  busy?: boolean;
}) {
  const { asPromptContext, isEmpty } = useSelection();
  const [draft, setDraft] = useState("");
  const context = asPromptContext();

  const submit = () => {
    const message = draft.trim();
    if (!message || busy) return;
    onSend(message, context);
    setDraft("");
  };

  return (
    <section
      className="flex h-full flex-col overflow-hidden border-l border-neutral-200 bg-white"
      aria-label="Conversation"
      data-testid="conversation-rail"
    >
      <header className="border-b border-neutral-200 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-700">
          Assistant
        </h2>
      </header>

      <ol className="flex-1 space-y-2 overflow-y-auto px-3 py-2">
        {turns.length === 0 ? (
          <li className="text-xs text-neutral-500" data-testid="rail-empty">
            Ask the assistant to draft, review or explain what is selected.
          </li>
        ) : (
          turns.map((turn) => (
            <li
              key={turn.id}
              className={`rounded px-2 py-1.5 text-xs ${
                turn.role === "user"
                  ? "bg-neutral-100 text-neutral-900"
                  : "bg-sky-50 text-neutral-900"
              }`}
              data-testid={`turn-${turn.id}`}
            >
              {turn.context ? (
                <span className="mb-0.5 block text-[11px] text-neutral-500">
                  re: {turn.context}
                </span>
              ) : null}
              {turn.text}
            </li>
          ))
        )}
      </ol>

      <footer className="border-t border-neutral-200 px-3 py-2">
        {/* Shown, not just sent: an invisible context gets restated anyway. */}
        <p className="mb-1 text-[11px] text-neutral-500" data-testid="rail-context">
          {isEmpty ? "Nothing selected" : `Context: ${context}`}
        </p>
        <div className="flex gap-2">
          <label className="sr-only" htmlFor="workbench-composer">
            Message the assistant
          </label>
          <textarea
            id="workbench-composer"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            rows={2}
            className="flex-1 resize-none rounded border border-neutral-300 px-2 py-1 text-xs"
            placeholder="Draft an answer for the selected requirement…"
            data-testid="rail-input"
          />
          <button
            type="button"
            onClick={submit}
            disabled={busy || draft.trim().length === 0}
            className="self-end rounded bg-neutral-900 px-2.5 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300"
            data-testid="rail-send"
          >
            Send
          </button>
        </div>
      </footer>
    </section>
  );
}
