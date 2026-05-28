// =============================================================================
// File: file-tree-context-menu.tsx
// Version: 1
// Path: ay_platform_ui/components/file-tree-context-menu.tsx
// Description: Floating context-menu paired with <FileTree>. The
//              caller (Working area DocsPane, future SourceFilesPane)
//              passes the menu target — `{ path, kind, clientX,
//              clientY }` — and a list of actions. The menu renders
//              at the cursor (or row bounding box for keyboard
//              activation), dismisses on outside-click / ESC, and
//              calls back the chosen action.
//
//              v1 dialogs : `window.prompt` / `window.confirm`. Spec
//              R-500-010 / R-500-011 only require the operations exist
//              + are keyboard-reachable (R-500-014) ; a modal/dialog
//              polish pass is a future iteration.
// =============================================================================

"use client";

import { useEffect, useRef } from "react";

import type { FileTreeContextMenuTarget } from "@/components/file-tree";

export interface ContextMenuAction {
  id: string;
  label: string;
  /** When set, the action is hidden when `target.kind` doesn't match. */
  appliesTo?: "file" | "folder" | "any";
  /** Marks destructive actions for an emphasized colour. */
  destructive?: boolean;
  /** Disable visually + ignore activations. */
  disabled?: boolean;
}

interface FileTreeContextMenuProps {
  target: FileTreeContextMenuTarget;
  actions: ContextMenuAction[];
  onPick: (actionId: string, target: FileTreeContextMenuTarget) => void;
  onClose: () => void;
}

export function FileTreeContextMenu({
  target,
  actions,
  onPick,
  onClose,
}: FileTreeContextMenuProps): React.JSX.Element {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current) return;
      if (e.target instanceof Node && ref.current.contains(e.target)) return;
      onClose();
    }
    function onDocKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onDocKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onDocKey);
    };
  }, [onClose]);

  // Focus first enabled action on mount for keyboard a11y.
  useEffect(() => {
    const first = ref.current?.querySelector<HTMLButtonElement>(
      "button[data-action-id]:not(:disabled)",
    );
    first?.focus();
  }, []);

  const visible = actions.filter(
    (a) => !a.appliesTo || a.appliesTo === "any" || a.appliesTo === target.kind,
  );

  return (
    <div
      ref={ref}
      role="menu"
      aria-label="File actions"
      style={{
        position: "fixed",
        top: target.clientY,
        left: target.clientX,
        zIndex: 9999,
      }}
      className="min-w-[180px] rounded-md border border-neutral-200 bg-white py-1 text-sm shadow-lg dark:border-neutral-700 dark:bg-neutral-900"
    >
      <div className="border-b border-neutral-100 px-3 py-1 text-[10px] font-medium uppercase tracking-wide text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
        {target.kind === "folder" ? "Folder" : "File"} · {target.path}
      </div>
      <ul className="py-1">
        {visible.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              data-action-id={a.id}
              disabled={a.disabled}
              onClick={() => {
                if (!a.disabled) onPick(a.id, target);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                  e.preventDefault();
                  const buttons = Array.from(
                    ref.current?.querySelectorAll<HTMLButtonElement>(
                      "button[data-action-id]:not(:disabled)",
                    ) ?? [],
                  );
                  const idx = buttons.indexOf(e.currentTarget);
                  const dir = e.key === "ArrowDown" ? 1 : -1;
                  const next = buttons[(idx + dir + buttons.length) % buttons.length];
                  next?.focus();
                }
              }}
              className={[
                "flex w-full items-center px-3 py-1.5 text-left transition-colors",
                a.disabled
                  ? "cursor-not-allowed text-neutral-400"
                  : a.destructive
                    ? "text-red-700 hover:bg-red-50 dark:text-red-300 dark:hover:bg-red-950"
                    : "text-neutral-800 hover:bg-neutral-100 dark:text-neutral-100 dark:hover:bg-neutral-800",
              ].join(" ")}
            >
              {a.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
