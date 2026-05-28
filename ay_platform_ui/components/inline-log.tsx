// =============================================================================
// File: inline-log.tsx
// Version: 4
// Path: ay_platform_ui/components/inline-log.tsx
//
// v4 (2026-05-21): #5 — the per-tool "Open in Working area →" deep-link
// is removed from the inline rows (the inline log is now pure
// chain-of-thought). Modified-document deep-links are surfaced by the
// new <ModifiedDocsLinks>, rendered BELOW the response, one compact
// versioned "Open in working area (vN)" link per modified doc (version
// read from the tool_call `done` event). InlineLog no longer needs the
// projectId/conversationId/hideOpenLink props.
//
// v3 (2026-05-21): tool_call rows are now an expandable chain-of-thought
// view (#4). A `done` row with `arguments` (or a summary) toggles open
// to reveal the call's step/round, arguments, and result summary. The
// arguments are size-capped server-side (C3 `_safe_tool_args`).
//
// v2 (2026-05-19): `hideOpenLink` — the Working-area surface
// suppresses the tool_call "Open in Working area" deep-link (we're
// already there ; it would self-navigate). Conversations keeps it.
// Description: THE single entry point that formats + displays every
//              inline-activity event (C3 `event: inline`, unified
//              2026-05-19). One `<InlineLog>` takes the unified
//              `InlineEvent[]` (live SSE for the in-flight turn OR
//              `Message.events` persisted/audit on reload — same
//              shape, same render) and dispatches each event to a
//              per-`kind` formatter via the FORMATTERS registry.
//
//              Architectural contract : adding a new inline event
//              kind = adding ONE formatter entry here. No plumbing
//              elsewhere (server emits on the one `event: inline`
//              channel, persists in the one `events` ledger). An
//              unknown kind never disappears — it falls back to a
//              generic row, so the inline log is GUARANTEED to be
//              populated for whatever we add later.
// =============================================================================

"use client";

import Link from "next/link";
import { type ReactNode, useState } from "react";

import type { InlineEvent } from "@/lib/types";

