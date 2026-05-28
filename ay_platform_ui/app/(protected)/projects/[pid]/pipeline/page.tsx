// =============================================================================
// File: page.tsx
// Version: 3
// Path: ay_platform_ui/app/(protected)/projects/[pid]/pipeline/page.tsx
// Description: Pipeline trigger page for the `code` profile. Lets the
//              operator state a goal, fires a C4 orchestrator run,
//              polls its phase, exposes the Gate A approval button
//              when the run pauses on PLAN, then redirects to the
//              Code source section on completion so the generated
//              files are immediately browsable (R-200-150..152).
//
//              Transparent backend invariant : no mention of MinIO /
//              Gitea / the LLM provider here — the operator just sees
//              a pipeline.
//
//              v2 (2026-05-14) : run state persisted in the URL via
//              `?run=<run_id>`. The Pipeline page re-fetches on mount
//              and resumes polling, so navigating to Conversations /
//              Requirements / Code source and back keeps the run
//              visible. F5 also restores. Clearing the goal textarea
//              + starting a fresh run pushes a new URL.
//
//              v3 (2026-05-20) : BLOCKED runs surface two operator
//              controls — Retry (re-attempt the failing phase per
//              R-200-021) and Abort (terminate the run). Both call
//              `POST /api/v1/orchestrator/runs/{id}/resume`. `admin`
//              role is enforced server-side ; non-admins see a 403
//              from the request. `skip-phase` deferred (Q-200-009).
//
//              v4 (2026-05-20) : Tranche B — live <RunTrace> timeline
//              renders the run's TraceEvent ledger (R-500-009) ; older
//              events are loaded lazily via the paginated /trace
//              endpoint. A <SteerComposer> is visible while the run is
//              RUNNING and POSTs operator hints to /steer (R-200-202).
// =============================================================================

"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useReadyConfig } from "@/app/providers";
import { RunTrace, SteerComposer } from "@/components/run-trace";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type {
  OrchestratorPhase,
  OrchestratorRun,
  OrchestratorRunResumeStrategy,
  OrchestratorRunStatus,
  TraceEvent,
} from "@/lib/types";

const _PHASE_ORDER: OrchestratorPhase[] = ["brainstorm", "spec", "plan", "generate", "review"];

const _PHASE_LABEL: Record<OrchestratorPhase, string> = {
  brainstorm: "Brainstorm",
  spec: "Spec",
  plan: "Plan",
  generate: "Generate",
  review: "Review",
};

const _STATUS_LABEL: Record<OrchestratorRunStatus, string> = {
  running: "Running",
  completed: "Completed",
  blocked: "Blocked",
};

interface RunLoad {
  status: "idle" | "creating" | "polling" | "ready" | "error";
  run?: OrchestratorRun;
  error?: string;
}

