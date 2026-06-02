// =============================================================================
// File: _global-setup.ts
// Version: 2
// Path: ay_platform_ui/tests/system/_global-setup.ts
// Description: Playwright global setup hook for the **system** tier.
//              Polls `<baseURL>/ux/config` once before any test runs ;
//              fails fast with a helpful message if the stack isn't up
//              so the operator doesn't waste time on a forest of
//              ECONNREFUSED traces.
//
//              v2 (2026-05-29): the `dev_credentials` presence check is
//              now a WARNING, not a hard throw. Rationale: dev_credentials
//              (C2_UX_DEV_MODE_ENABLED) is a precondition for the
//              demo-seed-login spec ONLY — those specs assert the
//              dev-credentials panel themselves and fail clearly without
//              it. Specs that authenticate via the real `/auth/login`
//              (e.g. source-upload, which runs against the `e2e_stack.sh
//              full` / `--profile test` stack where dev mode is OFF) MUST
//              NOT be gated on it. Reachability + auth_mode=local stay
//              hard preconditions (universal to the tier).
// =============================================================================

import type { FullConfig } from "@playwright/test";

const HINT = `

  System tests need a running stack. Bring one up first :

      ay_platform_core/scripts/e2e_stack.sh dev    # demo-seed UX specs
      ay_platform_core/scripts/e2e_stack.sh full   # real-login specs (source-upload)

  Then re-run :

      npm run test:system

`;

export default async function globalSetup(config: FullConfig): Promise<void> {
  const project = config.projects[0];
  const baseURL = (project?.use?.baseURL as string) ?? "http://localhost:56000";

  let resp: Response;
  try {
    resp = await fetch(`${baseURL}/ux/config`, { method: "GET" });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(`Stack unreachable at ${baseURL}: ${msg}\n${HINT}`);
  }
  if (!resp.ok) {
    throw new Error(`Stack at ${baseURL} returned ${resp.status} for /ux/config\n${HINT}`);
  }
  const body = (await resp.json()) as {
    auth_mode?: string;
    dev_credentials?: unknown;
  };
  if (body.auth_mode !== "local") {
    throw new Error(
      `Expected auth_mode=local from /ux/config, got ${String(body.auth_mode)}.${HINT}`,
    );
  }
  if (!Array.isArray(body.dev_credentials) || body.dev_credentials.length === 0) {
    // Non-fatal : only the demo-seed specs need this (they assert the
    // dev-credentials panel themselves). Real-login specs run fine
    // against the `full`/`--profile test` stack where dev mode is OFF.
    console.warn(
      `[system-setup] /ux/config returned no dev_credentials — the ` +
        `demo-seed specs need \`e2e_stack.sh dev\` (C2_UX_DEV_MODE_ENABLED). ` +
        `Real-login specs (source-upload) are unaffected.`,
    );
  }
}
