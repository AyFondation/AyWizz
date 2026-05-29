// =============================================================================
// File: components.test.tsx
// Path: ay_platform_ui/tests/integration/components.test.tsx
// Description: Unit-style tests for the small presentational components that
//              the page tests only exercised shallowly. All are pure (props
//              in → DOM out, plus local interaction state) so no providers /
//              MSW are needed. Covers : InlineLog + ModifiedDocsLinks,
//              ReferenceTray, ComingSoonSection, FileTreeContextMenu,
//              RunTrace + SteerComposer.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ComingSoonSection } from "@/components/coming-soon-section";
import { FileTreeContextMenu } from "@/components/file-tree-context-menu";
import { InlineLog, ModifiedDocsLinks } from "@/components/inline-log";
import { ReferenceTray } from "@/components/reference-tray";
import { RunTrace, SteerComposer } from "@/components/run-trace";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

// biome-ignore lint/suspicious/noExplicitAny: minimal event fixtures for render-only tests
const ev = (o: Record<string, unknown>) => o as any;

describe("InlineLog", () => {
  it("renders nothing for empty / null events", () => {
    const { container } = render(<InlineLog events={[]} />);
    expect(container).toBeEmptyDOMElement();
    const { container: c2 } = render(<InlineLog events={null} />);
    expect(c2).toBeEmptyDOMElement();
  });

  it("renders a collapsible stage chip that expands into a timeline", async () => {
    render(
      <InlineLog
        events={[
          ev({ kind: "stage", name: "retrieve", label: "Retrieve", status: "running" }),
          ev({
            kind: "stage",
            name: "retrieve",
            label: "Retrieve",
            status: "done",
            duration_ms: 1500,
          }),
        ]}
      />,
    );
    const chip = screen.getByTestId("inline-stage-chip");
    expect(chip).toBeInTheDocument();
    await userEvent.click(chip);
    expect(screen.getByTestId("inline-stage-timeline")).toBeInTheDocument();
    // running+done collapsed by name → a single row
    expect(screen.getByTestId("inline-stage-retrieve")).toBeInTheDocument();
  });

  it("renders tool-call rows and expands a done call's detail", async () => {
    render(
      <InlineLog
        events={[
          ev({
            kind: "tool_call",
            name: "create_document",
            label: "create_document",
            status: "done",
            ok: true,
            arguments: { path: "a.md" },
            summary: "created a.md",
          }),
        ]}
      />,
    );
    expect(screen.getByTestId("inline-toolcalls")).toBeInTheDocument();
    expect(screen.getByTestId("inline-toolcall-create_document-done")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("inline-toolcall-toggle-create_document"));
    expect(screen.getByTestId("inline-toolcall-detail-create_document-0")).toHaveTextContent(
      "a.md",
    );
  });

  it("falls back to the generic formatter for an unknown kind", () => {
    render(<InlineLog events={[ev({ kind: "future_thing", label: "hmm", status: "running" })]} />);
    expect(screen.getByTestId("inline-generic")).toHaveTextContent("future_thing");
  });
});

