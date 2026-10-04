// =============================================================================
// File: object-region.tsx
// Version: 1
// Path: ay_platform_ui/components/workbench/object-region.tsx
// Description: The open container's objects — 500-SPEC R-500-017 / R-500-021,
//              realising 310-SPEC R-310-222 and the supervision model.
//
//              A PROPOSAL MUST NOT LOOK LIKE SETTLED CONTENT (R-500-021).
//              The operating model is that the LLM produces and humans vet
//              systematically; an agent's paragraph rendered identically to
//              an accepted one gets read as decided. So a `proposed` object
//              carries a visible band, a state badge and its review
//              affordance IN PLACE — not in a separate queue, because a
//              queue beside the document makes reviewing a second activity
//              people defer.
//
//              `auto-accepted` IS BADGED DISTINCTLY from `accepted`, for the
//              same reason R-500-019 splits the figures: it means nobody
//              examined this one individually.
//
//              A LINK EXPANDS IN PLACE (R-500-017 / R-310-222). Within the
//              object's own row — no dialog, no scroll, no navigation. The
//              requirement exists because judging whether an object answers
//              a requirement means reading both at once, and anything that
//              moves the reader loses the comparison.
//
// @relation implements:R-310-222
// @relation implements:R-500-017
// @relation implements:R-500-021
// =============================================================================

"use client";

import { useState } from "react";

import type { DocObjectSummary, RequirementCoverageView, ReviewState } from "@/lib/workbenchTypes";
import { isFigure, isProposal, objectText } from "@/lib/workbenchTypes";
import { useSelection } from "./selection-context";

const STATE_BADGE: Record<ReviewState, { label: string; className: string }> = {
  proposed: {
    label: "Proposed",
    className: "bg-sky-100 text-sky-800 ring-1 ring-sky-300",
  },
  // Distinct from `accepted` on purpose (R-310-007): nobody examined this
  // one individually.
  "auto-accepted": {
    label: "Auto-accepted",
    className: "bg-amber-100 text-amber-800 ring-1 ring-amber-300",
  },
  accepted: { label: "Accepted", className: "bg-emerald-100 text-emerald-800" },
  stale: { label: "Stale", className: "bg-rose-100 text-rose-800" },
  rejected: { label: "Rejected", className: "bg-neutral-200 text-neutral-700" },
};

