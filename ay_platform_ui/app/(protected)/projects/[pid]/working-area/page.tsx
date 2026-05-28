// =============================================================================
// File: page.tsx
// Version: 10
// Path: ay_platform_ui/app/(protected)/projects/[pid]/working-area/page.tsx
//
// v10 (2026-05-28) : R-500-010 v2 — file-manager parity with the
// Documents tab. Live-docs only : (a) toolbar `+ New file` /
// `+ New folder` buttons above the tree ; (b) root empty-state
// affordance when the live-docs tree has zero nodes ; (c) inline
// content editor in the viewer header (`Edit` → textarea → `Save`)
// backed by `apiClient.updateDocument`. Non-live-docs runs are
// unchanged (read-only with their existing source-files menu).
//
// v9 (2026-05-21) : #6 — full-width layout (dropped the max-w-7xl cap)
// and a mouse-resizable 3-pane row. The left (tree) and right (chat)
// panes have draggable splitters (WAI-ARIA window-splitter pattern,
// also keyboard-resizable) ; the middle viewer flexes. Widths persist
// per-user in preferences (`workingAreaPaneWidths`) and restore on load.
//
// v8 (2026-05-21) : version history viewer (R-200-147). A live-docs
// tree item's context menu gains "View history…" → a panel listing the
// file's revisions (per-file commit history). Picking a revision loads
// that content into the viewer at the chosen commit (`?ref=<sha>`),
// with a "revision <sha> · back to latest" banner. Selecting any file
// via the tree, or switching runs, resets to the latest content.
//
// v7 (2026-05-21) : drag-and-drop relocation replaces the context-menu
// "Move to…" prompt (removed from both _LIVE_DOCS_ACTIONS and
// _SOURCE_ACTIONS). The tree's `onMove` callback fires `handleMove`,
// which routes to moveDocument (live-docs) or moveSource (other runs),
// re-selects the moved file at its new path, and refreshes via docsNonce.
//
// v6 (2026-05-20) : Tranche B §5.18 — non-`live-docs` runs (source
// files) also surface a right-click menu : New folder, Rename, Move,
// Metadata. The Metadata action opens a side panel populated by
// `GET /source/file/{path}/meta` (R-200-173). Delete is omitted from
// the source action set (Q-200-019 — no source-DELETE endpoint in v1).
//
// v5 (2026-05-20) : Tranche B §5.17 — when the active run is
// `live-docs`, the Documents tree exposes a right-click context menu
// (New folder, Rename, Move to…, Delete) backed by the new
// /documents/mkdir|rename|move|DELETE endpoints. Other runs stay
// read-only (no menu) until Bloc C (source-files) lands.
//
// v4 (2026-05-19): Increment 3 phase 3a — selected run/doc are
// mirrored into the cross-nav WorkspaceProvider store (restore-once
// on hydration + persist-on-change), so switching tab or refreshing
// returns the operator to the same run/document.
//
// v3 (2026-05-19): layout fit. `h-[calc(100vh-6rem)]` under-budgeted
// the navbar + version footer chrome, so the 3-pane main overflowed
// the viewport and the chat composer's Send button sat below the
// fold (needed a page scroll). Now `flex flex-col h-[calc(100dvh-
// 8rem)]` with the grid as `flex-1 min-h-0` — fills exactly the
// space under the header, Send always visible, panes scroll
// internally (same "nothing overflows the viewport" behaviour the
// Conversations page got).
// Description: DocGen working area — 3-pane layout dedicated to the
//              chat-driven document generation flow. The Documents
//              tab (artifacts) is for browsing + versioning ; this
//              page is where the operator iterates with the assistant
//              to produce new docs.
//
//              Sourced from the same artifacts surface
//              (`/api/v1/projects/{pid}/artifacts/runs/...`) so the
//              tree shows the same files as Documents/Files. Same
//              conversations as the dedicated Conversations page
//              (both filter `listConversations()` by project_id).
//
//              v2 (2026-05-18) : honours an optional `?path=<doc>`
//              URL param (alongside `?conv=`). The Conversations
//              inline tool-call strip deep-links here so the operator
//              lands on the freshly created / updated document in the
//              viewer with the originating conversation pre-selected
//              (Phase 2.C.3).
//
//              v1 (2026-05-14) : extracted out of the artifacts page
//              after the user split "view + versioning" (Documents)
//              from "chat to generate" (Working area) into two
//              top-level sidebar entries.
// =============================================================================

"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useProjectUi } from "@/app/(protected)/workspace-store";
import { useAuth } from "@/app/auth-provider";
import { useReadyConfig } from "@/app/providers";
import { ChatSidebar, type QuotedSnippet } from "@/components/chat-sidebar";
import { CodeViewer } from "@/components/code-viewer";
import { FileTree, type FileTreeContextMenuTarget } from "@/components/file-tree";
import { type ContextMenuAction, FileTreeContextMenu } from "@/components/file-tree-context-menu";
import { ApiClient, ApiError } from "@/lib/apiClient";
import { readPreferences, writePreferences } from "@/lib/preferences";
import type {
  ArtifactCommit,
  ArtifactNode,
  ArtifactRun,
  PromptReference,
  SourceFileMeta,
} from "@/lib/types";

// #6 — working-area pane sizing. Default + clamped pixel widths for the
// left (file tree) and right (chat) panes ; the middle viewer flexes.
const _DEFAULT_LEFT_W = 256;
const _DEFAULT_RIGHT_W = 352;
const _clampLeft = (w: number): number => Math.min(Math.max(w, 180), 520);
const _clampRight = (w: number): number => Math.min(Math.max(w, 260), 600);

