// =============================================================================
// File: file-tree.tsx
// Version: 4
// Path: ay_platform_ui/components/file-tree.tsx
// Description: VSCode-like file tree component used by the Code source /
//              Documents section. Takes the flat `ArtifactNode[]` list
//              returned by `/api/v1/projects/{pid}/artifacts/runs/{rid}/tree`
//              and renders it as a hierarchy with collapsible folders.
//
//              Layout rules :
//                - One row per node ; folders show a caret (▸/▾) ;
//                  files show a small file glyph and the basename.
//                - Indentation = 16 px per depth level.
//                - Click on a folder row toggles open/closed (independent
//                  state per node). Click on a file row fires `onSelect`.
//                - Selected file row gets a blue tint ; folders never
//                  hold "selection" state — only files do.
//                - First mount expands every folder so small trees are
//                  visible without clicking. Operators can collapse the
//                  ones they don't need.
//
//              Profile-agnostic : `code` shows `src/`, `tests/`, ... ;
//              `docgen` shows `docs/`, `reports/`, ... — the component
//              doesn't care.
//
//              v2 (2026-05-20) : optional `onContextMenu` callback
//              (R-500-010 / R-500-014). Right-click or Shift+F10 on a
//              focused row fires the callback with the path, the kind
//              (file/folder), and the originating event so the parent
//              can position a context menu at the cursor.
//
//              v3 (2026-05-21) : optional `onMove` callback enabling
//              drag-and-drop relocation. Rows become `draggable` ; folder
//              rows (and an implicit root drop zone) accept a drop and
//              fire `onMove(sourcePath, destDir)`. Replaces the context
//              menu "Move to…" prompt (removed by the working-area page).
//              Invalid drops (onto self, into own subtree, or into the
//              current parent = no-op) are rejected before firing.
//
//              v4 (2026-05-21) : file rows render the per-file version
//              suffix `name (vN)` when the `ArtifactNode.version` field
//              is present (live-docs, batched per AI response). Folders
//              and version-less nodes render the bare name.
// =============================================================================

"use client";

import { useMemo, useState } from "react";

import type { ArtifactNode } from "@/lib/types";

// MIME-ish key for the dragged node path. A custom type (vs plain
// `text/plain`) keeps unrelated text drags from being interpreted as a
// move, and lets `onDragOver` decide droppability from the types list.
const _DRAG_TYPE = "application/x-aywizz-path";

/** Reject moves that would be no-ops or cycles:
 *  - source === dest folder (already its own parent's child target)
 *  - dest is the source's current parent directory (no-op)
 *  - dest is the source folder itself or a descendant of it (cycle)
 *  Returns the parent dir of `source` for the no-op check. */
function _isInvalidMove(sourcePath: string, destDir: string): boolean {
  if (sourcePath === destDir) return true;
  const parent = sourcePath.includes("/") ? sourcePath.split("/").slice(0, -1).join("/") : "";
  if (parent === destDir) return true; // already in that directory
  // Dropping a folder into itself or its own subtree would orphan it.
  if (destDir === sourcePath || destDir.startsWith(`${sourcePath}/`)) return true;
  return false;
}

interface TreeNode {
  name: string;
  path: string; // full path from root (file only ; for folders we compose synthetic ids)
  type: "file" | "folder";
  sizeBytes?: number;
  // Per-file revision count (live-docs). Files only ; rendered as the
  // `name (vN)` suffix when present.
  version?: number | null;
  children?: TreeNode[];
}

export interface FileTreeContextMenuTarget {
  path: string;
  kind: "file" | "folder";
  /** Screen coordinates where the menu should appear. */
  clientX: number;
  clientY: number;
}

