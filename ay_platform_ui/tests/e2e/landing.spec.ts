// =============================================================================
// File: landing.spec.ts
// Version: 1
// Path: ay_platform_ui/tests/e2e/landing.spec.ts
// Description: E2E — anonymous user landing page. Verifies the
//              two-stage runtime-config bootstrap renders the brand
//              + auth_mode + Sign in CTA, and degrades gracefully
//              when the bootstrap fails.
// =============================================================================

import { expect, test } from "@playwright/test";

import { mockHappyPlatform } from "./_fixtures";

test.describe("Anonymous landing page", () => {
  test("renders brand + features + Sign in CTA when bootstrap succeeds", async ({ page }) => {
    await mockHappyPlatform(page);

    await page.goto("/");

    await expect(page.getByRole("heading", { name: "AyWizz Platform" })).toBeVisible();
    // Auth mode rendered.
    await expect(page.getByText("local")).toBeVisible();
    // Sign in link points at /login.
    const signIn = page.getByRole("link", { name: /sign in/i });
    await expect(signIn).toBeVisible();
    await expect(signIn).toHaveAttribute("href", "/login");
  });

  test("displays an error UI when /runtime-config.json returns 404", async ({ page }) => {
    await page.route("**/runtime-config.json", (route) =>
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "not found" }),
      }),
    );
    // /ux/config is never hit because Stage 1 fails ; route it just
    // in case so the test doesn't depend on the order of effects.
    await page.route("**/ux/config", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: "{}",
      }),
    );

    await page.goto("/");

    await expect(page.getByRole("heading", { name: /bootstrap failed/i })).toBeVisible();
    await expect(page.getByText(/stage=runtime/)).toBeVisible();
    await expect(page.getByText(/404/)).toBeVisible();
  });
});
