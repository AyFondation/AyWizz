// =============================================================================
// File: source-upload.spec.ts
// Version: 4
// Path: ay_platform_ui/tests/system/source-upload.spec.ts
//
// v3 (2026-06-01, R-100-081 v3): the upload is now a multipart POST to C7
// `/sources/upload` (C7 owns byte custody), asserting 202 + a pending
// SourcePublic — NOT the old C12 webhook with base64-in-JSON.
// Description: System-tier E2E — exercises the D-020 source-upload ingestion
//              pipeline end-to-end against a REAL running stack
//              (Traefik → C12 n8n webhook → C13 AyExtractor → C7). NOT mocked.
//
//              This is the system-tier counterpart of the contract test
//              (tests/contract/api-surface.test.ts) for the bug that started
//              this work : the Sources upload returned HTTP 405 because the
//              client POSTed to a C7 endpoint D-020 had removed. Here we POST
//              the ingestion webhook EXACTLY as `lib/apiClient.uploadSource`
//              does (JSON body, file as base64) and assert the route is wired.
//
//              Pre-requisite : the operator has brought the stack up with the
//              `test` profile so mock_llm + c13-extractor run, e.g.
//                  ay_platform_core/scripts/e2e_stack.sh full
//              (v9 passes `--profile test`; `cmd_seed` provisions `alice`).
//
//              ASSERTION SCOPE (deliberate, honest about what a mock-LLM
//              stack can prove) :
//                (1) the upload webhook is ROUTED and accepts POST — never
//                    404 (route gone) / 405 (wrong method, the original
//                    symptom).
//                (2) the n8n workflow does NOT CRASH — status < 500. A 5xx
//                    (`{"message":"Error in workflow"}`) means a node threw
//                    (unseeded MinIO credential, duplicate webhook, C13
//                    unreachable, …) — the pipeline is broken end-to-end.
//                    v1 missed this : it only checked 404/405 and SKIPPED
//                    assertions on non-2xx, so a 500 passed. v2 makes < 500
//                    a hard assertion (route-works-but-pipeline-crashes is
//                    the exact class the operator asked an e2e test to catch).
//                (3) the response is a terminal {status}/{accepted} envelope
//                    (the workflow ran to its Respond node). We do NOT assert
//                    `completed` : the mock LLM exposes no `/v1/embeddings`,
//                    so a clean run ends `failed` (still HTTP 200 via the
//                    Respond node). Real extraction needs the `litellm`
//                    profile + a real key (operator decision 2026-05-29).
// =============================================================================

import { expect, test } from "@playwright/test";

// -----------------------------------------------------------------------------
// WHO UPLOADS, and why it is not an admin.
//
// v4 (2026-10-12): this spec authenticated as `alice` — a GLOBAL `admin` —
// and wrote to project `demo`. Run against a real K8s stack it answered
// **403 "requires a project role on this project"**, and that is the
// platform working as designed, not a defect: E-100-002 v7 makes `admin`
// and `tenant_admin` CONTENT-BLIND, and v8 made the gateway enforce it
// (`_role_gated_project_id` at `/auth/verify`). A test that uses a global
// administrator to write project content asserts the opposite of the rule
// the platform exists to keep.
//
// It was also written for a DIFFERENT seeder: `alice`/`seed-password` and
// project `demo` come from `seed_e2e.py` (the compose stack), while a K8s
// stack is provisioned by C2's own `_ensure_demo_seed` — which grants
// `project_editor` to `project-editor` on `project-test` and
// `project-docgen`, and grants `alice` nothing.
//
// So: the demo-seed editor, on a demo-seed project. That identity holds a
// real project role, which is what both the gateway gate and C7's own
// `project_editor` requirement ask for — and it is the same account the
// four `demo-seed-login` specs use, so one precondition covers the tier.
// Overridable by env for a stack seeded differently, with no edit needed.
// -----------------------------------------------------------------------------
const UPLOAD_USER = process.env.SYSTEM_UPLOAD_USER ?? "project-editor";
const UPLOAD_PASSWORD = process.env.SYSTEM_UPLOAD_PASSWORD ?? "dev-editor";
const UPLOAD_PROJECT = process.env.SYSTEM_UPLOAD_PROJECT ?? "project-test";

test.describe("Source upload pipeline (real stack, D-020)", () => {
  test("upload webhook is routed and accepts POST (regression: the 405 bug)", async ({
    request,
  }) => {
    // 1. Real login against C2 (no MSW), as the demo-seed editor.
    const loginResp = await request.post("/auth/login", {
      data: { username: UPLOAD_USER, password: UPLOAD_PASSWORD },
    });
    expect(
      loginResp.ok(),
      `login failed (${loginResp.status()}) for ${UPLOAD_USER} — is the stack ` +
        `demo-seeded? K8s: infra/k8s/run.sh dev ; compose: ` +
        `ay_platform_core/scripts/e2e_stack.sh full`,
    ).toBeTruthy();
    const { access_token: token } = (await loginResp.json()) as { access_token: string };
    expect(token, "no access_token in /auth/login response").toBeTruthy();

    // 2. POST the file as multipart to C7 /sources/upload — EXACTLY as
    //    lib/apiClient.uploadSource does (R-100-081 v3: C7 owns byte custody;
    //    it stores the raw bytes + triggers the C12 workflow async, returns
    //    202). The tenant is taken server-side from the forward-auth header.
    const sourceId = `sys-upload-${Date.now()}`;
    const resp = await request.post(`/api/v1/memory/projects/${UPLOAD_PROJECT}/sources/upload`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: "system-test.md",
          mimeType: "text/markdown",
          buffer: Buffer.from("# system test\nhello from the upload pipeline\n", "utf-8"),
        },
        source_id: sourceId,
        mime_type: "text/markdown",
        format: "md",
      },
      timeout: 30_000,
    });

    const status = resp.status();
    const bodyText = await resp.text().catch(() => "");

    // (1) The route exists and accepts POST. A 404 (route gone) or 405
    // (the original symptom: client hit a route the backend did not expose
    // for POST) MUST never recur.
    expect(
      [404, 405],
      `C7 /sources/upload returned ${status} — 404/405 means the D-020 ` +
        `ingestion route is broken (the original bug). Body: ${bodyText}`,
    ).not.toContain(status);

    // (2) C7 must not CRASH (5xx). C7 stores the raw bytes and triggers the
    // C12 workflow async, then returns 202. A 5xx means C7 failed to store
    // or could not reach C12 (it returns 502 in that case) — the pipeline is
    // broken. This is the assertion that catches a working-route-but-broken-
    // pipeline (the gap an earlier version missed by only checking 404/405).
    expect(
      status,
      `C7 /sources/upload returned ${status} — C7 failed (5xx). Body: ${bodyText}`,
    ).toBeLessThan(500);

    // (3) A successful upload is 202 Accepted with a `pending` SourcePublic
    // (R-100-081 v3 — the heavy extraction runs asynchronously). We assert
    // the SHAPE (C7 accepted + recorded the source), not the async outcome.
    expect(status, `expected 202 Accepted, got ${status}: ${bodyText}`).toBe(202);
    let body: { source_id?: unknown; parse_status?: unknown } = {};
    try {
      body = JSON.parse(bodyText || "{}");
    } catch {
      /* asserted below */
    }
    expect(
      body.source_id === sourceId && typeof body.parse_status === "string",
      `expected a pending SourcePublic for ${sourceId}, got: ${bodyText}`,
    ).toBeTruthy();
  });
});
