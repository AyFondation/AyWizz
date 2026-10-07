// =============================================================================
// File: workbench-regions.test.tsx
// Version: 2
// Path: ay_platform_ui/tests/integration/workbench-regions.test.tsx
// Description: The workbench regions — 500-SPEC R-500-016 … R-500-021.
//
//              v2 (2026-10-07) : the coverage fixtures gained the fields
//              C5 actually serves (`allocation_state`, the three object
//              tuples, `project_id` on each link). They were already
//              typed as `RequirementCoverageView`, so `tsc` named every
//              gap the moment the type stopped claiming data C5 never
//              sent — which is the whole argument for typing fixtures.
//
//              THE TESTS THAT CARRY THE REQUIREMENTS, as opposed to the ones
//              that merely render:
//
//              - selecting in ONE region selects in ALL of them (R-500-016).
//                A per-region `useState` passes a render test and fails this
//                one, which is the whole reason the context exists.
//              - a coverage figure ALWAYS shows its auto-accepted share,
//                including at zero (R-500-019). Asserted on both the count
//                and the bar, because "every figure" means every renderer.
//              - the findings region is mounted with no tab and no collapse,
//                and its empty state SAYS "nothing outstanding" rather than
//                being blank (R-500-018).
//              - a proposal is visibly distinguishable from accepted content
//                and carries its review affordance in place (R-500-021).
//              - a link expands INSIDE the row, with no dialog (R-500-017).
// =============================================================================

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ContainerNavigator } from "@/components/workbench/container-navigator";
import { ConversationRail } from "@/components/workbench/conversation-rail";
import { CoverageFigureBar, CoverageFigureView } from "@/components/workbench/coverage-figure";
import { CoverageRegion } from "@/components/workbench/coverage-region";
import { buildFindings, FindingsRegion } from "@/components/workbench/findings-region";
import { ObjectRegion } from "@/components/workbench/object-region";
import { SelectionProvider, useSelection } from "@/components/workbench/selection-context";
import type {
  ContainerCoverageView,
  ContainerSpecView,
  DocObjectSummary,
  RequirementCoverageView,
  SpeculativeList,
  SuspectLinkList,
} from "@/lib/workbenchTypes";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONTAINERS: ContainerSpecView[] = [
  {
    slug: "050-TECH",
    ordinal: 50,
    scope_id: "SC-003",
    scope: "Technical design",
    coverage_obligatory: true,
  },
  {
    slug: "020-FUNC",
    ordinal: 20,
    scope_id: "SC-001",
    scope: "Functional description",
    coverage_obligatory: true,
  },
  {
    slug: "030-ARCH",
    ordinal: 30,
    scope_id: "SC-002",
    scope: "Architecture design",
    coverage_obligatory: false,
  },
];

const OBJECTS: DocObjectSummary[] = [
  {
    object_id: "AD-100",
    container: "030-ARCH",
    type: "paragraph",
    ordinal: 1,
    version: 3,
    review_state: "accepted",
    body: "The brake controller ramps torque at 140 Nm/s.",
  },
  {
    object_id: "AD-101",
    container: "030-ARCH",
    type: "paragraph",
    ordinal: 2,
    version: 1,
    review_state: "proposed",
    body: "Deceleration reaches zero within 150 ms of pedal release.",
    produced_by: { workflow: "WF-AUTHOR", workflow_version: 2 },
  },
  {
    object_id: "AD-102",
    container: "030-ARCH",
    type: "paragraph",
    ordinal: 3,
    version: 2,
    review_state: "auto-accepted",
    body: "Torque is logged per cycle.",
  },
];

const COVERAGE: ContainerCoverageView = {
  container: "030-ARCH",
  allocated: ["CUST-001", "CUST-002", "CUST-003"],
  uncovered: ["CUST-003"],
  weak: ["CUST-002"],
  stale: [],
};

