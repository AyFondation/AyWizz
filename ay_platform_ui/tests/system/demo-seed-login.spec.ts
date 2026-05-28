// =============================================================================
// File: demo-seed-login.spec.ts
// Version: 1
// Path: ay_platform_ui/tests/system/demo-seed-login.spec.ts
// Description: System-tier E2E — exercises the demo-seed login flow
//              end-to-end against a REAL running stack (Traefik → C2 →
//              ArangoDB). NOT mocked. The stack MUST have been started
//              with `e2e_stack.sh dev` so :
//                - C2's `_ensure_demo_seed()` has provisioned the
//                  tenant + 4 users + project + grants in Arango.
//                - C2's `/ux/config` exposes the demo credentials
//                  (`C2_UX_DEV_MODE_ENABLED=true` from `.env.dev`).
//
//              Goal : prove the operator-facing manual-test flow works
//              without any setup beyond `e2e_stack.sh dev`. Click
//              `project-editor` → form auto-fills → submit → land on
//              `/dashboard` rendering the editor's JWT claims.
// =============================================================================

import { expect, test } from "@playwright/test";

test.describe("Demo seed login flow (real stack)", () => {
  test.beforeEach(async ({ context }) => {
    // Ensure we start anonymous : no token leaking between tests if
    // the same browser context is reused (workers=1 shouldn't reuse,
    // but be defensive).
    await context.clearCookies();
  });

  test("dev credentials panel surfaces 4 demo accounts", async ({ page }) => {
    await page.goto("/login");

    // Panel renders synchronously after /ux/config resolves.
    await expect(page.getByTestId("dev-credentials-panel")).toBeVisible();

    // Four accounts as seeded by `_ensure_demo_seed`.
    await expect(page.getByTestId("dev-credential-superroot")).toBeVisible();
    await expect(page.getByTestId("dev-credential-tenant-admin")).toBeVisible();
    await expect(page.getByTestId("dev-credential-project-editor")).toBeVisible();
    await expect(page.getByTestId("dev-credential-project-viewer")).toBeVisible();
  });

  test("clicking project-editor → auto-fill → submit → /dashboard", async ({ page }) => {
    await page.goto("/login");

    await expect(page.getByTestId("dev-credentials-panel")).toBeVisible();

    // Click the project-editor row : auto-fills both inputs.
    await page.getByTestId("dev-credential-project-editor").click();
    await expect(page.getByLabel(/username/i)).toHaveValue("project-editor");
    await expect(page.getByLabel(/password/i)).toHaveValue("dev-editor");

    // Submit hits the REAL C2 /auth/login (no MSW, no page.route).
    // C2 verifies the argon2id hash, returns a JWT signed with the
    // stack's HS256 secret, the AuthProvider stores it and bounces
    // to /dashboard.
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page).toHaveURL("/dashboard");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    // The dashboard renders the decoded JWT subject — the demo
    // editor user_id is `demo-project-editor`.
    await expect(page.getByText(/welcome,\s*project-editor/i)).toBeVisible();
  });

  test("clicking superroot logs in as tenant_manager (cross-tenant content-blind)", async ({
    page,
  }) => {
    await page.goto("/login");

    await page.getByTestId("dev-credential-superroot").click();
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page).toHaveURL("/dashboard");
    // Dashboard renders the decoded claims — superroot has the
    // `tenant_manager` global role.
    await expect(page.getByText(/welcome,\s*superroot/i)).toBeVisible();
    await expect(page.getByText("tenant_manager")).toBeVisible();
  });

  test("clicking project-viewer logs in with viewer scope", async ({ page }) => {
    await page.goto("/login");

    await page.getByTestId("dev-credential-project-viewer").click();
    await expect(page.getByLabel(/username/i)).toHaveValue("project-viewer");

    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page).toHaveURL("/dashboard");
    await expect(page.getByText(/welcome,\s*project-viewer/i)).toBeVisible();
  });
});
