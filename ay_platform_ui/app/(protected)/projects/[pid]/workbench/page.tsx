// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/projects/[pid]/workbench/page.tsx
// Description: The traceability workbench — 500-SPEC R-500-015, realising
//              310-SPEC R-310-220.
//
//              ONE ROUTE, FIVE REGIONS, NO NAVIGATION. Changing the active
//              container, the selected object or an expanded link mutates
//              state inside this page; nothing pushes a route. R-310-220
//              forbids page transitions because the activity is a loop —
//              read a requirement, read what answers it, judge, move on —
//              and a transition between any two of those steps costs the
//              reader their place.
//
//              THIS PAGE OWNS THE FETCHING, the regions own the rendering.
//              Each region is a pure function of its props plus the shared
//              selection, which is what lets the region tests assert
//              behaviour without a network at all. The page is the only
//              place that knows an endpoint exists.
//
//              WHAT IS DELIBERATELY NOT HERE. No review mutation, no
//              allocation, no plan ratification: this increment delivers the
//              READING and REVIEW-SURFACING loop. The write affordances are
//              rendered (R-500-021 requires the review prompt to be at the
//              point of reading) but wiring each mutation is increment 8's
//              scope alongside baseline export. Stated rather than left to be
//              discovered.
//
// @relation implements:R-310-220
// @relation implements:R-500-015
// =============================================================================

"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useReadyConfig } from "@/app/providers";
import { BaselinePanel } from "@/components/workbench/baseline-panel";
import { ContainerNavigator } from "@/components/workbench/container-navigator";
import { ConversationRail, type RailTurn } from "@/components/workbench/conversation-rail";
import { CoverageRegion } from "@/components/workbench/coverage-region";
import { FindingsRegion } from "@/components/workbench/findings-region";
import { ObjectRegion } from "@/components/workbench/object-region";
import { SelectionProvider, useSelection } from "@/components/workbench/selection-context";
import { ApiClient } from "@/lib/apiClient";
import {
  type BaselineList,
  type BaselineReadinessView,
  type ContainerCoverageView,
  type ContainerSpecView,
  type CoverageFigure,
  containerFigure,
  type DocObjectSummary,
  type RequirementCoverageView,
  type SpeculativeList,
  type SuspectLinkList,
} from "@/lib/workbenchTypes";

/** The cycle a project runs on. Hardcoding one id would be wrong, so the
 *  page reads it from the resolved-cycle call and reports its absence
 *  instead of rendering an empty navigator that looks like a missing
 *  project. */
const DEFAULT_CYCLE = "C-AUTO";