const REQ_COVERAGE: RequirementCoverageView = {
  requirement_id: "CUST-001",
  out_of_project: false,
  fragments: [],
  allocations: [
    {
      container: "030-ARCH",
      allocation_state: "accepted",
      covering_objects: ["AD-100"],
      weak_objects: [],
      stale_objects: [],
      is_covered: true,
      links: [
        {
          object_id: "AD-100",
          project_id: "p1",
          container: "030-ARCH",
          target_id: "CUST-001",
          pinned_version: 4,
          strength: "covered",
          state: "accepted",
          actor: "o.mathieu",
          at: "2026-10-01T09:00:00Z",
        },
      ],
    },
  ],
};

const SUSPECT: SuspectLinkList = {
  links: [
    {
      object_id: "AD-100",
      project_id: "p1",
      container: "030-ARCH",
      target_id: "CUST-001",
      pinned_version: 4,
      current_version: 7,
      strength: "covered",
      state: "accepted",
      actor: "o.mathieu",
      at: "2026-10-01T09:00:00Z",
    },
  ],
};

const SPECULATIVE: SpeculativeList = {
  count: 1,
  stale_count: 1,
  markings: [
    {
      object_id: "AD-101",
      container: "030-ARCH",
      unaccepted_targets: ["FD-010"],
      advanced_targets: ["FD-010"],
    },
  ],
};

