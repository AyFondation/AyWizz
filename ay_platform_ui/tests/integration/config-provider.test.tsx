// =============================================================================
// File: config-provider.test.tsx
// Version: 1
// Path: ay_platform_ui/tests/integration/config-provider.test.tsx
// Description: Integration tests for <ConfigProvider>. Exercises the
//              two-stage bootstrap : MSW serves the static
//              `/runtime-config.json` + the dynamic `/ux/config`,
//              the provider hydrates and exposes a `ready` state.
//
//              Failure paths : 404 on either stage transitions to
//              `error` with a stage-tagged message.
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { ConfigProvider, useConfigState } from "@/app/providers";

import { server } from "../helpers/msw-server";

/** Probe that surfaces the bootstrap state as text + brand details
 *  when ready. Tests assert against the rendered output. */
function ConfigProbe() {
  const state = useConfigState();
  if (state.status === "loading") {
    return <div data-testid="status">loading</div>;
  }
  if (state.status === "error") {
    return (
      <>
        <div data-testid="status">error</div>
        <div data-testid="error-msg">{state.error}</div>
      </>
    );
  }
  return (
    <>
      <div data-testid="status">ready</div>
      <div data-testid="brand-name">{state.config.ux.brand.name}</div>
      <div data-testid="auth-mode">{state.config.ux.auth_mode}</div>
      <div data-testid="api-base">{state.config.runtime.apiBaseUrl}</div>
    </>
  );
}

describe("ConfigProvider — happy path", () => {
  it("transitions loading → ready and exposes brand + auth_mode + apiBaseUrl", async () => {
    render(
      <ConfigProvider>
        <ConfigProbe />
      </ConfigProvider>,
    );

    expect(screen.getByTestId("status")).toHaveTextContent("loading");

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("ready");
    });
    expect(screen.getByTestId("brand-name")).toHaveTextContent("AyWizz Platform");
    expect(screen.getByTestId("auth-mode")).toHaveTextContent("local");
    // Default MSW handler returns empty apiBaseUrl (same-origin).
    expect(screen.getByTestId("api-base")).toHaveTextContent("");
  });

  it("renders the apiBaseUrl coming from runtime-config.json (cross-origin scenario)", async () => {
    server.use(
      http.get("/runtime-config.json", () =>
        HttpResponse.json({
          apiBaseUrl: "https://api.acme.com",
          publicBaseUrl: "https://app.acme.com",
        }),
      ),
      // The /ux/config request now goes to the absolute URL.
      http.get("https://api.acme.com/ux/config", () =>
        HttpResponse.json({
          api_version: "v1",
          auth_mode: "none",
          brand: {
            name: "ACME Internal",
            short_name: "ACME",
            accent_color_hex: "#ff0066",
          },
          features: {
            chat_enabled: false,
            kg_enabled: false,
            cross_tenant_enabled: false,
            file_download_enabled: false,
          },
        }),
      ),
    );

    render(
      <ConfigProvider>
        <ConfigProbe />
      </ConfigProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("ready");
    });
    expect(screen.getByTestId("brand-name")).toHaveTextContent("ACME Internal");
    expect(screen.getByTestId("api-base")).toHaveTextContent("https://api.acme.com");
  });
});

describe("ConfigProvider — failure paths", () => {
  it("transitions to error when /runtime-config.json returns 404", async () => {
    server.use(
      http.get("/runtime-config.json", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );

    render(
      <ConfigProvider>
        <ConfigProbe />
      </ConfigProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("error");
    });
    expect(screen.getByTestId("error-msg")).toHaveTextContent(/stage=runtime/);
    expect(screen.getByTestId("error-msg")).toHaveTextContent(/404/);
  });

  it("transitions to error when /ux/config returns 503", async () => {
    server.use(
      http.get("/ux/config", () =>
        HttpResponse.json({ detail: "service unavailable" }, { status: 503 }),
      ),
    );

    render(
      <ConfigProvider>
        <ConfigProbe />
      </ConfigProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("error");
    });
    expect(screen.getByTestId("error-msg")).toHaveTextContent(/stage=ux/);
    expect(screen.getByTestId("error-msg")).toHaveTextContent(/503/);
  });

  it("transitions to error when /runtime-config.json returns invalid JSON", async () => {
    server.use(
      http.get("/runtime-config.json", () => HttpResponse.text("not-json-at-all", { status: 200 })),
    );

    render(
      <ConfigProvider>
        <ConfigProbe />
      </ConfigProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status")).toHaveTextContent("error");
    });
    expect(screen.getByTestId("error-msg")).toHaveTextContent(/invalid JSON/);
  });
});
