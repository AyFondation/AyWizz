// =============================================================================
// File: baseline-panel.tsx
// Version: 1
// Path: ay_platform_ui/components/workbench/baseline-panel.tsx
// Description: Taking and exporting a baseline — 310-SPEC §4.11
//              (R-310-200, R-310-201, R-310-207).
//
//              THE GATE IS SHOWN BEFORE THE BUTTON IS OFFERED. `R-310-201`
//              refuses a baseline on three preconditions; a reviewer who
//              clicks and reads a 409 has learned the same thing one round
//              trip later and with an error to interpret. So the readiness
//              verdict is fetched and listed, EVERY refusal of it, and the
//              take control is disabled while any stands.
//
//              THE TAG FIELD IS NOT PRE-FILLED. A baseline is immutable
//              (`R-310-204`) and its tag is how a human will refer to it at
//              a review months later — a generated default would produce
//              `B-2026-10-04-1` names nobody chose and nobody can cite.
//
//              THE EXPORT IS A PLAIN LINK, not a fetch. The response is a
//              file; routing it through the JSON client would corrupt it,
//              and an anchor gives the browser its own download handling for
//              free.
//
// @relation implements:R-310-201
// @relation implements:R-310-207
// =============================================================================

"use client";

import { useState } from "react";

import type {
  BaselineList,
  BaselineReadinessView,
  BaselineSummaryView,
} from "@/lib/workbenchTypes";

const BLOCKER_LABEL: Record<string, string> = {
  "open-change-ticket": "Change not absorbed",
  "critical-coverage-gap": "Rated requirement unanswered",
  "stale-coverage-link": "Coverage pin superseded",
};

export function BaselinePanel({
  readiness,
  baselines,
  cycleId,
  onTake,
  renderUrl,
  busy = false,
  error = null,
}: {
  readiness: BaselineReadinessView | null;
  baselines: BaselineList | null;
  cycleId: string | null;
  onTake: (tag: string, note: string) => void;
  renderUrl: (tag: string, fmt: "docx" | "pdf") => string;
  busy?: boolean;
  error?: string | null;
}) {
  const [tag, setTag] = useState("");
  const [note, setNote] = useState("");

  const refusals = readiness?.refusals ?? [];
  const ready = readiness !== null && refusals.length === 0;
  const canTake = ready && !busy && tag.trim().length > 0 && cycleId !== null;

  return (
    <section
      className="flex h-full flex-col overflow-hidden border-l border-neutral-200 bg-white"
      aria-label="Baselines"
      data-testid="baseline-panel"
    >
      <header className="border-b border-neutral-200 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-700">
          Baselines
        </h2>
      </header>

      <div className="space-y-3 overflow-y-auto px-3 py-3">
        {/* The gate, before the button (R-310-201). */}
        {readiness === null ? (
          <p className="text-xs text-neutral-500" data-testid="readiness-loading">
            Checking whether the corpus can be baselined…
          </p>
        ) : ready ? (
          <p
            className="rounded bg-emerald-50 px-2 py-1.5 text-xs text-emerald-900"
            data-testid="readiness-ready"
          >
            Nothing outstanding — the corpus can be baselined.
          </p>
        ) : (
          <div className="rounded bg-rose-50 px-2 py-1.5" data-testid="readiness-blocked">
            <p className="text-xs font-medium text-rose-900">
              {refusals.length} precondition
              {refusals.length === 1 ? "" : "s"} outstanding
            </p>
            <ul className="mt-1 space-y-1">
              {refusals.map((refusal) => (
                <li
                  key={`${refusal.blocker}:${refusal.subject}`}
                  className="text-[11px] text-rose-900"
                  data-testid={`refusal-${refusal.subject}`}
                >
                  <strong className="font-medium">
                    {BLOCKER_LABEL[refusal.blocker] ?? refusal.blocker}
                  </strong>
                  {" — "}
                  {refusal.subject}
                  <span className="block text-rose-800">{refusal.detail}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Taking one. */}
        <div className="space-y-1.5">
          <label className="block text-[11px] font-medium text-neutral-700" htmlFor="baseline-tag">
            Tag
          </label>
          <input
            id="baseline-tag"
            value={tag}
            onChange={(event) => setTag(event.target.value)}
            placeholder="e.g. SOP-2026-10"
            disabled={!ready || busy}
            className="w-full rounded border border-neutral-300 px-2 py-1 text-xs disabled:bg-neutral-100"
            data-testid="baseline-tag"
          />
          <label className="block text-[11px] font-medium text-neutral-700" htmlFor="baseline-note">
            Note
          </label>
          <input
            id="baseline-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Why this baseline was taken"
            disabled={!ready || busy}
            className="w-full rounded border border-neutral-300 px-2 py-1 text-xs disabled:bg-neutral-100"
            data-testid="baseline-note"
          />
          <button
            type="button"
            onClick={() => onTake(tag.trim(), note)}
            disabled={!canTake}
            className="w-full rounded bg-neutral-900 px-2 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-neutral-300"
            data-testid="baseline-take"
          >
            {busy ? "Taking…" : "Take baseline"}
          </button>
          {cycleId === null ? (
            <p className="text-[11px] text-amber-800" data-testid="baseline-no-cycle">
              No published cycle — a baseline needs one to have containers to photograph.
            </p>
          ) : null}
          {error ? (
            <p className="text-[11px] text-rose-800" data-testid="baseline-error">
              {error}
            </p>
          ) : null}
        </div>

        {/* What has been taken, and its exports. */}
        {baselines === null ? null : baselines.count === 0 ? (
          <p className="text-xs text-neutral-500" data-testid="baseline-none">
            No baseline taken yet.
          </p>
        ) : (
          <ul className="space-y-2" data-testid="baseline-list">
            {baselines.baselines.map((baseline: BaselineSummaryView) => (
              <li
                key={baseline.tag}
                className="rounded border border-neutral-200 px-2 py-1.5"
                data-testid={`baseline-${baseline.tag}`}
              >
                <p className="text-xs font-medium text-neutral-900">{baseline.tag}</p>
                <p className="text-[11px] text-neutral-500">
                  {baseline.object_count} objects · {baseline.link_count} pins · {baseline.cycle_id}{" "}
                  v{baseline.cycle_version}
                </p>
                <p className="text-[11px] text-neutral-500">by {baseline.created_by}</p>
                {baseline.note ? (
                  <p className="text-[11px] text-neutral-700">{baseline.note}</p>
                ) : null}
                <p className="mt-1 flex gap-2">
                  {/* A plain link: the response is a file (R-310-207). */}
                  <a
                    href={renderUrl(baseline.tag, "docx")}
                    className="text-[11px] text-sky-700 underline"
                    data-testid={`export-docx-${baseline.tag}`}
                  >
                    Word
                  </a>
                  <a
                    href={renderUrl(baseline.tag, "pdf")}
                    className="text-[11px] text-sky-700 underline"
                    data-testid={`export-pdf-${baseline.tag}`}
                  >
                    PDF
                  </a>
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
