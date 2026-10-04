// =============================================================================
// File: workbench-types.test.ts
// Path: ay_platform_ui/tests/unit/workbench-types.test.ts
// Description: The pure helpers behind 500-SPEC R-500-019 / R-500-021.
//
//              These four functions decide what every coverage figure in the
//              workbench reports, so they are tested directly rather than
//              only through the components that call them: a rendering test
//              that happens to exercise the happy path leaves the guards
//              (empty figure, zero total) unproven, and those are exactly
//              where a figure turns into `NaN%` in front of a reviewer.
// =============================================================================

import { describe, expect, it } from "vitest";

import {
  ACCEPTED_STATES,
  autoAcceptedShare,
  type ContainerCoverageView,
  containerFigure,
  hasAutoAccepted,
  isProposal,
  type ReviewState,
} from "@/lib/workbenchTypes";

describe("autoAcceptedShare (R-500-019)", () => {
  it("returns the fraction granted without individual examination", () => {
    expect(autoAcceptedShare({ total: 10, autoAccepted: 4 })).toBe(0.4);
  });

  it("returns 0 for an empty figure rather than NaN", () => {
    // A reviewer must never be shown "NaN%" where a percentage belongs.
    expect(autoAcceptedShare({ total: 0, autoAccepted: 0 })).toBe(0);
  });

  it("returns 0 rather than dividing when the total is negative", () => {
    expect(autoAcceptedShare({ total: -1, autoAccepted: 0 })).toBe(0);
  });

  it("returns 1 when every unit was auto-accepted", () => {
    expect(autoAcceptedShare({ total: 7, autoAccepted: 7 })).toBe(1);
  });
});

describe("hasAutoAccepted (R-500-019)", () => {
  it("is false when nothing was granted by cluster review", () => {
    expect(hasAutoAccepted({ total: 9, autoAccepted: 0 })).toBe(false);
  });

  it("is true for a single auto-accepted unit", () => {
    // One is enough: the figure is no longer purely a review result.
    expect(hasAutoAccepted({ total: 9, autoAccepted: 1 })).toBe(true);
  });
});

describe("isProposal (R-500-021)", () => {
  it("is true only for `proposed`", () => {
    expect(isProposal("proposed")).toBe(true);
    for (const state of ["accepted", "auto-accepted", "stale", "rejected"] as ReviewState[]) {
      expect(isProposal(state)).toBe(false);
    }
  });
});

describe("ACCEPTED_STATES (R-310-007)", () => {
  it("counts both acceptance kinds as a sound foundation", () => {
    expect(ACCEPTED_STATES.has("accepted")).toBe(true);
    expect(ACCEPTED_STATES.has("auto-accepted")).toBe(true);
  });

  it("excludes every non-acceptance state", () => {
    for (const state of ["proposed", "stale", "rejected"] as ReviewState[]) {
      expect(ACCEPTED_STATES.has(state)).toBe(false);
    }
  });
});

describe("containerFigure (R-500-019)", () => {
  const view = (over: Partial<ContainerCoverageView> = {}): ContainerCoverageView => ({
    container: "030-ARCH",
    allocated: ["A", "B", "C"],
    uncovered: ["C"],
    weak: [],
    stale: [],
    ...over,
  });

  it("counts covered as allocated minus uncovered", () => {
    const figure = containerFigure(view(), new Set());
    expect(figure.total).toBe(2);
    expect(figure.outOf).toBe(3);
  });

  it("counts the auto-accepted share from the marked ids, not from a difference", () => {
    // A difference between totals would silently absorb any other reason a
    // count might not match.
    const figure = containerFigure(view(), new Set(["A"]));
    expect(figure.autoAccepted).toBe(1);
  });

  it("ignores an auto-accepted id that is not covered", () => {
    // "C" is uncovered, so it contributes to neither number.
    const figure = containerFigure(view(), new Set(["C"]));
    expect(figure.total).toBe(2);
    expect(figure.autoAccepted).toBe(0);
  });

  it("reports nothing covered when everything is outstanding", () => {
    const figure = containerFigure(view({ uncovered: ["A", "B", "C"] }), new Set(["A"]));
    expect(figure.total).toBe(0);
    expect(figure.autoAccepted).toBe(0);
    expect(autoAcceptedShare(figure)).toBe(0);
  });

  it("handles a container with no allocations at all", () => {
    const figure = containerFigure(view({ allocated: [], uncovered: [] }), new Set());
    expect(figure).toEqual({ total: 0, autoAccepted: 0, outOf: 0 });
  });
});
