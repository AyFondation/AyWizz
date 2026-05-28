// =============================================================================
// File: live-docs-manager.tsx
// Version: 2
// Path: ay_platform_ui/components/live-docs-manager.tsx
// Description: Shared live-docs file manager — used by BOTH the project
//              Documents tab AND the Working area Documents pane
//              (R-500-010 v2). Composes the existing <FileTree> +
//              <FileTreeContextMenu> with three additional capabilities :
//                - default root + empty-state affordance (New file /
//                  New folder when the tree is empty) ;
//                - inline content editor (view → Edit → Save) backed by
//                  `PUT /documents/{path}` ;
//                - blank-file creation (`+ New file`) backed by
//                  `POST /documents`.
//              Folder ops (mkdir / rename / move / delete) reuse the
//              R-200-160..163 endpoints already wired in apiClient.
//
//              Two layouts via `variant` prop : `full` (side-by-side
//              tree | editor) for the Documents tab ; `compact` (stacked)
//              for the Working area side pane. The component is
//              self-contained — it owns its fetch + state.
// =============================================================================
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { useReadyConfig } from "@/app/providers";
import { FileTree, type FileTreeContextMenuTarget } from "@/components/file-tree";
import { type ContextMenuAction, FileTreeContextMenu } from "@/components/file-tree-context-menu";
import { ApiClient, ApiError } from "@/lib/apiClient";
import type { ArtifactNode } from "@/lib/types";

const LIVE_DOCS_RUN_ID = "live-docs";

// Context-menu actions. `appliesTo` filters by node kind (the menu
// component handles the filtering). New-file / New-folder offer to create
// INSIDE the right-clicked folder ; rename / delete work on file or
// folder. Move is handled by drag-and-drop (R-500-010 v2 — same as the
// pre-existing working-area wiring).
const _MENU_ACTIONS: ContextMenuAction[] = [
  { id: "newFile", label: "New file…", appliesTo: "folder" },
  { id: "newFolder", label: "New folder…", appliesTo: "folder" },
  { id: "rename", label: "Rename…", appliesTo: "any" },
  { id: "delete", label: "Delete", appliesTo: "any", destructive: true },
];

export interface LiveDocsManagerProps {
  projectId: string;
  /** `full` = side-by-side tree | editor (Documents tab).
   *  `compact` = stacked tree on top, editor below (Working area pane). */
  variant?: "compact" | "full";
}

