// =============================================================================
// File: workbench-baseline.test.tsx
// Path: ay_platform_ui/tests/integration/workbench-baseline.test.tsx
// Description: The baseline panel — 310-SPEC R-310-201 / R-310-207.
//
//              THE TEST THAT CARRIES THE REQUIREMENT asserts the take
//              control is DISABLED while a precondition stands, and that
//              every refusal is listed. R-310-201 is a gate; a UI that
//              offers the button and reports the 409 afterwards has taught
//              the reviewer the same thing one round trip later, with an
//              error to interpret instead of a list to act on.
//
//              The export links are asserted to be plain anchors with the
//              render URL: the response is a FILE, and a fetch through the
//              JSON client would corrupt it.
// =============================================================================

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BaselinePanel } from "@/components/workbench/baseline-panel";
import type { BaselineList, BaselineReadinessView } from "@/lib/workbenchTypes";

const READY: BaselineReadinessView = { project_id: "p1", refusals: [] };

const BLOCKED: BaselineReadinessView = {
  project_id: "p1",
  refusals: [
    {
      blocker: "open-change-ticket",
      subject: "p1:W14:CUST-001",
      detail: "a supplied change has not been absorbed end to end",
    },
    {
      blocker: "critical-coverage-gap",
      subject: "CUST-009",
      detail: "rated ASIL-D and answered by nothing",
    },
    {
      blocker: "stale-coverage-link",
      subject: "AD-100->CUST-001",
      detail: "pins v2 of CUST-001, now at v7",
    },
  ],
};

const TAKEN: BaselineList = {
  count: 1,
  baselines: [
    {
      tag: "SOP-2026-10",
      project_id: "p1",
      cycle_id: "C-AUTO",
      cycle_version: 2,
      created_by: "o.mathieu",
      created_at: "2026-10-04T09:00:00Z",
      object_count: 412,
      link_count: 388,
      note: "Gate review sign-off.",
    },
  ],
};

const renderUrl = (tag: string, fmt: "docx" | "pdf") =>
  `/api/v1/projects/p1/baselines/${tag}/render/${fmt}`;

function panel(props: Partial<React.ComponentProps<typeof BaselinePanel>> = {}) {
  return render(
    <BaselinePanel
      readiness={READY}
      baselines={TAKEN}
      cycleId="C-AUTO"
      onTake={vi.fn()}
      renderUrl={renderUrl}
      {...props}
    />,
  );
}

// ---------------------------------------------------------------------------
// R-310-201 — the gate, shown before the button
// ---------------------------------------------------------------------------

describe("the baseline gate (R-310-201)", () => {
  it("lists EVERY outstanding precondition, not the first", () => {
    panel({ readiness: BLOCKED });
    const blocked = screen.getByTestId("readiness-blocked");
    expect(blocked).toHaveTextContent("3 preconditions outstanding");
    expect(screen.getByTestId("refusal-p1:W14:CUST-001")).toBeInTheDocument();
    expect(screen.getByTestId("refusal-CUST-009")).toBeInTheDocument();
    expect(screen.getByTestId("refusal-AD-100->CUST-001")).toBeInTheDocument();
  });

  it("names each blocker in terms a reviewer can act on", () => {
    panel({ readiness: BLOCKED });
    expect(screen.getByTestId("refusal-CUST-009")).toHaveTextContent(
      "Rated requirement unanswered",
    );
    expect(screen.getByTestId("refusal-AD-100->CUST-001")).toHaveTextContent("now at v7");
  });

  it("disables taking a baseline while any precondition stands", () => {
    panel({ readiness: BLOCKED });
    expect(screen.getByTestId("baseline-take")).toBeDisabled();
    expect(screen.getByTestId("baseline-tag")).toBeDisabled();
  });

  it("says so plainly when nothing is outstanding", () => {
    panel();
    expect(screen.getByTestId("readiness-ready")).toHaveTextContent("Nothing outstanding");
  });

  it("waits rather than claiming readiness before the gate answered", () => {
    panel({ readiness: null });
    expect(screen.getByTestId("readiness-loading")).toBeInTheDocument();
    expect(screen.getByTestId("baseline-take")).toBeDisabled();
  });

  it("uses the singular for one precondition", () => {
    panel({
      readiness: { project_id: "p1", refusals: [BLOCKED.refusals[0]] },
    });
    expect(screen.getByTestId("readiness-blocked")).toHaveTextContent("1 precondition outstanding");
  });
});

