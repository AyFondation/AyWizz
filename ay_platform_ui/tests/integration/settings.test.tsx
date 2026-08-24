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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
const ENRICH_URL = "/api/v1/memory/projects/p1/enrichment-config";

function makeEnrichment(over: Partial<Record<string, unknown>> = {}) {
  return {
    quality_tier: "minimal",
    summarization_enabled: null,
    decontextualization_enabled: null,
    densification_enabled: null,
    image_vision_enabled: null,
    chain_of_density_iterations: null,
    model_quality: null,
    image_analyzer_model: null,
    ...over,
  };
}

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

// The settings page now loads the enrichment config on mount; give every test
// a default handler so the section renders (per-test `server.use` can override).
const CAT_URL = "/api/v1/llm/catalog";
const PM_URL = "/api/v1/llm/projects/p1/models";
const EMB_CAT_URL = "/api/v1/llm/embedding-catalog";
const PE_URL = "/api/v1/llm/projects/p1/embedding";

function embCatEntry(over: Record<string, unknown> = {}) {
  return {
    tenant_id: "tenant-x",
    model_id: "e1",
    enabled: true,
    default_for_new_projects: true,
    registry: {
      model_id: "e1",
      alias: "all-minilm",
      provider_id: "p1",
      upstream_model: "all-minilm",
      dimension: 384,
      enabled: true,
      effective_from: "2026-08-21T00:00:00+00:00",
    },
    ...over,
  };
}

function catModel(over: Record<string, unknown> = {}) {
  return {
    tenant_id: "tenant-x",
    model_id: "m1",
    enabled: true,
    rate_in_per_1m: null,
    rate_out_per_1m: null,
    markup_pct: null,
    default_for_new_projects: true,
    registry: {
      model_id: "m1",
      alias: "claude-haiku-fast",
      provider_id: "p1",
      upstream_model: "claude-haiku-4-5",
      capabilities: { vision: true, tool_calling: true, context_window: 200000 },
      provider_cost_in_per_1m: 0.8,
      provider_cost_out_per_1m: 4.0,
      default_model_quality: "low",
      enabled: true,
      effective_from: "2026-06-08T00:00:00+00:00",
    },
    ...over,
  };
}

beforeEach(() => {
  server.use(
    http.get(ENRICH_URL, () => HttpResponse.json(makeEnrichment())),
    // The tenant-admin Models section fetches these; benign defaults so the
    // existing admin tests don't trip the unhandled-request guard.
    http.get(CAT_URL, () => HttpResponse.json({ models: [] })),
    http.get(PM_URL, () =>
      HttpResponse.json({
        tenant_id: "tenant-x",
        project_id: "p1",
        model_ids: [],
        is_explicit: false,
        models: [],
      }),
    ),
    // The tenant-admin Embedding section fetches these too.
    http.get(EMB_CAT_URL, () => HttpResponse.json({ models: [] })),
    http.get(PE_URL, () =>
      HttpResponse.json({
        tenant_id: "tenant-x",
        project_id: "p1",
        model_id: null,
        is_explicit: false,
        model: null,
      }),
    ),
  );
});

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

describe("ProjectSettingsPage — enrichment config", () => {
  it("loads the config and saves an edited tier + image model (admin)", async () => {
    seedToken(["admin"]);
    let sentBody: Record<string, unknown> | null = null;
    const put = vi.fn(async ({ request }: { request: Request }) => {
      sentBody = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(sentBody);
    });
    server.use(
      http.get(PROJECT_URL, () => HttpResponse.json(makeProject())),
      http.get(ENRICH_URL, () => HttpResponse.json(makeEnrichment({ quality_tier: "high" }))),
      http.put(ENRICH_URL, put),
    );
    renderSettings();

    await waitFor(() => expect(screen.getByTestId("enrichment-tier")).toHaveValue("high"));
    expect(screen.getByTestId("enrichment-tier")).not.toBeDisabled();

    const user = userEvent.setup();
    await user.selectOptions(screen.getByTestId("enrichment-tier"), "standard");
    await user.selectOptions(screen.getByTestId("enrichment-image_vision_enabled"), "off");
    await user.type(screen.getByTestId("enrichment-image-model"), "ollama:llava");
    await user.click(screen.getByTestId("enrichment-save"));

    await waitFor(() => expect(screen.getByTestId("enrichment-saved")).toBeInTheDocument());
    expect(put).toHaveBeenCalled();
    expect(sentBody).toMatchObject({
      quality_tier: "standard",
      image_vision_enabled: false,
      image_analyzer_model: "ollama:llava",
    });
  });

  it("loads + saves the model_quality picker (the project's LLM choice)", async () => {
    seedToken(["admin"]);
    let sentBody: Record<string, unknown> | null = null;
    const put = vi.fn(async ({ request }: { request: Request }) => {
      sentBody = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(sentBody);
    });
    server.use(
      http.get(PROJECT_URL, () => HttpResponse.json(makeProject())),
      http.get(ENRICH_URL, () => HttpResponse.json(makeEnrichment({ model_quality: "low" }))),
      http.put(ENRICH_URL, put),
    );
    renderSettings();

    await waitFor(() => expect(screen.getByTestId("enrichment-model-quality")).toHaveValue("low"));
    const user = userEvent.setup();
    await user.selectOptions(screen.getByTestId("enrichment-model-quality"), "high");
    await user.click(screen.getByTestId("enrichment-save"));

    await waitFor(() => expect(screen.getByTestId("enrichment-saved")).toBeInTheDocument());
    expect(sentBody).toMatchObject({ model_quality: "high" });
  });

  it("renders the enrichment config read-only for a non-editor role", async () => {
    seedToken(["project_viewer"]);
    server.use(http.get(PROJECT_URL, () => HttpResponse.json(makeProject())));
    renderSettings();

    await waitFor(() => expect(screen.getByTestId("enrichment-tier")).toBeInTheDocument());
    expect(screen.getByTestId("enrichment-tier")).toBeDisabled();
    expect(screen.getByTestId("enrichment-model-quality")).toBeDisabled();
    expect(screen.queryByTestId("enrichment-save")).not.toBeInTheDocument();
  });
});