export function LiveDocsManager({
  projectId,
  variant = "full",
}: LiveDocsManagerProps): React.JSX.Element {
  const cfg = useReadyConfig();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  const [nodes, setNodes] = useState<ArtifactNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [menu, setMenu] = useState<FileTreeContextMenuTarget | null>(null);
  const [content, setContent] = useState<string>("");
  const [contentLoading, setContentLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  // -------------------------------------------------------------------------
  // Tree fetch + refresh
  // -------------------------------------------------------------------------
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const tree = await apiClient.getArtifactTree(projectId, LIVE_DOCS_RUN_ID);
      setNodes(tree.nodes);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // The live-docs run does not exist yet (no docs in this project).
        // Render the empty-state ; first create call will materialise it.
        setNodes([]);
      } else {
        setError(_msg(e));
      }
    } finally {
      setLoading(false);
    }
  }, [apiClient, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // -------------------------------------------------------------------------
  // Load file content when a file is selected (skip folders).
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!selectedPath) {
      setContent("");
      setEditing(false);
      return;
    }
    let cancelled = false;
    setContentLoading(true);
    setEditing(false);
    apiClient
      .getDocumentText(projectId, selectedPath)
      .then(({ text }) => {
        if (!cancelled) setContent(text);
      })
      .catch((e) => {
        if (!cancelled) setError(_msg(e));
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, selectedPath]);

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------
  const newFile = useCallback(
    async (parentDir = "") => {
      const name = window.prompt(`New file name in '${parentDir || "/"}':`)?.trim();
      if (!name) return;
      if (name.includes("/")) {
        window.alert("Name cannot contain '/'.");
        return;
      }
      const path = parentDir ? `${parentDir}/${name}` : name;
      try {
        await apiClient.createDocument(projectId, path, "");
        await refresh();
        setSelectedPath(path);
      } catch (e) {
        setError(_msg(e));
      }
    },
    [apiClient, projectId, refresh],
  );

  const newFolder = useCallback(
    async (parentDir = "") => {
      const name = window.prompt(`New folder name in '${parentDir || "/"}':`)?.trim();
      if (!name) return;
      if (name.includes("/")) {
        window.alert("Name cannot contain '/'.");
        return;
      }
      const path = parentDir ? `${parentDir}/${name}` : name;
      try {
        await apiClient.mkdirDocument(projectId, path);
        await refresh();
      } catch (e) {
        setError(_msg(e));
      }
    },
    [apiClient, projectId, refresh],
  );

  const renameAt = useCallback(
    async (path: string) => {
      const current = path.split("/").pop() ?? path;
      const next = window.prompt("Rename to:", current)?.trim();
      if (!next || next === current) return;
      if (next.includes("/")) {
        window.alert("Name cannot contain '/'.");
        return;
      }
      const parent = path.split("/").slice(0, -1).join("/");
      const toPath = parent ? `${parent}/${next}` : next;
      try {
        await apiClient.renameDocument(projectId, path, toPath);
        if (selectedPath === path) setSelectedPath(toPath);
        await refresh();
      } catch (e) {
        setError(_msg(e));
      }
    },
    [apiClient, projectId, refresh, selectedPath],
  );

  const deleteAt = useCallback(
    async (path: string) => {
      if (!window.confirm(`Delete '${path}' ? This cannot be undone.`)) return;
      try {
        await apiClient.deleteDocument(projectId, path);
        if (selectedPath === path || selectedPath?.startsWith(`${path}/`)) {
          setSelectedPath(null);
        }
        await refresh();
      } catch (e) {
        setError(_msg(e));
      }
    },
    [apiClient, projectId, refresh, selectedPath],
  );

  const moveAt = useCallback(
    async (sourcePath: string, destDir: string) => {
      try {
        await apiClient.moveDocument(projectId, sourcePath, destDir);
        if (selectedPath === sourcePath) {
          const base = sourcePath.split("/").pop() ?? "";
          setSelectedPath(destDir ? `${destDir}/${base}` : base);
        }
        await refresh();
      } catch (e) {
        setError(_msg(e));
      }
    },
    [apiClient, projectId, refresh, selectedPath],
  );

  const save = useCallback(async () => {
    if (!selectedPath) return;
    setSaving(true);
    try {
      await apiClient.updateDocument(projectId, selectedPath, draft);
      setContent(draft);
      setEditing(false);
      await refresh();
    } catch (e) {
      setError(_msg(e));
    } finally {
      setSaving(false);
    }
  }, [apiClient, projectId, refresh, selectedPath, draft]);

  // -------------------------------------------------------------------------
  // Context menu pick handler
  // -------------------------------------------------------------------------
  const onMenuPick = useCallback(
    (actionId: string, target: FileTreeContextMenuTarget) => {
      setMenu(null);
      switch (actionId) {
        case "newFile":
          void newFile(target.path);
          break;
        case "newFolder":
          void newFolder(target.path);
          break;
        case "rename":
          void renameAt(target.path);
          break;
        case "delete":
          void deleteAt(target.path);
          break;
      }
    },
    [newFile, newFolder, renameAt, deleteAt],
  );

  // -------------------------------------------------------------------------
  // Layout
  // -------------------------------------------------------------------------
  const isEmpty = !loading && nodes.length === 0;
  const containerClass =
    variant === "full"
      ? "grid grid-cols-[minmax(240px,320px)_1fr] gap-3 h-full min-h-[420px]"
      : "flex flex-col gap-2";

  return (
    <div className={containerClass} data-testid="live-docs-manager">
      {/* ---------- Left / top : tree panel ---------- */}
      <div className="flex flex-col">
        <div className="flex items-center gap-2 border-b border-neutral-200 px-2 py-2 dark:border-neutral-800">
          <button
            type="button"
            onClick={() => void newFile("")}
            className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700"
            data-testid="live-docs-new-file"
          >
            + New file
          </button>
          <button
            type="button"
            onClick={() => void newFolder("")}
            className="rounded bg-neutral-200 px-2 py-1 text-xs text-neutral-800 hover:bg-neutral-300 dark:bg-neutral-700 dark:text-neutral-100 dark:hover:bg-neutral-600"
            data-testid="live-docs-new-folder"
          >
            + New folder
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            title="Refresh"
            aria-label="Refresh"
            className="ml-auto rounded px-2 py-1 text-xs text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            ⟳
          </button>
        </div>
        {error && (
          <div className="m-2 flex items-start gap-2 rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-800 dark:border-red-700 dark:bg-red-950 dark:text-red-200">
            <span className="flex-1">{error}</span>
            <button type="button" onClick={() => setError(null)} className="underline">
              dismiss
            </button>
          </div>
        )}
        {loading ? (
          <p className="px-3 py-3 text-sm text-neutral-500">Loading…</p>
        ) : isEmpty ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center text-sm text-neutral-500">
            <p className="font-medium">No documents yet</p>
            <p className="text-xs">
              Create the first file or folder — they live at the project root.
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void newFile("")}
                className="rounded bg-blue-600 px-3 py-1 text-xs text-white hover:bg-blue-700"
              >
                New file
              </button>
              <button
                type="button"
                onClick={() => void newFolder("")}
                className="rounded bg-neutral-200 px-3 py-1 text-xs text-neutral-800 dark:bg-neutral-700 dark:text-neutral-100"
              >
                New folder
              </button>
            </div>
          </div>
        ) : (
          <FileTree
            nodes={nodes}
            selectedPath={selectedPath}
            onSelect={(p) => setSelectedPath(p)}
            onContextMenu={(target) => setMenu(target)}
            onMove={(src, dest) => void moveAt(src, dest)}
          />
        )}
      </div>

      {/* ---------- Right / bottom : content panel ---------- */}
      <div
        className={
          variant === "full"
            ? "flex h-full flex-col border-l border-neutral-200 pl-3 dark:border-neutral-800"
            : "flex flex-col border-t border-neutral-200 pt-2 dark:border-neutral-800"
        }
      >
        {selectedPath ? (
          <>
            <div className="flex items-center gap-2 pb-2">
              <span
                className="truncate font-mono text-xs text-neutral-700 dark:text-neutral-200"
                title={selectedPath}
              >
                {selectedPath}
              </span>
              <div className="ml-auto flex gap-2">
                {editing ? (
                  <>
                    <button
                      type="button"
                      onClick={() => void save()}
                      disabled={saving}
                      className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
                      data-testid="live-docs-save"
                    >
                      {saving ? "Saving…" : "Save"}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setEditing(false);
                        setDraft(content);
                      }}
                      disabled={saving}
                      className="rounded bg-neutral-200 px-2 py-1 text-xs dark:bg-neutral-700"
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setDraft(content);
                      setEditing(true);
                    }}
                    disabled={contentLoading}
                    className="rounded bg-neutral-200 px-2 py-1 text-xs dark:bg-neutral-700 disabled:opacity-50"
                    data-testid="live-docs-edit"
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
            {contentLoading ? (
              <p className="py-3 text-sm text-neutral-500">Loading content…</p>
            ) : editing ? (
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                className="flex-1 min-h-[280px] w-full resize-y rounded border border-neutral-300 bg-white p-2 font-mono text-xs dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100"
                data-testid="live-docs-editor"
              />
            ) : (
              <pre className="flex-1 overflow-auto whitespace-pre-wrap rounded bg-neutral-50 p-2 font-mono text-xs text-neutral-800 dark:bg-neutral-900 dark:text-neutral-100">
                {content}
              </pre>
            )}
          </>
        ) : (
          <p className="flex flex-1 items-center justify-center p-6 text-sm text-neutral-500">
            Select a file to view or edit its content.
          </p>
        )}
      </div>

      {menu && (
        <FileTreeContextMenu
          target={menu}
          actions={_MENU_ACTIONS}
          onPick={onMenuPick}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  );
}

function _msg(e: unknown): string {
  if (e instanceof ApiError) {
    return `HTTP ${e.status} — ${e.body || "request failed"}`;
  }
  if (e instanceof Error) return e.message;
  return String(e);
}