/** Shows the shared selection, so a test can assert what every region sees. */
function SelectionProbe() {
  const { container, objectId, requirementId, asPromptContext } = useSelection();
  return (
    <div>
      <span data-testid="probe-container">{container ?? "-"}</span>
      <span data-testid="probe-object">{objectId ?? "-"}</span>
      <span data-testid="probe-requirement">{requirementId ?? "-"}</span>
      <span data-testid="probe-context">{asPromptContext()}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// R-500-016 — one selection, shared
// ---------------------------------------------------------------------------

describe("shared selection (R-500-016)", () => {
  it("selecting a container in the navigator is seen by every region", async () => {
    render(
      <SelectionProvider>
        <ContainerNavigator containers={CONTAINERS} uncoveredCounts={{}} />
        <SelectionProbe />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("container-030-ARCH"));
    expect(screen.getByTestId("probe-container")).toHaveTextContent("030-ARCH");
  });

  it("selecting an object in the object region selects its container too", async () => {
    // Seeded with a DIFFERENT container on purpose. An earlier version of
    // this test seeded "030-ARCH" — the value it then asserted — so it
    // passed even when `selectObject` dropped the container entirely, and a
    // mutation proved it. Only `selectObject` can produce "030-ARCH" here.
    render(
      <SelectionProvider initial={{ container: "020-FUNC" }}>
        <ObjectRegion objects={OBJECTS} coversOf={{}} coverageOf={{}} onRequestCoverage={vi.fn()} />
        <SelectionProbe />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("probe-container")).toHaveTextContent("020-FUNC");

    await userEvent.click(
      within(screen.getByTestId("object-AD-101")).getByText(/Deceleration reaches/),
    );
    expect(screen.getByTestId("probe-object")).toHaveTextContent("AD-101");
    // The object carries container "030-ARCH"; selecting it must SWITCH the
    // shared container, which is the split-brain R-500-016 exists to prevent.
    expect(screen.getByTestId("probe-container")).toHaveTextContent("030-ARCH");
  });

  it("clicking a finding selects its subject in the other regions", async () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <FindingsRegion coverage={COVERAGE} suspect={null} speculative={null} />
        <SelectionProbe />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("finding-CUST-003"));
    expect(screen.getByTestId("probe-requirement")).toHaveTextContent("CUST-003");
  });

  it("a finding on an object selects the object AND its container", async () => {
    render(
      <SelectionProvider>
        <FindingsRegion coverage={null} suspect={SUSPECT} speculative={null} />
        <SelectionProbe />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("finding-AD-100"));
    expect(screen.getByTestId("probe-object")).toHaveTextContent("AD-100");
    expect(screen.getByTestId("probe-container")).toHaveTextContent("030-ARCH");
  });

  it("switching container clears the object, which no longer exists there", async () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH", objectId: "AD-100" }}>
        <ContainerNavigator containers={CONTAINERS} uncoveredCounts={{}} />
        <SelectionProbe />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("container-020-FUNC"));
    expect(screen.getByTestId("probe-object")).toHaveTextContent("-");
  });

  it("the composer submits the selection so the user never restates it", async () => {
    const onSend = vi.fn();
    render(
      <SelectionProvider
        initial={{ container: "030-ARCH", objectId: "AD-101", requirementId: "CUST-001" }}
      >
        <ConversationRail turns={[]} onSend={onSend} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("rail-context")).toHaveTextContent(
      "container 030-ARCH, object AD-101, requirement CUST-001",
    );
    await userEvent.type(screen.getByTestId("rail-input"), "redraft this");
    await userEvent.click(screen.getByTestId("rail-send"));
    expect(onSend).toHaveBeenCalledWith(
      "redraft this",
      "container 030-ARCH, object AD-101, requirement CUST-001",
    );
  });

  it("the composer says nothing is selected rather than sending an empty context", () => {
    render(
      <SelectionProvider>
        <ConversationRail turns={[]} onSend={vi.fn()} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("rail-context")).toHaveTextContent("Nothing selected");
  });

  it("a region outside the provider throws rather than keeping private state", () => {
    // The silent-default alternative would look like it works and share
    // nothing — exactly what R-500-016 forbids, reintroduced invisibly.
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() =>
      render(<FindingsRegion coverage={null} suspect={null} speculative={null} />),
    ).toThrow(/SelectionProvider/);
    quiet.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// R-500-019 — every figure carries its auto-accepted share
// ---------------------------------------------------------------------------

describe("coverage figures (R-500-019)", () => {
  it("shows the auto-accepted share even when it is zero", () => {
    // Zero is information: its absence would leave a fully-reviewed figure
    // indistinguishable from a component that forgot the breakdown.
    render(
      <CoverageFigureView
        label="answered"
        figure={{ total: 12, autoAccepted: 0, outOf: 12 }}
        testId="fig"
      />,
    );
    expect(screen.getByTestId("fig-auto")).toHaveTextContent("0 of 12 auto-accepted");
  });

  it("shows the share and its percentage when some coverage was auto-accepted", () => {
    render(
      <CoverageFigureView
        label="answered"
        figure={{ total: 10, autoAccepted: 4, outOf: 20 }}
        testId="fig"
      />,
    );
    expect(screen.getByTestId("fig-auto")).toHaveTextContent("4 of 10 auto-accepted");
    expect(screen.getByTestId("fig-auto")).toHaveTextContent("40%");
  });

  it("explains what auto-accepted means when any is present", () => {
    render(
      <CoverageFigureView label="answered" figure={{ total: 10, autoAccepted: 1 }} testId="fig" />,
    );
    expect(screen.getByText(/without individual examination/)).toBeInTheDocument();
  });

  it("the bar draws auto-accepted as its own band, never merged", () => {
    render(<CoverageFigureBar figure={{ total: 10, autoAccepted: 4, outOf: 20 }} testId="bar" />);
    const reviewed = screen.getByTestId("bar-reviewed");
    const auto = screen.getByTestId("bar-auto");
    expect(reviewed).toHaveStyle({ width: "30%" });
    expect(auto).toHaveStyle({ width: "20%" });
  });

  it("the bar labels both parts for a screen reader", () => {
    render(<CoverageFigureBar figure={{ total: 10, autoAccepted: 4, outOf: 20 }} testId="bar" />);
    expect(screen.getByLabelText("6 reviewed, 4 auto-accepted, out of 20")).toBeInTheDocument();
  });

  it("an empty figure renders without dividing by zero", () => {
    render(<CoverageFigureBar figure={{ total: 0, autoAccepted: 0, outOf: 0 }} testId="bar" />);
    expect(screen.getByTestId("bar-reviewed")).toHaveStyle({ width: "0%" });
  });
});

// ---------------------------------------------------------------------------
// R-500-018 — the findings region
// ---------------------------------------------------------------------------

describe("findings region (R-500-018)", () => {
  it("lists uncovered allocations of the open container", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <FindingsRegion coverage={COVERAGE} suspect={null} speculative={null} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("finding-CUST-003")).toHaveTextContent(
      "allocated to 030-ARCH and not yet answered",
    );
  });

  it("cites a criterion identifier and a location on every finding", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <FindingsRegion coverage={COVERAGE} suspect={SUSPECT} speculative={SPECULATIVE} />
      </SelectionProvider>,
    );
    for (const finding of buildFindings({
      coverage: COVERAGE,
      suspect: SUSPECT,
      speculative: SPECULATIVE,
    })) {
      expect(finding.criterion).toMatch(/^CRIT-/);
      expect(finding.location.length).toBeGreaterThan(0);
    }
  });

  it("says nothing is outstanding instead of rendering blank", () => {
    render(
      <SelectionProvider>
        <FindingsRegion coverage={null} suspect={null} speculative={null} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("findings-empty")).toHaveTextContent("Nothing outstanding");
  });

  it("is mounted with no tab and no collapse control", () => {
    render(
      <SelectionProvider>
        <FindingsRegion coverage={null} suspect={null} speculative={null} />
      </SelectionProvider>,
    );
    const region = screen.getByTestId("findings-region");
    expect(region).toBeInTheDocument();
    // No control can hide it: the gap list is the one thing a reviewer must
    // not be able to work without seeing.
    expect(within(region).queryByRole("tab")).toBeNull();
    expect(within(region).queryByText(/collapse|hide/i)).toBeNull();
  });

  it("reports a speculative object whose upstream changed before acceptance", () => {
    const findings = buildFindings({
      coverage: null,
      suspect: null,
      speculative: SPECULATIVE,
    });
    expect(findings[0].detail).toContain("changed before acceptance");
  });

  it("reports a speculative object whose upstream has not moved differently", () => {
    const findings = buildFindings({
      coverage: null,
      suspect: null,
      speculative: {
        count: 1,
        stale_count: 0,
        markings: [
          {
            object_id: "AD-101",
            container: "030-ARCH",
            unaccepted_targets: ["FD-010"],
            advanced_targets: [],
          },
        ],
      },
    });
    expect(findings[0].detail).toContain("not yet accepted");
    expect(findings[0].detail).not.toContain("changed before acceptance");
  });

  it("names both versions on a suspect link", () => {
    const findings = buildFindings({ coverage: null, suspect: SUSPECT, speculative: null });
    expect(findings[0].detail).toContain("pins v4");
    expect(findings[0].detail).toContain("now at v7");
  });

  it("derives nothing when every view is absent", () => {
    expect(buildFindings({ coverage: null, suspect: null, speculative: null })).toEqual([]);
  });

  it("counts what it lists", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <FindingsRegion coverage={COVERAGE} suspect={SUSPECT} speculative={SPECULATIVE} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("findings-count")).toHaveTextContent("4");
  });
});

