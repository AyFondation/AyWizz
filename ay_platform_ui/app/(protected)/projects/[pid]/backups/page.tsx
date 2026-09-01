// =============================================================================
// File: page.tsx
// Version: 1
// Path: ay_platform_ui/app/(protected)/projects/[pid]/backups/page.tsx
// Description: Project Backups page (D-022 / 900-SPEC). Lists the project's
//              stored backups, creates a snapshot, downloads an archive
//              (auth-bearing fetch → browser save), and restores-as-new. Gated
//              server-side (R-900-011: project_owner / tenant_admin /
//              platform_manager) — the UI surfaces the 403/404 as-is.
// =============================================================================

"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useReadyConfig } from "@/app/providers";
import { ApiClient, ApiError, apiErrorDetail } from "@/lib/apiClient";
import type { BackupRecord } from "@/lib/types";

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export default function BackupsPage() {
  const params = useParams<{ pid: string }>();
  const pid = decodeURIComponent(params.pid);
  const cfg = useReadyConfig();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [backups, setBackups] = useState<BackupRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    apiClient
      .listProjectBackups(pid)
      .then(setBackups)
      .catch((err) =>
        setError(err instanceof ApiError ? `Load failed (${err.status})` : "Load failed."),
      );
  }, [apiClient, pid]);

  useEffect(reload, [reload]);

  const run = useCallback(
    async (fn: () => Promise<string>) => {
      setError(null);
      setNotice(null);
      setBusy(true);
      try {
        setNotice(await fn());
        reload();
      } catch (err) {
        const detail = apiErrorDetail(err);
        setError(detail ?? (err instanceof ApiError ? `Failed (${err.status})` : "Failed."));
      } finally {
        setBusy(false);
      }
    },
    [reload],
  );

  const onSnapshot = () =>
    run(async () => {
      const rec = await apiClient.createProjectSnapshot(pid);
      return `Snapshot created (${rec.backup_id.slice(0, 8)}…, ${fmtBytes(rec.size_bytes)}).`;
    });

  const onDownload = (b: BackupRecord) =>
    run(async () => {
      const blob = await apiClient.downloadProjectBackup(pid, b.backup_id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${b.backup_id}.tar.gz`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      return "Download started.";
    });

  const onRestore = (b: BackupRecord) =>
    run(async () => {
      const report = await apiClient.restoreProjectBackup(pid, b.backup_id, {
        dry_run: false,
      });
      return `Restored as new project ${report.new_project_id}.`;
    });

  return (
    <main className="mx-auto max-w-5xl px-6 py-6" data-testid="backups-page">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-neutral-900">Backups</h1>
          <p className="text-sm text-neutral-500">
            Snapshot this project, download the archive, or restore it as a new project.
          </p>
        </div>
        <button
          type="button"
          onClick={onSnapshot}
          disabled={busy}
          className="rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="backups-snapshot"
        >
          Create snapshot
        </button>
      </div>

      {error ? (
        <p
          role="alert"
          data-testid="backups-error"
          className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {error}
        </p>
      ) : null}
      {notice ? (
        <p
          data-testid="backups-notice"
          className="mb-3 rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800"
        >
          {notice}
        </p>
      ) : null}

      {backups === null ? (
        <p className="text-neutral-500">Loading…</p>
      ) : backups.length === 0 ? (
        <p className="text-neutral-500" data-testid="backups-empty">
          No backups yet. Create a snapshot to get started.
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-neutral-200">
          <table className="w-full text-sm" data-testid="backups-table">
            <thead className="bg-neutral-50 text-left text-neutral-600">
              <tr>
                <th className="px-3 py-2 font-medium">Created</th>
                <th className="px-3 py-2 font-medium">Size</th>
                <th className="px-3 py-2 font-medium">Origin</th>
                <th className="px-3 py-2 font-medium">By</th>
                <th className="px-3 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((b) => (
                <tr
                  key={b.backup_id}
                  className="border-t border-neutral-100"
                  data-testid={`backups-row-${b.backup_id}`}
                >
                  <td className="px-3 py-2 text-neutral-700">{b.created_at}</td>
                  <td className="px-3 py-2 text-neutral-700">{fmtBytes(b.size_bytes)}</td>
                  <td className="px-3 py-2 text-neutral-700">{b.origin}</td>
                  <td className="px-3 py-2 text-neutral-700">{b.created_by}</td>
                  <td className="px-3 py-2">
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => onDownload(b)}
                        disabled={busy}
                        className="rounded border border-neutral-300 px-2 py-1 text-neutral-700 hover:bg-neutral-100 disabled:opacity-50"
                        data-testid={`backups-download-${b.backup_id}`}
                      >
                        Download
                      </button>
                      <button
                        type="button"
                        onClick={() => onRestore(b)}
                        disabled={busy}
                        className="rounded border border-neutral-300 px-2 py-1 text-neutral-700 hover:bg-neutral-100 disabled:opacity-50"
                        data-testid={`backups-restore-${b.backup_id}`}
                      >
                        Restore as new
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