describe("ProjectSettingsPage — project models (tenant-admin)", () => {
  it("lists the catalogue, pre-checks the effective set, and saves an explicit list", async () => {
    seedToken(["tenant_admin"]);
    let sent: { model_ids: string[] } | null = null;
    const put = vi.fn(async ({ request }) => {
      sent = (await request.json()) as { model_ids: string[] };
      return HttpResponse.json({
        tenant_id: "tenant-x",
        project_id: "p1",
        model_ids: sent.model_ids,
        is_explicit: true,
        models: [],
      });
    });
    server.use(
      http.get(PROJECT_URL, () => HttpResponse.json(makeProject())),
      http.get(CAT_URL, () => HttpResponse.json({ models: [catModel()] })),
      http.get(PM_URL, () =>
        HttpResponse.json({
          tenant_id: "tenant-x",
          project_id: "p1",
          model_ids: ["m1"],
          is_explicit: false,
          models: [catModel()],
        }),
      ),
      http.put(PM_URL, put),
    );
    renderSettings();
    await waitFor(() =>
      expect(screen.getByTestId("project-model-claude-haiku-fast")).toBeInTheDocument(),
    );
    // Lazy default → the "using defaults" hint shows, and m1 is pre-checked.
    expect(screen.getByTestId("project-models-using-defaults")).toBeInTheDocument();
    const cb = screen.getByTestId("project-model-claude-haiku-fast") as HTMLInputElement;
    expect(cb.checked).toBe(true);
    const user = userEvent.setup();
    await user.click(cb); // unselect
    await user.click(screen.getByTestId("project-models-save"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(sent).toEqual({ model_ids: [] });
  });

  it("is hidden from a plain project_owner (not tenant-admin)", async () => {
    seedToken(["project_viewer"], { p1: ["project_owner"] });
    server.use(http.get(PROJECT_URL, () => HttpResponse.json(makeProject())));
    renderSettings();
    await waitFor(() => expect(screen.getByTestId("project-system-prompt")).toBeInTheDocument());
    expect(screen.queryByTestId("project-models")).not.toBeInTheDocument();
  });
});

describe("ProjectSettingsPage — project embedding (tenant-admin)", () => {
  it("lists enabled catalogue embeddings and switches the project's selection", async () => {
    seedToken(["tenant_admin"]);
    let sent: { model_id: string } | null = null;
    const put = vi.fn(async ({ request }) => {
      sent = (await request.json()) as { model_id: string };
      return HttpResponse.json({
        tenant_id: "tenant-x",
        project_id: "p1",
        model_id: sent.model_id,
        is_explicit: true,
        model: embCatEntry().registry,
      });
    });
    server.use(
      http.get(PROJECT_URL, () => HttpResponse.json(makeProject())),
      http.get(EMB_CAT_URL, () => HttpResponse.json({ models: [embCatEntry()] })),
      http.put(PE_URL, put),
    );
    renderSettings();
    await waitFor(() =>
      expect(screen.getByTestId("project-embedding-all-minilm")).toBeInTheDocument(),
    );
    // Lazy default → "using default" hint, nothing explicitly checked yet.
    expect(screen.getByTestId("project-embedding-using-default")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("project-embedding-all-minilm"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(sent).toEqual({ model_id: "e1" });
    await waitFor(() =>
      expect(screen.getByTestId("project-embedding-notice")).toHaveTextContent(/re-embedded/i),
    );
  });

  it("is hidden from a plain project_owner (not tenant-admin)", async () => {
    seedToken(["project_viewer"], { p1: ["project_owner"] });
    server.use(http.get(PROJECT_URL, () => HttpResponse.json(makeProject())));
    renderSettings();
    await waitFor(() => expect(screen.getByTestId("project-system-prompt")).toBeInTheDocument());
    expect(screen.queryByTestId("project-embedding")).not.toBeInTheDocument();
  });
});
