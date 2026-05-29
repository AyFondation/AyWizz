// =============================================================================
// File: overview.test.tsx
// Path: ay_platform_ui/tests/integration/overview.test.tsx
// Description: Tests for the project Overview page — a static quick-links
//              grid derived from the code profile's sections (minus
//              "overview" itself). No API ; only useParams + <Link>.
// =============================================================================

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import OverviewPage from "@/app/(protected)/projects/[pid]/overview/page";
import { CODE_PROFILE } from "@/lib/profiles/code";

vi.mock("next/navigation", () => ({ useParams: () => ({ pid: "p1" }) }));
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

describe("OverviewPage", () => {
  it("renders a quick link for every code-profile section except overview", () => {
    render(<OverviewPage />);
    expect(screen.getByTestId("overview-quicklinks")).toBeInTheDocument();

    const expected = CODE_PROFILE.sections.filter((s) => s.id !== "overview");
    for (const s of expected) {
      const link = screen.getByTestId(`overview-link-${s.id}`);
      expect(link).toBeInTheDocument();
      expect(link).toHaveAttribute("href", `/projects/p1/${s.path}`);
    }
    // overview itself is excluded
    expect(screen.queryByTestId("overview-link-overview")).not.toBeInTheDocument();
  });
});
