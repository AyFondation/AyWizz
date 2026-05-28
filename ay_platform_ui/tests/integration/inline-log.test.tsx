// =============================================================================
// File: inline-log.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/inline-log.test.tsx
// Description: Tests for the chain-of-thought tool-call rendering in
//              <InlineLog> (inline-log.tsx v3, #4). A done tool_call
//              with arguments is expandable : the toggle reveals the
//              step/round, the call arguments, and the result summary.
//              Running rows stay compact.
// =============================================================================

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { InlineLog, ModifiedDocsLinks } from "@/components/inline-log";
import type { InlineEvent } from "@/lib/types";

const DONE_CREATE: InlineEvent = {
  kind: "tool_call",
  status: "done",
  name: "create_document",
  label: "created docs/x.md",
  ok: true,
  round: 1,
  summary: "created docs/x.md",
  path: "docs/x.md",
  arguments: { path: "docs/x.md", content: "# Title… (1200 chars)" },
};

describe("InlineLog chain-of-thought tool detail", () => {
  it("renders the step badge and result summary on a done tool call", () => {
    render(<InlineLog events={[DONE_CREATE]} />);
    expect(screen.getByText("step 1")).toBeInTheDocument();
    expect(screen.getByText("— created docs/x.md")).toBeInTheDocument();
  });

  it("hides the argument detail until the row is toggled open", async () => {
    render(<InlineLog events={[DONE_CREATE]} />);
    // Detail is collapsed initially.
    expect(screen.queryByTestId("inline-toolcall-detail-create_document-0")).toBeNull();
    await userEvent.click(screen.getByTestId("inline-toolcall-toggle-create_document"));
    // Expanded : the call arguments are now visible (key + truncated value).
    const detail = screen.getByTestId("inline-toolcall-detail-create_document-0");
    expect(detail).toHaveTextContent("path");
    expect(detail).toHaveTextContent("content");
    expect(detail).toHaveTextContent("# Title… (1200 chars)");
  });

  it("keeps a running tool call compact (no expand toggle)", () => {
    const running: InlineEvent = {
      kind: "tool_call",
      status: "running",
      name: "read_document",
      label: "read_document",
      round: 1,
    };
    render(<InlineLog events={[running]} />);
    expect(screen.queryByTestId("inline-toolcall-toggle-read_document")).toBeNull();
    expect(screen.getByText("read_document")).toBeInTheDocument();
  });

  it("does not render any deep-link inside the inline log (moved below)", () => {
    render(<InlineLog events={[DONE_CREATE]} />);
    expect(screen.queryByTestId("inline-toolcall-open-create_document")).toBeNull();
  });
});

describe("ModifiedDocsLinks", () => {
  const events: InlineEvent[] = [
    { ...DONE_CREATE, version: 3 },
    {
      kind: "tool_call",
      status: "done",
      name: "read_document",
      label: "read",
      ok: true,
      round: 2,
      summary: "read docs/x.md",
      path: "docs/x.md",
    },
  ];

  it("renders one versioned link per modified doc (create/update only)", () => {
    render(<ModifiedDocsLinks events={events} projectId="proj-1" conversationId="conv-1" />);
    const link = screen.getByTestId("modified-doc-link-docs/x.md");
    expect(link).toHaveTextContent("Open in working area: x.md");
    expect(link).toHaveTextContent("(v3)");
    expect(link).toHaveAttribute(
      "href",
      "/projects/proj-1/working-area?conv=conv-1&path=docs%2Fx.md",
    );
  });

  it("renders nothing without a project / conversation context", () => {
    const { container } = render(<ModifiedDocsLinks events={events} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when no document was created or updated", () => {
    const readOnly: InlineEvent[] = [
      {
        kind: "tool_call",
        status: "done",
        name: "list_documents",
        label: "2 document(s)",
        ok: true,
        round: 1,
        summary: "2 document(s)",
      },
    ];
    const { container } = render(
      <ModifiedDocsLinks events={readOnly} projectId="p" conversationId="c" />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
