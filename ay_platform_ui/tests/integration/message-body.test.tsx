// =============================================================================
// File: message-body.test.tsx
// Path: ay_platform_ui/tests/integration/message-body.test.tsx
// Description: Tests for <MessageBody>, the markdown renderer resolving
//              Q-500-003. Covers the reported defect (bold markers shown
//              literally), the security property that justified choosing
//              react-markdown over `marked` (raw HTML is escaped, never
//              mounted), GFM tables, link hardening, and the partial-markdown
//              case that occurs on every streamed turn.
// =============================================================================

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageBody } from "@/components/message-body";

describe("MessageBody", () => {
  it("renders bold markers as emphasis, not as literal asterisks", () => {
    // The exact defect reported: "La capitale de l'Italie est **Rome**."
    const { container } = render(<MessageBody content="La capitale de l'Italie est **Rome**." />);

    const strong = container.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong?.textContent).toBe("Rome");
    // The markers themselves must be gone from the rendered text.
    expect(container.textContent).toBe("La capitale de l'Italie est Rome.");
    expect(container.textContent).not.toContain("**");
  });

  it("escapes embedded raw HTML instead of mounting it", () => {
    // THE security property. LLM output is influenceable by the ingested RAG
    // corpus, so it is untrusted. react-markdown emits React elements and
    // escapes raw HTML; `marked` + dangerouslySetInnerHTML would have mounted
    // this as a live element. If `rehype-raw` is ever added, this test fails —
    // which is the point.
    const hostile = '<img src="x" onerror="alert(1)"> and <script>alert(2)</script>';
    const { container } = render(<MessageBody content={hostile} />);

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    // The markup survives as visible text rather than vanishing silently.
    expect(container.textContent).toContain("<img");
    expect(container.textContent).toContain("<script>");
  });

  it("renders a GFM table", () => {
    const md = ["| Model | Tier |", "| --- | --- |", "| opus | high |"].join("\n");
    render(<MessageBody content={md} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Model" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "opus" })).toBeInTheDocument();
  });

  it("opens links in a new tab without leaking window.opener", () => {
    render(<MessageBody content="[Anthropic](https://www.anthropic.com)" />);

    const link = screen.getByRole("link", { name: "Anthropic" });
    expect(link).toHaveAttribute("href", "https://www.anthropic.com");
    expect(link).toHaveAttribute("target", "_blank");
    // Without `noopener` the opened page keeps a handle back to this origin.
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(link.getAttribute("rel")).toContain("noreferrer");
  });

  it("renders partial markdown mid-stream without dropping the text", () => {
    // Every in-flight turn passes through states like this one: the opening
    // `**` has arrived, the closing pair has not. The text must stay visible
    // (it reflows into <strong> once the stream completes).
    const { container } = render(<MessageBody content="La capitale est **Rom" />);

    expect(container.textContent).toContain("La capitale est");
    expect(container.textContent).toContain("Rom");
  });

  it("renders fenced code blocks", () => {
    const md = ["```", "kubectl get pods", "```"].join("\n");
    const { container } = render(<MessageBody content={md} />);

    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain("kubectl get pods");
  });

  it("renders an empty string without crashing", () => {
    const { container } = render(<MessageBody content="" />);
    expect(container.querySelector('[data-testid="message-body"]')).not.toBeNull();
  });
});
