// =============================================================================
// File: project-index-redirect.test.tsx
// Path: ay_platform_ui/tests/integration/project-index-redirect.test.tsx
// Description: Tests for the project index route — it redirects to the
//              default `/overview` section on mount and renders a loading
//              placeholder meanwhile.
// =============================================================================

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ProjectIndexRedirect from "@/app/(protected)/projects/[pid]/page";

const { mockRouter } = vi.hoisted(() => ({
  mockRouter: {
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1" }),
  useRouter: () => mockRouter,
}));

describe("ProjectIndexRedirect", () => {
  it("replaces the URL with the project's overview section", () => {
    render(<ProjectIndexRedirect />);
    expect(mockRouter.replace).toHaveBeenCalledWith("/projects/p1/overview");
    expect(screen.getByText(/Loading project overview/)).toBeInTheDocument();
  });
});
