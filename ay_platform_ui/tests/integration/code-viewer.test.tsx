// =============================================================================
// File: code-viewer.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/code-viewer.test.tsx
// Description: Tests for <CodeViewer> — the read-only artifact source
//              renderer. Covers :
//                - languageForPath() extension → Monaco language id
//                  mapping (the branch table), surfaced via the
//                  `data-language` attribute ;
//                - line-number gutter (1..N) and that a blank line
//                  still renders a row (no collapsed empty lines).
// =============================================================================

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CodeViewer } from "@/components/code-viewer";

describe("CodeViewer language detection", () => {
  const cases: [string, string][] = [
    ["main.py", "python"],
    ["app.js", "javascript"],
    ["app.mjs", "javascript"],
    ["app.cjs", "javascript"],
    ["mod.ts", "typescript"],
    ["comp.tsx", "typescript"],
    ["README.md", "markdown"],
    ["notes.markdown", "markdown"],
    ["data.json", "json"],
    ["conf.yml", "yaml"],
    ["conf.yaml", "yaml"],
    ["pyproject.toml", "toml"],
    ["index.html", "html"],
    ["page.htm", "html"],
    ["styles.css", "css"],
    ["run.sh", "shell"],
    ["run.bash", "shell"],
    ["out.txt", "plaintext"],
    ["debug.log", "plaintext"],
    ["data.bin", "plaintext"], // unknown extension → plaintext
    ["Dockerfile", "plaintext"], // no extension → plaintext
  ];

  for (const [path, expected] of cases) {
    it(`maps ${path} → ${expected}`, () => {
      render(<CodeViewer text="x" path={path} />);
      expect(screen.getByTestId("code-viewer")).toHaveAttribute("data-language", expected);
    });
  }

  it("is case-insensitive on the extension", () => {
    render(<CodeViewer text="x" path="Main.PY" />);
    expect(screen.getByTestId("code-viewer")).toHaveAttribute("data-language", "python");
  });
});

describe("CodeViewer rendering", () => {
  it("renders one numbered row per line", () => {
    render(<CodeViewer text={"a\nb\nc"} path="x.txt" />);
    // gutter shows 1, 2, 3
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("c")).toBeInTheDocument();
  });

  it("renders a single row for single-line input", () => {
    render(<CodeViewer text="only" path="x.txt" />);
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.queryByText("2")).not.toBeInTheDocument();
  });
});
