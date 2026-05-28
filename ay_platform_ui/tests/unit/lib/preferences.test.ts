// =============================================================================
// File: preferences.test.ts
// Version: 1
// Path: ay_platform_ui/tests/unit/lib/preferences.test.ts
// Description: Unit tests for the user-scoped preferences store. Focuses
//              on the working-area pane-width persistence (#6) :
//              round-trip, per-user scoping, and merge with existing
//              prefs (no clobber). localStorage is reset between tests
//              by the global setup.
// =============================================================================

import { describe, expect, it } from "vitest";

import { readPreferences, writePreferences } from "@/lib/preferences";

describe("workingAreaPaneWidths preference (#6)", () => {
  it("round-trips the pane widths for a user", () => {
    writePreferences("user-1", { workingAreaPaneWidths: { left: 300, right: 400 } });
    expect(readPreferences("user-1").workingAreaPaneWidths).toEqual({ left: 300, right: 400 });
  });

  it("is scoped per user (another sub sees nothing)", () => {
    writePreferences("user-1", { workingAreaPaneWidths: { left: 300, right: 400 } });
    expect(readPreferences("user-2").workingAreaPaneWidths).toBeUndefined();
  });

  it("merges with existing prefs without clobbering the trigram", () => {
    writePreferences("user-1", { trigram: "ABC" });
    writePreferences("user-1", { workingAreaPaneWidths: { left: 280, right: 360 } });
    const prefs = readPreferences("user-1");
    expect(prefs.trigram).toBe("ABC");
    expect(prefs.workingAreaPaneWidths).toEqual({ left: 280, right: 360 });
  });

  it("returns an empty object when nothing is stored", () => {
    expect(readPreferences("nobody")).toEqual({});
  });
});
