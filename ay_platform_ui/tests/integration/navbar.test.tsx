// =============================================================================
// File: navbar.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/navbar.test.tsx
// Description: Integration tests for <Navbar>. Brand from ConfigProvider,
//              user info from AuthProvider claims, logout button
//              clears auth + redirects.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/app/auth-provider";
import { ConfigProvider } from "@/app/providers";
import { Navbar } from "@/components/navbar";

import { fakeJWT } from "../helpers/msw-handlers";

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
  useRouter: () => mockRouter,
  // Navbar v3 reads usePathname to mark the active nav item ;
  // tests don't care about active state, so a stable "/" suffices.
  usePathname: () => "/",
}));

beforeEach(() => {
  mockRouter.push.mockClear();
});

const NOW = Math.floor(Date.now() / 1000);

function seedAuthenticated(claims: Record<string, unknown> = {}): void {
  const token = fakeJWT({
    sub: "user-alice",
    username: "alice",
    tenant_id: "tenant-x",
    roles: ["project_editor"],
    exp: NOW + 3600,
    iat: NOW,
    ...claims,
  });
  window.localStorage.setItem("aywizz.token", token);
}

function renderNavbar() {
  return render(
    <ConfigProvider>
      <AuthProvider>
        <Navbar />
      </AuthProvider>
    </ConfigProvider>,
  );
}

describe("Navbar rendering", () => {
  it("renders nothing while AuthProvider is not authenticated", async () => {
    // No token seeded → AuthProvider hydrates as anonymous → navbar
    // returns null per its defensive guard.
    const { container } = renderNavbar();
    // Wait long enough for any potential async render.
    await waitFor(() => {
      // Should NOT contain a header element.
      expect(container.querySelector("header")).toBeNull();
    });
  });

  it("renders brand short_name from ConfigProvider once authenticated", async () => {
    seedAuthenticated();
    renderNavbar();

    await waitFor(() => {
      expect(screen.getByText("AyWizz")).toBeInTheDocument();
    });
    // Brand link points at the projects list (post-Phase A landing).
    const link = screen.getByRole("link", { name: /aywizz/i });
    expect(link).toHaveAttribute("href", "/projects");
  });

  it("renders the user trigram avatar with full-name tooltip", async () => {
    // Navbar v5 : the user area is now a 3-letter trigram avatar
    // with the full identity in the `title` attribute (tooltip). The
    // detailed username + tenant + roles line lives in /profile.
    seedAuthenticated({
      username: "platform-admin",
      tenant_id: "acme-prod",
      roles: ["tenant_manager", "admin"],
    });
    renderNavbar();

    await waitFor(() => {
      // Default trigram derivation from username "platform-admin" =
      // first 3 chars uppercase = "PLA".
      expect(screen.getByText("PLA")).toBeInTheDocument();
    });
    // The link's aria-label carries the full identity for screen
    // readers ; sighted users see the tooltip via `title`.
    const link = screen.getByTestId("navbar-link-profile");
    expect(link).toHaveAttribute("aria-label", expect.stringContaining("platform-admin"));
    expect(link).toHaveAttribute("href", "/preferences");
  });

  it("falls back to sub-derived trigram when username is absent", async () => {
    seedAuthenticated({ username: undefined });
    renderNavbar();

    await waitFor(() => {
      // sub is "user-alice" → first 3 chars uppercase = "USE".
      expect(screen.getByText("USE")).toBeInTheDocument();
    });
  });
});

describe("Navbar logout", () => {
  it("clears localStorage AND redirects to /login on Sign out click", async () => {
    seedAuthenticated();
    renderNavbar();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /sign out/i }));

    expect(window.localStorage.getItem("aywizz.token")).toBeNull();
    expect(mockRouter.push).toHaveBeenCalledWith("/login");
  });
});
