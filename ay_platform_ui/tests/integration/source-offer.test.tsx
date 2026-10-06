// =============================================================================
// File: source-offer.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/source-offer.test.tsx
// Description: Tests for <SourceOffer> — the AGPL §13 network source offer.
//
//              WHY THESE TESTS ARE NOT OPTIONAL. §13 is the clause that
//              makes the platform's AGPL-3.0-or-later licence reach a HOSTED
//              deployment: a running service must offer every network user
//              an opportunity to receive the Corresponding Source. Shipping
//              a LICENSE file does not discharge it. So this link is a
//              licence obligation wearing the costume of a UI element, and
//              it is exactly the kind of element a later layout refactor
//              deletes without noticing.
//
//              Two properties are pinned: the link itself, and its
//              PLACEMENT in the root layout outside the providers — because
//              the offer has to reach UNAUTHENTICATED users on /login, and
//              anything behind the config bootstrap renders nothing there.
// =============================================================================

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SOURCE_URL, SourceOffer } from "@/components/source-offer";

describe("SourceOffer — AGPL §13 source offer", () => {
  it("renders a link to the Corresponding Source", () => {
    render(<SourceOffer />);
    const link = screen.getByTestId("agpl-source-offer");
    expect(link).toBeVisible();
    expect(link).toHaveAttribute("href", SOURCE_URL);
    expect(link.tagName).toBe("A");
  });

  it("names the licence, so the offer is recognisable as one", () => {
    render(<SourceOffer />);
    // A bare "Source" link reads as documentation; §13 asks for an offer a
    // user can recognise as their right to the source of THIS service.
    expect(screen.getByTestId("agpl-source-offer")).toHaveTextContent(/AGPL/);
  });

  it("opens the source without leaking the referrer or the window handle", () => {
    render(<SourceOffer />);
    const link = screen.getByTestId("agpl-source-offer");
    expect(link).toHaveAttribute("target", "_blank");
    // `noopener` matters on a `target=_blank` link: without it the opened
    // page gets a handle on this one. `license` is the semantically correct
    // rel for a link to the terms the work is under.
    const rel = link.getAttribute("rel") ?? "";
    expect(rel).toContain("noopener");
    expect(rel).toContain("noreferrer");
    expect(rel).toContain("license");
  });

  it("defaults to a real absolute URL", () => {
    // A relative or empty href would render an offer that leads nowhere —
    // worse than no offer, because it looks discharged.
    expect(SOURCE_URL).toMatch(/^https?:\/\/\S+$/);
  });
});

describe("SourceOffer placement in the root layout", () => {
  // Read as TEXT rather than rendered: a Next.js root layout returns
  // <html><body>, which RTL cannot mount, and the property under test is
  // structural — WHERE the element sits relative to the providers.
  const layout = readFileSync(resolve(__dirname, "../../app/layout.tsx"), "utf-8");

  it("is rendered by the root layout", () => {
    expect(layout).toContain("<SourceOffer />");
    expect(layout).toContain('from "../components/source-offer"');
  });

  it("sits OUTSIDE ConfigProvider, so /login shows it too", () => {
    // The offer must reach unauthenticated users. `<BuildStamp>` returns
    // null until the config bootstrap is `ready`, so anything nested inside
    // the providers renders nothing on the login page — which is precisely
    // where a network user who has not signed in arrives.
    const closeConfig = layout.indexOf("</ConfigProvider>");
    const offer = layout.indexOf("<SourceOffer />");
    expect(closeConfig).toBeGreaterThan(-1);
    expect(offer).toBeGreaterThan(-1);
    expect(
      offer,
      "SourceOffer moved inside ConfigProvider — it would stop rendering " +
        "for unauthenticated users on /login, which is the population AGPL " +
        "§13 is about",
    ).toBeGreaterThan(closeConfig);
  });
});
