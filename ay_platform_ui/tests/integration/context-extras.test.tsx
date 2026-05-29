// =============================================================================
// File: context-extras.test.tsx
// Path: ay_platform_ui/tests/integration/context-extras.test.tsx
// Description: Small coverage top-ups — the sidebar-collapse context
//              (toggle + localStorage persist + hydration) and the
//              FileTreeContextMenu keyboard navigation (ArrowDown moves
//              focus between actions). Both are pure / hook-level.
// =============================================================================

import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FileTreeContextMenu } from "@/components/file-tree-context-menu";
import { SidebarProvider, useSidebar } from "@/components/sidebar-context";

describe("sidebar-context", () => {
  it("toggles collapsed and persists to localStorage", () => {
    const { result } = renderHook(() => useSidebar(), { wrapper: SidebarProvider });
    expect(result.current.collapsed).toBe(false);

    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(true);
    expect(window.localStorage.getItem("aywizz.sidebar.collapsed")).toBe("true");

    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(false);
    expect(window.localStorage.getItem("aywizz.sidebar.collapsed")).toBe("false");
  });

  it("hydrates collapsed=true from localStorage on mount", async () => {
    window.localStorage.setItem("aywizz.sidebar.collapsed", "true");
    const { result } = renderHook(() => useSidebar(), { wrapper: SidebarProvider });
    await waitFor(() => expect(result.current.collapsed).toBe(true));
  });

  it("returns the inert default outside a provider", () => {
    const { result } = renderHook(() => useSidebar());
    expect(result.current.collapsed).toBe(false);
    // default toggle is a no-op — must not throw
    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(false);
  });
});

describe("FileTreeContextMenu keyboard navigation", () => {
  it("moves focus to the next action on ArrowDown", () => {
    const actions = [
      { id: "rename", label: "Rename…", appliesTo: "any" as const },
      { id: "copy", label: "Copy", appliesTo: "any" as const },
      { id: "delete", label: "Delete", appliesTo: "any" as const, destructive: true },
    ];
    render(
      <FileTreeContextMenu
        target={{ path: "a.md", kind: "file", clientX: 0, clientY: 0 }}
        actions={actions}
        onPick={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const first = screen.getByText("Rename…");
    // first enabled action auto-focuses on mount
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(screen.getByText("Copy")).toHaveFocus();
    // wrap-around: ArrowUp from the first goes to the last
    fireEvent.keyDown(screen.getByText("Copy"), { key: "ArrowUp" });
    expect(first).toHaveFocus();
  });

  it("closes on an outside mousedown but stays open on an inside one", () => {
    const onClose = vi.fn();
    render(
      <FileTreeContextMenu
        target={{ path: "a.md", kind: "file", clientX: 0, clientY: 0 }}
        actions={[{ id: "rename", label: "Rename…", appliesTo: "any" }]}
        onPick={vi.fn()}
        onClose={onClose}
      />,
    );
    // mousedown INSIDE the menu must NOT close it (contains → return branch)
    fireEvent.mouseDown(screen.getByRole("menu"));
    expect(onClose).not.toHaveBeenCalled();
    // mousedown OUTSIDE closes it (the outside branch)
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