// "Move to…" was removed from these menus in favour of drag-and-drop
// in the tree (drag a row onto a folder, or into empty space for root).
const _LIVE_DOCS_ACTIONS: ContextMenuAction[] = [
  { id: "newFile", label: "New file…", appliesTo: "folder" },
  { id: "mkdir", label: "New folder…", appliesTo: "folder" },
  { id: "rename", label: "Rename…", appliesTo: "any" },
  { id: "history", label: "View history…", appliesTo: "file" },
  { id: "delete", label: "Delete", appliesTo: "file", destructive: true },
  { id: "add-as-ref", label: "Add as reference", appliesTo: "file" },
];

const _SOURCE_ACTIONS: ContextMenuAction[] = [
  { id: "mkdir", label: "New folder…", appliesTo: "folder" },
  { id: "rename", label: "Rename…", appliesTo: "any" },
  { id: "metadata", label: "Metadata", appliesTo: "file" },
  // R-200-175 / P2.2.a — single-file DELETE on source files. Editor+
  // RBAC enforced server-side ; a viewer's right-click click→delete
  // surfaces a 403 toast through `structuralOpError`.
  { id: "delete", label: "Delete", appliesTo: "file", destructive: true },
];

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

export default function WorkingAreaPage() {
  const params = useParams<{ pid: string }>();
  const projectId = decodeURIComponent(params.pid);
  const searchParams = useSearchParams();
  // Optional pre-selected conversation, sourced from the URL
  // `?conv=<id>` param. Populated by the "Open in Working area"
  // button on the Conversations list page.
  const initialConversationId = searchParams.get("conv");
  // Optional pre-selected document path (`?path=<doc>`), set by the
  // Conversations inline tool-call deep-link. Consumed exactly once
  // (on the first tree load that contains it) so later run switches
  // or manual selections aren't hijacked back to it.
  const initialPath = searchParams.get("path");
  const deepLinkConsumedRef = useRef(false);
  const cfg = useReadyConfig();
  const apiClient = useMemo(() => new ApiClient(cfg), [cfg]);

  // Cross-tab-nav UI store (Increment 3 phase 3a). Mirror pattern :
  // local state stays the working source of truth for this page's
  // effect web ; we only RESTORE the last selection from the store
  // once on mount/hydration and PERSIST it on change. So switching
  // tab (or F5) returns the operator to the same run/document.
  const { ui, setUi } = useProjectUi(projectId);
  const restoredRef = useRef(false);

  const [runsLoad, setRunsLoad] = useState<RunsLoad>({ status: "loading" });
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [treeLoad, setTreeLoad] = useState<TreeLoad>({ status: "idle" });
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [blobLoad, setBlobLoad] = useState<BlobLoad>({ status: "idle" });
  const [quoted, setQuoted] = useState<QuotedSnippet | null>(null);
  const [pendingQuote, setPendingQuote] = useState<string | null>(null);
  const [contextTarget, setContextTarget] = useState<FileTreeContextMenuTarget | null>(null);
  const [structuralOpError, setStructuralOpError] = useState<string | null>(null);
  const [metaPanel, setMetaPanel] = useState<SourceFileMeta | null>(null);
  // #3 version history (R-200-147). `historyTarget` is the live-docs
  // path whose revision list is open ; `viewingRef` is the commit SHA
  // currently rendered in the viewer (null = latest content). Picking
  // a revision sets both the path and the ref ; selecting any file via
  // the tree (or switching runs) resets back to latest.
  const [historyTarget, setHistoryTarget] = useState<string | null>(null);
  const [viewingRef, setViewingRef] = useState<string | null>(null);

  // R-500-010 v2 — inline content editor for live-docs. `editing` toggles
  // the viewer between read-only CodeViewer and a textarea ; `draft`
  // holds the working copy ; `saving` is the PUT-in-flight flag. Reset
  // whenever the selected path or viewed revision changes.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  // #6 — resizable 3-pane widths, persisted per-user in preferences.
  // `sub` scopes the stored widths ; null while auth is still loading
  // (we then keep the defaults and skip persistence).
  const { state: authState } = useAuth();
  const sub = authState.status === "authenticated" ? authState.claims.sub : null;
  const [leftW, setLeftW] = useState(_DEFAULT_LEFT_W);
  const [rightW, setRightW] = useState(_DEFAULT_RIGHT_W);
  const paneHydratedRef = useRef(false);
  // Mirror the live widths into a ref so the drag-commit persists the
  // latest values without a stale closure.
  const paneWidthsRef = useRef({ left: leftW, right: rightW });
  paneWidthsRef.current = { left: leftW, right: rightW };

  // Restore the persisted pane widths once, when the user id is known.
  useEffect(() => {
    if (paneHydratedRef.current || !sub) return;
    paneHydratedRef.current = true;
    const stored = readPreferences(sub).workingAreaPaneWidths;
    if (stored) {
      setLeftW(_clampLeft(stored.left));
      setRightW(_clampRight(stored.right));
    }
  }, [sub]);

  // Persist on drag end (pointer up) — not on every pixel.
  const persistPaneWidths = (): void => {
    if (!sub) return;
    writePreferences(sub, { workingAreaPaneWidths: paneWidthsRef.current });
  };
  // Tranche B Bloc D — prompt-attached references the operator has
  // pinned to the next chat turn (R-200-180 / R-500-012). Cleared on
  // successful send by ChatSidebar's `onClearReferences` callback.
  const [references, setReferences] = useState<PromptReference[]>([]);
  // Bumped by the chat sidebar's `onDocsMutated` after a successful
  // create/update/delete tool call. Drives a re-fetch of the runs
  // list + the active run's tree so the new/changed document shows
  // without a manual reload. `live-docs` is auto-selected when it
  // appears (it's where chat-direct mutations land).
  const [docsNonce, setDocsNonce] = useState(0);

  // RESTORE (once) the last run/doc from the cross-nav store. Runs
  // when the store finishes hydrating (sessionStorage) ; guarded so
  // it never fights the user's later selections. If the store is
  // empty (first visit) it no-ops and the normal auto-select wins.
  useEffect(() => {
    if (restoredRef.current) return;
    if (ui.selectedRunId || ui.selectedPath) {
      restoredRef.current = true;
      if (ui.selectedRunId) setSelectedRunId(ui.selectedRunId);
      if (ui.selectedPath) setSelectedPath(ui.selectedPath);
    }
  }, [ui.selectedRunId, ui.selectedPath]);

  // PERSIST run/doc selection so a tab switch or F5 returns here.
  useEffect(() => {
    setUi({ selectedRunId, selectedPath });
  }, [selectedRunId, selectedPath, setUi]);

  // Load runs on mount + after each doc mutation. Auto-select the
  // `live-docs` run if present (chat-direct corpus) else the most
  // recent ; never clobber an explicit user selection that still
  // exists in the refreshed list. `docsNonce` is a re-run trigger,
  // not referenced in the body — biome's static analysis can't see
  // that, hence the suppression (same pattern as the conversations
  // refresh-counter effect).
  // biome-ignore lint/correctness/useExhaustiveDependencies: docsNonce is a refresh trigger
  useEffect(() => {
    let cancelled = false;
    apiClient
      .listArtifactRuns(projectId)
      .then((resp) => {
        if (cancelled) return;
        setRunsLoad({ status: "ready", runs: resp.runs });
        setSelectedRunId((prev) => {
          if (prev && resp.runs.some((r) => r.run_id === prev)) return prev;
          const live = resp.runs.find((r) => r.run_id === "live-docs");
          if (live) return live.run_id;
          return resp.runs.length > 0 ? resp.runs[0].run_id : null;
        });
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
  }, [apiClient, projectId, docsNonce]);

  // Tree on selectedRunId change.
  useEffect(() => {
    if (!selectedRunId) {
      setTreeLoad({ status: "idle" });
      return;
    }
    let cancelled = false;
    setTreeLoad({ status: "loading" });
    setSelectedPath(null);
    setViewingRef(null);
    setBlobLoad({ status: "idle" });
    apiClient
      .getArtifactTree(projectId, selectedRunId)
      .then((resp) => {
        if (cancelled) return;
        setTreeLoad({ status: "ready", nodes: resp.nodes });
        // Deep-link target wins on its first appearance, then we
        // never reapply it (operator stays in control afterwards).
        if (
          !deepLinkConsumedRef.current &&
          initialPath &&
          resp.nodes.some((n) => n.path === initialPath)
        ) {
          deepLinkConsumedRef.current = true;
          setSelectedPath(initialPath);
        } else if (resp.nodes.length > 0) {
          setSelectedPath(resp.nodes[0].path);
        }
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
  }, [apiClient, projectId, selectedRunId, initialPath]);

  // Soft tree refresh after a doc mutation : re-fetch the active
  // run's nodes WITHOUT resetting `selectedPath` (keep the viewer on
  // the file the operator is reading). Skips the initial render
  // (docsNonce === 0) — the selectedRunId effect above already
  // populated the tree.
  useEffect(() => {
    if (docsNonce === 0 || !selectedRunId) return;
    let cancelled = false;
    apiClient
      .getArtifactTree(projectId, selectedRunId)
      .then((resp) => {
        if (cancelled) return;
        setTreeLoad({ status: "ready", nodes: resp.nodes });
        // Drop the viewer selection only if the file vanished.
        setSelectedPath((prev) => (prev && resp.nodes.some((n) => n.path === prev) ? prev : null));
      })
      .catch(() => {
        // Non-fatal — the stale tree stays ; next manual run switch
        // re-fetches cleanly.
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, selectedRunId, docsNonce]);

  // Blob on selectedPath / viewingRef change. When `viewingRef` is set
  // for the live-docs run, load the document AT that commit (R-200-147
  // history viewer) ; otherwise load the latest content from the
  // artifacts blob surface.
  useEffect(() => {
    if (!selectedRunId || !selectedPath) {
      setBlobLoad({ status: "idle" });
      return;
    }
    let cancelled = false;
    setBlobLoad({ status: "loading" });
    const loader =
      viewingRef && selectedRunId === "live-docs"
        ? apiClient.getDocumentTextAtRef(projectId, selectedPath, viewingRef)
        : apiClient.getArtifactBlobText(projectId, selectedRunId, selectedPath);
    loader
      .then(({ text, contentType }) => {
        if (cancelled) return;
        const isText =
          contentType.startsWith("text/") ||
          contentType === "application/json" ||
          contentType === "application/javascript" ||
          contentType === "application/x-python";
        setBlobLoad(
          isText ? { status: "ready", text, contentType } : { status: "binary", contentType },
        );
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
  }, [apiClient, projectId, selectedRunId, selectedPath, viewingRef]);

  // R-500-010 v2 — leave edit mode whenever the selected file or viewed
  // revision changes (a stale draft on the wrong target would silently
  // overwrite the next selection on Save). The dep array entries are
  // pure triggers — biome can't see that statically.
  // biome-ignore lint/correctness/useExhaustiveDependencies: triggers, not body refs
  useEffect(() => {
    setEditing(false);
    setDraft("");
  }, [selectedPath, viewingRef, selectedRunId]);

  // Tranche B §5.17 / §5.18 — operator-driven structural ops on the
  // active run. The handler dispatches per actionId AND per run type
  // (live-docs uses /documents/... ; any other run uses /source/...
  // scoped to that run_id). v1 dialogs use native prompt/confirm
  // (R-500-010 minimum bar) ; success refreshes the tree via
  // docsNonce. Errors surface in the `structuralOpError` toast.
  const handleStructuralAction = async (
    actionId: string,
    target: FileTreeContextMenuTarget,
  ): Promise<void> => {
    setContextTarget(null);
    setStructuralOpError(null);
    if (!selectedRunId) return;
    const isLiveDocs = selectedRunId === "live-docs";
    try {
      if (actionId === "mkdir") {
        const childName = window.prompt(`New folder under "${target.path}"\n\nName (no slashes):`);
        if (!childName) return;
        if (childName.includes("/") || childName.includes("\\")) {
          setStructuralOpError("Folder name SHALL NOT contain slashes.");
          return;
        }
        const newPath = `${target.path}/${childName}`;
        if (isLiveDocs) {
          await apiClient.mkdirDocument(projectId, newPath);
        } else {
          await apiClient.mkdirSource(projectId, selectedRunId, newPath);
        }
      } else if (actionId === "newFile") {
        // R-500-010 v2 — blank-file creation, live-docs only. Source
        // runs have no equivalent endpoint in v1 (Q-200-019).
        if (!isLiveDocs) return;
        const childName = window.prompt(`New file under "${target.path}"\n\nName (no slashes):`);
        if (!childName) return;
        if (childName.includes("/") || childName.includes("\\")) {
          setStructuralOpError("File name SHALL NOT contain slashes.");
          return;
        }
        const newPath = `${target.path}/${childName}`;
        await apiClient.createDocument(projectId, newPath, "");
        setSelectedPath(newPath);
      } else if (actionId === "rename") {
        const next = window.prompt(
          `Rename ${target.kind}\n\nFrom: ${target.path}\nTo (full new path):`,
          target.path,
        );
        if (!next || next === target.path) return;
        if (isLiveDocs) {
          await apiClient.renameDocument(projectId, target.path, next);
        } else {
          await apiClient.renameSource(projectId, selectedRunId, target.path, next);
        }
        if (selectedPath === target.path) setSelectedPath(next);
      } else if (actionId === "delete") {
        // R-200-175 — source DELETE now supported (P2.2.a). Live-docs
        // path keeps its existing endpoint ; source path routes to
        // `deleteSourceFile` with the active run_id.
        const ok = window.confirm(`Delete "${target.path}" permanently ?`);
        if (!ok) return;
        if (isLiveDocs) {
          await apiClient.deleteDocument(projectId, target.path);
        } else {
          await apiClient.deleteSourceFile(projectId, selectedRunId, target.path);
        }
        if (selectedPath === target.path) setSelectedPath(null);
      } else if (actionId === "metadata") {
        // Source-only (R-200-173). Live-docs metadata is not exposed
        // by spec ; the action set hides this item there.
        if (isLiveDocs) return;
        const meta = await apiClient.getSourceFileMeta(projectId, selectedRunId, target.path);
        setMetaPanel(meta);
        return; // do NOT bump docsNonce — read-only.
      } else if (actionId === "history") {
        // #3 (R-200-147) — live-docs only : open the revision list for
        // this file. The panel fetches the per-file commit history and
        // lets the operator load a past revision into the viewer.
        if (!isLiveDocs) return;
        setHistoryTarget(target.path);
        return; // read-only — no tree refresh.
      } else if (actionId === "add-as-ref") {
        // R-500-012 — attach the whole file as a reference to the
        // next chat turn. Live-docs only in v1 (Q-200-019 defers
        // source references). Caps to 10 refs per R-200-180.
        if (!isLiveDocs) return;
        setReferences((prev) => {
          if (prev.length >= 10) return prev;
          const already = prev.some(
            (r) => r.source === "live-docs" && r.path === target.path && r.kind === "file",
          );
          if (already) return prev;
          return [...prev, { kind: "file", source: "live-docs", path: target.path }];
        });
        return; // no tree refresh — UI-only state.
      } else {
        return;
      }
      setDocsNonce((n) => n + 1);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${actionId} failed (${err.status}): ${_apiDetail(err.body)}`
          : String(err);
      setStructuralOpError(msg);
    }
  };

  // R-500-010 v2 — root-level "+ New file" / "+ New folder" affordances.
  // Used by both the live-docs toolbar above the tree and the empty-state
  // panel when the tree has zero nodes. Live-docs only.
  const createBlankFileAtRoot = async (): Promise<void> => {
    if (selectedRunId !== "live-docs") return;
    setStructuralOpError(null);
    const name = window.prompt("New file at root\n\nName (no slashes):");
    if (!name) return;
    if (name.includes("/") || name.includes("\\")) {
      setStructuralOpError("File name SHALL NOT contain slashes.");
      return;
    }
    try {
      await apiClient.createDocument(projectId, name, "");
      setSelectedPath(name);
      setDocsNonce((n) => n + 1);
    } catch (err) {
      setStructuralOpError(
        err instanceof ApiError
          ? `createDocument failed (${err.status}): ${_apiDetail(err.body)}`
          : String(err),
      );
    }
  };

  const createFolderAtRoot = async (): Promise<void> => {
    if (selectedRunId !== "live-docs") return;
    setStructuralOpError(null);
    const name = window.prompt("New folder at root\n\nName (no slashes):");
    if (!name) return;
    if (name.includes("/") || name.includes("\\")) {
      setStructuralOpError("Folder name SHALL NOT contain slashes.");
      return;
    }
    try {
      await apiClient.mkdirDocument(projectId, name);
      setDocsNonce((n) => n + 1);
    } catch (err) {
      setStructuralOpError(
        err instanceof ApiError
          ? `mkdir failed (${err.status}): ${_apiDetail(err.body)}`
          : String(err),
      );
    }
  };

  // R-500-010 v2 — inline content save for the live-docs viewer.
  const saveLiveDoc = async (): Promise<void> => {
    if (selectedRunId !== "live-docs" || !selectedPath) return;
    setSaving(true);
    setStructuralOpError(null);
    try {
      await apiClient.updateDocument(projectId, selectedPath, draft);
      const ct = blobLoad.status === "ready" ? blobLoad.contentType : "text/plain";
      setBlobLoad({ status: "ready", text: draft, contentType: ct });
      setEditing(false);
      setDocsNonce((n) => n + 1);
    } catch (err) {
      setStructuralOpError(
        err instanceof ApiError
          ? `Save failed (${err.status}): ${_apiDetail(err.body)}`
          : String(err),
      );
    } finally {
      setSaving(false);
    }
  };

  // Drag-and-drop move (replaces the former "Move to…" context action).
  // `destDir` is the target directory ("" = root). The tree component
  // already rejects no-op and into-self moves before calling this.
  const handleMove = async (sourcePath: string, destDir: string): Promise<void> => {
    setStructuralOpError(null);
    if (!selectedRunId) return;
    const isLiveDocs = selectedRunId === "live-docs";
    try {
      if (isLiveDocs) {
        await apiClient.moveDocument(projectId, sourcePath, destDir);
      } else {
        await apiClient.moveSource(projectId, selectedRunId, sourcePath, destDir);
      }
      if (selectedPath === sourcePath) {
        const basename = sourcePath.split("/").pop() ?? sourcePath;
        setSelectedPath(destDir ? `${destDir}/${basename}` : basename);
      }
      setDocsNonce((n) => n + 1);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `move failed (${err.status}): ${_apiDetail(err.body)}`
          : String(err);
      setStructuralOpError(msg);
    }
  };

  // Text selection capture (scoped to the viewer pane via data attr).
  useEffect(() => {
    function handler() {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed) {
        setPendingQuote(null);
        return;
      }
      const text = sel.toString();
      if (!text.trim()) {
        setPendingQuote(null);
        return;
      }
      const viewerNode = document.querySelector("[data-working-viewer='true']");
      if (!viewerNode) {
        setPendingQuote(null);
        return;
      }
      const anchorNode = sel.anchorNode;
      if (anchorNode && viewerNode.contains(anchorNode)) {
        setPendingQuote(text);
      } else {
        setPendingQuote(null);
      }
    }
    document.addEventListener("selectionchange", handler);
    return () => document.removeEventListener("selectionchange", handler);
  }, []);

  return (
    <main
      className="flex h-[calc(100dvh-8rem)] min-h-0 w-full flex-col px-6 py-6"
      data-testid="working-area"
    >
      <header className="mb-3">
        <h2 className="text-2xl font-semibold tracking-tight">Working area</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Chat with the assistant to draft and refine documents. Select text in the viewer to quote
          it into the conversation.
        </p>
      </header>

      {/* `flex-1 min-h-0` makes the 3-pane row fill exactly the space
          left under the header (no guessed header-height subtraction),
          so the chat column's pinned composer + Send button are always
          within the viewport — no page scroll. #6 : full-width +
          mouse-resizable panes (left tree / right chat have persisted
          pixel widths ; the middle viewer flexes to fill the rest). */}
      <div className="flex min-h-0 flex-1 gap-0">
        {/* Left : run picker + tree */}
        <aside
          style={{ width: leftW }}
          className="flex min-h-0 shrink-0 flex-col gap-3 overflow-hidden"
        >
          <div className="shrink-0">
            <RunsList load={runsLoad} selectedRunId={selectedRunId} onSelect={setSelectedRunId} />
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            {selectedRunId ? (
              <div className="flex h-full flex-col gap-2">
                {/* R-500-010 v2 — live-docs toolbar : root-level
                    affordances mirroring the Documents tab. Non-live-docs
                    runs (source corpus) stay read-only at v1. */}
                {selectedRunId === "live-docs" && (
                  <div
                    className="flex items-center gap-2 rounded-md border border-neutral-200 bg-white px-2 py-1"
                    data-testid="working-live-docs-toolbar"
                  >
                    <button
                      type="button"
                      onClick={() => void createBlankFileAtRoot()}
                      className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700"
                      data-testid="working-new-file"
                    >
                      + New file
                    </button>
                    <button
                      type="button"
                      onClick={() => void createFolderAtRoot()}
                      className="rounded bg-neutral-200 px-2 py-1 text-xs text-neutral-800 hover:bg-neutral-300 dark:bg-neutral-700 dark:text-neutral-100"
                      data-testid="working-new-folder"
                    >
                      + New folder
                    </button>
                  </div>
                )}
                {/* Live-docs empty-state : when the tree is ready and
                    empty, surface the root affordance so the operator
                    can bootstrap the project from a clean slate. */}
                {selectedRunId === "live-docs" &&
                treeLoad.status === "ready" &&
                (treeLoad.nodes?.length ?? 0) === 0 ? (
                  <div
                    className="flex flex-1 flex-col items-center justify-center gap-3 rounded-md border border-neutral-200 bg-white p-6 text-center text-sm text-neutral-500"
                    data-testid="working-live-docs-empty"
                  >
                    <p className="font-medium">No documents yet</p>
                    <p className="text-xs">
                      Create the first file or folder — they live at the project root.
                    </p>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => void createBlankFileAtRoot()}
                        className="rounded bg-blue-600 px-3 py-1 text-xs text-white hover:bg-blue-700"
                      >
                        New file
                      </button>
                      <button
                        type="button"
                        onClick={() => void createFolderAtRoot()}
                        className="rounded bg-neutral-200 px-3 py-1 text-xs text-neutral-800 dark:bg-neutral-700 dark:text-neutral-100"
                      >
                        New folder
                      </button>
                    </div>
                  </div>
                ) : (
                  <TreePanel
                    load={treeLoad}
                    selectedPath={selectedPath}
                    onSelect={(p) => {
                      setSelectedPath(p);
                      setViewingRef(null); // tree click always shows latest
                    }}
                    onContextMenu={setContextTarget}
                    onMove={(src, dest) => void handleMove(src, dest)}
                  />
                )}
              </div>
            ) : (
              <p className="rounded-md border border-neutral-200 bg-white px-3 py-3 text-sm text-neutral-500">
                Pick a run to see its files.
              </p>
            )}
            {structuralOpError && (
              <div className="mt-2 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300">
                {structuralOpError}{" "}
                <button
                  type="button"
                  onClick={() => setStructuralOpError(null)}
                  className="ml-1 underline"
                >
                  dismiss
                </button>
              </div>
            )}
          </div>
        </aside>

        <PaneResizer
          side="left"
          value={leftW}
          min={180}
          max={520}
          onDrag={(dx) => setLeftW((w) => _clampLeft(w + dx))}
          onCommit={persistPaneWidths}
        />

        {/* Middle : viewer + floating Quote-in-chat button */}
        <section
          data-working-viewer="true"
          className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white"
          data-testid="working-viewer-pane"
        >
          {selectedPath ? (
            <div className="flex items-center justify-between gap-3 border-b border-neutral-200 px-4 py-2">
              <div className="flex min-w-0 items-center gap-2">
                <p className="truncate font-mono text-sm text-neutral-900">{selectedPath}</p>
                {viewingRef && (
                  <span
                    className="inline-flex shrink-0 items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                    data-testid="working-viewing-revision"
                  >
                    revision {viewingRef.slice(0, 8)}
                    <button
                      type="button"
                      onClick={() => setViewingRef(null)}
                      className="underline hover:no-underline"
                      data-testid="working-back-to-latest"
                    >
                      back to latest
                    </button>
                  </span>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {/* R-500-010 v2 — inline Edit/Save for live-docs latest
                    revision. Hidden when viewing a past commit (history
                    viewer) — editing those would silently fork the
                    history ; the operator must "back to latest" first. */}
                {selectedRunId === "live-docs" && !viewingRef && blobLoad.status === "ready" ? (
                  editing ? (
                    <>
                      <button
                        type="button"
                        onClick={() => void saveLiveDoc()}
                        disabled={saving}
                        className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
                        data-testid="working-save"
                      >
                        {saving ? "Saving…" : "Save"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setEditing(false);
                          setDraft("");
                        }}
                        disabled={saving}
                        className="rounded bg-neutral-200 px-2 py-1 text-xs text-neutral-800 dark:bg-neutral-700 dark:text-neutral-100"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setDraft(blobLoad.text ?? "");
                        setEditing(true);
                      }}
                      className="rounded bg-neutral-200 px-2 py-1 text-xs text-neutral-800 dark:bg-neutral-700 dark:text-neutral-100"
                      data-testid="working-edit"
                    >
                      Edit
                    </button>
                  )
                ) : null}
                {blobLoad.status === "ready" || blobLoad.status === "binary" ? (
                  <span className="text-xs text-neutral-500">{blobLoad.contentType}</span>
                ) : null}
              </div>
            </div>
          ) : null}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {editing && selectedRunId === "live-docs" ? (
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                className="h-full min-h-[320px] w-full resize-none border-0 bg-white p-4 font-mono text-xs text-neutral-900 focus:outline-none dark:bg-neutral-900 dark:text-neutral-100"
                data-testid="working-editor"
              />
            ) : (
              <Viewer blobLoad={blobLoad} selectedPath={selectedPath} />
            )}
          </div>
          {pendingQuote && selectedPath && (
            <div className="absolute bottom-3 left-1/2 flex -translate-x-1/2 gap-2">
              <button
                type="button"
                onClick={() => {
                  setQuoted({ path: selectedPath, text: pendingQuote });
                  window.getSelection()?.removeAllRanges();
                  setPendingQuote(null);
                }}
                className="rounded-full bg-blue-600 px-4 py-2 text-xs font-semibold text-white shadow-lg hover:bg-blue-700"
                data-testid="working-quote-button"
              >
                Quote in chat ({pendingQuote.length} chars)
              </button>
              {selectedRunId === "live-docs" && (
                <button
                  type="button"
                  onClick={() => {
                    // R-500-012 — compute line range from the current
                    // selection, attach as an `excerpt`-kind ref.
                    const range = _selectionLineRange();
                    if (range && selectedPath) {
                      setReferences((prev) => {
                        if (prev.length >= 10) return prev;
                        return [
                          ...prev,
                          {
                            kind: "excerpt",
                            source: "live-docs",
                            path: selectedPath,
                            range,
                          },
                        ];
                      });
                      window.getSelection()?.removeAllRanges();
                      setPendingQuote(null);
                    }
                  }}
                  className="rounded-full border border-blue-300 bg-white px-4 py-2 text-xs font-semibold text-blue-700 shadow-lg hover:bg-blue-50 dark:border-blue-700 dark:bg-zinc-900 dark:text-blue-300 dark:hover:bg-blue-950"
                  data-testid="working-add-reference-button"
                  title="Attach this excerpt as a prompt reference"
                >
                  Add as reference
                </button>
              )}
            </div>
          )}
        </section>

        <PaneResizer
          side="right"
          value={rightW}
          min={260}
          max={600}
          onDrag={(dx) => setRightW((w) => _clampRight(w - dx))}
          onCommit={persistPaneWidths}
        />

        {/* Right : chat sidebar */}
        <aside
          style={{ width: rightW }}
          className="min-h-0 shrink-0 overflow-hidden rounded-lg border border-neutral-200 bg-white"
        >
          <ChatSidebar
            cfg={cfg}
            projectId={projectId}
            quoted={quoted}
            onClearQuote={() => setQuoted(null)}
            initialConversationId={initialConversationId}
            onDocsMutated={() => setDocsNonce((n) => n + 1)}
            references={references}
            onClearReferences={() => setReferences([])}
            onRemoveReference={(idx) => setReferences((prev) => prev.filter((_, i) => i !== idx))}
          />
        </aside>
      </div>
      {contextTarget && (
        <FileTreeContextMenu
          target={contextTarget}
          actions={selectedRunId === "live-docs" ? _LIVE_DOCS_ACTIONS : _SOURCE_ACTIONS}
          onPick={(actionId, tgt) => void handleStructuralAction(actionId, tgt)}
          onClose={() => setContextTarget(null)}
        />
      )}
      {metaPanel && <SourceFileMetaPanel meta={metaPanel} onClose={() => setMetaPanel(null)} />}
      {historyTarget && (
        <HistoryPanel
          apiClient={apiClient}
          projectId={projectId}
          path={historyTarget}
          onPick={(sha) => {
            setSelectedPath(historyTarget);
            setViewingRef(sha);
            setHistoryTarget(null);
          }}
          onClose={() => setHistoryTarget(null)}
        />
      )}
    </main>
  );
}

/** Floating panel listing one live-docs file's revision history
 *  (R-200-147). Fetches the per-file commit list on mount ; clicking a
 *  revision loads that content into the viewer via `onPick(sha)`. ESC
 *  dismisses. Read-only — never mutates the corpus. */
function HistoryPanel({
  apiClient,
  projectId,
  path,
  onPick,
  onClose,
}: {
  apiClient: ApiClient;
  projectId: string;
  path: string;
  onPick: (sha: string) => void;
  onClose: () => void;
}): React.JSX.Element {
  const [load, setLoad] = useState<{
    status: "loading" | "ready" | "error";
    commits?: ArtifactCommit[];
    error?: string;
  }>({ status: "loading" });

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setLoad({ status: "loading" });
    apiClient
      .listProjectCommits(projectId, 1, path)
      .then((resp) => {
        if (cancelled) return;
        setLoad({ status: "ready", commits: resp.commits });
      })
      .catch((err) => {
        if (cancelled) return;
        setLoad({
          status: "error",
          error: err instanceof ApiError ? `Failed to load history (${err.status})` : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient, projectId, path]);

  return (
    <div
      role="dialog"
      aria-label={`History for ${path}`}
      className="fixed right-4 top-20 z-40 max-h-[70vh] w-96 overflow-y-auto rounded-lg border border-neutral-200 bg-white p-4 shadow-xl dark:border-neutral-700 dark:bg-neutral-900"
      data-testid="working-history-panel"
    >
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="truncate font-mono text-sm text-neutral-900 dark:text-neutral-100">
          History · {path}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200"
          aria-label="Close history panel"
        >
          ✕
        </button>
      </div>
      {load.status === "loading" ? (
        <p className="px-1 py-2 text-sm text-neutral-500">Loading…</p>
      ) : load.status === "error" ? (
        <p className="px-1 py-2 text-sm text-red-700" role="alert">
          {load.error}
        </p>
      ) : !load.commits || load.commits.length === 0 ? (
        <p className="px-1 py-2 text-sm text-neutral-500">No history yet.</p>
      ) : (
        <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
          {load.commits.map((commit, idx) => (
            <li key={commit.sha}>
              <button
                type="button"
                onClick={() => onPick(commit.sha)}
                className="flex w-full flex-col gap-0.5 px-1 py-2 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800"
                data-testid={`working-history-commit-${commit.sha}`}
              >
                <span className="flex items-center gap-2">
                  <span className="font-mono text-xs text-blue-700 dark:text-blue-300">
                    {commit.sha.slice(0, 8)}
                  </span>
                  {idx === 0 && (
                    <span className="rounded bg-green-100 px-1 text-[10px] text-green-800 dark:bg-green-950 dark:text-green-300">
                      latest
                    </span>
                  )}
                  <span className="text-[11px] text-neutral-400">
                    {new Date(commit.committed_at).toLocaleString()}
                  </span>
                </span>
                <span className="truncate text-xs text-neutral-700 dark:text-neutral-300">
                  {commit.message.split("\n")[0]}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Floating side panel rendering one source-file's metadata
 *  (R-200-173). ESC + click-outside dismiss. R-500-011 mandates
 *  non-blocking — the underlying tree stays interactive. */
function SourceFileMetaPanel({
  meta,
  onClose,
}: {
  meta: SourceFileMeta;
  onClose: () => void;
}): React.JSX.Element {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-label={`Metadata for ${meta.path}`}
      className="fixed right-4 top-20 z-40 w-80 rounded-lg border border-neutral-200 bg-white p-4 shadow-xl dark:border-neutral-700 dark:bg-neutral-900"
    >
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="truncate font-mono text-sm text-neutral-900 dark:text-neutral-100">
          {meta.path}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200"
          aria-label="Close metadata panel"
        >
          ✕
        </button>
      </div>
      <dl className="space-y-1 text-xs">
        <_MetaRow label="Size" value={`${meta.size.toLocaleString()} B`} />
        <_MetaRow label="MIME" value={meta.mime_type} />
        {meta.modified_at && <_MetaRow label="Modified" value={meta.modified_at} />}
        {meta.last_commit_sha && (
          <_MetaRow label="Commit" value={meta.last_commit_sha.slice(0, 8)} />
        )}
        {meta.last_commit_message && <_MetaRow label="Message" value={meta.last_commit_message} />}
        {meta.last_commit_author && <_MetaRow label="Author" value={meta.last_commit_author} />}
        {meta.kg_indexed !== null && meta.kg_indexed !== undefined && (
          <_MetaRow label="In KG" value={meta.kg_indexed ? "yes" : "no"} />
        )}
      </dl>
    </div>
  );
}

function _MetaRow({ label, value }: { label: string; value: string }): React.JSX.Element {
  return (
    <div className="grid grid-cols-[5rem_1fr] gap-2">
      <dt className="text-neutral-500">{label}</dt>
      <dd className="break-words text-neutral-800 dark:text-neutral-100">{value}</dd>
    </div>
  );
}

function _selectionLineRange(): { start_line: number; end_line: number } | null {
  // Walk the viewer pane's text content from the selection's anchor /
  // focus to compute 1-indexed line numbers. This is best-effort —
  // the viewer renders text in a `<pre>` block so newline counting is
  // straightforward ; richer renderers will need a paragraph→line
  // mapping (Q-500-006 polish).
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  const viewer = document.querySelector("[data-working-viewer='true']");
  if (!viewer) return null;
  const range = sel.getRangeAt(0);
  const fullRange = document.createRange();
  fullRange.selectNodeContents(viewer);
  fullRange.setEnd(range.startContainer, range.startOffset);
  const before = fullRange.toString();
  const selectionText = range.toString();
  const start = (before.match(/\n/g)?.length ?? 0) + 1;
  const end = start + (selectionText.match(/\n/g)?.length ?? 0);
  return { start_line: start, end_line: end };
}

function _apiDetail(body: string): string {
  // FastAPI 4xx body is `{"detail": "..."}` ; surface just the detail
  // text to keep the toast short. Falls back to the raw body for the
  // edge cases (non-JSON, plain text).
  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // fall through
  }
  return body || "(no body)";
}

// ---------------------------------------------------------------------------
// Internal helpers — kept inline to avoid an extra module while the
// shapes stabilise. If a third consumer (beyond artifacts page +
// working area) ever needs these, extract to `components/`.
// ---------------------------------------------------------------------------

/** #6 — a vertical drag handle between two panes (the WAI-ARIA "window
 *  splitter" pattern : role=separator, focusable, with aria-value* and
 *  keyboard arrows). Reports the incremental pointer delta (`dx`) on
 *  move + ±16px on arrow keys, and fires `onCommit` on release so the
 *  parent can persist the new widths. Pointer capture keeps the drag
 *  alive even if the cursor leaves the thin handle. */
function PaneResizer({
  side,
  value,
  min,
  max,
  onDrag,
  onCommit,
}: {
  side: "left" | "right";
  value: number;
  min: number;
  max: number;
  onDrag: (dx: number) => void;
  onCommit: () => void;
}): React.JSX.Element {
  const dragging = useRef(false);
  const lastX = useRef(0);
  return (
    // biome-ignore lint/a11y/useSemanticElements: interactive WAI-ARIA window-splitter needs role="separator" on a focusable div; <hr> is a thematic break and cannot host the drag/keyboard handlers.
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize ${side} pane`}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      className="group mx-1 flex w-1.5 shrink-0 cursor-col-resize items-center justify-center focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
      data-testid={`working-resizer-${side}`}
      onPointerDown={(e) => {
        dragging.current = true;
        lastX.current = e.clientX;
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!dragging.current) return;
        const dx = e.clientX - lastX.current;
        lastX.current = e.clientX;
        if (dx !== 0) onDrag(dx);
      }}
      onPointerUp={(e) => {
        if (!dragging.current) return;
        dragging.current = false;
        e.currentTarget.releasePointerCapture(e.pointerId);
        onCommit();
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          onDrag(-16);
          onCommit();
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          onDrag(16);
          onCommit();
        }
      }}
    >
      <span
        className="h-8 w-0.5 rounded bg-neutral-300 group-hover:bg-blue-400 dark:bg-neutral-700"
        aria-hidden="true"
      />
    </div>
  );
}

function RunsList({
  load,
  selectedRunId,
  onSelect,
}: {
  load: RunsLoad;
  selectedRunId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white">
      <header className="border-b border-neutral-200 px-3 py-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
        Runs
      </header>
      {load.status === "loading" ? (
        <p className="px-3 py-3 text-sm text-neutral-500">Loading…</p>
      ) : load.status === "error" ? (
        <p className="px-3 py-3 text-sm text-red-700" role="alert">
          {load.error}
        </p>
      ) : !load.runs || load.runs.length === 0 ? (
        <p className="px-3 py-3 text-sm text-neutral-500">No runs yet.</p>
      ) : (
        <ul className="max-h-32 overflow-y-auto divide-y divide-neutral-100">
          {load.runs.map((r) => {
            const active = r.run_id === selectedRunId;
            return (
              <li key={r.run_id}>
                <button
                  type="button"
                  onClick={() => onSelect(r.run_id)}
                  className={[
                    "block w-full truncate px-3 py-1.5 text-left text-xs",
                    active ? "bg-blue-50 text-blue-900" : "text-neutral-700 hover:bg-neutral-50",
                  ].join(" ")}
                >
                  <span className="font-medium">{r.label ?? r.run_id.slice(0, 8)}</span>
                  <span className="ml-1 text-neutral-400">· {r.file_count} files</span>
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
  onContextMenu,
  onMove,
}: {
  load: TreeLoad;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  onContextMenu?: (target: FileTreeContextMenuTarget) => void;
  onMove?: (sourcePath: string, destDir: string) => void;
}) {
  return (
    <div className="h-full rounded-lg border border-neutral-200 bg-white">
      <header className="border-b border-neutral-200 px-3 py-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
        Files
      </header>
      {load.status === "loading" ? (
        <p className="px-3 py-3 text-sm text-neutral-500">Loading…</p>
      ) : load.status === "error" ? (
        <p className="px-3 py-3 text-sm text-red-700" role="alert">
          {load.error}
        </p>
      ) : (
        <FileTree
          nodes={load.nodes ?? []}
          selectedPath={selectedPath}
          onSelect={onSelect}
          onContextMenu={onContextMenu}
          onMove={onMove}
        />
      )}
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
        Binary file ({blobLoad.contentType}). Download to inspect.
      </p>
    );
  }
  if (blobLoad.status === "ready") {
    return <CodeViewer text={blobLoad.text ?? ""} path={selectedPath} />;
  }
  return null;
}
