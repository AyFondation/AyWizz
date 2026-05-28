// =============================================================================
// File: run-trace.tsx
// Version: 1
// Path: ay_platform_ui/components/run-trace.tsx
// Description: <RunTrace> renders a live, newest-first timeline of the
//              C4 orchestrator's TraceEvent ledger (R-200-200..201). The
//              parent hydrates `events` from `OrchestratorRun.trace`
//              (sliding window of 200) and refreshes it via the existing
//              polling loop. Older events are loaded lazily on demand
//              via the `onLoadMore` callback that wraps
//              `apiClient.readOrchestratorTrace({ before })`.
//
//              Kind formatters live in `_KIND_LABEL` / `_KIND_ICON` —
//              add a kind = add an entry (same pattern as the unified
//              <InlineLog> registry in `inline-log.tsx`).
// =============================================================================

"use client";

import { useState } from "react";

import type { TraceEvent, TraceEventKind } from "@/lib/types";

interface RunTraceProps {
  events: TraceEvent[];
  /** When true, the parent owns the "load more" affordance and will
   *  call `onLoadMore` with the timestamp of the oldest visible event. */
  canLoadMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: (beforeIso: string) => void;
}

const _KIND_ICON: Record<TraceEventKind, string> = {
  "agent-dispatch": "▶",
  "gate-eval": "✓",
  "fix-attempt": "⟳",
  "phase-boundary": "→",
  "steer-applied": "✎",
};

const _KIND_LABEL: Record<TraceEventKind, string> = {
  "agent-dispatch": "Agent",
  "gate-eval": "Gate",
  "fix-attempt": "Fix",
  "phase-boundary": "Phase",
  "steer-applied": "Steer",
};

function _formatRelativeTs(iso: string): string {
  const ts = new Date(iso);
  if (Number.isNaN(ts.getTime())) return iso;
  const diffMs = Date.now() - ts.getTime();
  if (diffMs < 1_000) return "just now";
  if (diffMs < 60_000) return `${Math.floor(diffMs / 1_000)}s ago`;
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}m ago`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)}h ago`;
  return ts.toLocaleString();
}

function _formatDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) return null;
  if (ms < 1_000) return `${ms}ms`;
  return `${(ms / 1_000).toFixed(1)}s`;
}

export function RunTrace({
  events,
  canLoadMore,
  loadingMore,
  onLoadMore,
}: RunTraceProps): React.JSX.Element {
  if (events.length === 0) {
    return (
      <div className="rounded-md border border-zinc-200 bg-zinc-50 p-4 text-xs text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400">
        No trace events yet. Polling will populate this timeline as the run progresses.
      </div>
    );
  }
  const oldestIso = events[events.length - 1]?.ts;
  return (
    <div className="rounded-md border border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-900">
      <ol className="divide-y divide-zinc-100 dark:divide-zinc-800">
        {events.map((ev) => {
          const okClass =
            ev.ok === true
              ? "text-green-700 dark:text-green-400"
              : ev.ok === false
                ? "text-red-700 dark:text-red-400"
                : "text-zinc-600 dark:text-zinc-300";
          const duration = _formatDuration(ev.duration_ms);
          // Composite key — `ts` may collide on bursty appends, so
          // combine with kind + label which together are unique enough
          // for a 200-event window. (Append-only ledger, no reordering.)
          const key = `${ev.ts}|${ev.kind}|${ev.label}`;
          return (
            <li key={key} className="flex items-start gap-3 px-3 py-2 text-xs">
              <span
                className={`mt-0.5 inline-block w-4 text-center font-mono ${okClass}`}
                aria-hidden
              >
                {_KIND_ICON[ev.kind]}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="font-medium uppercase tracking-wide text-[10px] text-zinc-500 dark:text-zinc-400">
                    {_KIND_LABEL[ev.kind]}
                  </span>
                  <span className="text-[10px] text-zinc-400">{ev.phase}</span>
                  <span className={`truncate ${okClass}`}>{ev.label}</span>
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[10px] text-zinc-400 dark:text-zinc-500">
                  <span>{_formatRelativeTs(ev.ts)}</span>
                  {duration && <span>· {duration}</span>}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      {canLoadMore && oldestIso && (
        <div className="border-t border-zinc-100 px-3 py-2 text-center dark:border-zinc-800">
          <button
            type="button"
            onClick={() => onLoadMore?.(oldestIso)}
            disabled={loadingMore}
            className="text-xs text-blue-600 hover:underline disabled:text-zinc-400 dark:text-blue-400"
          >
            {loadingMore ? "Loading…" : "Load older events"}
          </button>
        </div>
      )}
    </div>
  );
}

interface SteerComposerProps {
  disabled?: boolean;
  onSubmit: (message: string) => Promise<void> | void;
}

export function SteerComposer({ disabled, onSubmit }: SteerComposerProps): React.JSX.Element {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending || disabled) return;
    setSending(true);
    setError(null);
    try {
      await onSubmit(text);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-xs dark:border-blue-700 dark:bg-blue-950">
      <div className="mb-1 text-[11px] font-medium text-blue-900 dark:text-blue-200">
        Steer the running pipeline
      </div>
      <div className="mb-2 text-[10px] text-blue-700 dark:text-blue-300">
        Your hint is consumed at the next phase boundary — no mid-call interruption.
      </div>
      <div className="flex items-stretch gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          placeholder="e.g. focus on the REST surface, skip the README"
          disabled={disabled || sending}
          className="flex-1 rounded border border-blue-300 bg-white px-2 py-1 text-xs text-zinc-900 outline-none focus:border-blue-500 disabled:bg-zinc-100 dark:border-blue-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={!draft.trim() || disabled || sending}
          className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:bg-zinc-400"
        >
          {sending ? "Sending…" : "Send hint"}
        </button>
      </div>
      {error && <div className="mt-1 text-[10px] text-red-600 dark:text-red-400">{error}</div>}
    </div>
  );
}