// ---------------------------------------------------------------------------
// R-500-021 — a proposal must not read as settled content
// ---------------------------------------------------------------------------

describe("object region (R-500-021)", () => {
  it("badges a proposal distinctly and prompts for review in place", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion objects={OBJECTS} coversOf={{}} coverageOf={{}} onRequestCoverage={vi.fn()} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("state-proposed")).toBeInTheDocument();
    // The affordance is at the point of reading, not in a separate queue.
    expect(screen.getByTestId("review-prompt-AD-101")).toHaveTextContent(/Awaiting your review/);
  });

  it("badges auto-accepted distinctly from accepted", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion objects={OBJECTS} coversOf={{}} coverageOf={{}} onRequestCoverage={vi.fn()} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("state-accepted")).toHaveTextContent("Accepted");
    expect(screen.getByTestId("state-auto-accepted")).toHaveTextContent("Auto-accepted");
  });

  it("does not prompt a review on an already-accepted object", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion objects={OBJECTS} coversOf={{}} coverageOf={{}} onRequestCoverage={vi.fn()} />
      </SelectionProvider>,
    );
    expect(screen.queryByTestId("review-prompt-AD-100")).toBeNull();
  });

  it("asks the reader to choose a container before showing objects", () => {
    render(
      <SelectionProvider>
        <ObjectRegion objects={[]} coversOf={{}} coverageOf={{}} onRequestCoverage={vi.fn()} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("object-region-empty")).toBeInTheDocument();
  });

  it("distinguishes an empty container from one still loading", () => {
    const { rerender } = render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion
          objects={[]}
          coversOf={{}}
          coverageOf={{}}
          onRequestCoverage={vi.fn()}
          loading
        />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("objects-loading")).toBeInTheDocument();
    rerender(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion objects={[]} coversOf={{}} coverageOf={{}} onRequestCoverage={vi.fn()} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("objects-none")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// R-500-017 — a link expands in place
// ---------------------------------------------------------------------------

describe("in-place link expansion (R-500-017)", () => {
  it("expands within the object row, with no dialog", async () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion
          objects={OBJECTS}
          coversOf={{ "AD-100": ["CUST-001"] }}
          coverageOf={{ "CUST-001": REQ_COVERAGE }}
          onRequestCoverage={vi.fn()}
        />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("link-AD-100-CUST-001"));
    const row = screen.getByTestId("object-AD-100");
    // Inside the row — not a portal, not a dialog.
    expect(within(row).getByTestId("expansion-CUST-001")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reveals the target's allocations and pinned version", async () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion
          objects={OBJECTS}
          coversOf={{ "AD-100": ["CUST-001"] }}
          coverageOf={{ "CUST-001": REQ_COVERAGE }}
          onRequestCoverage={vi.fn()}
        />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("link-AD-100-CUST-001"));
    const expansion = screen.getByTestId("expansion-CUST-001");
    expect(expansion).toHaveTextContent("030-ARCH");
    expect(expansion).toHaveTextContent("answered");
    expect(expansion).toHaveTextContent("pinned v4");
  });

  it("fetches the target's coverage the first time only", async () => {
    const onRequest = vi.fn();
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion
          objects={OBJECTS}
          coversOf={{ "AD-100": ["CUST-001"] }}
          coverageOf={{}}
          onRequestCoverage={onRequest}
        />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("link-AD-100-CUST-001"));
    expect(onRequest).toHaveBeenCalledWith("CUST-001");
    expect(screen.getByTestId("link-loading")).toBeInTheDocument();
  });

  it("expanding a link selects the requirement for the other regions", async () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion
          objects={OBJECTS}
          coversOf={{ "AD-100": ["CUST-001"] }}
          coverageOf={{ "CUST-001": REQ_COVERAGE }}
          onRequestCoverage={vi.fn()}
        />
        <SelectionProbe />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("link-AD-100-CUST-001"));
    expect(screen.getByTestId("probe-requirement")).toHaveTextContent("CUST-001");
  });

  it("clicking the same link again collapses it", async () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion
          objects={OBJECTS}
          coversOf={{ "AD-100": ["CUST-001"] }}
          coverageOf={{ "CUST-001": REQ_COVERAGE }}
          onRequestCoverage={vi.fn()}
        />
      </SelectionProvider>,
    );
    const chip = screen.getByTestId("link-AD-100-CUST-001");
    await userEvent.click(chip);
    await userEvent.click(chip);
    expect(screen.queryByTestId("expansion-CUST-001")).toBeNull();
  });

  it("says so when an object answers nothing yet", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ObjectRegion objects={OBJECTS} coversOf={{}} coverageOf={{}} onRequestCoverage={vi.fn()} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("covers-none-AD-100")).toHaveTextContent("answers nothing yet");
  });
});

