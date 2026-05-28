// =============================================================================
// File: _global-setup.ts
// Version: 1
// Path: ay_platform_ui/tests/system/_global-setup.ts
// Description: Playwright global setup hook for the **system** tier.
//              Polls `<baseURL>/ux/config` once before any test runs ;
//              fails fast with a helpful message if the stack isn't up
//              so the operator doesn't waste time on a forest of
//              ECONNREFUSED traces. Also sanity-checks that
//              `dev_credentials` is populated — the whole point of the
//              system suite is to exercise the demo-seed end-to-end.
// =============================================================================

import type { FullConfig } from "@playwright/test";

const HINT = `

  System tests need a running stack with the demo seed.
  Bring it up first :

      ay_platform_core/scripts/e2e_stack.sh dev

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
    throw new Error(
      `/ux/config returned no dev_credentials. Did you start the stack with ` +
        `\`e2e_stack.sh dev\` (which enables C2_UX_DEV_MODE_ENABLED) ?${HINT}`,
    );
  }
}