interface FileTreeProps {
  nodes: ArtifactNode[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
  /** Optional right-click / Shift+F10 handler. When set, every row
   *  becomes interactive : right-click + the ContextMenu key both
   *  fire it. R-500-010 (live-docs) / R-500-011 (source-files) /
   *  R-500-014 (keyboard a11y). */
  onContextMenu?: (target: FileTreeContextMenuTarget) => void;
  /** Optional drag-and-drop move handler. When set, rows become
   *  `draggable` and folders (plus the implicit root) accept drops ;
   *  dropping a node onto a folder fires `onMove(sourcePath, destDir)`
   *  (destDir "" = repository root). Invalid drops are filtered out. */
  onMove?: (sourcePath: string, destDir: string) => void;
}

/** Build the hierarchical tree from the flat node list. Sort entries
 *  alphabetically within each level, folders first then files (the
 *  VSCode convention). */
function buildTree(flat: ArtifactNode[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", type: "folder", children: [] };
  for (const node of flat) {
    if (node.kind !== "file") continue;
    const parts = node.path.split("/").filter(Boolean);
    let cursor = root;
    parts.forEach((part, idx) => {
      const isLeaf = idx === parts.length - 1;
      cursor.children ??= [];
      let next = cursor.children.find((c) => c.name === part);
      if (!next) {
        next = {
          name: part,
          path: parts.slice(0, idx + 1).join("/"),
          type: isLeaf ? "file" : "folder",
          sizeBytes: isLeaf ? node.size_bytes : undefined,
          version: isLeaf ? node.version : undefined,
          children: isLeaf ? undefined : [],
        };
        cursor.children.push(next);
      }
      cursor = next;
    });
  }
  sortTree(root);
  return root.children ?? [];
}

function sortTree(node: TreeNode): void {
  if (!node.children) return;
  node.children.sort((a, b) => {
    if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  for (const child of node.children) sortTree(child);
}

/** All folder paths in the tree — used as the initial expanded-set so
 *  every level renders open on first mount. */
function collectFolderPaths(nodes: TreeNode[]): string[] {
  const out: string[] = [];
  const walk = (list: TreeNode[]) => {
    for (const n of list) {
      if (n.type === "folder") {
        out.push(n.path);
        if (n.children) walk(n.children);
      }
    }
  };
  walk(nodes);
  return out;
}

export function FileTree({ nodes, selectedPath, onSelect, onContextMenu, onMove }: FileTreeProps) {
  const tree = useMemo(() => buildTree(nodes), [nodes]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(collectFolderPaths(tree)));
  // Folder path ("" = root) currently hovered during a drag, for the
  // drop-target highlight. null = no active drag-over.
  const [dragOverPath, setDragOverPath] = useState<string | null>(null);

  function toggle(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  // Shared drop handler. `destDir` is the target directory ("" = root).
  // Reads the dragged source path from the dataTransfer, validates, then
  // fires onMove. Always clears the highlight.
  function handleDrop(destDir: string, e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragOverPath(null);
    if (!onMove) return;
    const source = e.dataTransfer.getData(_DRAG_TYPE);
    if (!source || _isInvalidMove(source, destDir)) return;
    onMove(source, destDir);
  }

  if (tree.length === 0) {
    return <p className="px-3 py-3 text-sm text-neutral-500">No files in this run.</p>;
  }

  // The root container doubles as the repository-root drop zone (a drop
  // in empty space moves the node to the root). Folder rows
  // `stopPropagation` on their own drop so the deepest target wins.
  const rootActive = !!onMove && dragOverPath === "";
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: the container doubles as the root drop target for drag-and-drop file moves — a mouse-only enhancement, not a keyboard control (the inner <ul>/rows carry the semantics).
    <div
      className={[
        "max-h-[60vh] overflow-y-auto py-1",
        rootActive ? "rounded ring-2 ring-inset ring-blue-400" : "",
      ].join(" ")}
      data-testid="file-tree"
      onDragOver={
        onMove
          ? (e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              setDragOverPath("");
            }
          : undefined
      }
      onDrop={onMove ? (e) => handleDrop("", e) : undefined}
    >
      <ul aria-label="Files in this run">
        {tree.map((node) => (
          <TreeRow
            key={node.path}
            node={node}
            depth={0}
            selectedPath={selectedPath}
            expanded={expanded}
            onToggle={toggle}
            onSelect={onSelect}
            onContextMenu={onContextMenu}
            onMove={onMove}
            dragOverPath={dragOverPath}
            setDragOverPath={setDragOverPath}
            onDropFolder={handleDrop}
          />
        ))}
      </ul>
    </div>
  );
}

interface TreeRowProps {
  node: TreeNode;
  depth: number;
  selectedPath: string | null;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
  onContextMenu?: (target: FileTreeContextMenuTarget) => void;
  onMove?: (sourcePath: string, destDir: string) => void;
  dragOverPath: string | null;
  setDragOverPath: (updater: (prev: string | null) => string | null) => void;
  onDropFolder: (destDir: string, e: React.DragEvent) => void;
}

/** Drag-source props shared by file + folder rows. Absent when `onMove`
 *  is not wired (DnD disabled). */
function _dragSourceProps(path: string, onMove?: (s: string, d: string) => void) {
  if (!onMove) return {} as const;
  return {
    draggable: true,
    onDragStart: (e: React.DragEvent) => {
      e.dataTransfer.setData(_DRAG_TYPE, path);
      e.dataTransfer.effectAllowed = "move";
    },
  };
}

/** Stable handler used by both file and folder rows : converts a
 *  native React event into a `FileTreeContextMenuTarget`. Keyboard
 *  (Shift+F10 / ContextMenu key) positions the menu at the row's
 *  bounding rect since there's no cursor. */
function _emitContextMenu(
  e: React.MouseEvent | React.KeyboardEvent,
  path: string,
  kind: "file" | "folder",
  cb: (target: FileTreeContextMenuTarget) => void,
) {
  e.preventDefault();
  e.stopPropagation();
  let x: number;
  let y: number;
  if ("clientX" in e) {
    x = e.clientX;
    y = e.clientY;
  } else {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    x = rect.left + 8;
    y = rect.bottom;
  }
  cb({ path, kind, clientX: x, clientY: y });
}

function _isContextMenuKey(e: React.KeyboardEvent): boolean {
  // Shift+F10 (Windows standard) ; ContextMenu key (most full keyboards).
  return (e.shiftKey && e.key === "F10") || e.key === "ContextMenu";
}

function TreeRow({
  node,
  depth,
  selectedPath,
  expanded,
  onToggle,
  onSelect,
  onContextMenu,
  onMove,
  dragOverPath,
  setDragOverPath,
  onDropFolder,
}: TreeRowProps) {
  const indentPx = depth * 16;
  const dragSource = _dragSourceProps(node.path, onMove);
  if (node.type === "folder") {
    const open = expanded.has(node.path);
    const dropActive = !!onMove && dragOverPath === node.path;
    return (
      <li>
        <button
          type="button"
          onClick={() => onToggle(node.path)}
          onContextMenu={
            onContextMenu
              ? (e) => _emitContextMenu(e, node.path, "folder", onContextMenu)
              : undefined
          }
          onKeyDown={
            onContextMenu
              ? (e) => {
                  if (_isContextMenuKey(e)) {
                    _emitContextMenu(e, node.path, "folder", onContextMenu);
                  }
                }
              : undefined
          }
          {...dragSource}
          onDragOver={
            onMove
              ? (e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  e.dataTransfer.dropEffect = "move";
                  setDragOverPath(() => node.path);
                }
              : undefined
          }
          onDragLeave={
            onMove
              ? (e) => {
                  e.stopPropagation();
                  setDragOverPath((prev) => (prev === node.path ? null : prev));
                }
              : undefined
          }
          onDrop={onMove ? (e) => onDropFolder(node.path, e) : undefined}
          style={{ paddingLeft: `${indentPx + 8}px` }}
          className={[
            "flex w-full items-center gap-1 py-1 pr-3 text-left text-xs text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800",
            dropActive ? "bg-blue-50 ring-1 ring-inset ring-blue-300 dark:bg-blue-950" : "",
          ].join(" ")}
          data-testid={`file-tree-folder-${node.path}`}
        >
          <span className="inline-block w-3 text-neutral-400">{open ? "▾" : "▸"}</span>
          <span aria-hidden="true">📁</span>
          <span className="font-medium">{node.name}</span>
        </button>
        {open && node.children && (
          <ul>
            {node.children.map((child) => (
              <TreeRow
                key={child.path}
                node={child}
                depth={depth + 1}
                selectedPath={selectedPath}
                expanded={expanded}
                onToggle={onToggle}
                onSelect={onSelect}
                onContextMenu={onContextMenu}
                onMove={onMove}
                dragOverPath={dragOverPath}
                setDragOverPath={setDragOverPath}
                onDropFolder={onDropFolder}
              />
            ))}
          </ul>
        )}
      </li>
    );
  }
  const active = node.path === selectedPath;
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(node.path)}
        onContextMenu={
          onContextMenu ? (e) => _emitContextMenu(e, node.path, "file", onContextMenu) : undefined
        }
        onKeyDown={
          onContextMenu
            ? (e) => {
                if (_isContextMenuKey(e)) {
                  _emitContextMenu(e, node.path, "file", onContextMenu);
                }
              }
            : undefined
        }
        {...dragSource}
        style={{ paddingLeft: `${indentPx + 8 + 12}px` /* align with folder text */ }}
        className={[
          "flex w-full items-center gap-1 py-1 pr-3 text-left font-mono text-xs transition-colors",
          active
            ? "bg-blue-100 text-blue-900 dark:bg-blue-900 dark:text-blue-100"
            : "text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800",
        ].join(" ")}
        data-testid={`file-tree-file-${node.path}`}
        data-active={active ? "true" : "false"}
        title={
          node.sizeBytes !== undefined ? `${node.path} · ${formatBytes(node.sizeBytes)}` : node.path
        }
      >
        <span aria-hidden="true">{_fileGlyph(node.name)}</span>
        <span className="truncate">{node.name}</span>
        {typeof node.version === "number" && (
          <span
            className="ml-1 shrink-0 text-[10px] font-normal text-neutral-400 dark:text-neutral-500"
            data-testid={`file-tree-version-${node.path}`}
          >
            (v{node.version})
          </span>
        )}
      </button>
    </li>
  );
}

function _fileGlyph(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith(".md") || lower.endsWith(".markdown")) return "📝";
  if (lower.endsWith(".py")) return "🐍";
  if (lower.endsWith(".ts") || lower.endsWith(".tsx")) return "🔷";
  if (lower.endsWith(".js") || lower.endsWith(".jsx")) return "🟨";
  if (
    lower.endsWith(".yml") ||
    lower.endsWith(".yaml") ||
    lower.endsWith(".toml") ||
    lower.endsWith(".json")
  )
    return "⚙️";
  if (lower.endsWith(".pdf")) return "📕";
  if (
    lower.endsWith(".png") ||
    lower.endsWith(".jpg") ||
    lower.endsWith(".jpeg") ||
    lower.endsWith(".svg")
  )
    return "🖼️";
  return "📄";
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