// ---------------------------------------------------------------------------
// R-500-020 — cluster grouping above a threshold
// ---------------------------------------------------------------------------

describe("coverage region (R-500-020)", () => {
  const clusters = [
    {
      cluster_id: "c1",
      label: "Braking timing",
      requirement_ids: ["CUST-001", "CUST-002"],
      figure: { total: 1, autoAccepted: 1, outOf: 2 },
    },
  ];

  it("lists individual requirements below the threshold", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <CoverageRegion
          coverage={COVERAGE}
          figure={{ total: 2, autoAccepted: 0, outOf: 3 }}
          clusters={clusters}
          requirementCount={3}
          autoAcceptedIds={new Set()}
          threshold={500}
        />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("requirement-list")).toBeInTheDocument();
    expect(screen.queryByTestId("grouping-notice")).toBeNull();
  });

  it("groups by cluster above the threshold AND states both numbers", () => {
    // Stating them is what stops grouping being mistaken for missing data.
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <CoverageRegion
          coverage={COVERAGE}
          figure={{ total: 2, autoAccepted: 0, outOf: 3 }}
          clusters={clusters}
          requirementCount={30000}
          autoAcceptedIds={new Set()}
          threshold={500}
        />
      </SelectionProvider>,
    );
    const notice = screen.getByTestId("grouping-notice");
    expect(notice).toHaveTextContent("30000 requirements");
    expect(notice).toHaveTextContent("500 threshold");
    expect(screen.getByTestId("cluster-list")).toBeInTheDocument();
  });

  it("expands a cluster to its individual requirements", async () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <CoverageRegion
          coverage={COVERAGE}
          figure={{ total: 2, autoAccepted: 0, outOf: 3 }}
          clusters={clusters}
          requirementCount={30000}
          autoAcceptedIds={new Set(["CUST-001"])}
          threshold={500}
        />
        <SelectionProbe />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("cluster-c1"));
    expect(screen.getByTestId("cluster-expanded-c1")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("cluster-req-CUST-002"));
    expect(screen.getByTestId("probe-requirement")).toHaveTextContent("CUST-002");
  });

  it("flags an auto-accepted requirement inside an expanded cluster", async () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <CoverageRegion
          coverage={COVERAGE}
          figure={{ total: 2, autoAccepted: 1, outOf: 3 }}
          clusters={clusters}
          requirementCount={30000}
          autoAcceptedIds={new Set(["CUST-001"])}
          threshold={500}
        />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("cluster-c1"));
    expect(screen.getByTestId("cluster-req-CUST-001")).toHaveTextContent("auto-accepted");
  });

  it("renders the container figure through the shared figure component", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <CoverageRegion
          coverage={COVERAGE}
          figure={{ total: 2, autoAccepted: 1, outOf: 3 }}
          clusters={[]}
          requirementCount={3}
          autoAcceptedIds={new Set()}
        />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("container-figure-auto")).toHaveTextContent("1 of 2 auto-accepted");
  });

  it("asks for a container rather than showing an empty matrix", () => {
    render(
      <SelectionProvider>
        <CoverageRegion
          coverage={null}
          figure={null}
          clusters={[]}
          requirementCount={0}
          autoAcceptedIds={new Set()}
        />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("coverage-empty")).toBeInTheDocument();
  });

  it("marks unanswered and weak requirements distinctly in the flat list", () => {
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <CoverageRegion
          coverage={COVERAGE}
          figure={{ total: 2, autoAccepted: 0, outOf: 3 }}
          clusters={[]}
          requirementCount={3}
          autoAcceptedIds={new Set()}
        />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("matrix-req-CUST-003")).toHaveTextContent("unanswered");
    expect(screen.getByTestId("matrix-req-CUST-002")).toHaveTextContent("weak");
    expect(screen.getByTestId("matrix-req-CUST-001")).toHaveTextContent("answered");
  });
});