export default function PipelinePage() {
  const params = useParams<{ pid: string }>();
  const projectId = decodeURIComponent(params.pid);
  const router = useRouter();
  const searchParams = useSearchParams();
  const cfg = useReadyConfig();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  // Stable per-page-mount session id — used to detect concurrent runs
  // server-side (R-200-002). One UUID per browser tab navigation here.
  const sessionId = useMemo(() => `ui-${crypto.randomUUID()}`, []);

  // Run id sourced from `?run=<id>` in the URL — survives navigation
  // away from the page and F5. `null` means "no active run yet".
  const urlRunId = searchParams.get("run");

  const [goal, setGoal] = useState("");
  const [runLoad, setRunLoad] = useState<RunLoad>({ status: "idle" });
  const [approving, setApproving] = useState(false);
  const [resuming, setResuming] = useState<OrchestratorRunResumeStrategy | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Older trace events fetched on demand via the paginated /trace
  // endpoint. They sit BENEATH the sliding-window `run.trace` block
  // in display order (newest-first stays unchanged on top).
  const [olderTrace, setOlderTrace] = useState<TraceEvent[]>([]);
  const [loadingMoreTrace, setLoadingMoreTrace] = useState(false);
  const [steerError, setSteerError] = useState<string | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  // Poll the run state every 2 s while the run is RUNNING and not
  // paused on Gate A (paused = phase=plan + status=running but no
  // further auto-advance until /feedback). We poll in either case
  // because the backend may still be mid-phase ; the UI relies on
  // the snapshot's `(phase, status)` to decide what to display.
  const startPolling = useCallback(
    (runId: string) => {
      stopPolling();
      const tick = async () => {
        try {
          const fresh = await apiClient.getOrchestratorRun(runId);
          setRunLoad({ status: "polling", run: fresh });
          if (fresh.status === "running") {
            pollTimer.current = setTimeout(tick, 2000);
            return;
          }
          setRunLoad({ status: "ready", run: fresh });
          stopPolling();
        } catch (err) {
          setRunLoad({
            status: "error",
            error: err instanceof ApiError ? `Poll failed (${err.status})` : String(err),
          });
          stopPolling();
        }
      };
      pollTimer.current = setTimeout(tick, 2000);
    },
    [apiClient, stopPolling],
  );

  useEffect(() => stopPolling, [stopPolling]);

  // Restore the run from the URL on mount (or when `?run=<id>` changes).
  // Pushed by `submitRun` AND by external navigation (e.g. operator
  // pasting a deep link). Fetch the latest snapshot and resume polling
  // when still running.
  useEffect(() => {
    if (!urlRunId) {
      // No active run in the URL — drop any stale state from a prior
      // mount of this component (Next.js may not unmount on shallow
      // route changes, so we cannot rely on initial state).
      setRunLoad((prev) => (prev.status === "idle" ? prev : { status: "idle" }));
      return;
    }
    // Skip the refetch when we already have this run in state — avoids
    // a flicker after `submitRun` (which sets state then pushes URL).
    if (runLoad.run?.run_id === urlRunId) return;
    let cancelled = false;
    apiClient
      .getOrchestratorRun(urlRunId)
      .then((run) => {
        if (cancelled) return;
        setRunLoad({ status: "ready", run });
        if (run.status === "running") {
          startPolling(run.run_id);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setRunLoad({
          status: "error",
          error:
            err instanceof ApiError
              ? `Could not load run ${urlRunId.slice(0, 8)} (${err.status})`
              : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, urlRunId, startPolling, runLoad.run?.run_id]);

  const submitRun = useCallback(async () => {
    const cleaned = goal.trim();
    if (!cleaned) return;
    setRunLoad({ status: "creating" });
    try {
      const run = await apiClient.createOrchestratorRun({
        project_id: projectId,
        session_id: sessionId,
        initial_prompt: cleaned,
      });
      // Brainstorm + spec + plan ran inline server-side ; the response
      // typically lands with current_phase=plan, status=running (paused
      // on Gate A). We immediately move into the "ready" panel and let
      // the operator approve. Polling starts only if it's still running
      // beyond plan (defensive — covers a future async refactor).
      setRunLoad({ status: "ready", run });
      // Persist the run id in the URL — survives navigation + F5 +
      // shareable as a deep link.
      router.replace(
        `/projects/${encodeURIComponent(projectId)}/pipeline?run=${encodeURIComponent(run.run_id)}`,
      );
      if (run.status === "running" && run.current_phase !== "plan") {
        startPolling(run.run_id);
      }
    } catch (err) {
      setRunLoad({
        status: "error",
        error:
          err instanceof ApiError
            ? `Run creation failed (${err.status}: ${err.body || "no body"})`
            : String(err),
      });
    }
  }, [apiClient, goal, projectId, sessionId, startPolling, router]);

  const loadMoreTrace = useCallback(
    async (beforeIso: string) => {
      if (!runLoad.run || loadingMoreTrace) return;
      setLoadingMoreTrace(true);
      try {
        const slice = await apiClient.readOrchestratorTrace(runLoad.run.run_id, {
          before: beforeIso,
          limit: 200,
        });
        setOlderTrace((prev) => [...prev, ...slice]);
      } catch (err) {
        // Non-fatal — keep the existing slice visible.
        if (err instanceof ApiError) {
          setSteerError(`Trace pagination failed (${err.status})`);
        }
      } finally {
        setLoadingMoreTrace(false);
      }
    },
    [apiClient, runLoad.run, loadingMoreTrace],
  );

  const submitSteer = useCallback(
    async (message: string) => {
      if (!runLoad.run) return;
      setSteerError(null);
      try {
        const next = await apiClient.steerOrchestratorRun(runLoad.run.run_id, {
          message,
        });
        setRunLoad({ status: "ready", run: next });
        if (next.status === "running") {
          startPolling(next.run_id);
        }
      } catch (err) {
        const msg = err instanceof ApiError ? `Steer rejected (${err.status})` : String(err);
        setSteerError(msg);
        throw err instanceof Error ? err : new Error(msg);
      }
    },
    [apiClient, runLoad.run, startPolling],
  );

  // Reset paginated history when the displayed run changes (new run or
  // deep-link to a different one). Avoids leaking events across runs.
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional reset on run_id change only.
  useEffect(() => {
    setOlderTrace([]);
    setSteerError(null);
  }, [runLoad.run?.run_id]);

  const resumeRun = useCallback(
    async (strategy: OrchestratorRunResumeStrategy) => {
      if (!runLoad.run) return;
      setResuming(strategy);
      try {
        const next = await apiClient.resumeOrchestratorRun(runLoad.run.run_id, strategy);
        setRunLoad({ status: "ready", run: next });
        if (next.status === "running") {
          startPolling(next.run_id);
        }
      } catch (err) {
        setRunLoad({
          status: "error",
          error: err instanceof ApiError ? `Resume failed (${err.status})` : String(err),
        });
      } finally {
        setResuming(null);
      }
    },
    [apiClient, runLoad.run, startPolling],
  );

  const approvePlan = useCallback(async () => {
    if (!runLoad.run) return;
    setApproving(true);
    try {
      const next = await apiClient.submitOrchestratorFeedback(runLoad.run.run_id, {
        phase: "plan",
        approved: true,
      });
      setRunLoad({ status: "ready", run: next });
      if (next.status === "running") {
        startPolling(next.run_id);
      }
    } catch (err) {
      setRunLoad({
        status: "error",
        error: err instanceof ApiError ? `Approval failed (${err.status})` : String(err),
      });
    } finally {
      setApproving(false);
    }
  }, [apiClient, runLoad.run, startPolling]);

  // On completion, redirect to the Code source section so the user
  // sees the generated files immediately. We delay 800 ms so the
  // success state is visible before the navigation.
  useEffect(() => {
    const run = runLoad.run;
    if (!run || run.status !== "completed") return;
    const t = setTimeout(() => {
      router.push(`/projects/${encodeURIComponent(projectId)}/artifacts`);
    }, 800);
    return () => clearTimeout(t);
  }, [runLoad.run, projectId, router]);

  const phaseIdx = runLoad.run ? _PHASE_ORDER.indexOf(runLoad.run.current_phase) : -1;
  const isPausedOnPlan = runLoad.run?.status === "running" && runLoad.run?.current_phase === "plan";
  const isWorking = runLoad.status === "creating" || runLoad.status === "polling";

  return (
    <div className="flex flex-col gap-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold">Pipeline</h1>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Describe what you want generated. The platform will plan, then ask you to approve before
          producing source files into <strong>Code source</strong>.
        </p>
      </header>

      <section className="rounded-lg border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-700 dark:bg-zinc-900">
        <label
          htmlFor="goal"
          className="mb-2 block text-sm font-medium text-zinc-700 dark:text-zinc-200"
        >
          Goal
        </label>
        <textarea
          id="goal"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          rows={4}
          placeholder="e.g. Create a Python module that validates IBAN numbers, with unit tests covering 3 edge cases."
          className="w-full resize-vertical rounded-md border border-zinc-300 bg-white p-3 text-sm text-zinc-900 outline-none focus:border-blue-500 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100"
          disabled={isWorking || runLoad.status === "ready"}
        />
        <div className="mt-3 flex items-center justify-end gap-2">
          {runLoad.run && (
            <button
              type="button"
              onClick={() => {
                stopPolling();
                setGoal("");
                setRunLoad({ status: "idle" });
                router.replace(`/projects/${encodeURIComponent(projectId)}/pipeline`);
              }}
              className="rounded-md border border-zinc-300 bg-white px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-100 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-200"
            >
              New run
            </button>
          )}
          <button
            type="button"
            onClick={submitRun}
            disabled={!goal.trim() || isWorking || runLoad.status === "ready"}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-zinc-400"
          >
            {runLoad.status === "creating" ? "Starting…" : "Run"}
          </button>
        </div>
      </section>

      {runLoad.status === "error" && (
        <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300">
          {runLoad.error ?? "Unknown error"}
        </div>
      )}

      {runLoad.run && (
        <section className="rounded-lg border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-700 dark:bg-zinc-900">
          <header className="mb-4 flex items-center justify-between">
            <div>
              <div className="text-xs font-mono text-zinc-500">
                Run {runLoad.run.run_id.slice(0, 8)}
              </div>
              <div className="mt-0.5 text-sm">
                <span
                  className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${
                    runLoad.run.status === "completed"
                      ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                      : runLoad.run.status === "blocked"
                        ? "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300"
                        : "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300"
                  }`}
                >
                  {_STATUS_LABEL[runLoad.run.status]}
                </span>
              </div>
            </div>
            {isWorking && <span className="text-xs text-zinc-500">Polling…</span>}
          </header>

          {/* Phase stepper */}
          <ol className="mb-4 flex items-center justify-between gap-1">
            {_PHASE_ORDER.map((phase, idx) => {
              const done = phaseIdx > idx || runLoad.run?.status === "completed";
              const active = phaseIdx === idx && runLoad.run?.status !== "completed";
              return (
                <li
                  key={phase}
                  className={`flex flex-1 flex-col items-center text-xs ${
                    done
                      ? "text-green-700 dark:text-green-400"
                      : active
                        ? "text-blue-700 dark:text-blue-400 font-semibold"
                        : "text-zinc-400 dark:text-zinc-500"
                  }`}
                >
                  <div
                    className={`mb-1 h-2 w-full rounded-full ${
                      done
                        ? "bg-green-500"
                        : active
                          ? "bg-blue-500"
                          : "bg-zinc-200 dark:bg-zinc-700"
                    }`}
                  />
                  {_PHASE_LABEL[phase]}
                </li>
              );
            })}
          </ol>

          {isPausedOnPlan && (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-950">
              <p className="mb-3 text-sm text-amber-900 dark:text-amber-200">
                Plan ready — approve to start generation. Gate A (R-200-010) is the operator&apos;s
                final check before the agent writes files.
              </p>
              <button
                type="button"
                onClick={approvePlan}
                disabled={approving}
                className="rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:bg-zinc-400"
              >
                {approving ? "Approving…" : "Approve plan & generate"}
              </button>
            </div>
          )}

          {runLoad.run.status === "completed" && (
            <div className="rounded-md border border-green-300 bg-green-50 p-4 text-sm text-green-800 dark:border-green-700 dark:bg-green-950 dark:text-green-300">
              Run completed. Redirecting to <strong>Code source</strong>…
            </div>
          )}

          {runLoad.run.status === "blocked" && (
            <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300">
              <div>
                Run blocked at <strong>{_PHASE_LABEL[runLoad.run.current_phase]}</strong>.
              </div>
              {runLoad.run.block_reason && (
                <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-red-100 px-2 py-1 font-mono text-xs text-red-900 dark:bg-red-900 dark:text-red-100">
                  {runLoad.run.block_reason}
                </pre>
              )}
              {!runLoad.run.block_reason && runLoad.run.concerns.length === 0 && (
                <div className="mt-2 text-xs text-red-600 dark:text-red-400">
                  No detailed reason was recorded. Inspect <code>c4_orchestrator</code> logs for the
                  full envelope.
                </div>
              )}
              <div className="mt-3 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => resumeRun("retry")}
                  disabled={resuming !== null}
                  className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:bg-zinc-400"
                >
                  {resuming === "retry" ? "Retrying…" : "Retry phase"}
                </button>
                <button
                  type="button"
                  onClick={() => resumeRun("abort")}
                  disabled={resuming !== null}
                  className="rounded-md border border-red-400 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100 disabled:bg-zinc-100 disabled:text-zinc-400 dark:border-red-700 dark:bg-zinc-900 dark:text-red-300 dark:hover:bg-red-950"
                >
                  {resuming === "abort" ? "Aborting…" : "Abort run"}
                </button>
                <span className="ml-2 text-[11px] text-red-600 dark:text-red-400">Admin only.</span>
              </div>
            </div>
          )}

          {runLoad.run.concerns.length > 0 && (
            <ul className="mt-4 space-y-1 text-xs">
              {runLoad.run.concerns.map((c) => (
                <li
                  key={`${c.severity}:${c.message}`}
                  className="rounded border border-zinc-200 bg-zinc-50 px-2 py-1 dark:border-zinc-700 dark:bg-zinc-800"
                >
                  <span className="font-medium uppercase tracking-wide">{c.severity}</span>
                  {" — "}
                  {c.message}
                </li>
              ))}
            </ul>
          )}

          {/* Tranche B — live trace timeline + steer composer (R-500-009) */}
          <div className="mt-6 space-y-3">
            <div className="flex items-baseline justify-between">
              <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-200">Run trace</h2>
              <span className="text-[10px] text-zinc-400">
                {runLoad.run.trace.length + olderTrace.length} event
                {runLoad.run.trace.length + olderTrace.length === 1 ? "" : "s"} loaded
              </span>
            </div>
            {runLoad.run.status === "running" && <SteerComposer onSubmit={submitSteer} />}
            {steerError && (
              <div className="rounded-md border border-red-300 bg-red-50 px-3 py-1 text-xs text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300">
                {steerError}
              </div>
            )}
            <RunTrace
              events={[...runLoad.run.trace, ...olderTrace]}
              canLoadMore={
                runLoad.run.trace.length >= 200 ||
                (runLoad.run.trace.length > 0 && olderTrace.length > 0)
              }
              loadingMore={loadingMoreTrace}
              onLoadMore={(beforeIso) => void loadMoreTrace(beforeIso)}
            />
          </div>
        </section>
      )}
    </div>
  );
}
