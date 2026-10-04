// =============================================================================
// File: coverage-region.tsx
// Version: 1
// Path: ay_platform_ui/components/workbench/coverage-region.tsx
// Description: The coverage matrix — 500-SPEC R-500-019 / R-500-020,
//              realising 310-SPEC R-310-225 / R-310-226.
//
//              GROUPED ABOVE A THRESHOLD, AND IT SAYS SO (R-500-020). At the
//              30 000 requirements of R-310-300 an ungrouped matrix is
//              unreadable and expensive to render. The threshold and the
//              count are STATED when grouping kicks in, because a reader who
//              sees 40 rows where there are 30 000 requirements and is told
//              nothing concludes the data is missing — and then goes looking
//              for a bug that is not there.
//
//              EVERY FIGURE GOES THROUGH `CoverageFigureView`. Not a
//              convention: this module never formats a number itself, so
//              R-500-019's breakdown cannot be dropped by editing one panel.
//
// @relation implements:R-310-226
// @relation implements:R-500-019
// @relation implements:R-500-020
// =============================================================================

"use client";

import { useState } from "react";

import type { ContainerCoverageView, CoverageFigure } from "@/lib/workbenchTypes";
import { CoverageFigureBar, CoverageFigureView } from "./coverage-figure";
import { useSelection } from "./selection-context";

/** Above this requirement count the matrix opens grouped (`R-500-020`).
 *  A module constant rather than a magic number so the one place to
 *  recalibrate it is obvious. */
export const CLUSTER_GROUPING_THRESHOLD = 500;

export interface ClusterView {
  cluster_id: string;
  label: string;
  requirement_ids: string[];
  figure: CoverageFigure;
}

export function CoverageRegion({
  coverage,
  figure,
  clusters,
  requirementCount,
  autoAcceptedIds,
  threshold = CLUSTER_GROUPING_THRESHOLD,
}: {
  coverage: ContainerCoverageView | null;
  /** The open container's figure, already carrying its auto-accepted share. */
  figure: CoverageFigure | null;
  clusters: ClusterView[];
  requirementCount: number;
  autoAcceptedIds: ReadonlySet<string>;
  threshold?: number;
}) {
  const { requirementId, selectRequirement } = useSelection();
  const grouped = requirementCount > threshold;
  const [expandedCluster, setExpandedCluster] = useState<string | null>(null);

  return (
    <section
      className="flex h-full flex-col overflow-hidden border-l border-neutral-200 bg-white"
      aria-label="Coverage"
      data-testid="coverage-region"
    >
      <header className="border-b border-neutral-200 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-700">Coverage</h2>
        {grouped ? (
          /* R-500-020: state the threshold AND the count, so grouping is not
             mistaken for missing data. */
          <p className="text-[11px] text-neutral-500" data-testid="grouping-notice">
            Grouped by cluster: {requirementCount} requirements exceeds the {threshold} threshold.
            Expand a cluster for its requirements.
          </p>
        ) : null}
      </header>

      <div className="space-y-3 overflow-y-auto px-3 py-3">
        {figure && coverage ? (
          <CoverageFigureView
            label={`${coverage.container} answered`}
            figure={figure}
            testId="container-figure"
          />
        ) : (
          <p className="text-xs text-neutral-500" data-testid="coverage-empty">
            Choose a container to see what it has answered.
          </p>
        )}

        {grouped ? (
          <ul className="space-y-2" data-testid="cluster-list">
            {clusters.map((cluster) => (
              <li key={cluster.cluster_id} className="rounded border border-neutral-200">
                <button
                  type="button"
                  onClick={() =>
                    setExpandedCluster((prev) =>
                      prev === cluster.cluster_id ? null : cluster.cluster_id,
                    )
                  }
                  className="w-full px-2 py-1.5 text-left hover:bg-neutral-50"
                  data-testid={`cluster-${cluster.cluster_id}`}
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="text-xs font-medium text-neutral-900">{cluster.label}</span>
                    <span className="text-[11px] tabular-nums text-neutral-500">
                      {cluster.requirement_ids.length}
                    </span>
                  </span>
                  <span className="mt-1 block">
                    <CoverageFigureBar
                      figure={cluster.figure}
                      testId={`cluster-bar-${cluster.cluster_id}`}
                    />
                  </span>
                </button>
                {expandedCluster === cluster.cluster_id ? (
                  <ul
                    className="border-t border-neutral-200 bg-neutral-50"
                    data-testid={`cluster-expanded-${cluster.cluster_id}`}
                  >
                    {cluster.requirement_ids.map((id) => (
                      <li key={id}>
                        <button
                          type="button"
                          onClick={() => selectRequirement(id)}
                          className={`w-full px-3 py-1 text-left text-[11px] hover:bg-white ${
                            id === requirementId ? "bg-white font-medium" : ""
                          }`}
                          data-testid={`cluster-req-${id}`}
                        >
                          {id}
                          {autoAcceptedIds.has(id) ? (
                            <span className="ml-2 text-amber-700">auto-accepted</span>
                          ) : null}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        ) : coverage ? (
          <ul className="space-y-1" data-testid="requirement-list">
            {coverage.allocated.map((id) => {
              const uncovered = coverage.uncovered.includes(id);
              const weak = coverage.weak.includes(id);
              return (
                <li key={id}>
                  <button
                    type="button"
                    onClick={() => selectRequirement(id)}
                    className={`flex w-full items-baseline justify-between gap-2 rounded px-2 py-1 text-left text-xs hover:bg-neutral-50 ${
                      id === requirementId ? "bg-neutral-100 font-medium" : ""
                    }`}
                    data-testid={`matrix-req-${id}`}
                  >
                    <span className="text-neutral-900">{id}</span>
                    <span
                      className={
                        uncovered
                          ? "text-rose-700"
                          : weak
                            ? "text-amber-700"
                            : autoAcceptedIds.has(id)
                              ? "text-amber-700"
                              : "text-emerald-700"
                      }
                    >
                      {uncovered
                        ? "unanswered"
                        : weak
                          ? "weak"
                          : autoAcceptedIds.has(id)
                            ? "auto-accepted"
                            : "answered"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
