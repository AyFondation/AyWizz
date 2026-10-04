// =============================================================================
// File: container-navigator.tsx
// Version: 1
// Path: ay_platform_ui/components/workbench/container-navigator.tsx
// Description: The cycle's containers, in cycle order — 500-SPEC R-500-015.
//
//              ORDERED BY THE CYCLE'S OWN ORDINAL, never alphabetically. The
//              cycle declares a cascade (functional → architecture →
//              technical); sorting by slug would scramble the one piece of
//              information the navigator exists to convey, which is where in
//              the cascade the reader is.
//
//              A container whose coverage is OBLIGATORY and incomplete is
//              marked here rather than only in the findings region, because
//              the navigator is what a reader scans to decide where to go.
//
// @relation implements:R-500-015
// =============================================================================

"use client";

import type { ContainerSpecView } from "@/lib/workbenchTypes";
import { useSelection } from "./selection-context";

export function ContainerNavigator({
  containers,
  uncoveredCounts,
  cycleTitle,
}: {
  containers: ContainerSpecView[];
  /** container slug -> how many allocated requirements it has not answered.
   *  Absent means not yet known, which renders as nothing rather than as
   *  zero: claiming "0 outstanding" before asking is the figure a reader
   *  would act on wrongly. */
  uncoveredCounts: Record<string, number | undefined>;
  cycleTitle?: string;
}) {
  const { container, selectContainer } = useSelection();
  const ordered = [...containers].sort((a, b) => a.ordinal - b.ordinal);

  return (
    <nav
      className="flex h-full flex-col overflow-hidden border-r border-neutral-200 bg-neutral-50"
      aria-label="Containers"
      data-testid="container-navigator"
    >
      <header className="border-b border-neutral-200 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-700">Cycle</h2>
        {cycleTitle ? <p className="text-[11px] text-neutral-500">{cycleTitle}</p> : null}
      </header>

      {ordered.length === 0 ? (
        <p className="px-3 py-4 text-xs text-neutral-500" data-testid="no-containers">
          No published cycle for this project yet.
        </p>
      ) : (
        <ul className="flex-1 overflow-y-auto">
          {ordered.map((spec) => {
            const outstanding = uncoveredCounts[spec.slug];
            const active = spec.slug === container;
            return (
              <li key={spec.slug}>
                <button
                  type="button"
                  onClick={() => selectContainer(spec.slug)}
                  className={`w-full px-3 py-2 text-left hover:bg-white ${
                    active ? "bg-white font-medium shadow-inner" : ""
                  }`}
                  data-testid={`container-${spec.slug}`}
                  aria-current={active ? "true" : undefined}
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="text-sm text-neutral-900">{spec.slug}</span>
                    {outstanding !== undefined && outstanding > 0 ? (
                      <span
                        className="rounded-full bg-rose-100 px-1.5 text-[11px] font-medium tabular-nums text-rose-800"
                        data-testid={`outstanding-${spec.slug}`}
                      >
                        {outstanding}
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-neutral-500">
                    {spec.coverage_obligatory ? "coverage obligatory" : "optional"}
                    {" · "}
                    {spec.scope_id}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}
