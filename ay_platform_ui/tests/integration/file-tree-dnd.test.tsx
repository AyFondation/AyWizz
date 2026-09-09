// =============================================================================
// File: file-tree-dnd.test.tsx
// Version: 2
// Path: ay_platform_ui/tests/integration/file-tree-dnd.test.tsx
// Description: Drag-and-drop relocation tests for <FileTree> (file-tree.tsx
//              v5). Validates that dropping a node onto a folder fires
//              `onMove(sourcePath, destDir)`, that the root drop targets
//              move to "", and that invalid moves (no-op into the
//              current parent, into the dragged folder's own subtree) are
//              filtered out before `onMove` runs.
//
//              v2 (2026-09-08) covers the explicit `/` root row: it is the
//              reachable root target once a directory occupies the tree
//              (the v3 container zone was only reachable through 4 px of
//              padding), it exists on an empty tree, and dropping on a
//              FILE row no longer relocates to the root by bubbling.
//
//              jsdom does not implement the HTML5 DnD data store, so a
//              minimal DataTransfer stub is shared across dragStart/drop
//              to carry the path the component reads on drop. This covers
//              the component's move logic ; full pointer-driven DnD is a
//              Playwright concern (system tier).
// =============================================================================

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FileTree } from "@/components/file-tree";
import type { ArtifactNode } from "@/lib/types";

function node(path: string): ArtifactNode {
  return { path, kind: "file", size_bytes: 1, mime_type: "text/plain" };
}

// Minimal DataTransfer stub : jsdom's drag events have no working data
// store, so we provide one and pass the SAME instance to dragStart and
// drop, mirroring the browser carrying the payload across the gesture.
function makeDataTransfer() {
  const store: Record<string, string> = {};
  return {
    setData: (type: string, value: string) => {
      store[type] = value;
    },
    getData: (type: string) => store[type] ?? "",
    effectAllowed: "",
    dropEffect: "",
  };
}

describe("FileTree drag-and-drop", () => {
  it("fires onMove(source, destDir) when a file is dropped on a folder", () => {
    const onMove = vi.fn();
    render(
      <FileTree
        nodes={[node("overview.md"), node("docs/intro.md")]}
        selectedPath={null}
        onSelect={() => {}}
        onMove={onMove}
      />,
    );
    const file = screen.getByTestId("file-tree-file-overview.md");
    const folder = screen.getByTestId("file-tree-folder-docs");
    const dt = makeDataTransfer();
    fireEvent.dragStart(file, { dataTransfer: dt });
    fireEvent.dragOver(folder, { dataTransfer: dt });
    fireEvent.drop(folder, { dataTransfer: dt });
    expect(onMove).toHaveBeenCalledTimes(1);
    expect(onMove).toHaveBeenCalledWith("overview.md", "docs");
  });

  it("moves to the repository root when dropped on the root container", () => {
    const onMove = vi.fn();
    render(
      <FileTree
        nodes={[node("docs/intro.md")]}
        selectedPath={null}
        onSelect={() => {}}
        onMove={onMove}
      />,
    );
    const file = screen.getByTestId("file-tree-file-docs/intro.md");
    const root = screen.getByTestId("file-tree");
    const dt = makeDataTransfer();
    fireEvent.dragStart(file, { dataTransfer: dt });
    fireEvent.drop(root, { dataTransfer: dt });
    expect(onMove).toHaveBeenCalledWith("docs/intro.md", "");
  });

  it("rejects a no-op drop into the file's current parent folder", () => {
    const onMove = vi.fn();
    render(
      <FileTree
        nodes={[node("docs/intro.md"), node("docs/notes/n.md")]}
        selectedPath={null}
        onSelect={() => {}}
        onMove={onMove}
      />,
    );
    const file = screen.getByTestId("file-tree-file-docs/intro.md");
    const sameParent = screen.getByTestId("file-tree-folder-docs");
    const dt = makeDataTransfer();
    fireEvent.dragStart(file, { dataTransfer: dt });
    fireEvent.drop(sameParent, { dataTransfer: dt });
    expect(onMove).not.toHaveBeenCalled();
  });

  it("rejects dropping a folder into its own subtree", () => {
    const onMove = vi.fn();
    render(
      <FileTree
        nodes={[node("docs/sub/leaf.md")]}
        selectedPath={null}
        onSelect={() => {}}
        onMove={onMove}
      />,
    );
    const folder = screen.getByTestId("file-tree-folder-docs");
    const ownChild = screen.getByTestId("file-tree-folder-docs/sub");
    const dt = makeDataTransfer();
    fireEvent.dragStart(folder, { dataTransfer: dt });
    fireEvent.drop(ownChild, { dataTransfer: dt });
    expect(onMove).not.toHaveBeenCalled();
  });

  it("moves to the repository root via the explicit root row when a directory exists", () => {
    const onMove = vi.fn();
    render(
      <FileTree
        nodes={[node("docs/intro.md"), node("docs/notes/n.md")]}
        selectedPath={null}
        onSelect={() => {}}
        onMove={onMove}
      />,
    );
    const file = screen.getByTestId("file-tree-file-docs/intro.md");
    const rootRow = screen.getByTestId("file-tree-root");
    const dt = makeDataTransfer();
    fireEvent.dragStart(file, { dataTransfer: dt });
    fireEvent.dragOver(rootRow, { dataTransfer: dt });
    fireEvent.drop(rootRow, { dataTransfer: dt });
    expect(onMove).toHaveBeenCalledTimes(1);
    expect(onMove).toHaveBeenCalledWith("docs/intro.md", "");
  });

  it("renders the root row even when the run has no files", () => {
    render(<FileTree nodes={[]} selectedPath={null} onSelect={() => {}} onMove={vi.fn()} />);
    expect(screen.getByTestId("file-tree-root")).toBeInTheDocument();
    expect(screen.getByText("No files in this run.")).toBeInTheDocument();
  });

  it("does not relocate to the root when a node is dropped on a file row", () => {
    const onMove = vi.fn();
    render(
      <FileTree
        nodes={[node("overview.md"), node("docs/intro.md")]}
        selectedPath={null}
        onSelect={() => {}}
        onMove={onMove}
      />,
    );
    const dragged = screen.getByTestId("file-tree-file-docs/intro.md");
    const otherFile = screen.getByTestId("file-tree-file-overview.md");
    const dt = makeDataTransfer();
    fireEvent.dragStart(dragged, { dataTransfer: dt });
    fireEvent.dragOver(otherFile, { dataTransfer: dt });
    fireEvent.drop(otherFile, { dataTransfer: dt });
    expect(onMove).not.toHaveBeenCalled();
  });

  it("does not make rows draggable when onMove is absent", () => {
    render(<FileTree nodes={[node("overview.md")]} selectedPath={null} onSelect={() => {}} />);
    const file = screen.getByTestId("file-tree-file-overview.md");
    expect(file).not.toHaveAttribute("draggable");
  });
});