// ---------------------------------------------------------------------------
// The navigator
// ---------------------------------------------------------------------------

describe("container navigator", () => {
  it("orders containers by the cycle's ordinal, not alphabetically", () => {
    render(
      <SelectionProvider>
        <ContainerNavigator containers={CONTAINERS} uncoveredCounts={{}} />
      </SelectionProvider>,
    );
    const buttons = screen.getAllByRole("button").map((b) => b.textContent ?? "");
    expect(buttons[0]).toContain("020-FUNC");
    expect(buttons[1]).toContain("030-ARCH");
    expect(buttons[2]).toContain("050-TECH");
  });

  it("shows an outstanding count when there is one", () => {
    render(
      <SelectionProvider>
        <ContainerNavigator containers={CONTAINERS} uncoveredCounts={{ "030-ARCH": 3 }} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("outstanding-030-ARCH")).toHaveTextContent("3");
  });

  it("shows nothing rather than zero when the count is unknown", () => {
    // "0 outstanding" before asking is a figure a reader would act on wrongly.
    render(
      <SelectionProvider>
        <ContainerNavigator containers={CONTAINERS} uncoveredCounts={{}} />
      </SelectionProvider>,
    );
    expect(screen.queryByTestId("outstanding-030-ARCH")).toBeNull();
  });

  it("reports an absent cycle instead of an empty list", () => {
    render(
      <SelectionProvider>
        <ContainerNavigator containers={[]} uncoveredCounts={{}} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("no-containers")).toHaveTextContent("No published cycle");
  });

  it("marks the active container for assistive technology", async () => {
    render(
      <SelectionProvider>
        <ContainerNavigator containers={CONTAINERS} uncoveredCounts={{}} />
      </SelectionProvider>,
    );
    await userEvent.click(screen.getByTestId("container-030-ARCH"));
    expect(screen.getByTestId("container-030-ARCH")).toHaveAttribute("aria-current", "true");
  });
});