function WorkbenchBody({ projectId }: { projectId: string }) {
  const cfg = useReadyConfig();
  const client = useMemo(() => new ApiClient(cfg), [cfg]);
  const { container, selectContainer } = useSelection();

  const [containers, setContainers] = useState<ContainerSpecView[]>([]);
  const [cycleTitle, setCycleTitle] = useState<string | undefined>(undefined);
  const [cycleError, setCycleError] = useState<string | null>(null);
  const [objects, setObjects] = useState<DocObjectSummary[]>([]);
  const [objectsLoading, setObjectsLoading] = useState(false);
  const [coverage, setCoverage] = useState<ContainerCoverageView | null>(null);
  const [suspect, setSuspect] = useState<SuspectLinkList | null>(null);
  const [speculative, setSpeculative] = useState<SpeculativeList | null>(null);
  const [coverageOf, setCoverageOf] = useState<Record<string, RequirementCoverageView | undefined>>(
    {},
  );
  const [turns, setTurns] = useState<RailTurn[]>([]);
  const [readiness, setReadiness] = useState<BaselineReadinessView | null>(null);
  const [baselines, setBaselines] = useState<BaselineList | null>(null);
  const [cycleId, setCycleId] = useState<string | null>(null);
  const [baselineBusy, setBaselineBusy] = useState(false);
  const [baselineError, setBaselineError] = useState<string | null>(null);

  // --- The cycle, once ------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    client
      .getResolvedCycle(projectId, DEFAULT_CYCLE)
      .then((cycle) => {
        if (cancelled) return;
        setContainers(cycle.containers);
        setCycleTitle(cycle.title);
        setCycleId(cycle.cycle_id);
        // Open the first container of the cascade so the surface is useful
        // on arrival rather than five empty panels.
        const first = [...cycle.containers].sort((a, b) => a.ordinal - b.ordinal)[0];
        if (first) selectContainer(first.slug);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setCycleError(err instanceof Error ? err.message : "cycle unavailable");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client, projectId, selectContainer]);

  // --- Project-wide findings, once ------------------------------------------
  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      client.listSuspectLinks(projectId).catch(() => null),
      client.listSpeculativeObjects(projectId).catch(() => null),
    ]).then(([links, markings]) => {
      if (cancelled) return;
      setSuspect(links);
      setSpeculative(markings);
    });
    return () => {
      cancelled = true;
    };
  }, [client, projectId]);

  // --- The open container ---------------------------------------------------
  useEffect(() => {
    if (!container) {
      setObjects([]);
      setCoverage(null);
      return;
    }
    let cancelled = false;
    setObjectsLoading(true);
    void Promise.all([
      client.listContainerObjects(projectId, container).catch(() => ({ objects: [] })),
      client.getContainerCoverage(projectId, container).catch(() => null),
    ]).then(([list, cov]) => {
      if (cancelled) return;
      setObjects(list.objects);
      setCoverage(cov);
      setObjectsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [client, projectId, container]);

  // --- Baselines, once ------------------------------------------------------
  const refreshBaselines = useCallback(async () => {
    const [verdict, taken] = await Promise.all([
      client.getBaselineReadiness(projectId).catch(() => null),
      client.listBaselines(projectId).catch(() => null),
    ]);
    setReadiness(verdict);
    setBaselines(taken);
  }, [client, projectId]);

  useEffect(() => {
    void refreshBaselines();
  }, [refreshBaselines]);

  const takeBaseline = useCallback(
    (tag: string, note: string) => {
      if (!cycleId) return;
      setBaselineBusy(true);
      setBaselineError(null);
      client
        .createBaseline(projectId, tag, cycleId, note)
        .then(() => refreshBaselines())
        .catch((err: unknown) => setBaselineError(err instanceof Error ? err.message : "refused"))
        .finally(() => setBaselineBusy(false));
    },
    [client, projectId, cycleId, refreshBaselines],
  );

  const renderUrl = useCallback(
    (tag: string, fmt: "docx" | "pdf") => client.baselineRenderUrl(projectId, tag, fmt),
    [client, projectId],
  );

  const requestCoverage = useCallback(
    (requirementId: string) => {
      void client
        .getRequirementCoverage(projectId, requirementId)
        .then((view) => setCoverageOf((prev) => ({ ...prev, [requirementId]: view })))
        .catch(() => undefined);
    },
    [client, projectId],
  );

  // `auto-accepted` requirement ids, read from the coverage views that have
  // been fetched. Counted from links actually marked auto-accepted, never
  // inferred from a difference between totals (R-500-019).
  const autoAcceptedIds = useMemo(() => {
    const ids = new Set<string>();
    for (const [requirementId, view] of Object.entries(coverageOf)) {
      if (!view) continue;
      const auto = view.allocations.some((allocation) =>
        allocation.links.some((link) => link.state === "auto-accepted"),
      );
      if (auto) ids.add(requirementId);
    }
    return ids;
  }, [coverageOf]);

  const figure: CoverageFigure | null = useMemo(
    () => (coverage ? containerFigure(coverage, autoAcceptedIds) : null),
    [coverage, autoAcceptedIds],
  );

  // object id -> what it answers, derived from the coverage views already in
  // hand. Empty until a requirement's coverage has been fetched, which is
  // why the region renders "answers nothing yet" rather than a spinner.
  const coversOf = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const [requirementId, view] of Object.entries(coverageOf)) {
      if (!view) continue;
      for (const allocation of view.allocations) {
        for (const link of allocation.links) {
          map[link.object_id] = [...(map[link.object_id] ?? []), requirementId];
        }
      }
    }
    // Every allocated requirement of the open container is offered on every
    // object as a link candidate: the reader needs to be able to open what
    // the container owes, not only what it already answers.
    if (coverage) {
      for (const obj of objects) {
        map[obj.object_id] = Array.from(
          new Set([...(map[obj.object_id] ?? []), ...coverage.allocated]),
        );
      }
    }
    return map;
  }, [coverageOf, coverage, objects]);

  const uncoveredCounts = useMemo(() => {
    const counts: Record<string, number | undefined> = {};
    if (coverage) counts[coverage.container] = coverage.uncovered.length;
    return counts;
  }, [coverage]);

  const send = useCallback((message: string, context: string) => {
    setTurns((prev) => [
      ...prev,
      { id: `t${prev.length + 1}`, role: "user", text: message, context },
    ]);
  }, []);

  return (
    <main className="flex h-[calc(100vh-4rem)] flex-col" data-testid="workbench">
      {cycleError ? (
        <p
          className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-900"
          data-testid="cycle-error"
        >
          Cycle unavailable: {cycleError}
        </p>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <div className="w-56 shrink-0">
          <ContainerNavigator
            containers={containers}
            uncoveredCounts={uncoveredCounts}
            cycleTitle={cycleTitle}
          />
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1">
            <ObjectRegion
              objects={objects}
              coversOf={coversOf}
              coverageOf={coverageOf}
              onRequestCoverage={requestCoverage}
              loading={objectsLoading}
            />
          </div>
          {/* Permanently mounted, no tab, no collapse (R-500-018). */}
          <div className="h-56 shrink-0">
            <FindingsRegion coverage={coverage} suspect={suspect} speculative={speculative} />
          </div>
        </div>

        <div className="w-72 shrink-0">
          <CoverageRegion
            coverage={coverage}
            figure={figure}
            clusters={[]}
            requirementCount={coverage?.allocated.length ?? 0}
            autoAcceptedIds={autoAcceptedIds}
          />
        </div>

        <div className="w-72 shrink-0">
          <BaselinePanel
            readiness={readiness}
            baselines={baselines}
            cycleId={cycleId}
            onTake={takeBaseline}
            renderUrl={renderUrl}
            busy={baselineBusy}
            error={baselineError}
          />
        </div>

        <div className="w-72 shrink-0">
          <ConversationRail turns={turns} onSend={send} />
        </div>
      </div>
    </main>
  );
}

export default function WorkbenchPage() {
  // `useParams` rather than `use(params)`: this is a client component, where
  // the promise form is a server-component idiom that SUSPENDS — with no
  // boundary above it the whole surface renders nothing at all. Every other
  // page in this app uses `useParams`, for the same reason.
  const params = useParams<{ pid: string }>();
  return (
    <SelectionProvider>
      <WorkbenchBody projectId={params.pid} />
    </SelectionProvider>
  );
}
