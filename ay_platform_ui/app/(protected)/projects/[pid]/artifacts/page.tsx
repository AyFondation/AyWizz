// =============================================================================
// File: page.tsx
// Version: 3
// Path: ay_platform_ui/app/(protected)/projects/[pid]/artifacts/page.tsx
//
// v3 (2026-05-29): fix the Versions tab hanging on "Loading versions…".
// The commits effect listed `commitsLoad.status` as a dependency AND
// called setCommitsLoad("loading"), so the resulting re-render re-ran the
// effect, whose cleanup cancelled the in-flight commits fetch before its
// result could land. Dropped the self-triggering dependency (status is now
// only a cache guard). Surfaced by tests/integration/artifacts.test.tsx.
// Description: Project artifacts browser (Pass 1 of the Code source /
//              DocGen feature, R-200-131). Lists runs in a left
//              column, the selected run's file tree underneath, and
//              the selected file's content in a viewer on the right.
//              Transparent backend : the UX never exposes MinIO ;
//              every blob transits through the C4 REST surface.
//
//              v2 (R-500-010 v2) : the DocGen profile now renders the
//              full <LiveDocsManager> here (file manager : create / edit
//              / rename / move / delete files & folders ; default root
//              + empty-state). The Code profile keeps the original
//              read-only artifact-runs browser for code-generation
//              outputs. Selection happens via `profileId === "docgen"`.
//
//              v1 ships a basic `<pre>`-based viewer ; a Monaco
//              upgrade is one swap away once `@monaco-editor/react`
//              is installed (see `lib/artifact-viewer.tsx`).
// =============================================================================

"use client";

import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { useReadyConfig } from "@/app/providers";
import { CodeViewer } from "@/components/code-viewer";
import { FileTree } from "@/components/file-tree";
import { LiveDocsManager } from "@/components/live-docs-manager";
import { ApiClient, ApiError } from "@/lib/apiClient";
import { resolveProfile } from "@/lib/profiles/registry";
import type { ArtifactCommit, ArtifactNode, ArtifactRun } from "@/lib/types";

type Tab = "files" | "versions";

interface RunsLoad {
  status: "loading" | "ready" | "error";
  runs?: ArtifactRun[];
  error?: string;
}

interface TreeLoad {
  status: "idle" | "loading" | "ready" | "error";
  nodes?: ArtifactNode[];
  error?: string;
}

interface BlobLoad {
  status: "idle" | "loading" | "ready" | "binary" | "error";
  text?: string;
  contentType?: string;
  error?: string;
}

interface CommitsLoad {
  status: "idle" | "loading" | "ready" | "error";
  commits?: ArtifactCommit[];
  error?: string;
}

