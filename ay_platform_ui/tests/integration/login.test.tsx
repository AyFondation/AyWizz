// =============================================================================
// File: login.test.tsx
// Version: 3
// Path: ay_platform_ui/tests/integration/login.test.tsx
// Description: Integration tests for the /login form. Exercises the
//              full chain : config bootstrap → form input → ApiClient
//              login → AuthProvider.setToken → router.push(<target>).
//              Failure paths : 401 with bad creds, network error.
//
//              v3 (2026-04-29) : adds the dev-credentials panel
//              suite — when `/ux/config` returns a `dev_credentials`
//              array, the login page renders an auto-fill panel ;
//              clicking a row populates the form ; submit then
//              completes the normal flow.
//
//              v2 (2026-04-29) : verifies the `?redirect=<path>`
//              feature — the destination after sign-in is the
//              decoded redirect param (sanitised) when present, or
//              `/dashboard` as the default. External / malformed
//              redirects are rejected (open-redirect defence).
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/app/auth-provider";
import LoginPage from "@/app/login/page";
import { ConfigProvider } from "@/app/providers";

import { server } from "../helpers/msw-server";

// `vi.hoisted` pulls the shared mocks ABOVE the vi.mock factory so the
// closure resolves correctly at module-load time. `mockNav.search`
// drives `useSearchParams()` — tests mutate it before render to
// simulate `/login?redirect=<path>`.
const { mockRouter, mockNav } = vi.hoisted(() => ({
  mockRouter: {
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  },
  mockNav: {
    search: new URLSearchParams(),
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useSearchParams: () => mockNav.search,
}));

beforeEach(() => {
  // Reset the router spies + nav state between tests so call counts
  // are accurate and prior `?redirect=` values don't leak.
  mockRouter.push.mockClear();
  mockRouter.replace.mockClear();
  mockNav.search = new URLSearchParams();
});

function renderLogin() {
  return render(
    <ConfigProvider>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </ConfigProvider>,
  );
}

describe("LoginPage — config gating", () => {
  it("shows a loading state while config is being fetched", () => {
    // The MSW default handlers respond synchronously in the next
    // microtask ; on initial render the config state is still
    // `loading`.
    renderLogin();
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders the form once config is ready, displaying auth_mode", async () => {
    renderLogin();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    });
    // Default MSW handler returns auth_mode="local".
    expect(screen.getByText("local")).toBeInTheDocument();
  });
});

describe("LoginPage — successful login", () => {
  it("posts to /auth/login, stores the token, and redirects to /dashboard", async () => {
    renderLogin();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/username/i), "alice");
    await user.type(screen.getByLabelText(/password/i), "any-pw");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/dashboard");
    });
    // Token persisted by AuthProvider.setToken.
    expect(window.localStorage.getItem("aywizz.token")).not.toBeNull();
  });
});

describe("LoginPage — failure paths", () => {
  it("displays 'Invalid credentials' on 401 without redirecting", async () => {
    renderLogin();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    // The MSW default handler returns 401 for any username != "alice".
    await user.type(screen.getByLabelText(/username/i), "bob");
    await user.type(screen.getByLabelText(/password/i), "wrong");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/invalid credentials/i);
    });
    expect(mockRouter.push).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("aywizz.token")).toBeNull();
  });

  it("displays a generic error on 503 server error", async () => {
    server.use(
      http.post("/auth/login", () =>
        HttpResponse.json({ detail: "service unavailable" }, { status: 503 }),
      ),
    );

    renderLogin();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/username/i), "alice");
    await user.type(screen.getByLabelText(/password/i), "pw");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 503/);
    });
    expect(mockRouter.push).not.toHaveBeenCalled();
  });

  it("displays a network-error message when fetch fails outright", async () => {
    server.use(http.post("/auth/login", () => HttpResponse.error()));

    renderLogin();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/username/i), "alice");
    await user.type(screen.getByLabelText(/password/i), "pw");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/network error/i);
    });
  });
});

describe("LoginPage — already-authenticated redirect", () => {
  it("redirects to /dashboard when the user lands here while logged in (no ?redirect=)", async () => {
    // Seed a valid token so AuthProvider hydrates as authenticated.
    const NOW = Math.floor(Date.now() / 1000);
    window.localStorage.setItem(
      "aywizz.token",
      // Reuse the helper from msw-handlers — keeps token shape consistent.
      // Inline-imported here to avoid a circular dep header.
      // eslint-disable-next-line — we intentionally keep this inline.
      // We can also just construct manually :
      "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
        Buffer.from(
          JSON.stringify({
            sub: "user-alice",
            username: "alice",
            exp: NOW + 3600,
            iat: NOW,
          }),
        )
          .toString("base64")
          .replace(/\+/g, "-")
          .replace(/\//g, "_")
          .replace(/=+$/, "") +
        ".fake-sig",
    );

    renderLogin();

    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("honours a sanitised ?redirect= when already authenticated", async () => {
    mockNav.search = new URLSearchParams("redirect=/projects/abc");
    const NOW = Math.floor(Date.now() / 1000);
    window.localStorage.setItem(
      "aywizz.token",
      "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
        Buffer.from(
          JSON.stringify({
            sub: "user-alice",
            exp: NOW + 3600,
            iat: NOW,
          }),
        )
          .toString("base64")
          .replace(/\+/g, "-")
          .replace(/\//g, "_")
          .replace(/=+$/, "") +
        ".fake-sig",
    );

    renderLogin();

    await waitFor(() => {
      expect(mockRouter.replace).toHaveBeenCalledWith("/projects/abc");
    });
  });
});

describe("LoginPage — redirect target after successful login", () => {
  it("uses the sanitised ?redirect= path as the post-login destination", async () => {
    mockNav.search = new URLSearchParams("redirect=/projects/abc?tab=members");
    renderLogin();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/username/i), "alice");
    await user.type(screen.getByLabelText(/password/i), "any-pw");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/projects/abc?tab=members");
    });
  });

  it("falls back to /dashboard when ?redirect= points to an external URL", async () => {
    // sanitizeRedirect rejects `//evil.com` — defence-in-depth against
    // open-redirect attacks via crafted query strings.
    mockNav.search = new URLSearchParams("redirect=//evil.com/phish");
    renderLogin();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/username/i), "alice");
    await user.type(screen.getByLabelText(/password/i), "pw");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/dashboard");
    });
    expect(mockRouter.push).not.toHaveBeenCalledWith(expect.stringContaining("evil.com"));
  });

  it("falls back to /dashboard when ?redirect= is absent", async () => {
    renderLogin();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/username/i), "alice");
    await user.type(screen.getByLabelText(/password/i), "pw");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/dashboard");
    });
  });
});