// ---------------------------------------------------------------------------
// The conversation rail
// ---------------------------------------------------------------------------

describe("conversation rail", () => {
  it("records the context a turn was sent with", () => {
    render(
      <SelectionProvider>
        <ConversationRail
          turns={[{ id: "t1", role: "user", text: "draft it", context: "object AD-101" }]}
          onSend={vi.fn()}
        />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("turn-t1")).toHaveTextContent("re: object AD-101");
  });

  it("refuses to send an empty message", async () => {
    const onSend = vi.fn();
    render(
      <SelectionProvider>
        <ConversationRail turns={[]} onSend={onSend} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("rail-send")).toBeDisabled();
    await userEvent.type(screen.getByTestId("rail-input"), "   ");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("sends on Enter and keeps Shift+Enter for a newline", async () => {
    const onSend = vi.fn();
    render(
      <SelectionProvider initial={{ container: "030-ARCH" }}>
        <ConversationRail turns={[]} onSend={onSend} />
      </SelectionProvider>,
    );
    const input = screen.getByTestId("rail-input");
    await userEvent.type(input, "line one{Shift>}{Enter}{/Shift}line two");
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.type(input, "{Enter}");
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("does not send while busy", async () => {
    const onSend = vi.fn();
    render(
      <SelectionProvider>
        <ConversationRail turns={[]} onSend={onSend} busy />
      </SelectionProvider>,
    );
    await userEvent.type(screen.getByTestId("rail-input"), "anything");
    expect(screen.getByTestId("rail-send")).toBeDisabled();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("invites the reader when there are no turns", () => {
    render(
      <SelectionProvider>
        <ConversationRail turns={[]} onSend={vi.fn()} />
      </SelectionProvider>,
    );
    expect(screen.getByTestId("rail-empty")).toBeInTheDocument();
  });
});
