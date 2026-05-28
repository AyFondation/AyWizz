// =============================================================================
// File: vitest.config.ts
// Version: 2
// Path: ay_platform_ui/vitest.config.ts
// Description: Vitest configuration for the UI test pyramid (unit +
//              integration). E2E tests use Playwright and live in
//              `playwright.config.ts` (mocked tier) +
//              `playwright.system.config.ts` (real-stack tier).
//
//              v2 (2026-04-29) : excludes `tests/system/` from Vitest
//              discovery — those are Playwright system specs, not
//              unit/integration suites.
//
//              Mirrors the backend's pytest discipline :
//                - 80% line coverage gate (matches `--cov-fail-under=80`
//                  in `ay_platform_core/pyproject.toml`).
//                - Branch coverage measured + reported (informational).
//                - Test debug discipline (CLAUDE.md §10) applies :
//                  no tautological tests, no implementation-shortcut
//                  fixes for failing tests.
//                - Coverage discipline (§11) : the threshold is a
//                  ratchet — only goes UP, never DOWN.
// =============================================================================

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // Mirror tsconfig's `@/*` path alias so test imports work the
      // same as production code.
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    globals: true, // expose `expect`, `describe`, `it` without imports
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.{test,spec}.{ts,tsx}"],
    exclude: [
      "node_modules/**",
      ".next/**",
      "tests/e2e/**", // Playwright manages its own runner (mocked tier)
      "tests/system/**", // Playwright manages its own runner (real-stack tier)
    ],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "html"],
      reportsDirectory: "./coverage",
      // Mirror the backend's 80% line gate (blocking).
      thresholds: {
        lines: 80,
        functions: 80,
        statements: 80,
        // Branch coverage measured but NOT enforced — same policy
        // as backend's coverage.run config.
        branches: 70,
      },
      include: ["app/**/*.{ts,tsx}", "lib/**/*.ts", "components/**/*.{ts,tsx}"],
      exclude: [
        // Next.js generated types
        "**/*.d.ts",
        "**/.next/**",
        // Production-only entry points (server.js wrapper, not unit-testable)
        "app/layout.tsx",
        // Coverage of the page components themselves comes from integration
        // tests under tests/integration/, not from synthetic unit tests.
      ],
    },
  },
});
