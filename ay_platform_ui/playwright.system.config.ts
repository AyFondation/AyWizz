// =============================================================================
// File: playwright.system.config.ts
// Version: 1
// Path: ay_platform_ui/playwright.system.config.ts
// Description: Playwright config for the **system** test tier — hits a
//              REAL running stack at http://localhost:56000 (Traefik
//              public port from R-100-122 §10.7). No webServer to spawn,
//              no `page.route()` mocks. Pre-requisite : the operator
//              has run `ay_platform_core/scripts/e2e_stack.sh dev` so
//              C2's lifespan has provisioned the demo seed.
//
//              Mirrors the backend's `tests/system/k8s/` philosophy :
//              real infrastructure, real wire protocol. The companion
//              `playwright.config.ts` covers the mocked tier
//              (`tests/e2e/`) and stays the default `npm run test:e2e`
//              command for fast feedback in CI.
// =============================================================================

import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.SYSTEM_STACK_PORT ?? 56000);
const BASE_URL = process.env.SYSTEM_STACK_BASE_URL ?? `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./tests/system",
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  // Generous timeouts — we go through the real Traefik → C2 → Arango
  // chain on every assertion ; first request after `dev` warm-up can
  // take several seconds.
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // Bypass HTTPS errors if someone routes via a self-signed cert in
    // a future overlay. Local stack is plain HTTP so this is a no-op.
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // No webServer — system tests assume the stack is already up.
  // Print a clear hint when it isn't, so a CI run that forgets to
  // bring up the stack fails with a helpful message rather than a
  // generic ECONNREFUSED.
  globalSetup: "./tests/system/_global-setup.ts",
});
