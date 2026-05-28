// =============================================================================
// File: reference-tray.tsx
// Version: 1
// Path: ay_platform_ui/components/reference-tray.tsx
// Description: Docked tray rendering the operator-attached prompt
//              references (R-500-012). One chip per ref ; X to remove ;
//              running token-count estimate vs the 32K cap (R-500-013).
//              Sits above the chat composer. The parent owns the
//              references list state ; this component only renders +
//              dispatches `onRemove`.
// =============================================================================

"use client";

import type { PromptReference } from "@/lib/types";

interface ReferenceTrayProps {
  references: PromptReference[];
  onRemove: (index: number) => void;
}

/** Approximate token cap per R-200-181 (4 chars/token heuristic). The
 *  UI mirrors this until C8 surfaces real tokenizers (Q-500-005). */
const _TOKEN_CAP = 32_000;
const _CHAR_PER_TOKEN_APPROX = 4;

function _estimateTokens(refs: PromptReference[]): number {
  // We don't have the actual content here — the parent doesn't fetch
  // it (server resolves at send time). So we estimate VERY
  // conservatively from path + range information alone. For a file
  // ref, we use a small baseline (a "guess" of 2 KB per file = 500
  // tokens) ; for an excerpt, we use the line range × 80 chars
  // average. The server is the authoritative gate (returns 413) ; this
  // estimate is just to warn the operator early when they pile up
  // refs.
  return refs.reduce((acc, r) => {
    if (r.kind === "excerpt" && r.range) {
      const lines = Math.max(1, r.range.end_line - r.range.start_line + 1);
      return acc + Math.ceil((lines * 80) / _CHAR_PER_TOKEN_APPROX);
    }
    return acc + 500; // conservative file baseline
  }, 0);
}

export function ReferenceTray({
  references,
  onRemove,
}: ReferenceTrayProps): React.JSX.Element | null {
  if (references.length === 0) return null;
  const estimate = _estimateTokens(references);
  const overCap = estimate > _TOKEN_CAP;
  return (
    <div
      className={[
        "mb-2 rounded-md border px-2 py-1.5 text-xs",
        overCap
          ? "border-red-300 bg-red-50 dark:border-red-700 dark:bg-red-950"
          : "border-blue-200 bg-blue-50 dark:border-blue-700 dark:bg-blue-950",
      ].join(" ")}
      data-testid="reference-tray"
    >
      <div className="mb-1 flex items-baseline justify-between">
        <span className="font-medium text-blue-900 dark:text-blue-200">
          {references.length} reference{references.length === 1 ? "" : "s"} attached
        </span>
        <span
          className={[
            "text-[10px]",
            overCap
              ? "font-semibold text-red-700 dark:text-red-300"
              : "text-blue-600 dark:text-blue-300",
          ].join(" ")}
        >
          ~{estimate.toLocaleString()} / {_TOKEN_CAP.toLocaleString()} tokens
          {overCap && " · over cap, send will 413"}
        </span>
      </div>
      <ul className="flex flex-wrap gap-1.5">
        {references.map((r, idx) => {
          const rangeLabel =
            r.kind === "excerpt" && r.range ? ` :${r.range.start_line}-${r.range.end_line}` : "";
          // Composite-stable key — same path + range pair is allowed
          // multiple times technically (operator paste twice) so we
          // include the index. (Same caveat as TreeRow — no reordering
          // happens client-side ; safe.)
          const key = `${r.source}|${r.path}${rangeLabel}|${idx}`;
          return (
            <li
              key={key}
              className="inline-flex items-center gap-1 rounded-full border border-blue-300 bg-white px-2 py-0.5 font-mono text-[11px] text-blue-900 dark:border-blue-600 dark:bg-zinc-900 dark:text-blue-100"
            >
              <span aria-hidden>{r.kind === "excerpt" ? "✂" : "📎"}</span>
              <span className="max-w-[18ch] truncate" title={`${r.path}${rangeLabel}`}>
                {r.path}
                {rangeLabel}
              </span>
              <button
                type="button"
                onClick={() => onRemove(idx)}
                className="text-blue-500 hover:text-red-600 dark:hover:text-red-400"
                aria-label={`Remove reference ${r.path}`}
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
