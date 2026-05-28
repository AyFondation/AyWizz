// =============================================================================
// File: protected-layout.test.tsx
// Version: 3
// Path: ay_platform_ui/tests/integration/protected-layout.test.tsx
// Description: Integration tests for app/(protected)/layout.tsx — the
//              auth gate. Three states :
//                - loading      : shows placeholder, NO redirect.
//                - anonymous    : redirects to /login (uses
//                                 router.replace, not push, so back-
//                                 button doesn't bounce).
//                - authenticated: renders Navbar + children.
//
//              Also covers the dashboard rendering through the layout
//              (since the layout's "authenticated" state IS the
//              dashboard's prerequisite).
//
//              v3 (2026-04-29) : the "loading" branch test now mocks
//              useAuth + useConfigState directly. AuthProvider's
//              hydration useEffect runs synchronously inside RTL's
//              act(), so the transient loading state of the real
//              provider is never observable from the test's
//              assertion phase. Mocking the hooks lets us pin the
//              layout in "loading" and assert the placeholder +
//              absence of redirect deterministically.
//
//              v2 (2026-04-29) : verifies the `?redirect=<path>`
//              query string the layout appends to /login so the user
//              lands back on their original page after re-auth.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProtectedLayout from "@/app/(protected)/layout";
import { AuthProvider } from "@/app/auth-provider";
import { ConfigProvider } from "@/app/providers";

import { fakeJWT } from "../helpers/msw-handlers";

const { mockRouter, mockNav } = vi.hoisted(() => ({
  mockRouter: {
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  },
  // Mutable holders so each test can set the path it pretends to be
  // navigating from. usePathname / useSearchParams read these on
  // every render.
  mockNav: {
    pathname: "/dashboard",
    search: new URLSearchParams(),
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => mockNav.pathname,
  useSearchParams: () => mockNav.search,
}));

beforeEach(() => {
  mockRouter.push.mockClear();
  mockRouter.replace.mockClear();
  mockNav.pathname = "/dashboard";
  mockNav.search = new URLSearchParams();
});

const NOW = Math.floor(Date.now() / 1000);

function renderProtected(child: React.ReactNode = <div data-testid="child">child-content</div>) {
  return render(
    <ConfigProvider>
      <AuthProvider>
        <ProtectedLayout>{child}</ProtectedLayout>
      </AuthProvider>
    </ConfigProvider>,
  );
}

describe("ProtectedLayout — anonymous", () => {
  it("redirects to /login carrying the current pathname as ?redirect=", async () => {
    // No token seeded → anonymous after hydration.
    mockNav.pathname = "/dashboard";
    renderProtected();

    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledWith("/login?redirect=%2Fdashboard");
    });
    // Child SHALL NOT have rendered.
    expect(screen.queryByTestId("child")).toBeNull();
  });

  it("preserves query string when redirecting (URL-encoded)", async () => {
    mockNav.pathname = "/projects/abc";
    mockNav.search = new URLSearchParams("tab=members&filter=active");
    renderProtected();

    await waitFor(() => {
      // %2F=/  %3F=?  %3D=  %26=&
      expect(mockRouter.replace).toHaveBeenCalledWith(
        "/login?redirect=%2Fprojects%2Fabc%3Ftab%3Dmembers%26filter%3Dactive",
      );
    });
  });

  it("uses router.replace (not push) so the login page replaces history", async () => {
    renderProtected();
    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledTimes(1);
    });
    expect(mockRouter.push).not.toHaveBeenCalled();
  });
});

describe("ProtectedLayout — authenticated", () => {
  it("renders Navbar + child content when a valid token is present", async () => {
    window.localStorage.setItem(
      "aywizz.token",
      fakeJWT({
        sub: "user-alice",
        username: "alice",
        exp: NOW + 3600,
        iat: NOW,
      }),
    );
    renderProtected();

    await waitFor(() => {
      expect(screen.getByTestId("child")).toBeInTheDocument();
    });
    // Navbar present (looks for the Sign out button — uniquely
    // identifies the navbar without coupling the test to brand text).
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
    // No redirect SHALL fire for an authenticated user.
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });
});

describe("ProtectedLayout — loading", () => {
  // The transient loading state of the real AuthProvider is flushed
  // synchronously inside RTL's render() (its hydration useEffect runs
  // in the act() that wraps render). To validate the loading branch
  // deterministically, we mock the hooks the layout consumes and
  // bypass the providers entirely. This is a focused branch test —
  // the integration coverage of the providers themselves lives in
  // auth-provider.test.tsx.
  it("shows a placeholder when either auth or config is still loading, without redirecting", async () => {
    vi.resetModules();
    vi.doMock("@/app/auth-provider", () => ({
      AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
      useAuth: () => ({
        state: { status: "loading" },
        setToken: () => {},
        clearAuth: () => {},
      }),
    }));
    vi.doMock("@/app/providers", () => ({
      ConfigProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
      useConfigState: () => ({ status: "loading" }),
      useReadyConfig: () => {
        throw new Error("not ready");
      },
    }));

    const { default: LayoutUnderTest } = await import("@/app/(protected)/layout");

    render(<LayoutUnderTest>{<div data-testid="child">child</div>}</LayoutUnderTest>);

    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    // Critically : NO redirect while either bootstrap is pending.
    expect(mockRouter.replace).not.toHaveBeenCalled();
    // Children NOT rendered yet.
    expect(screen.queryByTestId("child")).toBeNull();

    vi.doUnmock("@/app/auth-provider");
    vi.doUnmock("@/app/providers");
  });
});