describe("LoginPage — dev credentials panel", () => {
  // The default MSW handler omits `dev_credentials` ; this suite
  // overrides /ux/config to return a populated array, then asserts
  // the panel renders + auto-fills correctly.
  const UX_CONFIG_WITH_DEV_CREDS = {
    api_version: "v1",
    auth_mode: "local" as const,
    brand: {
      name: "AyWizz Platform",
      short_name: "AyWizz",
      accent_color_hex: "#3b82f6",
    },
    features: {
      chat_enabled: true,
      kg_enabled: true,
      cross_tenant_enabled: false,
      file_download_enabled: true,
    },
    dev_credentials: [
      {
        username: "superroot",
        password: "dev-superroot",
        role_label: "super-root (tenant_manager)",
        note: "Content-blind: lifecycle ops only.",
      },
      {
        username: "tenant-admin",
        password: "dev-tenant",
        role_label: "tenant admin",
        note: "Admin of tenant 'tenant-test'.",
      },
      {
        username: "project-editor",
        password: "dev-editor",
        role_label: "project editor (read/write)",
        note: null,
      },
    ],
  };

  it("does NOT render the panel when /ux/config has no dev_credentials", async () => {
    renderLogin();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    });
    expect(screen.queryByTestId("dev-credentials-panel")).toBeNull();
  });

  it("renders the panel with one button per credential when /ux/config exposes them", async () => {
    server.use(http.get("/ux/config", () => HttpResponse.json(UX_CONFIG_WITH_DEV_CREDS)));

    renderLogin();
    await waitFor(() => {
      expect(screen.getByTestId("dev-credentials-panel")).toBeInTheDocument();
    });
    // Heading visible — banner + dev-only warning.
    expect(screen.getByText(/dev only/i)).toBeInTheDocument();
    // Three rows, each with an explicit data-testid for clicks.
    expect(screen.getByTestId("dev-credential-superroot")).toBeInTheDocument();
    expect(screen.getByTestId("dev-credential-tenant-admin")).toBeInTheDocument();
    expect(screen.getByTestId("dev-credential-project-editor")).toBeInTheDocument();
    // Role labels surface for orientation.
    expect(screen.getByText(/super-root \(tenant_manager\)/)).toBeInTheDocument();
    expect(screen.getByText(/project editor \(read\/write\)/)).toBeInTheDocument();
    // Optional note rendered when present, omitted when null.
    expect(screen.getByText(/content-blind/i)).toBeInTheDocument();
  });

  it("auto-fills username + password when a credential row is clicked", async () => {
    server.use(http.get("/ux/config", () => HttpResponse.json(UX_CONFIG_WITH_DEV_CREDS)));

    renderLogin();
    await waitFor(() => {
      expect(screen.getByTestId("dev-credentials-panel")).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTestId("dev-credential-tenant-admin"));

    // Inputs SHALL hold the seeded credential pair.
    expect(screen.getByLabelText(/username/i)).toHaveValue("tenant-admin");
    expect(screen.getByLabelText(/password/i)).toHaveValue("dev-tenant");
  });

  it("auto-filled credentials submit cleanly through the normal login flow", async () => {
    // The default /auth/login mock accepts username "alice" only ; we
    // override to also accept the demo username so the flow round-
    // trips through ApiClient → setToken → router.push.
    server.use(
      http.get("/ux/config", () => HttpResponse.json(UX_CONFIG_WITH_DEV_CREDS)),
      http.post("/auth/login", async ({ request }) => {
        const body = (await request.json()) as { username: string; password: string };
        if (body.username === "tenant-admin" && body.password === "dev-tenant") {
          const NOW = Math.floor(Date.now() / 1000);
          const token =
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
            Buffer.from(
              JSON.stringify({
                sub: "demo-tenant-admin",
                username: "tenant-admin",
                tenant_id: "tenant-test",
                roles: ["admin"],
                exp: NOW + 3600,
                iat: NOW,
              }),
            )
              .toString("base64")
              .replace(/\+/g, "-")
              .replace(/\//g, "_")
              .replace(/=+$/, "") +
            ".fake-sig";
          return HttpResponse.json({
            access_token: token,
            token_type: "bearer",
            expires_in: 3600,
          });
        }
        return HttpResponse.json({ detail: "invalid" }, { status: 401 });
      }),
    );

    renderLogin();
    await waitFor(() => {
      expect(screen.getByTestId("dev-credentials-panel")).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTestId("dev-credential-tenant-admin"));
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(mockRouter.push).toHaveBeenCalledWith("/dashboard");
    });
  });
});
