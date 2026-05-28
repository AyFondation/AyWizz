// =============================================================================
// File: _fixtures.ts
// Version: 1
// Path: ay_platform_ui/tests/e2e/_fixtures.ts
// Description: Playwright fixtures + helpers shared by E2E specs.
//              Wraps `page.route()` for the 3 endpoints every test
//              needs : `/runtime-config.json`, `/ux/config`,
//              `/auth/login`. Per-test overrides via `page.unroute()`
//              + `page.route()` again.
// =============================================================================

import type { Page, Route } from "@playwright/test";

export const RUNTIME_CONFIG_DEFAULT = {
  apiBaseUrl: "",
  publicBaseUrl: "",
};

export const UX_CONFIG_DEFAULT = {
  api_version: "v1",
  auth_mode: "local",
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
};

/** Build a syntactically-valid JWT (fake signature — clients never
 *  verify it ; the server does on every protected request). */
export function fakeJWT(claims: Record<string, unknown>): string {
  const enc = (obj: unknown) =>
    Buffer.from(JSON.stringify(obj))
      .toString("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  const header = enc({ alg: "HS256", typ: "JWT" });
  const payload = enc(claims);
  return `${header}.${payload}.fake-signature`;
}

/** Install the default mocks for every test that needs the platform
 *  to look "healthy" — runtime config, UX config, login (alice → 200,
 *  any other user → 401). Tests can call `page.unroute()` then
 *  re-route to override a specific endpoint. */
export async function mockHappyPlatform(page: Page): Promise<void> {
  await page.route("**/runtime-config.json", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(RUNTIME_CONFIG_DEFAULT),
    }),
  );

  await page.route("**/ux/config", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(UX_CONFIG_DEFAULT),
    }),
  );

  await page.route("**/auth/login", async (route: Route) => {
    const request = route.request();
    const body = request.postDataJSON() as {
      username: string;
      password: string;
    };
    if (body.username === "alice") {
      const NOW = Math.floor(Date.now() / 1000);
      const token = fakeJWT({
        sub: "user-alice",
        username: "alice",
        tenant_id: "tenant-test",
        roles: ["project_editor", "admin"],
        exp: NOW + 3600,
        iat: NOW,
      });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          access_token: token,
          token_type: "bearer",
          expires_in: 3600,
        }),
      });
      return;
    }
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "invalid credentials" }),
    });
  });
}

/** Pre-seed localStorage with a fake-but-valid JWT so the next page
 *  load hydrates as authenticated. Call BEFORE `page.goto(...)`
 *  via `page.addInitScript` so the value is in place when AuthProvider
 *  reads it on mount. */
export async function seedAuthenticated(
  page: Page,
  claims: Record<string, unknown> = {},
): Promise<void> {
  const NOW = Math.floor(Date.now() / 1000);
  const token = fakeJWT({
    sub: "user-alice",
    username: "alice",
    tenant_id: "tenant-test",
    roles: ["project_editor"],
    exp: NOW + 3600,
    iat: NOW,
    ...claims,
  });
  await page.addInitScript((t: string) => {
    window.localStorage.setItem("aywizz.token", t);
  }, token);
}