export function ReviewStateBadge({ state }: { state: ReviewState }) {
  const badge = STATE_BADGE[state];
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${badge.className}`}
      data-testid={`state-${state}`}
    >
      {badge.label}
    </span>
  );
}

/** The in-place expansion of one coverage link (`R-500-017`). */
function LinkExpansion({ coverage }: { coverage: RequirementCoverageView | null }) {
  if (coverage === null) {
    return (
      <p className="px-3 py-2 text-xs text-neutral-500" data-testid="link-loading">
        Loading what this answers…
      </p>
    );
  }
  return (
    <div
      className="border-t border-neutral-200 bg-neutral-50 px-3 py-2"
      data-testid={`expansion-${coverage.requirement_id}`}
    >
      <p className="text-xs font-medium text-neutral-800">
        {coverage.requirement_id}
        {coverage.out_of_project ? (
          <span className="ml-2 text-[11px] text-neutral-500">ruled out of project</span>
        ) : null}
      </p>
      {coverage.allocations.length === 0 ? (
        <p className="mt-1 text-xs text-neutral-500">No allocation recorded for it yet.</p>
      ) : (
        <ul className="mt-1 space-y-1">
          {coverage.allocations.map((allocation) => (
            <li key={allocation.container} className="text-xs text-neutral-700">
              <span className="font-medium">{allocation.container}</span>
              {" — "}
              {allocation.is_covered ? "answered" : "not yet answered"}
              {allocation.links.length > 0 ? (
                <ul className="ml-3 mt-0.5 space-y-0.5">
                  {allocation.links.map((link) => (
                    <li
                      key={`${link.object_id}:${link.target_id}`}
                      className="text-[11px] text-neutral-600"
                    >
                      {link.object_id} · pinned v{link.pinned_version} · {link.strength} ·{" "}
                      {link.state}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ObjectRegion({
  objects,
  coversOf,
  coverageOf,
  onRequestCoverage,
  loading = false,
}: {
  objects: DocObjectSummary[];
  /** object id -> the requirement ids its coverage links target. Supplied by
   *  the page from the coverage graph: an object document does not carry its
   *  own edges, and deriving them here would mean a second query per row. */
  coversOf: Record<string, string[] | undefined>;
  /** requirement id -> its coverage, as the expansions arrive. */
  coverageOf: Record<string, RequirementCoverageView | undefined>;
  /** Asked for when a link is expanded for the first time. */
  onRequestCoverage: (requirementId: string) => void;
  loading?: boolean;
}) {
  const { container, objectId, selectObject, selectRequirement } = useSelection();
  // Keyed by `<object id>:<requirement id>`: the same requirement may be
  // answered by two objects, and a bare requirement key would expand both
  // rows at once.
  const [expanded, setExpanded] = useState<string | null>(null);

  const toggle = (ownerId: string, requirementId: string) => {
    const key = `${ownerId}:${requirementId}`;
    if (expanded === key) {
      setExpanded(null);
      return;
    }
    setExpanded(key);
    selectRequirement(requirementId);
    if (coverageOf[requirementId] === undefined) {
      onRequestCoverage(requirementId);
    }
  };

  if (container === null) {
    return (
      <section
        className="flex h-full items-center justify-center bg-white"
        aria-label="Container objects"
        data-testid="object-region-empty"
      >
        <p className="text-sm text-neutral-500">Choose a container to read its objects.</p>
      </section>
    );
  }

  return (
    <section
      className="flex h-full flex-col overflow-hidden bg-white"
      aria-label="Container objects"
      data-testid="object-region"
    >
      <header className="flex items-baseline justify-between border-b border-neutral-200 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-700">
          {container}
        </h2>
        <span className="text-xs tabular-nums text-neutral-500">{objects.length} objects</span>
      </header>

      {loading ? (
        <p className="px-3 py-4 text-xs text-neutral-500" data-testid="objects-loading">
          Loading objects…
        </p>
      ) : objects.length === 0 ? (
        <p className="px-3 py-4 text-xs text-neutral-500" data-testid="objects-none">
          This container holds no objects yet.
        </p>
      ) : (
        <ul className="flex-1 overflow-y-auto">
          {objects.map((obj) => {
            const proposal = isProposal(obj.review_state);
            const selected = obj.object_id === objectId;
            return (
              <li
                key={obj.object_id}
                className={`border-b border-neutral-100 ${
                  /* R-500-021: a proposal must not read as settled content. */
                  proposal ? "border-l-4 border-l-sky-400 bg-sky-50/40" : ""
                } ${selected ? "bg-neutral-100" : ""}`}
                data-testid={`object-${obj.object_id}`}
              >
                <button
                  type="button"
                  className="block w-full px-3 py-2 text-left"
                  onClick={() => selectObject(obj.container, obj.object_id)}
                >
                  <span className="flex items-baseline gap-2">
                    <code className="text-[11px] text-neutral-500">{obj.object_id}</code>
                    <span className="text-[11px] text-neutral-400">
                      {obj.type} · v{obj.version}
                    </span>
                    <ReviewStateBadge state={obj.review_state} />
                    {obj.produced_by ? (
                      <span className="text-[11px] text-neutral-500">
                        by {obj.produced_by.workflow} v{obj.produced_by.workflow_version}
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-1 block text-sm text-neutral-900">
                    {/* A figure carries a textual NOTATION, not prose
                        (R-310-008) — labelled, so a reader is not left
                        wondering why a diagram reads like a sentence. */}
                    {isFigure(obj) ? (
                      <span className="italic text-neutral-700">figure: {objectText(obj)}</span>
                    ) : (
                      objectText(obj)
                    )}
                  </span>
                </button>

                {proposal ? (
                  /* The review affordance sits at the point of reading, not
                     in a separate queue (R-500-021). */
                  <p
                    className="px-3 pb-2 text-[11px] text-sky-800"
                    data-testid={`review-prompt-${obj.object_id}`}
                  >
                    Awaiting your review — accept, redraft or confirm unchanged.
                  </p>
                ) : null}

                {/* One chip per coverage link on this object (R-310-222). */}
                <div className="flex flex-wrap gap-1 px-3 pb-2">
                  {(coversOf[obj.object_id] ?? []).length === 0 ? (
                    <span
                      className="text-[11px] text-neutral-400"
                      data-testid={`covers-none-${obj.object_id}`}
                    >
                      answers nothing yet
                    </span>
                  ) : (
                    (coversOf[obj.object_id] ?? []).map((target) => {
                      const key = `${obj.object_id}:${target}`;
                      return (
                        <button
                          key={key}
                          type="button"
                          onClick={() => toggle(obj.object_id, target)}
                          className={`rounded border px-1.5 py-0.5 text-[11px] hover:bg-neutral-50 ${
                            expanded === key
                              ? "border-neutral-500 bg-neutral-100 text-neutral-900"
                              : "border-neutral-300 text-neutral-700"
                          }`}
                          data-testid={`link-${obj.object_id}-${target}`}
                        >
                          {target}
                        </button>
                      );
                    })
                  )}
                </div>

                {/* Expanded WITHIN the row: no dialog, no navigation
                    (R-500-017). */}
                {(coversOf[obj.object_id] ?? []).map((target) =>
                  expanded === `${obj.object_id}:${target}` ? (
                    <LinkExpansion key={`exp-${target}`} coverage={coverageOf[target] ?? null} />
                  ) : null,
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
