// =============================================================================
// File: playwright.config.ts
// Version: 1
// Path: ay_platform_ui/playwright.config.ts
// Description: Playwright config for E2E tests. Spawns a Next.js dev
//              server on localhost:3000 and runs the test suite
//              against it. Backend calls are intercepted at the
//              browser level via `page.route()` per-test (NO real
//              backend required) — the deeper tier that runs against
//              a real cluster lives under `tests/system/` and is
//              deferred to a future phase.
//
//              Mirrors the backend's `tests/e2e/` philosophy : real
//              browser, real Next.js, mocked external dependencies.
// =============================================================================

import { defineConfig, devices } from "@playwright/test";

const PORT = 3000;
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  // No tests in unit/integration dirs — those run via Vitest.
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: true,
  // Fail CI if a test was accidentally marked as `.only` left in.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // 1 worker on CI keeps logs deterministic ; locally Playwright
  // picks an automatic count.
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html"]] : "html",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    // Add `firefox` / `webkit` once the Chromium suite stabilises ;
    // running 3 browsers triples CI time so v1 is Chromium-only.
  ],
  webServer: {
    command: "npm run dev",
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    // Don't fail the suite on dev-server stderr noise (Next.js
    // emits info-level messages there).
    stderr: "ignore",
  },
});
