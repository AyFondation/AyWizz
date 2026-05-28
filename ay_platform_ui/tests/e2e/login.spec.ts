// =============================================================================
// File: login.spec.ts
// Version: 2
// Path: ay_platform_ui/tests/e2e/login.spec.ts
// Description: E2E — login flow happy + failure paths. Tests the
//              full chain : form input → POST /auth/login → token
//              persisted → redirect to /dashboard with rendered
//              claims. Failure paths : 401 (bad creds), 503 (server).
//
//              v2 (2026-04-29) : alert assertions scope to the
//              login form (`<form>`) — Next.js 16 inserts a
//              `<div role="alert" id="__next-route-announcer__">`
//              for screen-reader navigation announcements, and a
//              bare `getByRole("alert")` matches BOTH that and the
//              form's error paragraph (strict-mode violation).
//              Claim-panel text uses exact match to avoid the
//              navbar's combined "tenant · roles" subline.
// =============================================================================

import { expect, test } from "@playwright/test";

import { mockHappyPlatform } from "./_fixtures";

test.describe("Login flow", () => {
  test("alice + any password lands on /dashboard with claims rendered", async ({ page }) => {
    await mockHappyPlatform(page);

    await page.goto("/login");

    await page.getByLabel(/username/i).fill("alice");
    await page.getByLabel(/password/i).fill("any-pw");
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page).toHaveURL("/dashboard");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByText("Welcome, alice.")).toBeVisible();
    // Claims panel renders at least the user_id and tenant_id. Exact
    // match — the navbar also surfaces tenant_id concatenated with
    // roles, which would shadow a substring match.
    await expect(page.getByText("user-alice", { exact: true })).toBeVisible();
    await expect(page.getByText("tenant-test", { exact: true })).toBeVisible();

    // Token persisted in localStorage so a refresh stays authenticated.
    const stored = await page.evaluate(() => window.localStorage.getItem("aywizz.token"));
    expect(stored).not.toBeNull();
  });

  test("displays 'Invalid credentials' on 401 without redirecting", async ({ page }) => {
    await mockHappyPlatform(page);

    await page.goto("/login");

    await page.getByLabel(/username/i).fill("bob");
    await page.getByLabel(/password/i).fill("wrong");
    await page.getByRole("button", { name: /sign in/i }).click();

    // Scope to the form to avoid the Next.js route-announcer
    // (`<div role="alert" id="__next-route-announcer__">`) collision.
    await expect(page.locator("form").getByRole("alert")).toHaveText(/invalid credentials/i);
    // No redirect : we stayed on /login.
    await expect(page).toHaveURL("/login");
    // No token persisted.
    const stored = await page.evaluate(() => window.localStorage.getItem("aywizz.token"));
    expect(stored).toBeNull();
  });

  test("displays a generic error on 503 without redirecting", async ({ page }) => {
    await mockHappyPlatform(page);
    // Override the login mock with a 503.
    await page.unroute("**/auth/login");
    await page.route("**/auth/login", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "service unavailable" }),
      }),
    );

    await page.goto("/login");

    await page.getByLabel(/username/i).fill("alice");
    await page.getByLabel(/password/i).fill("pw");
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page.locator("form").getByRole("alert")).toHaveText(/HTTP 503/);
    await expect(page).toHaveURL("/login");
  });
});