/** Render a millisecond duration human-readably (sub-second → 3
 *  decimals so a fast phase isn't a meaningless `0 s`). */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${(ms / 1000).toFixed(3)} s`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function StageDot({ status }: { status: "running" | "done" }) {
  return (
    <span
      className={[
        "inline-block h-1.5 w-1.5 rounded-full",
        status === "running" ? "bg-blue-500 animate-pulse" : "bg-emerald-500",
      ].join(" ")}
      aria-hidden="true"
    />
  );
}

/** kind="stage" formatter — collapsible pipeline timeline. Same UX as
 *  the former PipelineChip/StageTimelineFull : a compact "+ Xs" chip
 *  that expands to one row per phase. running/done with the same
 *  `name` collapse into a single in-place-updating row. */
function StageFormatter({ events }: { events: InlineEvent[] }): ReactNode {
  const [open, setOpen] = useState(false);
  // Merge running→done by name (done wins, keeps order of first sight).
  const byName = new Map<string, InlineEvent>();
  for (const e of events) {
    const key = e.name ?? e.label;
    const prev = byName.get(key);
    if (!prev || e.status === "done") byName.set(key, e);
  }
  const rows = [...byName.values()];
  const totalMs = rows.reduce((acc, s) => acc + (s.duration_ms ?? 0), 0);
  const allDone = rows.every((s) => s.status === "done");

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex shrink-0 items-center gap-1 rounded-full border border-neutral-200 bg-neutral-100 px-2 py-0.5 font-mono text-[10px] text-neutral-600 hover:bg-neutral-200"
        data-testid="inline-stage-chip"
        title={`Pipeline · ${allDone ? `${formatDuration(totalMs)} total` : "in progress"}`}
      >
        <StageDot status={allDone ? "done" : "running"} />
        <span>+ {totalMs > 0 ? formatDuration(totalMs) : "…"} pipeline</span>
      </button>
    );
  }
  return (
    <div
      className="w-full rounded-md border border-neutral-200 bg-neutral-100/60 text-xs"
      data-testid="inline-stage-timeline"
    >
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-neutral-700 hover:bg-neutral-200/60"
      >
        <span className="font-mono text-[11px] uppercase tracking-wide text-neutral-500">
          − pipeline
        </span>
        <span className="truncate text-[11px] text-neutral-500">
          {allDone ? `${formatDuration(totalMs)} total` : "in progress"}
        </span>
      </button>
      <ul className="divide-y divide-neutral-200/60 px-2.5 pb-1.5 pt-0.5">
        {rows.map((s) => (
          <li
            key={s.name ?? s.label}
            className="flex items-center justify-between gap-2 py-1"
            data-testid={`inline-stage-${s.name ?? "x"}`}
          >
            <span className="flex items-center gap-2">
              <StageDot status={s.status} />
              <span className="text-neutral-800">{s.label}</span>
            </span>
            <span className="font-mono text-[10px] text-neutral-500">
              {s.duration_ms != null ? formatDuration(s.duration_ms) : "…"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Render one tool-call argument value for the chain-of-thought
 *  detail. Strings (already size-capped server-side) pass through ;
 *  everything else is JSON-stringified compactly. */
function _argValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** One tool-call row. A `done` event with arguments or a summary is
 *  expandable : the toggle reveals the chain-of-thought detail (step /
 *  round, the call arguments, the result summary). Running rows stay
 *  compact. No deep-link here — the versioned "Open in working area"
 *  links live in <ModifiedDocsLinks>, rendered below the response (#5). */
function ToolCallRow({ tc, index }: { tc: InlineEvent; index: number }): ReactNode {
  const [open, setOpen] = useState(false);
  const argEntries = tc.arguments ? Object.entries(tc.arguments) : [];
  const expandable = tc.status === "done" && (argEntries.length > 0 || !!tc.summary);
  const icon = tc.status === "running" ? "⏳" : tc.ok ? "✅" : "❌";
  const stepBadge =
    typeof tc.round === "number" ? (
      <span className="rounded bg-amber-100 px-1 text-[10px] text-amber-700">step {tc.round}</span>
    ) : null;

  return (
    <li
      className="font-mono text-[11px] text-amber-900"
      data-testid={`inline-toolcall-${tc.name}-${tc.status}`}
    >
      <div className="flex items-center gap-1.5">
        <span aria-hidden="true">{icon}</span>
        {expandable ? (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="flex items-center gap-1.5 text-left hover:underline"
            data-testid={`inline-toolcall-toggle-${tc.name}`}
          >
            <span className="font-semibold">{tc.name}</span>
            {stepBadge}
            {tc.summary ? <span className="text-amber-700">— {tc.summary}</span> : null}
            <span aria-hidden="true">{open ? "▾" : "▸"}</span>
          </button>
        ) : (
          <span className="flex items-center gap-1.5">
            <span className="font-semibold">{tc.name}</span>
            {stepBadge}
            {tc.status === "done" && tc.summary ? (
              <span className="text-amber-700">— {tc.summary}</span>
            ) : null}
          </span>
        )}
      </div>
      {expandable && open ? (
        <div
          className="mt-1 ml-5 rounded border border-amber-200 bg-white/70 p-2 dark:bg-zinc-900/40"
          data-testid={`inline-toolcall-detail-${tc.name}-${index}`}
        >
          {argEntries.length > 0 ? (
            <dl className="space-y-0.5">
              {argEntries.map(([key, value]) => (
                <div key={key} className="grid grid-cols-[5rem_1fr] gap-2">
                  <dt className="text-amber-600">{key}</dt>
                  <dd className="whitespace-pre-wrap break-words text-amber-900">
                    {_argValue(value)}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-amber-700">{tc.summary}</p>
          )}
        </div>
      ) : null}
    </li>
  );
}

/** kind="tool_call" formatter — the amber tool strip, now a
 *  chain-of-thought view : one row per tool call (in step order) that
 *  expands to show the call's arguments + result summary. ⏳ running /
 *  ✅ ok / ❌ error. Deep-links to modified docs are surfaced separately
 *  by <ModifiedDocsLinks> below the response (#5). */
function ToolCallFormatter({ events }: { events: InlineEvent[] }): ReactNode {
  return (
    <div
      className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs"
      data-testid="inline-toolcalls"
    >
      <div className="mb-1 font-medium uppercase tracking-wide text-amber-700">
        Chain of thought · tools
      </div>
      <ul className="space-y-0.5">
        {events.map((tc, i) => (
          <ToolCallRow
            // Append-only across turns ; round restarts each turn so
            // the index is the correct stable key (order is stable).
            // biome-ignore lint/suspicious/noArrayIndexKey: append-only, stable order
            key={`${i}-${tc.name}-${tc.status}`}
            tc={tc}
            index={i}
          />
        ))}
      </ul>
    </div>
  );
}

/** Generic fallback — guarantees an unknown future `kind` is still
 *  surfaced (never silently dropped) until a dedicated formatter is
 *  added. This is the safety net behind the "inline is guaranteed
 *  populated" contract. */
function GenericFormatter({ events }: { events: InlineEvent[] }): ReactNode {
  return (
    <div
      className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-700"
      data-testid="inline-generic"
    >
      <ul className="space-y-0.5">
        {events.map((e, i) => (
          <li
            // biome-ignore lint/suspicious/noArrayIndexKey: append-only, stable order
            key={`${i}-${e.kind}-${e.status}`}
            className="flex items-center gap-1.5 font-mono text-[11px]"
          >
            <StageDot status={e.status} />
            <span className="font-semibold">{e.kind}</span>
            <span>{e.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Per-kind formatter registry. THE extension point : a new inline
 *  event kind only needs an entry here. */
const FORMATTERS: Record<string, (args: { events: InlineEvent[] }) => ReactNode> = {
  stage: ({ events }) => <StageFormatter events={events} />,
  tool_call: ({ events }) => <ToolCallFormatter events={events} />,
};

/** Unified inline-activity renderer. Feed it the live-accumulated
 *  events for an in-flight turn OR a message's persisted
 *  `events` (audit ledger) — identical render either way. Groups by
 *  `kind` (preserving first-seen order) and dispatches each group to
 *  its formatter ; unknown kinds use the generic fallback so nothing
 *  is ever dropped. */
export function InlineLog({
  events,
  className,
}: {
  events: InlineEvent[] | null | undefined;
  className?: string;
}): ReactNode {
  if (!events || events.length === 0) return null;
  // Group by kind, preserving the order each kind first appears.
  const order: string[] = [];
  const groups = new Map<string, InlineEvent[]>();
  for (const e of events) {
    if (!groups.has(e.kind)) {
      groups.set(e.kind, []);
      order.push(e.kind);
    }
    (groups.get(e.kind) as InlineEvent[]).push(e);
  }
  return (
    <div className={["flex flex-col gap-2", className ?? ""].join(" ")} data-testid="inline-log">
      {order.map((kind) => {
        const groupEvents = groups.get(kind) as InlineEvent[];
        const fmt = FORMATTERS[kind];
        return (
          <div key={kind}>
            {fmt ? fmt({ events: groupEvents }) : GenericFormatter({ events: groupEvents })}
          </div>
        );
      })}
    </div>
  );
}

/** Below-response deep-links to the documents a turn created/updated
 *  (#5). One compact "Open in working area (vN)" link per distinct
 *  modified path (last write in the turn wins the version). Reads the
 *  path + resulting version straight from the tool_call `done` events
 *  (no extra fetch). Rendered only where navigation makes sense
 *  (Conversations) — the Working-area sidebar omits it (already there). */
export function ModifiedDocsLinks({
  events,
  projectId,
  conversationId,
}: {
  events: InlineEvent[] | null | undefined;
  projectId?: string;
  conversationId?: string;
}): ReactNode {
  if (!events || !projectId || !conversationId) return null;
  // Distinct modified paths → resulting version (last write wins).
  const byPath = new Map<string, number | null>();
  for (const e of events) {
    if (
      e.kind === "tool_call" &&
      e.status === "done" &&
      e.ok &&
      e.path &&
      (e.name === "create_document" || e.name === "update_document")
    ) {
      byPath.set(e.path, typeof e.version === "number" ? e.version : null);
    }
  }
  if (byPath.size === 0) return null;
  return (
    <div className="flex flex-col gap-1" data-testid="modified-docs-links">
      {[...byPath.entries()].map(([path, version]) => {
        const basename = path.split("/").pop() ?? path;
        return (
          <Link
            key={path}
            href={`/projects/${encodeURIComponent(
              projectId,
            )}/working-area?conv=${encodeURIComponent(
              conversationId,
            )}&path=${encodeURIComponent(path)}`}
            className="inline-flex w-fit items-center gap-1 rounded border border-blue-200 bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700 no-underline hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
            data-testid={`modified-doc-link-${path}`}
          >
            <span aria-hidden="true">📄</span>
            <span>Open in working area: {basename}</span>
            {typeof version === "number" ? (
              <span className="text-blue-500">(v{version})</span>
            ) : null}
          </Link>
        );
      })}
    </div>
  );
}
