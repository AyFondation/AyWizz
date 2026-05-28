// =============================================================================
// File: file-tree-version.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/file-tree-version.test.tsx
// Description: Per-file version badge tests for <FileTree> (file-tree.tsx
//              v4). A file node carrying `ArtifactNode.version` renders a
//              `(vN)` suffix after the name ; nodes without a version (or
//              with null) render the bare name. Folders never show a badge.
// =============================================================================

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FileTree } from "@/components/file-tree";
import type { ArtifactNode } from "@/lib/types";

function fileNode(path: string, version?: number | null): ArtifactNode {
  return { path, kind: "file", size_bytes: 1, mime_type: "text/plain", version };
}

describe("FileTree per-file version badge", () => {
  it("renders the (vN) suffix when a file carries a version", () => {
    render(
      <FileTree
        nodes={[fileNode("docs/overview.md", 3)]}
        selectedPath={null}
        onSelect={() => {}}
      />,
    );
    const badge = screen.getByTestId("file-tree-version-docs/overview.md");
    expect(badge).toHaveTextContent("(v3)");
  });

  it("renders no badge when the version is absent or null", () => {
    render(
      <FileTree
        nodes={[fileNode("a.md"), fileNode("b.md", null)]}
        selectedPath={null}
        onSelect={() => {}}
      />,
    );
    expect(screen.queryByTestId("file-tree-version-a.md")).toBeNull();
    expect(screen.queryByTestId("file-tree-version-b.md")).toBeNull();
  });

  it("shows version 1 (not hidden) — a v1 file still gets a badge", () => {
    render(<FileTree nodes={[fileNode("c.md", 1)]} selectedPath={null} onSelect={() => {}} />);
    expect(screen.getByTestId("file-tree-version-c.md")).toHaveTextContent("(v1)");
  });
});