export default function ArtifactsPage() {
  const params = useParams<{ pid: string }>();
  const projectId = decodeURIComponent(params.pid);
  const cfg = useReadyConfig();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [activeTab, setActiveTab] = useState<Tab>("files");
  const [runsLoad, setRunsLoad] = useState<RunsLoad>({ status: "loading" });
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [treeLoad, setTreeLoad] = useState<TreeLoad>({ status: "idle" });
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [blobLoad, setBlobLoad] = useState<BlobLoad>({ status: "idle" });
  const [commitsLoad, setCommitsLoad] = useState<CommitsLoad>({ status: "idle" });
  const [profileId, setProfileId] = useState<string | null>(null);

  // The artifacts route is profile-agnostic (R-200-131) but the
  // label varies : `code` → "Code source", `docgen` → "Documents".
  // We fetch the project once at mount to know which to show.
  useEffect(() => {
    let cancelled = false;
    apiClient
      .getProject(projectId)
      .then((p) => {
        if (!cancelled) setProfileId(p.profile);
      })
      .catch(() => {
        // Profile fetch failure is non-fatal — fall back to the
        // generic "Artifacts" label instead of blocking the page.
        if (!cancelled) setProfileId(null);
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId]);

  const sectionLabel = useMemo(() => {
    const profile = profileId ? resolveProfile(profileId) : null;
    if (profile) {
      const section = profile.sections.find((s) => s.path === "artifacts");
      if (section?.label) return section.label;
    }
    return "Artifacts";
  }, [profileId]);

  // Load the runs list once on mount. Auto-select the most recent
  // run so the operator lands directly on something useful.
  useEffect(() => {
    let cancelled = false;
    apiClient
      .listArtifactRuns(projectId)
      .then((resp) => {
        if (cancelled) return;
        setRunsLoad({ status: "ready", runs: resp.runs });
        if (resp.runs.length > 0) setSelectedRunId(resp.runs[0].run_id);
      })
      .catch((err) => {
        if (cancelled) return;
        setRunsLoad({
          status: "error",
          error: err instanceof ApiError ? `Failed to load runs (${err.status})` : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId]);

  // Whenever the selected run changes, fetch its tree.
  useEffect(() => {
    if (!selectedRunId) {
      setTreeLoad({ status: "idle" });
      return;
    }
    let cancelled = false;
    setTreeLoad({ status: "loading" });
    setSelectedPath(null);
    setBlobLoad({ status: "idle" });
    apiClient
      .getArtifactTree(projectId, selectedRunId)
      .then((resp) => {
        if (cancelled) return;
        setTreeLoad({ status: "ready", nodes: resp.nodes });
        // Auto-select the first file so the viewer isn't empty.
        if (resp.nodes.length > 0) setSelectedPath(resp.nodes[0].path);
      })
      .catch((err) => {
        if (cancelled) return;
        setTreeLoad({
          status: "error",
          error: err instanceof ApiError ? `Failed to load tree (${err.status})` : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, selectedRunId]);

  // Whenever the selected path changes, fetch the blob.
  useEffect(() => {
    if (!selectedRunId || !selectedPath) {
      setBlobLoad({ status: "idle" });
      return;
    }
    let cancelled = false;
    setBlobLoad({ status: "loading" });
    apiClient
      .getArtifactBlobText(projectId, selectedRunId, selectedPath)
      .then(({ text, contentType }) => {
        if (cancelled) return;
        // Heuristic : if the server signalled a non-text Content-Type
        // (image, PDF, binary), surface a download link instead of
        // dumping bytes into <pre>. Pass 2 will integrate proper
        // PDF preview via Monaco / iframe.
        const isText =
          contentType.startsWith("text/") ||
          contentType === "application/json" ||
          contentType === "application/javascript" ||
          contentType === "application/x-python";
        if (isText) {
          setBlobLoad({ status: "ready", text, contentType });
        } else {
          setBlobLoad({ status: "binary", contentType });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setBlobLoad({
          status: "error",
          error: err instanceof ApiError ? `Failed to load file (${err.status})` : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, selectedRunId, selectedPath]);

  // Load commits the first time the "Versions" tab is opened.
  // Subsequent re-clicks are idempotent — the load stays cached.
  // NOTE: `commitsLoad.status` is read as a cache guard but intentionally
  // NOT a dependency. Including it would re-run the effect when this very
  // effect calls setCommitsLoad("loading"), whose cleanup would then flip
  // `cancelled=true` on the in-flight fetch and drop its result — leaving
  // the tab stuck on "Loading versions…". Firing on `activeTab` change is
  // the correct trigger; the guard still short-circuits a cached reload.
  // biome-ignore lint/correctness/useExhaustiveDependencies: status is a cache guard, not a trigger (see note).
  useEffect(() => {
    if (activeTab !== "versions") return;
    if (commitsLoad.status === "ready" || commitsLoad.status === "loading") return;
    let cancelled = false;
    setCommitsLoad({ status: "loading" });
    apiClient
      .listProjectCommits(projectId, 1)
      .then((resp) => {
        if (cancelled) return;
        setCommitsLoad({ status: "ready", commits: resp.commits });
      })
      .catch((err) => {
        if (cancelled) return;
        setCommitsLoad({
          status: "error",
          error: err instanceof ApiError ? `Failed to load commits (${err.status})` : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, activeTab]);

  async function onDownload(): Promise<void> {
    if (!selectedRunId || !selectedPath) return;
    try {
      const { blob, filename } = await apiClient.downloadArtifactBlob(
        projectId,
        selectedRunId,
        selectedPath,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      window.alert(`Download failed: ${String(err)}`);
    }
  }

  // R-500-010 v2 : the DocGen profile uses this section as a full live-docs
  // FILE MANAGER (tree + create / edit / rename / move / delete files &
  // folders + default root + empty-state). The Code profile keeps the
  // existing read-only artifact-runs browser below for code-generation
  // outputs (R-200-131).
  if (profileId === "docgen") {
    return (
      <main className="mx-auto max-w-7xl px-6 py-8" data-testid="artifacts-view-docgen">
        <header>
          <h2 className="text-2xl font-semibold tracking-tight">{sectionLabel}</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Create, edit, rename, move, and delete files and folders for this project.
          </p>
        </header>
        <div className="mt-6">
          <LiveDocsManager projectId={projectId} variant="full" />
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-7xl px-6 py-8" data-testid="artifacts-view">
      <header>
        <h2 className="text-2xl font-semibold tracking-tight">{sectionLabel}</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Browse files produced by each pipeline run. Read-only view ; downloads stream through the
          platform gateway.
        </p>
      </header>

      <nav
        className="mt-6 flex gap-1 border-b border-neutral-200"
        aria-label="Artifacts tabs"
        data-testid="artifacts-tabs"
      >
        <TabButton
          label="Files"
          active={activeTab === "files"}
          onClick={() => setActiveTab("files")}
          testId="artifacts-tab-files"
        />
        <TabButton
          label="Versions"
          active={activeTab === "versions"}
          onClick={() => setActiveTab("versions")}
          testId="artifacts-tab-versions"
        />
      </nav>

      {activeTab === "files" ? (
        <div className="mt-6 grid gap-6 lg:grid-cols-[18rem_1fr]">
          <aside className="space-y-4">
            <RunsList load={runsLoad} selectedRunId={selectedRunId} onSelect={setSelectedRunId} />
            {selectedRunId ? (
              <TreePanel load={treeLoad} selectedPath={selectedPath} onSelect={setSelectedPath} />
            ) : null}
          </aside>

          <section
            className="min-h-[24rem] rounded-lg border border-neutral-200 bg-white"
            data-testid="artifacts-viewer-pane"
          >
            {selectedPath ? (
              <ViewerHeader
                path={selectedPath}
                onDownload={onDownload}
                contentType={
                  blobLoad.status === "ready" || blobLoad.status === "binary"
                    ? blobLoad.contentType
                    : undefined
                }
              />
            ) : null}
            <Viewer blobLoad={blobLoad} selectedPath={selectedPath} />
          </section>
        </div>
      ) : (
        <CommitsPanel load={commitsLoad} />
      )}
    </main>
  );
}

function TabButton({
  label,
  active,
  onClick,
  testId,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "border-blue-600 text-blue-700"
          : "border-transparent text-neutral-600 hover:text-neutral-900",
      ].join(" ")}
      aria-current={active ? "page" : undefined}
      data-testid={testId}
    >
      {label}
    </button>
  );
}

function CommitsPanel({ load }: { load: CommitsLoad }) {
  return (
    <section
      className="mt-6 rounded-lg border border-neutral-200 bg-white"
      data-testid="artifacts-commits-panel"
    >
      <header className="border-b border-neutral-200 px-4 py-3">
        <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-500">Versions</h3>
        <p className="mt-0.5 text-xs text-neutral-500">
          Commit history of the project, most recent first. One commit per file pushed at run
          completion.
        </p>
      </header>
      {load.status === "idle" || load.status === "loading" ? (
        <p className="px-4 py-6 text-sm text-neutral-500">Loading versions…</p>
      ) : load.status === "error" ? (
        <p className="px-4 py-6 text-sm text-red-700" role="alert">
          {load.error}
        </p>
      ) : !load.commits || load.commits.length === 0 ? (
        <p className="px-4 py-6 text-sm text-neutral-500">
          No versions yet. They land here automatically once a pipeline run completes.
        </p>
      ) : (
        <ul className="divide-y divide-neutral-100">
          {load.commits.map((c) => (
            <li
              key={c.sha}
              className="grid grid-cols-[8rem_1fr_10rem] items-center gap-4 px-4 py-2"
              data-testid={`artifacts-commit-${c.sha}`}
            >
              <span className="truncate font-mono text-xs text-neutral-500">
                {c.sha.slice(0, 8)}
              </span>
              <span className="truncate text-sm text-neutral-900" title={c.message}>
                {c.message.split("\n")[0]}
              </span>
              <span className="truncate text-right text-xs text-neutral-500">
                {c.author_name} · {new Date(c.committed_at).toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RunsList({
  load,
  selectedRunId,
  onSelect,
}: {
  load: RunsLoad;
  selectedRunId: string | null;
  onSelect: (rid: string) => void;
}) {
  return (
    <div
      className="rounded-lg border border-neutral-200 bg-white"
      data-testid="artifacts-runs-list"
    >
      <header className="border-b border-neutral-200 px-3 py-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
        Runs
      </header>
      {load.status === "loading" ? (
        <p className="px-3 py-3 text-sm text-neutral-500">Loading runs…</p>
      ) : load.status === "error" ? (
        <p className="px-3 py-3 text-sm text-red-700" role="alert">
          {load.error}
        </p>
      ) : !load.runs || load.runs.length === 0 ? (
        <p className="px-3 py-3 text-sm text-neutral-500">
          No artifacts yet. They land here once a pipeline run completes.
        </p>
      ) : (
        <ul className="divide-y divide-neutral-100">
          {load.runs.map((r) => {
            const active = r.run_id === selectedRunId;
            return (
              <li key={r.run_id}>
                <button
                  type="button"
                  onClick={() => onSelect(r.run_id)}
                  className={[
                    "block w-full px-3 py-2 text-left text-sm transition-colors",
                    active ? "bg-blue-50 text-blue-900" : "text-neutral-700 hover:bg-neutral-50",
                  ].join(" ")}
                  data-testid={`artifacts-run-${r.run_id}`}
                  data-active={active ? "true" : "false"}
                >
                  <span className="block truncate font-medium">
                    {r.label ?? r.run_id.slice(0, 8)}
                  </span>
                  <span className="mt-0.5 block text-xs text-neutral-500">
                    {r.file_count} file{r.file_count === 1 ? "" : "s"} ·{" "}
                    {formatBytes(r.total_bytes)} · {r.status}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function TreePanel({
  load,
  selectedPath,
  onSelect,
}: {
  load: TreeLoad;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white" data-testid="artifacts-tree">
      <header className="border-b border-neutral-200 px-3 py-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
        Files
      </header>
      {load.status === "loading" ? (
        <p className="px-3 py-3 text-sm text-neutral-500">Loading tree…</p>
      ) : load.status === "error" ? (
        <p className="px-3 py-3 text-sm text-red-700" role="alert">
          {load.error}
        </p>
      ) : (
        <FileTree nodes={load.nodes ?? []} selectedPath={selectedPath} onSelect={onSelect} />
      )}
    </div>
  );
}

function ViewerHeader({
  path,
  contentType,
  onDownload,
}: {
  path: string;
  contentType?: string;
  onDownload: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-neutral-200 px-4 py-2">
      <div className="min-w-0">
        <p
          className="truncate font-mono text-sm text-neutral-900"
          data-testid="artifacts-current-path"
        >
          {path}
        </p>
        {contentType ? <p className="truncate text-xs text-neutral-500">{contentType}</p> : null}
      </div>
      <button
        type="button"
        onClick={onDownload}
        className="shrink-0 rounded-md border border-neutral-300 px-3 py-1 text-xs font-medium text-neutral-700 hover:bg-neutral-50"
        data-testid="artifacts-download"
      >
        Download
      </button>
    </div>
  );
}

function Viewer({ blobLoad, selectedPath }: { blobLoad: BlobLoad; selectedPath: string | null }) {
  if (!selectedPath) {
    return (
      <p className="px-4 py-10 text-center text-sm text-neutral-500">
        Pick a file in the tree to preview it.
      </p>
    );
  }
  if (blobLoad.status === "loading") {
    return <p className="px-4 py-10 text-sm text-neutral-500">Loading file…</p>;
  }
  if (blobLoad.status === "error") {
    return (
      <p className="px-4 py-10 text-sm text-red-700" role="alert">
        {blobLoad.error}
      </p>
    );
  }
  if (blobLoad.status === "binary") {
    return (
      <p className="px-4 py-10 text-sm text-neutral-500">
        Binary file ({blobLoad.contentType}). Use the Download button to retrieve it.
      </p>
    );
  }
  if (blobLoad.status === "ready" && blobLoad.text != null) {
    return <CodeViewer text={blobLoad.text} path={selectedPath} />;
  }
  return null;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
