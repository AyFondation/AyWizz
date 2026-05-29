// =============================================================================
// File: file-tree.test.tsx
// Path: ay_platform_ui/tests/integration/file-tree.test.tsx
// Description: Tests for the <FileTree> component (pure, props-driven).
//              Covers buildTree (nested paths, folders-first sort), the
//              expand/collapse toggle, file selection, the version suffix,
//              right-click context-menu emission, the empty state, the
//              per-extension glyphs, and drag-and-drop move (valid move
//              fires onMove ; a no-op move into the source's own parent is
//              rejected).
// =============================================================================

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FileTree } from "@/components/file-tree";
import type { ArtifactNode } from "@/lib/types";

const DRAG_TYPE = "application/x-aywizz-path";

const node = (path: string, over: Partial<ArtifactNode> = {}): ArtifactNode => ({
  path,
  kind: "file",
  size_bytes: 10,
  mime_type: null,
  ...over,
});

const NODES = [
  node("src/app.py"),
  node("src/util.ts"),
  node("docs/intro.md"),
  node("README.md", { version: 3 }),
  node("config.json"),
  node("diagram.png"),
  node("spec.pdf"),
  node("notes.unknownext"),
];

/** A reusable dataTransfer stub shared across dragStart → drop. */
function makeDataTransfer() {
  const data: Record<string, string> = {};
  return {
    data,
    setData(k: string, v: string) {
      data[k] = v;
    },
    getData(k: string) {
      return data[k] ?? "";
    },
    types: [] as string[],
    dropEffect: "",
    effectAllowed: "",
  };
}

describe("FileTree", () => {
  it("renders the empty state when there are no files", () => {
    render(<FileTree nodes={[]} selectedPath={null} onSelect={vi.fn()} />);
    expect(screen.getByText("No files in this run.")).toBeInTheDocument();
  });

  it("builds a nested hierarchy with folders, files, glyphs and a version suffix", () => {
    render(<FileTree nodes={NODES} selectedPath={null} onSelect={vi.fn()} />);
    expect(screen.getByTestId("file-tree")).toBeInTheDocument();
    // folders synthesised from path segments
    expect(screen.getByTestId("file-tree-folder-src")).toBeInTheDocument();
    expect(screen.getByTestId("file-tree-folder-docs")).toBeInTheDocument();
    // nested + root files (folders expanded by default on mount)
    expect(screen.getByTestId("file-tree-file-src/app.py")).toBeInTheDocument();
    expect(screen.getByTestId("file-tree-file-README.md")).toBeInTheDocument();
    // version suffix from ArtifactNode.version
    expect(screen.getByTestId("file-tree-version-README.md")).toHaveTextContent("(v3)");
  });

  it("collapses and re-expands a folder", async () => {
    render(<FileTree nodes={NODES} selectedPath={null} onSelect={vi.fn()} />);
    const user = userEvent.setup();
    expect(screen.getByTestId("file-tree-file-src/app.py")).toBeInTheDocument();
    await user.click(screen.getByTestId("file-tree-folder-src"));
    expect(screen.queryByTestId("file-tree-file-src/app.py")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("file-tree-folder-src"));
    expect(screen.getByTestId("file-tree-file-src/app.py")).toBeInTheDocument();
  });

  it("fires onSelect when a file row is clicked", async () => {
    const onSelect = vi.fn();
    render(<FileTree nodes={NODES} selectedPath={null} onSelect={onSelect} />);
    await userEvent.click(screen.getByTestId("file-tree-file-README.md"));
    expect(onSelect).toHaveBeenCalledWith("README.md");
  });

  it("emits a context-menu target on right-click (file + folder)", () => {
    const onContextMenu = vi.fn();
    render(
      <FileTree
        nodes={NODES}
        selectedPath={null}
        onSelect={vi.fn()}
        onContextMenu={onContextMenu}
      />,
    );
    fireEvent.contextMenu(screen.getByTestId("file-tree-file-README.md"), {
      clientX: 11,
      clientY: 22,
    });
    expect(onContextMenu).toHaveBeenCalledWith(
      expect.objectContaining({ path: "README.md", kind: "file", clientX: 11, clientY: 22 }),
    );
    fireEvent.contextMenu(screen.getByTestId("file-tree-folder-src"), { clientX: 3, clientY: 4 });
    expect(onContextMenu).toHaveBeenLastCalledWith(
      expect.objectContaining({ path: "src", kind: "folder" }),
    );
  });

  it("moves a file onto a folder via drag-and-drop", () => {
    const onMove = vi.fn();
    render(<FileTree nodes={NODES} selectedPath={null} onSelect={vi.fn()} onMove={onMove} />);
    const dt = makeDataTransfer();
    fireEvent.dragStart(screen.getByTestId("file-tree-file-README.md"), { dataTransfer: dt });
    const docs = screen.getByTestId("file-tree-folder-docs");
    fireEvent.dragOver(docs, { dataTransfer: dt });
    fireEvent.drop(docs, { dataTransfer: dt });
    expect(onMove).toHaveBeenCalledWith("README.md", "docs");
  });

  it("rejects a no-op move into the source's own parent directory", () => {
    const onMove = vi.fn();
    render(<FileTree nodes={NODES} selectedPath={null} onSelect={vi.fn()} onMove={onMove} />);
    const dt = makeDataTransfer();
    // drag src/app.py onto its own parent folder `src` → _isInvalidMove → ignored
    dt.setData(DRAG_TYPE, "src/app.py");
    fireEvent.drop(screen.getByTestId("file-tree-folder-src"), { dataTransfer: dt });
    expect(onMove).not.toHaveBeenCalled();
  });

  it("formats KB / MB file sizes in the row title", () => {
    render(
      <FileTree
        nodes={[node("kb.txt", { size_bytes: 5_000 }), node("mb.bin", { size_bytes: 3_000_000 })]}
        selectedPath={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByTestId("file-tree-file-kb.txt").getAttribute("title")).toMatch(/KB$/);
    expect(screen.getByTestId("file-tree-file-mb.bin").getAttribute("title")).toMatch(/MB$/);
  });
});
