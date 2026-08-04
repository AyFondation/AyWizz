// =============================================================================
// File: operator-projects.test.tsx
// Path: ay_platform_ui/tests/integration/operator-projects.test.tsx
// Description: Tests for the project governance console (operator — E-100-002
//              v7). Covers: forbidden for a baseline user; cross-tenant list
//              with lifecycle status; deactivate / archive / activate; ACL
//              expand / grant / deactivate-access; per-project cost columns +
//              6-window cost table (Inc B); disk-storage column + series (Inc C).
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProjectsGovernancePage from "@/app/(protected)/operator/projects/page";
import { AuthProvider } from "@/app/auth-provider";
import { fakeJWT } from "../helpers/msw-handlers";
import { server } from "../helpers/msw-server";

const READY_CONFIG = {
  runtime: { apiBaseUrl: "", publicBaseUrl: "" },
  ux: {
    api_version: "v1",
    auth_mode: "local",
    brand: { name: "AyWizz", short_name: "AY", accent_color_hex: "#000" },
    features: {
      chat_enabled: true,
      kg_enabled: true,
      cross_tenant_enabled: false,
      file_download_enabled: true,
    },
  },
};

vi.mock("@/app/providers", () => ({ useReadyConfig: () => READY_CONFIG }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

const PROJECTS_URL = "/admin/projects";
const COST_URL = "/admin/v1/quota/consumption/projects";
const STORAGE_URL = "/admin/v1/storage/projects";

function emptyCost() {
  return {
    currency: "EUR",
    windows: ["day", "week", "month", "quarter", "semester", "year"],
    tenant_id: null,
    projects: [],
  };
}

function project(over: Partial<Record<string, unknown>> = {}) {
  return {
    project_id: "proj-alpha",
    tenant_id: "t-acme",
    name: "Alpha",
    profile: "code",
    created_at: "2026-07-01T00:00:00+00:00",
    created_by: "system",
    status: "active",
    system_prompt: "",
    system_prompt_is_default: true,
    ...over,
  };
}

function seedToken(roles: string[]) {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "u1",
      username: "root",
      tenant_id: "t-platform",
      roles,
      exp: Math.floor(Date.now() / 1000) + 3600,
      iat: Math.floor(Date.now() / 1000),
    }),
  );
}

function renderPage() {
  return render(
    <AuthProvider>
      <ProjectsGovernancePage />
    </AuthProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  // Default cost/storage handlers so reload() never hits an unhandled request.
  // Tests that assert on cost/storage override these.
  server.use(
    http.get(COST_URL, () => HttpResponse.json(emptyCost())),
    http.get(STORAGE_URL, () =>
      HttpResponse.json({
        tenant_id: null,
        measured_at: "2026-07-29T00:00:00+00:00",
        projects: [],
      }),
    ),
    http.get(`${STORAGE_URL}/:pid/series`, () =>
      HttpResponse.json({
        project_id: "proj-alpha",
        tenant_id: "t-acme",
        window: "month",
        current_bytes: 0,
        points: [],
      }),
    ),
  );
});

