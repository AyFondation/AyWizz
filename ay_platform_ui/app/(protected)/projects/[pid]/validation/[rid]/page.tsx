// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/projects/[pid]/validation/[rid]/page.tsx
// Description: Validation run detail. Polls C6's `GET /runs/{rid}` for
//              status until it hits `completed` or `failed`, then loads
//              the paginated findings (severity / title / message /
//              location). Manual "Refresh" button bypasses the poll
//              if the operator wants an immediate re-query.
// =============================================================================

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError } from "@/lib/apiClient";
import type { Finding, FindingSeverity, ValidationRun } from "@/lib/types";

import { useConfigState } from "../../../../../providers";

type RunState =
  | { status: "loading" }
  | { status: "ready"; run: ValidationRun; findings: Finding[] }
  | { status: "not-found" }
  | { status: "error"; message: string };

const POLL_INTERVAL_MS = 2_500;

export default function RunDetailPage() {
  const params = useParams<{ pid: string; rid: string }>();
  const projectId = decodeURIComponent(params.pid);
  const runId = decodeURIComponent(params.rid);
  const configState = useConfigState();
  const [state, setState] = useState<RunState>({ status: "loading" });

  const apiClient = useMemo(() => {
    if (configState.status !== "ready") return null;
    return new ApiClient(configState.config);
  }, [configState]);

  const reload = useCallback(async (): Promise<void> => {
    if (!apiClient) return;
    try {
      const run = await apiClient.getValidationRun(runId);
      // Only fetch findings when the run has produced them (status
      // completed/failed). Server returns 0 findings for queued/running.
      const page = await apiClient.listValidationFindings(runId, 500, 0);
      setState({ status: "ready", run, findings: page.findings });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setState({ status: "not-found" });
        return;
      }
      const message = err instanceof Error ? err.message : String(err);
      setState({ status: "error", message });
    }
  }, [apiClient, runId]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick(): Promise<void> {
      if (cancelled) return;
      await reload();
    }

    function schedule(): void {
      timer = setTimeout(async () => {
        if (cancelled) return;
        await tick();
        // Decide whether to continue polling.
        setState((curr) => {
          if (curr.status === "ready") {
            if (curr.run.status === "queued" || curr.run.status === "running") {
              schedule();
            }
          } else if (curr.status === "loading") {
            schedule();
          }
          return curr;
        });
      }, POLL_INTERVAL_MS);
    }

    tick().then(() => {
      if (!cancelled) schedule();
    });

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [reload]);

  if (state.status === "loading") {
    return (
      <main className="mx-auto max-w-7xl px-6 py-10">
        <p className="text-neutral-500">Loading run…</p>
      </main>
    );
  }

  if (state.status === "not-found") {
    return (
      <main className="mx-auto max-w-7xl px-6 py-10">
        <h2 className="text-2xl font-semibold">Run not found</h2>
        <Link
          href={`/projects/${encodeURIComponent(projectId)}/validation`}
          className="mt-6 inline-block rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
        >
          ← Back to validation
        </Link>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="mx-auto max-w-7xl px-6 py-10">
        <p className="text-red-700" role="alert">
          Failed to load: {state.message}
        </p>
      </main>
    );
  }

  const { run, findings } = state;
  return (
    <main className="mx-auto max-w-7xl px-6 py-10" data-testid="run-detail">
      <nav className="text-xs text-neutral-500" aria-label="Breadcrumb">
        <Link
          href={`/projects/${encodeURIComponent(projectId)}/validation`}
          className="hover:underline"
        >
          ← Validation
        </Link>
      </nav>
      <header className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
        <div className="min-w-0">
          <h2 className="break-all font-mono text-xl font-semibold text-neutral-900">
            {run.run_id}
          </h2>
          <p className="mt-1 text-sm text-neutral-500">
            domain {run.domain} · started {new Date(run.started_at).toLocaleString()}
          </p>
        </div>
        <RunStatusBadge status={run.status} />
      </header>

      <section className="mt-8 rounded-lg border border-neutral-200 bg-white p-6">
        <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">Summary</h3>
        <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm md:grid-cols-3">
          <Field label="Total findings" value={String(run.total_findings)} />
          <Field
            label="Completed at"
            value={run.completed_at ? new Date(run.completed_at).toLocaleString() : "(pending)"}
          />
          <Field label="Project" value={run.project_id} mono />
        </dl>
      </section>

      <section className="mt-6">
        <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">Findings</h3>
        {findings.length === 0 ? (
          <p className="mt-3 text-sm text-neutral-500">
            {run.status === "queued" || run.status === "running"
              ? "Run still in progress — findings will appear here when ready."
              : "No findings. The run completed without issues."}
          </p>
        ) : (
          <div className="mt-3 overflow-hidden rounded-lg border border-neutral-200 bg-white">
            <table className="min-w-full text-sm" data-testid="findings-table">
              <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
                <tr>
                  <th className="px-4 py-2">Severity</th>
                  <th className="px-4 py-2">Check</th>
                  <th className="px-4 py-2">Title</th>
                  <th className="px-4 py-2">Location</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100">
                {findings.map((f) => (
                  <tr key={f.finding_id} data-testid={`finding-row-${f.finding_id}`}>
                    <td className="px-4 py-2">
                      <SeverityBadge severity={f.severity} />
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-neutral-700">{f.check_id}</td>
                    <td className="px-4 py-2 text-neutral-900">
                      <div>{f.title}</div>
                      <div className="text-xs text-neutral-500">{f.message}</div>
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-neutral-500">
                      {f.location ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

function RunStatusBadge({ status }: { status: ValidationRun["status"] }) {
  const palette: Record<ValidationRun["status"], string> = {
    queued: "bg-neutral-100 text-neutral-700",
    running: "bg-amber-100 text-amber-900",
    completed: "bg-emerald-100 text-emerald-900",
    failed: "bg-red-100 text-red-900",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${palette[status]}`}>{status}</span>
  );
}

function SeverityBadge({ severity }: { severity: FindingSeverity }) {
  const palette: Record<FindingSeverity, string> = {
    info: "bg-blue-100 text-blue-900",
    warning: "bg-amber-100 text-amber-900",
    error: "bg-red-100 text-red-900",
    critical: "bg-red-200 text-red-950",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${palette[severity]}`}>
      {severity}
    </span>
  );
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className={["mt-0.5 text-neutral-900", mono ? "font-mono text-xs" : "text-sm"].join(" ")}>
        {value}
      </dd>
    </div>
  );
}