// ---------------------------------------------------------------------------
// Taking one
// ---------------------------------------------------------------------------

describe("taking a baseline", () => {
  it("requires a tag the human chose", async () => {
    // Not pre-filled: the tag is how a reviewer cites this months later.
    const onTake = vi.fn();
    panel({ onTake });
    expect(screen.getByTestId("baseline-tag")).toHaveValue("");
    expect(screen.getByTestId("baseline-take")).toBeDisabled();

    await userEvent.type(screen.getByTestId("baseline-tag"), "SOP-2026-11");
    expect(screen.getByTestId("baseline-take")).toBeEnabled();
  });

  it("submits the tag and the note", async () => {
    const onTake = vi.fn();
    panel({ onTake });
    await userEvent.type(screen.getByTestId("baseline-tag"), "SOP-2026-11");
    await userEvent.type(screen.getByTestId("baseline-note"), "Second gate.");
    await userEvent.click(screen.getByTestId("baseline-take"));
    expect(onTake).toHaveBeenCalledWith("SOP-2026-11", "Second gate.");
  });

  it("trims the tag so a stray space cannot create a different one", async () => {
    const onTake = vi.fn();
    panel({ onTake });
    await userEvent.type(screen.getByTestId("baseline-tag"), "  SOP-X  ");
    await userEvent.click(screen.getByTestId("baseline-take"));
    expect(onTake).toHaveBeenCalledWith("SOP-X", "");
  });

  it("refuses to take one without a published cycle", () => {
    panel({ cycleId: null });
    expect(screen.getByTestId("baseline-no-cycle")).toHaveTextContent(
      "needs one to have containers",
    );
    expect(screen.getByTestId("baseline-take")).toBeDisabled();
  });

  it("surfaces a refusal from the backend", () => {
    panel({ error: "baseline 'SOP-X' already exists; a baseline is immutable" });
    expect(screen.getByTestId("baseline-error")).toHaveTextContent("immutable");
  });

  it("does not offer a second click while one is in flight", () => {
    panel({ busy: true });
    expect(screen.getByTestId("baseline-take")).toBeDisabled();
    expect(screen.getByTestId("baseline-take")).toHaveTextContent("Taking…");
  });
});

// ---------------------------------------------------------------------------
// R-310-207 — the export
// ---------------------------------------------------------------------------

describe("exporting a baseline (R-310-207)", () => {
  it("offers Word and PDF as plain links to the render route", () => {
    // Anchors, not fetches: the response is a file.
    panel();
    const row = screen.getByTestId("baseline-SOP-2026-10");
    expect(within(row).getByTestId("export-docx-SOP-2026-10")).toHaveAttribute(
      "href",
      "/api/v1/projects/p1/baselines/SOP-2026-10/render/docx",
    );
    expect(within(row).getByTestId("export-pdf-SOP-2026-10")).toHaveAttribute(
      "href",
      "/api/v1/projects/p1/baselines/SOP-2026-10/render/pdf",
    );
  });

  it("states what the baseline contains without listing it", () => {
    // A manifest of 30 000 objects has no business in a chooser (R-310-300).
    panel();
    const row = screen.getByTestId("baseline-SOP-2026-10");
    expect(row).toHaveTextContent("412 objects");
    expect(row).toHaveTextContent("388 pins");
    expect(row).toHaveTextContent("C-AUTO v2");
    expect(row).toHaveTextContent("Gate review sign-off.");
  });

  it("says when no baseline has been taken", () => {
    panel({ baselines: { count: 0, baselines: [] } });
    expect(screen.getByTestId("baseline-none")).toHaveTextContent("No baseline taken yet");
  });

  it("renders nothing for the list before it has loaded", () => {
    panel({ baselines: null });
    expect(screen.queryByTestId("baseline-list")).toBeNull();
    expect(screen.queryByTestId("baseline-none")).toBeNull();
  });
});
