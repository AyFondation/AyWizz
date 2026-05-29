// =============================================================================
// File: profiles.test.ts
// Version: 1
// Path: ay_platform_ui/tests/unit/lib/profiles.test.ts
// Description: Unit tests for the profile registry + the shipped profile
//              definitions (code, docgen). Covers :
//                - resolveProfile() returns the matching definition and
//                  null (NOT a silent fallback) for unknown ids ;
//                - listKnownProfiles() enumerates every registered id ;
//                - each profile's invariant shape : unique section ids,
//                  the first section is the default landing, and the
//                  documented section layout per profile.
// =============================================================================

import { describe, expect, it } from "vitest";

import { CODE_PROFILE } from "@/lib/profiles/code";
import { DOCGEN_PROFILE } from "@/lib/profiles/docgen";
import { listKnownProfiles, resolveProfile } from "@/lib/profiles/registry";

describe("resolveProfile", () => {
  it("returns the code profile for the 'code' id", () => {
    expect(resolveProfile("code")).toBe(CODE_PROFILE);
  });

  it("returns the docgen profile for the 'docgen' id", () => {
    expect(resolveProfile("docgen")).toBe(DOCGEN_PROFILE);
  });

  it("returns null for an unknown id (no silent fallback)", () => {
    expect(resolveProfile("nope")).toBeNull();
    expect(resolveProfile("")).toBeNull();
  });
});

describe("listKnownProfiles", () => {
  it("enumerates every registered profile as {id, label}", () => {
    const known = listKnownProfiles();
    const ids = known.map((p) => p.id).sort();
    expect(ids).toEqual(["code", "docgen"]);
    // labels are carried through, not just ids
    expect(known.find((p) => p.id === "code")?.label).toBe("Code");
    expect(known.find((p) => p.id === "docgen")?.label).toBe("DocGen");
  });
});

describe("profile definitions", () => {
  for (const profile of [CODE_PROFILE, DOCGEN_PROFILE]) {
    describe(`${profile.id} profile`, () => {
      it("has a stable id, label and tagline", () => {
        expect(profile.id).toBeTruthy();
        expect(profile.label).toBeTruthy();
        expect(profile.tagline).toBeTruthy();
      });

      it("declares at least one section with unique ids", () => {
        expect(profile.sections.length).toBeGreaterThan(0);
        const ids = profile.sections.map((s) => s.id);
        expect(new Set(ids).size).toBe(ids.length);
      });

      it("lands on 'overview' first (default section)", () => {
        expect(profile.sections[0].id).toBe("overview");
      });
    });
  }

  it("code profile exposes the documented sections in order", () => {
    expect(CODE_PROFILE.sections.map((s) => s.id)).toEqual([
      "overview",
      "sources",
      "conversations",
      "requirements",
      "pipeline",
      "validation",
      "artifacts",
      "settings",
    ]);
  });

  it("docgen profile drops code-only sections and adds the working area", () => {
    const ids = DOCGEN_PROFILE.sections.map((s) => s.id);
    expect(ids).toContain("working-area");
    expect(ids).not.toContain("pipeline");
    expect(ids).not.toContain("validation");
    expect(ids).not.toContain("requirements");
  });

  it("docgen reuses the artifacts route for its Documents section", () => {
    const documents = DOCGEN_PROFILE.sections.find((s) => s.id === "documents");
    expect(documents?.path).toBe("artifacts");
    expect(documents?.label).toBe("Documents");
  });
});
