// =============================================================================
// File: settings.test.tsx
// Path: ay_platform_ui/tests/integration/settings.test.tsx
// Description: Tests for the project Settings page (system_prompt editor +
//              git repo). useReadyConfig is mocked ; useAuth comes from a
//              real AuthProvider with a role-controlled seeded token. C5
//              project read/patch via MSW. Covers : editable view (admin)
//              with save + reset ; read-only view for a non-editor role ;
//              git-repo block ; load error.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProjectSettingsPage from "@/app/(protected)/projects/[pid]/settings/page";
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
vi.mock("next/navigation", () => ({
  useParams: () => ({ pid: "p1" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
}));

const PROJECT_URL = "/api/v1/projects/p1";

function seedToken(roles: string[], projectScopes: Record<string, string[]> = {}) {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "u1",
      username: "alice",
      tenant_id: "t1",
      roles,
      project_scopes: projectScopes,
      exp: Math.floor(Date.now() / 1000) + 3600,
      iat: Math.floor(Date.now() / 1000),
    }),
  );
}

function makeProject(over: Partial<Record<string, unknown>> = {}) {
  return {
    project_id: "p1",
    name: "P",
    profile: "code",
    tenant_id: "t1",
    created_by: "alice",
    created_at: "2026-01-01T00:00:00Z",
    system_prompt: "be precise",
    system_prompt_is_default: false,
    git_repo_url: "https://git.example/p1.git",
    ...over,
  };
}

afterEach(() => vi.restoreAllMocks());

function renderSettings() {
  return render(
    <AuthProvider>
      <ProjectSettingsPage />
    </AuthProvider>,
  );
}

describe("ProjectSettingsPage", () => {
  it("shows the editable prompt + git repo for an admin and saves", async () => {
    seedToken(["admin"]);
    const patch = vi.fn(() =>
      HttpResponse.json(
        makeProject({ system_prompt: "be precise", system_prompt_is_default: false }),
      ),
    );
    server.use(
      http.get(PROJECT_URL, () => HttpResponse.json(makeProject())),
      http.patch(PROJECT_URL, patch),
    );
    renderSettings();

    await waitFor(() =>
      expect(screen.getByTestId("project-prompt-input")).toHaveValue("be precise"),
    );
    expect(screen.getByTestId("project-prompt-input")).not.toBeDisabled();
    expect(screen.getByTestId("project-git-clone-url")).toHaveValue("https://git.example/p1.git");
    expect(screen.getByTestId("project-prompt-reset")).toBeInTheDocument(); // override active

    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-prompt-save"));
    await waitFor(() =>
      expect(screen.getByTestId("project-settings-saved")).toHaveTextContent(/saved/i),
    );
    expect(patch).toHaveBeenCalled();
  });

  it("renders a read-only view for a non-editor role", async () => {
    seedToken(["project_viewer"]);
    server.use(http.get(PROJECT_URL, () => HttpResponse.json(makeProject())));
    renderSettings();

    await waitFor(() => expect(screen.getByTestId("project-prompt-input")).toBeInTheDocument());
    expect(screen.getByTestId("project-prompt-input")).toBeDisabled();
    expect(screen.queryByTestId("project-prompt-save")).not.toBeInTheDocument();
    expect(screen.getByText(/Read-only/)).toBeInTheDocument();
  });

  it("allows a project_owner (per-project scope) to edit", async () => {
    seedToken(["project_viewer"], { p1: ["project_owner"] });
    server.use(http.get(PROJECT_URL, () => HttpResponse.json(makeProject())));
    renderSettings();
    await waitFor(() => expect(screen.getByTestId("project-prompt-save")).toBeInTheDocument());
    expect(screen.getByTestId("project-prompt-input")).not.toBeDisabled();
  });

  it("surfaces a project load error", async () => {
    seedToken(["admin"]);
    server.use(http.get(PROJECT_URL, () => HttpResponse.json({ detail: "x" }, { status: 500 })));
    renderSettings();
    await waitFor(() => expect(screen.getByText(/Failed to load project/)).toBeInTheDocument());
  });

  it("resets the project prompt to default", async () => {
    seedToken(["admin"]);
    const patch = vi.fn(() =>
      HttpResponse.json(makeProject({ system_prompt: "", system_prompt_is_default: true })),
    );
    server.use(
      http.get(PROJECT_URL, () => HttpResponse.json(makeProject())),
      http.patch(PROJECT_URL, patch),
    );
    renderSettings();

    const user = userEvent.setup();
    await user.click(await screen.findByTestId("project-prompt-reset"));
    await waitFor(() =>
      expect(screen.getByTestId("project-settings-saved")).toHaveTextContent(/reset to default/i),
    );
    expect(patch).toHaveBeenCalled();
  });
});