describe("ProjectsGovernancePage", () => {
  it("forbids a non-operator (baseline user)", async () => {
    // E-100-002 v7: project governance is an operator surface. `admin` (tenant
    // operator) is now allowed (scoped); a baseline `user` is refused.
    seedToken(["user"]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId("projects-forbidden")).toBeInTheDocument());
  });

  it("lists projects across tenants with lifecycle status", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(PROJECTS_URL, () =>
        HttpResponse.json({
          items: [
            project(),
            project({ project_id: "proj-beta", tenant_id: "t-globex", status: "archived" }),
          ],
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("projects-table")).toBeInTheDocument());
    expect(screen.getByTestId("project-status-proj-alpha")).toHaveTextContent("active");
    expect(screen.getByTestId("project-status-proj-beta")).toHaveTextContent("archived");
    expect(screen.getByTestId("project-row-proj-beta")).toHaveTextContent("t-globex");
  });

  it("deactivates an active project", async () => {
    seedToken(["platform_manager"]);
    const deact = vi.fn(() => HttpResponse.json(project({ status: "inactive" })));
    server.use(
      http.get(PROJECTS_URL, () => HttpResponse.json({ items: [project()] })),
      http.post(`${PROJECTS_URL}/proj-alpha/deactivate`, deact),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("projects-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-deactivate-proj-alpha"));
    await waitFor(() => expect(deact).toHaveBeenCalled());
  });

  it("archives then reactivates a project", async () => {
    seedToken(["platform_manager"]);
    const archive = vi.fn(() => HttpResponse.json(project({ status: "archived" })));
    const activate = vi.fn(() => HttpResponse.json(project({ status: "active" })));
    server.use(
      http.get(PROJECTS_URL, () => HttpResponse.json({ items: [project({ status: "inactive" })] })),
      http.post(`${PROJECTS_URL}/proj-alpha/archive`, archive),
      http.post(`${PROJECTS_URL}/proj-alpha/activate`, activate),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("projects-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-activate-proj-alpha"));
    await waitFor(() => expect(activate).toHaveBeenCalled());
    await user.click(screen.getByTestId("project-archive-proj-alpha"));
    await waitFor(() => expect(archive).toHaveBeenCalled());
  });

  it("reveals the ACL, grants then revokes a member", async () => {
    seedToken(["platform_manager"]);
    const grant = vi.fn(() =>
      HttpResponse.json({
        project_id: "proj-alpha",
        tenant_id: "t-acme",
        members: [{ user_id: "u9", username: "dev", role: "project_editor" }],
      }),
    );
    const revoke = vi.fn(() =>
      HttpResponse.json({ project_id: "proj-alpha", tenant_id: "t-acme", members: [] }),
    );
    server.use(
      http.get(PROJECTS_URL, () => HttpResponse.json({ items: [project()] })),
      http.get(`${PROJECTS_URL}/proj-alpha/members`, () =>
        HttpResponse.json({ project_id: "proj-alpha", tenant_id: "t-acme", members: [] }),
      ),
      http.get("/admin/v1/quota/status", () => HttpResponse.json({ windows: [] })),
      http.post(`${PROJECTS_URL}/proj-alpha/members/u9`, grant),
      http.delete(`${PROJECTS_URL}/proj-alpha/members/u9`, revoke),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("projects-table")).toBeInTheDocument());
    const user = userEvent.setup();

    await user.click(screen.getByTestId("project-members-proj-alpha"));
    await waitFor(() => expect(screen.getByTestId("project-acl-proj-alpha")).toBeInTheDocument());

    await user.type(screen.getByTestId("project-grant-user-proj-alpha"), "u9");
    await user.click(screen.getByTestId("project-grant-submit-proj-alpha"));
    await waitFor(() => expect(grant).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId("project-member-proj-alpha-u9")).toBeInTheDocument(),
    );

    await user.click(screen.getByTestId("project-revoke-proj-alpha-u9"));
    await waitFor(() => expect(revoke).toHaveBeenCalled());
  });

  it("shows per-project cost columns and disk storage", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(PROJECTS_URL, () => HttpResponse.json({ items: [project()] })),
      http.get(COST_URL, () =>
        HttpResponse.json({
          currency: "EUR",
          windows: ["day", "week", "month", "quarter", "semester", "year"],
          tenant_id: null,
          projects: [
            {
              project_id: "proj-alpha",
              windows: {
                day: { cost: 1.5, tokens: 100 },
                week: { cost: 4, tokens: 400 },
                month: { cost: 12, tokens: 1200 },
                quarter: { cost: 30, tokens: 3000 },
                semester: { cost: 60, tokens: 6000 },
                year: { cost: 120, tokens: 12000 },
              },
            },
          ],
        }),
      ),
      http.get(STORAGE_URL, () =>
        HttpResponse.json({
          tenant_id: null,
          measured_at: "2026-07-29T00:00:00+00:00",
          projects: [{ project_id: "proj-alpha", tenant_id: "t-acme", bytes: 2 * 1024 * 1024 }],
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("projects-table")).toBeInTheDocument());
    // Day cost cell + storage cell in the list.
    await waitFor(() =>
      expect(screen.getByTestId("project-cost-day-proj-alpha")).toHaveTextContent("1.50 EUR"),
    );
    expect(screen.getByTestId("project-storage-proj-alpha")).toHaveTextContent("2.0 MB");
  });

  it("lets a platform_manager trigger a storage snapshot", async () => {
    seedToken(["platform_manager"]);
    const snap = vi.fn(() =>
      HttpResponse.json({ snapshots_written: 2, measured_at: "2026-07-29T12:00:00+00:00" }),
    );
    server.use(
      http.get(PROJECTS_URL, () => HttpResponse.json({ items: [project()] })),
      http.post("/admin/v1/storage/snapshot", snap),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("projects-table")).toBeInTheDocument());
    await userEvent.setup().click(screen.getByTestId("storage-snapshot-now"));
    await waitFor(() => expect(snap).toHaveBeenCalled());
    expect(screen.getByTestId("projects-notice")).toHaveTextContent("2 project(s) measured");
  });

  it("hides the storage snapshot trigger from a tenant admin", async () => {
    seedToken(["admin"]);
    server.use(http.get(PROJECTS_URL, () => HttpResponse.json({ items: [project()] })));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("projects-table")).toBeInTheDocument());
    expect(screen.queryByTestId("storage-snapshot-now")).not.toBeInTheDocument();
  });

  it("shows the project's per-window consumption on expand", async () => {
    seedToken(["platform_manager"]);
    const quota = vi.fn(() =>
      HttpResponse.json({
        windows: [
          {
            key: "month",
            label: "Monthly",
            levels: [{ level: "tenant", usage_cost_usd: 9, usage_tokens: 9 }],
          },
          {
            key: "session",
            label: "Session",
            levels: [{ level: "project", usage_cost_usd: 1.23, usage_tokens: 4567 }],
          },
        ],
      }),
    );
    server.use(
      http.get(PROJECTS_URL, () => HttpResponse.json({ items: [project()] })),
      http.get(`${PROJECTS_URL}/proj-alpha/members`, () =>
        HttpResponse.json({ project_id: "proj-alpha", tenant_id: "t-acme", members: [] }),
      ),
      http.get("/admin/v1/quota/status", quota),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("projects-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-members-proj-alpha"));

    const cons = await screen.findByTestId("project-consumption-proj-alpha");
    // Only the PROJECT level is shown — the tenant-level window is filtered out.
    expect(cons).toHaveTextContent("Session");
    expect(cons).toHaveTextContent("1.23");
    expect(cons).toHaveTextContent("4,567 tokens");
    expect(cons).not.toHaveTextContent("Monthly");
  });
});
