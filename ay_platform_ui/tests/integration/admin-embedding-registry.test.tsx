// =============================================================================
// File: admin-embedding-registry.test.tsx
// Path: ay_platform_ui/tests/integration/admin-embedding-registry.test.tsx
// Description: Tests for the embedding model registry page. Covers: forbidden;
//              list; add-disabled without a provider; create with a dimension;
//              edit; delete.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EmbeddingRegistryPage from "@/app/(protected)/admin/embedding-registry/page";
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

const MODELS = "/admin/v1/llm/embedding-models";
const PROV = "/admin/v1/llm/embedding-providers";

function provider(over: Partial<Record<string, unknown>> = {}) {
  return {
    provider_id: "p1",
    name: "Ollama",
    adapter: "ollama",
    base_url: "http://ollama:11434",
    effective_from: "2026-08-21T00:00:00+00:00",
    key_status: "not_set",
    api_key_hint: "",
    ...over,
  };
}

function model(over: Partial<Record<string, unknown>> = {}) {
  return {
    model_id: "m1",
    alias: "all-minilm",
    provider_id: "p1",
    upstream_model: "all-minilm",
    dimension: 384,
    enabled: true,
    effective_from: "2026-08-21T00:00:00+00:00",
    ...over,
  };
}

function seedToken(roles: string[]) {
  window.localStorage.setItem(
    "aywizz.token",
    fakeJWT({
      sub: "u1",
      username: "root",
      tenant_id: "t1",
      roles,
      exp: Math.floor(Date.now() / 1000) + 3600,
      iat: Math.floor(Date.now() / 1000),
    }),
  );
}

function renderPage() {
  return render(
    <AuthProvider>
      <EmbeddingRegistryPage />
    </AuthProvider>,
  );
}

beforeEach(() => window.localStorage.clear());

describe("EmbeddingRegistryPage", () => {
  it("forbids a non-platform_manager", async () => {
    seedToken(["admin"]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("embedding-registry-forbidden")).toBeInTheDocument(),
    );
  });

  it("lists models and disables add without providers", async () => {
    seedToken(["platform_manager"]);
    server.use(
      http.get(MODELS, () => HttpResponse.json({ models: [model()] })),
      http.get(PROV, () => HttpResponse.json({ providers: [] })),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("embedding-registry-table")).toBeInTheDocument());
    expect(screen.getByTestId("embedding-registry-row-all-minilm")).toBeInTheDocument();
    expect(screen.getByTestId("embedding-registry-add")).toBeDisabled();
    expect(screen.getByTestId("embedding-registry-no-providers")).toBeInTheDocument();
  });

  it("creates a model with a dimension", async () => {
    seedToken(["platform_manager"]);
    let body: Record<string, unknown> | null = null;
    const post = vi.fn(async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(model({ model_id: "m9", alias: "e5", dimension: 768 }));
    });
    server.use(
      http.get(MODELS, () => HttpResponse.json({ models: [] })),
      http.get(PROV, () => HttpResponse.json({ providers: [provider()] })),
      http.post(MODELS, post),
    );
    renderPage();
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByTestId("embedding-registry-add")).toBeEnabled());
    await user.click(screen.getByTestId("embedding-registry-add"));
    await user.type(screen.getByTestId("embedding-registry-form-alias"), "e5");
    await user.type(screen.getByTestId("embedding-registry-form-upstream"), "e5-small");
    await user.clear(screen.getByTestId("embedding-registry-form-dimension"));
    await user.type(screen.getByTestId("embedding-registry-form-dimension"), "768");
    await user.click(screen.getByTestId("embedding-registry-form-submit"));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(body).toMatchObject({
      alias: "e5",
      provider_id: "p1",
      upstream_model: "e5-small",
      dimension: 768,
    });
  });

  it("edits then deletes a model", async () => {
    seedToken(["platform_manager"]);
    const put = vi.fn(() => HttpResponse.json(model({ dimension: 512 })));
    const del = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(
      http.get(MODELS, () => HttpResponse.json({ models: [model()] })),
      http.get(PROV, () => HttpResponse.json({ providers: [provider()] })),
      http.put(`${MODELS}/m1`, put),
      http.delete(`${MODELS}/m1`, del),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("embedding-registry-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("embedding-registry-edit-all-minilm"));
    await user.click(screen.getByTestId("embedding-registry-form-submit"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    await user.click(screen.getByTestId("embedding-registry-delete-all-minilm"));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });

  it("surfaces the backend's real reason on a guarded delete (409)", async () => {
    // Regression: a delete blocked because a project still selects the model
    // MUST show the actionable backend detail, not the old misleading
    // "alias already exists" (which is a create-conflict message).
    seedToken(["platform_manager"]);
    const del = vi.fn(() =>
      HttpResponse.json(
        {
          detail: "embedding model m1 is selected by 1 project(s): ['proj-1']; reassign them first",
        },
        { status: 409 },
      ),
    );
    server.use(
      http.get(MODELS, () => HttpResponse.json({ models: [model()] })),
      http.get(PROV, () => HttpResponse.json({ providers: [provider()] })),
      http.delete(`${MODELS}/m1`, del),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("embedding-registry-table")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByTestId("embedding-registry-delete-all-minilm"));
    const alert = await screen.findByTestId("embedding-registry-error");
    expect(alert).toHaveTextContent(/selected by 1 project\(s\)/i);
    expect(alert).toHaveTextContent(/reassign them first/i);
    expect(alert).not.toHaveTextContent(/alias already exists/i);
  });
});
