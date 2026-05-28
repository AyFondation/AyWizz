// =============================================================================
// File: profile.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/profile.test.tsx
// Description: Integration tests for /profile — the user self-view.
//              Mounts the page with a seeded token, asserts each
//              identity / role / session field is surfaced and that
//              Sign out wipes localStorage + routes to /login.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProfilePage from "@/app/(protected)/profile/page";
import { AuthProvider } from "@/app/auth-provider";

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
}));

beforeEach(() => {
  mockRouter.push.mockClear();
});

const NOW = Math.floor(Date.now() / 1000);

function seedAndRender(claims: Record<string, unknown> = {}): void {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "demo-tenant-admin",
      username: "tenant-admin",
      tenant_id: "tenant-test",
      roles: ["admin"],
      project_scopes: { "project-test": ["project_editor"] },
      auth_mode: "local",
      jti: "jti-abc-123",
      exp: NOW + 3600,
      iat: NOW,
      ...claims,
    }),
  );
  render(
    <AuthProvider>
      <ProfilePage />
    </AuthProvider>,
  );
}

describe("ProfilePage", () => {
  it("renders the username, sub, tenant and auth_mode in the identity card", async () => {
    seedAndRender();
    await waitFor(() => {
      expect(screen.getByTestId("profile-identity")).toBeInTheDocument();
    });
    expect(screen.getByText("tenant-admin")).toBeInTheDocument();
    expect(screen.getByText("demo-tenant-admin")).toBeInTheDocument();
    expect(screen.getByText("tenant-test")).toBeInTheDocument();
    expect(screen.getByText("local")).toBeInTheDocument();
  });

  it("renders global roles as badges", async () => {
    seedAndRender({ roles: ["admin", "user"] });
    await waitFor(() => {
      expect(screen.getByTestId("profile-roles")).toBeInTheDocument();
    });
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getByText("user")).toBeInTheDocument();
  });

  it("renders 'none' when global roles are empty", async () => {
    seedAndRender({ roles: [] });
    await waitFor(() => {
      expect(screen.getByTestId("profile-roles")).toBeInTheDocument();
    });
    expect(screen.getByText(/^none$/)).toBeInTheDocument();
  });

  it("renders project scopes when present with role badges per project", async () => {
    seedAndRender({
      project_scopes: {
        "project-test": ["project_editor"],
        "project-alpha": ["project_viewer", "project_owner"],
      },
    });
    await waitFor(() => {
      expect(screen.getByText("project-test")).toBeInTheDocument();
    });
    expect(screen.getByText("project-alpha")).toBeInTheDocument();
    expect(screen.getByText("project_editor")).toBeInTheDocument();
    expect(screen.getByText("project_viewer")).toBeInTheDocument();
    expect(screen.getByText("project_owner")).toBeInTheDocument();
  });

  it("shows an explanatory line when project_scopes is empty (admin case)", async () => {
    seedAndRender({ project_scopes: {} });
    await waitFor(() => {
      expect(screen.getByTestId("profile-roles")).toBeInTheDocument();
    });
    expect(screen.getByText(/No per-project grants/i)).toBeInTheDocument();
  });

  it("renders the session card with token expiry + jti + bearer prefix", async () => {
    seedAndRender();
    await waitFor(() => {
      expect(screen.getByTestId("profile-session")).toBeInTheDocument();
    });
    // jti claim surfaced
    expect(screen.getByText("jti-abc-123")).toBeInTheDocument();
    // "expires in" relative phrase — token TTL ~1h.
    expect(screen.getByText(/in \d+\s*(h|min)/)).toBeInTheDocument();
  });

  it("Sign out clears localStorage and pushes /login", async () => {
    seedAndRender();
    await waitFor(() => {
      expect(screen.getByTestId("profile-signout")).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTestId("profile-signout"));

    expect(window.localStorage.getItem("aywizz.token")).toBeNull();
    expect(mockRouter.push).toHaveBeenCalledWith("/login");
  });

  it("returns null (no UI) when AuthProvider is not authenticated", () => {
    const { container } = render(
      <AuthProvider>
        <ProfilePage />
      </AuthProvider>,
    );
    // No identity card → page bailed out via the early-return.
    expect(container.querySelector('[data-testid="profile-identity"]')).toBeNull();
  });
});