describe("ModifiedDocsLinks", () => {
  it("links each created/updated doc with its version", () => {
    render(
      <ModifiedDocsLinks
        projectId="p1"
        conversationId="c1"
        events={[
          ev({
            kind: "tool_call",
            name: "create_document",
            status: "done",
            ok: true,
            path: "a.md",
            version: 2,
          }),
        ]}
      />,
    );
    const link = screen.getByTestId("modified-doc-link-a.md");
    expect(link).toHaveAttribute("href", "/projects/p1/working-area?conv=c1&path=a.md");
    expect(link).toHaveTextContent("(v2)");
  });

  it("renders nothing without projectId/conversationId", () => {
    const { container } = render(<ModifiedDocsLinks events={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ReferenceTray", () => {
  it("renders nothing when empty", () => {
    const { container } = render(<ReferenceTray references={[]} onRemove={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists chips and removes one", async () => {
    const onRemove = vi.fn();
    render(
      <ReferenceTray
        references={[ev({ kind: "file", source: "live-docs", path: "doc.md" })]}
        onRemove={onRemove}
      />,
    );
    expect(screen.getByTestId("reference-tray")).toHaveTextContent("1 reference attached");
    await userEvent.click(screen.getByLabelText("Remove reference doc.md"));
    expect(onRemove).toHaveBeenCalledWith(0);
  });

  it("flags an over-cap token estimate", () => {
    render(
      <ReferenceTray
        references={[
          ev({
            kind: "excerpt",
            source: "x",
            path: "big",
            range: { start_line: 1, end_line: 200000 },
          }),
        ]}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByTestId("reference-tray")).toHaveTextContent(/over cap, send will 413/);
  });
});

describe("ComingSoonSection", () => {
  it("renders label, description, phase tag and bullets", () => {
    render(
      <ComingSoonSection
        label="Members"
        description="Manage access"
        phaseTag="Phase G"
        bullets={["roles", "invites"]}
      />,
    );
    expect(screen.getByText("Members")).toBeInTheDocument();
    expect(screen.getByText("Manage access")).toBeInTheDocument();
    expect(screen.getByTestId("coming-soon-panel")).toHaveTextContent("Phase G");
    expect(screen.getByText("roles")).toBeInTheDocument();
  });
});

describe("FileTreeContextMenu", () => {
  const target = { path: "a.md", kind: "file" as const, clientX: 10, clientY: 20 };
  const actions = [
    { id: "rename", label: "Rename…", appliesTo: "any" as const },
    { id: "mkdir", label: "New folder…", appliesTo: "folder" as const }, // hidden for a file
    { id: "delete", label: "Delete", appliesTo: "file" as const, destructive: true },
  ];

  it("shows only the actions applicable to the target kind and dispatches a pick", async () => {
    const onPick = vi.fn();
    render(
      <FileTreeContextMenu target={target} actions={actions} onPick={onPick} onClose={vi.fn()} />,
    );
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByText("Rename…")).toBeInTheDocument();
    expect(screen.getByText("Delete")).toBeInTheDocument();
    // folder-only action hidden for a file target
    expect(screen.queryByText("New folder…")).not.toBeInTheDocument();

    await userEvent.click(screen.getByText("Delete"));
    expect(onPick).toHaveBeenCalledWith("delete", target);
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(
      <FileTreeContextMenu target={target} actions={actions} onPick={vi.fn()} onClose={onClose} />,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});

describe("RunTrace", () => {
  const trace = (o: Record<string, unknown>) =>
    ev({
      ts: "2026-01-01T00:00:00Z",
      kind: "agent-dispatch",
      phase: "generate",
      label: "x",
      ok: true,
      ...o,
    });

  it("shows the empty placeholder", () => {
    render(<RunTrace events={[]} />);
    expect(screen.getByText(/No trace events yet/)).toBeInTheDocument();
  });

  it("renders rows with the kind label + a load-more affordance", async () => {
    const onLoadMore = vi.fn();
    render(
      <RunTrace
        events={[
          trace({ label: "dispatched coder", duration_ms: 1500 }),
          trace({ kind: "gate-eval", label: "gate A" }),
        ]}
        canLoadMore
        onLoadMore={onLoadMore}
      />,
    );
    expect(screen.getByText("dispatched coder")).toBeInTheDocument();
    expect(screen.getByText("gate A")).toBeInTheDocument();
    expect(screen.getByText("Agent")).toBeInTheDocument(); // kind label
    await userEvent.click(screen.getByRole("button", { name: "Load older events" }));
    expect(onLoadMore).toHaveBeenCalledWith("2026-01-01T00:00:00Z");
  });
});

describe("SteerComposer", () => {
  it("submits a hint then clears the input", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<SteerComposer onSubmit={onSubmit} />);
    const input = screen.getByPlaceholderText(/focus on the REST surface/);
    await userEvent.type(input, "skip the README");
    await userEvent.click(screen.getByRole("button", { name: "Send hint" }));
    expect(onSubmit).toHaveBeenCalledWith("skip the README");
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("surfaces an error when onSubmit rejects", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error("run not steerable"));
    render(<SteerComposer onSubmit={onSubmit} />);
    await userEvent.type(screen.getByPlaceholderText(/focus on the REST surface/), "go");
    await userEvent.click(screen.getByRole("button", { name: "Send hint" }));
    await waitFor(() => expect(screen.getByText("run not steerable")).toBeInTheDocument());
  });
});
